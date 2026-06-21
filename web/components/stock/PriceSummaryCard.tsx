import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { PriceSection } from "@/types/stock";
import { cn } from "@/lib/utils";

const fmt = (n: number | null, suffix = "") =>
  n === null ? "—" : `${n.toLocaleString("ko-KR")}${suffix}`;

/** 주가현황 — 현재가·등락·밸류에이션·52주 위치·컨센서스 목표가. */
export function PriceSummaryCard({ price }: { price: PriceSection }) {
  const upDay = price.changePct > 0;
  const flat = price.changePct === 0;
  const rows: { label: string; value: string }[] = [
    { label: "시가총액", value: price.marketCap ?? "—" },
    {
      label: "거래대금(통합)",
      value: price.tradingValue === null ? "—" : `${Math.round(price.tradingValue).toLocaleString("ko-KR")}억`,
    },
    { label: "PER / 컨센서스", value: `${fmt(price.per, "배")} / ${fmt(price.cnsPer, "배")}` },
    { label: "PBR", value: fmt(price.pbr, "배") },
    { label: "EPS", value: fmt(price.eps, "원") },
    { label: "배당수익률", value: fmt(price.dividendYield, "%") },
    { label: "외국인 보유율", value: fmt(price.foreignRate, "%") },
    {
      label: "52주 고가 대비",
      value: price.pctFrom52High === null ? "—" : `${price.pctFrom52High}% (${fmt(price.high52, "원")})`,
    },
    {
      label: "52주 저가 대비",
      value: price.pctFrom52Low === null ? "—" : `+${price.pctFrom52Low}% (${fmt(price.low52, "원")})`,
    },
  ];

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-lg">주가 현황</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-baseline gap-3">
          <span className="text-3xl font-bold tabular-nums">{fmt(price.close, "원")}</span>
          <span
            className={cn(
              "text-sm font-semibold tabular-nums",
              flat ? "text-muted-foreground" : upDay ? "text-up" : "text-down"
            )}
          >
            {upDay ? "▲" : flat ? "" : "▼"} {Math.abs(price.change).toLocaleString("ko-KR")}원 (
            {price.changePct > 0 ? "+" : ""}
            {price.changePct}%)
          </span>
        </div>

        <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs sm:grid-cols-4">
          {rows.map((r) => (
            <div key={r.label} className="flex flex-col">
              <dt className="text-muted-foreground">{r.label}</dt>
              <dd className="font-medium tabular-nums">{r.value}</dd>
            </div>
          ))}
        </dl>

        {price.consensus && (
          <div className="flex flex-wrap items-center gap-2 rounded-md border border-white/10 bg-white/[0.03] px-3 py-2 text-xs">
            <Badge variant={price.consensus.upsidePct >= 0 ? "up" : "down"}>
              애널리스트 컨센서스
            </Badge>
            <span>
              목표주가 <b className="tabular-nums">{fmt(price.consensus.targetPrice, "원")}</b>
            </span>
            <span className={price.consensus.upsidePct >= 0 ? "text-up" : "text-down"}>
              여력 {price.consensus.upsidePct > 0 ? "+" : ""}
              {price.consensus.upsidePct}%
            </span>
            <span className="text-muted-foreground">
              투자의견 {price.consensus.recommMean}/5 · {price.consensus.date}
            </span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
