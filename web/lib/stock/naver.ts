// 네이버 공개(무인증) API fetch 계층 — Vercel route handler 전용.
// 시크릿 불필요: 모바일 공개 엔드포인트만 사용한다 (KIS는 로컬 파이프라인 전용, 여기 금지).
// 모든 호출은 no-store + 타임아웃 — 신선도 제어는 라우트의 CDN Cache-Control이 담당.

import { ymdKST } from "./parse";
import type { Candle, MinuteBar } from "@/types/stock";

const UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36";

async function fetchNaver(url: string, timeoutMs = 6000): Promise<Response> {
  const res = await fetch(url, {
    cache: "no-store",
    signal: AbortSignal.timeout(timeoutMs),
    headers: { "User-Agent": UA, Accept: "application/json, text/plain, */*" },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status} ${url}`);
  return res;
}

async function fetchJson<T = unknown>(url: string, timeoutMs = 6000): Promise<T> {
  return (await fetchNaver(url, timeoutMs)).json() as Promise<T>;
}

/* eslint-disable @typescript-eslint/no-explicit-any */

/** 자동완성: 종목명/코드 → 후보 목록 (국내 주식만). */
export async function fetchAutocomplete(q: string): Promise<any[]> {
  const url = `https://ac.stock.naver.com/ac?q=${encodeURIComponent(q)}&target=stock`;
  const d = await fetchJson<any>(url, 4000);
  const items = Array.isArray(d?.items) ? d.items.flat(2) : [];
  return items.filter(
    (it: any) => it?.nationCode === "KOR" && it?.category === "stock" && it?.code
  );
}

/** 현재가·등락·시장상태·거래정지 여부 등 기본 정보. */
export async function fetchBasic(code: string): Promise<any> {
  return fetchJson(`https://m.stock.naver.com/api/stock/${code}/basic`);
}

/** PER/PBR/52주/시총/컨센서스/증권사리포트 등 종합 정보. */
export async function fetchIntegration(code: string): Promise<any> {
  return fetchJson(`https://m.stock.naver.com/api/stock/${code}/integration`);
}

/** 연간 재무 (매출액/영업이익/당기순이익 + 컨센서스 연도). ETF는 financeInfo=null. */
export async function fetchFinanceAnnual(code: string): Promise<any> {
  return fetchJson(`https://m.stock.naver.com/api/stock/${code}/finance/annual`);
}

/** 종목 뉴스 피드 (최신순). */
export async function fetchNews(code: string, pageSize = 20): Promise<any[]> {
  const d = await fetchJson<any>(
    `https://m.stock.naver.com/api/news/stock/${code}?pageSize=${pageSize}&page=1`
  );
  // 응답: [{total, items:[...]}, ...] 묶음 배열
  const groups = Array.isArray(d) ? d : [];
  return groups.flatMap((g: any) => (Array.isArray(g?.items) ? g.items : []));
}

/** 외인/기관/개인 일별 순매수 (최신순). */
export async function fetchTrend(code: string): Promise<any[]> {
  const d = await fetchJson<any>(`https://m.stock.naver.com/api/stock/${code}/trend`);
  return Array.isArray(d) ? d : [];
}

/**
 * 당일 1분봉 — fchart(무인증, EUC-KR XML). 실측: 시/고/저는 항상 "null"이고
 * 종가·당일 누적거래량만 유효 → 분당 거래량은 인접 봉 차분으로 복원한다.
 * count와 무관하게 ~6세션치가 섞여 오므로 KST 오늘 날짜로 필터(휴장일 → 빈 배열).
 * 주말은 당일 봉이 존재할 수 없어 호출 없이 빈 배열 (호출량 절약).
 * data 속성은 순수 ASCII라 EUC-KR 응답을 UTF-8 text()로 읽어도 파싱 안전.
 */
export async function fetchMinuteCandles(code: string, count = 480): Promise<MinuteBar[]> {
  const wd = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Seoul",
    weekday: "short",
  }).format(new Date());
  if (wd === "Sat" || wd === "Sun") return [];

  const url =
    `https://fchart.stock.naver.com/sise.nhn?symbol=${code}` +
    `&timeframe=minute&count=${count}&requestType=0`;
  const raw = await (await fetchNaver(url, 8000)).text();
  const today = ymdKST();
  const rows: { time: string; close: number; cum: number }[] = [];
  for (const m of raw.matchAll(/data="(\d{12})\|[^|]*\|[^|]*\|[^|]*\|([^|"]*)\|(\d*)"/g)) {
    const [, dt, c, v] = m;
    if (dt.slice(0, 8) !== today) continue;
    rows.push({
      time: dt.slice(8, 12),
      close: c && c !== "null" ? Number(c) : 0,
      cum: v ? Number(v) : 0,
    });
  }
  rows.sort((a, b) => (a.time < b.time ? -1 : 1));
  // 누적 → 분당 차분. 장전(08:30~) 봉 포함해 차분한 뒤 09:00 이전은 버린다
  // (09:00 봉에 시초가 동시호가 물량이 자연 반영 — KIS 분봉과 정합).
  const bars: MinuteBar[] = [];
  let prevCum = 0;
  let preClose = 0; // 마지막 장전 체결가 (장전시간외는 전일 종가 고정 = 전일 종가)
  for (const r of rows) {
    const vol = Math.max(0, r.cum - prevCum);
    prevCum = r.cum;
    if (r.time >= "0900") bars.push({ time: r.time, close: r.close, vol });
    else if (r.close > 0) preClose = r.close;
  }
  // 09:00 무체결 종목(갭 동시호가 지연 체결): fchart는 행이 없지만 KIS는 09:00 봉을
  // 전일 종가·거래량 0으로 채워 준다 → 동일하게 가상 봉을 넣어 등락률 체인 기준가를
  // 맞춘다 (없으면 첫 체결봉의 갭 등락이 0%로 처리돼 개장 스파크를 통째로 놓침).
  if (preClose > 0 && bars.length > 0 && bars[0].time !== "0900") {
    bars.unshift({ time: "0900", close: preClose, vol: 0 });
  }
  return bars;
}

