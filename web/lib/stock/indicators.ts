// 기술적 지표 — analyzer/indicators.py 1:1 포팅 (순수 함수, 환각 없는 코드 계산).
// 입력은 일봉 배열(과거→최신 정렬), 최소 35봉 미만이면 null.

import type { Candle, TechnicalSection } from "@/types/stock";

export function sma(xs: number[], n: number): number | null {
  if (xs.length < n) return null;
  let s = 0;
  for (let i = xs.length - n; i < xs.length; i++) s += xs[i];
  return s / n;
}

/** EMA 시리즈 — 시드는 첫 n개 SMA. 길이 = len-n+1. */
function emaSeries(xs: number[], n: number): number[] {
  if (xs.length < n) return [];
  const k = 2 / (n + 1);
  let seed = 0;
  for (let i = 0; i < n; i++) seed += xs[i];
  const out = [seed / n];
  for (let i = n; i < xs.length; i++) {
    out.push(xs[i] * k + out[out.length - 1] * (1 - k));
  }
  return out;
}

export function macd(
  closes: number[],
  fast = 12,
  slow = 26,
  sig = 9
): TechnicalSection["macd"] {
  if (closes.length < slow + sig) return null;
  const ef = emaSeries(closes, fast);
  const es = emaSeries(closes, slow);
  const tail = Math.min(ef.length, es.length);
  const line: number[] = [];
  for (let i = 0; i < tail; i++) {
    line.push(ef[ef.length - tail + i] - es[es.length - tail + i]);
  }
  const sigs = emaSeries(line, sig);
  if (sigs.length === 0) return null;
  const m = line[line.length - 1];
  const s = sigs[sigs.length - 1];
  const pm = line.length >= 2 ? line[line.length - 2] : m;
  const ps = sigs.length >= 2 ? sigs[sigs.length - 2] : s;
  return {
    macd: round2(m),
    signal: round2(s),
    hist: round2(m - s),
    aboveZero: m > 0,
    goldenCross: pm <= ps && m > s, // 시그널 상향 돌파
    bullish: m > s,
  };
}

export function rsi(closes: number[], n = 14): TechnicalSection["rsi"] {
  if (closes.length < n + 1) return null;
  const gains: number[] = [];
  const losses: number[] = [];
  for (let i = 1; i < closes.length; i++) {
    const d = closes[i] - closes[i - 1];
    gains.push(Math.max(d, 0));
    losses.push(Math.max(-d, 0));
  }
  let ag = 0;
  let al = 0;
  for (let i = 0; i < n; i++) {
    ag += gains[i];
    al += losses[i];
  }
  ag /= n;
  al /= n;
  for (let i = n; i < gains.length; i++) {
    // Wilder 평활
    ag = (ag * (n - 1) + gains[i]) / n;
    al = (al * (n - 1) + losses[i]) / n;
  }
  const r = al === 0 ? 100 : 100 - 100 / (1 + ag / al);
  const value = Math.round(r * 10) / 10;
  const zone = r >= 80 ? "과매수" : r <= 30 ? "과매도" : r >= 50 ? "강세" : "약세";
  return { value, zone };
}

export function stochasticSlow(
  highs: number[],
  lows: number[],
  closes: number[],
  n = 14,
  k = 3,
  d = 3
): TechnicalSection["stochastic"] {
  if (closes.length < n + k + d) return null;
  const fastK: number[] = [];
  for (let i = n - 1; i < closes.length; i++) {
    let hh = -Infinity;
    let ll = Infinity;
    for (let j = i - n + 1; j <= i; j++) {
      if (highs[j] > hh) hh = highs[j];
      if (lows[j] < ll) ll = lows[j];
    }
    fastK.push(hh > ll ? (100 * (closes[i] - ll)) / (hh - ll) : 50.0);
  }
  const trailingAvg = (xs: number[], end: number, m: number) => {
    let s = 0;
    for (let j = end - m + 1; j <= end; j++) s += xs[j];
    return s / m;
  };
  const slowK: number[] = [];
  for (let i = k - 1; i < fastK.length; i++) slowK.push(trailingAvg(fastK, i, k));
  const slowD: number[] = [];
  for (let i = d - 1; i < slowK.length; i++) slowD.push(trailingAvg(slowK, i, d));
  if (slowD.length === 0) return null;
  const K = slowK[slowK.length - 1];
  const D = slowD[slowD.length - 1];
  const pk = slowK.length >= 2 ? slowK[slowK.length - 2] : K;
  const pd = slowD.length >= 2 ? slowD[slowD.length - 2] : D;
  return {
    k: Math.round(K * 10) / 10,
    d: Math.round(D * 10) / 10,
    goldenCross: pk <= pd && K > D,
    overbought: K >= 80,
    bullish: K > D,
  };
}

