import { BrainCircuit, Newspaper, TrendingDown, Zap } from "lucide-react";

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

function aiBadge(v: Suspect["ai_verdict"]) {
  if (!v || v.status === "not_configured") return { label: "AI 미설정", variant: "neutral" as const };
  if (v.status === "disabled") return { label: "AI 꺼짐", variant: "neutral" as const };
  if (v.status === "outside_window") return { label: "AI 대기", variant: "neutral" as const };
  if (v.status === "unavailable") return { label: "AI 미검증", variant: "outline" as const };
  if (v.verdict === "CONFIRM") return { label: "AI 확인", variant: "outline" as const, className: "border-up/50 text-up" };
  if (v.verdict === "REJECT") return { label: "AI 경고", variant: "outline" as const, className: "border-down/50 text-down" };
  return { label: "AI 관망", variant: "warning" as const };
}

function peakDaysAgo(yyyymmdd?: string) {
  if (!yyyymmdd || yyyymmdd.length !== 8) return null;
  const y = Number(yyyymmdd.slice(0, 4));
  const m = Number(yyyymmdd.slice(4, 6));
  const d = Number(yyyymmdd.slice(6, 8));
  const peak = Date.UTC(y, m - 1, d);
  const kstNow = new Date(Date.now() + 9 * 60 * 60 * 1000);
  const today = Date.UTC(kstNow.getUTCFullYear(), kstNow.getUTCMonth(), kstNow.getUTCDate());
  return Math.max(0, Math.floor((today - peak) / 86_400_000));
}

/**
 * 수상 종목 카드 — "큰돈이 들어와 급등 후 식은, 이벤트에 민감한 종목"의 증거를 한 장에.
 * 고가→현재 페이드 바 + 분봉 스파크 타임라인 + 수급 + 점수 해부도.
 */
