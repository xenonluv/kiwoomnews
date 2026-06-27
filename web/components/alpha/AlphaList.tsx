"use client";

import { useEffect, useRef, useState } from "react";
import type { AlphaData, AlphaMover, AlphaCalibCell } from "@/types/alpha";

const POLL_MS = 60_000;

function pct(v: number | null | undefined, digits = 1) {
  return v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
}
function chgClass(v: number | null | undefined) {
  if (v == null) return "text-muted-foreground";
  return v > 0 ? "text-up" : v < 0 ? "text-down" : "text-muted-foreground";
}
function inst(m: AlphaMover) {
  if (m.frgn_net == null && m.orgn_net == null) return "수급 n/a";
  const v = (m.frgn_net ?? 0) + (m.orgn_net ?? 0);
  return `외인+기관 ${v > 0 ? "+" : ""}${v.toLocaleString()}`;
}

function CalibCellRow({ label, c }: { label: string; c?: AlphaCalibCell }) {
  return (
    <div className="flex items-center justify-between gap-2 text-xs tabular-nums">
      <span className="text-muted-foreground">{label}</span>
      {!c || c.n === 0 ? (
        <span className="text-muted-foreground">관찰중 (n=0)</span>
      ) : (
        <span>
          <span className={c.hit_rate != null && c.hit_rate >= 50 ? "text-up" : "text-down"}>
            익일상승 {c.hit_rate}%
          </span>{" "}
          · 평균 {pct(c.avg_return, 2)} · n={c.n}{" "}
          <span className={c.valid ? "text-foreground" : "text-warning"}>
            {c.valid ? "" : "(관찰중)"}
          </span>
        </span>
      )}
    </div>
  );
}

function CalibrationPanel({ data }: { data: AlphaData }) {
  const cal = data.calibration;
  if (!cal) {
    return (
      <div className="rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">
        전진검증 데이터 적재 중 — 익일 라벨이 쌓이면 셋업별 실측 익일확률이 표시됩니다(관찰중).
      </div>
    );
  }
  const byT = cal.by_turnover2d_eumbong ?? {};
  const byS = cal.by_spark_eumbong_hi_turnover ?? {};
  return (
    <div className="space-y-3 rounded-lg border border-white/10 bg-white/[0.03] p-4">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-bold">📊 전진검증 (실측 익일확률)</h2>
        <span className="text-xs text-muted-foreground">
          라벨표본 {cal.total_labeled} · min_n {cal.min_n}
        </span>
      </div>
      <div>
        <p className="mb-1 text-xs font-medium text-muted-foreground">음봉 · 2일 유통회전율별</p>
        <div className="space-y-1">
          {Object.entries(byT).map(([k, c]) => (
            <CalibCellRow key={k} label={`회전율 ${k}%`} c={c} />
          ))}
        </div>
      </div>
      <div>
        <p className="mb-1 text-xs font-medium text-muted-foreground">음봉 + 고회전(200%+) · 14:30 스파크별</p>
        <div className="space-y-1">
          {Object.entries(byS).map(([k, c]) => (
            <CalibCellRow key={k} label={`스파크 ${k}`} c={c} />
          ))}
        </div>
      </div>
      {cal.llm && (
        <p className="text-xs text-muted-foreground">
          LLM 판단 보정: n={cal.llm.n} · Brier {cal.llm.brier}
        </p>
      )}
      <p className="text-[11px] text-warning">{cal.note}</p>
    </div>
  );
}

function MoverCard({ m }: { m: AlphaMover }) {
  const danger = m.redteam_flag || (m.manipulation_risk ?? 0) >= 0.6;
  return (
    <div className="space-y-2 rounded-lg border border-white/10 bg-white/[0.045] p-4">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-lg font-bold tracking-tight">
          {m.name}
          {m.date && (
            <span className="ml-1.5 text-[10px] font-normal text-muted-foreground tabular-nums">
              {m.date.length === 8 ? `${m.date.slice(4, 6)}/${m.date.slice(6)}` : m.date}
            </span>
          )}
        </h3>
        <span className={`text-sm font-semibold tabular-nums ${chgClass(m.change_pct)}`}>
          {pct(m.change_pct)} {m.is_eumbong ? "음봉" : ""}
        </span>
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs tabular-nums text-muted-foreground">
        <span>회전2d <b className="text-foreground">{m.turnover_2d_pct ?? "—"}%</b></span>
        <span>종가강도 {m.close_strength ?? "—"}</span>
        <span>14:30스파크 <b className="text-foreground">{m.spark_1430_count ?? "—"}</b></span>
        <span>{inst(m)}</span>
        <span>키움 {m.kiwoom_buy_concentration != null ? Math.round(m.kiwoom_buy_concentration * 100) + "%" : "—"}</span>
      </div>
      {m.catalyst && (
        <p className="text-xs">
          <span className="text-muted-foreground">왜↑ </span>
          {m.catalyst}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        {m.prob_up != null && (
          <span className="rounded bg-white/5 px-1.5 py-0.5">
            익일확률 {Math.round((m.prob_up ?? 0) * 100)}%
            {m.confidence != null ? ` · conf ${Math.round((m.confidence ?? 0) * 100)}%` : ""}
          </span>
        )}
        {danger && <span className="rounded bg-warning/15 px-1.5 py-0.5 text-warning">⚠ 작전/조작 의심</span>}
        {m.labeled && m.hit != null && (
          <span className={`rounded px-1.5 py-0.5 ${m.hit ? "bg-up/15 text-up" : "bg-down/15 text-down"}`}>
            익일 {m.hit ? "↑" : "↓"} {pct(m.next_return_pct, 1)}
          </span>
        )}
      </div>
      <a href={`/stock/${m.code}`} className="block text-[11px] text-muted-foreground hover:text-foreground">
        {m.code} · 상세 분석 →
      </a>
    </div>
  );
}

export function AlphaList({ initial }: { initial: AlphaData }) {
  const [data, setData] = useState<AlphaData>(initial);
  const last = useRef(JSON.stringify(initial));
  useEffect(() => {
    let alive = true;
    async function refresh() {
      try {
        const r = await fetch("/api/alpha", { cache: "no-store" });
        if (!r.ok || !alive) return;
        const next = (await r.json()) as AlphaData;
        const s = JSON.stringify(next);
        if (s !== last.current) {
          last.current = s;
          setData(next);
        }
      } catch {
        /* 폴링 실패 무시 */
      }
    }
    const id = setInterval(refresh, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const movers = data.movers ?? [];
  return (
    <div className="space-y-6">
      <p className="text-xs text-muted-foreground tabular-nums">
        기준 {data.date ?? "—"} · 갱신 {data.generated_at} · {movers.length}종목
      </p>
      <CalibrationPanel data={data} />
      {movers.length === 0 ? (
        <div className="flex min-h-40 items-center justify-center rounded-lg border border-dashed border-border text-sm text-muted-foreground">
          아직 적재된 알파 movers가 없습니다 — 거래일 마감 후 수집됩니다.
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {movers.map((m, i) => (
            <MoverCard key={`${m.code}-${m.file_date ?? m.date ?? i}`} m={m} />
          ))}
        </div>
      )}
      <p className="text-xs text-warning">{data.disclaimer}</p>
    </div>
  );
}