export function ichimoku(
  highs: number[],
  lows: number[],
  closes: number[]
): TechnicalSection["ichimoku"] {
  const midpoint = (n: number, idx: number): number | null => {
    if (idx - n + 1 < 0) return null;
    let hh = -Infinity;
    let ll = Infinity;
    for (let j = idx - n + 1; j <= idx; j++) {
      if (highs[j] > hh) hh = highs[j];
      if (lows[j] < ll) ll = lows[j];
    }
    return (hh + ll) / 2;
  };
  const i = closes.length - 1;
  const tenkan = midpoint(9, i);
  const kijun = midpoint(26, i);
  // 오늘 위치에 그려지는 구름 = 26봉 전에 계산된 선행스팬
  const j = i - 26;
  if (j < 0 || tenkan === null || kijun === null) return { available: false };
  const t26 = midpoint(9, j);
  const k26 = midpoint(26, j);
  const spanA = t26 !== null && k26 !== null ? (t26 + k26) / 2 : null;
  const spanB = midpoint(52, j);
  if (spanA === null || spanB === null) return { available: false };
  const cloudTop = Math.max(spanA, spanB);
  const cloudBot = Math.min(spanA, spanB);
  const close = closes[closes.length - 1];
  return {
    available: true,
    aboveCloud: close > cloudTop,
    inCloud: cloudBot <= close && close <= cloudTop,
    tenkanGtKijun: tenkan > kijun,
    tenkan: round1(tenkan),
    kijun: round1(kijun),
    cloudTop: round1(cloudTop),
    cloudBot: round1(cloudBot),
  };
}

type WeekBar = Candle & { days: number }; // days=주중 거래일 수(이번주 미완성 판별)

/** 일봉을 주차(월요일 기준)로 묶어 주봉 OHLCV 생성 (과거→최신). */
function toWeekly(candles: Candle[]): WeekBar[] {
  const byWeek = new Map<string, WeekBar>();
  for (const c of candles) {
    const y = +c.date.slice(0, 4), m = +c.date.slice(4, 6) - 1, d = +c.date.slice(6, 8);
    const dt = new Date(Date.UTC(y, m, d));
    dt.setUTCDate(dt.getUTCDate() - ((dt.getUTCDay() + 6) % 7)); // 그 주 월요일
    const key = dt.toISOString().slice(0, 10);
    const w = byWeek.get(key);
    if (!w) {
      byWeek.set(key, { ...c, days: 1 });
    } else {
      w.high = Math.max(w.high, c.high);
      w.low = Math.min(w.low, c.low);
      w.close = c.close; // 주중 최신 종가
      w.volume += c.volume;
      w.date = c.date; // 주중 마지막 거래일
      w.days += 1;
    }
  }
  return [...byWeek.values()]; // 일봉이 시간순이라 Map 삽입순=주차순
}

/** 주봉 구조 — 직전 N주(최대 8) 신고가 돌파·이번주 종가위치(윗꼬리)·거래량 배수. 5주 미만이면 null.
 * ⚠ 이번주(cur)는 미완성 부분주일 수 있어 currentWeekDays로 진행 거래일 수를 함께 노출(거래량 디플레이트 해석용). */
function weeklyStructure(weeklies: WeekBar[]): TechnicalSection["weeklyStructure"] {
  if (weeklies.length < 5) return null;
  const cur = weeklies[weeklies.length - 1];
  const prior = weeklies.slice(-9, -1); // 직전 최대 8주
  if (prior.length < 3) return null;
  const priorHigh = Math.max(...prior.map((w) => w.high));
  const range = cur.high - cur.low;
  const priorVolAvg = prior.reduce((s, w) => s + w.volume, 0) / prior.length;
  const breakoutPct = priorHigh > 0 ? Math.round((cur.high / priorHigh - 1) * 1000) / 10 : null;
  return {
    weeks: prior.length,
    currentWeekDays: cur.days,
    brokeRecentHigh: breakoutPct !== null && breakoutPct > 0,
    breakoutPct,
    closePositionPct: range > 0 ? Math.round(((cur.close - cur.low) / range) * 100) : null,
    volumeVsAvg: priorVolAvg > 0 ? Math.round((cur.volume / priorVolAvg) * 10) / 10 : null,
  };
}

/** 일봉 → 지표 묶음. 35봉 미만(신규상장 등)이면 null. */
export function computeIndicators(candles: Candle[]): TechnicalSection | null {
  if (candles.length < 35) return null;
  const closes = candles.map((c) => c.close);
  const highs = candles.map((c) => c.high);
  const lows = candles.map((c) => c.low);
  const vols = candles.map((c) => c.volume);
  const last = candles[candles.length - 1];

  const ma5 = sma(closes, 5);
  const ma20 = sma(closes, 20);
  const ma60 = sma(closes, 60);
  const volAvg20 = sma(vols, 20);

  return {
    ma5: ma5 !== null ? Math.round(ma5) : null,
    ma20: ma20 !== null ? Math.round(ma20) : null,
    ma60: ma60 !== null ? Math.round(ma60) : null,
    maAligned: ma5 !== null && ma20 !== null && ma60 !== null && ma5 > ma20 && ma20 > ma60,
    closeStrength:
      last.high > last.low
        ? Math.round(((last.close - last.low) / (last.high - last.low)) * 100) / 100
        : null,
    volumeVs20d:
      volAvg20 && volAvg20 > 0 ? Math.round((vols[vols.length - 1] / volAvg20) * 100) / 100 : null,
    macd: macd(closes),
    rsi: rsi(closes),
    stochastic: stochasticSlow(highs, lows, closes),
    ichimoku: ichimoku(highs, lows, closes),
    weeklyStructure: weeklyStructure(toWeekly(candles)),
  };
}

const round1 = (x: number) => Math.round(x * 10) / 10;
const round2 = (x: number) => Math.round(x * 100) / 100;
