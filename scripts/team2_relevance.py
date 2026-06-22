#!/usr/bin/env python3
"""팀원2 자동화 — 뉴스 재료 관련성/중요도 필터.

종목 피드 뉴스에서 시황·일반 기사를 제거하고 재료성 뉴스만 추출,
호재/악재 판별 + 중요도 점수(1~10) 산출.

규칙(결정론):
  - 제목이 '시황/지수/일반' 패턴이고 재료 키워드가 없으면 제외(노이즈).
  - 재료 키워드(실적/수주/계약/신고가/급등/수출/투자/승인 등)로 관련성·중요도 가중.
  - 종목명 별칭 문제를 피하려 '제거(blacklist) + 재료(whitelist)' 방식 사용.
"""
import re
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

KST = timezone(timedelta(hours=9))

# 종목명 별칭 (공식명 ↔ 뉴스 통용 표기). 영문/약어 종목 위주로 수동 보강.
MANUAL_ALIAS = {
    "NAVER": ["네이버"],
    "삼성에스디에스": ["삼성SDS", "삼성 SDS"],
    "LG씨엔에스": ["LG CNS", "엘지씨엔에스", "LGCNS"],
    "LG전자": ["엘지전자"],
    "LG디스플레이": ["엘지디스플레이", "LGD"],
    "LG이노텍": ["엘지이노텍"],
    "LG": ["엘지"],
    "삼성전자": ["삼전"],
    "SK텔레콤": ["SKT", "SK 텔레콤", "에스케이텔레콤"],
    "SK하이닉스": ["하이닉스", "SK 하이닉스"],
    "카카오뱅크": ["카뱅"],
    "현대차": ["현대자동차"],
}


def make_aliases(name):
    """공식명 → 매칭용 별칭 집합 (소문자)."""
    al = {name, name.replace(" ", ""), re.sub(r"(우|우B)$", "", name)}
    al.update(MANUAL_ALIAS.get(name, []))
    return {a.lower() for a in al if a and len(a) >= 2}


_ASCII_ALIAS = re.compile(r"^[a-z0-9]+$")


def mentions(text, aliases):
    """본문/제목에 종목(별칭) 언급 여부."""
    if not aliases:
        return True  # 별칭 미지정 시 검사 생략(하위호환)
    t = text.lower()
    for a in aliases:
        if a not in t:
            continue
        # 영문 약어(lg·sk 등)는 무관 단어 내부 우연일치(flagship·risk·task) 방지 — 영숫자 경계 요구.
        # 한글 별칭은 단어경계가 무의미하므로 substring 그대로(종목명은 충분히 변별적). news-score.ts와 정합.
        if _ASCII_ALIAS.match(a):
            if re.search(r"(?<![a-z0-9])" + a + r"(?![a-z0-9])", t):
                return True
        else:
            return True
    return False


