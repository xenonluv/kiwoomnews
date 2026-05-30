import { Clock, Newspaper } from "lucide-react";

import { cn } from "@/lib/utils";
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
import type { MarketStatus, SignalPost, SignalTier } from "@/types/signal";

/** 카드에 간략 노출할 뉴스 (제목 + 감성). 링크는 상세 NewsList에서 제공. */
export interface CardNewsItem {
  title: string;
  sentiment?: string | null; // 호재 | 악재 | 중립 등
}

const CARD_NEWS_LIMIT = 2;

/** SignalPost(API/스키마) → SignalCard props 매핑 */
export function toSignalCardProps(s: SignalPost): SignalCardProps {
  const news = (s.news ?? []).filter((n) => n.title);
  return {
    tier: s.tier,
    targetStock: s.target_stock,
    dayChange: s.day_change ?? null,
    signalProbability: s.signal_probability,
    positionType: s.position_type,
    headline: s.headline,
    summary: s.summary,
    disclaimer: s.disclaimer,
    publishedAt: s.published_at,
    newsCount: news.length,
    news: news.slice(0, CARD_NEWS_LIMIT).map((n) => ({
      title: n.title,
      sentiment: n.sentiment,
    })),
  };
}

export interface SignalCardProps {
  tier?: SignalTier; // signal=글래스(강조) / candidate=무광
  targetStock: string;
  dayChange?: number | null; // 당일 등락률(%)
  signalProbability: string; // "45%"
  positionType: MarketStatus; // 저점 | 눌림목 | 과다상승 | 분석불가
  headline: string;
  summary: string;
  disclaimer?: string;
  publishedAt: string;
  newsCount?: number; // 관련 뉴스 총 건수
  news?: CardNewsItem[]; // 카드에 간략 노출(상위 2건). 비링크 텍스트.
}

/** 감성 → 점 색 (한국 색 관례: 호재=빨강, 악재=파랑, 그 외=뉴트럴) */
function sentimentDotClass(sentiment?: string | null) {
  if (sentiment === "호재") return "bg-up";
  if (sentiment === "악재") return "bg-down";
  return "bg-muted-foreground/50";
}

/**
 * 매매 시그널 게시 카드 — 다크 금융 대시보드.
 * 디자이너 SSOT 컴포넌트. 팀원5는 승인 데이터를 props로 바인딩만 한다.
 */
/** 당일 등락률 표기 (한국 색 관례: 상승=빨강 up, 하락=파랑 down) */
function fmtChange(v?: number | null) {
  if (v === null || v === undefined) return null;
  const sign = v > 0 ? "+" : "";
  const cls = v > 0 ? "text-up" : v < 0 ? "text-down" : "text-muted-foreground";
  return { text: `${sign}${v.toFixed(2)}%`, cls };
}

export function SignalCard({
  tier,
  targetStock,
  dayChange,
  signalProbability,
  positionType,
  headline,
  summary,
  disclaimer,
  publishedAt,
  newsCount = 0,
  news = [],
}: SignalCardProps) {
  const isSignal = tier !== "candidate";
  return (
    <Card
      className={cn(
        "relative overflow-hidden h-full transition-shadow",
        isSignal
          ? // 시그널: 세련된 블루 서리유리(backdrop-blur로 뒤 색블롭 굴절) + 블루 오라 + 상단 하이라이트
            "border border-[rgba(125,176,255,0.32)] bg-gradient-to-br from-[rgba(59,130,246,0.15)] to-[rgba(255,255,255,0.04)] backdrop-blur-2xl backdrop-saturate-150 shadow-[inset_0_1px_0_rgba(255,255,255,0.35),0_0_0_1px_rgba(59,130,246,0.32),0_28px_60px_-20px_rgba(59,130,246,0.55)] hover:shadow-[inset_0_1px_0_rgba(255,255,255,0.45),0_0_0_1px_rgba(59,130,246,0.5),0_34px_72px_-18px_rgba(59,130,246,0.7)]"
          : // 후보: 무광 뉴트럴 평면 (블루·발광 없음 → 시그널과 확실히 구분)
            "border border-white/10 bg-white/[0.045] backdrop-blur-md shadow-none"
      )}
    >
      {isSignal && (
        <div
          className="pointer-events-none absolute inset-x-0 top-0 h-[3px] bg-gradient-to-r from-[#3b82f6] to-[rgba(59,130,246,0.3)]"
          aria-hidden
        />
      )}
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
        <h2 className="flex items-baseline gap-2 text-2xl font-bold tracking-tight text-foreground">
          <span>{targetStock}</span>
          {(() => {
            const c = fmtChange(dayChange);
            return c ? (
              <span className={`text-base font-semibold tabular-nums ${c.cls}`}>
                {c.text}
              </span>
            ) : null;
          })()}
        </h2>
        <p className="text-sm font-medium text-muted-foreground">{headline}</p>
      </CardHeader>

      <CardContent className="flex flex-col gap-4">
        <div className="flex items-center gap-5">
          <ProbabilityGauge value={signalProbability} size={108} />
          <p className="flex-1 text-sm leading-relaxed text-foreground/90">
            {summary}
          </p>
        </div>

        {news.length > 0 && (
          <div className="rounded-md border border-white/10 bg-white/[0.04] p-3">
            <p className="mb-2 flex items-center gap-1 text-xs font-medium text-muted-foreground">
              <Newspaper className="size-3" aria-hidden />
              관련 뉴스 <span className="tabular-nums">{newsCount}</span>
            </p>
            <ul className="space-y-1.5">
              {news.map((n, i) => (
                <li
                  key={`${n.title}-${i}`}
                  className="flex items-start gap-2 text-sm text-foreground/90"
                >
                  <span
                    className={`mt-1.5 size-1.5 shrink-0 rounded-full ${sentimentDotClass(n.sentiment)}`}
                    aria-hidden
                  />
                  <span className="line-clamp-1">{n.title}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>

      <CardFooter className="border-t border-white/10 pt-4">
        <DisclaimerNote text={disclaimer} />
      </CardFooter>
    </Card>
  );
}
