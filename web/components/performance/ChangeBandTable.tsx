import type { ChangeBandStats } from "@/types/performance";

/**
 * 등락률 구간별 익일 상승확률 — "몇 % 구간에서 종가 매수하면 익일 더 오르나".
 * hit_rate = 익일 종가 > 신호일 종가 비율(실측 상승확률). 구간당 min_n 이상일 때만 수치 표시.
 */
export function ChangeBandTable({ data }: { data: ChangeBandStats }) {
  return (
    <section className="rounded-lg border border-white/10 bg-white/[0.03] p-4">
      <h3 className="mb-1 text-sm font-semibold">등락률 구간별 익일 상승확률</h3>
      <p className="mb-3 text-[11px] text-muted-foreground">
        신호일 당일 등락률 구간별 &quot;종가 매수 → 익일 종가&quot; 상승 비율(실측 상승확률)과 평균수익 ·
        구간당 {data.min_n}건 이상 쌓이면 표시
      </p>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-muted-foreground">
            <th className="pb-2 font-medium">등락률 구간</th>
            <th className="pb-2 text-right font-medium">익일 상승확률</th>
            <th className="pb-2 text-right font-medium">평균수익</th>
            <th className="pb-2 text-right font-medium">표본</th>
          </tr>
        </thead>
        <tbody>
          {data.cells.map((c) => {
            const show = c.valid && c.hit_rate != null;
            return (
              <tr key={c.band} className="border-t border-white/5">
                <td className="py-1.5 font-medium tabular-nums">{c.band}</td>
                <td className="py-1.5 text-right tabular-nums">
                  {show ? (
                    <span
                      className={`font-semibold ${
                        c.hit_rate! > 50 ? "text-up" : c.hit_rate! < 50 ? "text-down" : "text-muted-foreground"
                      }`}
                    >
                      {c.hit_rate}%
                    </span>
                  ) : (
                    <span className="text-muted-foreground">수집 중</span>
                  )}
                </td>
                <td className="py-1.5 text-right tabular-nums">
                  {show && c.avg_return != null
                    ? `${c.avg_return > 0 ? "+" : ""}${c.avg_return}%`
                    : "—"}
                </td>
                <td className="py-1.5 text-right tabular-nums text-muted-foreground">{c.n}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-2 text-[11px] text-muted-foreground">
        레이더 게이트가 −4~+10%라 그 범위 안에서의 구간 비교입니다(밖은 표본 없음). 시장 전체 흐름(베타) 영향 가능.
      </p>
    </section>
  );
}
