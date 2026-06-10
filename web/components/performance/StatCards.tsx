import type { PerformanceData } from "@/types/performance";

function Stat({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: string;
  sub?: string;
  accent?: "up" | "down" | "none";
}) {
  const cls =
    accent === "up" ? "text-up" : accent === "down" ? "text-down" : "text-foreground";
  return (
    <div className="rounded-lg border border-white/10 bg-white/[0.04] px-4 py-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={`text-2xl font-bold tabular-nums ${cls}`}>{value}</p>
      {sub && <p className="text-[11px] text-muted-foreground tabular-nums">{sub}</p>}
    </div>
  );
}

/** 핵심 지표 4카드 — 표본 부족 시 "수집 중"을 정직하게 표시.
 *  통계는 마감 카드에 남은 종목(= 종가 매수 가능했던 종목)만 집계한다. */
export function StatCards({ data }: { data: PerformanceData }) {
  const s = data.summary;
  const has = s.n > 0;
  return (
    <div>
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <Stat
        label="누적 적중률 (마감 카드 · 익일 종가↑)"
        value={has && s.hit_rate !== null ? `${s.hit_rate}%` : "수집 중"}
        sub={`표본 ${s.n}건 · ${s.tracking_days}일째 추적`}
        accent={has && (s.hit_rate ?? 0) >= 50 ? "up" : "none"}
      />
      <Stat
        label="평균 수익률 (익일 종가)"
        value={has && s.avg_return !== null ? `${s.avg_return > 0 ? "+" : ""}${s.avg_return}%` : "—"}
        accent={!has ? "none" : (s.avg_return ?? 0) > 0 ? "up" : "down"}
      />
      <Stat
        label="익일 고가 +3% 도달률"
        value={has && s.high3_rate !== null ? `${s.high3_rate}%` : "—"}
        sub="장중 익절 기회 비율"
      />
      <Stat
        label="자가 튜닝"
        value={data.weights.tuned ? "활성" : "대기"}
        sub={
          data.weights.tuned
            ? `표본 ${data.weights.basis_n}건 기반`
            : `표본 ${data.weights.tune_min_samples}건 누적 시 활성 (현재 ${s.n})`
        }
        accent={data.weights.tuned ? "up" : "none"}
      />
    </div>
    {s.dropout && s.dropout.n > 0 && (
      <p className="mt-2 text-[11px] text-muted-foreground">
        참고: 장중에 잡혔다가 마감 전 탈락한 종목{" "}
        <span className="tabular-nums">{s.dropout.n}건</span>의 적중률은{" "}
        <span className="tabular-nums">{s.dropout.hit_rate}%</span> — 통계·튜닝에는
        포함하지 않습니다 (마감 카드만 종가 매수가 가능하므로).
      </p>
    )}
    </div>
  );
}
