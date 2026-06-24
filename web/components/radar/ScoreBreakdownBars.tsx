import type { ScoreBreakdown } from "@/types/radar";

type Item = { key: keyof ScoreBreakdown; label: string; max: number };

// 재매집(reaccum) 변별 가산점 — 표시 전용 '강도'(검증된 확률 아님). 현 파이프라인의 유일 산출물.
const REACCUM_ITEMS: Item[] = [
  { key: "re_count", label: "5분 스파크 수", max: 10 },
  { key: "re_body", label: "양봉 몸통%", max: 6 },
  { key: "peak_turnover", label: "폭발일 회전율", max: 10 },
  { key: "re_turnover", label: "당일 회전율", max: 6 },
];

/**
 * 수상함 점수 해부도 — 각 가점 항목의 기여를 막대로 투명 공개.
 * (base 점수는 전 조건 통과 자체의 기본 점수라 생략.)
 */
export function ScoreBreakdownBars({ breakdown }: { breakdown: ScoreBreakdown }) {
  return (
    <ul className="space-y-1">
      {REACCUM_ITEMS.map(({ key, label, max }) => {
        const v = breakdown[key] ?? 0;
        const w = Math.min(100, Math.max(0, (Math.abs(v) / max) * 100));
        return (
          <li key={key} className="flex items-center gap-2 text-[11px]">
            <span className="w-24 shrink-0 text-muted-foreground">{label}</span>
            <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/10">
              <span
                className={`block h-full rounded-full ${v < 0 ? "bg-down/80" : "bg-up/80"}`}
                style={{ width: `${w}%` }}
              />
            </span>
            <span className="w-9 shrink-0 text-right tabular-nums text-foreground/80">
              {v > 0 ? `+${v}` : v < 0 ? v : "0"}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
