"use client";

import { useEffect, useState } from "react";
import { Bot, AlertTriangle } from "lucide-react";

import { cn } from "@/lib/utils";
import { autoTradeClientService } from "@/services/autotrade.client";

type Suspect = { code?: string; name?: string };

/**
 * 🤖 자동매매 On/Off + 상위 1~3위 개별 선택(최대 2종목) — 매일 종가(15:18 KRX / NXT 19:50)에
 * 선택 종목을 실계좌로 매수(당일 100만원을 선택 종목수로 균등분할: 2종목=각 50만 / 1종목=100만),
 * -5% 손절 / +7% 50%익절 / +11% 잔량익절 / 본전방어 · 익일 14:50 강제청산으로 청산한다.
 * 웹은 KV(enabled·ranks)만 세팅하고, 실제 주문은 실행기(autotrade_executor.py)가 낸다. ⚠ 실제 손익 발생.
 */
export function AutoTradeToggle({ suspects = [] }: { suspects?: Suspect[] }) {
  const top = suspects.slice(0, 3);
  const [enabled, setEnabled] = useState(false);
  const [ranks, setRanks] = useState<number[]>([1]);
  const [configured, setConfigured] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    let alive = true;
    autoTradeClientService
      .get()
      .then((s) => {
        if (!alive) return;
        setEnabled(s.enabled);
        setRanks(s.ranks?.length ? s.ranks : [1]);
        setConfigured(s.configured);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  const perStock = ranks.length ? Math.floor(1_000_000 / ranks.length) : 1_000_000;

  async function save(nextEnabled: boolean, nextRanks: number[]) {
    setBusy(true);
    setErr("");
    try {
      const s = await autoTradeClientService.set(nextEnabled, nextRanks);
      setEnabled(s.enabled);
      setRanks(s.ranks?.length ? s.ranks : [1]);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "실패");
    } finally {
      setBusy(false);
    }
  }

  function toggleRank(r: number) {
    if (busy || !configured) return;
    let next: number[];
    if (ranks.includes(r)) next = ranks.filter((x) => x !== r);
    else {
      if (ranks.length >= 2) return; // 최대 2
      next = [...ranks, r].sort();
    }
    if (enabled) {
      // 켜진 상태: 실매수 대상이 바뀌므로 즉시 KV 반영.
      if (next.length === 0) {
        // 모든 순위 해제 = 자동매매 중단(마스터 OFF까지 저장 — UI만 비고 실행기는 계속 사는 괴리 방지).
        setRanks(next);
        void save(false, next);
        return;
      }
      const per = Math.floor(1_000_000 / next.length);
      const lines = next
        .map((x) => `${x}위 ${top[x - 1]?.name ?? "-"}: ${per.toLocaleString()}원`)
        .join("\n");
      if (
        !window.confirm(
          `자동매매 대상을 변경합니다:\n\n${lines}\n\n실제 매수 종목·금액이 바뀝니다. 계속할까요?`
        )
      )
        return; // 취소 → 로컬 상태도 그대로 유지(변경 안 함)
      setRanks(next);
      void save(true, next);
    } else {
      setRanks(next); // 꺼진 상태: 로컬만(마스터 켤 때 저장)
    }
  }

  async function toggleMaster() {
    if (busy || !configured) return;
    const next = !enabled;
    if (next) {
      if (!ranks.length) {
        setErr("매수할 순위를 최소 1개 선택하세요");
        return;
      }
      const lines = ranks
        .map((r) => `${r}위 ${top[r - 1]?.name ?? "-"}: ${perStock.toLocaleString()}원`)
        .join("\n");
      if (
        !window.confirm(
          `자동매매를 켭니다.\n\n매일 종가(15:18 / NXT 19:50)에 아래 종목을 실계좌 매수합니다:\n\n${lines}\n\n` +
            `실제 주문·실제 손익이 발생합니다. 계속할까요?`
        )
      )
        return;
    }
    void save(next, ranks);
  }

  return (
    <div
      className={cn(
        "mb-6 rounded-lg border px-4 py-3",
        enabled ? "border-down/50 bg-down/[0.06]" : "border-white/10 bg-white/[0.03]"
      )}
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="flex items-center gap-2 font-semibold">
          <Bot className={cn("size-4", enabled ? "text-down" : "text-muted-foreground")} aria-hidden />
          자동매매
        </span>
        <button
          type="button"
          onClick={toggleMaster}
          disabled={busy || !configured}
          role="switch"
          aria-checked={enabled}
          className={cn(
            "relative inline-flex h-6 w-11 items-center rounded-full transition-colors disabled:opacity-40",
            enabled ? "bg-down" : "bg-white/20"
          )}
        >
          <span
            className={cn(
              "inline-block size-4 transform rounded-full bg-white transition-transform",
              enabled ? "translate-x-6" : "translate-x-1"
            )}
          />
        </button>
        <span className={cn("text-sm font-medium", enabled ? "text-down" : "text-muted-foreground")}>
          {busy ? "…" : enabled ? "ON" : "OFF"}
        </span>
        <span className="text-xs text-muted-foreground">
          선택 {ranks.length}종목 · 각 {perStock.toLocaleString()}원 (당일 100만 분할)
        </span>
      </div>

      {/* 상위 3위 개별 선택 (최대 2) */}
      <div className="mt-3 grid gap-2 sm:grid-cols-3">
        {[1, 2, 3].map((r) => {
          const s = top[r - 1];
          const checked = ranks.includes(r);
          const disabledRow = !configured || !s?.code || (!checked && ranks.length >= 2);
          return (
            <button
              key={r}
              type="button"
              onClick={() => toggleRank(r)}
              disabled={busy || disabledRow}
              className={cn(
                "flex items-center gap-2 rounded-md border px-3 py-2 text-left text-sm transition-colors",
                checked ? "border-down/60 bg-down/[0.1]" : "border-white/10 bg-white/[0.02]",
                disabledRow && !checked ? "opacity-40" : ""
              )}
            >
              <span
                className={cn(
                  "flex size-4 shrink-0 items-center justify-center rounded border text-[10px]",
                  checked ? "border-down bg-down text-white" : "border-white/30"
                )}
              >
                {checked ? "✓" : ""}
              </span>
              <span className="min-w-0">
                <span className="font-semibold">{r}위</span>{" "}
                <span className="text-foreground">{s?.name ?? "—"}</span>
                {s?.code ? <span className="tabular-nums text-muted-foreground"> ({s.code})</span> : null}
              </span>
            </button>
          );
        })}
      </div>

      <p className="mt-2 flex items-center gap-1 text-xs text-warning">
        <AlertTriangle className="size-3" aria-hidden />
        {!configured
          ? "KV 미설정 — 토글 비활성(Upstash 연결 필요)"
          : err
            ? err
            : "실계좌 종가 자동매수(최대 2종목·100만 분할) · -5% 손절/+7% 50%익절/+11% 익절 · 실제 손익 발생"}
      </p>
    </div>
  );
}
