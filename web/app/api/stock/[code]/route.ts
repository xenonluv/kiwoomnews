import { NextRequest, NextResponse } from "next/server";

import { buildStockReport, NotFoundError, UnreachableError } from "@/lib/stock/report";
import type { StockReport } from "@/types/stock";

// 온디맨드 종목 분석 — 요청 시 네이버 공개 API를 병렬 호출해 룰베이스 리포트 생성.
// 남용 방어 3중: ① 성공 180초 + 에러(400/404/502) 30초 네거티브 CDN 캐시
// ② 쿼리스트링 차단(캐시 키 분기로 캐시 우회하는 것을 막음 — 네이버 호출 전 400)
// ③ 같은 코드 동시 요청은 인스턴스 내 in-flight 디둡으로 네이버 호출 1회에 합침.
const CACHE_OK = "public, s-maxage=180, stale-while-revalidate=600";
const CACHE_ERR = "public, s-maxage=30, stale-while-revalidate=60";

export const dynamic = "force-dynamic";

const inflight = new Map<string, Promise<StockReport>>();

function getReportDeduped(code: string): Promise<StockReport> {
  let p = inflight.get(code);
  if (!p) {
    p = buildStockReport(code).finally(() => inflight.delete(code));
    inflight.set(code, p);
  }
  return p;
}

/**
 * GET /api/stock/{code}
 * 외부 공개 · 읽기 전용. 종목 분석 리포트
 * (주가현황·기술·수급·재무·재료뉴스·이벤트·판정). 시크릿 미사용.
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
    const report = await getReportDeduped(code);
    return NextResponse.json(report, { headers: { "Cache-Control": CACHE_OK } });
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
    return NextResponse.json(
      { error: { code: "INTERNAL_ERROR", message: "리포트 생성 중 오류가 발생했습니다." } },
      { status: 500 }
    );
  }
}
