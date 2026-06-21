"use client";

import type {
  AiAnalysis,
  PhaseAnalysis,
  SearchResponse,
  StockAnswer,
  StockReport,
} from "@/types/stock";

/**
 * 종목 분석 클라이언트 서비스 — 컴포넌트는 fetch를 직접 호출하지 않고
 * 이 서비스를 통해 /api/stock/* 에 접근한다. (브라우저 전용, 상대 경로)
 */
export const stockClientService = {
  async search(q: string, signal?: AbortSignal): Promise<SearchResponse> {
    const res = await fetch(`/api/stock/search?q=${encodeURIComponent(q)}`, { signal });
    if (!res.ok) throw new Error(`종목 검색 실패 (HTTP ${res.status})`);
    return res.json();
  },

  async getReport(code: string): Promise<StockReport> {
    const res = await fetch(`/api/stock/${code}`, { cache: "no-store" });
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new Error(
        body?.error?.message ?? `리포트 조회 실패 (HTTP ${res.status})`
      );
    }
    return res.json();
  },

  async getAiAnalysis(code: string): Promise<AiAnalysis> {
    const res = await fetch(`/api/stock/${code}/ai`, { cache: "no-store" });
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new Error(
        body?.error?.message ?? `AI 분석 실패 (HTTP ${res.status})`
      );
    }
    return res.json();
  },

  async getPhaseAnalysis(code: string): Promise<PhaseAnalysis> {
    const res = await fetch(`/api/stock/${code}/phase`, { cache: "no-store" });
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new Error(body?.error?.message ?? `AI 국면 판정 실패 (HTTP ${res.status})`);
    }
    return res.json();
  },

  async askQuestion(code: string, question: string): Promise<StockAnswer> {
    const res = await fetch(`/api/stock/${code}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
      cache: "no-store",
    });
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new Error(body?.error?.message ?? `AI 질문 실패 (HTTP ${res.status})`);
    }
    return res.json();
  },
};
