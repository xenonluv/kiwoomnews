import type { PhasePerformance } from "@/types/performance";

/**
 * AI 국면 판정(재매집/분산/중립) 검증 — 종목상세 'AI 국면 판정' 클릭분을 익일 등락으로 채점.
 * 재매집→상승·분산→하락을 적중으로, 중립은 방향 무판단(참고). 신뢰도 구간별 적중률로 "신뢰도 높을수록
 * 맞나"도 본다. 데이터 = phase_performance.json (scripts/phase_eval.py가 익일 채점으로 갱신).
 * 검증·표시 전용 — 자동 튜닝 미사용.
 */
export function PhasePerformancePanel({ data }: { data: PhasePerformance }) {
  const { n, total_n, accuracy, by_phase, confidence_bands, recent, min_n } = data;
  const phaseTone = (p: string) => (p === "재매집" ? "text-up" : p === "분산" ? "text-down" : "text-muted-foreground");
  const cell = (v: number | null, suffix = "%") => (v === null ? "—" : `${v}${suffix}`);

  return (
    <section className="rounded-lg border border-white/10 bg-white/[0.03] p-4">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-bold">AI 국면 판정 검증 <span className="text-xs font-normal text-muted-foreground">· 식음 vs 고점</span></h2>
        <span className="text-xs text-muted-foreground">방향 표본 {n}건{total_n > n ? ` (중립 ${total_n - n} 별도)` : ""}</span>
      </div>

      {n === 0 ? (
        <div className="flex min-h-32 flex-col items-center justify-center gap-1 rounded-md border border-dashed border-border text-sm text-muted-foreground">
          <p>국면 판정 채점 데이터 수집 중</p>
          <p className="text-xs">
            종목상세에서 &apos;AI 국면 판정&apos; 버튼을 누른 종목만 기록되어, 다음 거래일 종가가 나오면 채점됩니다.
            표본이 쌓이는 대로 자동 갱신.
          </p>
        </div>
      ) : (
        <>
          <div className="mb-4 rounded-md border border-white/10 bg-white/[0.02] px-3 py-2">
            <span className="text-[11px] text-muted-foreground">전체 방향 적중률(재매집→상승·분산→하락)</span>
            <div className={`text-2xl font-bold tabular-nums ${accuracy !== null && accuracy >= 50 ? "text-up" : "text-down"}`}>
              {cell(accuracy)}
            </div>
          </div>

          {/* 국면별 성적 */}
          <div className="mb-4 overflow-x-auto">
            <table className="w-full min-w-[420px] text-sm">
              <thead>
                <tr className="border-b border-white/10 text-left text-xs text-muted-foreground">
                  <th className="py-1.5 pr-2">국면</th>
                  <th className="px-2 text-right">표본</th>
                  <th className="px-2 text-right">방향 적중률</th>
                  <th className="px-2 text-right">익일 상승률</th>
                  <th className="pl-2 text-right">평균 수익</th>
                </tr>
              </thead>
              <tbody>
                {by_phase.map((b) => (
                  <tr key={b.phase} className="border-b border-white/5">
                    <td className={`py-1.5 pr-2 font-semibold ${phaseTone(b.phase)}`}>{b.phase}</td>
                    <td className="px-2 text-right tabular-nums">{b.n}</td>
                    <td className="px-2 text-right tabular-nums">
                      {b.phase === "중립" ? <span className="text-muted-foreground">방향 무판단</span>
                        : b.valid ? cell(b.hit_rate) : <span className="text-muted-foreground" title={`표본 ${min_n}건 미만`}>수집 중</span>}
                    </td>
                    <td className="px-2 text-right tabular-nums text-muted-foreground">{b.valid ? cell(b.rose_rate) : "—"}</td>
                    <td className={`pl-2 text-right tabular-nums ${b.avg_return === null ? "" : b.avg_return > 0 ? "text-up" : "text-down"}`}>
                      {b.avg_return === null ? "—" : `${b.avg_return > 0 ? "+" : ""}${b.avg_return}%`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* 신뢰도 구간별 적중률 — '신뢰도 높을수록 맞나' */}
          <div className="mb-4">
            <h3 className="mb-1.5 text-sm font-semibold">신뢰도 구간별 방향 적중률 <span className="text-xs font-normal text-muted-foreground">(중립 제외)</span></h3>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[360px] text-sm">
                <thead>
                  <tr className="border-b border-white/10 text-left text-xs text-muted-foreground">
                    <th className="py-1.5 pr-2">신뢰도</th>
                    <th className="px-2 text-right">표본</th>
                    <th className="pl-2 text-right">적중률</th>
                  </tr>
                </thead>
                <tbody>
                  {confidence_bands.map((b) => (
                    <tr key={`${b.lo}-${b.hi}`} className="border-b border-white/5">
                      <td className="py-1.5 pr-2 tabular-nums">{b.lo}~{b.hi >= 100 ? "" : b.hi}%</td>
                      <td className="px-2 text-right tabular-nums">{b.n}</td>
                      <td className="pl-2 text-right tabular-nums">
                        {b.valid ? cell(b.hit_rate) : <span className="text-muted-foreground">수집 중</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {recent.length > 0 && (
            <div>
              <h3 className="mb-2 text-sm font-semibold">최근 채점 결과</h3>
              <ul className="space-y-1.5 text-sm">
                {recent.map((r, i) => (
                  <li key={`${r.date}-${r.code}-${i}`} className="flex items-center justify-between gap-2 border-t border-white/5 pt-1.5 first:border-t-0 first:pt-0">
                    <span className="text-muted-foreground tabular-nums">{r.date.slice(4, 6)}/{r.date.slice(6, 8)}</span>
                    <span className={`w-14 font-semibold ${phaseTone(r.phase)}`}>{r.phase}</span>
                    <span className="flex-1 text-xs text-muted-foreground tabular-nums">{r.confidence !== null ? `신뢰 ${r.confidence}%` : ""}</span>
                    <span className={`w-16 text-right font-semibold ${r.hit === null ? "text-muted-foreground" : r.hit ? "text-up" : "text-down"}`}>
                      {r.hit === null ? "관망" : r.hit ? "적중" : "미적중"}
                    </span>
                    <span className={`w-16 text-right tabular-nums ${r.return_pct > 0 ? "text-up" : r.return_pct < 0 ? "text-down" : ""}`}>
                      {r.return_pct > 0 ? "+" : ""}{r.return_pct}%
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">{data.disclaimer}</p>
    </section>
  );
}
