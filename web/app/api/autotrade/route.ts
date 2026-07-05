import { NextRequest, NextResponse } from "next/server";

import { kvCommand, kvConfigured } from "@/lib/kv";

// 자동매매 On/Off 토글 — 웹(브라우저)이 KV에 플래그를 쓰고, Windows 실행기(autotrade_executor.py)가 읽는다.
// GET  /api/autotrade            → { enabled, code, name, configured }
// POST /api/autotrade {enabled, code?, name?} → 토글 저장
// ⚠ enabled=1이면 실행기가 매일 종가(15:18/NXT 19:50)에 레이더 1위를 실계좌 100만원 매수.
export const dynamic = "force-dynamic";

const K_EN = "autotrade:enabled";
const K_CODE = "autotrade:code";
const K_NAME = "autotrade:name";

export async function GET() {
  if (!kvConfigured()) return NextResponse.json({ enabled: false, code: null, name: null, configured: false });
  try {
    const [en, code, name] = await Promise.all([
      kvCommand(["GET", K_EN]),
      kvCommand(["GET", K_CODE]),
      kvCommand(["GET", K_NAME]),
    ]);
    return NextResponse.json({
      enabled: en === "1",
      code: (code as string) ?? null,
      name: (name as string) ?? null,
      configured: true,
    });
  } catch {
    return NextResponse.json({ enabled: false, code: null, name: null, configured: true });
  }
}

export async function POST(req: NextRequest) {
  let body: { enabled?: unknown; code?: unknown; name?: unknown };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid body" }, { status: 400 });
  }
  const enabled = body.enabled === true;
  const code = typeof body.code === "string" && /^\d{6}$/.test(body.code) ? body.code : "";
  const name = typeof body.name === "string" ? body.name.slice(0, 40) : "";
  if (!kvConfigured()) return NextResponse.json({ error: "KV not configured" }, { status: 503 });
  try {
    await kvCommand(["SET", K_EN, enabled ? "1" : "0"]);
    if (enabled && code) {
      await kvCommand(["SET", K_CODE, code]);
      await kvCommand(["SET", K_NAME, name]);
    }
    return NextResponse.json({ enabled, code, name, configured: true });
  } catch (e) {
    return NextResponse.json({ error: `저장 실패: ${String(e).slice(0, 80)}` }, { status: 502 });
  }
}
