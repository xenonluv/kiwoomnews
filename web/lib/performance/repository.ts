import performanceData from "@/data/performance.json";
import trackData from "@/data/track_performance.json";
import type { PerformanceData, TrackPerformance } from "@/types/performance";

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
