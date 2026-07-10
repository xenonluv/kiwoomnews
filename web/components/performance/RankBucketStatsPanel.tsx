"use client";

import { useState } from "react";

import type {
  RankBucketStatCell,
  RankBucketStats,
  RankBucketStatsForward,
  RankBucketStatsRetro,
  RankPrior,
} from "@/types/performance";

function pct(v: number | null, suffix = "%") {
  if (v == null) return "—";
  return `${v}${suffix}`;
}

function signedPct(v: number | null) {
  if (v == null) return "—";
  return `${v > 0 ? "+" : ""}${v}%`;
}

function Rate({ value }: { value: number | null }) {
  if (value == null) return <span className="text-muted-foreground">—</span>;
  return (
    <span
      className={`font-semibold ${
        value >= 70 ? "text-up" : value < 50 ? "text-down" : "text-muted-foreground"
      }`}
    >
      {value}%
    </span>
  );
}

function Rows({ rows, idHeader }: { rows: RankBucketStatCell[]; idHeader: string }) {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-[900px] text-sm">
        <thead>
          <tr className="text-left text-xs text-muted-foreground">
            <th className="pb-2 font-medium">{idHeader}</th>
            <th className="pb-2 font-medium">조건</th>
            <th className="pb-2 text-right font-medium">+7%</th>
            <th className="pb-2 text-right font-medium">Wilson</th>
            <th className="pb-2 text-right font-medium">평균고가</th>
            <th className="pb-2 text-right font-medium">중앙고가</th>
            <th className="pb-2 text-right font-medium">최저고가</th>
            <th className="pb-2 text-right font-medium">종가평균</th>
            <th className="pb-2 text-right font-medium">표본</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.bucket ?? r.shadow}-${r.band}`} className="border-t border-white/5">
              <td className="py-1.5 font-medium tabular-nums">
                {r.bucket != null ? `B${r.bucket}` : r.shadow}
              </td>
              <td className="py-1.5 text-muted-foreground">{r.band}</td>
              <td className="py-1.5 text-right tabular-nums">
                <Rate value={r.touch7_rate} />
              </td>
              <td className="py-1.5 text-right tabular-nums text-muted-foreground">
                {pct(r.wilson7_lower)}
              </td>
              <td className="py-1.5 text-right tabular-nums">{signedPct(r.avg_high)}</td>
              <td className="py-1.5 text-right tabular-nums">{signedPct(r.median_high)}</td>
              <td className="py-1.5 text-right tabular-nums">{signedPct(r.min_high)}</td>
              <td className="py-1.5 text-right tabular-nums">{signedPct(r.avg_return)}</td>
              <td className="py-1.5 text-right tabular-nums text-muted-foreground">
                <span>{r.n}/{r.unique_n}</span>
                {!r.valid && <span className="ml-1 text-warning">수집 중</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type PopulationOption = { key: string; label: string; data: RankBucketStats };

function retroPopulations(data: RankBucketStatsRetro): PopulationOption[] {
  return [
    { key: "all", label: "전체 소급", data: data.exclusive_all },
    ...(data.exclusive_final
      ? [{ key: "final", label: "기존 final", data: data.exclusive_final }]
      : []),
    { key: "krx_decision", label: "KRX 15:18", data: data.exclusive_krx_decision },
    { key: "nxt_decision", label: "NXT 19:50", data: data.exclusive_nxt_decision },
    { key: "eod", label: "운영 EOD", data: data.exclusive_eod },
    { key: "dropout", label: "마감 탈락", data: data.dropout },
  ];
}

function forwardPopulations(data: RankBucketStatsForward): PopulationOption[] {
  return [
    { key: "krx_decision", label: "KRX 15:18", data: data.krx_decision },
    { key: "nxt_decision", label: "NXT 19:50", data: data.nxt_decision },
    { key: "eod", label: "운영 EOD", data: data.eod },
    ...(data.final ? [{ key: "final", label: "기존 final", data: data.final }] : []),
    { key: "dropout", label: "마감 탈락", data: data.dropout },
  ];
}

export function RankBucketStatsPanel({
  data,
  prior,
  retro,
  forward,
}: {
  data?: RankBucketStats;
  prior?: RankPrior;
  retro?: RankBucketStatsRetro;
  forward?: RankBucketStatsForward;
}) {
  const [basis, setBasis] = useState<"forward" | "retro">(forward ? "forward" : "retro");
  const [population, setPopulation] = useState("eod");
  const availableBasis = Boolean(forward && retro);
  const populations = basis === "forward" && forward
    ? forwardPopulations(forward)
    : retro
      ? retroPopulations(retro)
      : data
        ? [{ key: "legacy", label: "기존 소급", data }]
        : [];
  const active = populations.find((item) => item.key === population) ?? populations[0];
  const killSwitches = basis === "forward" ? forward?.kill_switches ?? [] : [];

  if (!active) return null;
  const sampleN = active.data.sample_n ?? active.data.cells.reduce((sum, cell) => sum + cell.n, 0);
  const hasShadowSamples = active.data.shadow_cells.some((cell) => cell.n > 0);

  return (
    <section className="rounded-lg border border-white/10 bg-white/[0.03] p-4">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">정렬4 bucket 성과</h3>
          <p className="mt-1 text-[11px] text-muted-foreground">
            {prior
              ? `${prior.model_version} · ${prior.source} prior · 자동 재정렬 없음`
              : "현재 정책 소급 통계와 저장된 신호시점 전진 통계를 분리합니다."}
          </p>
        </div>
        {availableBasis && (
          <div className="inline-flex overflow-hidden rounded-md border border-white/10 text-xs">
            <button
              type="button"
              className={`px-3 py-1.5 ${basis === "forward" ? "bg-white/10 text-foreground" : "text-muted-foreground"}`}
              onClick={() => setBasis("forward")}
            >
              전진
            </button>
            <button
              type="button"
              className={`border-l border-white/10 px-3 py-1.5 ${basis === "retro" ? "bg-white/10 text-foreground" : "text-muted-foreground"}`}
              onClick={() => setBasis("retro")}
            >
              소급
            </button>
          </div>
        )}
      </div>

      <div className="mb-3 flex flex-wrap gap-x-1 border-b border-white/10">
        {populations.map((item) => (
          <button
            type="button"
            key={item.key}
            onClick={() => setPopulation(item.key)}
            className={`border-b-2 px-2 py-1.5 text-xs ${
              active.key === item.key
                ? "border-foreground font-medium text-foreground"
                : "border-transparent text-muted-foreground"
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>

      <p className="mb-3 text-[11px] text-muted-foreground">
        {basis === "forward"
          ? `저장된 신호 bucket만 사용 · 발효 ${forward?.effective_from ?? "—"} · ${sampleN}건`
          : `현재 규칙 재분류 참고값 · 실제 forward 상신에 사용하지 않음 · ${sampleN}건`}
      </p>
      <Rows rows={active.data.cells} idHeader="Bucket" />

      {killSwitches.length > 0 && active.key === "eod" && (
        <div className="mt-4 border-t border-white/10 pt-3">
          <p className="mb-2 text-xs font-semibold">Forward 자동판정 · 수동상신</p>
          <ul className="space-y-1 text-[11px] text-muted-foreground">
            {killSwitches.map((item) => (
              <li key={item.key} className="flex items-start justify-between gap-3">
                <span>{item.label} · n={item.n ?? 0}</span>
                <span className={item.status.includes("하향") ? "text-down" : item.status.includes("재승격") ? "text-up" : ""}>
                  {item.status}
                  {item.reasons.length > 0 && ` · ${item.reasons.join(", ")}`}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {hasShadowSamples && (
        <div className="mt-4 border-t border-white/10 pt-3">
          <p className="mb-2 text-xs font-semibold">Shadow bucket</p>
          <Rows rows={active.data.shadow_cells.filter((cell) => cell.n > 0)} idHeader="Shadow" />
        </div>
      )}
    </section>
  );
}
