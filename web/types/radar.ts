// 이벤트 매집 레이더 게시 데이터 타입 — scripts/publish.py가 생성하는 web/data/radar.json과 정합.

/** 종목 관련 뉴스 (재료필터 통과분, 네이버 기사 링크) */
export interface NewsItem {
  title: string;
  url: string | null;
  office: string | null;
  sentiment?: string | null; // 호재 | 악재 | 중립
  summary?: string | null;
  datetime?: string | null; // "YYYYMMDDHHMM"
}

/** D-10 이내 매크로/실적 이벤트 (조건 1) */
export interface RadarEvent {
  id: string;
  date: string; // YYYY-MM-DD
  dday: number; // 0 = 오늘
  title: string;
  category: string[]; // 금리 | 반도체 | 환율 | 유가 | 전쟁 | 실적 | 수급
  importance: number; // 1~10
  country: string; // US | KR
  estimated: boolean; // 규칙 기반 추정일 여부
}

/** 분봉 스파크 클러스터 (조건 2 증거) */
export interface SparkCluster {
  time: string; // "09:21"
  vol_x: number; // 당일 분봉 거래량 중앙값 대비 배수
  pct: number; // 클러스터 누적 등락(%)
  minutes: number; // 지속 분
}

/** 외국인/기관 수급 */
export interface FlowInfo {
  net_days: number; // 최근 5일 중 순매수일 수
  today_buy: boolean;
  streak: number; // 연속 순매수일
  detail: { date: string; frgn: number; orgn: number }[];
}

/** 이벤트 민감도 매칭 결과 (조건 5) */
export interface MatchedEvent {
  id: string;
  title: string;
  dday: number;
  categories: string[];
  score: number;
}

/** 재매집(반등) 점수 해부 (결정론 가산점 — 표시 전용 '강도', raw에는 없음) */
export interface ScoreBreakdown {
  base: number;
  re_count?: number; // 당일 5분 양봉 스파크 자격 봉 개수
  re_body?: number; // 최대 5분 양봉 몸통%
  peak_turnover?: number; // 폭발일 회전율(거래량/유통주식수) 가점 — 폭발의 자명함(주신호)
  re_turnover?: number; // 당일 회전율(거래량/유통주식수) 가점
}

/** 흔들기(눌림 후 재상승) 패턴 증거 — pattern === "shakeout"일 때만 */
export interface ShakeInfo {
  depth_pct: number; // 장중 고점 대비 최대 눌림 깊이(%)
  recovery_pct: number; // 낙폭 대비 회복률(%) — 100 초과 = 고점 돌파 재상승
  high_time: string; // "10:21"
  trough_time: string;
}

/** 급락 흡수 패턴 증거 — pattern === "deep_shakeout"일 때만 */
export interface DeepShakeInfo {
  drop_low_from_high_pct: number;
  drop_close_from_high_pct: number;
  ibs: number;
  recovery_pct: number;
  high_time: string;
  low_time: string;
  late_reclaim: boolean;
  vwap_reclaim: boolean;
  retest_broken: boolean;
  close_hold_score: number;
  bars15_count: number;
}

/** 재매집(반등) 후보 메타 — 최근 6거래일 폭발(고가22%+거래량90%) 종목 */
export interface ReaccumInfo {
  peak_date: string; // YYYYMMDD
  peak_value_eok: number;
  peak_high_pct: number;
  peak_turnover_pct?: number | null; // 폭발일 거래량 회전율(유통주식수 대비 %)
  peak_ibs?: number | null; // 폭발일 마감강도 IBS(0=저가마감·1=고가마감) — 약마감(낮음)일수록 익일 연속성↑ 경향(전진검증중)
  peak_uppertail?: number | null; // 폭발일 윗꼬리%((고가−종가)/종가) — 클수록 약마감
  ma20?: number;
  ma20_margin_pct?: number;
  cause_summary?: string; // 폭발 catalyst 한 줄("왜 올랐나") — 구버전 JSON엔 없음
  /** 폭발일에 같은 업종 거래대금 1위(업종 대장)였는지 — '예전 대장 재등장' 의심 신호. 구버전 JSON엔 없음 */
  was_theme_leader?: boolean;
  /** 진입 경로 — "live"(랭킹) | "seed"(시드파일) | "telegram"(채널發) | "backfill"(6일 소급). 구버전 JSON엔 없음 */
  source?: "live" | "seed" | "telegram" | "backfill";
}

/** 재매집(오늘) 신호 — 폭발 종목이 오늘 5분봉 양봉으로 다시 분출(스파크)하는지 */
export interface ReignitionInfo {
  body_pct: number; // 5분봉 양봉 몸통%(|종가−시가|/시가) 최댓값
  time: string; // 대표(최대 몸통) 5분 스파크 시각 "HH:MM"
  count?: number; // 당일 자격 양봉 스파크 수(게이트 ≥3)
  value_eok?: number; // 그 5분봉 1개의 거래대금(억) — 메타데이터(미표시)
}

/** 당일 폭발 종목 — 고가등락률 ≥22% AND 당일 거래량/유통주식수 ≥90% (/forecast 게시) */
export interface Explosion {
  code: string;
  name: string;
  sector: string;
  high_pct: number; // 당일 고가 등락률(%)
  vol_turnover_pct: number; // 당일 거래량 / 유통주식수 회전율(%)
  value_eok: number; // 당일 거래대금(억)
  /** 현재가 — 랭킹에서 밀려 registry로 백필된 종목은 null(현재가 미표시) */
  price: number | null;
  /** 현재 등락률(라이브 행). 백필 행(price=null)은 신뢰할 등락률이 없어 null — 미표시 */
  change_pct: number | null;
}

