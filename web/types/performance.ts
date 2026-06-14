// 레이더 성과 검증 데이터 — scripts/radar_backtest.py가 생성하는 web/data/performance.json과 정합.

export interface PerfPoint {
  date: string; // YYYYMMDD
  n: number; // 해당일 평가 표본 수
  hits: number;
  hit_rate: number | null; // 해당일 적중률(%)
  cum_n: number;
  cum_hit_rate: number | null; // 누적 적중률(%) — 우상향 추적 대상
}

export interface CalibBin {
  lo: number;
  hi: number;
  n: number;
  actual_rate: number | null; // 실측 적중률(%)
  valid: boolean; // n >= 20
}

export interface WeightsInfo {
  current: Record<string, number>;
  default: Record<string, number>;
  tuned: boolean; // 자가 튜닝 활성 여부
  basis_n: number;
  tune_min_samples: number;
  history: { date: string; weights: Record<string, number>; basis_n: number }[];
}

export interface RecentSample {
  date: string;
  name: string;
  score: number;
  hit: boolean;
  return_pct: number;
}

/** AI(prob_up) 익일 예측 검증 — radar_backtest.ai_stats()와 정합. */
export interface AiProbBand {
  lo: number;
  hi: number;
  n: number;
  avg_prob: number | null; // 구간 내 평균 예측 확률(%)
  actual_rate: number | null; // 실측 적중률(%) — avg_prob와 가까울수록 보정 양호
  valid: boolean; // n >= 20
}

export interface AiDirStat {
  key: string; // 상승 | 관망 | 하락
  n: number;
  hit_rate: number;
  avg_return: number;
  high3_rate: number;
}

/** 룰베이스 vs AI 괴리 4분면 — radar_backtest.divergence_stats()와 정합. */
export interface DivergenceCell {
  key: string;
  rule_buy: boolean; // 룰베이스 점수 >= rule_buy_min
  ai_up: boolean; // AI 확률 >= ai_up_min
  n: number;
  hit_rate: number | null;
  avg_return: number | null;
  valid: boolean;
}

export interface DivergenceStats {
  rule_buy_min: number;
  ai_up_min: number;
  min_n: number;
  unknown_n: number; // verdict_score 미기록 구표본
  cells: DivergenceCell[];
}

export interface AiStats {
  n: number; // ai_pred 기록 + 익일 평가 완료 표본
  by_direction: AiDirStat[];
  prob_bands: AiProbBand[];
  avg_prob: number | null;
  actual_rate: number | null;
  brier: number | null; // 낮을수록 좋음 (0.25 = 무정보 기준선)
  /** 룰베이스 vs AI 일치/불일치 4분면 적중률 — 구버전 JSON엔 없을 수 있음 */
  divergence?: DivergenceStats;
}

/** 메가스파크×수급 가설 검증 표 — radar_backtest.spark_flow_matrix()와 정합. */
export interface SparkFlowCell {
  spark_bucket: string; // "<10x" | "10~40x" | "≥40x"
  flow_buy: boolean; // 당일 외인+기관 순매수 여부
  n: number;
  hit_rate: number | null;
  avg_return: number | null;
  high3_rate: number | null;
  valid: boolean; // n >= min_n
}

export interface SparkFlowStats {
  mega_x: number; // 메가 스파크 임계 (배)
  min_n: number; // 셀 유효 최소 표본
  unknown_n: number; // spark_max_x 미기록 구표본 수 (셀 제외)
  cells: SparkFlowCell[];
}

/** 테마/섹터별 성과 — radar_backtest.group_stats_gated()와 정합. */
export interface GroupStat {
  key: string; // 테마명 또는 섹터명 (미분류는 "unknown")
  n: number;
  hit_rate: number;
  avg_return: number;
  high3_rate: number;
  valid: boolean; // n >= FEATURE_MIN_N(10) — 미달 시 수치 숨김
  /** 그 테마에서 '테마 대장'(거래대금 1위)으로 가장 자주 뽑힌 종목 — by_theme만, 구버전엔 없음 */
  leader_name?: string;
  leader_count?: number;
}

export interface ExperimentalStats {
  reaccum: {
    n: number;
    hit_rate: number | null;
    avg_return: number | null;
    high3_rate: number | null;
  };
}

export interface PerformanceData {
  as_of: string;
  summary: {
    n: number; // 최종 카드(마감 잔존) 표본 — 주 통계·튜닝 기준
    hit_rate: number | null;
    avg_return: number | null;
    high3_rate: number | null;
    tracking_days: number;
    /** 장중 탈락군 참고 성적 (주 통계·튜닝 미포함) */
    dropout?: { n: number; hit_rate: number | null } | null;
  };
  series: PerfPoint[];
  bins: CalibBin[];
  weights: WeightsInfo;
  /** AI 익일 예측(prob_up) 검증 — 구버전 performance.json에는 없을 수 있음 */
  ai?: AiStats;
  /** 메가스파크×수급 가설 검증 표 — 구버전 performance.json에는 없을 수 있음 */
  spark_flow?: SparkFlowStats;
  /** 테마별 성과 — 구버전 performance.json에는 없을 수 있음 */
  by_theme?: GroupStat[];
  /** 섹터별 성과 — 구버전 performance.json에는 없을 수 있음 */
  by_sector?: GroupStat[];
  /** 기존 기준선에서 제외한 화면 노출 실험 표본 */
  experimental?: ExperimentalStats;
  recent: RecentSample[];
  disclaimer: string;
}
