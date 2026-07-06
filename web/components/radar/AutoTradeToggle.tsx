"use client";

import { useEffect, useState } from "react";
import { Bot, AlertTriangle } from "lucide-react";

import { cn } from "@/lib/utils";
import { autoTradeClientService } from "@/services/autotrade.client";

type Suspect = { code?: string; name?: string; strength?: string | null };

const BUDGET_MIN = 10_000;
const BUDGET_MAX = 100_000_000;
const BUDGET_DEFAULT = 1_000_000;
const PRESETS_MAN = [50, 100, 200, 300, 500]; // 만원 빠른버튼

function clampBudget(won: number): number {
  if (!Number.isFinite(won)) return BUDGET_DEFAULT;
  return Math.max(BUDGET_MIN, Math.min(BUDGET_MAX, Math.floor(won)));
}

/**
 * 🤖 자동매매 On/Off + 상위 1~3위 개별 선택(최대 2종목) + 당일 총예산 설정 —
 * 매일 종가(15:18 KRX / NXT 19:50)에 선택 종목을 실계좌 매수(총예산을 선택 종목수로 균등분할),
 * -5% 손절 / +7% 50%익절 / +11% 잔량익절 / 본전방어 · 익일 14:50 강제청산으로 청산.
 * 웹은 KV(enabled·ranks·budget)만 세팅, 실제 주문은 실행기(autotrade_executor.py)가 낸다. ⚠ 실제 손익 발생.
 */
export function AutoTradeToggle({ suspects = [] }: { suspects?: Suspect[] }) {
  const top = suspects.slice(0, 3);
  const [enabled, setEnabled] = useState(false);
  const [ranks, setRanks] = useState<number[]>([1]);
  const [budget, setBudget] = useState<number>(BUDGET_DEFAULT);
  const [manInput, setManInput] = useState<string>(String(BUDGET_DEFAULT / 10_000));
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
        const b = s.budget || BUDGET_DEFAULT;
        setBudget(b);
        setManInput(String(Math.round(b / 10_000)));
        setConfigured(s.configured);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  const perStock = Math.floor(budget / (ranks.length || 1));

  function applyState(s: {
    enabled: boolean;
    ranks: number[];
    budget: number;
  }) {
    setEnabled(s.enabled);
    setRanks(s.ranks?.length ? s.ranks : [1]);
    const b = s.budget || BUDGET_DEFAULT;
    setBudget(b);
    setManInput(String(Math.round(b / 10_000)));
  }

  async function save(nextEnabled: boolean, nextRanks: number[], nextBudget: number) {
    setBusy(true);
    setErr("");
    try {
      applyState(await autoTradeClientService.set(nextEnabled, nextRanks, nextBudget));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "실패");
      // 저장 실패 → 서버 실제값으로 재동기화(화면≠실제 방지). ⚠ 단 KV 읽기까지 실패(configured:false)면
      // '상태 불명'이므로 화면을 가짜 OFF로 바꾸지 않는다 — 실행기가 아직 enabled=1로 켜져 실계좌가 매수 중일 수 있다.
      const UNKNOWN = "⚠ 상태 확인 불가 — 자동매매가 아직 켜져 있을 수 있습니다. 잠시 후 새로고침해 확인하세요.";
      try {
        const s = await autoTradeClientService.get();
        if (s.configured) applyState(s);
        else setErr(UNKNOWN);
      } catch {
        setErr(UNKNOWN); // GET도 실패 → 상태 불명. 토글을 확정값으로 바꾸지 않음.
      }
    } finally {
      setBusy(false);
    }
  }

  function commitBudget(man: number) {
    if (busy || !configured) return;
    const b = clampBudget(Math.round(man) * 10_000);
    if (b === budget) return; // 변경 없음
    if (enabled) {
      // 켜진 상태: 실매수 금액이 즉시 바뀌므로 확인(랭크 변경 확인창과 일관).
      const per = Math.floor(b / (ranks.length || 1));
      if (
        !window.confirm(
          `당일 총예산을 ${b.toLocaleString()}원으로 변경합니다.\n` +
            `선택 ${ranks.length}종목 · 종목당 ${per.toLocaleString()}원으로 매수됩니다. 계속할까요?`
        )
      ) {
        setManInput(String(Math.round(budget / 10_000))); // 취소 → 입력값 원복(저장 안 함)
        return;
      }
    }
    setBudget(b);
    setManInput(String(b / 10_000));
    void save(enabled, ranks, b);
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
      if (next.length === 0) {
        setRanks(next);
        void save(false, next, budget); // 모든 순위 해제 = 자동매매 중단(마스터 OFF까지 저장)
        return;
      }
      const per = Math.floor(budget / next.length);
      const lines = next
        .map((x) => `${x}위 ${top[x - 1]?.name ?? "-"}: ${per.toLocaleString()}원`)
        .join("\n");
      if (
        !window.confirm(
          `자동매매 대상을 변경합니다:\n\n${lines}\n\n실제 매수 종목·금액이 바뀝니다. 계속할까요?`
        )
      )
        return; // 취소 → 상태 유지
      setRanks(next);
      void save(true, next, budget);
    } else {
      setRanks(next); // 꺼진 상태: 로컬만
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
      const per = Math.floor(budget / ranks.length);
      const lines = ranks
        .map((r) => `${r}위 ${top[r - 1]?.name ?? "-"}: ${per.toLocaleString()}원`)
        .join("\n");
      if (
        !window.confirm(
          `자동매매를 켭니다.\n\n매일 종가(15:18 / NXT 19:50)에 아래 종목을 실계좌 매수합니다 ` +
            `(당일 총예산 ${budget.toLocaleString()}원):\n\n${lines}\n\n실제 주문·실제 손익이 발생합니다. 계속할까요?`
        )
      )
        return;
    }
    void save(next, ranks, budget);
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
          선택 {ranks.length}종목 · 각 {perStock.toLocaleString()}원 (총예산 {budget.toLocaleString()} 분할)
        </span>
      </div>

      {/* 당일 총예산 설정 (만원) */}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">당일 총예산</span>
        <div className="flex items-center gap-1">
          <input
            type="number"
            inputMode="numeric"
            value={manInput}
            disabled={busy || !configured}
            onChange={(e) => setManInput(e.target.value)}
            onBlur={() => commitBudget(Number(manInput) || 0)}
            onKeyDown={(e) => {
              if (e.key === "Enter") commitBudget(Number(manInput) || 0);
            }}
            className="w-20 rounded-md border border-white/15 bg-white/[0.04] px-2 py-1 text-right text-sm tabular-nums outline-none focus:border-down/60"
          />
          <span className="text-xs text-muted-foreground">만원</span>
        </div>
        {PRESETS_MAN.map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => commitBudget(m)}
            disabled={busy || !configured}
            className={cn(
              "rounded-md border px-2 py-1 text-xs tabular-nums transition-colors disabled:opacity-40",
              budget === m * 10_000
                ? "border-down/60 bg-down/[0.12] text-down"
                : "border-white/10 bg-white/[0.02] hover:border-white/25"
            )}
          >
            {m}만
          </button>
        ))}
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
                {s?.strength ? (
                  <span className="ml-1 text-[10px] text-warning">· {s.strength}</span>
                ) : null}
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
            : "실계좌 종가 자동매수(최대 2종목·총예산 분할) · -5% 손절/+7% 50%익절/+11% 익절 · 실제 손익 발생"}
      </p>
    </div>
  );
}
