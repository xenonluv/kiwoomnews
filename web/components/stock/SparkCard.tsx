import { Zap } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SparkTimeline } from "@/components/radar/SparkTimeline";
import type { SparkSection } from "@/types/stock";

/** 당일 분봉 스파크 — 레이더 조건2와 동일 규칙(1분 거래량 ≥ 당일 중앙값 8배 + |등락| ≥ 0.8%). */
export function SparkCard({ spark }: { spark: SparkSection }) {
  const n = spark.clusters.length;
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-lg">
          <Zap className="size-4 text-warning" aria-hidden /> 당일 분봉 스파크
          {n > 0 && (
            <span className="text-xs font-normal text-muted-foreground tabular-nums">
              {n}회 · 최대 {spark.maxVolX}배
            </span>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {n === 0 ? (
          <p className="text-sm text-muted-foreground">
            오늘 1분봉 {spark.barCount}개 중 거래량 급증(중앙값 8배+ 동반 등락) 분봉이
            없습니다.
          </p>
        ) : (
          <>
            <SparkTimeline clusters={spark.clusters} />
            <p className="mt-2 text-[11px] text-muted-foreground">
              스파크 = 1분 거래량이 당일 중앙값의 8배 이상 + 등락 ±0.8% 이상 (개장 직후는
              12배 기준). 점이 클수록 거래량 배수가 큽니다.
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}
