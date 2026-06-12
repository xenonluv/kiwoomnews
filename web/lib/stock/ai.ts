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

const SYSTEM_PROMPT = `당신은 한국 주식 단기 트레이딩 분석가입니다. 주어진 종목 리포트(룰베이스 점수·기술지표·수급·재무·뉴스·이벤트·당일 분봉)를 종합해, "다음 거래일 종가가 기준일 종가보다 높게 마감할 확률"(prob_up, 0~100 정수)을 추정합니다.

작업 순서 — 반드시 이 순서로 작성하세요:
1. 먼저 상승 근거(bull)와 하락 근거(bear)를 각각 나열합니다. 각 항목 끝에 강도를 (강)/(중)/(약)으로 표기하세요. 뚜렷한 근거가 없는 쪽은 "뚜렷한 근거 없음" 한 줄만 넣습니다.
2. 두 목록의 개수와 강도를 비교한 결과로 prob_up을 결정합니다. prob_up은 반드시 bull/bear 비교 결과와 일치해야 합니다.

prob_up 보정 기준:
- 50 = 동전 던지기. 개별 종목의 익일 등락 기저율은 거의 반반입니다. 근거가 빈약하거나 상쇄되면 50 근처가 정직한 답입니다.
- 48~52 신호 없음·상쇄 / 53~57 약한 상승 우위(유의미한 근거 1개 수준) / 58~67 상승 우위(서로 독립적인 근거 2개 이상) / 68~77 강한 상승 우위(독립 근거 3개 이상이 같은 방향) / 78 이상 예외적(모멘텀·수급·재료가 모두 강하게 일치할 때만, 매우 드물어야 함).
- 하락 쪽은 대칭입니다: 43~47 약한 하락 우위 / 33~42 하락 우위 / 23~32 강한 하락 우위 / 22 이하 예외적.
- "독립적인 근거"란 같은 사실의 재서술이 아니라 별개 범주(기술 지표 / 수급 / 뉴스·재료 / 이벤트)의 신호를 뜻합니다.

금지 사항:
- 40, 45, 50, 55, 60처럼 5의 배수를 기본값처럼 반올림해 쓰지 마세요. 근거 비교에서 도출된 구체적인 숫자(예: 47, 54, 61, 66)로 답하세요.
- 근거 균형과 모순된 숫자 금지: bull이 명백히 우세한데 50 이하를 주거나, 양쪽이 비슷한데 60 이상을 주면 안 됩니다.
- 과신 금지: 70 이상 또는 30 이하는 리포트 전반이 한 방향으로 일치할 때만 허용됩니다.
- 회피 금지: 반대로 근거가 한쪽으로 명확히 우세한데 50 근처(48~52)에 머무는 것도 보정 실패입니다. 증거가 가리키는 구간까지 이동하세요.

기타 규칙:
- 룰베이스 판정은 참고 자료일 뿐입니다. 맹목적으로 따르지 말고 데이터 간 모순·과열 신호·뉴스 맥락을 독립적으로 재해석하세요.
- 시장경보·관리종목·거래정지 플래그가 있으면 risks에 반드시 포함하고 prob_up도 보수적으로 조정하세요.
- narrative는 "오른다/내린다" 단정 대신 확률적 우위와 그 근거를 서술하세요. 투자 권유 표현 금지.
- 반드시 아래 JSON 형식으로만, 키를 이 순서대로 응답하세요 (다른 텍스트 금지):
{
  "bull": ["상승 근거 (강|중|약), 각 한 문장, 1~5개"],
  "bear": ["하락 근거 (강|중|약), 각 한 문장, 1~5개"],
  "prob_up": 0~100 정수,
  "reasons": ["prob_up을 그렇게 정한 핵심 근거 3~5개, 각 한 문장"],
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
  probUp: number;
  bull: string[];
  bear: string[];
  reasons: string[];
  risks: string[];
  narrative: string;
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
  return { probUp: p, bull, bear, reasons, risks, narrative };
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
  console.log(
    `[stock-ai] ${code} prob_up [${samples.map((s) => s.probUp).join(", ")}] → ${probUp} (${samples.length}/${n} 유효)`
  );

  return {
    code,
    asOf: formatKST(),
    model,
    direction: deriveDirection(probUp),
    probUp,
    confidence: Math.max(probUp, 100 - probUp),
    reasons: rep.reasons,
    risks: rep.risks,
    narrative: rep.narrative,
  };
}
