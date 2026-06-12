// 리포트 오케스트레이터 — 네이버 공개 API 6종을 병렬 호출해
// 섹션별로 조립한다. 일부 실패는 해당 섹션 null + warnings로 우아하게 강등,
// 종목 자체를 못 찾으면 NotFoundError, 전부 실패면 UnreachableError.

import { getRadar } from "@/lib/radar/repository";
import type {
  Candle,
  FinancialSection,
  FlowDay,
  FlowSection,
  MarketAlert,
  PriceSection,
  SparkSection,
  StockReport,
} from "@/types/stock";
import {
  fetchBasic,
  fetchDaily,
  fetchFinanceAnnual,
  fetchIntegration,
  fetchMinuteCandles,
  fetchNews,
  fetchTrend,
} from "./naver";
import { cleanText, formatKST, num } from "./parse";
import { computeIndicators } from "./indicators";
import { detectSparks } from "./sparks";
import { makeAliases, scoreNews } from "./news-score";
import { matchEvents } from "./theme-match";
import { computeVerdict } from "./scoring";

export class NotFoundError extends Error {}
export class UnreachableError extends Error {}

const DISCLAIMER =
  "본 리포트는 네이버 공개 데이터 기반의 규칙(룰베이스) 자동 계산 결과로 투자 참고용이며, " +
  "매수·매도 추천이 아닙니다. 투자 판단과 책임은 본인에게 있습니다.";

/* eslint-disable @typescript-eslint/no-explicit-any */

const val = <T>(r: PromiseSettledResult<T>): T | null =>
  r.status === "fulfilled" ? r.value : null;

/**
 * KRX 시장경보 파싱 — 네이버 basic.marketAlertType: {code:"01",text:"투자주의"}.
 * 미지정 종목은 필드 자체가 없다. code(01/02/03)와 text 양쪽으로 방어적 판별.
 */
function parseMarketAlert(raw: any): MarketAlert | null {
  const code = typeof raw?.code === "string" ? raw.code : "";
  const text = typeof raw?.text === "string" ? raw.text : "";
  if (!code && !text) return null;
  const level =
    code === "03" || text.includes("위험")
      ? "위험"
      : code === "02" || text.includes("경고")
        ? "경고"
        : "주의";
  return { level, label: text || `투자${level}` };
}

