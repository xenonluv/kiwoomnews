"use client";

import { useEffect, useState } from "react";

import { ForecastCard } from "./ForecastCard";
import { marketPhaseKST, type MarketPhase } from "@/lib/market";
import type { Predictions } from "@/types/prediction";
import type { NewsItem } from "@/types/signal";

const POLL_MS = 60_000;

const PHASE_MSG: Record<MarketPhase, { dot: string; text: string }> = {
  pre: { dot: "bg-muted-foreground/50", text: "개장 전 · 09:00부터 잠정 랭킹 시작" },
  intraday: { dot: "bg-up animate-pulse", text: "장중 잠정 랭킹 (15분 갱신) · 14:20 종가베팅 확정 예정" },
  locked: { dot: "bg-up animate-pulse", text: "🎯 종가베팅 확정 (14:20 기준) · 종가 매수 후보" },
  closed: { dot: "bg-muted-foreground/50", text: "장 마감 · 다음 거래일 갱신" },
};

export function ForecastList({
  initial,
  newsByCode,
}: {
  initial: Predictions;
  newsByCode: Record<string, NewsItem[]>;
}) {
  const [data, setData] = useState<Predictions>(initial);
  const [phase, setPhase] = useState<MarketPhase>("closed");

  useEffect(() => {
    setPhase(marketPhaseKST());
    const ph = setInterval(() => setPhase(marketPhaseKST()), 60_000);
    let alive = true;
    async function refresh() {
      try {
        const r = await fetch("/api/predictions", { cache: "no-store" });
        if (alive && r.ok) setData(await r.json());
      } catch {
        /* 조용히 무시 */
      }
    }
    const id = setInterval(refresh, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
      clearInterval(ph);
    };
  }, []);

  // 14:20 이후/마감엔 종가베팅 확정 강조, 그 전엔 잠정 랭킹
  const showClosing = phase === "locked" || phase === "closed";
  const status = PHASE_MSG[phase];
  const bet = data.closing_bet ?? [];
  const rank = data.intraday_rank ?? [];

  return (
    <>
      <div className="mb-5 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-white/10 bg-white/[0.03] px-4 py-2.5 text-sm">
        <span className="flex items-center gap-2 font-medium">
          <span className={`inline-flex size-2.5 rounded-full ${status.dot}`} aria-hidden />
          {status.text}
        </span>
        <span className="text-xs text-muted-foreground tabular-nums">기준: {data.as_of}</span>
        {data.backtest?.recent_hit_rate && (
          <span className="text-xs text-up">적중률 {data.backtest.recent_hit_rate}</span>
        )}
        <span className="text-xs text-warning">예측·참고용, 매수 추천 아님</span>
      </div>

      {showClosing && bet.length > 0 && (
        <section className="mb-10">
          <h2 className="mb-1 text-xl font-bold tracking-tight">
            🎯 종가베팅 후보 <span className="text-sm font-normal text-muted-foreground">{bet.length}</span>
          </h2>
          <p className="mb-4 text-xs text-muted-foreground">
            오늘 종가 매수 시 내일 상승 확률이 높은 종목 (확신 ≥ 중)
          </p>
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {bet.map((it, i) => (
              <ForecastCard
                key={it.code}
                item={it}
                rank={i + 1}
                news={it.cause_news?.length ? it.cause_news : (newsByCode[it.code] ?? [])}
                newsLabel={it.cause_news?.length ? "상승 원인 뉴스" : "관련 뉴스"}
              />
            ))}
          </div>
        </section>
      )}

      <section className="mb-10">
        <h2 className="mb-1 text-xl font-bold tracking-tight">
          {showClosing ? "전체 잠정 랭킹" : "장중 잠정 랭킹"}{" "}
          <span className="text-sm font-normal text-muted-foreground">{rank.length}</span>
        </h2>
        <p className="mb-4 text-xs text-muted-foreground">
          기술(차트) + 재료(뉴스) + 장중 지속성 합치 점수 순
        </p>
        <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {rank.map((it, i) => (
            <ForecastCard key={it.code} item={it} rank={i + 1} />
          ))}
        </div>
      </section>

      <p className="text-xs text-muted-foreground">{data.disclaimer}</p>
    </>
  );
}
