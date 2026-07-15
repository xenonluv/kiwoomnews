"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Flame, ArrowRight } from "lucide-react";

import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { radarClientService } from "@/services/radar.client";
import { marketPhaseKST, type MarketPhase } from "@/lib/market";
import type { Explosion } from "@/types/radar";

const POLL_MS = 60_000;

const PHASE_MSG: Record<MarketPhase, { dot: string; text: string }> = {
  pre: { dot: "bg-muted-foreground/50", text: "개장 전 · 09:00부터 폭발 감시 시작" },
  intraday: { dot: "bg-up animate-pulse", text: "장중 폭발 감시 (실시간 갱신)" },
  locked: { dot: "bg-up animate-pulse", text: "장중 폭발 감시 (실시간 갱신)" },
  closed: { dot: "bg-muted-foreground/50", text: "장 마감 · 다음 거래일 갱신" },
};

/** 등락률 표기 (한국 색 관례: 상승=빨강 up, 하락=파랑 down) */
function changeClass(v: number) {
  return v > 0 ? "text-up" : v < 0 ? "text-down" : "text-muted-foreground";
}

function ExplosionCard({ e, rank }: { e: Explosion; rank: number }) {
  return (
    <Link href={`/stock/${e.code}`} className="group block">
      <Card className="h-full border border-[rgba(242,54,69,0.35)] bg-gradient-to-br from-[rgba(242,54,69,0.1)] to-[rgba(255,255,255,0.03)] backdrop-blur-xl transition-shadow hover:shadow-[0_0_18px_1px_rgba(242,54,69,0.35)]">
        <CardHeader className="gap-2 pb-2">
          <div className="flex items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge className="border-transparent bg-up px-2 py-0.5 text-sm font-bold text-up-foreground">
                <Flame className="mr-1 size-3.5" aria-hidden /> #{rank}
              </Badge>
              {e.sector && <Badge variant="neutral">{e.sector}</Badge>}
              {e.backfill && (
                <Badge
                  variant="outline"
                  className="border-muted-foreground/40 text-muted-foreground"
                  title="장중 폭발 후 등락률 상위 랭킹에서 밀려난 종목. 고가·회전율은 폭발 시점 기준, 현재가·등락률은 실시간 조회값입니다."
                >
                  장중 폭발(랭킹 밀림)
                </Badge>
              )}
            </div>
            <span className="text-xs text-muted-foreground tabular-nums">
              거래대금 {e.value_eok.toLocaleString()}억
            </span>
          </div>
          <h2 className="flex items-baseline gap-2 text-2xl font-bold tracking-tight">
            <span>{e.name}</span>
            {/* 현재 등락률(라이브·백필 모두 실시간 조회값). 조회 실패로 null인 경우에만 생략 — 거짓 표기 방지 */}
            {e.change_pct != null && (
              <span className={`text-base font-semibold tabular-nums ${changeClass(e.change_pct)}`}>
                {e.change_pct > 0 ? "+" : ""}
                {e.change_pct.toFixed(2)}%
              </span>
            )}
            <ArrowRight className="ml-auto size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5" aria-hidden />
          </h2>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-3 pt-1">
          <div>
            <p className="text-[11px] text-muted-foreground">당일 고가 등락률</p>
            <p className="text-lg font-bold tabular-nums text-up">+{e.high_pct.toFixed(1)}%</p>
          </div>
          <div>
            <p
              className="text-[11px] text-muted-foreground"
              title="당일 거래량 / 유통주식수 — 유통주식이 통째로 손바뀐 강도(90%+ 폭발 게이트)"
            >
              유통주식 회전율
            </p>
            <p className="text-lg font-bold tabular-nums text-foreground">
              {e.vol_turnover_pct.toLocaleString()}%
            </p>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}

type Thresholds = { highPct: number; volTurnover: number };

export function ExplosionList({
  initial,
  thresholds,
}: {
  initial: Explosion[];
  thresholds: Thresholds;
}) {
  const [explosions, setExplosions] = useState<Explosion[]>(initial);
  const [th, setTh] = useState<Thresholds>(thresholds);
  const [phase, setPhase] = useState<MarketPhase>("closed");

  useEffect(() => {
    setPhase(marketPhaseKST());
    const ph = setInterval(() => setPhase(marketPhaseKST()), 60_000);
    let alive = true;
    async function refresh() {
      try {
        const data = await radarClientService.get();
        if (!alive) return;
        setExplosions(data.explosions ?? []);
        if (data.params?.explosion_high_pct != null || data.params?.explosion_vol_turnover != null) {
          setTh((prev) => ({
            highPct: data.params.explosion_high_pct ?? prev.highPct,
            volTurnover: data.params.explosion_vol_turnover ?? prev.volTurnover,
          }));
        }
      } catch {
        /* 조용히 무시 */
      }
    }
    refresh(); // 마운트 즉시 1회 — SSG 빌드 스냅샷(최대 ~5분 stale)을 첫 폴링(60초) 전에 갱신
    const id = setInterval(refresh, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
      clearInterval(ph);
    };
  }, []);

  const status = PHASE_MSG[phase];

  return (
    <>
      <div className="mb-5 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-white/10 bg-white/[0.03] px-4 py-2.5 text-sm">
        <span className="flex items-center gap-2 font-medium">
          <span className={`inline-flex size-2.5 rounded-full ${status.dot}`} aria-hidden />
          {status.text}
        </span>
        <span className="text-xs text-warning">표시·참고용, 매수 추천 아님</span>
      </div>

      {explosions.length === 0 ? (
        <div className="rounded-xl border border-white/10 bg-white/[0.03] py-16 text-center">
          <p className="text-lg font-semibold">오늘은 폭발 종목이 없습니다</p>
          <p className="mt-1 text-sm text-muted-foreground">
            고가 +{th.highPct}% 이상 AND 당일 거래량이 유통주식수의 {th.volTurnover}% 이상인 종목만 표시합니다.
          </p>
        </div>
      ) : (
        <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {explosions.map((e, i) => (
            <ExplosionCard key={e.code} e={e} rank={i + 1} />
          ))}
        </div>
      )}
    </>
  );
}
