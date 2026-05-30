import Link from "next/link";
import { notFound } from "next/navigation";
import type { Metadata } from "next";
import { ArrowLeft } from "lucide-react";

import { signalService } from "@/services/signal.service";
import { SignalCard, toSignalCardProps } from "@/components/signal/SignalCard";
import { NewsList } from "@/components/signal/NewsList";

export const dynamic = "force-dynamic";

export async function generateMetadata({
  params,
}: {
  params: { post_id: string };
}): Promise<Metadata> {
  const signal = await signalService.getById(params.post_id);
  if (!signal) return { title: "시그널을 찾을 수 없음" };
  const title = `${signal.target_stock} ${signal.signal_probability} · ${signal.position_type}`;
  return {
    title,
    description: signal.headline,
    openGraph: {
      type: "article",
      title: `${title} — StockNews`,
      description: signal.headline,
      url: `/signals/${signal.post_id}`,
      publishedTime: signal.published_at,
    },
    twitter: { card: "summary", title, description: signal.headline },
  };
}

export default async function SignalDetailPage({
  params,
}: {
  params: { post_id: string };
}) {
  const signal = await signalService.getById(params.post_id);
  if (!signal) notFound();

  return (
    <main className="container max-w-2xl py-12">
      <Link
        href="/"
        className="mb-6 inline-flex items-center gap-1 text-sm text-muted-foreground transition-colors hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <ArrowLeft className="size-4" aria-hidden />
        목록으로
      </Link>
      <SignalCard {...toSignalCardProps(signal)} />
      <NewsList news={signal.news} />
    </main>
  );
}
