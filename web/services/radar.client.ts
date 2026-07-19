"use client";

import type { RadarData } from "@/types/radar";

/**
 * 레이더 클라이언트 서비스 — 컴포넌트는 fetch를 직접 호출하지 않고
 * 이 서비스를 통해 /api/radar에 접근한다. (브라우저 전용, 상대 경로)
 */
export const radarClientService = {
  async get(): Promise<RadarData> {
    const res = await fetch("/api/radar", { cache: "no-store" });
    if (!res.ok) {
      throw new Error(`레이더 조회 실패 (HTTP ${res.status})`);
    }
    return res.json();
  },
};
