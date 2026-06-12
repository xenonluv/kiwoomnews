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
    if (typeof close !== "number" || close <= 0) continue;
    out.push({
      date: String(date),
      open: Number(open),
      high: Number(high),
      low: Number(low),
      close: Number(close),
      volume: Number(volume),
    });
  }
  return out;
}
