// 유통주식 회전율(거래량/유통주식수) 정밀 분석 + 음봉 매집/흔들기/분산 판별 신호.
// ⚠ 파이썬 동기화: floatTurnoverPct = volume/(상장주식수×유동비율)×100, 1dp 는
//   scripts/float_ratio.py:vol_turnover 와 동일 산식이어야 한다(산식 변경 시 양쪽 같이).
// 거래량 기준(radar 정의)이며, report.ts 의 price.turnoverPct(거래대금/유통시총)와는 별개 지표.
//
// 실증 교훈(광주신세계 037710·동양파일 228340): 폭발→음봉눌림→익일 +30% 케이스의 음봉일은
// 외인/기관 순매도 + 큰 윗꼬리였는데도 급등 → 꼬리·수급 중심 매집판정은 거꾸로 본다(개인 주도 테마).
// 그래서 음봉 판별은 ① 회전율 역대급(폭발일 이상) ② 직전 폭발 연속성 을 최우선으로 둔다.

import type {
  Candle,
  FlowDay,
  FloatTurnoverSection,
  DownCandleSection,
  DownCandleSignal,
} from "@/types/stock";

const r1 = (n: number) => Math.round(n * 10) / 10;

/** 일별 회전율 시계열(%, candles와 같은 순서). floatSharesEff<=0이면 전부 null. */
function turnoverSeries(candles: Candle[], floatSharesEff: number | null): (number | null)[] {
  return candles.map((c) =>
    floatSharesEff && floatSharesEff > 0 ? r1((c.volume / floatSharesEff) * 100) : null
  );
}

/**
 * 유통주식수 기준 회전율 정밀.
 * @param floatShares 유통주식수(상장×유동비율). null이면 fallbackListed(상장주식수 추정)로 cap 폴백.
 */
export function computeFloatTurnover(
  candles: Candle[],
  floatShares: number | null,
  fallbackListed: number | null
): FloatTurnoverSection {
  const basis: "float" | "cap" = floatShares && floatShares > 0 ? "float" : "cap";
  const eff = floatShares && floatShares > 0 ? floatShares : fallbackListed && fallbackListed > 0 ? fallbackListed : null;
  const empty: FloatTurnoverSection = {
    basis, floatShares: eff, today: null, avg20: null, todayVsAvg: null,
    cum5: null, cum20: null, rankWindow: candles.length, rankToday: null, percentile: null,
  };
  if (!eff || candles.length === 0) return empty;
  const series = turnoverSeries(candles, eff).filter((x): x is number => x != null);
  if (series.length === 0) return empty;
  const today = series[series.length - 1];
  const tail = (n: number) => series.slice(Math.max(0, series.length - n));
  const avg = (a: number[]) => (a.length ? a.reduce((s, x) => s + x, 0) / a.length : null);
  const avg20 = avg(tail(20));
  const cum5 = r1(tail(5).reduce((s, x) => s + x, 0));
  const cum20 = r1(tail(20).reduce((s, x) => s + x, 0));
  const rankToday = 1 + series.filter((x) => x > today).length; // 1 = 역대 최고
  const rankWindow = series.length;
  return {
    basis,
    floatShares: eff,
    today: r1(today),
    avg20: avg20 != null ? r1(avg20) : null,
    todayVsAvg: avg20 && avg20 > 0 ? r1(today / avg20) : null,
    cum5,
    cum20,
    rankWindow,
    rankToday,
    percentile: Math.round((1 - (rankToday - 1) / rankWindow) * 100),
  };
}

const EXPLOSION_HIGH_PCT = 15; // 직전 '폭발' 감지 하한(고가등락률 %) — 폭발→눌림→재분출 맥락
const BIG_WICK = 0.6;

/**
 * 음봉(하락일) 매집/흔들기/분산 판별 신호. 최근 lookback 거래일에 대해 일별 라벨 + 종합.
 * 라벨 우선순위(회전율·폭발연속성 우선): 재분출후보 > 매집후보 > 분산우려 > 중립.
 * - 재분출후보: 음봉 + 회전율 역대급(percentile≥80 또는 20일평균의 3배↑) + 직전 폭발(그 전 lookback 내) — 윗꼬리·기관매도 무관.
 * - 매집후보: 음봉 + 외인/기관 순매수 + 큰 아래꼬리 + 고회전(기관 매집형, 보조).
 * - 분산우려: 음봉 + 회전율 급감(<20일평균) + 수급 순매도. (윗꼬리 단독 분산 단정 금지)
 */
