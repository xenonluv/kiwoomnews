"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Zap, ArrowRight, Clock } from "lucide-react";

import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { radarClientService } from "@/services/radar.client";
import { marketPhaseKST, type MarketPhase } from "@/lib/market";
import type { Youtong } from "@/types/radar";

const POLL_MS = 60_000;

const PHASE_MSG: Record<MarketPhase, { dot: string; text: string }> = {
  pre: { dot: "bg-muted-foreground/50", text: "개장 전 · 09:30부터 감시 시작" },
  intraday: { dot: "bg-warning animate-pulse", text: "곧 폭발 후보 감시 (실시간 갱신)" },
  locked: { dot: "bg-warning animate-pulse", text: "곧 폭발 후보 감시 (실시간 갱신)" },
  closed: { dot: "bg-muted-foreground/50", text: "장 마감 · 다음 거래일 갱신" },
};

/** 등락률 표기 (한국 색 관례: 상승=빨강 up, 하락=파랑 down) */
function changeClass(v: number) {
  return v > 0 ? "text-up" : v < 0 ? "text-down" : "text-muted-foreground";
}

/** "0930" → "09:30" */
function hhmm(s: string) {
  return s.length === 4 ? `${s.slice(0, 2)}:${s.slice(2)}` : s;
}

function YoutongCard({ e, rank }: { e: Youtong; rank: number }) {
  return (
    <Link href={`/stock/${e.code}`} className="group block">
      <Card className="h-full border border-[rgba(245,158,11,0.35)] bg-gradient-to-br from-[rgba(245,158,11,0.1)] to-[rgba(255,255,255,0.03)] backdrop-blur-xl transition-shadow hover:shadow-[0_0_18px_1px_rgba(245,158,11,0.35)]">
        <CardHeader className="gap-2 pb-2">
          <div className="flex items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge className="border-transparent bg-warning px-2 py-0.5 text-sm font-bold text-warning-foreground">
                <Zap className="mr-1 size-3.5" aria-hidden /> #{rank}
              </Badge>
              {e.sector && <Badge variant="neutral">{e.sector}</Badge>}
              {e.first_seen && (
                <Badge
                  variant="outline"
                  className="border-muted-foreground/40 text-muted-foreground"
                  title="처음 포착된 시각 — 한 번 포착되면 장 마감까지 목록에 유지됩니다(현재가는 실시간 갱신)."
                >
                  <Clock className="mr-1 size-3" aria-hidden /> {e.first_seen} 포착
                </Badge>
              )}
            </div>
            <span className="text-xs text-muted-foreground tabular-nums">
              거래대금 {e.value_eok.toLocaleString()}억
            </span>
          </div>
          <h2 className="flex items-baseline gap-2 text-2xl font-bold tracking-tight">
            <span>{e.name}</span>
            {/* 현재 등락률(실시간) — 조회 실패(지속 종목 일시 결측) 시에만 생략 */}
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
            <p
              className="text-[11px] text-muted-foreground"
              title="당일 거래량 / 유통주식수 — 유통주식이 손바뀐 강도(매집 진행도). 50%+부터 후보, 상한 없음"
            >
              유통주식 회전율
            </p>
            <p className="text-lg font-bold tabular-nums text-warning">
              {e.vol_turnover_pct.toLocaleString()}%
            </p>
          </div>
          <div>
            <p className="text-[11px] text-muted-foreground">당일 고가 등락률</p>
            <p className="text-lg font-bold tabular-nums text-up">+{e.high_pct.toFixed(1)}%</p>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}

type Thresholds = { changePct: number; turnoverMin: number; startHhmm: string };

export function YoutongList({
  initial,
  thresholds,
}: {
  initial: Youtong[];
  thresholds: Thresholds;
}) {
  const [youtong, setYoutong] = useState<Youtong[]>(initial);
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
        setYoutong(data.youtong ?? []);
        const p = data.params ?? {};
        if (p.youtong_change_pct != null || p.youtong_turnover_min != null || p.youtong_start != null) {
          setTh((prev) => ({
            changePct: p.youtong_change_pct ?? prev.changePct,
            turnoverMin: p.youtong_turnover_min ?? prev.turnoverMin,
            startHhmm: p.youtong_start ?? prev.startHhmm,
          }));
        }
      } catch {
        /* 조용히 무시 */
      }
    }
    refresh(); // 마운트 즉시 1회 — SSG 빌드 스냅샷(최대 ~10분 stale)을 첫 폴링(60초) 전에 갱신
    const id = setInterval(refresh, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
      clearInterval(ph);
    };
  }, []);

  // 개장(09:00)~감지 시작(startHhmm, 09:30) 사이엔 phase가 intraday여도 youtong 감지는 아직 시작 전 →
  // 펄스 '실시간 감시 중' 대신 '감시 시작 전' 배너로(빈 목록 오해 방지). 클라 KST 기준.
  const k = new Date(Date.now() + 9 * 3600_000);
  const nowHhmm = `${String(k.getUTCHours()).padStart(2, "0")}${String(k.getUTCMinutes()).padStart(2, "0")}`;
  const beforeStart = (phase === "intraday" || phase === "locked") && nowHhmm < th.startHhmm;
  const status = beforeStart
    ? { dot: "bg-muted-foreground/50", text: `개장 후 · ${hhmm(th.startHhmm)}부터 감시 시작` }
    : PHASE_MSG[phase];

  return (
    <>
      <div className="mb-5 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-white/10 bg-white/[0.03] px-4 py-2.5 text-sm">
        <span className="flex items-center gap-2 font-medium">
          <span className={`inline-flex size-2.5 rounded-full ${status.dot}`} aria-hidden />
          {status.text}
        </span>
        <span className="text-xs text-warning">표시·참고용, 매수 추천 아님</span>
      </div>

      {youtong.length === 0 ? (
        <div className="rounded-xl border border-white/10 bg-white/[0.03] py-16 text-center">
          <p className="text-lg font-semibold">현재 조건을 충족하는 종목이 없습니다</p>
          <p className="mt-1 text-sm text-muted-foreground">
            {hhmm(th.startHhmm)} 이후 · 현재 등락률 +{th.changePct}% 이상 · 유통주식 회전율 {th.turnoverMin}%+ ·
            5분봉 양봉 분출(스파크) 발생 시 포착합니다 (한 번 뜨면 종일 유지, 이미 폭발한 종목은 폭발 페이지에서 확인).
          </p>
        </div>
      ) : (
        <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {youtong.map((e, i) => (
            <YoutongCard key={e.code} e={e} rank={i + 1} />
          ))}
        </div>
      )}
    </>
  );
}
