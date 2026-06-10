#!/usr/bin/env python3
"""테마 매핑 — 이벤트 카테고리 ↔ 종목(뉴스·업종) 민감도 매칭 (조건 5).

이벤트 category(macro_events.json과 동일 키) → 키워드 정규식 사전.
종목의 뉴스 제목 + 업종명을 대조해 카테고리별 hit 수를 센다.

사용:
  from theme_map import match_sensitivity, match_events
"""
import re

THEMES = {
    "금리": re.compile(r"은행|보험|증권|금융지주|카드|캐피탈|리츠|건설|금리|부동산"),
    "반도체": re.compile(r"반도체|HBM|파운드리|D램|디램|낸드|메모리|엔비디아|NVIDIA|TSMC|"
                       r"소부장|웨이퍼|패키징|팹리스|칩스|칩셋|반도체\s?장비|AI\s?서버|데이터센터"),
    "환율": re.compile(r"환율|달러|원화|수출|관세|무역"),
    "유가": re.compile(r"유가|원유|정유|조선|해운|시추|피팅|OPEC|셰일|LNG|탱커"),
    "전쟁": re.compile(r"방산|방위|탄약|미사일|레이더|무기|전쟁|휴전|우크라|중동|이란|지정학"),
    "실적": re.compile(r"실적|영업이익|어닝|컨센서스|흑자|턴어라운드|가이던스"),
    "수급": re.compile(r"공매도|만기|선물|옵션|배당|자사주|블록딜|수급"),
}

# 업종명 → 카테고리 직결 매핑 (뉴스 없어도 업종만으로 민감도 인정)
SECTOR_HINTS = {
    "금리": re.compile(r"은행|금융|보험|증권|부동산"),
    "반도체": re.compile(r"반도체|전기.?전자|IT"),
    "유가": re.compile(r"정유|화학|조선|운송|에너지"),
    "전쟁": re.compile(r"방산|항공|기계"),
    "환율": re.compile(r"자동차|운송장비|전기.?전자"),
}


def match_sensitivity(texts, sector=""):
    """뉴스 제목 리스트 + 업종명 → {category: hit수}."""
    hits = {}
    for cat, pat in THEMES.items():
        n = sum(1 for t in texts if t and pat.search(t))
        if sector and cat in SECTOR_HINTS and SECTOR_HINTS[cat].search(sector):
            n += 1
        if n > 0:
            hits[cat] = n
    return hits


def match_events(events, texts, sector=""):
    """D-N 이벤트 목록과 종목 민감도를 매칭.

    Returns:
      (matched, score) — matched: [{id,title,dday,score}], score: 0~15 가점.
      이벤트별 기여 = min(hit,3) × (importance/10) × 근접가중((11-dday)/11) × 3
    """
    sens = match_sensitivity(texts, sector)
    matched = []
    total = 0.0
    for ev in events:
        cats = [c for c in ev.get("category", []) if c in sens]
        if not cats:
            continue
        hit = max(sens[c] for c in cats)
        proximity = max(0.0, (11 - ev.get("dday", 10)) / 11)
        contrib = min(hit, 3) * (ev.get("importance", 5) / 10) * proximity * 3
        matched.append({"id": ev["id"], "title": ev["title"], "dday": ev["dday"],
                        "categories": cats, "score": round(contrib, 1)})
        total += contrib
    matched.sort(key=lambda m: -m["score"])
    return matched, round(min(15.0, total), 1)
