import type { ChangeBandStats } from "@/types/performance";

/**
 * 폭발일 회전율(폭발일 거래대금/시총) 구간별 익일 상승확률 — "시총 대비 폭발이 클수록 익일 더 오르나".
 * peak_turnover 가점 비중을 데이터로 검증하는 패널(재매집 실험 풀, 코어 통계와 격리).
 * ChangeBandStats 구조를 그대로 재사용한다. 구간당 min_n 이상 쌓일 때만 수치 표시.
 */
export function PeakTurnoverBandTable({ data }: { data: ChangeBandStats }) {
  return (
    <section className="rounded-lg border border-white/10 bg-white/[0.03] p-4">
      <h3 className="mb-1 text-sm font-semibold">폭발일 회전율 구간별 익일 상승확률</h3>
      <p className="mb-3 text-[11px] text-muted-foreground">
        폭발일 회전율(폭발일 거래대금/시총) 구간별 &quot;종가 매수 → 익일 종가&quot; 상승 비율(실측 상승확률)과
        평균수익 · 구간당 {data.min_n}건 이상 쌓이면 표시. &quot;시총 대비 폭발이 클수록 더 오르나&quot; 검증.
      </p>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-muted-foreground">
            <th className="pb-2 font-medium">폭발일 회전율</th>
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
        재매집 실험 표본(score_raw=0·코어 통계 격리). 폭발일 시총은 현재시총×(폭발일종가/현재가)로 복원 ·
        6개월 약세 단일 레짐 한계 · 보장 아님. 표본이 차면 peak_turnover 가점 비중을 데이터로 재조정.
      </p>
    </section>
  );
}
