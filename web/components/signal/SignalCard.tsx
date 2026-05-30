import { Clock, Newspaper } from "lucide-react";

import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { MarketStatusBadge } from "./MarketStatusBadge";
import { ProbabilityGauge } from "./ProbabilityGauge";
import { DisclaimerNote } from "./DisclaimerNote";
import type { MarketStatus, SignalPost } from "@/types/signal";

/** SignalPost(API/스키마) → SignalCard props 매핑 */
export function toSignalCardProps(s: SignalPost): SignalCardProps {
  return {
    targetStock: s.target_stock,
    signalProbability: s.signal_probability,
    positionType: s.position_type,
    headline: s.headline,
    summary: s.summary,
    disclaimer: s.disclaimer,
    publishedAt: s.published_at,
    newsCount: s.news?.length ?? 0,
  };
}

export interface SignalCardProps {
  targetStock: string;
  signalProbability: string; // "45%"
  positionType: MarketStatus; // 저점 | 눌림목 | 과다상승 | 분석불가
  headline: string;
  summary: string;
  disclaimer?: string;
  publishedAt: string;
  newsCount?: number; // 관련 뉴스 건수 (카드 클릭 시 상세에서 노출)
}

/**
 * 매매 시그널 게시 카드 — 다크 금융 대시보드.
 * 디자이너 SSOT 컴포넌트. 팀원5는 승인 데이터를 props로 바인딩만 한다.
 */
export function SignalCard({
  targetStock,
  signalProbability,
  positionType,
  headline,
  summary,
  disclaimer,
  publishedAt,
  newsCount = 0,
}: SignalCardProps) {
  return (
    <Card className="overflow-hidden">
      <CardHeader className="gap-3 pb-4">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Badge variant="neutral">분석</Badge>
            <MarketStatusBadge status={positionType} />
          </div>
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            <Clock className="size-3" aria-hidden />
            <time dateTime={publishedAt} className="tabular-nums">
              {publishedAt}
            </time>
          </span>
        </div>
        <h2 className="text-2xl font-bold tracking-tight text-foreground">
          {targetStock}
        </h2>
        <p className="text-sm font-medium text-muted-foreground">{headline}</p>
        {newsCount > 0 && (
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            <Newspaper className="size-3" aria-hidden />
            관련 뉴스 <span className="tabular-nums">{newsCount}</span>건
          </span>
        )}
      </CardHeader>

      <CardContent className="flex items-center gap-5">
        <ProbabilityGauge value={signalProbability} size={108} />
        <p className="flex-1 text-sm leading-relaxed text-foreground/90">
          {summary}
        </p>
      </CardContent>

      <CardFooter className="border-t border-border pt-4">
        <DisclaimerNote text={disclaimer} />
      </CardFooter>
    </Card>
  );
}
