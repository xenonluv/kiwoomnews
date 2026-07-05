import { Wallet, Percent, Repeat, Trophy } from "lucide-react";

import type { AutoTradePerformance } from "@/types/performance";

/**
 * 홈 최상단 자동매매 실전 성과 히어로 — "지금 얼마나 벌고 있나"를 한눈에.
 * 표본 부족/무거래는 친절한 빈 상태로. 한국 색관례(수익=빨강 up / 손실=파랑 down).
 * 표시 전용·가격 근사·보장 아님.
 */
function won(n: number) {
  const sign = n > 0 ? "+" : n < 0 ? "−" : "";
  return `${sign}${Math.abs(n).toLocaleString("ko-KR")}원`;
}

function Tile({
  icon,
  label,
  value,
  sub,
  accent = "none",
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  sub?: string;
  accent?: "up" | "down" | "none";
}) {
  const cls = accent === "up" ? "text-up" : accent === "down" ? "text-down" : "text-foreground";
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.04] px-4 py-3">
      <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
        <span className="opacity-70">{icon}</span>
        {label}
      </div>
      <p className={`mt-1 text-xl font-bold tabular-nums sm:text-2xl ${cls}`}>{value}</p>
      {sub && <p className="text-[11px] text-muted-foreground tabular-nums">{sub}</p>}
    </div>
  );
}

export function AutoTradeHero({ data }: { data: AutoTradePerformance }) {
  const s = data.summary;
  const has = s.n > 0;
  const live = s.status === "OK";
  const pnlAccent = s.total_pnl_krw > 0 ? "up" : s.total_pnl_krw < 0 ? "down" : "none";

  // 익절 vs 손절 vs 기타 분포(이해 쉬운 3분할 바)
  const grp = Object.fromEntries((data.by_reason || []).map((r) => [r.reason, r.n]));
  const tp = (grp.tp1 || 0) + (grp.tp2 || 0);
  const sl = grp.stop_loss || 0;
  const etc = (grp.force_exit || 0) + (grp.breakeven || 0) + (grp.other || 0);
  const tot = tp + sl + etc || 1;

  return (
    <section className="relative overflow-hidden rounded-2xl border border-[rgba(242,54,69,0.28)] bg-gradient-to-br from-[rgba(242,54,69,0.10)] via-[rgba(255,255,255,0.03)] to-[rgba(56,132,255,0.08)] p-5 backdrop-blur-xl sm:p-6">
      {/* 헤더 */}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-lg">🤖</span>
          <h2 className="text-lg font-bold tracking-tight">자동매매 실전 성과</h2>
          <span
            className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
              live
                ? "bg-[rgba(242,54,69,0.18)] text-up"
                : "bg-white/10 text-muted-foreground"
            }`}
          >
            {live ? "LIVE" : "수집 중"}
          </span>
        </div>
        {data.as_of && (
          <span className="text-[11px] text-muted-foreground tabular-nums">{data.as_of}</span>
        )}
      </div>

      {!has ? (
        // ── 빈 상태(무거래) — 친절하게 ──
        <div className="rounded-xl border border-white/10 bg-white/[0.03] px-5 py-8 text-center">
          <p className="text-2xl font-bold">첫 거래를 기다리는 중 📈</p>
          <p className="mx-auto mt-2 max-w-md text-sm text-muted-foreground">
            실매매가 시작되면 <span className="text-foreground">승률 · 평균 수익 · 누적 손익</span>이
            이 자리에 실시간으로 쌓입니다.
          </p>
        </div>
      ) : (
        <>
          {/* 핵심 히어로: 누적 손익 */}
          <div className="mb-4 rounded-xl border border-white/10 bg-white/[0.03] px-5 py-4">
            <p className="text-xs text-muted-foreground">누적 실현손익</p>
            <p
              className={`text-3xl font-black tabular-nums sm:text-4xl ${
                pnlAccent === "up" ? "text-up" : pnlAccent === "down" ? "text-down" : "text-foreground"
              }`}
            >
              {won(s.total_pnl_krw)}
            </p>
            <p className="text-[11px] text-muted-foreground tabular-nums">
              완결 {s.n}건 누적 · 수수료 {s.fee_pct}%p 차감 근사
            </p>
          </div>

          {/* 지표 타일 */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Tile
              icon={<Percent className="size-3.5" />}
              label="승률"
              value={s.win_rate !== null ? `${s.win_rate}%` : "—"}
              accent={(s.win_rate ?? 0) >= 50 ? "up" : "down"}
            />
            <Tile
              icon={<Wallet className="size-3.5" />}
              label="거래당 평균수익"
              value={s.avg_net !== null ? `${s.avg_net > 0 ? "+" : ""}${s.avg_net}%` : "—"}
              accent={(s.avg_net ?? 0) > 0 ? "up" : (s.avg_net ?? 0) < 0 ? "down" : "none"}
            />
            <Tile
              icon={<Trophy className="size-3.5" />}
              label="최고 / 최악"
              value={s.best !== null ? `${s.best > 0 ? "+" : ""}${s.best}%` : "—"}
              sub={s.worst !== null ? `최악 ${s.worst > 0 ? "+" : ""}${s.worst}%` : undefined}
              accent={(s.best ?? 0) >= 0 ? "up" : "down"}
            />
            <Tile
              icon={<Repeat className="size-3.5" />}
              label="완결 거래"
              value={`${s.n}건`}
              sub={live ? "충분" : `${s.n}/${s.min_n} 수집 중`}
            />
          </div>

          {/* 익절/손절 분포 바 */}
          <div className="mt-4">
            <div className="mb-1 flex justify-between text-[11px] text-muted-foreground">
              <span>청산 분포</span>
              <span className="tabular-nums">
                익절 {tp} · 손절 {sl} · 기타 {etc}
              </span>
            </div>
            <div className="flex h-2.5 overflow-hidden rounded-full bg-white/10">
              <div className="bg-up" style={{ width: `${(tp / tot) * 100}%` }} />
              <div className="bg-down" style={{ width: `${(sl / tot) * 100}%` }} />
              <div className="bg-white/25" style={{ width: `${(etc / tot) * 100}%` }} />
            </div>
          </div>
        </>
      )}

      <p className="mt-4 text-[11px] leading-relaxed text-muted-foreground">
        {data.disclaimer}
      </p>
    </section>
  );
}
