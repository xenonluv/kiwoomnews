import Link from "next/link";
import type { Metadata } from "next";
import { ArrowLeft } from "lucide-react";

import {
  getAiClickPerformance,
  getPerformance,
  getTrackPerformance,
} from "@/lib/performance/repository";
import { TrendChart } from "@/components/performance/TrendChart";
import { StatCards } from "@/components/performance/StatCards";
import { CalibrationTable } from "@/components/performance/CalibrationTable";
import { WeightsPanel } from "@/components/performance/WeightsPanel";
import { AiPredictionPanel } from "@/components/performance/AiPredictionPanel";
import { SparkFlowMatrix } from "@/components/performance/SparkFlowMatrix";
import { ChangeBandTable } from "@/components/performance/ChangeBandTable";
import { PeakTurnoverBandTable } from "@/components/performance/PeakTurnoverBandTable";
import { LeaderReaccumPanel } from "@/components/performance/LeaderReaccumPanel";
import { ReaccumPerformancePanel } from "@/components/performance/ReaccumPerformancePanel";
import { StrategySimPanel } from "@/components/performance/StrategySimPanel";
import { ThemeStatsTable } from "@/components/performance/ThemeStatsTable";
import { TrackPerformancePanel } from "@/components/performance/TrackPerformancePanel";
import { AiClickCalibrationPanel } from "@/components/performance/AiClickCalibrationPanel";

export const metadata: Metadata = {
  title: "성과 검증 · 자가 개선",
  description:
    "레이더 수상 종목의 익일 상승 적중률을 매일 누적 검증하고, 그 결과로 점수 체계를 자동 개선합니다.",
};

export default function PerformancePage() {
  const data = getPerformance();
  const track = getTrackPerformance();
  const aiClick = getAiClickPerformance();

  return (
    <main className="container max-w-4xl py-12">
      <header className="mb-8 space-y-1">
        <Link
          href="/"
          className="mb-2 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-4" aria-hidden /> 레이더로
        </Link>
        <h1 className="text-3xl font-bold tracking-tight">성과 검증 · 자가 개선</h1>
        <p className="text-sm text-muted-foreground">
          매일 장후, 수상 종목을 &quot;당일 종가 매수 → 익일 종가&quot; 기준으로 자동 채점하고
          그 결과로 점수 체계를 스스로 보정합니다 ·
          <span className="text-warning"> 기준 {data.as_of}</span>
        </p>
      </header>

      <div className="space-y-6">
        <StatCards data={data} />

        {/* 재매집(reaccum) = 현 주력 산출물 — 매일 갱신되는 트랙. core보다 먼저 노출. */}
        {data.experimental?.reaccum && (
          <ReaccumPerformancePanel data={data.experimental.reaccum} />
        )}

        <section>
          <h2 className="mb-1 text-lg font-bold">누적 적중률 추세 (core 트랙)</h2>
          <p className="mb-2 text-xs text-muted-foreground">
            급등 후 식음(fade)·눌림(shakeout) 트랙. 레이더가 재매집만 산출하는 기간엔 신규 표본이 없어
            정지할 수 있습니다(재매집은 위 패널에서 별도 집계).
          </p>
          {data.summary.n === 0 ? (
            <div className="flex min-h-40 flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-border text-sm text-muted-foreground">
              <p>검증 데이터 수집 중 — {data.summary.tracking_days}일째</p>
              <p className="text-xs">
                수상 종목은 하루 0~3건이라 의미 있는 통계까지 수 주가 걸립니다. 그래프는 표본이
                쌓이는 대로 자동으로 그려집니다.
              </p>
            </div>
          ) : (
            <TrendChart series={data.series} />
          )}
        </section>

        <div className="grid gap-6 lg:grid-cols-2">
          <CalibrationTable bins={data.bins} />
          <WeightsPanel weights={data.weights} />
        </div>

        <TrackPerformancePanel data={track} />

        <AiClickCalibrationPanel data={aiClick} />

        {data.spark_flow && <SparkFlowMatrix data={data.spark_flow} />}

        {data.change_bands && <ChangeBandTable data={data.change_bands} />}

        {data.peak_turnover_bands && <PeakTurnoverBandTable data={data.peak_turnover_bands} />}

        {data.experimental?.leader_reaccum && (
          <LeaderReaccumPanel data={data.experimental.leader_reaccum} />
        )}

        {data.strategy_sim && <StrategySimPanel data={data.strategy_sim} />}

        {(data.by_theme || data.by_sector) && (
          <div className="grid gap-6 lg:grid-cols-2">
            {data.by_theme && (
              <ThemeStatsTable
                title="테마별 성과"
                subtitle="어느 테마 폭발이 익일 반등이 강한가 — 표본 누적 시 우선순위 근거"
                label="테마"
                rows={data.by_theme}
              />
            )}
            {data.by_sector && (
              <ThemeStatsTable title="섹터별 성과" label="섹터" rows={data.by_sector} />
            )}
          </div>
        )}

        {data.ai && <AiPredictionPanel ai={data.ai} />}

        {data.recent.length > 0 && (
          <section className="rounded-lg border border-white/10 bg-white/[0.03] p-4">
            <h3 className="mb-3 text-sm font-semibold">최근 채점 결과</h3>
            <ul className="space-y-1.5 text-sm">
              {data.recent.map((r, i) => (
                <li
                  key={`${r.date}-${r.name}-${i}`}
                  className="flex items-center justify-between gap-2 border-t border-white/5 pt-1.5 first:border-t-0 first:pt-0"
                >
                  <span className="text-muted-foreground tabular-nums">
                    {r.date.slice(4, 6)}/{r.date.slice(6, 8)}
                  </span>
                  <span className="flex-1 font-medium">{r.name}</span>
                  <span className="text-xs text-muted-foreground tabular-nums">
                    점수 {r.score}
                  </span>
                  <span
                    className={`w-20 text-right font-semibold tabular-nums ${r.hit ? "text-up" : "text-down"}`}
                  >
                    {r.hit ? "적중" : "미적중"} {r.return_pct > 0 ? "+" : ""}
                    {r.return_pct}%
                  </span>
                </li>
              ))}
            </ul>
          </section>
        )}

        <p className="text-[11px] leading-relaxed text-muted-foreground">{data.disclaimer}</p>
      </div>
    </main>
  );
}
