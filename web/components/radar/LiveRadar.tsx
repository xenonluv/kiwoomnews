"use client";

import { useEffect, useRef, useState } from "react";
import { Radar } from "lucide-react";

import { EventStrip } from "./EventStrip";
import { ThemeStrip } from "./ThemeStrip";
import { SuspectCard } from "./SuspectCard";
import { AutoTradeToggle } from "./AutoTradeToggle";
import { radarClientService } from "@/services/radar.client";
import type { RadarData } from "@/types/radar";

/** 자동 확인 주기(ms). publish cron이 5분 주기이므로 새 결과를 1분 이내 반영. */
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
          <span className="text-up">장중 스캔 중 · 자동 갱신(약 5분 주기)</span>
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
  const [selectedTheme, setSelectedTheme] = useState<string | null>(null);
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

  // 폴링으로 데이터가 교체된 뒤 선택한 칩의 대상이 사라지면(테마 소멸/이벤트 만료) 필터 자동 해제.
  // — 칩이 더는 렌더되지 않아 다시 클릭해 해제할 수 없는 '빈 화면에 갇힘'을 방지.
  useEffect(() => {
    if (selectedEvent && !data.events.some((e) => e.id === selectedEvent)) {
      setSelectedEvent(null);
    }
    if (selectedTheme && !data.suspects.some((s) => s.theme === selectedTheme)) {
      setSelectedTheme(null);
    }
  }, [data, selectedEvent, selectedTheme]);

  // 테마 칩 — 수상 종목의 상위 테마를 빈도순(동률은 이름순)으로 + 테마별 대장(거래대금 1위).
  // (개편 후 reaccum이 유일 산출물 = 전부 visible_experimental이므로 실험 여부로 거르지 않는다.
  //  publish.py의 theme_leader 태깅과 동일 모집단 — 칩·필터·🏆 대장 배지가 일치한다.)
  const themeCounts = new Map<string, number>();
  const themeLeader = new Map<string, string>();
  for (const s of data.suspects) {
    if (!s.theme) continue;
    themeCounts.set(s.theme, (themeCounts.get(s.theme) ?? 0) + 1);
    if (s.theme_leader) themeLeader.set(s.theme, s.name);
  }
  const themes = [...themeCounts.entries()]
    .map(([name, count]) => ({ name, count, leader: themeLeader.get(name) }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));

  // 테마 필터/리셋/칩 집계는 동일 모집단 — 칩 count와 클릭 결과 일치.
  const inSelectedTheme = (s: RadarData["suspects"][number]) => s.theme === selectedTheme;
  const suspects = data.suspects.filter(
    (s) =>
      (!selectedEvent || s.matched_events.some((m) => m.id === selectedEvent)) &&
      (!selectedTheme || inSelectedTheme(s))
  );

  return (
    <>
      <LiveStatusBar data={data} justUpdated={justUpdated} />
      <AutoTradeToggle suspects={data.suspects.slice(0, 3)} />
      <EventStrip events={data.events} selected={selectedEvent} onSelect={setSelectedEvent} />
      <ThemeStrip themes={themes} selected={selectedTheme} onSelect={setSelectedTheme} />

      <section>
        <div className="mb-4">
          <h2 className="text-xl font-bold tracking-tight">
            🕵️ 수상 종목{" "}
            <span className="text-sm font-normal text-muted-foreground tabular-nums">
              {suspects.length}
            </span>
          </h2>
          <p className="text-xs text-muted-foreground">
            폭발(고가 +{data.params.explosion_high_pct ?? 22}% · 거래량 유통주식수의{" "}
            {data.params.explosion_vol_turnover ?? 90}%+, 최근 {data.params.explosion_window ?? 6}거래일) →{" "}
            {(data.params.reignition_start ?? "1430").replace(/^(\d\d)(\d\d)$/, "$1:$2")}↑ 재분출 (5분 양봉 몸통{" "}
            {data.params.reignition_body_pct ?? 2}%+ · {data.params.reignition_min_count ?? 2}회+ 스파크 · 현재 등락률{" "}
            {(data.params.reaccum_change_min ?? -5) >= 0 ? "+" : ""}
            {data.params.reaccum_change_min ?? -5}~{(data.params.reaccum_change_max ?? 7) >= 0 ? "+" : ""}
            {data.params.reaccum_change_max ?? 7}%)
          </p>
        </div>

        {suspects.length === 0 ? (
          <div className="flex min-h-48 flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border text-sm text-muted-foreground">
            <Radar className="size-8 opacity-60" aria-hidden />
            {selectedEvent || selectedTheme ? (
              <p>이 필터에 해당하는 수상 종목이 없습니다. (필터 해제: 칩 다시 클릭)</p>
            ) : (
              <p>오늘은 레이더에 잡힌 종목이 없습니다 — 조건을 모두 만족하는 날만 표시됩니다.</p>
            )}
          </div>
        ) : (
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {suspects.map((s) => (
              <SuspectCard
                key={s.code}
                s={s}
                disclaimer={data.disclaimer}
              />
            ))}
          </div>
        )}
      </section>

      {(data.blocked_suspects?.length ?? 0) > 0 && (
        <section className="mt-6 rounded-lg border border-up/30 bg-up/5 px-4 py-3">
          <h2 className="text-sm font-semibold text-up">⛔ 다음 거래일 추천 제외</h2>
          <div className="mt-2 space-y-1 text-xs text-muted-foreground">
            {data.blocked_suspects!.map((s) => (
              <p key={s.code}>
                <span className="font-medium text-foreground">{s.name} ({s.code})</span>
                {" · "}
                {s.next_session_eligibility?.reason ?? s.blocked_reason ?? "공시 확인 불완전"}
                {s.next_session_eligibility?.checked_at && (
                  <span className="ml-1 tabular-nums">({s.next_session_eligibility.checked_at})</span>
                )}
              </p>
            ))}
          </div>
        </section>
      )}
    </>
  );
}
