// 당일 분봉 스파크 탐지 — scripts/radar.py:detect_sparks(58-98) 1:1 포팅.
// 파이썬 쪽 산식 변경 시 동기화 필요 (indicators.ts와 동일한 유지보수 규칙).

import type { SparkCluster } from "@/types/radar";
import type { MinuteBar } from "@/types/stock";

export const SPARK_VOL_X = 8.0; // 분봉 거래량 / 당일 중앙값 배수
export const SPARK_PCT = 0.8; // 분봉 등락 절대값 (%)
export const MEGA_SPARK_X = 40; // 메가 스파크 임계 — radar.py MEGA_SPARK_X와 동기화

/**
 * 당일 1분봉에서 거래량 스파크 클러스터 추출.
 * 스파크 = 거래량 ≥ 당일 중앙값×volX AND |봉 등락| ≥ pct%.
 * 개장 직후(~09:10)는 원래 거래량이 크므로 임계 1.5배 가중.
 * 연속 분봉은 1개 클러스터로 묶는다.
 */
export function detectSparks(
  bars: MinuteBar[],
  volX = SPARK_VOL_X,
  pct = SPARK_PCT
): SparkCluster[] {
  if (bars.length < 30) return [];
  const vols = bars
    .map((b) => b.vol)
    .filter((v) => v > 0)
    .sort((a, b) => a - b);
  if (vols.length === 0) return [];
  const median = vols[Math.floor(vols.length / 2)]; // 파이썬 vols[len//2] 동일 (상위 중앙값)
  if (median <= 0) return [];

  type Cluster = { time: string; volX: number; pct: number; n: number };
  const clusters: Cluster[] = [];
  let cur: Cluster | null = null;
  let prevClose: number | null = null;
  for (const b of bars) {
    if (!b.close) continue; // 거래 없는 봉(close=0) — -100% 거짓 등락 방지 (체인·클러스터 유지)
    const chg = prevClose ? (b.close / prevClose - 1) * 100 : 0;
    prevClose = b.close;
    const x = b.vol / median;
    const need = volX * (b.time <= "0910" ? 1.5 : 1.0);
    if (x >= need && Math.abs(chg) >= pct) {
      if (cur === null) {
        cur = { time: b.time, volX: x, pct: chg, n: 1 };
      } else {
        cur.n += 1;
        cur.volX = Math.max(cur.volX, x);
        cur.pct += chg;
      }
    } else if (cur !== null) {
      clusters.push(cur);
      cur = null;
    }
  }
  if (cur !== null) clusters.push(cur);

  return clusters.map((c) => ({
    time: `${c.time.slice(0, 2)}:${c.time.slice(2, 4)}`,
    vol_x: Math.round(c.volX * 10) / 10,
    pct: Math.round(c.pct * 100) / 100,
    minutes: c.n,
  }));
}