export function SuspectCard({ s, disclaimer }: { s: Suspect; disclaimer?: string }) {
  const change = fmtChange(s.change_pct);
  const highChange = fmtChange(s.high_pct);
  const trendMargin = fmtChange(
    s.pattern === "reaccum" && s.reaccum?.ma20_margin_pct != null
      ? s.reaccum.ma20_margin_pct
      : s.ma10_margin_pct
  );
  const trendLabel =
    s.pattern === "reaccum" && s.reaccum?.ma20_margin_pct != null ? "20일선 위" : "10일선 위";
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
            {s.prime && (
              <Badge
                className="border-transparent bg-up px-2.5 py-1 text-sm font-bold text-up-foreground"
                title="폭등 후 식음 + 분봉 스파크 + 투자자 수급 — 핵심 조건이 모두 정렬된 1순위 관찰 후보 (매수 추천 아님)"
              >
                🎯 유력
              </Badge>
            )}
            {s.pattern === "shakeout" && (
              <Badge variant="warning" title="장중 고점에서 크게 눌렀다가 다시 끌어올리는 패턴">
                눌림 후 재상승
              </Badge>
            )}
            {s.pattern === "deep_shakeout" && (
              <Badge variant="warning" title="고점 대비 급락 후 종가에 저가 방어와 흡수 흔적이 있는 패턴">
                급락 흡수
              </Badge>
            )}
            {(s.pattern === "reaccum" || s.reaccum_badge) && (
              <Badge variant="warning" title="최근 6거래일 내 1천억·고가+13% 폭발 후 식은 구간 + 투신 매집 — 15분봉 120선 재돌파를 직접 확인하고 진입(매수 추천 아님)">
                재매집 감시
              </Badge>
            )}
            {s.reaccum?.source === "telegram" && (
              <Badge
                variant="outline"
                className="border-warning/60 text-warning"
                title="텔레그램 채널 언급에서 보조 시드로 포착(랭킹 미진입) — 재료 발생을 한발 일찍 본 것일 뿐, 검증된 신호 아님"
              >
                📰 채널포착
              </Badge>
            )}
            {s.visible_experimental && (
              <Badge variant="outline" title="기존 성과·튜닝 기준선에서 분리 집계 중">
                검증중
              </Badge>
            )}
            {s.mega_flow && (
              <Badge
                variant="outline"
                className="border-up/60 font-semibold text-up"
                title="초대형 분봉 스파크에 당일 외인·기관 순매수가 동반 — 강한 회복력 가설로 최대 +12점 가점"
              >
                메가스파크 {s.spark_max_x}배 × 수급매수
              </Badge>
            )}
            {!s.visible_experimental && (() => {
              const b = aiBadge(s.ai_verdict);
              return (
                <Badge variant={b.variant} className={b.className} title={s.ai_verdict?.reason ?? undefined}>
                  {b.label}
                </Badge>
              );
            })()}
            {s.sector && <Badge variant="neutral">{s.sector}</Badge>}
            {s.theme && s.theme !== s.sector && (
              <Badge variant="outline" title="원인 테마(뉴스·업종 기반)">#{s.theme}</Badge>
            )}
            {s.theme_leader && (
              <Badge
                variant="outline"
                className="border-up/60 font-semibold text-up"
                title="같은 테마 종목 중 당일 거래대금 1위(테마 대장)"
              >
                🏆 테마 대장
              </Badge>
            )}
            {s.reaccum?.was_theme_leader && (
              <Badge
                variant="outline"
                className="border-up/70 font-semibold text-up"
                title="폭발일에 같은 업종 거래대금 1위(업종 대장)였던 종목이 식었다 재매집 — 강한 의심 신호"
              >
                🏆 예전 대장
              </Badge>
            )}
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
                  고가 <span className={`font-semibold tabular-nums ${highChange.cls}`}>{highChange.text}</span>
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
            {s.deep_shake && (
              <p className="text-[11px] text-warning">
                급락흡수: {s.deep_shake.high_time} 고점 → {s.deep_shake.low_time} 저점 −
                <span className="tabular-nums">{s.deep_shake.drop_low_from_high_pct.toFixed(1)}%</span>
                {" · "}IBS <span className="tabular-nums">{Math.round(s.deep_shake.ibs * 100)}</span>
                {" · "}회복 <span className="tabular-nums">{s.deep_shake.recovery_pct}%</span>
                {s.deep_shake.late_reclaim && <span> · 막판회복</span>}
              </p>
            )}
            {s.reaccum && (
              <p className="text-[11px] text-warning">
                재매집 감시: {peakDaysAgo(s.reaccum.peak_date) ?? "-"}일 전{" "}
                <span className="tabular-nums">{s.reaccum.peak_value_eok.toLocaleString()}억</span>
                {" · "}고가{" "}
                <span className="tabular-nums">+{s.reaccum.peak_high_pct.toFixed(1)}%</span>
                {" 폭발"}
                {s.reaccum.ivtr_days != null && (
                  <>
                    {" · 투신 "}
                    <span className="tabular-nums text-up">{s.reaccum.ivtr_days}일</span>
                    {s.reaccum.ivtr_eok != null && (
                      <span className="tabular-nums text-up"> +{s.reaccum.ivtr_eok.toLocaleString()}억</span>
                    )}
                    {" 매집"}
                  </>
                )}
              </p>
            )}
            {s.reignition && (
              <p className="text-[11px] text-up">
                오늘 재반등: {s.reignition.time} 10분봉 몸통{" "}
                <span className="tabular-nums">{s.reignition.body_pct}%</span>
                {" · "}거래대금{" "}
                <span className="tabular-nums">{s.reignition.value_10m_eok.toLocaleString()}억</span>
              </p>
            )}
            {s.reaccum?.cause_summary && (
              <p className="line-clamp-1 text-[11px] text-muted-foreground" title={s.reaccum.cause_summary}>
                왜 올랐나: {s.reaccum.cause_summary}
              </p>
            )}
            {s.forecast && (
              <p className="text-[11px] text-muted-foreground">
                📊 유사셋업 {s.forecast.horizon} 과거{" "}
                <span className={`font-semibold tabular-nums ${s.forecast.strong ? "text-up" : "text-foreground"}`}>
                  ~{s.forecast.prob_pct}%
                </span>
                {s.forecast.strong && " (강 모멘텀)"}
                {" · 내일1일 +7%는 ~"}
                <span className="tabular-nums">{s.forecast.next_day_7_pct}%</span>
                {" · 코호트 통계·보장 아님"}
              </p>
            )}
            {s.leader_cohort_prob?.rate != null && (
              <p className="text-[11px] text-muted-foreground">
                🏆 예전 대장 재매집 코호트 실측 익일 상승{" "}
                <span
                  className={`font-semibold tabular-nums ${s.leader_cohort_prob.rate >= 50 ? "text-up" : "text-down"}`}
                >
                  {s.leader_cohort_prob.rate}%
                </span>{" "}
                (표본 {s.leader_cohort_prob.n}건) · 코호트 통계·보장 아님
              </p>
            )}
            {s.ai_verdict?.status === "ok" && s.ai_verdict.reason && (
              <p className="flex items-start gap-1 text-[11px] text-muted-foreground">
                <BrainCircuit className="mt-0.5 size-3 shrink-0" aria-hidden />
                <span>
                  Kimi {s.ai_verdict.confidence ?? 0}% · {s.ai_verdict.reason}
                </span>
              </p>
            )}
            <p className="text-[11px] text-muted-foreground">
              {trendLabel} <span className={`tabular-nums ${trendMargin.cls}`}>{trendMargin.text}</span>
              {" · "}외인·기관 순매수 <span className="text-foreground/90 tabular-nums">{flowBuyDays}/5일</span>
              {s.flow.streak >= 2 && (
                <span className="text-up"> ({s.flow.streak}일 연속)</span>
              )}
            </p>
          </div>
        </div>

        {/* 분봉 스파크 타임라인 (구 fade 카드 전용 — reaccum은 클러스터 없음) */}
        {s.spark.clusters.length > 0 && (
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
        )}

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
