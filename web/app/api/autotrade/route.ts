import { NextRequest, NextResponse } from "next/server";

import { kvCommand, kvConfigured } from "@/lib/kv";

// 자동매매 On/Off + 매수 대상 랭크 선택(최대 2) — 웹이 KV에 쓰고, 실행기(autotrade_executor.py)가 읽는다.
// GET  /api/autotrade                         → { enabled, ranks:number[], configured }
// POST /api/autotrade {enabled, ranks:number[]} → 저장 (ranks: 1~3 중 최대 2개)
// ⚠ enabled=1이면 실행기가 매일 종가(15:18/NXT 19:50)에 선택 랭크 종목을 실계좌 100만원(종목수 분할) 매수.
export const dynamic = "force-dynamic";

const K_EN = "autotrade:enabled";
const K_RANKS = "autotrade:ranks";

function parseRanks(raw: unknown): number[] {
  const out: number[] = [];
  const toks = typeof raw === "string" ? raw.split(",") : Array.isArray(raw) ? raw : [];
  for (const t of toks) {
    const r = typeof t === "number" ? t : parseInt(String(t).trim(), 10);
    if (Number.isInteger(r) && r >= 1 && r <= 3 && !out.includes(r)) out.push(r);
  }
  return out.slice(0, 2); // 최대 2
}

export async function GET() {
  if (!kvConfigured()) return NextResponse.json({ enabled: false, ranks: [1], configured: false });
  try {
    const [en, ranks] = await Promise.all([kvCommand(["GET", K_EN]), kvCommand(["GET", K_RANKS])]);
    const parsed = parseRanks(ranks as string);
    return NextResponse.json({
      enabled: en === "1",
      ranks: parsed.length ? parsed : [1],
      configured: true,
    });
  } catch {
    return NextResponse.json({ enabled: false, ranks: [1], configured: true });
  }
}

export async function POST(req: NextRequest) {
  let body: { enabled?: unknown; ranks?: unknown };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid body" }, { status: 400 });
  }
  const enabled = body.enabled === true;
  const ranks = parseRanks(body.ranks);
  if (enabled && ranks.length === 0)
    return NextResponse.json({ error: "매수할 랭크를 최소 1개 선택하세요" }, { status: 400 });
  if (!kvConfigured()) return NextResponse.json({ error: "KV not configured" }, { status: 503 });
  try {
    await kvCommand(["SET", K_EN, enabled ? "1" : "0"]);
    if (ranks.length) await kvCommand(["SET", K_RANKS, ranks.join(",")]);
    return NextResponse.json({ enabled, ranks: ranks.length ? ranks : [1], configured: true });
  } catch (e) {
    return NextResponse.json({ error: `저장 실패: ${String(e).slice(0, 80)}` }, { status: 502 });
  }
}
