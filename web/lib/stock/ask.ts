// AI 자유질문 — 사용자가 친 질문을, 그 종목의 실제 데이터 + 수집한 뉴스·찌라시(토론방·텔레그램)
// "원문만" 근거로 Kimi가 답하게 한다(RAG). 환각 차단의 핵심은 두 가지:
//  ① 프롬프트: 주어진 자료 밖 사실 생성 금지, 모르면 "확인 불가", 글 인용 시 원문 발췌(quote) 필수.
//  ② 사후 대조: 모델이 댄 quote가 실제 수집 원문에 있는지 코드가 substring 대조 → 없으면 그 주장 삭제.
// 찌라시(토론방·텔레그램)는 "사실"이 아니라 "이런 말이 돈다(미확인)"로만 전달.

import type { AskItem, StockAnswer, StockReport } from "@/types/stock";
import { buildStockReport } from "./report";
import { getKimiConfig, callKimiJson, serializeForPrompt } from "./ai";
import { gatherRumors, type RumorItem } from "./rumors";
import { formatKST } from "./parse";

const MAX_EVIDENCE = 10;
const MIN_QUOTE_LEN = 5; // 정규화 후 최소 길이 — 너무 짧은 인용은 검증 불가로 간주

const SYSTEM_PROMPT = `당신은 한국 주식 분석가입니다. 아래에 주어진 [데이터](그 종목의 시세·시총·재무·수급·기술지표)와 [수집한 글](뉴스·토론방·텔레그램에서 그 종목을 검색해 가져온 실제 글)을 근거로 사용자의 질문에 한국어로 답하세요. 주어진 자료를 **종합해 추론·해석·결론을 제시해도 됩니다** — 단, 그 추론의 바탕이 된 근거는 반드시 evidence에 출처와 함께 남기세요(사용자가 링크로 직접 확인합니다).

반드시 지킬 규칙:
- 자료에 근거가 전혀 없는 사실(없는 사건·수치·발표 등)을 새로 지어내지 마세요. 추론은 주어진 자료에서 출발해야 합니다. 자료가 질문과 전혀 무관하면 answerable=false로 하고 answer에 "수집된 자료로는 확인할 수 없습니다"라고 적으세요.
- 추론·결론에는 그 바탕이 된 근거를 evidence에 남기고, 각 항목에 출처를 src로 표기하세요: "데이터" 또는 글 번호(N#=뉴스, B#=토론방, T#=텔레그램) 중 하나.
- 글(N#/B#/T#)을 근거로 쓸 때는 quote에 그 글에서 **글자 그대로 복사한 짧은 발췌**(최소 6자)를 넣으세요. 발췌를 지어내면 시스템이 원문 대조로 탐지해 그 근거를 삭제합니다.
- 토론방·텔레그램(B#/T#)은 진위가 검증되지 않은 루머입니다. "사실이다"라고 단정하지 말고 "이런 말이 돈다(미확인)"처럼 전달하세요. 진위 판단은 [데이터]로 확인 가능할 때만 하세요.
- 숫자는 [데이터]에 적힌 값을 그대로 쓰세요. 비율·배수를 직접 계산하지는 마세요(계산 오류 위험) — 원래 수치를 제시하고 "A가 B보다 크다/작다, 대략 몇 배 수준" 정도의 정성적 비교·추론을 하세요.
- 재무 수치는 확정 실적과 추정치(E)를 구분해 말하세요. 추정치는 "추정(E)"임을 밝히세요.
- 매수·매도 권유 표현은 금지합니다.
- 반드시 아래 JSON 형식으로만 응답하세요(다른 텍스트 금지):
{
  "answerable": true 또는 false,
  "answer": "2~4문장 한국어 종합·추론 — 아래 evidence를 바탕으로 한 해석·결론 포함 가능",
  "evidence": [
    { "text": "한 줄 근거", "src": "데이터|N1|B2|T3", "quote": "글 원문 발췌(데이터 근거면 생략 가능)" }
  ],
  "caveat": "한 줄 한계·주의"
}`;

/** "2,069조 5,826억" → 20695826(억). 단위 혼용으로 인한 모델 계산오류를 줄이려 같은 단위로 환산. */
function marketCapEok(s: string | null): number | null {
  if (!s) return null;
  const jo = /([\d,]+)\s*조/.exec(s);
  const eok = /([\d,]+)\s*억/.exec(s);
  const total = (jo ? Number(jo[1].replace(/,/g, "")) : 0) * 10000 + (eok ? Number(eok[1].replace(/,/g, "")) : 0);
  return total > 0 ? total : null;
}

