import { Newspaper, TrendingUp } from "lucide-react";

import { cn } from "@/lib/utils";
import { Card, CardContent, CardFooter, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { SuspicionGauge } from "./SuspicionGauge";
import { ScoreBreakdownBars } from "./ScoreBreakdownBars";
import { DisclaimerNote } from "./DisclaimerNote";
import type { NextMarketAlertPreview, NextSessionEligibility, Suspect } from "@/types/radar";

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

// 재료 배지 — 등급별 파스텔 밝은색 + 3D 질감(그라데이션·상단 하이라이트·그림자).
// 다크 대시보드에서 유일하게 밝은 면 배지라 한눈에 띈다. D는 한국 관례(하락=파랑)상 차가운 톤.
function materialBadgeClass(grade?: string) {
  const base =
    "border font-bold rounded-md bg-gradient-to-b " +
    "shadow-[inset_0_1px_0_rgba(255,255,255,0.55),inset_0_-1px_0_rgba(0,0,0,0.15),0_2px_4px_rgba(0,0,0,0.45)] " +
    "[text-shadow:0_1px_0_rgba(255,255,255,0.35)]";
  if (grade === "S") return cn(base, "from-[#ffe9a8] to-[#f3c469] border-[#d9a83f] text-[#6b4400]"); // 파스텔 골드
  if (grade === "A") return cn(base, "from-[#c9f5dc] to-[#8fdfb2] border-[#6fc496] text-[#0c5232]"); // 파스텔 민트
  if (grade === "B") return cn(base, "from-[#d3e9ff] to-[#97c8f2] border-[#7fb3e0] text-[#123f66]"); // 파스텔 스카이
  if (grade === "C") return cn(base, "from-[#ececf2] to-[#c9c9d6] border-[#b3b3c2] text-[#3f3f4e]"); // 파스텔 그레이
  if (grade === "D") return cn(base, "from-[#ded9f0] to-[#b0a6d8] border-[#9a8ec6] text-[#332a58]"); // 파스텔 라벤더(악재·차가운 톤)
  return "border-white/10 bg-transparent text-muted-foreground";
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

function signedStat(value?: number | null) {
  if (value == null) return null;
  return `${value > 0 ? "+" : ""}${value}%`;
}

function priorLabel(source: string) {
  const labels: Record<string, string> = {
    chairman_40y_rule: "회장님 40년 경험칙",
    census_140k: "14만건 전수조사",
    agreed_rule: "합의 규칙",
    fallback: "기본 규칙",
  };
  return labels[source] ?? source;
}

function alertReleaseTitle(s: Suspect) {
  const rule = s.alert_release_rule;
  const checks = s.alert_release_checks;
  if (!rule || rule.parse_status !== "ok") {
    return "KRX 투자경고 해제요건을 오늘 현재가에 적용한 내일 해제 예상입니다. 예측이며 KRX 최종 공시가 우선합니다.";
  }
  const elapsed = rule.min_elapsed_days ?? 10;
  const five = rule.threshold_5d_pct;
  const fifteen = rule.threshold_15d_pct;
  const highWindow = rule.recent_high_window ?? 15;
  const haltCount = checks?.halt_days_excluded?.length ?? 0;
  return [
    `KRX/KOSCOM 종목별 공시 기준: 실제 매매일 ${elapsed}일 경과`,
    five != null ? `T-5 대비 +${five}% 이상 상승하지 않음` : null,
    fifteen != null ? `T-15 대비 +${fifteen}% 이상 상승하지 않음` : null,
    `최근 ${highWindow}매매일 최고 종가가 아님`,
    haltCount ? `거래정지 ${haltCount}일 제외` : null,
    "오늘 현재가를 가상 종가로 계산한 내일 해제 예상이며 KRX 최종 공시가 우선합니다.",
  ].filter(Boolean).join(" · ");
}

function rankStatsLine(label: string, stats: NonNullable<Suspect["rank_retro_stats"]>) {
  const values = [
    stats.touch7_rate != null ? `+7% ${stats.touch7_rate}%` : null,
    signedStat(stats.avg_high_pct) ? `평균 ${signedStat(stats.avg_high_pct)}` : null,
    signedStat(stats.median_high_pct) ? `중앙 ${signedStat(stats.median_high_pct)}` : null,
    signedStat(stats.min_high_pct) ? `최저 ${signedStat(stats.min_high_pct)}` : null,
  ].filter(Boolean);
  return `${label} n=${stats.n}${stats.unique_n != null ? `/${stats.unique_n}` : ""}${
    values.length ? ` · ${values.join(" · ")}` : ""
  }${stats.valid === false ? " · 수집 중" : ""}`;
}

type ShakeoutBadgeMeta = {
  label: string;
  title: string;
  variant: "outline" | "warning" | undefined;
  className: string;
};

/**
 * 흔들기 강·약 표시의 단일 판정점.
 * very_good·조합D 단독·약한흔들기를 서로 겹치지 않게 표시하고,
 * 구버전/결측 데이터는 강·약으로 추정하지 않는다.
 */
function shakeoutBadgeMeta(s: Pick<Suspect, "shakeout" | "strength_tier" | "very_good">): ShakeoutBadgeMeta | null {
  if (!s.shakeout) return null;
  if (s.very_good === true) {
    return {
      label: "💥 강한흔들기",
      title:
        "강한흔들기 — 매우좋음 기준(dd6 ≤ -30%)을 충족한 흔들기입니다. 조합D 단독과는 별도 분류이며, 수익·체결을 보장하거나 자동매수를 지시하지 않습니다.",
      variant: undefined,
      className: "bg-up px-2.5 py-1 text-base font-black text-white",
    };
  }
  if (s.strength_tier == null) {
    return {
      label: "💥 흔들기 · 강도 미확인",
      title:
        "흔들기 조건은 충족했지만 강도 결합축이 저장되지 않은 데이터입니다. 강한흔들기나 약한흔들기로 추정하지 않습니다.",
      variant: "outline",
      className: "border-white/30 px-2.5 py-1 text-base font-bold text-muted-foreground",
    };
  }
  if (s.strength_tier >= 3) {
    return {
      label: "💥 흔들기 · 조합D 단독",
      title:
        "조합D 단독 — strength_tier ≥ 3이지만 very_good은 아닌 별도 관찰군입니다. strength_tier는 강도점수가 아니라 2일 유통회전율과 고점 대비 낙폭의 결합축이며, 과열 조합도 포함될 수 있습니다. 강한흔들기나 매수 신호를 의미하지 않습니다.",
      variant: "warning",
      className: "border border-warning/60 px-2.5 py-1 text-base font-black",
    };
  }
  return {
    label: "〰️ 약한흔들기",
    title:
      "약한흔들기 — 기본 흔들기 조건은 충족했지만 기존 rank4 조합D 기준(strength_tier ≥ 3)에는 들지 않은 조합A~C입니다. 상대 구분이며 익일 상승이 불가능하다는 뜻은 아닙니다.",
    variant: "warning",
    className: "border border-warning/60 px-2.5 py-1 text-base font-bold",
  };
}

function alertPreviewMeta(preview?: NextMarketAlertPreview) {
  if (!preview) return null;
  const expiresAt = Date.parse(preview.expires_at);
  if (!Number.isFinite(expiresAt) || expiresAt <= Date.now()) return null;
  const commonTitle = `${preview.reason} · 기준가 ${preview.price?.toLocaleString("ko-KR") ?? "미확인"}원 · ${preview.generated_at} · KRX 최종 공시가 우선합니다.`;
  if (preview.status === "CONDITION_MET_CLOSE" || preview.status === "CONDITION_MET_INTRADAY") {
    return {
      label: "🚨 투자경고 예정",
      sublabel: preview.status === "CONDITION_MET_CLOSE" ? "종가조건 충족" : "현재가 기준",
      className: "bg-up px-2.5 py-1 text-base font-black text-white",
      title: commonTitle,
    };
  }
  return null;
}

function eligibilityBadgeMeta(eligibility?: NextSessionEligibility | null) {
  if (!eligibility) return null;
  const dateLabel = (value?: string | null) =>
    value && /^\d{8}$/.test(value)
      ? `${value.slice(0, 4)}.${value.slice(4, 6)}.${value.slice(6, 8)}`
      : value;
  const restriction =
    eligibility.restriction_start &&
    eligibility.restriction_end &&
    eligibility.restriction_start !== eligibility.restriction_end
      ? `${dateLabel(eligibility.restriction_start)}~${dateLabel(eligibility.restriction_end)}`
      : dateLabel(eligibility.restriction_start);
  const title = [
    eligibility.target_trade_date
      ? `목표 거래일 ${dateLabel(eligibility.target_trade_date)}`
      : null,
    restriction ? `정지기간 ${restriction}` : null,
    eligibility.evidence?.title ? `공시 ${eligibility.evidence.title}` : null,
    eligibility.checked_at ? `확인 ${eligibility.checked_at}` : null,
    "KRX/KOSCOM 확정 공시가 최우선입니다.",
  ].filter(Boolean).join(" · ");

  if (eligibility.status === "HALT_CONFIRMED") {
    return {
      label: "⛔ 다음 거래일 거래정지 예정 · 확정공시",
      className: "bg-up px-2.5 py-1 text-base font-black text-white",
      title,
    };
  }
  if (eligibility.status === "CURRENTLY_HALTED") {
    return {
      label: "⛔ 현재 거래정지",
      className: "bg-up px-2.5 py-1 text-base font-black text-white",
      title,
    };
  }
  if (eligibility.status === "NOTICE_ONLY") {
    return {
      label: "⚠ 거래정지 예고 · 미확정",
      className: "bg-warning px-2.5 py-1 text-base font-black text-black",
      title,
    };
  }
  return null;
}

/**
 * 수상 종목 카드 — "큰돈이 들어와 급등 후 식은, 이벤트에 민감한 종목"의 증거를 한 장에.
 * 고가→현재 페이드 바 + 분봉 스파크 타임라인 + 수급 + 점수 해부도.
 */
export function SuspectCard({
  s,
  disclaimer,
}: {
  s: Suspect;
  disclaimer?: string;
}) {
  const change = fmtChange(s.change_pct);
  // MA20 생존 게이트가 폐지돼 ma20_margin_pct는 음수일 수 있다 → 라벨에 위/아래를 부호로 반영.
  const ma20Margin = s.pattern === "reaccum" ? s.reaccum?.ma20_margin_pct ?? null : null;
  const trendVal = ma20Margin != null ? ma20Margin : s.ma10_margin_pct;
  const trendMargin = fmtChange(trendVal);
  const trendLabel = `${ma20Margin != null ? "20일선" : "10일선"} ${trendVal >= 0 ? "위" : "아래"}`;
  const strong = s.suspicion_score >= 75 || !!s.very_good;
  const veryGoodLabel =
    s.very_good_tier === "tier2" ? "⭐ 매우좋음 Tier2" : s.very_good_tier === "tier1" ? "⭐ 매우좋음 Tier1" : "⭐ 매우좋음";
  const material = s.material;
  const materialGrade = material?.grade;
  const showMaterial = !!materialGrade && materialGrade !== "N";
  const priorSnapshot = s.rank_bucket_stats_snapshot;
  const veryGoodComboD =
    s.shakeout === true &&
    s.very_good === true &&
    s.very_good_tier != null &&
    s.strength_tier != null &&
    s.strength_tier >= 3;
  const sampleCaution = veryGoodComboD || (priorSnapshot?.n != null
    ? priorSnapshot.n < 10
    : s.rank_bucket === 1 || s.rank_bucket === 4);
  const topCut =
    (s.run_6d_pct ?? 0) >= 30 ||
    ((materialGrade === "C" || materialGrade === "N") && (s.turnover_pct ?? 0) >= 90);
  const highRiskMomentum = s.alert_now === "경고" || s.alert_now === "위험";
  const comboDOnly =
    s.shakeout === true &&
    s.very_good !== true &&
    s.strength_tier != null &&
    s.strength_tier >= 3;
  const turnover2dPct = s.turnover_2d_pct ?? null;
  const turnover2dOverheated =
    s.shakeout === true &&
    turnover2dPct != null &&
    turnover2dPct > 180;
  const shakeoutBadge = shakeoutBadgeMeta(s);
  const previewBadge = alertPreviewMeta(s.next_market_alert_preview ?? undefined);
  const eligibilityBadge = eligibilityBadgeMeta(s.next_session_eligibility);

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
            {eligibilityBadge && (
              <Badge className={eligibilityBadge.className} title={eligibilityBadge.title}>
                {eligibilityBadge.label}
              </Badge>
            )}
            {previewBadge && (
              <span className="inline-flex flex-col items-start gap-0.5">
                <Badge className={previewBadge.className} title={previewBadge.title}>
                  {previewBadge.label}
                </Badge>
                <span className="pl-1 text-[10px] font-medium text-muted-foreground">
                  {previewBadge.sublabel}
                </span>
              </span>
            )}
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
                title="⭐ 매우좋음 후보 — 흔들기 AND 6일 고점 대비 낙폭 −25~-30%. 승격키는 제거했고 배지·전진검증만 유지합니다. 매수 추천 아님"
              >
                ☆ 매우좋음 후보
              </Badge>
            )}
            {shakeoutBadge && (
              <Badge
                variant={shakeoutBadge.variant}
                className={shakeoutBadge.className}
                title={shakeoutBadge.title}
              >
                {shakeoutBadge.label}
              </Badge>
            )}
            {turnover2dOverheated && turnover2dPct != null && (
              <Badge
                variant="warning"
                className="border border-warning/70 px-2.5 py-1 text-base font-black"
                title="신호일과 전일의 유통주식수 대비 거래량 합계가 180%를 초과했습니다. 손바뀜이 매우 큰 구간으로 재상승과 물량소진이 모두 가능한 위험 관찰값입니다. 강한흔들기 판정이나 매수 신호가 아닙니다."
              >
                ⚠ 2일 회전 극과열 {turnover2dPct.toFixed(1)}%
              </Badge>
            )}
            {s.shakeout && s.strength && !comboDOnly && (
              <Badge
                className={
                  s.strength_tier != null && s.strength_tier >= 3
                    ? "bg-up px-2 py-1 font-bold text-white"
                    : (s.strength_tier ?? 9) === 2
                      ? "border border-warning/60 bg-transparent px-2 py-1 text-warning"
                      : "border border-white/20 bg-transparent px-2 py-1 text-muted-foreground"
                }
                title="흔들기 결합축 — 2일 유통회전율과 60일 고점 대비 낙폭의 조합입니다. 최근 실측상 조합D는 익일 고가 터치가 강하게 나와 통계상 고가강으로 표시합니다. 정렬·자동매매 기준 아님, 보장 아님"
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
                title={alertReleaseTitle(s)}
              >
                🔓 투자경고 해제 예정
              </Badge>
            )}
            {s.alert_risk_released && (
              <Badge
                className="bg-up px-2.5 py-1 text-base font-black text-white"
                title="투자위험종목 지정해제 공시 3일 내(위험→경고 강등 직후) — 최고 단계 규제가 방금 풀린 종목. rank4 순위는 별도 정책이며 배지·전진검증용입니다. 매수 추천 아님"
              >
                🔓 투자위험 해제 직후
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
                title="KRX 시장경보 현재 지정 — 정렬은 직접 바꾸지 않고 고위험 고탄력 배지로 격리합니다. 경고/위험은 재상승 시 매매정지 지정 리스크가 있습니다"
              >
                {s.alert_now === "주의" ? "⚠️투자주의" : s.alert_now === "경고" ? "🚨투자경고" : "⛔투자위험"}
              </Badge>
            )}
            {s.rank_bucket != null && (
              <Badge
                variant="outline"
                className="border-white/25 text-muted-foreground"
                title={s.rank_reason ?? "정렬4 rank_bucket"}
              >
                B{s.rank_bucket}
              </Badge>
            )}
            {s.rank_bucket === 1 && (
              <Badge
                className="bg-up px-2 py-0.5 text-sm font-bold text-white"
                title="급소+폭발일 회전율 150% 이상 — 소표본 전승 조합. kill switch 적용 대상"
              >
                폭발형
              </Badge>
            )}
            {sampleCaution && (
              <Badge
                variant="outline"
                className="border-warning/70 text-warning"
                title="근거 표본 n<10인 상위 버킷입니다. 정렬에는 반영하지만 전진검증과 kill switch를 같이 봅니다"
              >
                표본주의
              </Badge>
            )}
            {topCut && (
              <Badge
                variant="outline"
                className="border-warning/60 text-warning"
                title="최근 단기 급등 또는 재료 대비 고회전으로 상단 여지가 제한될 수 있는 관찰 배지입니다. 정렬 bucket은 바꾸지 않습니다"
              >
                상단컷
              </Badge>
            )}
            {highRiskMomentum && (
              <Badge
                variant="outline"
                className="border-[color:var(--down,#2962FF)]/60 text-[color:var(--down,#2962FF)]"
                title="투자경고/위험 지정 상태의 고탄력 종목입니다. 통계 승격 조건이 아니라 리스크 배지입니다"
              >
                고위험 고탄력
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
            {showMaterial && (
              <Badge
                variant="outline"
                className={cn("font-semibold", materialBadgeClass(materialGrade))}
                title={`뉴스/공시 재료 전진검증 등급 — 점수·정렬·자동매매 미반영. 신뢰도 ${material?.reliability ?? "-"} · 직접성 ${material?.directness ?? "-"} · 신선도 ${material?.freshness ?? "-"}${material?.risk_flags?.length ? ` · 리스크 ${material.risk_flags.join(", ")}` : ""}`}
              >
                재료 {materialGrade}
              </Badge>
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
        {(s.rank_reason || priorSnapshot || s.rank_retro_stats || s.rank_forward_stats) && (
          <div className="space-y-0.5 text-[11px] text-muted-foreground">
            <p>
              정렬근거: <span className="font-medium text-foreground/80">{s.rank_reason ?? `bucket ${s.rank_bucket ?? "—"}`}</span>
              {s.rank_prior?.source && <span>{` · ${priorLabel(s.rank_prior.source)} prior`}</span>}
            </p>
            {priorSnapshot && (
              <p className="tabular-nums">
                참고 스냅샷
                {priorSnapshot.population ? ` (${priorSnapshot.population})` : ""}
                {priorSnapshot.n != null ? ` · n=${priorSnapshot.n}/${priorSnapshot.unique_n ?? priorSnapshot.n}` : ""}
                {priorSnapshot.touch7_rate != null ? ` · +7% ${priorSnapshot.touch7_rate}%` : ""}
                {(priorSnapshot.avg_high_pct ?? priorSnapshot.expected_high_pct) != null
                  ? ` · 평균 ${signedStat(priorSnapshot.avg_high_pct ?? priorSnapshot.expected_high_pct)}`
                  : ""}
                {priorSnapshot.median_high_pct != null
                  ? ` · 중앙 ${signedStat(priorSnapshot.median_high_pct)}`
                  : ""}
                {priorSnapshot.min_high_pct != null
                  ? ` · 최저 ${signedStat(priorSnapshot.min_high_pct)}`
                  : ""}
              </p>
            )}
            {!priorSnapshot && (s.expected_touch7_rate != null || s.expected_high_pct != null) && (
              <p className="tabular-nums">
                기존 참고값
                {s.expected_touch7_rate != null ? ` · +7% ${s.expected_touch7_rate}%` : ""}
                {s.expected_high_pct != null ? ` · 평균 ${signedStat(s.expected_high_pct)}` : ""}
              </p>
            )}
            {s.rank_retro_stats && (
              <p className="tabular-nums">{rankStatsLine("소급", s.rank_retro_stats)}</p>
            )}
            {s.rank_forward_stats && (
              <p className="tabular-nums">{rankStatsLine("전진", s.rank_forward_stats)}</p>
            )}
          </div>
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
            {showMaterial && material.summary && (
              <p className="line-clamp-1 text-[11px] text-muted-foreground" title={material.summary}>
                재료: {material.summary}
                {material.directness && ` · ${material.directness}`}
                {material.reliability && ` · ${material.reliability}`}
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
