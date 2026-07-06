import { NextRequest, NextResponse } from "next/server";

import { kvCommand, kvConfigured } from "@/lib/kv";

// 자동매매 On/Off + 매수 대상 랭크 선택(최대 2) — 웹이 KV에 쓰고, 실행기(autotrade_executor.py)가 읽는다.
// GET  /api/autotrade                         → { enabled, ranks:number[], configured }
// POST /api/autotrade {enabled, ranks:number[]} → 저장 (ranks: 1~3 중 최대 2개)
// ⚠ enabled=1이면 실행기가 매일 종가(15:18/NXT 19:50)에 선택 랭크 종목을 실계좌 100만원(종목수 분할) 매수.
export const dynamic = "force-dynamic";

const K_EN = "autotrade:enabled";
const K_RANKS = "autotrade:ranks";
const K_BUDGET = "autotrade:budget";
const BUDGET_MIN = 10_000;
const BUDGET_MAX = 100_000_000;
const BUDGET_DEFAULT = 1_000_000;

function parseRanks(raw: unknown): number[] {
  const out: number[] = [];
  const toks = typeof raw === "string" ? raw.split(",") : Array.isArray(raw) ? raw : [];
  for (const t of toks) {
    const r = typeof t === "number" ? t : parseInt(String(t).trim(), 10);
    if (Number.isInteger(r) && r >= 1 && r <= 3 && !out.includes(r)) out.push(r);
  }
  return out.slice(0, 2); // 최대 2
}

function parseBudget(raw: unknown): number {
  const v = typeof raw === "number" ? raw : parseInt(String(raw ?? "").trim(), 10);
  if (!Number.isFinite(v)) return BUDGET_DEFAULT;
  return Math.max(BUDGET_MIN, Math.min(BUDGET_MAX, Math.floor(v)));
}

export async function GET() {
  if (!kvConfigured())
    return NextResponse.json({ enabled: false, ranks: [1], budget: BUDGET_DEFAULT, configured: false });
  try {
    const [en, ranks, budget] = await Promise.all([
      kvCommand(["GET", K_EN]),
      kvCommand(["GET", K_RANKS]),
      kvCommand(["GET", K_BUDGET]),
    ]);
    const parsed = parseRanks(ranks as string);
    return NextResponse.json({
      enabled: en === "1",
      ranks: parsed.length ? parsed : [1],
      budget: budget != null ? parseBudget(budget as string) : BUDGET_DEFAULT,
      configured: true,
    });
  } catch {
    return NextResponse.json({ enabled: false, ranks: [1], budget: BUDGET_DEFAULT, configured: true });
  }
}

export async function POST(req: NextRequest) {
  let body: { enabled?: unknown; ranks?: unknown; budget?: unknown };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid body" }, { status: 400 });
  }
  const enabled = body.enabled === true;
  const ranks = parseRanks(body.ranks);
  const budget = body.budget != null ? parseBudget(body.budget) : null;
  if (enabled && ranks.length === 0)
    return NextResponse.json({ error: "매수할 랭크를 최소 1개 선택하세요" }, { status: 400 });
  if (!kvConfigured()) return NextResponse.json({ error: "KV not configured" }, { status: 503 });
  try {
    await kvCommand(["SET", K_EN, enabled ? "1" : "0"]);
    if (ranks.length) await kvCommand(["SET", K_RANKS, ranks.join(",")]);
    if (budget != null) await kvCommand(["SET", K_BUDGET, String(budget)]);
    return NextResponse.json({
      enabled,
      ranks: ranks.length ? ranks : [1],
      budget: budget ?? BUDGET_DEFAULT,
      configured: true,
    });
  } catch (e) {
    return NextResponse.json({ error: `저장 실패: ${String(e).slice(0, 80)}` }, { status: 502 });
  }
}
