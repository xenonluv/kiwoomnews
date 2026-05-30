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
import sys
import json
import urllib.parse
import urllib.request

from team3_price_context import compute_context  # 동일 scripts/ 디렉터리

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}
# 랭킹 정렬키 매핑 (네이버 m.stock api/stocks/{sort}/{market})
SORT = {"상승률": "up", "거래대금": "transactionAmount", "거래량": "tradingVolume"}


def _get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


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
    sort = SORT.get(sort_key, "transactionAmount")
    url = f"https://m.stock.naver.com/api/stocks/{sort}/{market}?page=1&pageSize={n}"
    d = _get(url)
    out = []
    for s in d.get("stocks", [])[:n]:
        out.append({"name": s.get("stockName"), "code": s.get("itemCode") or s.get("reutersCode")})
    return out


def fetch_news(code, k=5):
    try:
        url = f"https://m.stock.naver.com/api/news/stock/{code}?pageSize={k}&page=1"
        d = _get(url)
        items = []
        groups = d if isinstance(d, list) else [d]
        for g in groups:
            for it in g.get("items", []):
                items.append({
                    "title": it.get("title", "").strip(),
                    "summary": (it.get("body", "") or "").strip()[:140],
                    "office": it.get("officeName"),
                    "datetime": it.get("datetime"),
                })
                if len(items) >= k:
                    return items
        return items
    except Exception as e:
        return [{"error": str(e)}]


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
