"use client";

import { useEffect, useRef, useState } from "react";
import { Radar } from "lucide-react";

import { EventStrip } from "./EventStrip";
import { SuspectCard } from "./SuspectCard";
import { radarClientService } from "@/services/radar.client";
import type { RadarData } from "@/types/radar";

/** 자동 갱신 주기(ms). 데이터는 cron 15분 주기로 바뀌므로 60초면 충분. */
const POLL_MS = 60_000;

function LiveStatusBar({ data, justUpdated }: { data: RadarData; justUpdated: boolean }) {
  const open = data.market_session === "open";
  return (
    <div className="mb-6 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-white/10 bg-white/[0.03] px-4 py-2.5 text-sm">
      <span className="flex items-center gap-2 font-medium">
        <span className="relative flex size-2.5">
          {open && (
            <span
              className={`absolute inline-flex size-full rounded-full bg-up/70 ${justUpdated ? "animate-ping" : "animate-[ping_2.5s_ease-in-out_infinite]"}`}
              aria-hidden
            />
          )}
          <span
            className={`relative inline-flex size-2.5 rounded-full ${open ? "bg-up" : "bg-muted-foreground/50"}`}
            aria-hidden
          />
        </span>
        {open ? (
          <span className="text-up">장중 스캔 중 · 15분마다 자동 업데이트</span>
        ) : (
          <span className="text-muted-foreground">장 마감 · 마지막 스캔 결과</span>
        )}
      </span>
      <span className="text-xs text-muted-foreground tabular-nums">
        기준: {data.generated_at} · 스캔 {data.universe_count}종목
      </span>
      <span className="text-xs text-warning">투자 참고용 · 매수 추천 아님</span>
    </div>
  );
}

/**
 * 레이더 메인 — 서버 초기 데이터로 즉시 렌더(정적/SEO),
 * 이후 60초마다 조용히 재요청해 변경분만 상태 교체. 이벤트 칩으로 종목 필터.
 */
export function LiveRadar({ initial }: { initial: RadarData }) {
  const [data, setData] = useState<RadarData>(initial);
  const [selectedEvent, setSelectedEvent] = useState<string | null>(null);
  const [justUpdated, setJustUpdated] = useState(false);
  const lastJson = useRef<string>(JSON.stringify(initial));

  useEffect(() => {
    let alive = true;

    async function refresh() {
      try {
        const next = await radarClientService.get();
        if (!alive) return;
        const nextJson = JSON.stringify(next);
        if (nextJson !== lastJson.current) {
          lastJson.current = nextJson;
          setData(next);
          setJustUpdated(true);
          setTimeout(() => alive && setJustUpdated(false), 4000);
        }
      } catch {
        // 폴링 실패는 조용히 무시 (다음 주기 재시도)
      }
    }

    const id = setInterval(refresh, POLL_MS);
    const onVisible = () => {
      if (document.visibilityState === "visible") refresh();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      alive = false;
      clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  const suspects = selectedEvent
    ? data.suspects.filter((s) => s.matched_events.some((m) => m.id === selectedEvent))
    : data.suspects;

  return (
    <>
      <LiveStatusBar data={data} justUpdated={justUpdated} />
      <EventStrip events={data.events} selected={selectedEvent} onSelect={setSelectedEvent} />

      <section>
        <div className="mb-4">
          <h2 className="text-xl font-bold tracking-tight">
            🕵️ 수상 종목{" "}
            <span className="text-sm font-normal text-muted-foreground tabular-nums">
              {suspects.length}
            </span>
          </h2>
          <p className="text-xs text-muted-foreground">
            {data.params.universe === "kis_rank"
              ? `시장별 거래대금·등락률 TOP${data.params.top_n ?? 20} 유니버스 · `
              : ""}
            당일 거래대금 {data.params.min_value_eok?.toLocaleString()}억+ · 고가 +
            {data.params.high_pct}% 후 후퇴 · 등락률 {data.params.chg_range?.[0]}~
            {data.params.chg_range?.[1]}% · 10일선 위 · 분봉 스파크
            {data.params.shake_pct != null &&
              ` · 흔들기 재상승(−${data.params.shake_pct}%+ 눌림 후 회복, ≤+${data.params.shake_chg_max}%)`}
          </p>
        </div>

        {suspects.length === 0 ? (
          <div className="flex min-h-48 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border text-sm text-muted-foreground">
            <Radar className="size-8 opacity-60" aria-hidden />
            {selectedEvent ? (
              <p>이 이벤트에 민감한 수상 종목이 없습니다. (필터 해제: 칩 다시 클릭)</p>
            ) : (
              <p>오늘은 레이더에 잡힌 종목이 없습니다 — 조건을 모두 만족하는 날만 표시됩니다.</p>
            )}
          </div>
        ) : (
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {suspects.map((s) => (
              <SuspectCard key={s.code} s={s} disclaimer={data.disclaimer} />
            ))}
          </div>
        )}
      </section>
    </>
  );
}
