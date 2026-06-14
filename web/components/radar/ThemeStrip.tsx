"use client";

import { Tag } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * 테마 칩 스트립 — 수상 종목의 상위 테마를 빈도순으로 나열.
 * 칩 클릭 시 같은 테마 동반군만 필터링한다(대장/후발을 눈으로 비교하는 용도).
 */
export function ThemeStrip({
  themes,
  selected,
  onSelect,
}: {
  themes: { name: string; count: number }[];
  selected: string | null;
  onSelect: (t: string | null) => void;
}) {
  if (themes.length === 0) return null;
  return (
    <section className="mb-6">
      <h2 className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-muted-foreground">
        <Tag className="size-4" aria-hidden />
        테마 <span className="text-xs font-normal">— 클릭하면 같은 테마 동반군만 묶어보기</span>
      </h2>
      <div className="flex flex-wrap gap-2">
        {themes.map((t) => {
          const isSel = selected === t.name;
          return (
            <button
              key={t.name}
              type="button"
              onClick={() => onSelect(isSel ? null : t.name)}
              aria-pressed={isSel}
              className={cn(
                "shrink-0 rounded-full border px-3 py-1 text-xs transition-colors",
                isSel
                  ? "border-up/70 bg-up/15 text-foreground shadow-[0_0_12px_1px_hsl(var(--up)/0.3)]"
                  : "border-white/10 bg-white/[0.04] text-muted-foreground hover:border-white/25"
              )}
            >
              #{t.name} <span className="tabular-nums">{t.count}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
