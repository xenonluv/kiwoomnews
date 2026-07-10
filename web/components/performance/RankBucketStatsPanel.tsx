import type { RankBucketStatCell, RankBucketStats } from "@/types/performance";

function pct(v: number | null, suffix = "%") {
  if (v == null) return "—";
  return `${v}${suffix}`;
}

function signedPct(v: number | null) {
  if (v == null) return "—";
  return `${v > 0 ? "+" : ""}${v}%`;
}

function Rate({ value }: { value: number | null }) {
  if (value == null) return <span className="text-muted-foreground">—</span>;
  return (
    <span
      className={`font-semibold ${
        value >= 70 ? "text-up" : value < 50 ? "text-down" : "text-muted-foreground"
      }`}
    >
      {value}%
    </span>
  );
}

function Rows({ rows, idHeader }: { rows: RankBucketStatCell[]; idHeader: string }) {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-[680px] text-sm">
        <thead>
          <tr className="text-left text-xs text-muted-foreground">
            <th className="pb-2 font-medium">{idHeader}</th>
            <th className="pb-2 font-medium">조건</th>
            <th className="pb-2 text-right font-medium">+7%</th>
            <th className="pb-2 text-right font-medium">Wilson</th>
            <th className="pb-2 text-right font-medium">중앙고가</th>
            <th className="pb-2 text-right font-medium">종가평균</th>
            <th className="pb-2 text-right font-medium">표본</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.bucket ?? r.shadow}-${r.band}`} className="border-t border-white/5">
              <td className="py-1.5 font-medium tabular-nums">
                {r.bucket != null ? `B${r.bucket}` : r.shadow}
              </td>
              <td className="py-1.5 text-muted-foreground">{r.band}</td>
              <td className="py-1.5 text-right tabular-nums">
                <Rate value={r.touch7_rate} />
              </td>
              <td className="py-1.5 text-right tabular-nums text-muted-foreground">
                {pct(r.wilson7_lower)}
              </td>
              <td className="py-1.5 text-right tabular-nums">{signedPct(r.median_high)}</td>
              <td className="py-1.5 text-right tabular-nums">{signedPct(r.avg_return)}</td>
              <td className="py-1.5 text-right tabular-nums text-muted-foreground">
                {r.n}/{r.unique_n}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function RankBucketStatsPanel({ data }: { data: RankBucketStats }) {
  return (
    <section className="rounded-lg border border-white/10 bg-white/[0.03] p-4">
      <h3 className="mb-1 text-sm font-semibold">정렬4 rank_bucket 성과</h3>
      <p className="mb-3 text-[11px] text-muted-foreground">
        suspects 실정렬 bucket별 익일 고가 +7% 터치, Wilson 하단, 중앙고가, 종가평균입니다. 표본은 n/고유종목 수입니다.
      </p>
      <Rows rows={data.cells} idHeader="Bucket" />

      {data.kill_switches.length > 0 && (
        <div className="mt-4 rounded-md border border-white/10 bg-black/10 p-3">
          <p className="mb-2 text-xs font-semibold">Kill switch</p>
          <ul className="space-y-1 text-[11px] text-muted-foreground">
            {data.kill_switches.map((k) => (
              <li key={k.key} className="flex items-start justify-between gap-3">
                <span>{k.label}</span>
                <span className={k.status.includes("하향") ? "text-down" : k.status.includes("재승격") ? "text-up" : ""}>
                  {k.status}
                  {k.reasons.length > 0 && ` · ${k.reasons.join(", ")}`}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {data.shadow_cells.length > 0 && (
        <div className="mt-4">
          <p className="mb-2 text-xs font-semibold">Shadow bucket</p>
          <Rows rows={data.shadow_cells} idHeader="Shadow" />
        </div>
      )}
    </section>
  );
}
