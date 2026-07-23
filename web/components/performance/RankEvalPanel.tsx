import type { RankEval, RankEvalPopulation } from "@/types/performance";

const POPULATION_LABELS: Record<string, string> = {
  krx_decision: "KRX 15:18",
  nxt_decision: "NXT 19:50",
  eod: "운영 EOD",
  final: "기존 final",
  dropout: "마감 탈락",
};

function metric(value: number | null, suffix = "%") {
  return value == null ? "—" : `${value}${suffix}`;
}

function PopulationRow({ data }: { data: RankEvalPopulation }) {
  const rankHigh = data.rank_avg_high
    .filter((row) => row.rank <= 3)
    .map((row) => `${row.rank}위 ${row.avg_high_pct > 0 ? "+" : ""}${row.avg_high_pct}% (n=${row.n})`)
    .join(" · ");
  return (
    <tr className="border-t border-white/5 align-top">
      <td className="py-2 font-medium">{POPULATION_LABELS[data.population] ?? data.population}</td>
      <td className="py-2 text-right tabular-nums">
        {data.multi_candidate_days}
        <span className="ml-1 text-[10px] text-muted-foreground">일</span>
      </td>
      <td className="py-2 text-right tabular-nums">
        {metric(data.top1_hit)}
        <span className="ml-1 text-[10px] text-muted-foreground">{data.top1_hits}/{data.top1_n}</span>
      </td>
      <td className="py-2 text-right tabular-nums">
        {metric(data.top3_contains_winner)}
        <span className="ml-1 text-[10px] text-muted-foreground">{data.top3_hits}/{data.top3_n}</span>
      </td>
      <td className="py-2 text-right tabular-nums">{data.spearman ?? "—"}</td>
      <td className="py-2 text-right tabular-nums">{data.ndcg ?? "—"}</td>
      <td className="py-2 text-right tabular-nums text-muted-foreground">
        {data.single_candidate.days}
      </td>
      <td className="max-w-[280px] py-2 pl-4 text-[11px] text-muted-foreground">
        {rankHigh || "—"}
        {!data.valid && <span className="ml-1 text-warning">수집 중</span>}
      </td>
    </tr>
  );
}

export function RankEvalPanel({ data }: { data: RankEval }) {
  const models = Object.entries(data.by_model_version);
  const reference = data.reference?.legacy_mixed_models;

  return (
    <section className="rounded-lg border border-white/10 bg-white/[0.03] p-4">
      <h3 className="text-sm font-semibold">익일 최고고가 상대순위</h3>
      <p className="mt-1 text-[11px] text-muted-foreground">
        같은 날 후보가 2개 이상일 때 화면 순위와 실제 익일 고가 순위를 비교합니다. 단일후보 날은 별도 집계합니다.
      </p>

      {models.length === 0 ? (
        <p className="mt-4 border-t border-white/10 pt-3 text-sm text-warning">
          현행 순위 모델 forward 수집 중 · 저장된 모델·신호 bucket·의사결정 시점이 모두 있는 표본만 집계
        </p>
      ) : (
        models.map(([version, model]) => (
          <div key={version} className="mt-4 border-t border-white/10 pt-3">
            <p className="mb-2 text-xs font-semibold">
              {version} <span className="font-normal text-muted-foreground">발효 {model.effective_from}</span>
            </p>
            <div className="overflow-x-auto">
              <table className="min-w-[900px] text-sm">
                <thead>
                  <tr className="text-left text-xs text-muted-foreground">
                    <th className="pb-2 font-medium">모집단</th>
                    <th className="pb-2 text-right font-medium">비교일</th>
                    <th className="pb-2 text-right font-medium">Top1</th>
                    <th className="pb-2 text-right font-medium">Top3 winner</th>
                    <th className="pb-2 text-right font-medium">Spearman</th>
                    <th className="pb-2 text-right font-medium">NDCG</th>
                    <th className="pb-2 text-right font-medium">단일후보일</th>
                    <th className="pb-2 pl-4 font-medium">순위별 평균고가</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.values(model.populations).map((population) => (
                    <PopulationRow key={population.population} data={population} />
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))
      )}

      {reference && (
        <p className="mt-3 text-[11px] text-muted-foreground">
          참고 기준선: 구정렬 혼합 {reference.multi_candidate_days}일 · Top1 {reference.top1_hits}/{reference.multi_candidate_days}
          {" · "}Top3 {reference.top3_hits}/{reference.multi_candidate_days}. 현행 모델 성과에는 포함하지 않습니다.
        </p>
      )}
    </section>
  );
}
