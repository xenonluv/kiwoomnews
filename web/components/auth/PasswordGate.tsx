"use client";

import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";

// ⚠ 프론트 전용 cosmetic 게이트 — 화면만 가린다. 비밀번호는 클라이언트 번들에 노출되고
// 공개 API(/api/radar·/api/stock·radar.json)는 그대로 접근 가능하다(실제 보안 아님).
// 위협 모델: "URL을 우연히 연 비기술 방문자가 대시보드를 바로 못 보게" 한정.
const STORAGE_KEY = "sn_gate_ok";
const PASSWORD = process.env.NEXT_PUBLIC_GATE_PASSWORD;

type GateState = "checking" | "locked" | "unlocked";

export function PasswordGate({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<GateState>("checking");
  const [input, setInput] = useState("");
  const [error, setError] = useState(false);

  useEffect(() => {
    // 비밀번호 미설정(env 없음) → 게이트 비활성(통과). 환경변수 누락 시 본인 잠금 사고 방지.
    if (!PASSWORD) {
      setState("unlocked");
      return;
    }
    try {
      if (localStorage.getItem(STORAGE_KEY) === "1") {
        setState("unlocked");
        return;
      }
    } catch {
      /* localStorage 차단(시크릿 등) → 잠금 유지 */
    }
    setState("locked");
  }, []);

  // 마운트 전엔 콘텐츠/폼 어느 쪽도 그리지 않아 깜빡임 방지
  if (state === "checking") return null;
  if (state === "unlocked") return <>{children}</>;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (input === PASSWORD) {
      try {
        localStorage.setItem(STORAGE_KEY, "1");
      } catch {
        /* 저장 실패해도 이번 세션은 통과 */
      }
      setState("unlocked");
    } else {
      setError(true);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <form onSubmit={submit} className="w-full max-w-xs space-y-4 text-center">
        <div className="space-y-1">
          <h1 className="text-lg font-bold tracking-tight">StockNews</h1>
          <p className="text-sm text-muted-foreground">비밀번호를 입력하세요</p>
        </div>
        <input
          type="password"
          autoFocus
          value={input}
          onChange={(e) => {
            setInput(e.target.value);
            setError(false);
          }}
          aria-label="비밀번호"
          className="w-full rounded-md border border-white/10 bg-white/5 px-3 py-2 text-center text-sm text-foreground outline-none focus:border-white/30"
        />
        {error && <p className="text-sm text-red-400">비밀번호가 올바르지 않습니다.</p>}
        <Button type="submit" className="w-full">
          입장
        </Button>
      </form>
    </div>
  );
}