export async function buildStockReport(code: string): Promise<StockReport> {
  const [basicR, integR, dailyR, newsR, trendR, finR, minuteR] = await Promise.allSettled([
    fetchBasic(code),
    fetchIntegration(code),
    fetchDaily(code),
    fetchNews(code),
    fetchTrend(code),
    fetchFinanceAnnual(code),
    fetchMinuteCandles(code),
  ]);

  const basic = val(basicR);
  const integ = val(integR);
  const candles = val(dailyR) ?? [];
  const rawNews = val(newsR);
  const trend = val(trendR);
  const fin = val(finR);

  if (!basic && !integ && candles.length === 0 && !rawNews && !trend && !fin) {
    throw new UnreachableError("네이버 응답 없음");
  }
  const name: string | null = basic?.stockName ?? integ?.stockName ?? null;
  if (!name && candles.length === 0) {
    throw new NotFoundError(`종목을 찾을 수 없습니다: ${code}`);
  }

  const warnings: string[] = [];
  const stockEndType: string = basic?.stockEndType ?? integ?.stockEndType ?? "stock";
  const isEtf = stockEndType !== "stock";
  const tradeStop =
    typeof basic?.tradeStopType?.name === "string" && basic.tradeStopType.name !== "TRADING";
  const newlyListed = basic?.newlyListed === true;
  const marketAlert = parseMarketAlert(basic?.marketAlertType);
  if (marketAlert && marketAlert.level !== "주의") {
    warnings.push(
      `거래소 시장경보: ${marketAlert.label} 종목입니다 — 이상급등·단기과열 종목에 지정되며 ` +
        "위탁증거금 100%·신용거래 제한 등이 적용될 수 있습니다."
    );
  }
  const isManagement = basic?.isManagement === true;
  if (isManagement) {
    warnings.push(
      "관리종목입니다 — 상장폐지 사유 발생·감사의견 거절 등 중대한 부실 사유로 지정되며 " +
        "상장폐지 위험이 있습니다."
    );
  }

  // ── 주가현황 ──
  const info: Record<string, string> = {};
  for (const t of integ?.totalInfos ?? []) {
    if (t?.code && t?.value != null) info[t.code] = String(t.value);
  }
  const close =
    num(basic?.closePrice) ??
    num(info.lastClosePrice) ??
    (candles.length ? candles[candles.length - 1].close : null);

  let price: PriceSection | null = null;
  if (close !== null) {
    const high52 = num(info.highPriceOf52Weeks);
    const low52 = num(info.lowPriceOf52Weeks);
    const targetPrice = num(integ?.consensusInfo?.priceTargetMean);
    const recommMean = num(integ?.consensusInfo?.recommMean);
    price = {
      close,
      change: num(basic?.compareToPreviousClosePrice) ?? 0,
      changePct: num(basic?.fluctuationsRatio) ?? 0,
      marketCap: info.marketValue ?? null,
      per: num(info.per),
      eps: num(info.eps),
      cnsPer: num(info.cnsPer),
      cnsEps: num(info.cnsEps),
      pbr: num(info.pbr),
      bps: num(info.bps),
      dividendYield: num(info.dividendYieldRatio),
      foreignRate: num(info.foreignRate),
      high52,
      low52,
      pctFrom52High:
        high52 && high52 > 0 ? Math.round((close / high52 - 1) * 1000) / 10 : null,
      pctFrom52Low: low52 && low52 > 0 ? Math.round((close / low52 - 1) * 1000) / 10 : null,
      consensus:
        targetPrice && recommMean
          ? {
              recommMean,
              targetPrice,
              upsidePct: Math.round((targetPrice / close - 1) * 1000) / 10,
              date: integ?.consensusInfo?.createDate ?? "",
            }
          : null,
    };
  }
  if (!integ) warnings.push("종합 정보(PER·컨센서스)를 불러오지 못했습니다.");

  // ── 차트·기술적 분석 ──
  const chart = candles.length > 0 ? { candles: candles.slice(-120) } : null;
  if (!chart) warnings.push("일봉 시세를 불러오지 못해 차트·기술 분석을 생략합니다.");
  const technical = candles.length > 0 ? computeIndicators(candles) : null;
  if (chart && !technical)
    warnings.push(`일봉이 ${candles.length}개뿐이라(신규상장 등) 기술적 분석을 생략합니다.`);

  // ── 수급 ──
  let flow: FlowSection | null = null;
  if (trend && trend.length > 0) {
    const daily: FlowDay[] = trend
      .map((r: any) => ({
        date: String(r.bizdate ?? ""),
        foreign: num(r.foreignerPureBuyQuant) ?? 0,
        organ: num(r.organPureBuyQuant) ?? 0,
        individual: num(r.individualPureBuyQuant) ?? 0,
        foreignHoldRatio: num(r.foreignerHoldRatio),
        close: num(r.closePrice),
      }))
      .filter((d) => d.date)
      .sort((a, b) => (a.date < b.date ? 1 : -1)) // 최신 우선 정렬 보장
      .slice(0, 10);
    const last5 = daily.slice(0, 5);
    flow = {
      daily,
      summary: {
        foreignNet5: last5.reduce((s, d) => s + d.foreign, 0),
        organNet5: last5.reduce((s, d) => s + d.organ, 0),
        foreignNetDays5: last5.filter((d) => d.foreign > 0).length,
        organNetDays5: last5.filter((d) => d.organ > 0).length,
      },
    };
  } else if (!isEtf) {
    warnings.push("투자자 수급 데이터를 불러오지 못했습니다.");
  }

  // ── 당일 분봉 스파크 (radar 조건2 — fchart 무인증 분봉) ──
  // fetch 실패만 경고. 당일 봉 <30(휴장·주말·개장 직후)은 조용히 null → 카드 숨김.
  let spark: SparkSection | null = null;
  const minuteBars = val(minuteR);
  if (minuteBars === null) {
    warnings.push("분봉 데이터를 불러오지 못해 스파크 분석을 생략합니다.");
  } else if (minuteBars.length >= 30) {
    const clusters = detectSparks(minuteBars);
    spark = {
      clusters,
      barCount: minuteBars.length,
      maxVolX: clusters.length > 0 ? Math.max(...clusters.map((c) => c.vol_x)) : null,
    };
  }

  // ── 재무 (ETF/일부 종목은 financeInfo=null) ──
  let financials: FinancialSection | null = null;
  const finInfo = fin?.financeInfo;
  if (finInfo?.trTitleList?.length && finInfo?.rowList?.length) {
    const periods = finInfo.trTitleList.map((t: any) => ({
      label: String(t.title ?? t.key ?? ""),
      isEstimate: t.isConsensus === "Y",
      key: String(t.key ?? ""),
    }));
    const WANT = ["매출액", "영업이익", "당기순이익", "영업이익률", "ROE", "부채비율"];
    const rows = WANT.flatMap((label) => {
      const row = finInfo.rowList.find((r: any) => r?.title === label);
      if (!row) return [];
      return [
        {
          label,
          values: periods.map((p: any) => num(row.columns?.[p.key]?.value)),
        },
      ];
    });
    if (rows.length > 0) {
      // 하이라이트: 확정(컨센서스 아님) 연도 기준 YoY/이익률/흑자 여부
      const confirmedIdx = periods
        .map((p: any, i: number) => (p.isEstimate ? -1 : i))
        .filter((i: number) => i >= 0);
      const li = confirmedIdx[confirmedIdx.length - 1];
      const pi = confirmedIdx[confirmedIdx.length - 2];
      const get = (label: string, i: number | undefined) =>
        i === undefined || i < 0
          ? null
          : rows.find((r) => r.label === label)?.values[i] ?? null;
      const yoy = (cur: number | null, prev: number | null) =>
        cur !== null && prev !== null && prev !== 0
          ? Math.round((cur / Math.abs(prev) - 1) * 1000) / 10
          : null;
      const rev = get("매출액", li);
      const op = get("영업이익", li);
      const net = get("당기순이익", li);
      financials = {
        periods: periods.map((p: any) => ({ label: p.label, isEstimate: p.isEstimate })),
        rows,
        highlights: {
          revenueYoY: yoy(rev, get("매출액", pi)),
          opYoY: yoy(op, get("영업이익", pi)),
          opMargin:
            get("영업이익률", li) ??
            (rev && op !== null ? Math.round((op / rev) * 1000) / 10 : null),
          profitable: net === null ? null : net > 0,
        },
      };
    }
  }
  if (!financials) {
    warnings.push(
      isEtf
        ? "ETF·ETN은 재무제표가 제공되지 않습니다."
        : "연간 재무 데이터가 제공되지 않는 종목입니다."
    );
  }

  // ── 재료 뉴스 ──
  let news: StockReport["news"] = null;
  let newsTitles: string[] = [];
  if (rawNews && name) {
    const mapped = rawNews
      .filter((n: any) => n?.title)
      .map((n: any) => ({
        title: cleanText(n.title),
        summary: cleanText(n.body),
        datetime: String(n.datetime ?? ""),
        office: n.officeName ?? null,
        url:
          n.officeId && n.articleId
            ? `https://n.news.naver.com/article/${n.officeId}/${n.articleId}`
            : null,
      }));
    news = scoreNews(mapped, makeAliases(name));
    newsTitles = mapped.map((m) => m.title);
  } else if (!rawNews) {
    warnings.push("종목 뉴스를 불러오지 못했습니다.");
  }

  // ── 이벤트 민감도 (radar.json D-10 캘린더 × 뉴스 테마) ──
  const events = matchEvents(getRadar().events, newsTitles);

  // ── 증권사 리포트 (integration 부산물) ──
  const researches = (integ?.researches ?? [])
    .slice(0, 3)
    .map((r: any) => ({
      firm: String(r.bnm ?? ""),
      title: cleanText(r.tit),
      date: String(r.wdt ?? ""),
    }))
    .filter((r: any) => r.firm && r.title);

  // ── 종합 판정 ──
  let verdict: StockReport["verdict"] = null;
  if (tradeStop) {
    warnings.push("거래정지 종목입니다 — 판정을 제공하지 않습니다.");
  } else if (technical && close !== null) {
    verdict = computeVerdict({
      close,
      technical,
      news,
      price,
      flow,
      events,
      marketAlert,
      isManagement,
      spark,
    });
  }

  return {
    code,
    name: name ?? code,
    market: basic?.stockExchangeType?.name ?? null,
    asOf: formatKST(),
    marketStatus: basic?.marketStatus ?? null,
    marketAlert,
    isManagement,
    tradeStop,
    newlyListed,
    isEtf,
    warnings,
    price,
    chart,
    technical,
    flow,
    spark,
    financials,
    news,
    events,
    researches,
    verdict,
    disclaimer: DISCLAIMER,
  };
}