export function computeDownCandles(
  candles: Candle[],
  flowDaily: FlowDay[],
  ft: FloatTurnoverSection,
  lookback = 10
): DownCandleSection {
  if (candles.length < 2) return { days: [], overall: "정보부족", recentExplosion: null };
  const eff = ft.floatShares;
  const series = turnoverSeries(candles, eff);
  const sortedTurn = series.filter((x): x is number => x != null).slice().sort((a, b) => a - b);
  const pctile = (v: number | null): number | null => {
    if (v == null || sortedTurn.length === 0) return null;
    const below = sortedTurn.filter((x) => x <= v).length;
    return Math.round((below / sortedTurn.length) * 100);
  };
  const avg20 = ft.avg20;
  const flowByDate = new Map<string, FlowDay>();
  for (const f of flowDaily) flowByDate.set(f.date, f);

  const start = Math.max(1, candles.length - lookback);
  // 전 구간 고가등락률(고가/전일종가-1, %) — 폭발 감지용
  const hpAll: (number | null)[] = candles.map((c, i) =>
    i > 0 && candles[i - 1].close > 0 ? (c.high / candles[i - 1].close - 1) * 100 : null
  );
  // 표시용 '최근 폭발' = lookback 창 안에서 가장 최근(날짜 max) 폭발일
  let recentExplosion: { date: string; highPct: number } | null = null;
  for (let i = start; i < candles.length; i++) {
    if (hpAll[i] != null && hpAll[i]! >= EXPLOSION_HIGH_PCT) {
      recentExplosion = { date: candles[i].date, highPct: r1(hpAll[i]!) };
    }
  }
  // 그 날보다 '이른' 거래일(직전 lookback 내)에 폭발이 있었나 — 폭발→눌림→재분출 맥락
  const explodedBefore = (i: number): boolean => {
    for (let j = Math.max(1, i - lookback); j < i; j++) {
      if (hpAll[j] != null && hpAll[j]! >= EXPLOSION_HIGH_PCT) return true;
    }
    return false;
  };

  const days: DownCandleSignal[] = [];
  for (let i = start; i < candles.length; i++) {
    const c = candles[i], prev = candles[i - 1];
    const changePct = prev.close > 0 ? r1((c.close / prev.close - 1) * 100) : 0;
    const highPct = prev.close > 0 ? r1((c.high / prev.close - 1) * 100) : null;
    const isDown = c.close < prev.close;
    const span = c.high - c.low;
    const upperWickPct = span > 0 ? Math.round(((c.high - c.close) / span) * 100) / 100 : null;
    const lowerWickPct = span > 0 ? Math.round(((c.close - c.low) / span) * 100) / 100 : null;
    const ftPct = series[i];
    const f = flowByDate.get(c.date);
    const foreignNet = f ? f.foreign : null;
    const organNet = f ? f.organ : null;
    const instNet = foreignNet != null || organNet != null ? (foreignNet ?? 0) + (organNet ?? 0) : null;
    const p = pctile(ftPct);
    const extreme = ftPct != null && ((p != null && p >= 80) || (avg20 != null && avg20 > 0 && ftPct >= avg20 * 3));
    const afterExplosion = explodedBefore(i); // 직전 lookback 내에 폭발이 있었나

    let label: DownCandleSignal["label"] = "중립";
    if (isDown && extreme && afterExplosion) label = "재분출후보";
    else if (isDown && instNet != null && instNet > 0 && lowerWickPct != null && lowerWickPct >= BIG_WICK && ftPct != null && avg20 != null && ftPct >= avg20) label = "매집후보";
    else if (isDown && ftPct != null && avg20 != null && ftPct < avg20 && instNet != null && instNet < 0) label = "분산우려";

    days.push({ date: c.date, isDown, changePct, highPct, upperWickPct, lowerWickPct, floatTurnoverPct: ftPct, foreignNet, organNet, label });
  }

  const downs = days.filter((d) => d.isDown);
  const order: DownCandleSignal["label"][] = ["재분출후보", "매집후보", "분산우려", "중립"];
  let overall: DownCandleSection["overall"] = "정보부족";
  if (downs.length > 0) {
    overall = order.find((lab) => downs.some((d) => d.label === lab)) ?? "중립";
  }
  return { days, overall, recentExplosion };
}
