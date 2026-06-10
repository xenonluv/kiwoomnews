import type { WeightsInfo } from "@/types/performance";

const COMP_LABELS: Record<string, string> = {
  spark: "분봉 스파크",
  fade: "고점 매집형 후퇴",
  flow: "외인·기관 수급",
  event: "이벤트 민감도",
  ma10: "10일선 여유",
};

/**
 * 자가 튜닝 가중치 패널 — 시스템이 무엇을 어떻게 학습했는지 전부 공개.
 * "몰래 학습" 없음: 현재값 vs 기본값 + 변경 이력.
 */
export function WeightsPanel({ weights }: { weights: WeightsInfo }) {
  const comps = Object.keys(weights.default);
  return (
    <div className="rounded-lg border border-white/10 bg-white/[0.03] p-4">
      <h3 className="mb-1 text-sm font-semibold">자가 튜닝 가중치</h3>
      <p className="mb-3 text-[11px] text-muted-foreground">
        {weights.tuned
          ? `백테스트 표본 ${weights.basis_n}건의 적중-항목 상관으로 자동 조정됨 (기본값 ±30% 제한)`
          : `표본 ${weights.tune_min_samples}건 누적 시 자동 조정 시작 — 현재는 기본 가중치 사용`}
      </p>
      <ul className="space-y-1.5">
        {comps.map((c) => {
          const cur = weights.current[c] ?? weights.default[c];
          const base = weights.default[c];
          const diff = cur - base;
          const w = base > 0 ? Math.min(100, (cur / (base * 1.3)) * 100) : 0;
          return (
            <li key={c} className="flex items-center gap-2 text-[12px]">
              <span className="w-28 shrink-0 text-muted-foreground">{COMP_LABELS[c] ?? c}</span>
              <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/10">
                <span
                  className={`block h-full rounded-full ${diff > 0 ? "bg-up/80" : diff < 0 ? "bg-down/70" : "bg-white/30"}`}
                  style={{ width: `${w}%` }}
                />
              </span>
              <span className="w-20 shrink-0 text-right tabular-nums">
                {cur}
                <span className="text-muted-foreground"> / {base}</span>
                {diff !== 0 && base > 0 && (
                  <span className={diff > 0 ? "text-up" : "text-down"}>
                    {" "}
                    ({diff > 0 ? "+" : ""}
                    {Math.round((diff / base) * 100)}%)
                  </span>
                )}
              </span>
            </li>
          );
        })}
      </ul>
      {weights.history.length > 0 && (
        <p className="mt-3 text-[11px] text-muted-foreground tabular-nums">
          가중치 변경 이력 {weights.history.length}회 · 최근:{" "}
          {weights.history[weights.history.length - 1]?.date}
        </p>
      )}
    </div>
  );
}
