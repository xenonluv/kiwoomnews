"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, RotateCcw, Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { stockClientService } from "@/services/stock.client";
import type { AiAnalysis, AiDirection } from "@/types/stock";
import { cn } from "@/lib/utils";

// 한국 색 관례: 상승=빨강(up), 하락=파랑(down)
const DIRECTION_STYLE: Record<AiDirection, { badge: "up" | "down" | "neutral"; text: string }> = {
  상승: { badge: "up", text: "text-up" },
  하락: { badge: "down", text: "text-down" },
  관망: { badge: "neutral", text: "text-muted-foreground" },
};

type Status = "idle" | "loading" | "done" | "error";

/**
 * AI 심층 분석 — 버튼 클릭 시에만 Kimi LLM 호출(비용 절약).
 * 룰베이스 판정(VerdictCard)을 대체하지 않고 나란히 보조 의견으로 표시.
 */
export function AiAnalysisCard({ code }: { code: string }) {
  const [status, setStatus] = useState<Status>("idle");
  const [analysis, setAnalysis] = useState<AiAnalysis | null>(null);
  const [error, setError] = useState<string | null>(null);

  // 종목이 바뀌면 이전 결과 초기화
  useEffect(() => {
    setStatus("idle");
    setAnalysis(null);
    setError(null);
  }, [code]);

  const run = () => {
    setStatus("loading");
    setError(null);
    const requested = code;
    stockClientService
      .getAiAnalysis(requested)
      .then((a) => {
        if (a.code !== requested) return; // stale 가드
        setAnalysis(a);
        setStatus("done");
      })
      .catch((e) => {
        setError(e instanceof Error ? e.message : "AI 분석에 실패했습니다.");
        setStatus("error");
      });
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-lg">
          <Sparkles className="size-4 text-warning" aria-hidden />
          AI 심층 분석
          {analysis && (
            <Badge variant={DIRECTION_STYLE[analysis.direction].badge}>
              익일 {analysis.direction}
            </Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {status === "idle" && (
          <div className="flex flex-col items-center gap-3 py-4 text-center">
            <p className="text-xs text-muted-foreground">
              버튼을 누르면 AI(Kimi)가 위 리포트 전체(점수 해부·기술지표·수급·뉴스·이벤트)를
              읽고 다음 거래일 상승 확률을 독립적으로 추정합니다. (약 5~20초)
            </p>
            <Button size="sm" onClick={run}>
              <Sparkles aria-hidden /> AI로 분석하기
            </Button>
          </div>
        )}

        {status === "loading" && (
          <div className="space-y-2 py-2" aria-label="AI 분석 중">
            <div className="h-5 w-40 animate-pulse rounded-md bg-white/10" />
            <div className="h-16 animate-pulse rounded-md bg-white/5" />
            <p className="text-center text-xs text-muted-foreground">
              AI가 리포트를 읽고 판단하는 중입니다… (약 5~20초)
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

        {status === "done" && analysis && (
          <>
            <div className="flex items-baseline gap-2">
              <span className={cn("text-2xl font-bold", DIRECTION_STYLE[analysis.direction].text)}>
                {analysis.direction}
              </span>
              <span
                className={cn("text-sm tabular-nums", DIRECTION_STYLE[analysis.direction].text)}
              >
                상승 확률 {analysis.probUp}%
              </span>
              <span className="text-[10px] text-muted-foreground">(50%=중립)</span>
            </div>

            <p className="text-sm leading-relaxed">{analysis.narrative}</p>

            {analysis.reasons.length > 0 && (
              <div>
                <p className="mb-1 text-xs font-semibold text-muted-foreground">핵심 근거</p>
                <ul className="space-y-1">
                  {analysis.reasons.map((x) => (
                    <li key={x} className="text-xs leading-relaxed">
                      · {x}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {analysis.risks.length > 0 && (
              <ul className="space-y-1 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
                {analysis.risks.map((x) => (
                  <li key={x} className="flex items-center gap-1.5">
                    <AlertTriangle className="size-3 shrink-0" aria-hidden /> {x}
                  </li>
                ))}
              </ul>
            )}

            <p className="text-[10px] text-muted-foreground">
              {analysis.model} · {analysis.asOf} 생성 · AI가 생성한 의견으로 부정확할 수 있으며
              매수·매도 추천이 아닙니다. 투자 판단과 책임은 본인에게 있습니다.
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}
