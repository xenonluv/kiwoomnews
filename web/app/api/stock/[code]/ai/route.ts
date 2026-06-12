import { NextRequest, NextResponse } from "next/server";

import { AiConfigError, AiUnavailableError, buildAiAnalysis } from "@/lib/stock/ai";
import { NotFoundError, UnreachableError } from "@/lib/stock/report";
import type { AiAnalysis } from "@/types/stock";

// AI(LLM) 심층 분석 — 버튼 클릭 시에만 호출되는 온디맨드 엔드포인트.
// LLM 호출은 건당 비용이 있으므로 방어를 더 길게 가져간다:
// ① 성공 30분 CDN 캐시 (같은 종목은 전 세계에서 30분에 1회만 Kimi 호출)
// ② 에러 60초 네거티브 캐시 (키 장애 시 Kimi 반복 호출 차단)
// ③ 쿼리스트링 차단(캐시 키 분기 우회 방지) + 같은 코드 동시 요청 in-flight 디둡.
// 추가 IP 레이트리밋은 외부 상태(KV)가 필요해 현 규모에선 과설계 — 필요 시 도입.
const CACHE_OK = "public, s-maxage=1800, stale-while-revalidate=3600";
const CACHE_ERR = "public, s-maxage=60, stale-while-revalidate=120";

export const dynamic = "force-dynamic";
// kimi-k2.6(reasoning)은 15~60초+ 소요 — Vercel 함수 한도 상향
// (Fluid Compute에서 Hobby도 300초 허용. 미지원 환경이면 Vercel이 플랜 한도로 클램프)
export const maxDuration = 300;

const inflight = new Map<string, Promise<AiAnalysis>>();

function getAnalysisDeduped(code: string): Promise<AiAnalysis> {
  let p = inflight.get(code);
  if (!p) {
    p = buildAiAnalysis(code).finally(() => inflight.delete(code));
    inflight.set(code, p);
  }
  return p;
}

/**
 * GET /api/stock/{code}/ai
 * 외부 공개 · 읽기 전용. Kimi LLM이 룰베이스 리포트 전체를 읽고
 * 익일 상승 확률(prob_up — N샘플 병렬 호출의 중앙값, self-consistency)을 추정.
 * 방향(상승/하락/관망)은 확률에서 코드로 파생해 근거와 함께 구조화 반환.
 * 시크릿: MOONSHOT_API_KEY (서버 온리). 샘플 수: MOONSHOT_SAMPLES(기본 3).
 */
export async function GET(
  req: NextRequest,
  { params }: { params: { code: string } }
) {
  const code = params.code;
  if (!/^\d{6}$/.test(code) || req.nextUrl.search !== "") {
    return NextResponse.json(
      { error: { code: "BAD_REQUEST", message: "종목코드는 6자리 숫자이며 쿼리 파라미터는 지원하지 않습니다." } },
      { status: 400, headers: { "Cache-Control": CACHE_ERR } }
    );
  }
  try {
    const analysis = await getAnalysisDeduped(code);
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
    if (e instanceof AiConfigError) {
      // 환경변수 미설정 — 운영 진단용으로 코드만 구분 (메시지는 동일하게 중립적으로)
      return NextResponse.json(
        { error: { code: "AI_NOT_CONFIGURED", message: "AI 분석을 일시적으로 사용할 수 없습니다. 잠시 후 다시 시도해 주세요." } },
        { status: 503, headers: { "Cache-Control": CACHE_ERR } }
      );
    }
    if (e instanceof AiUnavailableError) {
      // 업스트림 오류 상세는 응답에 싣지 않고 서버 로그로만 남긴다 (ai.ts의 console.error)
      return NextResponse.json(
        { error: { code: "AI_UNAVAILABLE", message: "AI 분석을 일시적으로 사용할 수 없습니다. 잠시 후 다시 시도해 주세요." } },
        { status: 503, headers: { "Cache-Control": CACHE_ERR } }
      );
    }
    return NextResponse.json(
      { error: { code: "INTERNAL_ERROR", message: "AI 분석 중 오류가 발생했습니다." } },
      { status: 500 }
    );
  }
}
