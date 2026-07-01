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

// 종가베팅 적합도 점수(0~100, 잠정 휴리스틱). 이번 전진검증 분석(22표본·2거래일) 근거 — 통계 확정 아님.
// 기준 50 + 가감: youtong/reaccum·적정회전(80~150%)·당일 0~+8%(또는 깊은 눌림)·14:30 스파크 1~2회는 가점,
// explosion(이미 폭발=식음)·극단/어중간 회전·이미 강세(+8~+20%)·스파크 3회+(과열)·숨은외인(역신호)은 감점.
function closeBetFitness(m: AlphaMover): { score: number; reasons: { k: string; v: number }[] } {
  const reasons: { k: string; v: number }[] = [];
  let s = 50;
  const add = (k: string, v: number) => {
    s += v;
    reasons.push({ k, v });
  };
  // ① mover 유형 — explosion은 익일종가 평균 -9.5%(맨 아래로), reaccum 최선
  if (m.mover_type === "reaccum") add("재매집", 10);
  else if (m.mover_type === "explosion") add("폭발(식음)", -45);
  else reasons.push({ k: "후보", v: 0 }); // youtong = 기준
  // ② 2일 누적 유통회전율 — 80~150%가 최적, 어중간 높음(150~250) 골짜기, 극단(250+) 약감점
  const t = m.turnover_2d_pct;
  if (t != null) {
    const v = t >= 80 && t < 150 ? 15 : t >= 40 && t < 80 ? 8 : t < 40 ? 3 : t < 250 ? -10 : -5;
    add(`회전${Math.round(t)}`, v);
  }
  // ③ 당일 등락률 — 0~+8% 최적(아직 안 터짐), 깊은 눌림(≤-10%) 반등, +8~+20% 최악(그날 다 오름)
  const c = m.change_pct;
  if (c != null) {
    const v = c >= 0 && c < 8 ? 15 : c <= -10 ? 12 : c < 0 ? 3 : c < 20 ? -15 : -5;
    add(`당일${c > 0 ? "+" : ""}${Math.round(c)}%`, v);
  }
  // ④ 14:30 스파크 — 1~2회 스윗스팟, 3회+ 과열(장중 털림). 미측정(none)은 판정 보류
  if (m.spark_source !== "none" && m.spark_1430_count != null) {
    const sc = m.spark_1430_count;
    add(`스파크${sc}`, sc >= 1 && sc <= 2 ? 12 : sc === 0 ? 2 : -8);
  }
  // ⑤ 숨은 외인매집 — 현 표본에선 역신호(소표본)
  if (hiddenForeign(m) >= 1) add("외인매집", -5);
  return { score: Math.max(0, Math.min(100, s)), reasons };
}

// 적합도 등급별 색(한국 관례: 빨강=좋음/강함). 높음=빨강·굵게 → 주황 → 앰버 → 회색(부적합·explosion).
function fitnessTier(score: number): { cls: string; label: string } {
  if (score >= 75) return { cls: "bg-up/20 text-up font-bold", label: "적합" };
  if (score >= 60) return { cls: "bg-orange-400/20 text-orange-300 font-semibold", label: "중간" };
  if (score >= 45) return { cls: "bg-amber-400/15 text-amber-300", label: "약" };
  return { cls: "bg-white/5 text-muted-foreground", label: "부적합" };
}

// 종베 적합도 정렬 순위 배지 색 (한국 관례: 빨강=상위). 1위 최강조 → 하위 회색.
function rankClass(rank: number): string {
  if (rank === 1) return "bg-up text-white font-bold";
  if (rank === 2) return "bg-orange-400/40 text-orange-100 font-bold";
  if (rank === 3) return "bg-amber-400/30 text-amber-100 font-semibold";
  if (rank <= 5) return "bg-white/10 text-foreground";
  return "bg-white/5 text-muted-foreground";
}

// mover 유형 한글 라벨 (표시용).
const MTYPE_LABEL: Record<string, string> = { reaccum: "재매집", youtong: "후보", explosion: "폭발" };

