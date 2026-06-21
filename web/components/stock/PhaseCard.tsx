"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, RotateCcw, Layers } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { stockClientService } from "@/services/stock.client";
import type { PhaseAnalysis, StockPhase } from "@/types/stock";
import { cn } from "@/lib/utils";

// 재매집=매집(긍정·빨강) / 분산=이탈(부정·파랑) / 중립=회색. (한국 색 관례: 상승 빨강·하락 파랑)
const PHASE_STYLE: Record<StockPhase, { badge: "up" | "down" | "neutral"; text: string; label: string }> = {
  재매집: { badge: "up", text: "text-up", label: "재매집(식음 후 재상승)" },
  분산: { badge: "down", text: "text-down", label: "분산(고점)" },
  중립: { badge: "neutral", text: "text-muted-foreground", label: "중립(혼재)" },
};

type Status = "idle" | "loading" | "done" | "error";

/**
 * AI 국면 판정 — "지금 식음(재매집)인가 고점(분산)인가". 룰베이스 게이트가 애매한 구간을 위해
 * 찌라시·뉴스·애널·수급을 AI(Kimi)가 종합해 보조 판단. 버튼 클릭 온디맨드(결과 30분 캐시).
 */
export function PhaseCard({ code }: { code: string }) {
  const [status, setStatus] = useState<Status>("idle");
  const [result, setResult] = useState<PhaseAnalysis | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setStatus("idle");
    setResult(null);
    setError(null);
  }, [code]);

  const run = () => {
    setStatus("loading");
    setError(null);
    const requested = code;
    stockClientService
      .getPhaseAnalysis(requested)
      .then((r) => {
        if (r.code !== requested) return; // stale 가드
        setResult(r);
        setStatus("done");
      })
      .catch((e) => {
        setError(e instanceof Error ? e.message : "국면 판정에 실패했습니다.");
        setStatus("error");
      });
  };

  const st = result ? PHASE_STYLE[result.phase] : null;

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-lg">
          <Layers className="size-4 text-warning" aria-hidden />
          AI 국면 판정 · 식음 vs 고점
          {result && st && <Badge variant={st.badge}>{result.phase}</Badge>}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {status === "idle" && (
          <div className="flex flex-col items-center gap-3 py-4 text-center">
            <p className="text-xs text-muted-foreground">
              버튼을 누르면 AI(Kimi)가 수급·기술·뉴스·애널 + 토론방·텔레그램 찌라시를 종합해
              지금이 <b>재매집(식음 후 재상승)</b>인지 <b>분산(고점)</b>인지 판정합니다. (약 5~20초)
            </p>
            <Button size="sm" onClick={run}>
              <Layers aria-hidden /> 식음/고점 판정하기
            </Button>
          </div>
        )}

        {status === "loading" && (
          <div className="space-y-2 py-2" aria-label="국면 판정 중">
            <div className="h-5 w-44 animate-pulse rounded-md bg-white/10" />
            <div className="h-16 animate-pulse rounded-md bg-white/5" />
            <p className="text-center text-xs text-muted-foreground">
              AI가 데이터·찌라시를 종합하는 중입니다… (약 5~20초)
            </p>
          </div>
        )}

        {status === "error" && (
          <div className="flex flex-col items-center gap-2 py-3 text-sm text-muted-foreground">
            <AlertTriangle className="size-5 text-warning" aria-hidden />
            <p>{error}</p>
            <Button variant="outline" size="sm" onClick={run}>
              <RotateCcw aria-hidden /> 다시 시도
            </Button>
          </div>
        )}

        {status === "done" && result && st && (
          <>
            <div className="flex items-baseline gap-2">
              <span className={cn("text-2xl font-bold", st.text)}>{st.label}</span>
              <span className={cn("text-sm tabular-nums", st.text)}>신뢰도 {result.confidence}%</span>
            </div>

            <p className="text-sm leading-relaxed">{result.narrative}</p>

            {result.reasons.length > 0 && (
              <div>
                <p className="mb-1 text-xs font-semibold text-muted-foreground">판정 근거</p>
                <ul className="space-y-1">
                  {result.reasons.map((x) => (
                    <li key={x} className="text-xs leading-relaxed">· {x}</li>
                  ))}
                </ul>
              </div>
            )}

            {result.risks.length > 0 && (
              <ul className="space-y-1 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
                {result.risks.map((x) => (
                  <li key={x} className="flex items-center gap-1.5">
                    <AlertTriangle className="size-3 shrink-0" aria-hidden /> {x}
                  </li>
                ))}
              </ul>
            )}

            <p className="text-[10px] text-muted-foreground">
              참고 원문: 뉴스 {result.sourceCounts.news} · 토론방 {result.sourceCounts.board} · 텔레그램{" "}
              {result.sourceCounts.telegram}건(B/T는 미확인 찌라시) · {result.model} · {result.asOf}
            </p>
            <p className="text-[10px] text-muted-foreground">{result.caveat}</p>
          </>
        )}
      </CardContent>
    </Card>
  );
}
