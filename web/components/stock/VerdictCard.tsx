import { AlertTriangle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DisclaimerNote } from "@/components/radar/DisclaimerNote";
import type { VerdictLevel, VerdictSection } from "@/types/stock";
import { cn } from "@/lib/utils";

const LEVEL_STYLE: Record<VerdictLevel, { ring: string; text: string; badge: "up" | "warning" | "neutral" | "down" }> = {
  "강한 매수신호": { ring: "hsl(var(--up))", text: "text-up", badge: "up" },
  "매수 우위": { ring: "hsl(var(--up))", text: "text-up", badge: "up" },
  중립: { ring: "hsl(var(--neutral))", text: "text-muted-foreground", badge: "neutral" },
  "관망·과열": { ring: "hsl(var(--warning))", text: "text-warning", badge: "warning" },
  "매도 우위": { ring: "hsl(var(--down))", text: "text-down", badge: "down" },
};

const GROUPS = ["기술", "재료", "컨센서스", "수급", "이벤트", "시장경보"] as const;

function Gauge({ score, level }: { score: number; level: VerdictLevel }) {
  const size = 120;
  const stroke = 10;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const offset = c * (1 - Math.max(0, Math.min(100, score)) / 100);
  const s = LEVEL_STYLE[level];
  return (
    <div className="relative inline-flex items-center justify-center" role="img" aria-label={`종합 점수 ${score}점, ${level}`}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="hsl(var(--muted))" strokeWidth={stroke} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={s.ring}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={offset}
          className="transition-[stroke-dashoffset] duration-700 ease-out"
        />
      </svg>
      <div className="absolute flex flex-col items-center">
        <span className={cn("text-3xl font-bold tabular-nums", s.text)}>{score}</span>
        <span className="text-[10px] tracking-wide text-muted-foreground">종합 점수</span>
      </div>
    </div>
  );
}

const won = (n: number) => `${n.toLocaleString("ko-KR")}원`;

/** 종합 판정 — 게이지 + 참고 구간(진입/목표/손절) + 지지/저항 + 점수 해부도. */
export function VerdictCard({ verdict, disclaimer }: { verdict: VerdictSection; disclaimer: string }) {
  const s = LEVEL_STYLE[verdict.level];
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-lg">
          종합 판정
          <Badge variant={s.badge}>{verdict.level}</Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-col items-center gap-4 sm:flex-row sm:items-start">
          <Gauge score={verdict.score} level={verdict.level} />
          <div className="min-w-0 flex-1 space-y-3">
            <p className="text-sm leading-relaxed">{verdict.summary}</p>
            <div className="grid grid-cols-3 gap-2 text-center">
              {[
                { label: "참고 진입가", v: verdict.entry, cls: "" },
                { label: "참고 목표 (+5%)", v: verdict.target, cls: "text-up" },
                { label: "참고 손절 (−3%)", v: verdict.stop, cls: "text-down" },
              ].map((b) => (
                <div key={b.label} className="rounded-md border border-white/10 bg-white/[0.03] px-2 py-2">
                  <p className="text-[10px] text-muted-foreground">{b.label}</p>
                  <p className={cn("text-sm font-bold tabular-nums", b.cls)}>{won(b.v)}</p>
                </div>
              ))}
            </div>
            {(verdict.supports.length > 0 || verdict.resistances.length > 0) && (
              <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground">
                {verdict.supports.length > 0 && (
                  <span>
                    지지:{" "}
                    {verdict.supports.map((x) => `${x.label} ${won(x.price)}`).join(" · ")}
                  </span>
                )}
                {verdict.resistances.length > 0 && (
                  <span>
                    저항:{" "}
                    {verdict.resistances.map((x) => `${x.label} ${won(x.price)}`).join(" · ")}
                  </span>
                )}
              </div>
            )}
          </div>
        </div>

        {verdict.cautionFlags.length > 0 && (
          <ul className="space-y-1 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
            {verdict.cautionFlags.map((f) => (
              <li key={f} className="flex items-center gap-1.5">
                <AlertTriangle className="size-3 shrink-0" aria-hidden /> {f}
              </li>
            ))}
          </ul>
        )}

        {/* 점수 해부도 — 가감점 전부 투명 공개 */}
        <div>
          <p className="mb-1.5 text-xs font-semibold text-muted-foreground">
            점수 해부 (기본 40점 ± 항목별 가감)
          </p>
          <ul className="space-y-1">
            {GROUPS.flatMap((g) =>
              verdict.breakdown
                .filter((b) => b.group === g)
                .map((b) => (
                  <li key={`${g}-${b.label}`} className="flex items-center gap-2 text-[11px]">
                    <span className="w-14 shrink-0 text-muted-foreground">{b.group}</span>
                    <span className="flex-1 truncate">{b.label}</span>
                    <span
                      className={cn(
                        "w-10 shrink-0 text-right tabular-nums font-semibold",
                        b.delta > 0 ? "text-up" : "text-down"
                      )}
                    >
                      {b.delta > 0 ? `+${b.delta}` : b.delta}
                    </span>
                  </li>
                ))
            )}
          </ul>
        </div>

        <DisclaimerNote text={disclaimer} />
      </CardContent>
    </Card>
  );
}
