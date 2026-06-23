import Link from "next/link";
import type { Metadata } from "next";
import { ArrowLeft } from "lucide-react";

import { getExplosions, getExplosionThresholds } from "@/lib/radar/repository";
import { ExplosionList } from "@/components/forecast/ExplosionList";

export const dynamic = "force-static";

export const metadata: Metadata = {
  title: "당일 폭발 종목",
  description: "당일 고가 +22% 이상 AND 거래량이 유통주식수의 90% 이상 회전한 폭발 종목. 표시·참고용.",
};

export default function ForecastPage() {
  const explosions = getExplosions();
  const th = getExplosionThresholds();
  return (
    <main className="container py-12">
      <Link
        href="/"
        className="mb-6 inline-flex items-center gap-1 text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="size-4" aria-hidden />
        실시간 레이더로
      </Link>
      <header className="mb-6 space-y-1">
        <h1 className="text-3xl font-bold tracking-tight">당일 폭발 종목 🔥</h1>
        <p className="text-sm text-muted-foreground">
          당일 <span className="text-up">고가 등락률 +{th.highPct}% 이상</span> 그리고{" "}
          <span className="text-up">거래량이 유통주식수의 {th.volTurnover}% 이상</span> 회전한 종목 ·
          <span className="text-warning"> 큰돈이 들어온 자명한 폭발(표시·참고용, 매수 추천 아님)</span>
        </p>
      </header>
      <ExplosionList initial={explosions} thresholds={th} />
    </main>
  );
}