/** 리포트에 시총·절대 재무를 보강해 직렬화(기본 serializeForPrompt는 비율만 담음). */
function serializeData(r: StockReport): string {
  const lines = [serializeForPrompt(r)];
  const extra: string[] = [];
  const p = r.price;
  if (p) {
    const capEok = marketCapEok(p.marketCap);
    const bits = [`시가총액 ${p.marketCap ?? "?"}`];
    if (capEok) bits.push(`(억원 환산 ${capEok.toLocaleString()}억)`);
    if (p.per != null) bits.push(`PER ${p.per}`);
    if (p.eps != null) bits.push(`EPS ${p.eps.toLocaleString()}원`);
    if (p.pbr != null) bits.push(`PBR ${p.pbr}`);
    if (p.bps != null) bits.push(`BPS ${p.bps.toLocaleString()}원`);
    extra.push(`시총·밸류: ${bits.join(" · ")}`);
  }
  const fin = r.financials;
  if (fin && fin.rows.length > 0) {
    extra.push(`재무 기간: ${fin.periods.map((x) => x.label + (x.isEstimate ? "(E)" : "")).join(" / ")}`);
    for (const row of fin.rows) {
      extra.push(
        `  ${row.label}(억원): ${row.values.map((v) => (v == null ? "?" : v.toLocaleString())).join(" / ")}`
      );
    }
  }
  if (extra.length > 0) lines.push("[밸류에이션·재무 상세]\n" + extra.join("\n"));
  return lines.join("\n");
}

interface SourceEntry {
  kind: AskItem["label"];
  text: string;
  date: string | null;
  url: string | null;
}

/** 수집 글에 ID 부여 + 프롬프트용 블록 + 검증용 맵(원문 링크 포함). */
function buildSources(
  news: { title: string; url: string | null }[],
  board: RumorItem[],
  telegram: RumorItem[]
): { block: string; map: Map<string, SourceEntry> } {
  const map = new Map<string, SourceEntry>();
  const L: string[] = [];
  news.slice(0, 10).forEach((n, i) => {
    const id = `N${i + 1}`;
    map.set(id, { kind: "뉴스", text: n.title, date: null, url: n.url });
    L.push(`${id} [뉴스] ${n.title}`);
  });
  board.forEach((r, i) => {
    const id = `B${i + 1}`;
    map.set(id, { kind: "토론방", text: r.text, date: r.date, url: r.url });
    L.push(`${id} [토론방·미확인] ${r.text}`);
  });
  telegram.forEach((r, i) => {
    const id = `T${i + 1}`;
    map.set(id, { kind: "텔레그램", text: r.text, date: r.date, url: r.url });
    L.push(`${id} [텔레그램·미확인] ${r.text}`);
  });
  return { block: L.length > 0 ? L.join("\n") : "(검색된 글 없음)", map };
}

const norm = (s: string) => s.replace(/\s+/g, "").toLowerCase();

interface RawEvidence {
  text: string;
  src: string;
  quote?: string;
}

function parseEvidence(raw: unknown): RawEvidence[] {
  if (!Array.isArray(raw)) return [];
  const out: RawEvidence[] = [];
  for (const e of raw) {
    if (typeof e !== "object" || e === null) continue;
    const o = e as Record<string, unknown>;
    const text = typeof o.text === "string" ? o.text.trim().slice(0, 300) : "";
    const src = typeof o.src === "string" ? o.src.trim() : "";
    const quote = typeof o.quote === "string" ? o.quote.trim().slice(0, 200) : undefined;
    if (text && src) out.push({ text, src, quote });
    if (out.length >= MAX_EVIDENCE) break;
  }
  return out;
}

