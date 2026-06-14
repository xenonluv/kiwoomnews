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

/** 수상함 점수 해부 (결정론 가중합 근거) */
export interface ScoreBreakdown {
  base: number;
  spark: number;
  fade: number;
  ma10: number;
  flow: number;
  event: number;
  ai?: number;
  /** 메가스파크(≥40x)×당일 수급매수 가점 — 표시 점수 전용 (raw에는 없음) */
  mega?: number;
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

/** 재매집 후보 메타 — 1천억+13% 폭발 이후 식은 구간 재매집 감시 */
export interface ReaccumInfo {
  peak_date: string; // YYYYMMDD
  peak_value_eok: number;
  peak_high_pct: number;
  ma20?: number;
  ma20_margin_pct?: number;
  /** 폭발 전후 투신 순매수 (조건=수량>0 느슨, 일수·금액은 정보 표시) */
  ivtr_net?: number; // 투신 순매수 수량
  ivtr_days?: number; // 투신 순매수일 수
  ivtr_eok?: number; // 투신 순매수 누적 금액(억)
  cause_summary?: string; // 폭발 catalyst 한 줄("왜 올랐나") — 구버전 JSON엔 없음
  orgn_net_after_peak?: number; // 구버전 JSON 하위호환
}

/** Kimi 후보 검증 결과 */
export interface AiVerdict {
  status: "ok" | "disabled" | "not_configured" | "unavailable" | "outside_window";
  verdict?: "CONFIRM" | "WATCH" | "REJECT";
  confidence?: number;
  reason?: string;
  risk_flags?: string[];
  manual_check?: string;
  model?: string;
  error?: string;
  window?: [string, string];
}

/** 수상 종목 (전 조건 통과) */
export interface Suspect {
  code: string;
  name: string;
  sector: string;
  /** 감지 패턴 — "fade"(급등 후 식음) | "shakeout"(눌림 후 재상승) | "deep_shakeout"(급락 흡수) | "reaccum"(재매집). */
  pattern?: "fade" | "shakeout" | "deep_shakeout" | "reaccum";
  shake?: ShakeInfo | null;
  deep_shake?: DeepShakeInfo | null;
  visible_experimental?: boolean;
  reaccum_badge?: boolean;
  reaccum?: ReaccumInfo | null;
  ai_verdict?: AiVerdict | null;
  suspicion_score: number; // 0~100
  /** 백테스트 실측 적중률 (점수대 표본 n>=20 구간만, 없으면 null) */
  calibrated_prob?: { rate: number | null; n: number } | null;
  score_breakdown: ScoreBreakdown; // 자가 튜닝 가중치 적용 후 (화면 표시값)
  score_raw?: number; // 가중치 적용 전 — 백테스트 통계 기준
  score_breakdown_raw?: ScoreBreakdown;
  price: number;
  change_pct: number; // 현재 등락률 (조건 6)
  high_pct: number; // 당일 고가 등락률 (조건 3)
  fade_pct: number; // 고가 상승분 대비 후퇴율
  value_eok: number; // 당일 거래대금(억)
  ma10: number;
  ma10_margin_pct: number; // 10일선 대비 여유 (조건 4)
  spark: { clusters: SparkCluster[] };
  /** 최대 스파크 클러스터 배수 — 구버전 JSON엔 없음 */
  spark_max_x?: number;
  /** 최대 배수 클러스터의 누적 등락(%) — 부호로 상승/하락 메가 구분 (기록 전용) */
  spark_max_pct?: number | null;
  /** 메가스파크(≥mega_x) × 당일 외인+기관 순매수 동반 여부 */
  mega_flow?: boolean;
  flow: FlowInfo;
  news: NewsItem[];
  matched_events: MatchedEvent[];
  /** 상위 테마(금리|반도체|환율|유가|전쟁|실적|수급) — 표시·그룹용, 점수 미반영. 구버전 JSON엔 없음 */
  theme?: string;
}

/** radar.json 루트 */
export interface RadarData {
  generated_at: string;
  market_session: "open" | "closed";
  disclaimer: string;
  params: {
    min_value_eok: number;
    high_pct: number;
    chg_range: [number, number];
    spark_x: number;
    spark_pct: number;
    /** 메가 스파크 임계(배) — 수급매수 동반 시 가점. 구버전 JSON엔 없음. */
    mega_x?: number;
    /** 유니버스 수집 방식 — "kis_rank"(기본) | "naver_scan"(폴백). 구버전 JSON엔 없음. */
    universe?: string;
    /** 시장×지표(거래대금/등락률)별 상위 N (kis_rank 방식) */
    top_n?: number;
    /** deep 수집용 확장 포함 유니버스 등락률 범위 */
    universe_chg_range?: [number, number];
    /** 흔들기 트랙: 눌림 깊이 하한(%) / 등락률 상한(%) */
    shake_pct?: number;
    shake_chg_max?: number;
    deep_shake_enabled?: boolean;
    deep_drop_range?: [number, number];
    deep_ibs_min?: number;
    kimi_mode?: "auto" | "on" | "off";
    kimi_max?: number;
    kimi_window?: [string, string];
    reaccum_enabled?: boolean;
    reaccum_visible?: boolean;
    reaccum_max?: number;
    explosion_value_eok?: number;
    explosion_high_pct?: number;
    explosion_window?: number;
    explosion_rank_n?: number;
  };
  universe_count: number;
  events: RadarEvent[];
  suspects: Suspect[];
}
