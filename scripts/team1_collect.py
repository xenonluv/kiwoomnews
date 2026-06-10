#!/usr/bin/env python3
"""팀원1 통합 수집기 (종목 중심).

기능:
  - 거래대금/상승률 상위 랭킹으로 후보 종목 발굴 (네이버증권)
  - 종목명 → 코드 해석 (autocomplete)
  - 종목별: 뉴스/이슈 + 애널리스트 컨센서스(목표주가·상승여력) + 차트지표(team3_price_context)

사용:
  python3 scripts/team1_collect.py --names 삼성전자 현대차 NAVER ...
  python3 scripts/team1_collect.py --top 거래대금 --market KOSPI --n 10
  python3 scripts/team1_collect.py --codes 005930 005380 ...

출력: stdout JSON 배열. 각 원소 = {ticker_name, ticker_code, news[], consensus{}, chart{}}
"""
import re
import sys
import json
import os
import html
from datetime import datetime, timezone, timedelta
import urllib.parse
import urllib.request

from team3_price_context import compute_context  # 동일 scripts/ 디렉터리
from net import get_bytes  # 정중한 간격 + 재시도
from team1_fetch_news import fetch as fetch_search_news
from team1_fetch_news import load_env, source_from_link, strip_html

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}
KST = timezone(timedelta(hours=9))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 랭킹 정렬키 매핑 (네이버 m.stock api/stocks/{sort}/{market})
# ⚠️ 2026-06 네이버가 transactionAmount/tradingVolume 랭킹을 폐지(404) — up/down만 동작
SORT = {"상승률": "up", "하락률": "down"}
CAUSE_QUERIES = ("{n} 급등", "{n} 강세", "{n} 특징주", "{n} 수혜")
# ETF/ETN 브랜드 패턴 (개별 종목만 남기기 위해 제외)
ETF_PAT = re.compile(
    r"KODEX|TIGER|KBSTAR|ARIRANG|HANARO|SOL |ACE |KOSEF|TIMEFOLIO|RISE|PLUS |"
    r"1Q |KIWOOM|히어로즈|마이다스|레버리지|인버스|ETN|국고채|채권액티브|TIMEF"
)


def is_individual_stock(name, code, end_type=None):
    """ETF/ETN/우선주 제외 → 개별 보통주만 True."""
    if not name or not code:
        return False
    if end_type and end_type != "stock":
        return False              # ETF/ETN 등
    if not code.endswith("0"):
        return False              # 우선주(…5/…7) 제외, 보통주는 …0
    if ETF_PAT.search(name):
        return False
    return True


def _get(url):
    return json.loads(get_bytes(url, UA))


def resolve_code(name):
    """종목명 → 코드 (네이버 autocomplete). 이름 정확 일치 우선."""
    try:
        url = "https://ac.stock.naver.com/ac?q=" + urllib.parse.quote(name) + "&target=stock"
        items = _get(url).get("items") or []
        stocks = [it for it in items if it.get("category") == "stock" and it.get("nationCode") == "KOR"]
        if not stocks:
            return None
        for it in stocks:
            if it.get("name") == name:
                return it.get("code")
        return stocks[0].get("code")  # 정확 일치 없으면 첫 후보
    except Exception:
        return None


def top_ranking(sort_key, market, n):
    sort = SORT.get(sort_key, "up")
    # 필터로 일부 빠지므로 넉넉히 받아서 n개 채움
    url = f"https://m.stock.naver.com/api/stocks/{sort}/{market}?page=1&pageSize={n * 3}"
    d = _get(url)
    out = []
    for s in d.get("stocks", []):
        name, code = s.get("stockName"), s.get("itemCode") or s.get("reutersCode")
        if not is_individual_stock(name, code, s.get("stockEndType")):
            continue
        def _num(key):
            try:
                return float(str(s.get(key, "0")).replace(",", ""))
            except ValueError:
                return 0.0
        out.append({"name": name, "code": code,
                    "change_pct": _num("fluctuationsRatio"),
                    "value_mn": _num("accumulatedTradingValue")})  # 백만원 단위
        if len(out) >= n:
            break
    return out


def fetch_news(code, k=5):
    try:
        url = f"https://m.stock.naver.com/api/news/stock/{code}?pageSize={k}&page=1"
        d = _get(url)
        items = []
        groups = d if isinstance(d, list) else [d]
        for g in groups:
            for it in g.get("items", []):
                oid, aid = it.get("officeId"), it.get("articleId")
                url = f"https://n.news.naver.com/mnews/article/{oid}/{aid}" if oid and aid else None
                items.append({
                    "title": it.get("title", "").strip(),
                    "summary": (it.get("body", "") or "").strip()[:140],
                    "office": it.get("officeName"),
                    "datetime": it.get("datetime"),
                    "url": url,
                })
                if len(items) >= k:
                    return items
        return items
    except Exception as e:
        return [{"error": str(e)}]


