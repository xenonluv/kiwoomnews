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

// 종가베팅 적합도 점수 v4 (0~100, 잠정 휴리스틱) — agent_alpha/fitness.py `close_bet_fitness` 와 1:1 동기화 필수.
// 2026-07-02 4각도 감사+2인 심판 "수정승인" 판결 반영(39표본·4거래일 — 순열검정 우연통과율 97.9%,
// 날짜내 순위상관≈0: 정밀 순위가 아니라 하위권(함정) 회피가 실효. ~07/25 표본 성숙 전 튜닝 동결).
// 기준 50 + 가감: 깊은눌림(≤-10%)·조용(0~+8%)·대금1000억↑·재매집 가점 /
// explosion(체결불가·갭 리스크)·이미 오른 놈(+8%↑)·유동성결핍·약스파크(찔끔 불꽃)·강마감·숨은외인 감점.
function closeBetFitness(m: AlphaMover): { score: number; reasons: { k: string; v: number }[] } {
  const reasons: { k: string; v: number }[] = [];
  let s = 50;
  const add = (k: string, v: number) => {
    s += v;
    reasons.push({ k, v });
  };
  // ① mover 유형 — explosion −50은 실행성(상한가류 종가 체결불가·익일 갭) 벌점, 고가터치 통계(67%=기저급) 아님.
  if (m.mover_type === "reaccum") add("재매집", 10);
  else if (m.mover_type === "explosion") add("폭발(추격불가)", -50);
  else reasons.push({ k: "후보", v: 0 }); // youtong = 기준
  // ② 유동성 결핍(통합) — 대금<50억 OR 2일회전<40% → 한 번만 −15 (씨피시스템 함정 차단, 이중처벌 금지)
  const v = m.value_eok;
  const t = m.turnover_2d_pct;
  if ((v != null && v < 50) || (t != null && t < 40)) add("유동성결핍", -15);
  // ③ 거래대금 대형 가점 — 실증 최강(날짜보정 +13.2%p·LODO 4/4)
  if (v != null && v >= 1000) add("대금1000억↑", 10);
  // ④ 당일 등락률 — ≤−10% 깊은눌림이 유일한 실증 가점(터치 100%). 8~15%는 실증 데드존(−41.7%p).
  //    +15%↑ 벌점은 실행성(종가 추격매수 불가·갭 위험 — 회장님 지시 2026-07-01) 근거.
  const c = m.change_pct;
  if (c != null) {
    const cv = c <= -10 ? 15 : c < 0 ? 8 : c < 8 ? 12 : c < 15 ? -20 : c < 22 ? -30 : -40;
    add(`당일${c > 0 ? "+" : ""}${Math.round(c)}%`, cv);
  }
  // ⑤ 약스파크 벌점 — 최대몸통 0<x<3% '찔끔 불꽃'=가짜 모멘텀(최견고 음신호 −16.8%p·LODO 0/4).
  const mx = m.spark_max_body_pct;
  if (mx != null && mx > 0 && mx < 3.0) add("약스파크", -8);
  // ⑤-2 강스파크(3%↑) 2회 이상 +8 — 회장님 지시 2026-07-02("고가를 먹는 프로그램"): 과거 4/4 익일 +7% 터치.
  //     저장값(spark_strong_count) 우선, 옛 행은 spark_bars로 재계산(fitness.py와 1:1). 1회·무스파크는 0.
  let ssc = m.spark_strong_count;
  if (ssc == null && m.spark_source != null && m.spark_source !== "none" && m.spark_bars != null) {
    ssc = m.spark_bars.filter((b) => (b.body_pct ?? 0) >= 3.0).length;
  }
  if (ssc != null && ssc >= 2) add("강스파크x2", 8);
  // ⑥ 강마감 — 연료 소진(실증 −4.5%p·코어 peak_ibs 방향 정합)
  if (m.close_strength != null && m.close_strength >= 0.6) add("강마감", -5);
  // ⑦ 숨은 외인매집 — 미약 역신호, 관찰축 재판정 전까지 유지
  if (hiddenForeign(m) >= 1) add("외인매집", -5);
  // ⑧ 폭락 제외(회장님 지시 2026-07-02) — 5연상 붕괴·연속하락 종목이 눌림 가점으로 상위 오는 것 차단.
  //    과확장붕괴: 6일 누적 +100%↑ & 당일 음수 −30 (금호건설형, 39표본 오폭 0건).
  //    연속하락: 종가 기준 4일 연속 하락 −15 (광주신세계형 — 승자군 최대 3일이라 4일이 안전선).
  const r6 = m.run_6d_pct;
  if (r6 != null && r6 >= 100 && c != null && c < 0) add("과확장붕괴", -30);
  if (m.down_streak != null && m.down_streak >= 4) add(`연속하락${m.down_streak}일`, -15);
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
// ⚠ 부적합(<45) 종목은 그날 풀이 약해 상대순위로 1~3위에 올라도 강조색 미적용(티어 우선 — 과신 방지, 감사 판결).
function rankClass(rank: number, score: number): string {
  if (score < 45) return "bg-white/5 text-muted-foreground";
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
  const byRank = cal.by_close_bet_rank ?? {};
  const byBand = cal.by_close_bet_band ?? {};
  const byChg = cal.by_change_pct ?? {};
  const byMT = cal.by_mover_type ?? {};
  const byVal = cal.by_value_band ?? {};
  const bySS = cal.by_spark_strength ?? {};
  const byLQ = cal.by_liquidity_deficit ?? {};
  return (
    <div className="space-y-3 rounded-lg border border-white/10 bg-white/[0.03] p-4">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-bold">📊 전진검증 (실측 익일확률)</h2>
        <span className="text-xs text-muted-foreground">
          라벨표본 {cal.total_labeled} · min_n {cal.min_n}
        </span>
      </div>
      {/* 🎯 종베 정렬 검증 — 현행 /alpha 정렬(종베 적합도)이 실제 익일 성과를 맞추는지 */}
      <div className="rounded-md bg-up/[0.04] p-2">
        <p className="mb-1 text-xs font-bold text-foreground">🎯 종가베팅 정렬 검증 (현행 순위축)</p>
        <div>
          <p className="mb-0.5 text-[11px] font-medium text-muted-foreground">종베 순위별 (1위/2위/…)</p>
          <div className="space-y-1">
            {Object.entries(byRank).map(([k, c]) => (
              <CalibCellRow key={k} label={k} c={c} />
            ))}
          </div>
        </div>
        <div className="mt-2">
          <p className="mb-0.5 text-[11px] font-medium text-muted-foreground">종베 적합도 점수대별</p>
          <div className="space-y-1">
            {Object.entries(byBand).map(([k, c]) => (
              <CalibCellRow key={k} label={k} c={c} />
            ))}
          </div>
        </div>
        <div className="mt-2">
          <p className="mb-0.5 text-[11px] font-medium text-muted-foreground">당일 등락률별 (0~+8% 최적 가설)</p>
          <div className="space-y-1">
            {Object.entries(byChg).map(([k, c]) => (
              <CalibCellRow key={k} label={`당일 ${k}%`} c={c} />
            ))}
          </div>
        </div>
        <div className="mt-2">
          <p className="mb-0.5 text-[11px] font-medium text-muted-foreground">mover 유형별</p>
          <div className="space-y-1">
            {Object.entries(byMT).map(([k, c]) => (
              <CalibCellRow key={k} label={k} c={c} />
            ))}
          </div>
        </div>
        <div className="mt-2">
          <p className="mb-0.5 text-[11px] font-medium text-muted-foreground">거래대금별 (v4 ≥1000억 가점 검증)</p>
          <div className="space-y-1">
            {Object.entries(byVal).map(([k, c]) => (
              <CalibCellRow key={k} label={k} c={c} />
            ))}
          </div>
        </div>
        <div className="mt-2">
          <p className="mb-0.5 text-[11px] font-medium text-muted-foreground">스파크 세기별 (무/약/강 — 서열 관찰중)</p>
          <div className="space-y-1">
            {Object.entries(bySS).map(([k, c]) => (
              <CalibCellRow key={k} label={k} c={c} />
            ))}
          </div>
        </div>
        <div className="mt-2">
          <p className="mb-0.5 text-[11px] font-medium text-muted-foreground">유동성결핍(대금&lt;50억·회전2d&lt;40%) 검증</p>
          <div className="space-y-1">
            {Object.entries(byLQ).map(([k, c]) => (
              <CalibCellRow key={k} label={k} c={c} />
            ))}
          </div>
        </div>
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
            <span className={`shrink-0 rounded px-1.5 py-0.5 text-xs tabular-nums ${rankClass(rank, score)}`}>
              {rank}위
            </span>
          )}
          <span>{m.name}</span>
          {m.mover_type && (
            <span className="shrink-0 rounded bg-white/10 px-1 py-0.5 text-[10px] font-normal text-muted-foreground">
              {MTYPE_LABEL[m.mover_type] ?? m.mover_type}
            </span>
          )}
          {/* KRX 시장경보 대형 배지(회장님 지시 2026-07-03) — 점수 무반영 정보 배지. 폭락 시 추가매수 기회 관점 */}
          {m.alert_now && (
            <span
              className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-bold ${
                m.alert_now === "주의" ? "bg-amber-500/30 text-amber-200" : "bg-down/30 text-down"
              }`}
              title="KRX 시장경보 현재 지정 상태 — 익일 급락·변동성 확대 가능(계획된 추가매수 기회 관점)"
            >
              {m.alert_now === "주의" ? "⚠️투자주의" : m.alert_now === "경고" ? "🚨투자경고" : "⛔투자위험"}
            </span>
          )}
          {m.alert_forecast && (
            <span
              className="shrink-0 rounded bg-down/30 px-1.5 py-0.5 text-xs font-bold text-down"
              title="장마감 직전 KRX 공식(5일+60%·15일+100%·신고가·지수배수) 계산 예측 — 지정 시 익일 급락·변동 주의(=기회). 보장 아님"
            >
              🚨{m.alert_forecast}
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
  // ⚠ 정렬키는 calibrate.py by_close_bet_rank 정렬과 **1:1 동기화** (순위축이 이 화면 순위를 검증하므로):
  //    점수 desc · 거래대금(value_eok) desc · code asc (완전 결정).
  //    (구 |회전2d-115| 타이브레이크는 폐기된 스윗스팟 유산 — v4에서 제거. value_eok desc가 유일한 양(+0.161)의 순위상관.)
  const scored = (data.movers ?? []).map((m) => ({ m, fit: closeBetFitness(m).score }));
  scored.sort(
    (a, b) =>
      b.fit - a.fit ||
      (b.m.value_eok ?? 0) - (a.m.value_eok ?? 0) ||
      (a.m.code ?? "").localeCompare(b.m.code ?? ""),
  );
  const movers = scored.map((x) => x.m);
  return (
    <div className="space-y-6">
      <p className="text-xs text-muted-foreground tabular-nums">
        기준 {data.date ?? "—"} · 갱신 {data.generated_at} · {movers.length}종목 · 정렬=
        <span className="text-foreground">종가베팅 적합도 v4</span>순 · ⚠ 순위상관≈0(감사 실측) —
        정밀 순위가 아니라 <span className="text-foreground">하위권(함정) 회피용</span>·잠정 휴리스틱
        {movers.some((m) => m.provisional) && (
          <span className="ml-2 rounded bg-warning/15 px-1.5 py-0.5 text-warning">🕒 장중 잠정(15:15 기준 · 마감 후 확정)</span>
        )}
      </p>
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
      {/* 전진검증(실측 익일확률) 패널 — 종목 카드가 먼저 보이도록 맨 아래 배치(회장님 지시 2026-07-02) */}
      <CalibrationPanel data={data} />
      <p className="text-xs text-warning">{data.disclaimer}</p>
    </div>
  );
}
