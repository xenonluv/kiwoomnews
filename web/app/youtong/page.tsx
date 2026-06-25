import Link from "next/link";
import type { Metadata } from "next";
import { ArrowLeft } from "lucide-react";

import { getYoutong, getYoutongThresholds } from "@/lib/radar/repository";
import { YoutongList } from "@/components/youtong/YoutongList";

export const dynamic = "force-static";

export const metadata: Metadata = {
  title: "곧 폭발할 후보 · 위로 올라오며 분출",
  description:
    "10:30 이후 현재 등락률 +7% 이상 · 유통주식 회전율 50%+ · 5분봉 양봉 분출(스파크)이 뜬 종목(폭발 직전). 종일 유지·표시 참고용.",
};

export default function YoutongPage() {
  const youtong = getYoutong();
  const th = getYoutongThresholds();
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
        <h1 className="text-3xl font-bold tracking-tight">곧 폭발할 후보 ⚡</h1>
        <p className="text-sm text-muted-foreground">
          {th.startHhmm.length === 4 ? `${th.startHhmm.slice(0, 2)}:${th.startHhmm.slice(2)}` : th.startHhmm} 이후{" "}
          <span className="text-up">현재 등락률 +{th.changePct}% 이상</span> ·{" "}
          <span className="text-warning">유통주식 회전율 {th.turnoverMin}%+</span> ·{" "}
          <span className="text-up">5분봉 양봉 분출(스파크)</span>이 뜬 종목(폭발 직전, 위로 올라오는 중) ·
          한 번 뜨면 종일 유지 ·
          <span className="text-warning"> 표시·참고용, 매수 추천 아님</span>
        </p>
      </header>
      <YoutongList initial={youtong} thresholds={th} />
    </main>
  );
}
