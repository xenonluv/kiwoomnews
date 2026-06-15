"use client";

import { useEffect, useState } from "react";
import { Bookmark, BookmarkCheck } from "lucide-react";

import { cn } from "@/lib/utils";
import { trackClientService } from "@/services/track.client";

/** 📌 추적 버튼 — 종목을 추적 목록(KV)에 추가. 이후 Mac cron이 매일 종합판정·AI 예측을
 * 기록하고 익일 결과로 검증(둘 중 누가 맞나)한다. 결과는 /performance "추적 종목"에 표시. */
export function TrackButton({ code }: { code: string }) {
  const [state, setState] = useState<"idle" | "tracked" | "loading" | "error">("idle");
  const [msg, setMsg] = useState("");

  useEffect(() => {
    let alive = true;
    trackClientService.isTracked(code).then((t) => {
      if (alive && t) setState("tracked");
    });
    return () => {
      alive = false;
    };
  }, [code]);

  async function onClick() {
    if (state === "tracked" || state === "loading") return;
    setState("loading");
    try {
      await trackClientService.add(code);
      setState("tracked");
    } catch (e) {
      setState("error");
      setMsg(e instanceof Error ? e.message : "실패");
    }
  }

  const tracked = state === "tracked";
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={tracked || state === "loading"}
      title={
        tracked
          ? "추적 중 — 매일 종합판정·AI 예측을 기록하고 익일 결과로 검증(/performance 추적 종목)"
          : "추적 목록에 추가 — 매일 종합판정·AI 예측을 기록해 누가 맞는지 검증"
      }
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-2.5 py-1 text-xs font-medium transition-colors",
        tracked
          ? "border-up/50 bg-up/10 text-up"
          : state === "error"
            ? "border-down/50 text-down"
            : "border-white/15 text-muted-foreground hover:border-white/30 hover:text-foreground"
      )}
    >
      {tracked ? <BookmarkCheck className="size-3.5" aria-hidden /> : <Bookmark className="size-3.5" aria-hidden />}
      {state === "loading" ? "추가 중…" : tracked ? "추적 중" : state === "error" ? msg || "실패" : "추적"}
    </button>
  );
}
