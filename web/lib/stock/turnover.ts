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
    // 거래량 0(거래정지 등)은 null로 drop — scripts/float_ratio.py:vol_turnover(`volume and volume>0`)와 동일.
    floatSharesEff && floatSharesEff > 0 && c.volume > 0 ? r1((c.volume / floatSharesEff) * 100) : null
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
  const seriesAll = turnoverSeries(candles, eff); // index-aligned(거래정지일 null)
  const series = seriesAll.filter((x): x is number => x != null); // 통계 풀(0거래량·null 제외)
  // 오늘 = '마지막 캔들' 그대로 — 오늘이 거래정지(거래량 0)면 today=null(직전일 값으로 오표기 방지)
  const today = seriesAll[seriesAll.length - 1];
  if (series.length === 0) return empty;
  const tail = (n: number) => series.slice(Math.max(0, series.length - n));
  const avg = (a: number[]) => (a.length ? a.reduce((s, x) => s + x, 0) / a.length : null);
  const avg20 = avg(tail(20));
  const cum5 = r1(tail(5).reduce((s, x) => s + x, 0));
  const cum20 = r1(tail(20).reduce((s, x) => s + x, 0));
  const rankWindow = series.length;
  const rankToday = today != null ? 1 + series.filter((x) => x > today).length : null; // 1 = 역대 최고
  return {
    basis,
    floatShares: eff,
    today: today != null ? r1(today) : null,
    avg20: avg20 != null ? r1(avg20) : null,
    todayVsAvg: today != null && avg20 && avg20 > 0 ? r1(today / avg20) : null,
    cum5,
    cum20,
    rankWindow,
    rankToday,
    percentile: rankToday != null ? Math.round((1 - (rankToday - 1) / rankWindow) * 100) : null,
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
  // 시점별 누수 방지(lookahead): day i의 percentile·avg20은 candles[0..i]만으로 산정(미래 캔들 미반영).
  const pctileAt = (i: number, v: number | null): number | null => {
    if (v == null) return null;
    const past = series.slice(0, i + 1).filter((x): x is number => x != null);
    if (past.length === 0) return null;
    return Math.round((past.filter((x) => x <= v).length / past.length) * 100);
  };
  const avg20At = (i: number): number | null => {
    const win = series.slice(Math.max(0, i - 19), i + 1).filter((x): x is number => x != null);
    return win.length ? win.reduce((s, x) => s + x, 0) / win.length : null;
  };
  const flowByDate = new Map<string, FlowDay>();
  for (const f of flowDaily) flowByDate.set(f.date, f);

  const start = Math.max(1, candles.length - lookback);
  // 전 구간 고가등락률(고가/전일종가-1, %) — 폭발 감지용
  const hpAll: (number | null)[] = candles.map((c, i) =>
    i > 0 && candles[i - 1].close > 0 ? (c.high / candles[i - 1].close - 1) * 100 : null
  );
  // 큰 급등(고가등락률≥15%). radar.py 폭발 정의(고가등락률+회전율, 종가 무관)와 정합 — 빨간 마감 캐털리스트도 폭발.
  const isBigSpike = (j: number): boolean => j > 0 && hpAll[j] != null && hpAll[j]! >= EXPLOSION_HIGH_PCT;
  // 표시용 '최근 폭발'엔 종가 상승까지 요구 — 판정 당일 음봉(윗꼬리 큰)이 자신을 '직전 폭발'로 자기지목하는 표시 오류 차단.
  const isUpExplosion = (j: number): boolean => isBigSpike(j) && candles[j].close >= candles[j - 1].close;
  let recentExplosion: { date: string; highPct: number } | null = null;
  for (let i = start; i < candles.length; i++) {
    if (isUpExplosion(i)) recentExplosion = { date: candles[i].date, highPct: r1(hpAll[i]!) };
  }
  // 그 날보다 '이른' 거래일(직전 lookback 내)에 큰 급등이 있었나 — 폭발→눌림→재분출 맥락. j<i라 자기지목 불가능하므로
  // 종가 조건 불필요(빨간 마감 캐털리스트 다음날 재분출도 잡아야 함 — radar.py 폭발 정의와 정합).
  const explodedBefore = (i: number): boolean => {
    for (let j = Math.max(1, i - lookback); j < i; j++) if (isBigSpike(j)) return true;
    return false;
  };

  const days: DownCandleSignal[] = [];
  for (let i = start; i < candles.length; i++) {
    const c = candles[i], prev = candles[i - 1];
    const changePct = prev.close > 0 ? r1((c.close / prev.close - 1) * 100) : 0;
    const highPct = prev.close > 0 ? r1((c.high / prev.close - 1) * 100) : null;
    const isDown = c.close < prev.close;
    const span = c.high - c.low;
    // 실제 꼬리(시가·종가 몸통 기준): 윗꼬리=(고가−몸통상단)/레인지, 아래꼬리=(몸통하단−저가)/레인지.
    // 아래꼬리 큰 음봉 = 저가에서 받힘(매수 흔적), 윗꼬리 큰 = 상단 거부 — '아래꼬리' 라벨이 실제 꼬리와 일치.
    const bodyTop = Math.max(c.open, c.close), bodyBot = Math.min(c.open, c.close);
    const upperWickPct = span > 0 ? Math.round(((c.high - bodyTop) / span) * 100) / 100 : null;
    const lowerWickPct = span > 0 ? Math.round(((bodyBot - c.low) / span) * 100) / 100 : null;
    const ftPct = series[i];
    const f = flowByDate.get(c.date);
    const foreignNet = f ? f.foreign : null;
    const organNet = f ? f.organ : null;
    const instNet = foreignNet != null || organNet != null ? (foreignNet ?? 0) + (organNet ?? 0) : null;
    const p = pctileAt(i, ftPct); // 시점별 백분위(미래 미반영)
    const avg20i = avg20At(i); // 시점별 20일 평균(미래 미반영)
    const extreme = ftPct != null && ((p != null && p >= 80) || (avg20i != null && avg20i > 0 && ftPct >= avg20i * 3));
    const afterExplosion = explodedBefore(i); // 직전 lookback 내에 폭발이 있었나

    let label: DownCandleSignal["label"] = "중립";
    if (isDown && extreme && afterExplosion) label = "재분출후보";
    else if (isDown && instNet != null && instNet > 0 && lowerWickPct != null && lowerWickPct >= BIG_WICK && ftPct != null && avg20i != null && avg20i > 0 && ftPct >= avg20i) label = "매집후보";
    else if (isDown && ftPct != null && avg20i != null && avg20i > 0 && ftPct < avg20i && instNet != null && instNet < 0) label = "분산우려";

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
