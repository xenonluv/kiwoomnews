"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { stockClientService } from "@/services/stock.client";
import type { StockReport } from "@/types/stock";
import { PriceSummaryCard } from "./PriceSummaryCard";
import { PriceChart } from "./PriceChart";
import { TechnicalCard } from "./TechnicalCard";
import { FlowCard } from "./FlowCard";
import { SparkCard } from "./SparkCard";
import { FinancialsCard } from "./FinancialsCard";
import { NewsCard } from "./NewsCard";
import { EventsCard } from "./EventsCard";
import { VerdictCard } from "./VerdictCard";
import { AiAnalysisCard } from "./AiAnalysisCard";
import { TrackButton } from "./TrackButton";

function Skeleton() {
  return (
    <div className="space-y-4" aria-label="리포트 생성 중">
      <div className="h-8 w-56 animate-pulse rounded-md bg-white/10" />
      <div className="h-44 animate-pulse rounded-lg bg-white/5" />
      <div className="h-72 animate-pulse rounded-lg bg-white/5" />
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="h-52 animate-pulse rounded-lg bg-white/5" />
        <div className="h-52 animate-pulse rounded-lg bg-white/5" />
      </div>
      <p className="text-center text-xs text-muted-foreground">
        네이버 공개 데이터를 모아 분석하는 중입니다… (수 초 소요)
      </p>
    </div>
  );
}

/** 종목 분석 리포트 컨테이너 — /api/stock/[code] 1회 호출 후 섹션 렌더. */
export function StockReportView({ code }: { code: string }) {
  const [report, setReport] = useState<StockReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);

  // code가 바뀌면 이전 요청 응답은 무시(stale 가드) — 늦게 도착한 다른 종목
  // 리포트가 현재 화면을 덮어쓰는 경합을 차단한다.
  useEffect(() => {
    let stale = false;
    setError(null);
    setReport(null);
    stockClientService
      .getReport(code)
      .then((r) => {
        if (!stale) setReport(r);
      })
      .catch((e) => {
        if (!stale) setError(e instanceof Error ? e.message : "리포트 조회에 실패했습니다.");
      });
    return () => {
      stale = true;
    };
  }, [code, retryKey]);

  if (error) {
    return (
      <div className="flex min-h-48 flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-border text-sm text-muted-foreground">
        <AlertTriangle className="size-6 text-warning" aria-hidden />
        <p>{error}</p>
        <Button variant="outline" size="sm" onClick={() => setRetryKey((k) => k + 1)}>
          <RotateCcw aria-hidden /> 다시 시도
        </Button>
      </div>
    );
  }
  if (!report) return <Skeleton />;

  const r = report;
  return (
    <div className="space-y-4">
      <header className="space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-2xl font-bold tracking-tight">{r.name}</h1>
          <span className="text-sm tabular-nums text-muted-foreground">{r.code}</span>
          {r.market && <Badge variant="secondary">{r.market}</Badge>}
          {r.isEtf && <Badge variant="neutral">ETF·ETN</Badge>}
          {r.marketAlert && (
            <Badge
              variant={r.marketAlert.level === "주의" ? "warning" : "up"}
              className={
                r.marketAlert.level === "위험" ? "bg-up text-primary-foreground" : undefined
              }
              title="한국거래소 시장경보 지정 종목"
            >
              ⚠ {r.marketAlert.label}
            </Badge>
          )}
          {r.isManagement && (
            <Badge
              variant="up"
              className="bg-up text-primary-foreground"
              title="상장폐지 사유 발생 등으로 거래소가 지정한 관리종목"
            >
              ⚠ 관리종목
            </Badge>
          )}
          {r.tradeStop && <Badge variant="warning">거래정지</Badge>}
          {r.marketStatus === "OPEN" && <Badge variant="up">장중</Badge>}
          <span className="ml-auto">
            <TrackButton code={r.code} />
          </span>
        </div>
        <p className="text-xs text-muted-foreground">기준 {r.asOf} · 네이버 공개 데이터</p>
      </header>

      {r.warnings.length > 0 && (
        <ul className="space-y-0.5 rounded-md border border-white/10 bg-white/[0.03] px-3 py-2 text-[11px] text-muted-foreground">
          {r.warnings.map((w) => (
            <li key={w}>· {w}</li>
          ))}
        </ul>
      )}

      {r.verdict && <VerdictCard verdict={r.verdict} disclaimer={r.disclaimer} />}
      {/* key=code: 종목 전환 시 강제 리마운트 — 이전 종목의 늦은 AI 응답이 새 화면을 덮어쓰는 것 차단 */}
      {r.verdict && !r.tradeStop && <AiAnalysisCard key={r.code} code={r.code} />}
      {r.price && <PriceSummaryCard price={r.price} />}
      {r.chart && <PriceChart candles={r.chart.candles} />}

      <div className="grid items-start gap-4 lg:grid-cols-2">
        {r.technical && <TechnicalCard technical={r.technical} />}
        {r.flow && <FlowCard flow={r.flow} />}
        {r.spark && <SparkCard spark={r.spark} />}
        {r.financials && <FinancialsCard financials={r.financials} researches={r.researches} />}
        {r.events && <EventsCard events={r.events} />}
      </div>

      {r.news && <NewsCard news={r.news} />}
    </div>
  );
}