# 강한 시황/일반(노이즈) — 제목에 있으면 재료 키워드와 무관하게 무조건 제외
# (지수·증시 기사, 데이터랩/칼럼/신문요약 등은 해당 종목 고유 재료가 아님)
HARD = re.compile(
    r"\[마감|\[개장|\[이 시각|데이터랩|뉴스초점|미리보는|마감\s*시황|검색\s*상위|"
    r"인기\s*검색|빚투|신용잔고|예탁금|오늘의 메모|기업 공시 \[|부고|인사 |사외이사|"
    r"본사 수도권|주간 증시|애프터마켓|리밸런싱|정기변경|코스피|코스닥|증시|지수"
)
# 약한 시황/일반 — 재료 키워드 없으면 제외
WRAP = re.compile(r"시황|개장|장\s*마감|특징주|오후 시황|오전 시황")
# 재료(호재 성향) 키워드
POS = re.compile(
    r"호실적|실적|영업이익|순이익|매출|흑자|수주|계약|공급|납품|출시|신제품|신고가|"
    r"상한가|급등|투자|유치|협력|제휴|인수|합병|수출|목표주가|상향|승인|허가|임상|"
    r"특허|점유율|1위|최대|최고|돌파|수혜|확대|성장|호조|반등"
)
# 악재 키워드
NEG = re.compile(
    r"적자|급락|폭락|하락|감소|소송|횡령|불성실|상장폐지|유상증자|하향|매도|손실|"
    r"리콜|결함|철회|부진|악재|반토막|하한가|영업정지|제재|벌금|배임"
)
# 강한 재료(제목에 있으면 가중치 큼)
STRONG = re.compile(
    r"실적|영업이익|순이익|매출|흑자|적자|수주|계약|공급|신고가|상한가|급등|급락|"
    r"수출|유치|인수|합병|목표주가|승인|허가|임상|특허|1위|최대 수주"
)
LIST_NOISE = re.compile(
    r"\[종합\]|종합\)|TOP\s*\d|외\s*\d+\s*종목|급등주|상승주|주목할|"
    r"오늘의\s*특징주|특징주\s*\[|증시\s*특징주|테마주"
)
CAUSE_STRONG = re.compile(
    r"소식에|기대감에|언급에|수혜주로|상한가|급등|강세|특징주|왜\s*(올랐|상승)"
)
CAUSE_MATERIAL = re.compile(
    r"계약|공급|수주|실적|영업이익|흑자|승인|허가|임상|인수|합병|투자|"
    r"유치|제휴|협력|엔비디아|젠슨\s*황|AI|반도체|로봇|데이터센터"
)


def classify(item, aliases=None):
    title = item.get("title", "")
    text = title + " " + (item.get("summary", "") or "")
    if HARD.search(title):  # 지수/시황/칼럼 → 종목 고유 재료 아님, 무조건 제외
        return {"relevant": False, "score": -9, "sentiment": "중립", "strong": False}
    if not mentions(text, aliases):  # 종목(별칭) 미언급 → 타 종목 재료(피드 혼입)
        return {"relevant": False, "score": -5, "sentiment": "중립", "strong": False}
    wrap = bool(WRAP.search(title))
    pos_t, neg_t = bool(POS.search(title)), bool(NEG.search(title))
    pos_b, neg_b = bool(POS.search(text)), bool(NEG.search(text))
    strong_title = bool(STRONG.search(title))

    score = 0
    if strong_title:
        score += 2
    elif pos_t or neg_t:
        score += 1
    if pos_b or neg_b:
        score += 1
    if wrap and not (pos_t or neg_t):
        score -= 3  # 재료 없는 순수 시황 → 강한 감점

    relevant = score >= 1
    if pos_b and neg_b:
        sentiment = "혼재"
    elif neg_b:
        sentiment = "악재"
    elif pos_b:
        sentiment = "호재"
    else:
        sentiment = "중립"
    return {"relevant": relevant, "score": score, "sentiment": sentiment,
            "strong": strong_title}


def score_news(news, aliases=None):
    """뉴스 리스트 → 관련 뉴스만 + 중요도/임팩트/감성 요약. aliases로 종목 언급 검사."""
    relevant, dropped = [], []
    strong_cnt = pos_cnt = neg_cnt = 0
    for it in news:
        c = classify(it, aliases)
        it2 = dict(it)
        it2["sentiment"] = c["sentiment"]
        if c["relevant"]:
            relevant.append(it2)
            if c["strong"]:
                strong_cnt += 1
            if c["sentiment"] == "호재":
                pos_cnt += 1
            elif c["sentiment"] == "악재":
                neg_cnt += 1
        else:
            dropped.append(it.get("title", ""))

    # 중요도 점수 (1~10)
    importance = min(10.0, 3.0 + 1.5 * strong_cnt + 0.5 * (len(relevant) - strong_cnt))
    if neg_cnt > pos_cnt:
        importance = max(1.0, importance - 2.0)  # 악재 우세 시 하향
    importance = round(importance, 1)
    impact = "상" if importance >= 7 else "중" if importance >= 5 else "하"
    overall = ("호재" if pos_cnt > neg_cnt else "악재" if neg_cnt > pos_cnt
               else "혼재" if pos_cnt and neg_cnt else "중립")

    return {"relevant": relevant, "dropped": dropped,
            "importance_score": importance, "impact_level": impact,
            "sentiment": overall, "relevant_count": len(relevant)}


