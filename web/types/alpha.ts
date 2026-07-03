// agent_alpha 사이드카 출력(web/data/alpha.json)의 타입. 코어와 무관(삭제안전: 이 파일 + lib/alpha + app/alpha + components/alpha 삭제 시 끝).

export interface AlphaMover {
  code: string;
  name: string;
  sector?: string;
  mover_type?: string; // explosion | youtong | reaccum
  date?: string; // 신호일(YYYYMMDD) — 최근 며칠 합쳐 게시
  file_date?: string; // 출처 forward 파일 sig_date(파일당 유니크) — React key 충돌 방지축
  provisional?: boolean; // 장중 잠정 수집(15:15, 마감 전 미확정값) — 15:40 확정이 덮어씀
  change_pct?: number | null;
  is_eumbong?: boolean;
  below_prev?: boolean;
  turnover_pct?: number | null;
  turnover_2d_pct?: number | null; // 2일 누적 유통회전율
  value_eok?: number | null; // 당일 거래대금(억) — v4 채점축(≥1000억 +10·유동성결핍)·정렬 타이브레이크
  run_6d_pct?: number | null; // 6세션 전 종가 대비 누적 상승률 — 과확장붕괴(≥100%&당일음수 −30) 판정
  peak_dd_pct?: number | null; // 직전 7세션 최고종가 대비 낙폭 — 표시·관찰 전용(점수 미반영)
  down_streak?: number | null; // 종가 기준 연속 하락 일수 — ≥4일 −15 (연속하락 벌점)
  close_strength?: number | null; // 종가강도(받힘) 0~1
  upper_wick_pct?: number | null;
  lower_wick_pct?: number | null;
  spark_1430_count?: number | null; // 14:30↑ 5분 양봉 스파크 수(몸통 1.5%↑ 탐지 기준)
  spark_max_body_pct?: number | null; // 스파크 최대 몸통% — v4 약스파크(0<x<3%) 벌점 입력
  spark_strong_count?: number | null; // 몸통 3%↑ 강스파크 개수 — 2회↑ +8 가점(고가 사냥, 과거 4/4 터치)
  spark_bars?: { time?: string; body_pct?: number }[] | null; // 원천 스파크 봉(옛 행 재계산 폴백용)
  spark_source?: string;
  frgn_net?: number | null;
  orgn_net?: number | null;
  prsn_net?: number | null;
  kiwoom_buy_concentration?: number | null; // 0~1
  kiwoom_is_top_buyer?: boolean;
  glob_net_qty?: number | null;
  hidden_foreign_level?: number | null; // 키움 속 외인매집 강도 0~3 (quant SSOT), null=결측
  combined_score?: number; // (레거시) 스파크 횟수 + 외인매집 강도 합산, quant SSOT
  close_bet_fitness?: number; // 종가베팅 적합도 0~100 (quant 저장·참고용). /alpha 정렬·calibrate 검증은 저장값 대신 현행 산식으로 재계산 — fitness.py 변경 시 AlphaList.tsx closeBetFitness도 1:1 동기화 필수.
  alert_now?: string | null; // KRX 시장경보 현재 지정: "주의"/"경고"/"위험" (네이버 marketAlertType)
  alert_forecast?: string | null; // 경고예고/지정 공식 예측: "경고예고 예상"/"경고지정 요건충족" — 정보 배지 전용(점수 무반영)
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
  by_combined_score?: Record<string, AlphaCalibCell>; // (레거시) 합산 종합점수 밴드
  by_change_pct?: Record<string, AlphaCalibCell>; // 당일 등락률 밴드별 — 종베 핵심(0~+8% 최적)
  by_mover_type?: Record<string, AlphaCalibCell>; // reaccum/youtong/explosion별
  by_close_bet_band?: Record<string, AlphaCalibCell>; // 종베 적합도 점수대별 — 현행 정렬축 검증
  by_close_bet_rank?: Record<string, AlphaCalibCell>; // 종베 정렬 순위별(1위/2위/…)
  by_value_band?: Record<string, AlphaCalibCell>; // 거래대금 밴드별 — v4 ≥1000억 +10 전진검증
  by_spark_strength?: Record<string, AlphaCalibCell>; // 스파크 세기(무/약/강) — 무>강 관측 서열 판정
  by_liquidity_deficit?: Record<string, AlphaCalibCell>; // 유동성결핍 해당/미해당 — v4 −15 검증
  by_crash_state?: Record<string, AlphaCalibCell>; // 폭락제외(과확장붕괴/연속하락/정상) 전진검증
  cells?: AlphaCalibCell[];
  llm?: { n: number; brier: number; by_prob_band?: Record<string, AlphaCalibCell> } | null;
  note?: string;
}

export interface AlphaData {
  generated_at: string;
  date: string | null;
  movers: AlphaMover[];
  yesterday_date?: string | null; // 어제(직전 거래일) 신호일
  yesterday_results?: AlphaMover[]; // 어제 종목 + 익일결과(라벨 완료) — '어제 결과' 섹션
  calibration: AlphaCalibration | null;
  disclaimer: string;
}
