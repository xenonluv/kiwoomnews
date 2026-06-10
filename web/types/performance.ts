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
  recent: RecentSample[];
  disclaimer: string;
}
