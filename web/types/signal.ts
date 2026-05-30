// 파이프라인 게시 데이터 타입 (CEO 승인 → 디자이너/팀원5)
// schemas/06_CEO.schema.json 의 publish_data 와 정합.

export type SafePosition = "눌림목" | "저점";
export type MarketStatus = SafePosition | "과다상승" | "분석불가";

/** 게시 분석 데이터 (스크리너 + AI 분석 결과, 참고용) */
export interface PublishData {
  status: "PUBLISHED";
  target_stock: string;
  signal_probability: string; // "45%"
  position_type: MarketStatus; // 실제 국면(과다상승/분석불가 포함) 정직 표기
  headline: string;
  published_at: string;
}

/** 게시 등급: 시그널(A+B+C 통과) / 후보(A+B, C 대기) */
export type SignalTier = "signal" | "candidate";

/** 웹 게시물 (팀원5 → 팀원6 API) */
export interface SignalPost extends PublishData {
  post_id: string;
  tier?: SignalTier;
  summary: string;
  disclaimer: string;
}
