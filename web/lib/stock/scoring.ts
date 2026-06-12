// 판정 엔진 — analyzer/run.py:score()의 confluence를 포팅하고
// 컨센서스·수급·이벤트 항목을 확장한 결정론 점수(5~95) + 5단계 판정.
// 모든 가감점은 breakdown[]에 기록해 투명 공개한다 (radar score_breakdown 정신).
// LLM 미사용 · 매수 추천 아님(참고 구간).

import type {
  EventSection,
  FlowSection,
  MarketAlert,
  NewsSection,
  PriceSection,
  TechnicalSection,
  VerdictLevel,
  VerdictSection,
} from "@/types/stock";

interface VerdictInputs {
  close: number;
  technical: TechnicalSection;
  news: NewsSection | null;
  price: PriceSection | null;
  flow: FlowSection | null;
  events: EventSection | null;
  marketAlert: MarketAlert | null;
  isManagement: boolean;
  /** 당일 분봉 스파크 (옵션 — 미전달 시 점수 무영향, 표시 전용 모드) */
  spark?: { maxVolX: number | null } | null;
}

export function computeVerdict(inp: VerdictInputs): VerdictSection {
  const { close, technical: t, news, price, flow, events, marketAlert, isManagement, spark } = inp;
  let p = 40;
  const breakdown: VerdictSection["breakdown"] = [];
  const cautionFlags: string[] = [];
  const add = (label: string, delta: number, group: VerdictSection["breakdown"][0]["group"]) => {
    p += delta;
    breakdown.push({ label, delta, group });
  };

  // ── 기술 confluence (analyzer/run.py:62-94 동일 가중) ──
  const cs = t.closeStrength;
  if (cs !== null && cs >= 0.7) add(`강세마감(${cs})`, 8, "기술");
  else if (cs !== null && cs < 0.4) add("약한 마감", -4, "기술");
  if (t.maAligned) add("정배열(5>20>60)", 6, "기술");
  if (t.macd && (t.macd.goldenCross || (t.macd.aboveZero && t.macd.bullish)))
    add("MACD 강세", 7, "기술");
  if (t.rsi) {
    if (t.rsi.value >= 80) {
      add(`RSI ${t.rsi.value} 과매수`, -12, "기술");
      cautionFlags.push(`RSI ${t.rsi.value} 과매수`);
    } else if (t.rsi.value >= 50 && t.rsi.value < 70) {
      add(`RSI ${t.rsi.value} 강세`, 5, "기술");
    }
  }
  if (t.stochastic) {
    if (t.stochastic.goldenCross && !t.stochastic.overbought)
      add("스토캐스틱 골든크로스", 5, "기술");
    else if (t.stochastic.overbought) {
      add("스토캐스틱 과매수", -6, "기술");
      cautionFlags.push("스토캐스틱 과매수");
    }
  }
  if (t.ichimoku.available && t.ichimoku.aboveCloud && t.ichimoku.tenkanGtKijun)
    add("일목 구름 위", 7, "기술");
  if ((t.volumeVs20d ?? 0) >= 1.5) add(`거래량 ${t.volumeVs20d}배`, 4, "기술");
  // 분봉 스파크 — radar suspicion_score의 (max_x−8)/24 스케일 1/3 (8배=0, 32배=+5 상한)
  const sparkMaxX = spark?.maxVolX ?? null;
  if (sparkMaxX !== null) {
    const sparkDelta =
      Math.round(Math.min(5, Math.max(0, ((sparkMaxX - 8) / 24) * 5)) * 10) / 10;
    if (sparkDelta > 0) add(`분봉 스파크 ${sparkMaxX}배`, sparkDelta, "기술");
  }

  // ── 재료 뉴스 (run.py:96-101 동일 가중) ──
  if (news) {
    if (news.summary.sentiment === "호재") add("재료 호재 우세", 6, "재료");
    else if (news.summary.sentiment === "악재") {
      add("재료 악재 우세", -12, "재료");
      cautionFlags.push("악재 뉴스 우세");
    }
    const impDelta = Math.min(6, news.summary.importance * 0.7);
    if (impDelta > 0 && news.summary.relevantCount > 0)
      add(`재료 중요도 ${news.summary.importance}`, Math.round(impDelta * 10) / 10, "재료");
  }

  // ── 애널리스트 컨센서스 ──
  const cons = price?.consensus ?? null;
  if (cons) {
    if (cons.upsidePct >= 15) add(`목표가 여력 +${Math.round(cons.upsidePct)}%`, 6, "컨센서스");
    else if (cons.upsidePct >= 5) add(`목표가 여력 +${Math.round(cons.upsidePct)}%`, 3, "컨센서스");
    else if (cons.upsidePct < 0) {
      add(`목표가 하회 ${Math.round(cons.upsidePct)}%`, -6, "컨센서스");
      cautionFlags.push("현재가가 컨센서스 목표가 상회");
    }
    if (cons.recommMean >= 4.0) add("투자의견 매수 우세", 2, "컨센서스");
  }

  // ── 외인/기관 수급 (최근 5거래일) ──
  if (flow) {
    const f = flow.summary;
    if (f.foreignNet5 > 0) add("외인 5일 순매수", 4, "수급");
    if (f.organNet5 > 0) add("기관 5일 순매수", 3, "수급");
    if (f.foreignNet5 <= 0 && f.organNet5 <= 0) {
      add("외인·기관 동반 매도", -5, "수급");
      cautionFlags.push("외인·기관 동반 순매도");
    }
    if (f.foreignNetDays5 >= 4) add("외인 연속 매집", 2, "수급");
  }

  // ── KRX 시장경보 (주의=플래그만, 경고/위험=감점 — 이상급등·단기과열 지정) ──
  if (marketAlert) {
    if (marketAlert.level === "위험") {
      add("투자위험 종목 지정", -15, "시장경보");
      cautionFlags.push("거래소 투자위험 종목 — 매매거래 정지 가능성 등 최고 수위 경보");
    } else if (marketAlert.level === "경고") {
      add("투자경고 종목 지정", -8, "시장경보");
      cautionFlags.push("거래소 투자경고 종목 — 이상급등 경보, 추격 매수 주의");
    } else {
      cautionFlags.push("거래소 투자주의 종목 지정");
    }
  }
  if (isManagement) {
    add("관리종목 지정", -15, "시장경보");
    cautionFlags.push("관리종목 — 상장폐지 사유 발생 등 중대 부실, 상장폐지 위험");
  }

  // ── 이벤트 민감도 (방향성 불명 — 소폭 가점 + 변동성 주의) ──
  let bigEventNear = false;
  if (events && events.totalScore > 0) {
    add("이벤트 모멘텀", Math.min(5, Math.round(events.totalScore * 0.4)), "이벤트");
    for (const m of events.matched) {
      if (m.dday <= 3 && m.importance >= 8) {
        bigEventNear = true;
        cautionFlags.push(`${m.title} D-${m.dday} 변동성 주의`);
        break;
      }
    }
  }

  const score = Math.max(5, Math.min(95, Math.round(p)));

  // ── 5단계 판정 ──
  let level: VerdictLevel =
    score >= 75 ? "강한 매수신호" : score >= 62 ? "매수 우위" : score >= 38 ? "중립" : "매도 우위";
  // 과열 오버라이드: 점수가 높아도 과매수·52주 고점 폭주 상태면 추격 경고
  const near52High =
    price?.high52 != null && price.high52 > 0 && close >= price.high52 * 0.98;
  const overheated =
    (t.rsi?.value ?? 0) >= 80 ||
    t.stochastic?.overbought === true ||
    (near52High && (t.volumeVs20d ?? 0) >= 3) ||
    marketAlert?.level === "경고" || // 거래소가 직접 과열로 지정한 종목은
    marketAlert?.level === "위험"; //  "매수" 계열 판정을 내지 않는다
  if (score >= 62 && overheated) level = "관망·과열";
  if (level === "강한 매수신호" && bigEventNear) level = "매수 우위"; // 대형 이벤트 직전 상한
  // 관리종목은 부실 사유 지정 — 과열과 별개로 매수 계열 판정을 금지
  if (isManagement && (level === "강한 매수신호" || level === "매수 우위")) level = "중립";

  // ── 지지/저항 (현재가 기준 가까운 순) ──
  const supports: VerdictSection["supports"] = [];
  const resistances: VerdictSection["resistances"] = [];
  const candidates: { label: string; price: number | null | undefined }[] = [
    { label: "5일선", price: t.ma5 },
    { label: "20일선", price: t.ma20 },
    { label: "60일선", price: t.ma60 },
    { label: "일목 구름 상단", price: t.ichimoku.cloudTop },
    { label: "일목 구름 하단", price: t.ichimoku.cloudBot },
    { label: "52주 고가", price: price?.high52 },
    { label: "52주 저가", price: price?.low52 },
  ];
  for (const c of candidates) {
    if (c.price == null || c.price <= 0) continue;
    if (c.price < close) supports.push({ label: c.label, price: Math.round(c.price) });
    else if (c.price > close) resistances.push({ label: c.label, price: Math.round(c.price) });
  }
  supports.sort((a, b) => b.price - a.price); // 가까운 지지 먼저
  resistances.sort((a, b) => a.price - b.price);

  // ── 요약 문장 (규칙 템플릿 — LLM 아님) ──
  const techUps = breakdown.filter((b) => b.group === "기술" && b.delta > 0).length;
  const parts: string[] = [`기술 강세 신호 ${techUps}개`];
  if (flow) {
    const f = flow.summary;
    if (f.foreignNet5 > 0 && f.organNet5 > 0) parts.push("외인·기관 동반 순매수");
    else if (f.foreignNet5 > 0) parts.push("외인 순매수");
    else if (f.organNet5 > 0) parts.push("기관 순매수");
    else parts.push("수급 이탈");
  }
  if (news && news.summary.sentiment !== "중립") parts.push(`재료 ${news.summary.sentiment}`);
  if (cons && cons.upsidePct >= 5) parts.push(`목표가 여력 +${Math.round(cons.upsidePct)}%`);
  const summary = `${parts.join(", ")} — 현재 "${level}" 구간으로 판정합니다.`;

  return {
    level,
    score,
    breakdown,
    cautionFlags,
    entry: Math.round(close),
    target: Math.round(close * 1.05),
    stop: Math.round(close * 0.97),
    supports: supports.slice(0, 2),
    resistances: resistances.slice(0, 2),
    summary,
  };
}