def _age_days(dt_text):
    if not dt_text:
        return None
    s = str(dt_text).strip()
    candidates = (
        "%Y-%m-%d %H:%M:%S KST",
        "%Y-%m-%d %H:%M:%S",
        "%Y.%m.%d %H:%M",
        "%Y.%m.%d. %H:%M",
        "%Y-%m-%dT%H:%M:%S%z",
    )
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return (datetime.now(KST) - dt.astimezone(KST)).total_seconds() / 86400
    except Exception:
        pass
    for fmt in candidates:
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=KST)
            return (datetime.now(KST) - dt.astimezone(KST)).total_seconds() / 86400
        except Exception:
            continue
    return None


def _alias_is_subject(title_lower, aliases):
    for alias in aliases or []:
        if title_lower.startswith(alias):
            return True
        for suffix in (",", "이", "가", "은", "는", "도", "·"):
            if f"{alias}{suffix}" in title_lower:
                return True
    return False


def score_cause_news(news, aliases=None, max_age_days=2):
    """급등 원인 후보 뉴스 점수화. 실패/원인 없음은 빈 cause_news로 fallback."""
    try:
        scored = []
        for item in news:
            title = item.get("title", "") or ""
            summary = item.get("summary", "") or ""
            text = f"{title} {summary}"
            title_lower = title.lower()
            score = 0
            reasons = []

            if not title or HARD.search(title) or not mentions(text, aliases):
                continue
            title_mentions = mentions(title, aliases)
            if not title_mentions:
                continue
            score += 3
            reasons.append("제목 언급")
            if _alias_is_subject(title_lower, aliases):
                score += 2
                reasons.append("주어")
            if CAUSE_STRONG.search(title):
                score += 3
                reasons.append("원인표현")
            if re.search(r"소식에|기대감에|언급에|수혜", text):
                score += 4
                reasons.append("인과표현")
            if CAUSE_MATERIAL.search(text):
                score += 2
                reasons.append("재료")
            if item.get("query") and CAUSE_STRONG.search(title):
                score += 1
                reasons.append("검색맥락")
            if NEG.search(text):
                score -= 3
                reasons.append("악재")
            if LIST_NOISE.search(title):
                score -= 5
                reasons.append("리스트감점")
            if score > 0 and not CAUSE_STRONG.search(title) and not re.search(r"소식에|기대감에|언급에|수혜", text):
                score -= 2
                reasons.append("일반뉴스")

            age = _age_days(item.get("datetime"))
            if age is not None:
                if age <= 1:
                    score += 2
                    reasons.append("당일")
                elif age > max_age_days:
                    score -= 4
                    reasons.append("오래됨")

            item2 = dict(item)
            item2["cause_score"] = score
            item2["cause_reason"] = ", ".join(reasons)
            item2["sentiment"] = "악재" if NEG.search(text) else "호재"
            scored.append(item2)

        scored.sort(key=lambda x: -x.get("cause_score", 0))
        top = [x for x in scored if x.get("cause_score", 0) >= 5][:3]
        if top and top[0].get("cause_score", 0) >= 8:
            confidence = "높음"
        elif top:
            confidence = "중간"
        else:
            confidence = "낮음"
        return {
            "cause_news": top,
            "cause_confidence": confidence,
            "cause_summary": top[0]["title"][:80] if top else "",
        }
    except Exception:
        return {"cause_news": [], "cause_confidence": "낮음", "cause_summary": ""}


if __name__ == "__main__":
    import sys
    import json
    data = json.load(sys.stdin)
    news = data if isinstance(data, list) else data.get("news", [])
    print(json.dumps(score_news(news), ensure_ascii=False, indent=2))
