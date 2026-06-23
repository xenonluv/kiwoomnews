import radarData from "@/data/radar.json";
import type { Explosion, RadarData } from "@/types/radar";

/**
 * 레이더 저장소 (단일 출처).
 * scripts/publish.py가 cron으로 갱신하는 정적 JSON을 빌드 시 import.
 * 빈 레이더(suspects=0)도 유효한 상태다 — "오늘은 레이더 깨끗".
 */
const RADAR = radarData as unknown as RadarData;

export function getRadar(): RadarData {
  return RADAR;
}

/** 당일 폭발 종목 (/forecast 페이지). 회전율 내림차순 정렬은 publish 단계에서 완료. */
export function getExplosions(): Explosion[] {
  return RADAR.explosions ?? [];
}

/** 폭발 게이트 임계값 — 화면 문구가 하드코딩 대신 radar params를 따르게(운영자가 튜닝해도 정합). */
export function getExplosionThresholds(): { highPct: number; volTurnover: number } {
  const p = RADAR.params ?? {};
  return { highPct: p.explosion_high_pct ?? 22, volTurnover: p.explosion_vol_turnover ?? 90 };
}