def _normalize_title(title):
    return re.sub(r"\s+", " ", html.unescape(strip_html(title or ""))).strip()


def _search_datetime(pub_date):
    try:
        dt = datetime.strptime(pub_date, "%a, %d %b %Y %H:%M:%S %z")
        return dt.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST")
    except Exception:
        return None


def fetch_cause_candidates(code, name, base_news=None, k=12, per_query=3):
    """종목 피드 + 검색 API 원인 후보. 검색 실패 시 기존 피드만 조용히 반환."""
    base = [n for n in (base_news or []) if n.get("title")]
    out = []
    try:
        load_env(os.path.join(REPO, ".env"))
        cid = os.environ.get("NAVER_CLIENT_ID")
        secret = os.environ.get("NAVER_CLIENT_SECRET")
        if cid and secret:
            for query_tpl in CAUSE_QUERIES:
                query = query_tpl.format(n=name)
                try:
                    data = fetch_search_news(query, cid, secret, display=per_query * 2)
                except Exception:
                    continue
                for it in data.get("items", [])[:per_query * 2]:
                    title = _normalize_title(it.get("title", ""))
                    if not title:
                        continue
                    link = it.get("originallink") or it.get("link")
                    out.append({
                        "title": title,
                        "summary": strip_html(it.get("description", ""))[:200],
                        "office": source_from_link(link or ""),
                        "datetime": _search_datetime(it.get("pubDate", "")),
                        "url": link,
                        "query": query,
                    })
    except Exception:
        pass

    out.extend(base)

    seen_urls, seen_titles, deduped = set(), set(), []
    for item in out:
        url = item.get("url")
        title = _normalize_title(item.get("title", ""))
        if not title:
            continue
        title_key = title.lower()
        if (url and url in seen_urls) or title_key in seen_titles:
            continue
        if url:
            seen_urls.add(url)
        seen_titles.add(title_key)
        item2 = dict(item)
        item2["title"] = title
        deduped.append(item2)
        if len(deduped) >= k:
            break
    return deduped


def fetch_consensus(code):
    """애널리스트 컨센서스 목표주가/투자의견. upside는 collect_one에서 차트 종가로 계산."""
    try:
        d = _get(f"https://m.stock.naver.com/api/stock/{code}/integration")
        c = d.get("consensusInfo") or {}
        return {"target_price_mean": c.get("priceTargetMean"),
                "recomm_mean": c.get("recommMean"),
                "as_of": c.get("createDate")}
    except Exception as e:
        return {"error": str(e)}


def _upside(target, last_close):
    try:
        tv = float(str(target).replace(",", ""))
        return round((tv / float(last_close) - 1) * 100, 1)
    except Exception:
        return None


def collect_one(name, code):
    if not code:
        code = resolve_code(name)
    if not code:
        return {"ticker_name": name, "ticker_code": None, "error": "코드 해석 실패"}
    chart = compute_context(code, name)
    consensus = fetch_consensus(code)
    consensus["upside_pct"] = _upside(consensus.get("target_price_mean"), chart.get("last_close"))
    return {
        "ticker_name": name,
        "ticker_code": code,
        "news": fetch_news(code),
        "consensus": consensus,
        "chart": chart,
    }


def main():
    args = sys.argv[1:]
    targets = []  # (name, code)

    if "--top" in args:
        i = args.index("--top")
        sort_key = args[i + 1]
        market = args[args.index("--market") + 1] if "--market" in args else "KOSPI"
        n = int(args[args.index("--n") + 1]) if "--n" in args else 10
        for r in top_ranking(sort_key, market, n):
            targets.append((r["name"], r["code"]))
    elif "--codes" in args:
        i = args.index("--codes")
        for c in args[i + 1:]:
            if c.startswith("--"):
                break
            targets.append((c, c))
    elif "--names" in args:
        i = args.index("--names")
        for nm in args[i + 1:]:
            if nm.startswith("--"):
                break
            targets.append((nm, None))
    else:
        print("usage: --names <names...> | --codes <codes...> | --top 거래대금|상승률 --market KOSPI|KOSDAQ --n 10", file=sys.stderr)
        sys.exit(1)

    out = [collect_one(name, code) for name, code in targets]
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
