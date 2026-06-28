// agent_alpha 사이드카 출력(web/data/alpha.json)의 타입. 코어와 무관(삭제안전: 이 파일 + lib/alpha + app/alpha + components/alpha 삭제 시 끝).

export interface AlphaMover {
  code: string;
  name: string;
  sector?: string;
  mover_type?: string; // explosion | youtong | reaccum
  date?: string; // 신호일(YYYYMMDD) — 최근 며칠 합쳐 게시
  file_date?: string; // 출처 forward 파일 sig_date(파일당 유니크) — React key 충돌 방지축
  change_pct?: number | null;
  is_eumbong?: boolean;
  below_prev?: boolean;
  turnover_pct?: number | null;
  turnover_2d_pct?: number | null; // 2일 누적 유통회전율(1순위 신호)
  close_strength?: number | null; // 종가강도(받힘) 0~1
  upper_wick_pct?: number | null;
  lower_wick_pct?: number | null;
  spark_1430_count?: number | null; // 14:30↑ 5분 양봉 스파크 수
  spark_source?: string;
  frgn_net?: number | null;
  orgn_net?: number | null;
  prsn_net?: number | null;
  kiwoom_buy_concentration?: number | null; // 0~1
  kiwoom_is_top_buyer?: boolean;
  glob_net_qty?: number | null;
  hidden_foreign_level?: number | null; // 키움 속 외인매집 강도 0~3 (quant SSOT), null=결측
  combined_score?: number; // 스파크 횟수 + 외인매집 강도 합산(정렬 순위, quant SSOT)
  kospi_chg?: number | null;
  kosdaq_chg?: number | null;
  catalyst?: string;
  real_likelihood?: number | null;
  sustainability?: number | null;
  manipulation_risk?: number | null;
  prob_up?: number | null;
  confidence?: number | null;
  redteam_flag?: boolean;
  labeled?: boolean;
  hit?: boolean | null;
  next_return_pct?: number | null;
  next_high_pct?: number | null; // 종가 대비 익일 고가 등락(종가베팅→다음날 고가 도달폭)
  next_date?: string;
}

export interface AlphaCalibCell {
  n: number;
  hit_rate: number | null;
  avg_return: number | null;
  avg_high?: number | null; // 평균 익일 고가 등락(종가 대비)
  touch7_rate?: number | null; // 익일 +7% 고가 터치율(익절 도달%)
  valid: boolean;
  status: string; // "입증가능" | "관찰중"
  turnover2d?: string;
  spark?: string;
  close_strength?: string;
}

export interface AlphaCalibration {
  generated_at?: string;
  total_labeled: number;
  min_n: number;
  overall?: AlphaCalibCell;
  eumbong_overall?: AlphaCalibCell;
  by_turnover2d_eumbong?: Record<string, AlphaCalibCell>;
  by_spark_eumbong_hi_turnover?: Record<string, AlphaCalibCell>;
  by_close_strength_eumbong?: Record<string, AlphaCalibCell>;
  by_spark_count?: Record<string, AlphaCalibCell>; // 14:30 스파크 횟수 단독(전체)
  by_hidden_foreign?: Record<string, AlphaCalibCell>; // 키움 속 외인매집 해당/미해당
  by_combined_score?: Record<string, AlphaCalibCell>; // 합산 종합점수(스파크+외인매집) 밴드
  cells?: AlphaCalibCell[];
  llm?: { n: number; brier: number; by_prob_band?: Record<string, AlphaCalibCell> } | null;
  note?: string;
}

export interface AlphaData {
  generated_at: string;
  date: string | null;
  movers: AlphaMover[];
  calibration: AlphaCalibration | null;
  disclaimer: string;
}
