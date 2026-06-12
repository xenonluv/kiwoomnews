import type { SparkFlowStats, SparkFlowCell } from "@/types/performance";

function Cell({ cell }: { cell: SparkFlowCell }) {
  if (!cell.valid || cell.hit_rate === null) {
    return (
      <span className="text-xs text-muted-foreground">
        {cell.n > 0 ? `수집 중 (${cell.n}건)` : "—"}
      </span>
    );
  }
  return (
    <span
      className={`font-semibold tabular-nums ${cell.hit_rate >= 50 ? "text-up" : "text-down"}`}
    >
      {cell.hit_rate}%{" "}
      <span className="text-xs font-normal text-muted-foreground">({cell.n}건)</span>
    </span>
  );
}

/** 메가스파크×수급 가설 검증 표 — 스파크 배율 구간 × 당일 수급매수 적중률.
 * 가설(2026-06): ≥40배 스파크 + 외인/기관 매수 동반 종목은 회복력이 강함.
 * 표본 미달 셀은 숨기지 않고 "수집 중" 명시 (정직성). */
export function SparkFlowMatrix({ data }: { data: SparkFlowStats }) {
  const buckets = [...new Set(data.cells.map((c) => c.spark_bucket))];
  const find = (bucket: string, flowBuy: boolean) =>
    data.cells.find((c) => c.spark_bucket === bucket && c.flow_buy === flowBuy);

  return (
    <div className="rounded-lg border border-white/10 bg-white/[0.03] p-4">
      <h3 className="mb-1 text-sm font-semibold">메가스파크 × 수급매수 검증</h3>
      <p className="mb-3 text-[11px] text-muted-foreground">
        가설: 분봉 스파크 ≥{data.mega_x}배 + 당일 외인/기관 순매수 동반 시 익일 회복력이 강하다
        — 이 조합엔 현재 최대 +12점 가점이 적용 중이며, 아래 실측으로 검증/반증됩니다.
      </p>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-muted-foreground">
            <th className="pb-2 font-medium">스파크 배율</th>
            <th className="pb-2 font-medium">수급매수 ○</th>
            <th className="pb-2 font-medium">수급매수 ×</th>
          </tr>
        </thead>
        <tbody>
          {buckets.map((bucket) => {
            const buy = find(bucket, true);
            const noBuy = find(bucket, false);
            const isMega = bucket.startsWith("≥");
            return (
              <tr key={bucket} className="border-t border-white/5">
                <td className={`py-2 ${isMega ? "font-semibold text-up" : ""}`}>{bucket}</td>
                <td className="py-2">{buy && <Cell cell={buy} />}</td>
                <td className="py-2">{noBuy && <Cell cell={noBuy} />}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-2 text-[11px] text-muted-foreground">
        셀당 {data.min_n}건 이상 누적 시 적중률 표시
        {data.unknown_n > 0 && ` · 배율 미기록 과거 표본 ${data.unknown_n}건은 제외`}
      </p>
    </div>
  );
}
