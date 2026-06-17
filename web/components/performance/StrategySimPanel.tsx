import type { StrategySim } from "@/types/performance";

/**
 * 분할 전략 실측 — 레이더 신호를 20/30/50 분할 + 익절/손절로 매매했다 가정한 실현 net 성적.
 * 라이브 누적(수수료 차감)·표시 전용·보장 아님. 표본 min_n 미만이면 "수집 중".
 */
export function StrategySimPanel({ data }: { data: StrategySim }) {
  const ready = data.n >= data.min_n && data.avg_net != null;
  const trc = (data.tranches || []).map((t) => `${Math.round(t * 100)}`).join("/");
  return (
    <section className="rounded-lg border border-white/10 bg-white/[0.03] p-4">
      <h3 className="mb-1 text-sm font-semibold">분할 전략 실측 (라이브 누적)</h3>
      <p className="mb-3 text-[11px] text-muted-foreground">
        레이더 신호를 {trc || "20/30/50"}% 분할 매수 + 익절 +{data.tp}% / 손절 −{data.sl}%로
        매매했다 가정한 실현 성적(수수료 {data.fee}%p 차감·종가손절 가정·보장 아님) · 누적 {data.n}건
      </p>
      {!ready ? (
        <p className="text-xs text-muted-foreground">
          수집 중 ({data.n}/{data.min_n}건) — 신호당 ~10거래일 보유 후 마감돼, 의미 있는 통계까지 수 주 걸립니다.
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          <Cell label="거래당 net 평균" main pos={data.avg_net! > 0}>
            {data.avg_net! > 0 ? "+" : ""}
            {data.avg_net}%
          </Cell>
          <Cell label="수익 거래" pos={(data.profit_rate ?? 0) >= 50}>{data.profit_rate}%</Cell>
          <Cell label={`익절(+${data.tp}%) 도달`}>{data.win_rate}%</Cell>
          <Cell label={`손절(−${data.sl}%)`} pos={false}>{data.stop_rate}%</Cell>
          <Cell label="최악 거래" pos={false}>{data.worst}%</Cell>
        </div>
      )}
      {ready && (
        <p className="mt-2 text-[11px] text-muted-foreground">
          net 평균이 +라도 최악 거래(꼬리)와 수수료를 감안하세요 — 분산·손절 규율이 핵심. 투자 참고용.
        </p>
      )}
    </section>
  );
}

function Cell({
  label,
  children,
  main,
  pos,
}: {
  label: string;
  children: React.ReactNode;
  main?: boolean;
  pos?: boolean;
}) {
  const color = pos === undefined ? "text-foreground" : pos ? "text-up" : "text-down";
  return (
    <div className="rounded-md border border-white/10 bg-white/[0.03] p-3">
      <p className="text-[11px] text-muted-foreground">{label}</p>
      <p className={`mt-1 ${main ? "text-lg" : "text-sm"} font-bold tabular-nums ${color}`}>
        {children}
      </p>
    </div>
  );
}
