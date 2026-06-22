import performanceData from "@/data/performance.json";
import trackData from "@/data/track_performance.json";
import aiClickData from "@/data/ai_click_performance.json";
import phaseData from "@/data/phase_performance.json";
import type {
  AiClickPerformance,
  PerformanceData,
  PhasePerformance,
  TrackPerformance,
} from "@/types/performance";

/**
 * 성과 검증 저장소 (단일 출처).
 * scripts/radar_backtest.py가 매일 장후 갱신하는 정적 JSON을 빌드 시 import.
 */
export function getPerformance(): PerformanceData {
  return performanceData as unknown as PerformanceData;
}

/** 추적 종목 검증 — scripts/track_eval.py가 갱신(web/data/track_performance.json). */
export function getTrackPerformance(): TrackPerformance {
  return trackData as unknown as TrackPerformance;
}

/** AI '클릭 예측' 보정 — scripts/ai_click_eval.py가 갱신(web/data/ai_click_performance.json). */
export function getAiClickPerformance(): AiClickPerformance {
  return aiClickData as unknown as AiClickPerformance;
}

/** AI 국면 판정 검증 — scripts/phase_eval.py가 갱신(web/data/phase_performance.json). */
export function getPhasePerformance(): PhasePerformance {
  return phaseData as unknown as PhasePerformance;
}
