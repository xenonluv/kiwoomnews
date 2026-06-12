import { Sparkles } from "lucide-react";

import type { AiStats } from "@/types/performance";

const BAND_LABELS: Record<string, string> = {
  "0-43": "하락 우위 (43% 미만)",
  "43-58": "관망 (43~57%)",
  "58-101": "상승 우위 (58%+)",
};

/**
 * AI 익일 예측(prob_up) 검증 — 매일 장후 마감 카드 종목의 AI 예측을 기록하고
 * 익일 채점해 방향별 적중률 + 확률 보정(예측 확률 vs 실측 적중률)을 누적 검증.
 */
export function AiPredictionPanel({ ai }: { ai: AiStats }) {
  return (
    <div className="rounded-lg border border-white/10 bg-white/[0.03] p-4">
      <h3 className="mb-1 flex items-center gap-1.5 text-sm font-semibold">
        <Sparkles className="size-3.5 text-warning" aria-hidden /> AI 예측 검증
      </h3>
      <p className="mb-3 text-[11px] text-muted-foreground">
        매일 장후 수상 종목의 AI 상승 확률(3샘플 중앙값)을 기록하고 익일 결과로 채점합니다.
      </p>

      {ai.n === 0 ? (
        <p className="py-4 text-center text-xs text-muted-foreground">
          검증 데이터 수집 중 — AI 예측은 기록 다음 거래일부터 채점됩니다.
        </p>
      ) : (
        <>
          <div className="mb-3 flex flex-wrap gap-x-4 gap-y-1 text-xs tabular-nums">
            <span>
              평가 표본 <strong>{ai.n}건</strong>
            </span>
            <span>
              평균 예측 <strong>{ai.avg_prob}%</strong> vs 실측 상승{" "}
              <strong>{ai.actual_rate}%</strong>
            </span>
            {ai.brier !== null && (
              <span title="낮을수록 좋음 · 0.25 = 항상 50%라 답한 무정보 기준선">
                Brier <strong>{ai.brier}</strong>
              </span>
            )}
          </div>

          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-muted-foreground">
                <th className="pb-2 font-medium">예측 구간</th>
                <th className="pb-2 font-medium tabular-nums">표본</th>
                <th className="pb-2 font-medium">평균 예측</th>
                <th className="pb-2 font-medium">실측 상승률</th>
              </tr>
            </thead>
            <tbody>
              {ai.prob_bands.map((b) => (
                <tr key={`${b.lo}-${b.hi}`} className="border-t border-white/5">
                  <td className="py-2">{BAND_LABELS[`${b.lo}-${b.hi}`] ?? `${b.lo}~${b.hi - 1}%`}</td>
                  <td className="py-2 tabular-nums text-muted-foreground">{b.n}건</td>
                  <td className="py-2 tabular-nums text-muted-foreground">
                    {b.avg_prob !== null ? `${b.avg_prob}%` : "—"}
                  </td>
                  <td className="py-2">
                    {b.valid && b.actual_rate !== null ? (
                      <span
                        className={`font-semibold tabular-nums ${b.actual_rate >= 50 ? "text-up" : "text-down"}`}
                      >
                        {b.actual_rate}%
                      </span>
                    ) : (
                      <span className="text-xs text-muted-foreground">
                        수집 중 (20건 이상 누적 시 표시)
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {ai.by_direction.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground tabular-nums">
              {ai.by_direction.map((d) => (
                <span key={d.key}>
                  {d.key} 예측 {d.n}건 · 익일상승 {d.hit_rate}% · 평균 {d.avg_return > 0 ? "+" : ""}
                  {d.avg_return}%
                </span>
              ))}
            </div>
          )}

          <p className="mt-2 text-[11px] text-muted-foreground">
            보정이 잘 될수록 &quot;평균 예측&quot;과 &quot;실측 상승률&quot;이 구간마다 비슷해집니다.
          </p>
        </>
      )}
    </div>
  );
}
