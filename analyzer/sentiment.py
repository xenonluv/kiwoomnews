#!/usr/bin/env python3
"""팀원3 보조 — 종목 재료(뉴스) 강도·호악재 수치화.

기존 scripts/team2_relevance(시황제외+별칭매칭+중요도)와 team1_collect.fetch_news 재사용.
상위 후보 소수에만 호출(네이버 호출량 통제).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from team1_collect import fetch_news, fetch_cause_candidates  # noqa: E402
from team2_relevance import score_news, score_cause_news, make_aliases  # noqa: E402


def analyze(code, name, k=12):
    """종목 뉴스 → {importance, sentiment, count, top_titles}."""
    try:
        news = [n for n in fetch_news(code, k) if n.get("title")]
        aliases = make_aliases(name)
        res = score_news(news, aliases)
        try:
            candidates = fetch_cause_candidates(code, name, base_news=news, k=12)
            cause = score_cause_news(candidates, aliases)
        except Exception:
            cause = {"cause_news": [], "cause_confidence": "낮음", "cause_summary": ""}
        return {
            "importance": res["importance_score"],   # 1~10
            "sentiment": res["sentiment"],            # 호재/악재/혼재/중립
            "count": res["relevant_count"],
            "top_titles": [n["title"] for n in res["relevant"][:3]],
            "cause_news": cause.get("cause_news", []),
            "cause_confidence": cause.get("cause_confidence"),
            "cause_summary": cause.get("cause_summary"),
        }
    except Exception as e:
        return {"importance": 0, "sentiment": "중립", "count": 0, "error": str(e)}


if __name__ == "__main__":
    import json
    code = sys.argv[1] if len(sys.argv) > 1 else "018880"
    name = sys.argv[2] if len(sys.argv) > 2 else code
    print(json.dumps(analyze(code, name), ensure_ascii=False, indent=2))
