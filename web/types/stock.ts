// 온디맨드 종목 분석 리포트 타입 — /api/stock/[code] 응답과 정합.
// 모든 섹션은 nullable: 네이버 일부 실패·ETF·신규상장 등에서 해당 섹션만 비우고
// 리포트 자체는 항상 렌더한다 (사유는 warnings[]에 기록).

export interface SearchItem {
  code: string;
  name: string;
  market: string; // KOSPI | KOSDAQ
}

export interface SearchResponse {
  items: SearchItem[];
}

export interface Candle {
  date: string; // YYYYMMDD
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface PriceSection {
  close: number;
  change: number;
  changePct: number;
  marketCap: string | null; // "1,753조 8,836억" 표시용 원문
  per: number | null;
  eps: number | null;
  cnsPer: number | null; // 컨센서스 기준
  cnsEps: number | null;
  pbr: number | null;
  bps: number | null;
  dividendYield: number | null; // %
  foreignRate: number | null; // 외국인 보유율 %
  high52: number | null;
  low52: number | null;
  pctFrom52High: number | null; // 52주 고가 대비 %
  pctFrom52Low: number | null;
  consensus: {
    recommMean: number; // 네이버 5점 척도(높을수록 매수 의견)
    targetPrice: number;
    upsidePct: number; // 목표가 대비 상승 여력 %
    date: string;
  } | null;
}

export interface TechnicalSection {
  ma5: number | null;
  ma20: number | null;
  ma60: number | null;
  maAligned: boolean; // 정배열 (5>20>60)
  closeStrength: number | null; // 0~1, 0.8+ = 고가 마감
  volumeVs20d: number | null; // 20일 평균 대비 배수
  macd: {
    macd: number;
    signal: number;
    hist: number;
    aboveZero: boolean;
    goldenCross: boolean;
    bullish: boolean;
  } | null;
  rsi: { value: number; zone: "과매수" | "과매도" | "강세" | "약세" } | null;
  stochastic: {
    k: number;
    d: number;
    goldenCross: boolean;
    overbought: boolean;
    bullish: boolean;
  } | null;
  ichimoku: {
    available: boolean;
    aboveCloud?: boolean;
    inCloud?: boolean;
    tenkanGtKijun?: boolean;
    tenkan?: number;
    kijun?: number;
    cloudTop?: number;
    cloudBot?: number;
  };
}

export interface FlowDay {
  date: string; // YYYYMMDD
  foreign: number; // 순매수 주수 (+매수/-매도)
  organ: number;
  individual: number;
  foreignHoldRatio: number | null; // %
  close: number | null;
}

export interface FlowSection {
  daily: FlowDay[]; // 최근 거래일 내림차순 → UI에서 정렬
  summary: {
    foreignNet5: number;
    organNet5: number;
    foreignNetDays5: number; // 최근 5일 중 외인 순매수일 수
    organNetDays5: number;
  };
}

export interface FinancialSection {
  periods: { label: string; isEstimate: boolean }[]; // "2025.12." / 컨센서스 연도
  rows: { label: string; values: (number | null)[] }[]; // 매출액·영업이익 등 (억원)
  highlights: {
    revenueYoY: number | null; // 최근 확정연도 매출 YoY %
    opYoY: number | null;
    opMargin: number | null; // %
    profitable: boolean | null;
  };
}

export interface StockNewsItem {
  title: string;
  url: string | null;
  office: string | null;
  datetime: string; // YYYYMMDDHHmm
  sentiment: "호재" | "악재" | "혼재" | "중립";
  relevant: boolean; // 재료필터 통과 여부
}

export interface NewsSection {
  items: StockNewsItem[];
  summary: {
    sentiment: "호재" | "악재" | "혼재" | "중립";
    importance: number; // 1~10
    impact: "상" | "중" | "하";
    relevantCount: number;
    posCount: number;
    negCount: number;
  };
}

export interface EventSection {
  matched: {
    id: string;
    title: string;
    date: string;
    dday: number;
    categories: string[];
    importance: number;
    score: number;
  }[];
  totalScore: number; // 0~15 (theme_map 동일 산식)
  upcomingCount: number; // D-10 이내 전체 이벤트 수 (매칭 0건이어도 맥락)
}

export interface ResearchItem {
  firm: string;
  title: string;
  date: string; // YYYYMMDD
}

export type VerdictLevel =
  | "강한 매수신호"
  | "매수 우위"
  | "중립"
  | "관망·과열"
  | "매도 우위";

export type VerdictGroup = "기술" | "재료" | "컨센서스" | "수급" | "이벤트" | "시장경보";

/** KRX 시장경보 3단계 (주의 → 경고 → 위험 순으로 심각) */
export type MarketAlertLevel = "주의" | "경고" | "위험";

export interface MarketAlert {
  level: MarketAlertLevel;
  label: string; // 네이버 원문 표기 (예: "투자주의")
}

export interface VerdictSection {
  level: VerdictLevel;
  score: number; // 5~95 (결정론 confluence)
  breakdown: { label: string; delta: number; group: VerdictGroup }[];
  cautionFlags: string[];
  entry: number;
  target: number; // 참고 +5%
  stop: number; // 참고 -3%
  supports: { label: string; price: number }[];
  resistances: { label: string; price: number }[];
  summary: string; // 규칙 템플릿 조합 1문장 (LLM 아님)
}

/** AI(LLM) 심층 분석 — 버튼 클릭 시 /api/stock/[code]/ai 에서 생성. */
export type AiDirection = "상승" | "하락" | "관망";

export interface AiAnalysis {
  code: string;
  asOf: string; // 생성 시각 KST
  model: string; // 사용 모델 id (투명성)
  direction: AiDirection; // 익일 방향 판단
  confidence: number; // 0~100
  reasons: string[]; // 핵심 근거 3~5개
  risks: string[]; // 리스크 1~3개
  narrative: string; // 2~4문장 한국어 서술
}

export interface StockReport {
  code: string;
  name: string;
  market: string | null;
  asOf: string; // "YYYY-MM-DD HH:mm KST"
  marketStatus: string | null; // OPEN | CLOSE ...
  /** 거래소 시장경보 지정 (투자주의/경고/위험). 미지정이면 null. */
  marketAlert: MarketAlert | null;
  /** 관리종목 여부 (상장폐지 사유 발생 등 — 시장경보와 별개 필드) */
  isManagement: boolean;
  tradeStop: boolean;
  newlyListed: boolean;
  isEtf: boolean;
  warnings: string[];
  price: PriceSection | null;
  chart: { candles: Candle[] } | null;
  technical: TechnicalSection | null;
  flow: FlowSection | null;
  financials: FinancialSection | null;
  news: NewsSection | null;
  events: EventSection | null;
  researches: ResearchItem[];
  verdict: VerdictSection | null;
  disclaimer: string;
}
