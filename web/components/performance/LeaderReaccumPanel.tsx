import type { LeaderCohort, LeaderReaccumStats } from "@/types/performance";

/**
 * '예전 대장' 재매집 엣지 검증 — 폭발일에 업종 거래대금 1위(예전 대장)였던 종목이 재매집 시
 * 익일 더 잘 오르는지 대장 vs 비대장 코호트로 실측 비교. 코호트당 min_n 이상일 때만 수치 표시.
 */
const ROWS: { key: keyof Pick<LeaderReaccumStats, "leader" | "nonleader" | "all">; label: string }[] = [
  { key: "leader", label: "🏆 예전 대장" },
  { key: "nonleader", label: "비대장" },
  { key: "all", label: "전체 재매집" },
];

function Rate({ c }: { c: LeaderCohort }) {
  if (!c.valid || c.hit_rate == null) return <span className="text-muted-foreground">수집 중</span>;
  return (
    <span className={`font-semibold ${c.hit_rate >= 50 ? "text-up" : "text-down"}`}>{c.hit_rate}%</span>
  );
}

export function LeaderReaccumPanel({ data }: { data: LeaderReaccumStats }) {
  return (
    <section className="rounded-lg border border-white/10 bg-white/[0.03] p-4">
      <h3 className="mb-1 text-sm font-semibold">🏆 예전 대장 재매집 엣지 검증</h3>
      <p className="mb-3 text-[11px] text-muted-foreground">
        폭발일에 같은 업종 거래대금 1위(예전 대장)였던 종목이 식은 뒤 재매집에 들어올 때, 익일 더 잘
        오르는지 검증 · 코호트당 {data.min_n}건 이상 쌓이면 표시
      </p>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-muted-foreground">
            <th className="pb-2 font-medium">코호트</th>
            <th className="pb-2 text-right font-medium">익일 상승확률</th>
            <th className="pb-2 text-right font-medium">평균수익</th>
            <th className="pb-2 text-right font-medium">고가+3%</th>
            <th className="pb-2 text-right font-medium">표본</th>
          </tr>
        </thead>
        <tbody>
          {ROWS.map(({ key, label }) => {
            const c = data[key];
            const show = c.valid && c.hit_rate != null;
            return (
              <tr key={key} className="border-t border-white/5">
                <td className="py-1.5 font-medium">{label}</td>
                <td className="py-1.5 text-right tabular-nums">
                  <Rate c={c} />
                </td>
                <td className="py-1.5 text-right tabular-nums">
                  {show && c.avg_return != null
                    ? `${c.avg_return > 0 ? "+" : ""}${c.avg_return}%`
                    : "—"}
                </td>
                <td className="py-1.5 text-right tabular-nums text-muted-foreground">
                  {show && c.high3_rate != null ? `${c.high3_rate}%` : "—"}
                </td>
                <td className="py-1.5 text-right tabular-nums text-muted-foreground">{c.n}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-2 text-[11px]">
        {data.lift == null ? (
          <span className="text-muted-foreground">표본 수집 중 — 엣지 미확정</span>
        ) : data.lift === 0 ? (
          <span className="text-muted-foreground">대장 코호트 동률 (0%p) — 엣지 미확정</span>
        ) : (
          <span className={`font-semibold ${data.lift > 0 ? "text-up" : "text-down"}`}>
            대장 코호트 {data.lift > 0 ? "+" : ""}
            {data.lift}%p {data.lift > 0 ? "우위" : "열위"}
          </span>
        )}
      </p>
      <p className="mt-2 text-[11px] text-muted-foreground">
        대장 판정 로직이 최근 개선되어 신뢰 표본은 그 이후부터 누적됩니다 · 코호트 통계이며 보장 아님.
      </p>
    </section>
  );
}
