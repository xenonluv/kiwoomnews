import Link from "next/link";
import { ArrowRight } from "lucide-react";

import { getRadar } from "@/lib/radar/repository";
import { getAutoTradePerformance } from "@/lib/performance/repository";
import { LiveRadar } from "@/components/radar/LiveRadar";
import { AutoTradeHero } from "@/components/autotrade/AutoTradeHero";
import { SearchBox } from "@/components/stock/SearchBox";

// 정적 생성: 데이터는 radar.json(빌드 시 import)에서 오므로 배포마다 갱신된다.
// 방문자는 CDN 정적 페이지를 받으므로 동시접속이 늘어도 서버 함수 호출이 없다.
// 열린 탭의 실시간 갱신은 LiveRadar(클라이언트 폴링)가 담당한다.

export default function Home() {
  const radar = getRadar();
  const autotrade = getAutoTradePerformance();

  return (
    <main className="container py-12">
      <header className="mb-8 space-y-1">
        <h1 className="text-3xl font-bold tracking-tight">이벤트 매집 레이더</h1>
        <p className="text-sm text-muted-foreground">
          10일 내 이벤트를 앞두고, 당일 큰돈이 들어와 급등 후 식은 — 매집이 의심되는
          종목을 자동 탐지 ·
          <span className="text-warning"> 투자 참고용, 매수 추천 아님</span>
        </p>
      </header>

      {/* 자동매매 실전 성과 — 홈 최상단(맨 위) 강조 배치 */}
      <section className="mb-8">
        <AutoTradeHero data={autotrade} />
      </section>

      {/* 종목 검색 — 이름/코드 입력 시 온디맨드 분석 리포트(/stock/[code])로 이동 */}
      <section className="mb-8">
        <SearchBox className="max-w-xl" />
        <p className="mt-1.5 text-xs text-muted-foreground">
          종목명이나 코드를 검색하면 재무·수급·기술지표·재료뉴스를 종합한 분석
          리포트를 바로 생성합니다.
        </p>
      </section>

      <div className="mb-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <Link
          href="/forecast"
          className="flex items-center justify-between gap-3 rounded-lg border border-[rgba(242,54,69,0.35)] bg-gradient-to-br from-[rgba(242,54,69,0.16)] to-[rgba(255,255,255,0.03)] px-5 py-4 backdrop-blur-xl transition-shadow hover:shadow-[0_0_28px_2px_rgba(242,54,69,0.35)]"
        >
          <div>
            <p className="text-base font-bold">🔥 당일 폭발 종목</p>
            <p className="text-xs text-muted-foreground">
              고가 +22% AND 유통주식 90%+ 회전한 폭발 종목 보기
            </p>
          </div>
          <ArrowRight className="size-5 shrink-0 text-up" aria-hidden />
        </Link>
        <Link
          href="/youtong"
          className="flex items-center justify-between gap-3 rounded-lg border border-[rgba(245,158,11,0.35)] bg-gradient-to-br from-[rgba(245,158,11,0.14)] to-[rgba(255,255,255,0.03)] px-5 py-4 backdrop-blur-xl transition-shadow hover:shadow-[0_0_28px_2px_rgba(245,158,11,0.32)]"
        >
          <div>
            <p className="text-base font-bold">⚡ 곧 폭발할 후보</p>
            <p className="text-xs text-muted-foreground">
              09:30↑ 현재 +7% · 유통 50%+ · 5분봉 분출 — 위로 올라오는 종목 보기
            </p>
          </div>
          <ArrowRight className="size-5 shrink-0 text-warning" aria-hidden />
        </Link>
        <Link
          href="/performance"
          className="flex items-center justify-between gap-3 rounded-lg border border-[rgba(242,54,69,0.35)] bg-gradient-to-br from-[rgba(242,54,69,0.12)] to-[rgba(255,255,255,0.03)] px-5 py-4 backdrop-blur-xl transition-shadow hover:shadow-[0_0_28px_2px_rgba(242,54,69,0.3)]"
        >
          <div>
            <p className="text-base font-bold">📈 성과 검증 · 자가 개선</p>
            <p className="text-xs text-muted-foreground">
              레이더가 실제로 맞췄는지 매일 채점 — 적중률 추세 보기
            </p>
          </div>
          <ArrowRight className="size-5 shrink-0 text-up" aria-hidden />
        </Link>
      </div>

      <LiveRadar initial={radar} />
    </main>
  );
}
