// AI(LLM) 심층 분석 — 룰베이스 리포트(buildStockReport) 전체를 컴팩트하게
// 직렬화해 Moonshot Kimi API(OpenAI 호환)에 전달하고, "익일 상승 확률"(prob_up)을
// 구조화 추정받는다. 방향(상승/하락/관망)은 확률에서 코드로 파생.
// temperature=1 고정(kimi-k2.6 제약)을 역이용해 N회 병렬 호출 → 중앙값 합의
// (self-consistency)로 단일 샘플 노이즈를 상쇄. 키는 서버 환경변수 전용.
// LLM 응답은 수동 타입가드로 검증하고, 전 샘플 실패 시 AiUnavailableError로 강등.

import { getRadar } from "@/lib/radar/repository";
import type { AiAnalysis, AiDirection, StockReport } from "@/types/stock";
import { buildStockReport } from "./report";
import { ddayKST, formatKST, ymdKST } from "./parse";

export class AiConfigError extends Error {}
export class AiUnavailableError extends Error {}

// 방향 파생 임계 (경계 포함). ⚠ 프롬프트에 노출 금지 — 모델이 임계 주변으로
// 재앵커링하는 것을 막기 위해 모델은 확률만 추정하고 매핑은 코드가 한다.
const PROB_BULL_MIN = 58; // prob_up ≥ 58 → 상승
const PROB_BEAR_MAX = 42; // prob_up ≤ 42 → 하락
const DEFAULT_SAMPLES = 3; // MOONSHOT_SAMPLES로 1~5 조절
// kimi-k2.6 reasoning은 15~120초+ 소요(실측, Vercel에서 종종 타임아웃) →
// 기본은 thinking disabled(실측 2~15초). MOONSHOT_THINKING=enabled로 켜면
// 깊은 추론 모드 + 긴 타임아웃(라우트 maxDuration=300과 짝).
const TIMEOUT_FAST_MS = 60_000;
const TIMEOUT_THINKING_MS = 280_000;

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

  // 당일 고가·되돌림 — 마지막 일봉이 KST 오늘일 때만 ("당일" 라벨 오답 방지)
  if (r.chart && r.chart.candles.length >= 2) {
    const cs = r.chart.candles;
    const today = cs[cs.length - 1];
    const prev = cs[cs.length - 2];
    if (today.date === ymdKST() && prev.close > 0) {
      const highPct = Math.round((today.high / prev.close - 1) * 1000) / 10;
      const range = today.high - prev.close;
      const fade = range > 0 ? Math.round(((today.high - today.close) / range) * 100) : null;
      L.push(
        `[당일 고가·되돌림] 장중 고가 등락 ${highPct > 0 ? "+" : ""}${highPct}%` +
          (fade != null
            ? ` · 상승분 되돌림 ${fade}% (0=고가 마감, 100=상승분 전부 반납, 100 초과=전일 종가 아래 마감)`
            : " · 전일 종가를 상회한 적 없음")
      );
    }
  }

  // 당일 분봉 스파크 — null(미수집)과 "스파크 없음"을 구분해 전달
  if (r.spark) {
    if (r.spark.clusters.length > 0) {
      L.push(
        `[당일 분봉 스파크] ${r.spark.clusters.length}건 · 최대 ${r.spark.maxVolX}배 · ` +
          r.spark.clusters
            .slice(0, 5)
            .map((c) => `${c.time} ${c.vol_x}배 ${c.pct > 0 ? "+" : ""}${c.pct}% ${c.minutes}분`)
            .join(" · ") +
          (r.spark.megaFlow
            ? " · 메가스파크(40배+) × 외인/기관 순매수 동반 — 강한 회복력 신호"
            : "")
      );
    } else {
      L.push(
        `[당일 분봉 스파크] 없음 (1분봉 ${r.spark.barCount}개 정상 수집 — 이상 거래량 미탐지)`
      );
    }
  } else {
    L.push("[당일 분봉] 데이터 없음 (휴장·주말·개장 직후)");
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
    if (top.length > 0) {
      for (const it of top) L.push(`- (${it.sentiment}) ${it.title}`);
    } else {
      // 종목 직접 언급 기사가 없는 날도 시황 기사로 업황·테마 맥락은 전달
      // (예: "반도체 장비株 급등" — 재료필터엔 비관련이지만 LLM 판단엔 유효한 배경)
      const ctx = n.items.slice(0, 5);
      if (ctx.length > 0) {
        L.push("(종목 직접 언급 기사 없음 — 아래는 같은 날 시황 기사 참고)");
        for (const it of ctx) L.push(`- (시황) ${it.title}`);
      }
    }
  }

  const e = r.events;
  if (e) {
    if (e.matched.length > 0) {
      L.push(
        `[D-10 이벤트] ` +
          e.matched
            .map((m) => `${m.title}(D-${m.dday}, 중요도 ${m.importance})`)
            .join(" · ") +
          ` · 민감도 ${e.totalScore}/15`
      );
    } else if (e.upcomingCount > 0) {
      // 테마 미매칭이어도 매크로 일정 자체는 변동성 맥락으로 유효 — 일정을 직접 나열
      const now = new Date();
      const upcoming = getRadar()
        .events.map((ev) => ({ ...ev, dday: ddayKST(ev.date, now) }))
        .filter((ev) => ev.dday >= 0 && ev.dday <= 10)
        .map((ev) => `${ev.title}(D-${ev.dday})`)
        .join(" · ");
      L.push(`[D-10 이벤트] 이 종목 테마와 직접 매칭된 이벤트는 없음 · 참고 매크로 일정: ${upcoming}`);
    }
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

// 앵커링 주의: 이 프롬프트에 확률 구간 숫자(예: "43~47")를 적으면 모델이 그 숫자로
// 군집한다 (2026-06-12 실측 — 3종목 전부 43% 동일). 최종 확률은 코드(evidenceProb)가
// 근거 강도 가중합으로 산출하므로, 프롬프트는 근거 선별·강도 판정에만 집중시킨다.
const SYSTEM_PROMPT = `당신은 한국 주식 단기 트레이딩 분석가입니다. 주어진 종목 리포트(룰베이스 점수·기술지표·수급·재무·뉴스·이벤트·당일 분봉)를 종합해, 다음 거래일 방향에 대한 상승 근거(bull)와 하락 근거(bear)를 선별하고 각 근거의 강도를 판정합니다. 최종 확률 숫자는 시스템이 당신의 근거 강도에서 산출합니다 — 당신의 핵심 임무는 근거의 선별과 정직한 강도 판정입니다.

작업 순서 — 반드시 이 순서로 작성하세요:
1. 상승 근거(bull)와 하락 근거(bear)를 각각 나열합니다. 각 항목 끝에 강도를 (강)/(중)/(약)으로 표기하세요. 뚜렷한 근거가 없는 쪽은 "뚜렷한 근거 없음" 한 줄만 넣습니다.
2. 두 목록을 종합한 당신의 감각을 prob_up(0~100 정수, 50=중립)으로 적습니다. 이 값은 참고 기록용이며 시스템 산출에 직접 쓰이지 않습니다.

강도 판정 기준 — 인색하게 매기세요:
- (강): 단독으로도 익일 방향을 좌우할 수 있는 굵직한 신호. 예: 대형 호재/악재 공시, 상한가·하한가 마감, 초대형 거래량 스파이크에 외인·기관 순매수 동반, 거래소 시장경보·관리종목 지정.
- (중): 분명한 신호지만 단독으로는 결정력이 부족한 것. 예: 이동평균 정배열, 기관 5일 순매수, 호재 뉴스 우세, MACD 골든크로스, 급등 후 저가 마감.
- (약): 미미하거나 간접적인 신호. 예: 소폭 거래량 증가, 오래된 재료, 테마 간접 연관, 단일 보조지표의 약한 신호.
- 확신이 없으면 한 단계 낮춰 적는 것이 정직한 판정입니다. 모든 항목이 (강)인 리포트는 거의 항상 과장입니다.

근거 선별 규칙:
- 같은 사실의 재서술은 별개 근거로 세지 마세요. 별개 범주(기술 지표 / 수급 / 뉴스·재료 / 이벤트 / 당일 분봉)의 독립적인 신호만 항목으로 올립니다.
- 룰베이스 판정은 참고 자료일 뿐입니다. 맹목적으로 따르지 말고 데이터 간 모순·과열 신호·뉴스 맥락을 독립적으로 재해석하세요.
- 시장경보·관리종목·거래정지 플래그가 있으면 bear와 risks에 반드시 반영하세요.
- narrative는 "오른다/내린다" 단정 대신 확률적 우위와 그 근거를 서술하세요. 투자 권유 표현 금지.
- 반드시 아래 JSON 형식으로만, 키를 이 순서대로 응답하세요 (다른 텍스트 금지):
{
  "bull": ["상승 근거 (강|중|약), 각 한 문장, 1~5개"],
  "bear": ["하락 근거 (강|중|약), 각 한 문장, 1~5개"],
  "prob_up": 0~100 정수 (참고값),
  "reasons": ["방향 판단의 핵심 근거 3~5개, 각 한 문장"],
  "risks": ["리스크 1~3개, 각 한 문장"],
  "narrative": "2~4문장의 한국어 종합 서술"
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

/** Kimi 1회 응답 (bull/bear는 추론 스캐폴딩 — API 응답에는 미포함) */
interface KimiSample {
  /** 코드 산출 확률 (근거 강도 가중합 — 앵커링 무관) */
  probUp: number;
  /** LLM이 직접 적은 원시 확률 (참고 기록용 — 어느 쪽 보정이 좋은지 추후 비교) */
  modelProb: number;
  bull: string[];
  bear: string[];
  reasons: string[];
  risks: string[];
  narrative: string;
}

// 확률 산출: LLM은 근거 강도(강/중/약 — 범주 판단)만, 숫자는 코드가 가중합으로.
// 프롬프트에 구간 숫자를 주면 모델이 그 값으로 군집하는 앵커링을 구조적으로 차단한다.
// 가중치는 초기 추정값 — ai 검증 표본(prob_bands) 누적 후 백테스트 튜닝 대상.
const STRENGTH_W: Record<string, number> = { 강: 7, 중: 3.5, 약: 1.5 };
const PROB_MIN = 12;
const PROB_MAX = 88;
const NO_EVIDENCE_RE = /뚜렷한\s*근거\s*없음/;

function evidenceSum(items: string[]): number {
  let sum = 0;
  for (const it of items) {
    if (NO_EVIDENCE_RE.test(it)) continue;
    // 끝 고정이 아닌 마지막 등장 태그 — "(강) — 부연"처럼 뒤에 말이 붙어도 강도 보존
    const tags = [...it.matchAll(/\((강|중|약)\)/g)];
    const tag = tags.length > 0 ? tags[tags.length - 1][1] : null;
    if (!tag) {
      // 태그 누락 = 프롬프트 드리프트 신호 — 조용히 깎이지 않게 관측 로그
      console.warn(`[stock-ai] 강도 태그 누락 → (중) 폴백: ${it.slice(0, 60)}`);
    }
    sum += STRENGTH_W[tag ?? "중"];
  }
  return sum;
}

function evidenceProb(bull: string[], bear: string[]): number {
  const p = 50 + evidenceSum(bull) - evidenceSum(bear);
  return Math.round(Math.max(PROB_MIN, Math.min(PROB_MAX, p)));
}

function validate(raw: unknown): KimiSample | null {
  if (typeof raw !== "object" || raw === null) return null;
  const o = raw as Record<string, unknown>;
  let p = typeof o.prob_up === "number" ? o.prob_up : NaN;
  if (!Number.isFinite(p)) return null;
  // 모델이 0~1 비율로 답하는 경우 실측됨 — 정규화 (정수 1은 1%로 그대로 둠)
  if (p > 0 && p < 1) p = p * 100;
  p = Math.max(0, Math.min(100, Math.round(p)));
  const bull = strArr(o.bull, 1, 6);
  const bear = strArr(o.bear, 1, 6);
  const reasons = strArr(o.reasons, 1, 5);
  const risks = strArr(o.risks, 0, 3) ?? [];
  const narrative = typeof o.narrative === "string" ? o.narrative.trim().slice(0, 1000) : "";
  // bull/bear 생략 = 근거 비교 없이 숫자만 낸 샘플 → 탈락 (다른 샘플이 보전)
  if (!bull || !bear || !reasons || narrative === "") return null;
  return { probUp: evidenceProb(bull, bear), modelProb: p, bull, bear, reasons, risks, narrative };
}

function deriveDirection(probUp: number): AiDirection {
  if (probUp >= PROB_BULL_MIN) return "상승";
  if (probUp <= PROB_BEAR_MAX) return "하락";
  return "관망";
}

/**
 * N샘플 합의 — prob_up 중앙값(짝수면 가운데 둘 평균 반올림).
 * 텍스트는 중앙값에 가장 가까운 샘플에서 채택, 동률이면 50에 가까운 쪽(보수적).
 */
function aggregate(samples: KimiSample[]): { probUp: number; rep: KimiSample } {
  const sorted = samples.map((s) => s.probUp).sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  const probUp =
    sorted.length % 2 === 1 ? sorted[mid] : Math.round((sorted[mid - 1] + sorted[mid]) / 2);
  let rep = samples[0];
  for (const s of samples.slice(1)) {
    const d = Math.abs(s.probUp - probUp) - Math.abs(rep.probUp - probUp);
    if (d < 0 || (d === 0 && Math.abs(s.probUp - 50) < Math.abs(rep.probUp - 50))) rep = s;
  }
  return { probUp, rep };
}

/* ── 메인 ── */

async function callKimiOnce(args: {
  baseUrl: string;
  apiKey: string;
  model: string;
  thinking: boolean;
  userContent: string;
  code: string;
}): Promise<KimiSample> {
  const { baseUrl, apiKey, model, thinking, userContent, code } = args;
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
        // thinking 미지정 시 모델 기본이 reasoning(느림) — 명시적으로 제어
        thinking: { type: thinking ? "enabled" : "disabled" },
        messages: [
          { role: "system", content: SYSTEM_PROMPT },
          { role: "user", content: userContent },
        ],
      }),
      signal: AbortSignal.timeout(thinking ? TIMEOUT_THINKING_MS : TIMEOUT_FAST_MS),
    });
  } catch (e) {
    console.error(`[stock-ai] Kimi 연결 실패 (${code}): ${e instanceof Error ? e.name : "unknown"}`);
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
  return valid;
}

export async function buildAiAnalysis(code: string): Promise<AiAnalysis> {
  const apiKey = env("MOONSHOT_API_KEY");
  if (!apiKey) throw new AiConfigError("MOONSHOT_API_KEY 미설정");
  const baseUrl = env("MOONSHOT_BASE_URL") ?? "https://api.moonshot.ai/v1";
  const model = env("MOONSHOT_MODEL") ?? "kimi-k2.6";
  const thinking = env("MOONSHOT_THINKING") === "enabled";
  const nRaw = Number.parseInt(env("MOONSHOT_SAMPLES") ?? "", 10);
  const n = Math.max(1, Math.min(5, Number.isFinite(nRaw) ? nRaw : DEFAULT_SAMPLES));

  // 룰베이스 리포트를 서버 내부에서 직접 생성 (HTTP 왕복 없음), 직렬화 1회 → N콜 재사용
  const report = await buildStockReport(code);
  const userContent = serializeForPrompt(report);

  // temperature=1 고정 제약을 역이용한 self-consistency: 병렬 N콜 → 중앙값 합의.
  // 일부 실패는 생존 샘플로 진행, 전부 실패 시에만 에러 (라우트 503 + 네거티브 캐시).
  const settled = await Promise.allSettled(
    Array.from({ length: n }, () =>
      callKimiOnce({ baseUrl, apiKey, model, thinking, userContent, code })
    )
  );
  const samples = settled
    .filter((r): r is PromiseFulfilledResult<KimiSample> => r.status === "fulfilled")
    .map((r) => r.value);
  if (samples.length === 0) {
    const first = settled.find((r) => r.status === "rejected") as
      | PromiseRejectedResult
      | undefined;
    throw first?.reason instanceof AiUnavailableError
      ? first.reason
      : new AiUnavailableError(`AI 응답 ${n}건 모두 실패`);
  }

  const { probUp, rep } = aggregate(samples);
  // LLM 원시 확률 중앙값 — 코드 산출(probUp)과의 보정 비교용 참고 기록
  const mSorted = samples.map((s) => s.modelProb).sort((a, b) => a - b);
  const mMid = Math.floor(mSorted.length / 2);
  const modelProbUp =
    mSorted.length % 2 === 1 ? mSorted[mMid] : Math.round((mSorted[mMid - 1] + mSorted[mMid]) / 2);
  console.log(
    `[stock-ai] ${code} 코드산출 [${samples.map((s) => s.probUp).join(", ")}] → ${probUp} · ` +
      `LLM원시 [${samples.map((s) => s.modelProb).join(", ")}] → ${modelProbUp} (${samples.length}/${n} 유효)`
  );

  return {
    code,
    asOf: formatKST(),
    model,
    // 같은 시점의 룰베이스 판정 동봉 — AI 확률과의 괴리를 백테스트가 기록·검증
    verdictScore: report.verdict?.score ?? null,
    verdictLevel: report.verdict?.level ?? null,
    direction: deriveDirection(probUp),
    probUp,
    modelProbUp,
    confidence: Math.max(probUp, 100 - probUp),
    reasons: rep.reasons,
    risks: rep.risks,
    narrative: rep.narrative,
  };
}
