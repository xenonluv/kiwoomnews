// 자동매매 토글 클라이언트 — 컴포넌트는 이 서비스를 통해서만 /api/autotrade 호출.

export interface AutoTradeState {
  enabled: boolean;
  code: string | null;
  name: string | null;
  configured: boolean;
}

export const autoTradeClientService = {
  async get(): Promise<AutoTradeState> {
    const r = await fetch("/api/autotrade", { cache: "no-store" });
    if (!r.ok) throw new Error(`autotrade ${r.status}`);
    return r.json();
  },
  async set(enabled: boolean, code?: string, name?: string): Promise<AutoTradeState> {
    const r = await fetch("/api/autotrade", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled, code, name }),
    });
    if (!r.ok) {
      const j = (await r.json().catch(() => ({}))) as { error?: string };
      throw new Error(j.error || `autotrade ${r.status}`);
    }
    return r.json();
  },
};
