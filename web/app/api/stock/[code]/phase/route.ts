import { NextRequest, NextResponse } from "next/server";

import { AiConfigError, AiUnavailableError } from "@/lib/stock/ai";
import { buildPhaseAnalysis } from "@/lib/stock/phase";
import { NotFoundError, UnreachableError } from "@/lib/stock/report";
import { kvCommand, kvConfigured } from "@/lib/kv";
import { ymdKST } from "@/lib/stock/parse";
import type { PhaseAnalysis } from "@/types/stock";

// AI 국면 판정(재매집/분산/중립) — 버튼 클릭 온디맨드. 같은 종목·같은 날 동일 결과라 /ai와 동일하게
// 30분 CDN 캐시 + in-flight 디둡으로 Kimi 호출을 아낀다(찌라시 30분 staleness는 보조 판단에 허용).
const CACHE_OK = "public, s-maxage=1800, stale-while-revalidate=3600";
const CACHE_ERR = "public, s-maxage=60, stale-while-revalidate=120";

export const dynamic = "force-dynamic";
export const maxDuration = 300; // kimi-k2.6 reasoning 여유(Fluid Compute)

const inflight = new Map<string, Promise<PhaseAnalysis>>();

function dedup(code: string): Promise<PhaseAnalysis> {
  let p = inflight.get(code);
  if (!p) {
    p = buildPhaseAnalysis(code).finally(() => inflight.delete(code));
    inflight.set(code, p);
  }
  return p;
}

const PRED_TTL_SEC = 60 * 60 * 24 * 90; // 판정 해시 90일 보관(평가·만료 충분)

/**
 * 클릭한 종목의 국면 판정을 KV에 1건 적재 — 익일 등락 채점(재매집→상승·분산→하락 적중)의 원천
 * (scripts/phase_eval.py). 종목·일자당 1건(HSETNX): 같은 날 여러 번 눌러도 첫 판정만 남는다.
 * KV 미설정(로컬·무시크릿 프리뷰)이면 조용히 skip → 판정 응답 동작 불변. 실패는 호출부에서 삼킨다.
 */
async function recordPhase(code: string, p: PhaseAnalysis): Promise<void> {
  if (!kvConfigured()) return;
  const date = ymdKST();
  const key = `phase:${date}`;
  const payload = JSON.stringify({ phase: p.phase, confidence: p.confidence, ts: Date.now() });
  // aipred와 동일: HSETNX 후 EXPIRE·SADD(멱등)로 비원자성 갭 자가치유.
  await kvCommand(["HSETNX", key, code, payload]);
  await kvCommand(["EXPIRE", key, PRED_TTL_SEC]);
  await kvCommand(["SADD", "phase:dates", date]);
}

export async function GET(req: NextRequest, { params }: { params: { code: string } }) {
  const code = params.code;
  if (!/^\d{6}$/.test(code) || req.nextUrl.search !== "") {
    return NextResponse.json(
      { error: { code: "BAD_REQUEST", message: "종목코드는 6자리 숫자이며 쿼리 파라미터는 지원하지 않습니다." } },
      { status: 400, headers: { "Cache-Control": CACHE_ERR } }
    );
  }
  try {
    const analysis = await dedup(code);
    // 판정 기록은 부가 작업 — 실패해도 응답엔 영향 없게 격리(fail-safe). CDN 캐시 미스에서만 실행되므로
    // (code,day) 첫 계산 시 1회 적재되면 충분.
    try {
      await recordPhase(code, analysis);
    } catch (e) {
      console.error("[phase] 판정 KV 기록 실패(무시):", e);
    }
    return NextResponse.json(analysis, { headers: { "Cache-Control": CACHE_OK } });
  } catch (e) {
    if (e instanceof NotFoundError) {
      return NextResponse.json(
        { error: { code: "NOT_FOUND", message: "해당 코드의 종목을 찾을 수 없습니다." } },
        { status: 404, headers: { "Cache-Control": CACHE_ERR } }
      );
    }
    if (e instanceof UnreachableError) {
      return NextResponse.json(
        { error: { code: "NAVER_UNREACHABLE", message: "네이버 데이터 응답이 없습니다. 잠시 후 다시 시도해 주세요." } },
        { status: 502, headers: { "Cache-Control": CACHE_ERR } }
      );
    }
    if (e instanceof AiConfigError || e instanceof AiUnavailableError) {
      return NextResponse.json(
        { error: { code: "AI_UNAVAILABLE", message: "AI 판정을 일시적으로 사용할 수 없습니다. 잠시 후 다시 시도해 주세요." } },
        { status: 503, headers: { "Cache-Control": CACHE_ERR } }
      );
    }
    return NextResponse.json(
      { error: { code: "INTERNAL_ERROR", message: "AI 판정 중 오류가 발생했습니다." } },
      { status: 500 }
    );
  }
}
