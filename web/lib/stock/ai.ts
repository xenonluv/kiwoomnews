// AI(LLM) 심층 분석 — 룰베이스 리포트(buildStockReport) 전체를 컴팩트하게
// 직렬화해 Moonshot Kimi API(OpenAI 호환)에 전달하고, 익일 방향(상승/하락/관망)
// 구조화 판단을 받아온다. 키는 서버 환경변수 전용(MOONSHOT_API_KEY) — 브라우저 미노출.
// LLM 응답은 수동 타입가드로 검증하고, 실패 시 AiUnavailableError로 우아하게 강등.

import type { AiAnalysis, AiDirection, StockReport } from "@/types/stock";
import { buildStockReport } from "./report";
import { formatKST } from "./parse";

export class AiConfigError extends Error {}
export class AiUnavailableError extends Error {}

const DIRECTIONS: AiDirection[] = ["상승", "하락", "관망"];
// kimi-k2.6은 reasoning 모델이라 응답에 15~40초 소요 (실측) — 라우트의
// maxDuration=60과 짝을 이뤄 55초까지 대기.
const TIMEOUT_MS = 55_000;

function env(name: string): string | null {
  const v = process.env[name];
  return v && v.trim() !== "" ? v.trim() : null;
}

/* ── 리포트 → 프롬프트 직렬화 (토큰 절약: 원시 캔들 제외, 요약만) ── */

const pct = (n: number | null | undefined) => (n == null ? "?" : `${n}%`);

