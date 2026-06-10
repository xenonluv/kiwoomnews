import type { SparkCluster } from "@/types/radar";

/** "09:21" → 09:00(장 시작) 기준 경과 분 */
function minutesFromOpen(time: string): number {
  const [h, m] = time.split(":").map(Number);
  return (h - 9) * 60 + m;
}

const SESSION_MINUTES = 390; // 09:00 ~ 15:30

/**
 * 분봉 스파크 타임라인 — 장중 어느 시각에 거래량이 터졌는지 점으로 표시.
 * 점 크기 = 거래량 배수, 색 = 등락 방향(한국 관례: 상승 빨강/하락 파랑).
 */
export function SparkTimeline({ clusters }: { clusters: SparkCluster[] }) {
  if (clusters.length === 0) return null;
  return (
    <div>
      <div className="relative h-6">
        {/* 트랙 */}
        <div className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-white/15" aria-hidden />
        {/* 정오 눈금 */}
        <div className="absolute left-[46%] top-1/2 h-2 w-px -translate-y-1/2 bg-white/20" aria-hidden />
        {clusters.map((c, i) => {
          const left = Math.min(100, Math.max(0, (minutesFromOpen(c.time) / SESSION_MINUTES) * 100));
          const sz = Math.min(14, 6 + Math.log2(Math.max(1, c.vol_x)) * 2);
          return (
            <span
              key={`${c.time}-${i}`}
              title={`${c.time} · 거래량 ${c.vol_x}배 · ${c.pct > 0 ? "+" : ""}${c.pct}%`}
              className={`absolute top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full ${
                c.pct >= 0
                  ? "bg-up shadow-[0_0_8px_1px_hsl(var(--up)/0.5)]"
                  : "bg-down shadow-[0_0_8px_1px_hsl(var(--down)/0.5)]"
              }`}
              style={{ left: `${left}%`, width: sz, height: sz }}
            />
          );
        })}
      </div>
      <div className="flex justify-between text-[10px] text-muted-foreground tabular-nums">
        <span>09:00</span>
        <span>12:00</span>
        <span>15:30</span>
      </div>
    </div>
  );
}
