"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, ExternalLink, MessageCircleQuestion, Send } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { stockClientService } from "@/services/stock.client";
import type { AskItem, StockAnswer } from "@/types/stock";

type Status = "idle" | "loading" | "done" | "error";

const LABEL_STYLE: Record<AskItem["label"], string> = {
  데이터: "neutral",
  뉴스: "neutral",
  토론방: "warning",
  텔레그램: "warning",
};

/** 근거 항목의 원문 링크(있을 때만) — 사용자가 직접 열어 검증. */
function SourceLink({ url }: { url?: string | null }) {
  if (!url) return null;
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="ml-1 inline-flex items-center gap-0.5 align-middle text-[10px] text-primary hover:underline"
    >
      원문
      <ExternalLink className="size-2.5" aria-hidden />
    </a>
  );
}

/**
 * AI 자유질문 — 사용자가 질문을 치면 Kimi가 그 종목의 데이터·뉴스·찌라시 원문만 근거로 답한다.
 * 환각 차단: 서버가 모델 인용을 원문과 대조해 통과분만 반환. 찌라시는 "미확인 루머"로 표시.
 * 기존 AiAnalysisCard(방향예측)와 별개로 나란히 둔다.
 */
export function AskQuestionCard({ code }: { code: string }) {
  const [question, setQuestion] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [result, setResult] = useState<StockAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setQuestion("");
    setStatus("idle");
    setResult(null);
    setError(null);
  }, [code]);

  const submit = () => {
    const q = question.trim();
    if (q.length < 2 || status === "loading") return;
    setStatus("loading");
    setError(null);
    const requested = code;
    stockClientService
      .askQuestion(requested, q)
      .then((a) => {
        if (a.code !== requested) return; // stale 가드
        setResult(a);
        setStatus("done");
      })
      .catch((e) => {
        setError(e instanceof Error ? e.message : "질문 처리에 실패했습니다.");
        setStatus("error");
      });
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    // Enter=전송, Shift+Enter=줄바꿈
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-lg">
          <MessageCircleQuestion className="size-4 text-warning" aria-hidden />
          AI에게 질문하기
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">
          이 종목에 대해 자유롭게 물어보세요. AI가 위 데이터 + 뉴스 + 찌라시(토론방·텔레그램)를
          검색해 <strong>실제 자료에 근거해서만</strong> 답합니다. 자료에 없으면 &quot;확인 불가&quot;,
          찌라시는 <strong>미확인 루머</strong>로 구분해 보여줍니다. (약 5~20초)
        </p>

        <div className="flex flex-col gap-2">
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={onKeyDown}
            rows={2}
            maxLength={300}
            placeholder="예) 시총보다 수익이 많다는데 사실인가? / 요즘 무슨 찌라시 도나?"
            className="w-full resize-y rounded-lg border border-border bg-card/80 px-3 py-2 text-sm shadow-inner placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-muted-foreground">
              Enter 전송 · Shift+Enter 줄바꿈 · {question.length}/300
            </span>
            <Button
              size="sm"
              onClick={submit}
              disabled={question.trim().length < 2 || status === "loading"}
            >
              <Send aria-hidden /> {status === "loading" ? "분석 중…" : "질문하기"}
            </Button>
          </div>
        </div>

        {status === "loading" && (
          <div className="space-y-2 py-1" aria-label="답변 생성 중">
            <div className="h-4 w-48 animate-pulse rounded-md bg-white/10" />
            <div className="h-12 animate-pulse rounded-md bg-white/5" />
            <p className="text-center text-xs text-muted-foreground">
              AI가 자료를 검색·대조하는 중입니다…
            </p>
          </div>
        )}

        {status === "error" && (
          <div className="flex items-center gap-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
            <AlertTriangle className="size-4 shrink-0" aria-hidden /> {error}
          </div>
        )}

        {status === "done" && result && (
          <div className="space-y-3 border-t border-white/10 pt-3">
            <p className="text-sm font-medium leading-relaxed">{result.answer}</p>
            <p className="text-[10px] text-muted-foreground">
              ※ AI가 아래 근거를 <strong>종합·추론</strong>한 답변입니다. 각 근거의 &lsquo;원문&rsquo; 링크로 직접 확인하세요.
            </p>

            {result.calcUnverified && (
              <p className="flex items-start gap-1.5 rounded-md border border-warning/30 bg-warning/10 px-3 py-1.5 text-[11px] text-warning">
                <AlertTriangle className="mt-0.5 size-3 shrink-0" aria-hidden />
                답변 속 계산·비율 수치는 자동 검증되지 않았습니다 — 아래 &lsquo;확인된 데이터&rsquo;의 원시 수치를 기준으로 보세요.
              </p>
            )}

            {result.facts.length > 0 && (
              <div>
                <p className="mb-1 text-xs font-semibold text-muted-foreground">확인된 데이터·뉴스</p>
                <ul className="space-y-1.5">
                  {result.facts.map((f, i) => (
                    <li key={`f${i}`} className="text-xs leading-relaxed">
                      <Badge variant={LABEL_STYLE[f.label] as "neutral"} className="mr-1 align-middle">
                        {f.label}
                      </Badge>
                      {f.text}
                      <SourceLink url={f.url} />
                      {f.quote && f.label !== "데이터" && (
                        <span className="mt-0.5 block text-[11px] text-muted-foreground">
                          └ &ldquo;{f.quote}&rdquo;
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {result.rumors.length > 0 && (
              <div className="rounded-md border border-warning/30 bg-warning/5 px-3 py-2">
                <p className="mb-1 flex items-center gap-1 text-xs font-semibold text-warning">
                  <AlertTriangle className="size-3" aria-hidden /> 찌라시·루머 (미확인 — 사실 아닐 수 있음)
                </p>
                <ul className="space-y-1.5">
                  {result.rumors.map((r, i) => (
                    <li key={`r${i}`} className="text-xs leading-relaxed">
                      <Badge variant="warning" className="mr-1 align-middle">
                        {r.label}
                      </Badge>
                      {r.text}
                      <SourceLink url={r.url} />
                      {r.quote && (
                        <span className="mt-0.5 block text-[11px] text-muted-foreground">
                          └ &ldquo;{r.quote}&rdquo;{r.date ? ` · ${r.date.slice(0, 10)}` : ""}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {result.facts.length === 0 && result.rumors.length === 0 && (
              <p className="text-xs text-muted-foreground">
                수집된 자료에서 이 질문을 뒷받침할 근거를 찾지 못했습니다.
              </p>
            )}

            <p className="text-[10px] leading-relaxed text-muted-foreground">
              수집: 뉴스 {result.sourceCounts.news} · 토론방 {result.sourceCounts.board} · 텔레그램{" "}
              {result.sourceCounts.telegram}
              {result.droppedCount > 0 && ` · 원문 대조 실패로 ${result.droppedCount}건 자동 삭제(환각 차단)`}
              <br />
              {result.caveat} · {result.model} · {result.asOf}
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