function serializeForPrompt(r: StockReport): string {
  const L: string[] = [];
  L.push(`종목: ${r.name}(${r.code}) ${r.market ?? ""} · 기준 ${r.asOf}`);
  if (r.marketAlert) L.push(`⚠ 거래소 시장경보: ${r.marketAlert.label}`);
  if (r.isManagement) L.push("⚠ 관리종목 (상장폐지 위험)");
  if (r.tradeStop) L.push("⚠ 거래정지");
  if (r.isEtf) L.push("ETF·ETN 종목");

  const p = r.price;
  if (p) {
    L.push(
      `[주가] 현재가 ${p.close.toLocaleString()}원 (${p.changePct > 0 ? "+" : ""}${p.changePct}%) · ` +
        `PER ${p.per ?? "?"} PBR ${p.pbr ?? "?"} · 52주고가 대비 ${pct(p.pctFrom52High)} 저가 대비 ${pct(p.pctFrom52Low)}` +
        (p.consensus
          ? ` · 컨센서스 목표가 ${p.consensus.targetPrice.toLocaleString()}원(상승여력 ${pct(p.consensus.upsidePct)}, 의견 ${p.consensus.recommMean}/5)`
          : "")
    );
  }

  if (r.chart && r.chart.candles.length >= 5) {
    const last5 = r.chart.candles.slice(-5);
    L.push(
      "[최근 5일 종가] " +
        last5.map((c) => `${c.date.slice(4)}: ${c.close.toLocaleString()}`).join(" · ")
    );
  }

  const t = r.technical;
  if (t) {
    const items: string[] = [];
    items.push(`MA ${t.maAligned ? "정배열(5>20>60)" : "정배열 아님"} (5일 ${t.ma5 ?? "?"} / 20일 ${t.ma20 ?? "?"} / 60일 ${t.ma60 ?? "?"})`);
    if (t.closeStrength != null) items.push(`종가강도 ${Math.round(t.closeStrength * 100)}/100`);
    if (t.volumeVs20d != null) items.push(`거래량 20일평균 대비 ${t.volumeVs20d}배`);
    if (t.macd)
      items.push(
        `MACD ${t.macd.goldenCross ? "골든크로스" : t.macd.bullish ? "강세" : "약세"}${t.macd.aboveZero ? "(0선 위)" : "(0선 아래)"}`
      );
    if (t.rsi) items.push(`RSI ${t.rsi.value} (${t.rsi.zone})`);
    if (t.stochastic)
      items.push(
        `스토캐스틱 K${t.stochastic.k}/D${t.stochastic.d} ${t.stochastic.overbought ? "과매수" : t.stochastic.goldenCross ? "골든크로스" : t.stochastic.bullish ? "강세" : "약세"}`
      );
    if (t.ichimoku.available)
      items.push(
        `일목균형표 ${t.ichimoku.aboveCloud ? "구름 위" : t.ichimoku.inCloud ? "구름 안" : "구름 아래"}${t.ichimoku.tenkanGtKijun ? "·전환>기준" : ""}`
      );
    L.push(`[기술] ${items.join(" · ")}`);
  }

  const f = r.flow;
  if (f) {
    const recent3 = f.daily
      .slice(0, 3)
      .map((d) => `${d.date.slice(4)} 외인 ${d.foreign.toLocaleString()}/기관 ${d.organ.toLocaleString()}`)
      .join(" · ");
    L.push(
      `[수급] 5일 누적 외인 순매수 ${f.summary.foreignNet5.toLocaleString()}주(순매수 ${f.summary.foreignNetDays5}일) · ` +
        `기관 ${f.summary.organNet5.toLocaleString()}주(${f.summary.organNetDays5}일) · 최근: ${recent3}`
    );
  }

  const fin = r.financials;
  if (fin) {
    const h = fin.highlights;
    L.push(
      `[재무] 매출 YoY ${pct(h.revenueYoY)} · 영업이익 YoY ${pct(h.opYoY)} · 영업이익률 ${pct(h.opMargin)} · ` +
        `${h.profitable == null ? "흑자여부 불명" : h.profitable ? "흑자" : "적자"}`
    );
  }

  const n = r.news;
  if (n) {
    L.push(
      `[뉴스 요약] 종합 ${n.summary.sentiment} · 중요도 ${n.summary.importance}/10 · 영향 ${n.summary.impact} · 관련기사 ${n.summary.relevantCount}건(호재 ${n.summary.posCount}/악재 ${n.summary.negCount})`
    );
    const top = n.items.filter((i) => i.relevant).slice(0, 8);
    for (const it of top) L.push(`- (${it.sentiment}) ${it.title}`);
  }

  const e = r.events;
  if (e && e.matched.length > 0) {
    L.push(
      `[D-10 이벤트] ` +
        e.matched
          .map((m) => `${m.title}(D-${m.dday}, 중요도 ${m.importance})`)
          .join(" · ") +
        ` · 민감도 ${e.totalScore}/15`
    );
  }

  if (r.researches.length > 0) {
    L.push(`[증권사 리포트] ` + r.researches.map((x) => `${x.firm}: ${x.title}`).join(" · "));
  }

  const v = r.verdict;
  if (v) {
    L.push(
      `[룰베이스 판정] ${v.level} · 점수 ${v.score}/100 · ${v.summary}`
    );
    L.push(
      "  가감 내역: " +
        v.breakdown.map((b) => `${b.group}/${b.label} ${b.delta > 0 ? "+" : ""}${b.delta}`).join(", ")
    );
    if (v.cautionFlags.length > 0) L.push("  주의 플래그: " + v.cautionFlags.join(" / "));
  }

  if (r.warnings.length > 0) L.push("[데이터 경고] " + r.warnings.join(" / "));
  return L.join("\n");
}

const SYSTEM_PROMPT = `당신은 한국 주식 단기 트레이딩 분석가입니다. 주어진 종목 리포트(룰베이스 점수·기술지표·수급·재무·뉴스·이벤트)를 종합해 "다음 거래일"의 주가 방향을 판단합니다.

규칙:
- 룰베이스 판정은 참고 자료일 뿐, 맹목적으로 따르지 말고 데이터 간 모순·과열 신호·뉴스 맥락을 독립적으로 재해석하세요.
- 시장경보·관리종목·거래정지 플래그가 있으면 리스크에 반드시 반영하세요.
- 확신이 없으면 "관망"을 선택하고 confidence를 낮추세요. 과장 금지.
- 반드시 아래 JSON 형식으로만 응답하세요 (다른 텍스트 금지):
{
  "direction": "상승" | "하락" | "관망",
  "confidence": 0~100 정수,
  "reasons": ["핵심 근거 3~5개, 각 한 문장"],
  "risks": ["리스크 1~3개, 각 한 문장"],
  "narrative": "2~4문장의 한국어 종합 서술. 투자 권유 표현 금지."
}`;

