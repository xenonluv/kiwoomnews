import { ExternalLink, Newspaper } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { NewsItem } from "@/types/signal";

/** 재료 감성 → 뱃지 (한국 색 관례: 호재=빨강 up, 악재=파랑 down) */
function sentimentBadge(sentiment?: string | null) {
  if (sentiment === "호재") return <Badge variant="up">호재</Badge>;
  if (sentiment === "악재") return <Badge variant="down">악재</Badge>;
  return null;
}

/**
 * 종목별 관련 뉴스 목록 — 카드 클릭 후 상세에서 노출.
 * 스크리너 재료필터 통과분(네이버 종목뉴스). 링크는 새 탭으로.
 */
export function NewsList({ news }: { news?: NewsItem[] }) {
  const items = (news ?? []).filter((n) => n.title);
  if (items.length === 0) return null;

  return (
    <section className="mt-6">
      <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-foreground">
        <Newspaper className="size-4" aria-hidden />
        관련 뉴스
        <span className="text-xs font-normal text-muted-foreground tabular-nums">
          {items.length}
        </span>
      </h3>
      <ul className="space-y-2">
        {items.map((n, i) => {
          const inner = (
            <div className="flex items-start gap-2 rounded-md border border-border bg-card/50 p-3 transition-colors hover:bg-card">
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium leading-snug text-foreground">
                  {n.title}
                  {n.url && (
                    <ExternalLink
                      className="ml-1 inline size-3 align-text-top text-muted-foreground"
                      aria-hidden
                    />
                  )}
                </p>
                <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                  {n.office && <span>{n.office}</span>}
                  {sentimentBadge(n.sentiment)}
                </div>
              </div>
            </div>
          );
          return (
            <li key={n.url ?? `${n.title}-${i}`}>
              {n.url ? (
                <a
                  href={n.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="block rounded-md focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {inner}
                </a>
              ) : (
                inner
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