/** 3일내 +7% 상승확률 라벨 — 6개월 백테스트 보정(과거 실측·보장 아님) */
export interface ForecastInfo {
  horizon: string; // "3일 내 +7%"
  prob_pct: number; // 과거 실측 확률(강 모멘텀 상위군이면 상향)
  base_pct: number; // 재매집 후보 전체 기저확률
  strong: boolean; // 강 모멘텀 상위군(holdout 검증 구간)
  next_day_7_pct: number; // 내일(1일) +7% 터치 — 낮음, 정직 표기
  note: string;
}

/** 수상 종목 (전 조건 통과) */
export interface Suspect {
  code: string;
  name: string;
  sector: string;
  /** 감지 패턴 — "fade"(급등 후 식음) | "shakeout"(눌림 후 재상승) | "deep_shakeout"(급락 흡수) | "reaccum"(재매집). */
  pattern?: "fade" | "shakeout" | "deep_shakeout" | "reaccum";
  /** 핵심 조건(분봉 스파크+식음/흔들기 품질+투자자 수급) 모두 충족 → 큰 "유력" 뱃지. 구버전 JSON엔 없음 */
  prime?: boolean;
  shake?: ShakeInfo | null;
  deep_shake?: DeepShakeInfo | null;
  visible_experimental?: boolean;
  reaccum_badge?: boolean;
  reaccum?: ReaccumInfo | null;
  /** 재반등(오늘) 신호 — pattern==="reaccum" 카드에 존재. 구버전 JSON엔 없음 */
  reignition?: ReignitionInfo | null;
  /** 3일내 +7% 과거 실측 확률 라벨 — 표시 전용·보장 아님. 구버전 JSON엔 없음 */
  forecast?: ForecastInfo | null;
  suspicion_score: number; // 0~100
  /** 백테스트 실측 적중률 (점수대 표본 n>=20 구간만, 없으면 null) */
  calibrated_prob?: { rate: number | null; n: number } | null;
  /** '예전 대장' 재매집 코호트 실측 익일 상승률 (was_theme_leader=true & 표본 충분할 때만, 표시 전용) */
  leader_cohort_prob?: { rate: number | null; n: number } | null;
  score_breakdown: ScoreBreakdown; // 자가 튜닝 가중치 적용 후 (화면 표시값)
  score_raw?: number; // 가중치 적용 전 — 백테스트 통계 기준
  score_breakdown_raw?: ScoreBreakdown;
  price: number;
  change_pct: number; // 현재 등락률
  high_pct: number; // 당일 고가 등락률
  value_eok: number; // 당일 거래대금(억)
  turnover_pct?: number | null; // 당일 회전율(거래량/유통주식수 %) — 손바뀜 강도
  peak_turnover_pct?: number | null; // 폭발일 회전율(거래량/유통주식수 %)
  float_ratio?: number | null; // 유동비율(0~1)
  turnover_basis?: "float" | "cap"; // 회전율 기준
  ma10: number;
  ma10_margin_pct: number; // 10일선 대비 여유
  spark: { clusters: SparkCluster[] };
  /** 최대 스파크 클러스터 배수 — 구버전 JSON엔 없음 */
  spark_max_x?: number;
  /** 최대 배수 클러스터의 누적 등락(%) — 부호로 상승/하락 메가 구분 (기록 전용) */
  spark_max_pct?: number | null;
  /** 메가스파크(≥mega_x) × 당일 외인+기관 순매수 동반 여부 */
  mega_flow?: boolean;
  flow?: FlowInfo; // 구버전 JSON 하위호환(현 파이프라인 미출력)
  news: NewsItem[];
  matched_events: MatchedEvent[];
  /** 상위 테마(금리|반도체|환율|유가|전쟁|실적|수급) — 표시·그룹용, 점수 미반영. 구버전 JSON엔 없음 */
  theme?: string;
  /** 같은 테마 내 당일 거래대금 1위(테마 대장) 여부 — 표시 전용. 구버전 JSON엔 없음 */
  theme_leader?: boolean;
}

/** radar.json 루트 */
export interface RadarData {
  generated_at: string;
  market_session: "open" | "closed";
  disclaimer: string;
  params: {
    /** 반등: 5분 양봉 몸통% 하한 */
    reignition_body_pct?: number;
    /** 반등: 분봉 합성 단위(분) */
    reignition_span_min?: number;
    /** 반등: 당일 자격 양봉 스파크 최소 횟수 */
    reignition_min_count?: number;
    /** 폭발: 거래량/유통주식수 회전율 하한(%) */
    explosion_vol_turnover?: number;
    /** 폭발: 시장별 네이버 up 스캔 상위 N */
    explosion_scan_n?: number;
    // ── 구버전 JSON 하위호환 ──
    reaccum_change_range?: [number, number];
    reaccum_high_range?: [number, number];
    reignition_value_10m_eok?: number;
    explosion_value_eok?: number;
    explosion_rank_n?: number;
    // --- 아래는 구버전(fade) radar.json 하위호환용 (현 파이프라인은 미출력) ---
    min_value_eok?: number;
    high_pct?: number;
    chg_range?: [number, number];
    spark_x?: number;
    spark_pct?: number;
    mega_x?: number;
    universe?: string;
    top_n?: number;
    universe_chg_range?: [number, number];
    shake_pct?: number;
    shake_chg_max?: number;
    deep_shake_enabled?: boolean;
    deep_drop_range?: [number, number];
    deep_ibs_min?: number;
    reaccum_enabled?: boolean;
    reaccum_visible?: boolean;
    reaccum_max?: number;
    explosion_high_pct?: number;
    explosion_window?: number;
  };
  universe_count: number;
  events: RadarEvent[];
  /** 당일 폭발 종목 (/forecast 게시용) */
  explosions?: Explosion[];
  suspects: Suspect[];
}
