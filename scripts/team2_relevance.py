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
    r"상한가|급등|투자(?!경고|주의|위험)|유치|협력|제휴|인수|합병|수출|목표주가|상향|승인|허가|임상|"
    r"특허|점유율|1위|최대|최고|돌파|수혜|확대|성장|호조|반등"
)
# 악재 키워드
NEG = re.compile(
    r"적자|급락|폭락|하락|감소|소송|횡령|불성실|상장폐지|하향|매도|손실|"
    r"리콜|결함|철회|부진|악재|반토막|하한가|영업정지|제재|벌금|배임|"
    r"투자경고|투자위험|투자주의|매매거래정지|거래정지|관리종목"
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

# 레이더 전진검증용 재료 등급. LLM 없이 제목/요약 키워드만으로 보수적으로 분류한다.
MATERIAL_S = re.compile(
    r"국가|정부|대통령|메가프로젝트|클러스터|삼성전자|SK하이닉스|대기업|"
    r"공개매수|상장폐지|상폐|퇴출|최대주주.*(유상증자|증자|출자|납입|지원|수혈)|"
    r"경영권|인수합병|M&A|거래재개|회생|"
    # 주요 그룹 계열사 — 명시 나열(bare '삼성/한화/현대'는 증권사 리포트 출처
    # '삼성증권/한화투자증권/현대차증권' 과매치 위험이라 금지. 씨이랩×삼성SDS 3,151억 실측 보완 2026-07-09)
    r"삼성(?:SDS|물산|바이오로직스|전기|중공업|디스플레이|엔지니어링)|"
    r"SK(?:텔레콤|이노베이션|온)|LG(?:전자|에너지솔루션|화학|디스플레이|이노텍|유플러스)|"
    r"현대차(?!증권)|현대자동차|현대모비스|현대건설|현대중공업|HD현대|HD한국조선해양|기아|"
    r"포스코|한국전력|한전|한화(?:에어로스페이스|오션|시스템|솔루션)|두산에너빌리티|대한항공"
)
MATERIAL_A = re.compile(
    r"제3자배정|유상증자|무상증자|최대주주|액면병합|감자|납입|"
    r"공급계약|수주|계약|투자(?!경고|주의|위험)|유치|타법인|지분\s*취득|공시"
)
MATERIAL_B = re.compile(
    r"채무상환|재무구조|운영자금|실적|영업이익|순이익|매출|흑자|"
    r"목표주가|특허|승인|허가|임상|신제품|출시|수출"
)
MATERIAL_C = re.compile(
    r"관련주|테마|수혜|기대감|부각|강세|급등|상한가|지역|호남|광주|"
    r"이름|명칭|연고|묶이"
)
MATERIAL_DISCLOSURE = re.compile(
    r"공시|금융감독원|전자공시|DART|한국거래소|KRX|유상증자\s*결정|계약\s*체결|수주"
)
MATERIAL_DIRECT_WEAK = re.compile(r"관련주|테마|수혜|기대감|부각|이름|명칭|지역|연고|묶이")
MATERIAL_RISK = re.compile(
    r"횡령|배임|불성실|관리종목|상장폐지|상폐|거래정지|감사의견|의견거절|"
    r"투자경고|투자위험|투자주의|매매거래정지|"
    r"대규모\s*희석|발행주식.*증가|주주배정|일반공모|"
    r"유상증자.*(희석|주주배정|일반공모|발행주식.*증가)|"
    r"감자|전환사채|(?i:CB)|(?i:BW)|메자닌|채무"
)
MATERIAL_RESCUE = re.compile(
    r"상폐.*(회피|해소|탈피|우려.*해소)|시가총액.*(회복|충족|상회|넘어섰|넘겼|초과|달성)|"
    r"투자경고.*(해제|해소|탈피)|투자위험.*(해제|해소|탈피)|투자주의.*(해제|해소|탈피)|"
    r"최대주주.*(유상증자|증자|출자|납입|지원|수혈)|"
    r"제3자배정.*(최대주주|특수관계인)|채무상환|재무구조.*개선|자금\s*수혈"
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
        "%Y%m%d%H%M",
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


def _material_source(item):
    return (item.get("office") or item.get("source") or item.get("press") or "").strip()


def _freshness_label(min_age):
    if min_age is None:
        return "미확인"
    if min_age <= 1:
        return "당일~1일"
    if min_age <= 3:
        return "2~3일"
    if min_age <= 7:
        return "1주내"
    return "오래됨"


def _material_grade(score, relevant_count, risk_flags, sentiment):
    if relevant_count <= 0:
        return "N"
    if risk_flags and sentiment in ("악재", "혼재", "중립") and score < 55:
        return "D"
    if score >= 85:
        return "S"
    if score >= 70:
        return "A"
    if score >= 55:
        return "B"
    if score >= 35:
        return "C"
    return "D" if risk_flags else "C"


def score_material(news, aliases=None, sector=""):
    """뉴스/공시 재료를 S/A/B/C/D/N 등급으로 요약한다.

    N은 재료뉴스 없음/미확인을 뜻한다. 결과는 레이더 기록·전진검증용이며 현재 점수/정렬에는 반영하지 않는다.
    """
    try:
        aliases = aliases or set()
        candidates = []
        raw_scores = []
        pos_cnt = neg_cnt = 0
        min_age = None
        disclosure = False
        source_count = 0
        tags = set()
        risk_flags = set()
        direct_hits = 0
        indirect_hits = 0
        rescue_catalyst = 0

        for item in news or []:
            title = item.get("title", "") or ""
            summary = item.get("summary", "") or ""
            text = f"{title} {summary}"
            if not title or HARD.search(title) or not mentions(text, aliases):
                continue

            c = classify(item, aliases)
            risk_match = MATERIAL_RISK.search(text)
            is_disclosure = bool(MATERIAL_DISCLOSURE.search(text))
            material_hit = (
                c["relevant"]
                or MATERIAL_S.search(text)
                or MATERIAL_A.search(text)
                or MATERIAL_B.search(text)
                or MATERIAL_C.search(text)
                or risk_match
            )
            if not material_hit:
                continue

            score = 20
            if c["strong"]:
                score += 8
            rescue = bool(MATERIAL_RESCUE.search(text))
            if c["sentiment"] == "호재" or rescue:
                pos_cnt += 1
                score += 10 if rescue else 6
                if rescue:
                    rescue_catalyst += 1
                    tags.add("구제/수혈")
            elif c["sentiment"] == "악재":
                neg_cnt += 1
                score -= 10

            if MATERIAL_S.search(text):
                score += 42
                tags.add("S급재료")
            if MATERIAL_A.search(text):
                score += 30
                tags.add("공시/대형이벤트")
                if is_disclosure:
                    score += 4
            if MATERIAL_B.search(text):
                score += 18
                tags.add("실적/재무")
            if MATERIAL_C.search(text):
                score += 8
                tags.add("테마/수혜")

            if is_disclosure:
                disclosure = True
                score += 10
                tags.add("공시")

            if mentions(title, aliases) and not MATERIAL_DIRECT_WEAK.search(title):
                direct_hits += 1
                score += 8
            elif MATERIAL_DIRECT_WEAK.search(text):
                indirect_hits += 1
                score -= 5

            if risk_match:
                risk_flags.add(risk_match.group(0)[:20])
                # 유증/감자는 재료가 될 수 있으나 희석·재무 리스크는 별도 감점으로 남긴다.
                score -= 6

            age = _age_days(item.get("datetime"))
            if age is not None:
                min_age = age if min_age is None else min(min_age, age)
                if age <= 1:
                    score += 8
                elif age <= 3:
                    score += 4
                elif age > 7:
                    score -= 8

            src = _material_source(item)
            if src:
                source_count += 1

            item2 = dict(item)
            item2["material_score"] = score
            candidates.append(item2)
            raw_scores.append(score)

        relevant_count = len(candidates)
        if not candidates:
            return {
                "grade": "N",
                "score": 0,
                "summary": "",
                "sentiment": "중립",
                "reliability": "뉴스없음",
                "freshness": "미확인",
                "directness": "미확인",
                "tags": [],
                "risk_flags": [],
                "evidence": [],
                "source_count": 0,
                "relevant_count": 0,
            }

        candidates.sort(key=lambda x: -x.get("material_score", 0))
        score = max(raw_scores) + min(12, (relevant_count - 1) * 3)
        if (neg_cnt > pos_cnt or risk_flags) and rescue_catalyst == 0:
            score = min(score, 45)
        # S등급(score≥85)은 MATERIAL_S 실매치 필수 — A급 일상 공시(공급계약 등)가
        # 호재·공시·신선도 보너스 합산만으로 S 코호트에 섞이는 것을 84 캡으로 차단.
        if "S급재료" not in tags:
            score = min(score, 84)
        score = max(0, min(100, round(score)))
        sentiment = ("호재" if pos_cnt > neg_cnt else "악재" if neg_cnt > pos_cnt
                     else "혼재" if pos_cnt and neg_cnt else "중립")
        directness = "직접" if direct_hits else "간접" if indirect_hits else "미확인"
        reliability = "공시+뉴스" if disclosure and source_count else "공시" if disclosure else "뉴스"
        if sector:
            tags.add(sector)
        grade = _material_grade(score, relevant_count, risk_flags, sentiment)
        return {
            "grade": grade,
            "score": score,
            "summary": candidates[0].get("title", "")[:90],
            "sentiment": sentiment,
            "reliability": reliability,
            "freshness": _freshness_label(min_age),
            "freshness_days": round(min_age, 2) if min_age is not None else None,
            "directness": directness,
            "tags": sorted(tags)[:8],
            "risk_flags": sorted(risk_flags)[:5],
            "evidence": [
                {
                    "title": c.get("title", "")[:120],
                    "datetime": c.get("datetime"),
                    "source": _material_source(c),
                    "url": c.get("url"),
                    "score": max(0, min(100, round(c.get("material_score", 0)))),
                }
                for c in candidates[:5]
            ],
            "source_count": source_count,
            "relevant_count": relevant_count,
        }
    except Exception:
        return {
            "grade": "N",
            "score": 0,
            "summary": "",
            "sentiment": "중립",
            "reliability": "분석실패",
            "freshness": "미확인",
            "directness": "미확인",
            "tags": [],
            "risk_flags": ["material_score_error"],
            "evidence": [],
            "source_count": 0,
            "relevant_count": 0,
        }


if __name__ == "__main__":
    import sys
    import json
    data = json.load(sys.stdin)
    news = data if isinstance(data, list) else data.get("news", [])
    print(json.dumps(score_news(news), ensure_ascii=False, indent=2))
