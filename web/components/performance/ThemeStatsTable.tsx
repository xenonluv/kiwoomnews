import type { GroupStat } from "@/types/performance";

/**
 * 테마/섹터별 성과 표 — "어느 테마가 강한 반등인가"의 데이터 근거.
 * valid(표본 10건+) 미달 셀은 적중률 수치를 숨기고 "수집 중" 표기(소표본 단정 방지·정직성).
 */
export function ThemeStatsTable({
  title,
  subtitle,
  label,
  rows,
}: {
  title: string;
  subtitle?: string;
  label: string; // 첫 열 헤더 ("테마" | "섹터")
  rows?: GroupStat[];
}) {
  const all = rows ?? [];
  const shown = all
    .filter((r) => r.key !== "unknown")
    .sort((a, b) => b.n - a.n || a.key.localeCompare(b.key));
  const unknown = all.find((r) => r.key === "unknown");

  return (
    <div className="rounded-lg border border-white/10 bg-white/[0.03] p-4">
      <h3 className="mb-1 text-sm font-semibold">{title}</h3>
      {subtitle && <p className="mb-3 text-[11px] text-muted-foreground">{subtitle}</p>}
      {shown.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">아직 분류된 표본이 없습니다 (수집 중).</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-muted-foreground">
              <th className="pb-2 font-medium">{label}</th>
              <th className="pb-2 font-medium">표본</th>
              <th className="pb-2 font-medium">익일적중</th>
              <th className="pb-2 font-medium">평균수익</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.key} className="border-t border-white/5">
                <td className="py-2 font-medium">
                  {r.key}
                  {r.leader_name && (
                    <span
                      className="ml-1 text-[11px] font-normal text-up"
                      title={`테마 대장(거래대금 1위 최빈) · ${r.leader_count}회`}
                    >
                      🏆{r.leader_name}
                    </span>
                  )}
                </td>
                <td className="py-2 tabular-nums">{r.n}건</td>
                <td className="py-2">
                  {r.valid ? (
                    <span
                      className={`font-semibold tabular-nums ${r.hit_rate >= 50 ? "text-up" : "text-down"}`}
                    >
                      {r.hit_rate}%
                    </span>
                  ) : (
                    <span className="text-xs text-muted-foreground">수집 중</span>
                  )}
                </td>
                <td className="py-2 tabular-nums">
                  {r.valid ? `${r.avg_return > 0 ? "+" : ""}${r.avg_return}%` : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="mt-2 text-[11px] text-muted-foreground">
        표본 10건 이상 누적 시 적중률 표시
        {unknown && unknown.n > 0 ? ` · 미분류 ${unknown.n}건 제외` : ""}
      </p>
    </div>
  );
}