/**
 * 일봉 OHLCV — api.finance.naver.com/siseJson (파이썬 리스트형 텍스트 응답).
 * scripts/team3_price_context.py:fetch_daily 의 관대 파싱을 TS로 포팅.
 * calendarDays=300 → 거래일 약 200봉 (지표 최소 35봉 + 일목 78봉 충분).
 */
/**
 * 유동비율(free float, 0~1) — 네이버 finance coinfo가 iframe으로 부르는 wisereport 페이지를 스크랩.
 * KIS·네이버모바일엔 유통주식수가 없어 '유통주식 회전율'(거래대금/유통시총)을 못 내므로 여기서 보충.
 * HTML 스크랩이라 best-effort: 실패·파싱불가·이상치(0/>1)면 null → 호출부가 전체 시총 기준으로 폴백.
 */
export interface FloatInfo {
  ratio: number; // 유동비율(0~1)
  listed: number | null; // 상장(발행)주식수(주) — 같은 셀에서 함께 파싱(유통주식수 = listed × ratio)
}

export async function fetchFloat(code: string): Promise<FloatInfo | null> {
  // 보통주(6자리·끝자리 0)만 — wisereport가 우선주/ETN 코드를 보통주 페이지로 합쳐 응답해 오귀속됨.
  if (!/^\d{5}0$/.test(code)) return null;
  try {
    const url =
      `https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx` +
      `?cmp_cd=${code}&target=finsum_more`;
    const res = await fetch(url, {
      cache: "no-store",
      signal: AbortSignal.timeout(5000), // 비핵심·graceful 폴백이라 짧게(전체 리포트 tail latency 억제)
      headers: { "User-Agent": UA, Referer: "https://finance.naver.com/" },
    });
    if (!res.ok) return null;
    const html = await res.text();
    // "발행주식수/유동비율" 행의 td 셀만 잡고(다음 행 숫자로 넘어가 오매칭 방지) 그 안에서 주식수·% 추출.
    // 셀 예: "19,948,221주 / 56.77%" — m[1]=발행주식수, m[2]=유동비율%.
    const cellM = html.match(/발행주식수\/유동비율\s*<\/th>\s*<td[^>]*>([\s\S]*?)<\/td>/);
    const m = cellM && cellM[1].match(/([\d,]+)\s*주\s*\/\s*([\d.]+)\s*%/);
    if (!m) return null;
    const r = Number(m[2]) / 100;
    // 3%~100%만 유효 — <3%(품절주·이상치)는 유통시총 극소→회전율 폭증이라 폴백
    if (!(r >= 0.03 && r <= 1)) return null;
    const listed = Number(m[1].replace(/,/g, ""));
    return { ratio: r, listed: Number.isFinite(listed) && listed > 0 ? listed : null };
  } catch {
    return null;
  }
}

