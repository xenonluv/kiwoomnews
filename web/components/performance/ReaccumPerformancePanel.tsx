import type { ExperimentalStats } from "@/types/performance";
import { TrendChart } from "./TrendChart";

/**
 * 재매집(reaccum) 트랙 성과 — 현 파이프라인의 주력 산출물.
 * core(fade/shakeout)와 통계 격리(score_raw=0)돼 메인 적중률·가중치 튜닝엔 미반영이나,
 * 실제 매일 쌓이는 트랙이라 자체 누적 적중률 추세·요약·최근 채점을 노출한다.
 * 데이터 = performance.json의 experimental.reaccum (radar_backtest.py가 익일 평가로 갱신).
 */
export function ReaccumPerformancePanel({ data }: { data: ExperimentalStats["reaccum"] }) {
  const { n, hit_rate, avg_return, high3_rate, series, recent } = data;
  const hasTrend = !!series && series.some((p) => p.cum_hit_rate !== null);

  return (
    <section className="rounded-lg border border-white/10 bg-white/[0.03] p-4">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-bold">
          재매집(reaccum) 트랙 성과 <span className="text-xs font-normal text-warning">· 현재 주력</span>
        </h2>
        <span className="text-xs text-muted-foreground">표본 {n}건 · 익일 종가 채점</span>
      </div>

      {n === 0 ? (
        <div className="flex min-h-32 flex-col items-center justify-center gap-1 rounded-md border border-dashed border-border text-sm text-muted-foreground">
          <p>재매집 채점 데이터 수집 중</p>
          <p className="text-xs">신호 다음 거래일 종가가 나와야 채점됩니다. 표본이 쌓이는 대로 자동 갱신.</p>
        </div>
      ) : (
        <>
          <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Stat label="적중률" value={hit_rate === null ? "—" : `${hit_rate}%`} tone={tone(hit_rate, 50)} />
            <Stat
              label="평균 익일수익"
              value={avg_return === null ? "—" : `${avg_return > 0 ? "+" : ""}${avg_return}%`}
              tone={tone(avg_return, 0)}
            />
            <Stat label="고가 +3% 도달" value={high3_rate === null ? "—" : `${high3_rate}%`} />
            <Stat label="표본 수" value={`${n}건`} />
          </div>

          {hasTrend ? (
            <TrendChart series={series!} />
          ) : (
            <p className="text-xs text-muted-foreground">추세는 누적 표본이 더 쌓이면 그려집니다.</p>
          )}

          {recent && recent.length > 0 && (
            <div className="mt-4">
              <h3 className="mb-2 text-sm font-semibold">최근 채점 결과</h3>
              <ul className="space-y-1.5 text-sm">
                {recent.map((r, i) => (
                  <li
                    key={`${r.date}-${r.name}-${i}`}
                    className="flex items-center justify-between gap-2 border-t border-white/5 pt-1.5 first:border-t-0 first:pt-0"
                  >
                    <span className="text-muted-foreground tabular-nums">
                      {r.date.slice(4, 6)}/{r.date.slice(6, 8)}
                    </span>
                    <span className="flex-1 font-medium">{r.name}</span>
                    {r.score > 0 && (
                      <span className="text-xs text-muted-foreground tabular-nums">점수 {r.score}</span>
                    )}
                    <span
                      className={`w-20 text-right font-semibold tabular-nums ${r.hit ? "text-up" : "text-down"}`}
                    >
                      {r.hit ? "적중" : "미적중"} {r.return_pct > 0 ? "+" : ""}
                      {r.return_pct}%
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
        ※ 재매집은 화면 노출용 실험 트랙으로 <strong>score_raw=0</strong>이라 메인 적중률·가중치 튜닝과
        분리됩니다. 익일 종가{">"}신호일 종가 = 적중. 6개월 약세 단일 레짐 표본이라 보장이 아닌 참고 통계입니다.
      </p>
    </section>
  );
}

function tone(v: number | null, mid: number): string {
  if (v === null) return "";
  return v > mid ? "text-up" : v < mid ? "text-down" : "";
}

function Stat({ label, value, tone = "" }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-md border border-white/10 bg-white/[0.02] px-3 py-2">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className={`text-lg font-bold tabular-nums ${tone}`}>{value}</div>
    </div>
  );
}
