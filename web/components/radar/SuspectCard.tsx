import { Newspaper, TrendingDown, Zap } from "lucide-react";

import { cn } from "@/lib/utils";
import { Card, CardContent, CardFooter, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { SuspicionGauge } from "./SuspicionGauge";
import { SparkTimeline } from "./SparkTimeline";
import { ScoreBreakdownBars } from "./ScoreBreakdownBars";
import { DisclaimerNote } from "./DisclaimerNote";
import type { Suspect } from "@/types/radar";

const CARD_NEWS_LIMIT = 2;

/** 등락률 표기 (한국 색 관례: 상승=빨강 up, 하락=파랑 down) */
function fmtChange(v: number) {
  const sign = v > 0 ? "+" : "";
  const cls = v > 0 ? "text-up" : v < 0 ? "text-down" : "text-muted-foreground";
  return { text: `${sign}${v.toFixed(2)}%`, cls };
}

function sentimentDotClass(sentiment?: string | null) {
  if (sentiment === "호재") return "bg-up";
  if (sentiment === "악재") return "bg-down";
  return "bg-muted-foreground/50";
}

/**
 * 수상 종목 카드 — "큰돈이 들어와 급등 후 식은, 이벤트에 민감한 종목"의 증거를 한 장에.
 * 고가→현재 페이드 바 + 분봉 스파크 타임라인 + 수급 + 점수 해부도.
 */
export function SuspectCard({ s, disclaimer }: { s: Suspect; disclaimer?: string }) {
  const change = fmtChange(s.change_pct);
  const strong = s.suspicion_score >= 75;
  // 페이드 바: 0% = 전일종가, 100% = 당일 고가. 현재 위치 = 100 - fade_pct.
  const curPos = Math.max(0, Math.min(100, 100 - s.fade_pct));
  const flowBuyDays = s.flow.net_days; // 서버(publish) 계산값 — 클라이언트 재계산 금지

  return (
    <Card
      className={cn(
        "relative h-full overflow-hidden transition-shadow",
        strong
          ? "border border-[rgba(242,54,69,0.45)] bg-gradient-to-br from-[rgba(242,54,69,0.12)] to-[rgba(255,255,255,0.03)] backdrop-blur-2xl shadow-[inset_0_1px_0_rgba(255,255,255,0.35),0_0_18px_1px_rgba(242,54,69,0.4),0_24px_55px_-22px_rgba(242,54,69,0.4)]"
          : "border border-white/10 bg-white/[0.045] backdrop-blur-md"
      )}
    >
      {strong && (
        <div
          className="pointer-events-none absolute inset-x-0 top-0 h-[3px] bg-gradient-to-r from-[#F23645] to-[rgba(242,54,69,0.25)]"
          aria-hidden
        />
      )}
      <CardHeader className="gap-3 pb-3">
        <div className="flex items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-1.5">
            {s.pattern === "shakeout" && (
              <Badge variant="warning" title="장중 고점에서 크게 눌렀다가 다시 끌어올리는 패턴">
                눌림 후 재상승
              </Badge>
            )}
            {s.sector && <Badge variant="neutral">{s.sector}</Badge>}
            {s.matched_events.slice(0, 2).map((m) => (
              <Badge key={m.id} variant="outline" className="border-up/50 text-up">
                {m.dday === 0 ? "D-DAY" : `D-${m.dday}`} {m.title.slice(0, 12)}
              </Badge>
            ))}
          </div>
          <span className="text-xs text-muted-foreground tabular-nums">
            거래대금 {s.value_eok.toLocaleString()}억
          </span>
        </div>
        <h2 className="flex items-baseline gap-2 text-2xl font-bold tracking-tight">
          <span>{s.name}</span>
          <span className={`text-base font-semibold tabular-nums ${change.cls}`}>
            {change.text}
          </span>
        </h2>
        {s.calibrated_prob?.rate != null && (
          <p className="text-[11px] text-muted-foreground">
            이 점수대의 실측 익일 상승률{" "}
            <span
              className={`font-semibold tabular-nums ${s.calibrated_prob.rate >= 50 ? "text-up" : "text-down"}`}
            >
              {s.calibrated_prob.rate}%
            </span>{" "}
            (표본 {s.calibrated_prob.n}건)
          </p>
        )}
      </CardHeader>

      <CardContent className="flex flex-col gap-4">
        <div className="flex items-center gap-5">
          <SuspicionGauge value={s.suspicion_score} size={104} />
          <div className="flex-1 space-y-3">
            {/* 고가 → 현재 페이드 바 */}
            <div>
              <div className="mb-1 flex items-center justify-between text-[11px] text-muted-foreground">
                <span className="flex items-center gap-1">
                  <TrendingDown className="size-3" aria-hidden />
                  고가 <span className="font-semibold text-up tabular-nums">+{s.high_pct.toFixed(1)}%</span>
                  {" → "}현재 후퇴 <span className="tabular-nums">{s.fade_pct.toFixed(0)}%</span>
                </span>
              </div>
              <div className="relative h-2 rounded-full bg-gradient-to-r from-white/10 to-up/50">
                <span
                  className="absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-background bg-foreground"
                  style={{ left: `${curPos}%` }}
                  title={`현재 ${change.text} (고가 상승분의 ${s.fade_pct.toFixed(0)}% 반납)`}
                />
              </div>
            </div>
            {s.shake && (
              <p className="text-[11px] text-warning">
                흔들기: {s.shake.high_time} 고점 → {s.shake.trough_time} 저점 −
                <span className="tabular-nums">{s.shake.depth_pct.toFixed(1)}%</span> 눌림 후 낙폭{" "}
                <span className="tabular-nums">{s.shake.recovery_pct}%</span> 회복
              </p>
            )}
            <p className="text-[11px] text-muted-foreground">
              10일선 위 <span className="text-foreground/90 tabular-nums">+{s.ma10_margin_pct.toFixed(1)}%</span>
              {" · "}외인·기관 순매수 <span className="text-foreground/90 tabular-nums">{flowBuyDays}/5일</span>
              {s.flow.streak >= 2 && (
                <span className="text-up"> ({s.flow.streak}일 연속)</span>
              )}
            </p>
          </div>
        </div>

        {/* 분봉 스파크 타임라인 */}
        <div className="rounded-md border border-white/10 bg-white/[0.04] p-3">
          <p className="mb-1.5 flex items-center gap-1 text-xs font-medium text-muted-foreground">
            <Zap className="size-3 text-warning" aria-hidden />
            당일 분봉 스파크{" "}
            <span className="tabular-nums">{s.spark.clusters.length}회</span>
            {s.spark.clusters[0] && (
              <span className="tabular-nums">
                · 최대 {Math.max(...s.spark.clusters.map((c) => c.vol_x))}배
              </span>
            )}
          </p>
          <SparkTimeline clusters={s.spark.clusters} />
        </div>

        {/* 점수 해부도 */}
        <ScoreBreakdownBars breakdown={s.score_breakdown} />

        {/* 관련 뉴스 */}
        {s.news.length > 0 && (
          <div className="rounded-md border border-white/10 bg-white/[0.04] p-3">
            <p className="mb-2 flex items-center gap-1 text-xs font-medium text-muted-foreground">
              <Newspaper className="size-3" aria-hidden />
              관련 뉴스 <span className="tabular-nums">{s.news.length}</span>
            </p>
            <ul className="space-y-1.5">
              {s.news.slice(0, CARD_NEWS_LIMIT).map((n, i) => (
                <li key={`${n.title}-${i}`} className="flex items-start gap-2 text-sm text-foreground/90">
                  <span
                    className={`mt-1.5 size-1.5 shrink-0 rounded-full ${sentimentDotClass(n.sentiment)}`}
                    aria-hidden
                  />
                  {n.url ? (
                    <a
                      href={n.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="line-clamp-1 hover:underline"
                    >
                      {n.title}
                    </a>
                  ) : (
                    <span className="line-clamp-1">{n.title}</span>
                  )}
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
