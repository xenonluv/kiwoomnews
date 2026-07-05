"use client";

import { useEffect, useState } from "react";
import { Bot, AlertTriangle } from "lucide-react";

import { cn } from "@/lib/utils";
import { autoTradeClientService } from "@/services/autotrade.client";

/**
 * 🤖 자동매매 On/Off — 레이더 메인 1위(suspects[0])를 매일 종가(15:18 KRX / NXT 19:50)에
 * 실계좌 100만원 자동 매수하고, -5% 손절 / +7% 50%익절 / +11% 잔량익절 / 본전방어로 청산한다.
 * 웹은 KV 플래그만 세팅하고, 실제 주문은 Windows 실행기(autotrade_executor.py)가 낸다.
 * ⚠ 실제 주문·실제 손익 발생. On 전환 시 확인창.
 */
export function AutoTradeToggle({ code, name }: { code?: string; name?: string }) {
  const [enabled, setEnabled] = useState(false);
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
        setConfigured(s.configured);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  async function toggle() {
    if (busy || !configured) return;
    const next = !enabled;
    if (
      next &&
      !window.confirm(
        `자동매매를 켭니다.\n\n레이더 1위 종목(${name ?? "-"})을 매일 종가(15:18 / NXT 19:50)에 ` +
          `실계좌로 100만원 자동 매수합니다.\n\n실제 주문·실제 손익이 발생합니다. 계속할까요?`
      )
    )
      return;
    setBusy(true);
    setErr("");
    try {
      const s = await autoTradeClientService.set(next, code, name);
      setEnabled(s.enabled);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "실패");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className={cn(
        "mb-6 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border px-4 py-3",
        enabled ? "border-down/50 bg-down/[0.06]" : "border-white/10 bg-white/[0.03]"
      )}
    >
      <span className="flex items-center gap-2 font-semibold">
        <Bot className={cn("size-4", enabled ? "text-down" : "text-muted-foreground")} aria-hidden />
        자동매매
      </span>

      <span className="text-sm text-muted-foreground">
        대상 1위:{" "}
        <span className="font-medium text-foreground">{name ?? "—"}</span>
        {code ? <span className="tabular-nums text-muted-foreground"> ({code})</span> : null}
      </span>

      <button
        type="button"
        onClick={toggle}
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

      <span className="flex w-full items-center gap-1 text-xs text-warning">
        <AlertTriangle className="size-3" aria-hidden />
        {!configured
          ? "KV 미설정 — 토글 비활성(Upstash 연결 필요)"
          : err
            ? err
            : "실계좌 100만원 종가 자동매수 · -5% 손절/+7% 50%익절/+11% 익절 · 실제 손익 발생"}
      </span>
    </div>
  );
}