export async function answerQuestion(code: string, question: string): Promise<StockAnswer> {
  const cfg = getKimiConfig(); // 키 없으면 AiConfigError (리포트 fetch 전에 빠르게 실패)
  const report = await buildStockReport(code); // NotFound/Unreachable는 라우트가 처리

  const newsItems = (report.news?.items ?? [])
    .slice()
    .sort((a, b) => Number(b.relevant) - Number(a.relevant))
    .filter((n) => !!n.title)
    .map((n) => ({ title: n.title, url: n.url }))
    .slice(0, 10);
  const { board, telegram } = await gatherRumors(report.name, code);
  const { block: sourceBlock, map: sourceMap } = buildSources(newsItems, board, telegram);

  const dataText = serializeData(report);
  const dataDigits = dataText.replace(/[^\d]/g, ""); // 데이터 근거 수치 대조용

  const userContent =
    `[데이터]\n${dataText}\n\n` +
    `[수집한 글] (N=뉴스, B=토론방, T=텔레그램. B/T는 미확인 루머)\n${sourceBlock}\n\n` +
    `[사용자 질문]\n${question}`;

  const parsed = (await callKimiJson({
    cfg,
    systemPrompt: SYSTEM_PROMPT,
    userContent,
    tag: `ask:${code}`,
  })) as Record<string, unknown>;

  const answerable = parsed?.answerable !== false; // 누락 시 관대하게 true
  const answer =
    typeof parsed?.answer === "string" && parsed.answer.trim()
      ? parsed.answer.trim().slice(0, 1200)
      : "수집된 자료로는 확인할 수 없습니다.";
  const caveat =
    typeof parsed?.caveat === "string" && parsed.caveat.trim()
      ? parsed.caveat.trim().slice(0, 300)
      : "AI가 수집 자료를 근거로 생성한 답변이며 찌라시는 미확인 루머입니다. 매수·매도 추천이 아닙니다.";

  // ── 사후 대조 검증: 글 인용은 원문에 실제 있을 때만 채택(환각 차단) ──
  const facts: AskItem[] = [];
  const rumors: AskItem[] = [];
  let dropped = 0;
  for (const e of parseEvidence(parsed?.evidence)) {
    if (e.src === "데이터") {
      // 데이터 근거의 4자리+ 숫자는 실제 [데이터]에 존재해야 채택 (조작·계산 산출 숫자 차단)
      const nums = (`${e.text} ${e.quote ?? ""}`.match(/\d[\d,]{3,}/g) ?? []).map((x) =>
        x.replace(/\D/g, "")
      );
      if (!nums.every((d) => d.length < 4 || dataDigits.includes(d))) {
        dropped++;
        continue;
      }
      facts.push({ text: e.text, label: "데이터" });
      continue;
    }
    // 모델이 src를 'N1, N3'·'n1 (뉴스)'처럼 복수 ID·부연과 함께 줄 수 있어 첫 ID 토큰만 추출 —
    // 정확 인용이 단순 형식 차이로 dropped(거짓 음성)되는 것 방지.
    const srcKey = (e.src.match(/[A-Za-z]+\d+/)?.[0] ?? e.src).toUpperCase();
    const src = sourceMap.get(srcKey);
    if (!src) {
      dropped++; // 존재하지 않는 출처 ID
      continue;
    }
    const q = e.quote ?? "";
    if (norm(q).length < MIN_QUOTE_LEN || !norm(src.text).includes(norm(q))) {
      dropped++; // 발췌가 원문에 없음 = 환각 의심 → 삭제
      continue;
    }
    const item: AskItem = { text: e.text, label: src.kind, quote: src.text, date: src.date, url: src.url };
    if (src.kind === "토론방" || src.kind === "텔레그램") rumors.push(item);
    else facts.push(item);
  }

  // 서술(answer) 속 모델 계산 수치 백스톱: 데이터에 없는 4자리+ 숫자나 비율(%)이 있으면
  // "검증 안 됨"으로 표시 — 모델이 지시를 어기고 계산해 틀린 비율을 내도 사용자가 안 믿게.
  const answerNums = (answer.match(/\d[\d,]{3,}/g) ?? []).map((x) => x.replace(/\D/g, ""));
  const calcUnverified =
    answerNums.some((d) => d.length >= 4 && !dataDigits.includes(d)) || /\d(?:\.\d+)?\s*%/.test(answer);

  return {
    code,
    question,
    asOf: formatKST(),
    model: cfg.model,
    answerable: answerable && (facts.length > 0 || rumors.length > 0 || answer.length > 0),
    answer,
    calcUnverified,
    facts,
    rumors,
    droppedCount: dropped,
    caveat,
    sourceCounts: { news: newsItems.length, board: board.length, telegram: telegram.length },
  };
}
