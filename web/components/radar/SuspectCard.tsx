import { Newspaper, TrendingUp } from "lucide-react";

import { cn } from "@/lib/utils";
import { Card, CardContent, CardFooter, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { SuspicionGauge } from "./SuspicionGauge";
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
  // MA20 생존 게이트가 폐지돼 ma20_margin_pct는 음수일 수 있다 → 라벨에 위/아래를 부호로 반영.
  const ma20Margin = s.pattern === "reaccum" ? s.reaccum?.ma20_margin_pct ?? null : null;
  const trendVal = ma20Margin != null ? ma20Margin : s.ma10_margin_pct;
  const trendMargin = fmtChange(trendVal);
  const trendLabel = `${ma20Margin != null ? "20일선" : "10일선"} ${trendVal >= 0 ? "위" : "아래"}`;
  const strong = s.suspicion_score >= 75 || !!s.very_good;
  const veryGoodLabel =
    s.very_good_tier === "tier2" ? "⭐ 매우좋음 Tier2" : s.very_good_tier === "tier1" ? "⭐ 매우좋음 Tier1" : "⭐ 매우좋음";

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
            {s.very_good && (
              <Badge
                className="bg-[#f59e0b] px-2.5 py-1 text-base font-black text-black"
                title="⭐ 매우좋음 — 흔들기 AND 6일 고점 대비 낙폭 −30%↑. Tier1은 −45~-30 적정 깊은눌림, Tier2는 ≤−45 과낙 구간. 최상단 승격 — 예측·매수추천 아님, 장중 익절 신호"
              >
                {veryGoodLabel}
              </Badge>
            )}
            {!s.very_good && s.very_good_candidate && (
              <Badge
                variant="outline"
                className="border-[#f59e0b]/70 px-2 py-1 font-bold text-[#fbbf24]"
                title="⭐ 매우좋음 후보 — 흔들기 AND 6일 고점 대비 낙폭 −25~-30%. 가격이 더 눌리면 매우좋음으로 재진입할 수 있으며, 일반 흔들기보다 우선 정렬됩니다. 매수 추천 아님"
              >
                ☆ 매우좋음 후보
              </Badge>
            )}
            {s.shakeout && (
              <Badge
                className="bg-up px-2.5 py-1 text-base font-black text-white"
                title="💥 흔들기 — 당일 상한권(고가 +20%↑) 터치 후 크게 밀리며(페이드 15%p↑) 유통물량 40%↑ 손바뀜, MA20 사수(경고 지정·과확장 붕괴 제외). 금호건설·동양파일 6/25 원형 — 실측 38건 익일 고가 +13% 터치 68%·평균 +18%. 예측·매수추천 아님"
              >
                💥 흔들기
              </Badge>
            )}
            {s.shakeout && s.strength && (
              <Badge
                className={
                  (s.strength_tier ?? 9) <= 0
                    ? "bg-up px-2 py-1 font-bold text-white"
                    : (s.strength_tier ?? 9) === 1
                      ? "bg-up/70 px-2 py-1 font-semibold text-white"
                      : (s.strength_tier ?? 9) === 2
                        ? "border border-warning/60 bg-transparent px-2 py-1 text-warning"
                        : "border border-white/20 bg-transparent px-2 py-1 text-muted-foreground"
                }
                title="흔들기 강도 등급 — 2일 유통회전율 스윗존(90~140%) + 고점 대비 낙폭 스윗존(−30~−45%) 결합. Tier1=둘 다 스윗(최강), Tier4=과회전/과낙 또는 얕음(약체). 종베 선택 보조·보장 아님"
              >
                {s.strength}
              </Badge>
            )}
            {s.shakeout && s.tp_hint && (
              <Badge
                variant="outline"
                className="border-up/60 text-up"
                title="회전율 밴드별 익일 고가 천장 실측(60건) 기반 익절선 힌트 — 90~120% 회전은 +12%가 천장(+13 걸면 기대값 역전). 보장 아님"
              >
                익절힌트 {s.tp_hint}
              </Badge>
            )}
            {s.alert_release && (
              <Badge
                className="bg-up px-2.5 py-1 text-base font-black text-white"
                title="KRX 투자경고 지정해제 공식(지정 후 10매매일 경과 + 5일 +45%↓ + 15일 +75%↓ + 15일 최고가 아님) 오늘 종가 기준 충족 예측 — 내일부터 해제 예상(=억눌림 해소 재료). 예측이며 KRX 최종 판단·보장 아님"
              >
                🔓 투자경고 해제 예정
              </Badge>
            )}
            {s.geupso && (
              <Badge
                className="bg-up px-2 py-0.5 text-sm font-bold text-white"
                title="🎯 매수급소 — 당일 14:30 이후 몸통 2%+ 5분 양봉 스파크 2회 이상(등락률 무관·폭발 이력 장기추적). 큰손이 아직 받치고 있다는 지문 = 식음 중 매수 시점 신호(매수 추천 아님)"
              >
                🎯 매수급소
              </Badge>
            )}
            {s.low_accum && (
              <Badge
                className="bg-orange-500 px-2 py-0.5 text-sm font-bold text-white"
                title="🧲 저점매집 의심 — 당일 −10% 이상 폭락 중인데 20일선을 사수하고 시간 무관 몸통 2%+ 5분 양봉이 3회 이상(주포가 눌러놓고 밑에서 받는 지문 — 덕신 7/3: −16%에 11시부터 4방). 매수 추천 아님"
              >
                🧲 저점매집
              </Badge>
            )}
            {s.alert_now && (
              <Badge
                className={
                  s.alert_now === "주의"
                    ? "bg-amber-500/30 px-2 py-0.5 text-sm font-bold text-amber-200"
                    : "bg-[rgba(41,98,255,0.25)] px-2 py-0.5 text-sm font-bold text-[color:var(--down,#2962FF)]"
                }
                title="KRX 시장경보 현재 지정 — 경고/위험 지정은 재상승 시 매매정지 지정 리스크가 있어 게시 순위 최후순위로 강등(회장님 지시). 주의는 표시만"
              >
                {s.alert_now === "주의" ? "⚠️투자주의" : s.alert_now === "경고" ? "🚨투자경고" : "⛔투자위험"}
              </Badge>
            )}
            {/* 재매집 게이트 설명 배지 — 흔들기 레코드는 그 게이트를 통과한 게 아니므로 미표시(리뷰 2026-07-04) */}
            {s.pattern !== "shakeout" && (
              <Badge variant="warning" title="최근 6거래일 고가+22%·거래량 90%+ 폭발 종목이 14:30~장종료 5분 양봉 몸통2%+ 스파크 2회+ AND 현재 등락률 −5~+7% 재분출 — 직접 확인하고 진입(매수 추천 아님)">
                재매집
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
            {s.turnover_pct != null && (
              <span title="당일 거래량/유통주식수 — 유통주식 대비 손바뀜 강도(높을수록 큰돈 집중)">
                {" · 회전 "}
                {s.turnover_pct}%
              </span>
            )}
          </span>
        </div>
        <h2 className="flex items-baseline gap-2 text-2xl font-bold tracking-tight">
          <span>{s.name}</span>
          <span className={`text-base font-semibold tabular-nums ${change.cls}`}>
            {change.text}
          </span>
          {s.change_basis === "NXT" && (
            <span
              className="rounded bg-warning/15 px-1.5 py-0.5 text-[10px] font-medium text-warning"
              title="정규장 마감 후 — NXT 시간외(애프터마켓) 야간가 기준 등락률"
            >
              NXT 시간외
            </span>
          )}
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
            {s.reaccum && (
              <p className="text-[11px] text-warning">
                재매집: {peakDaysAgo(s.reaccum.peak_date) ?? "-"}일 전{" "}
                고가 <span className="tabular-nums">+{s.reaccum.peak_high_pct.toFixed(1)}%</span> 폭발
                {(s.peak_turnover_pct ?? s.reaccum.peak_turnover_pct) != null && (
                  <span className="tabular-nums" title="폭발일 거래량/유통주식수 — 유통주식 손바뀜 강도">
                    {" (회전 "}
                    {s.peak_turnover_pct ?? s.reaccum.peak_turnover_pct}%{")"}
                  </span>
                )}
                {s.reaccum.peak_ibs != null && (
                  <span
                    className="tabular-nums"
                    title="폭발일 마감강도 IBS(0=저가마감·1=고가마감)·윗꼬리%. 7일 표본: 약마감(윗꼬리 큰)이 익일 연속성↑, 상한가류 강마감은 식음↑ 경향(검증 중·점수 미반영)"
                  >
                    {" · 마감 "}
                    {s.reaccum.peak_ibs >= 0.7 ? "강함" : s.reaccum.peak_ibs <= 0.4 ? "약함" : "중간"}
                    (IBS {s.reaccum.peak_ibs}
                    {s.reaccum.peak_uppertail != null && `·윗꼬리 ${s.reaccum.peak_uppertail}%`})
                  </span>
                )}
              </p>
            )}
            {s.reignition && (
              <p className="text-[11px] text-up">
                <TrendingUp className="mr-0.5 inline size-3" aria-hidden />
                오늘 5분 스파크{" "}
                <span className="tabular-nums">{s.reignition.count ?? "-"}회</span>
                {" · 최대 몸통 "}
                <span className="tabular-nums">{s.reignition.body_pct}%</span>
                {" ("}
                {s.reignition.time}
                {")"}
              </p>
            )}
            {s.geupso && (s.geupso_bars?.length ?? 0) > 0 && (
              <p className="text-[11px] font-semibold text-up tabular-nums">
                🎯 2%+ 급소 스파크: {s.geupso_bars!.map((b) => `${b.time} ${b.body_pct}%`).join(" · ")}
              </p>
            )}
            {s.low_accum && (s.low_accum_bars?.length ?? 0) > 0 && (
              <p className="text-[11px] font-semibold text-orange-400 tabular-nums">
                🧲 저점 매집봉(2%+): {s.low_accum_bars!.map((b) => `${b.time} ${b.body_pct}%`).join(" · ")}
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
            <p className="text-[11px] text-muted-foreground">
              {trendLabel} <span className={`tabular-nums ${trendMargin.cls}`}>{trendMargin.text}</span>
              {s.turnover_pct != null && (
                <>
                  {" · 당일 회전 "}
                  <span className="text-foreground/90 tabular-nums">{s.turnover_pct}%</span>
                </>
              )}
            </p>
          </div>
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