/* ── LLM 응답 검증 (zod 미사용 — 수동 타입가드, 실패 시 즉시 에러) ── */

function strArr(v: unknown, min: number, max: number): string[] | null {
  if (!Array.isArray(v)) return null;
  const arr = v
    .filter((x): x is string => typeof x === "string" && x.trim() !== "")
    .map((x) => x.trim().slice(0, 200))
    .slice(0, max);
  return arr.length >= min ? arr : null;
}

function validate(raw: unknown): Pick<AiAnalysis, "direction" | "confidence" | "reasons" | "risks" | "narrative"> | null {
  if (typeof raw !== "object" || raw === null) return null;
  const o = raw as Record<string, unknown>;
  const direction = DIRECTIONS.find((d) => d === o.direction);
  if (!direction) return null;
  let conf = typeof o.confidence === "number" ? o.confidence : NaN;
  if (!Number.isFinite(conf)) return null;
  // 모델이 0~1 비율로 답하는 경우 실측됨 — 0~100으로 정규화
  if (conf > 0 && conf <= 1) conf = conf * 100;
  conf = Math.round(conf);
  const reasons = strArr(o.reasons, 1, 5);
  const risks = strArr(o.risks, 0, 3) ?? [];
  const narrative = typeof o.narrative === "string" ? o.narrative.trim().slice(0, 1000) : "";
  if (!reasons || narrative === "") return null;
  return {
    direction,
    confidence: Math.max(0, Math.min(100, conf)),
    reasons,
    risks,
    narrative,
  };
}

/* ── 메인 ── */

export async function buildAiAnalysis(code: string): Promise<AiAnalysis> {
  const apiKey = env("MOONSHOT_API_KEY");
  if (!apiKey) throw new AiConfigError("MOONSHOT_API_KEY 미설정");
  const baseUrl = env("MOONSHOT_BASE_URL") ?? "https://api.moonshot.ai/v1";
  const model = env("MOONSHOT_MODEL") ?? "kimi-k2.6";

  // 룰베이스 리포트를 서버 내부에서 직접 생성 (HTTP 왕복 없음)
  const report = await buildStockReport(code);

  let res: Response;
  try {
    res = await fetch(`${baseUrl}/chat/completions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${apiKey}`,
      },
      body: JSON.stringify({
        model,
        // kimi-k2.6은 temperature=1만 허용 — 기본값 사용 (지정 시 400)
        response_format: { type: "json_object" },
        messages: [
          { role: "system", content: SYSTEM_PROMPT },
          { role: "user", content: serializeForPrompt(report) },
        ],
      }),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch {
    throw new AiUnavailableError("AI 서버 연결 실패 (타임아웃/네트워크)");
  }
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    console.error(`[stock-ai] Kimi HTTP ${res.status}: ${body.slice(0, 300)}`);
    throw new AiUnavailableError(`AI 서버 오류 (HTTP ${res.status})`);
  }

  let parsed: unknown;
  try {
    const data = (await res.json()) as {
      choices?: { message?: { content?: string } }[];
    };
    parsed = JSON.parse(data.choices?.[0]?.message?.content ?? "");
  } catch {
    throw new AiUnavailableError("AI 응답 파싱 실패");
  }

  const valid = validate(parsed);
  if (!valid) throw new AiUnavailableError("AI 응답 형식 검증 실패");

  return {
    code,
    asOf: formatKST(),
    model,
    ...valid,
  };
}