// '키움 속 숨은 외국인 매집' 흔적 강도(0=없음, 1~3 강). 정의: 투자자별 외국인 순매수(+)인데
// 외국계 창구 순매수는 거의 0(<외인순매수×10%) AND 키움 매수집중≥30% → 외국인이 외국계 창구를
// 안 거치고 키움 등 리테일 창구로 숨어 매집한 흔적(의심). 데이터 결측(null)이면 판정 안 함.
function hiddenForeign(m: AlphaMover): number {
  if (m.hidden_foreign_level != null) return m.hidden_foreign_level; // SSOT: quant 저장값 우선
  const fn = m.frgn_net,
    gq = m.glob_net_qty,
    kc = m.kiwoom_buy_concentration;
  if (fn == null || gq == null || kc == null) return 0; // 결측(옛 행) 재계산 fallback
  if (fn <= 0 || Math.abs(gq) >= Math.abs(fn) * 0.1 || kc < 0.3) return 0;
  return fn >= 100000 ? 3 : fn >= 30000 ? 2 : 1; // 외인 순매수 규모로 강도
}
const HF_BADGE: Record<number, string> = {
  1: "bg-orange-400/15 text-orange-300",
  2: "bg-orange-400/25 text-orange-300 font-medium",
  3: "bg-orange-400/40 text-orange-200 font-bold",
};

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
          · 평균 {pct(c.avg_return, 2)} · 고가 {pct(c.avg_high, 1)} · +7%터치{" "}
          <span className={c.touch7_rate != null && c.touch7_rate >= 50 ? "text-up" : ""}>
            {c.touch7_rate != null ? `${c.touch7_rate}%` : "—"}
          </span>{" "}
          · n={c.n}{" "}
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
  const bySC = cal.by_spark_count ?? {};
  const byHF = cal.by_hidden_foreign ?? {};
  const byCB = cal.by_combined_score ?? {};
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
      <div>
        <p className="mb-1 text-xs font-medium text-muted-foreground">14:30 스파크 횟수별 (전체)</p>
        <div className="space-y-1">
          {Object.entries(bySC).map(([k, c]) => (
            <CalibCellRow key={k} label={`스파크 ${k}`} c={c} />
          ))}
        </div>
      </div>
      <div>
        <p className="mb-1 text-xs font-medium text-muted-foreground">🕵 키움 속 외인매집</p>
        <div className="space-y-1">
          {Object.entries(byHF).map(([k, c]) => (
            <CalibCellRow key={k} label={k} c={c} />
          ))}
        </div>
      </div>
      <div>
        <p className="mb-1 text-xs font-medium text-muted-foreground">종합점수(스파크+외인매집)별 — 정렬 순위 검증</p>
        <div className="space-y-1">
          {Object.entries(byCB).map(([k, c]) => (
            <CalibCellRow key={k} label={`점수 ${k}`} c={c} />
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

function MoverCard({ m, rank }: { m: AlphaMover; rank?: number }) {
  const danger = m.redteam_flag || (m.manipulation_risk ?? 0) >= 0.6;
  const hf = hiddenForeign(m);
  const { score, reasons } = closeBetFitness(m);
  const tier = fitnessTier(score);
  const reasonText = reasons.map((r) => `${r.k}${r.v !== 0 ? ` ${r.v > 0 ? "+" : ""}${r.v}` : ""}`).join(" · ");
  return (
    <div className="space-y-2 rounded-lg border border-white/10 bg-white/[0.045] p-4">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="flex items-baseline gap-1.5 text-lg font-bold tracking-tight">
          {rank != null && (
            <span className={`shrink-0 rounded px-1.5 py-0.5 text-xs tabular-nums ${rankClass(rank)}`}>
              {rank}위
            </span>
          )}
          <span>{m.name}</span>
          {m.mover_type && (
            <span className="shrink-0 rounded bg-white/10 px-1 py-0.5 text-[10px] font-normal text-muted-foreground">
              {MTYPE_LABEL[m.mover_type] ?? m.mover_type}
            </span>
          )}
          {m.date && (
            <span className="text-[10px] font-normal text-muted-foreground tabular-nums">
              {m.date.length === 8 ? `${m.date.slice(4, 6)}/${m.date.slice(6)}` : m.date}
            </span>
          )}
        </h3>
        <div className="flex shrink-0 items-center gap-1.5">
          <span
            className={`rounded px-1.5 py-0.5 text-[11px] tabular-nums ${tier.cls}`}
            title={`종가베팅 적합도(잠정 휴리스틱) ${score}/100 · ${tier.label}\n${reasonText}`}
          >
            종베 {score}
          </span>
          <span className={`text-sm font-semibold tabular-nums ${chgClass(m.change_pct)}`}>
            {pct(m.change_pct)} {m.is_eumbong ? "음봉" : ""}
          </span>
        </div>
      </div>
      <div className="flex flex-wrap gap-x-1.5 text-[10px] tabular-nums text-muted-foreground">
        {reasons.map((r) => (
          <span key={r.k} className={r.v > 0 ? "text-up/80" : r.v < 0 ? "text-down/80" : ""}>
            {r.k}
            {r.v !== 0 ? ` ${r.v > 0 ? "+" : ""}${r.v}` : ""}
          </span>
        ))}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs tabular-nums text-muted-foreground">
        <span>회전2d <b className="text-foreground">{m.turnover_2d_pct ?? "—"}%</b></span>
        <span>종가강도 {m.close_strength ?? "—"}</span>
        <span>14:30스파크 <b className="text-foreground">{m.spark_source === "none" ? "— 미측정" : (m.spark_1430_count ?? "—")}</b></span>
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
        {m.provisional && <span className="rounded bg-warning/10 px-1.5 py-0.5 text-warning">🕒 장중 잠정</span>}
        {danger && <span className="rounded bg-warning/15 px-1.5 py-0.5 text-warning">⚠ 작전/조작 의심</span>}
        {hf > 0 && (
          <span
            className={`rounded px-1.5 py-0.5 ${HF_BADGE[hf]}`}
            title="투자자별 외국인 순매수(+)인데 외국계 창구 순매수는 0 → 외국인이 외국계 창구를 안 거치고 키움 등 리테일 창구로 숨어 매집한 흔적(의심·창구≠주체)"
          >
            🕵 키움 속 외인매집 +{Math.round((m.frgn_net ?? 0) / 1000)}k
          </span>
        )}
        {m.labeled && m.hit != null && (
          <span className={`rounded px-1.5 py-0.5 ${m.hit ? "bg-up/15 text-up" : "bg-down/15 text-down"}`}>
            익일 {m.hit ? "↑" : "↓"} {pct(m.next_return_pct, 1)}
            {m.next_high_pct != null ? ` · 고가 ${pct(m.next_high_pct, 1)}` : ""}
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

  // 종가베팅 적합도(잠정 휴리스틱) 내림차순 — explosion·과열(스파크3+)·극단/어중간 회전·이미강세(+8~20%)는
  // 하위로, youtong/reaccum·적정회전(80~150%)·당일 0~+8%·스파크 1~2는 상위로. 동점은 회전율이 스윗스팟
  // 중심(115%)에 가까운 순. ⚠ 22표본·2거래일 기반 잠정 — calibration 패널(combined_score 검증)과는 별개 축.
  const scored = (data.movers ?? []).map((m) => ({ m, fit: closeBetFitness(m).score }));
  scored.sort((a, b) =>
    b.fit !== a.fit
      ? b.fit - a.fit
      : Math.abs((a.m.turnover_2d_pct ?? 9999) - 115) - Math.abs((b.m.turnover_2d_pct ?? 9999) - 115),
  );
  const movers = scored.map((x) => x.m);
  return (
    <div className="space-y-6">
      <p className="text-xs text-muted-foreground tabular-nums">
        기준 {data.date ?? "—"} · 갱신 {data.generated_at} · {movers.length}종목 · 정렬=
        <span className="text-foreground">종가베팅 적합도</span>순(잠정 휴리스틱·22표본)
        {movers.some((m) => m.provisional) && (
          <span className="ml-2 rounded bg-warning/15 px-1.5 py-0.5 text-warning">🕒 장중 잠정(15:15 기준 · 마감 후 확정)</span>
        )}
      </p>
      <CalibrationPanel data={data} />
      {movers.length === 0 ? (
        <div className="flex min-h-40 items-center justify-center rounded-lg border border-dashed border-border text-sm text-muted-foreground">
          아직 적재된 알파 movers가 없습니다 — 거래일 마감 후 수집됩니다.
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {movers.map((m, i) => (
            <MoverCard key={`${m.code}-${m.file_date ?? m.date ?? i}`} m={m} rank={i + 1} />
          ))}
        </div>
      )}
      {(data.yesterday_results?.length ?? 0) > 0 && (
        <div className="space-y-3 border-t border-white/10 pt-5">
          <h2 className="text-sm font-bold">
            📋 어제
            {data.yesterday_date && data.yesterday_date.length === 8
              ? ` (${data.yesterday_date.slice(4, 6)}/${data.yesterday_date.slice(6)})`
              : ""}{" "}
            결과 → 익일
            <span className="ml-2 text-xs font-normal text-muted-foreground tabular-nums">
              익일상승 {data.yesterday_results!.filter((m) => m.hit).length}/{data.yesterday_results!.length} · 고가 +7%터치{" "}
              {data.yesterday_results!.filter((m) => (m.next_high_pct ?? 0) >= 7).length}/{data.yesterday_results!.length}
            </span>
          </h2>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {data.yesterday_results!.map((m, i) => (
              <MoverCard key={`y-${m.code}-${i}`} m={m} />
            ))}
          </div>
        </div>
      )}
      <p className="text-xs text-warning">{data.disclaimer}</p>
    </div>
  );
}
