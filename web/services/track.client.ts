"use client";

// 추적 버튼 → /api/track (Upstash KV). 추적 추가/조회.
export const trackClientService = {
  async isTracked(code: string): Promise<boolean> {
    try {
      const res = await fetch(`/api/track?code=${encodeURIComponent(code)}`, { cache: "no-store" });
      if (!res.ok) return false;
      const j = (await res.json()) as { tracked?: boolean };
      return !!j.tracked;
    } catch {
      return false;
    }
  },
  async add(code: string): Promise<void> {
    const res = await fetch(`/api/track`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    });
    if (!res.ok) {
      const j = (await res.json().catch(() => null)) as { error?: string } | null;
      throw new Error(j?.error || "추적 추가 실패");
    }
  },
};
