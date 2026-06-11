import type { ScoreBreakdown } from "@/types/radar";

const ITEMS: { key: keyof ScoreBreakdown; label: string; max: number }[] = [
  { key: "spark", label: "분봉 스파크", max: 15 },
  { key: "fade", label: "고점 후퇴·흔들기 패턴", max: 15 },
  { key: "flow", label: "외인·기관 수급", max: 15 },
  { key: "event", label: "이벤트 민감도", max: 15 },
  { key: "ma10", label: "10일선 여유", max: 10 },
];

/**
 * 수상함 점수 해부도 — 각 가점 항목의 기여를 막대로 투명 공개.
 * (base 30점은 전 조건 통과 자체의 기본 점수라 생략)
 */
export function ScoreBreakdownBars({ breakdown }: { breakdown: ScoreBreakdown }) {
  return (
    <ul className="space-y-1">
      {ITEMS.map(({ key, label, max }) => {
        const v = breakdown[key] ?? 0;
        const w = Math.min(100, Math.max(0, (v / max) * 100));
        return (
          <li key={key} className="flex items-center gap-2 text-[11px]">
            <span className="w-24 shrink-0 text-muted-foreground">{label}</span>
            <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/10">
              <span
                className="block h-full rounded-full bg-up/80"
                style={{ width: `${w}%` }}
              />
            </span>
            <span className="w-9 shrink-0 text-right tabular-nums text-foreground/80">
              {v > 0 ? `+${v}` : "0"}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