/** 유동비율만 필요한 기존 호출부 호환 래퍼. */
export async function fetchFloatRatio(code: string): Promise<number | null> {
  return (await fetchFloat(code))?.ratio ?? null;
}

/**
 * 시장 지수(코스피/코스닥) 당일 종가·등락률 — fetchBasic과 동일 패턴(무인증).
 * 음봉이 시장 전체 하락 때문인지 종목 고유인지 구분하는 [시장 레짐] 맥락용. 실패 시 null(graceful).
 */
export async function fetchIndex(
  market: "KOSPI" | "KOSDAQ"
): Promise<{ name: string; close: number; changePct: number } | null> {
  try {
    const d = await fetchJson<any>(
      `https://m.stock.naver.com/api/index/${market}/basic`,
      4000
    );
    const close = Number(String(d?.closePrice ?? "").replace(/,/g, ""));
    const changePct = Number(String(d?.fluctuationsRatio ?? "").replace(/,/g, ""));
    if (!Number.isFinite(close) || !Number.isFinite(changePct)) return null;
    return { name: String(d?.stockName ?? market), close, changePct };
  } catch {
    return null;
  }
}

export async function fetchDaily(code: string, calendarDays = 300): Promise<Candle[]> {
  const end = ymdKST();
  const start = ymdKST(new Date(Date.now() - calendarDays * 86_400_000));
  const url =
    `https://api.finance.naver.com/siseJson.naver?symbol=${code}` +
    `&requestType=1&startTime=${start}&endTime=${end}&timeframe=day`;
  const raw = await (await fetchNaver(url, 8000)).text();
  const normalized = raw
    .trim()
    .replace(/'/g, '"')
    .replace(/,\s*([\]}])/g, "$1"); // 후행 콤마 방어
  const rows = JSON.parse(normalized) as unknown[];
  const out: Candle[] = [];
  for (const row of rows.slice(1)) {
    // [날짜, 시가, 고가, 저가, 종가, 거래량, ...]
    if (!Array.isArray(row) || row.length < 6) continue;
    const [date, open, high, low, close, volume] = row;
    const o = Number(open), h = Number(high), l = Number(low), c = Number(close);
    // close만이 아니라 OHLC 전부 유효수 검증 — 하나라도 NaN/비정상이면(거래정지·신규상장 첫행 등)
    // stochastic(-Infinity 비교)·주봉 Math.max·일목 등으로 NaN이 전파돼 지표가 조용히 틀린 값을 낸다.
    if (![o, h, l, c].every(Number.isFinite) || c <= 0) continue;
    const v = Number(volume);
    out.push({
      date: String(date),
      open: o,
      high: h,
      low: l,
      close: c,
      volume: Number.isFinite(v) ? v : 0,
    });
  }
  return out;
}
