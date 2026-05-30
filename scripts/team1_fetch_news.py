#!/usr/bin/env python3
"""팀원1 — 네이버 뉴스 수집기.
.env의 NAVER_CLIENT_ID/SECRET로 실시간 뉴스를 받아 팀원1 출력 스키마(JSON 배열)로 정리.

사용: python3 scripts/team1_fetch_news.py [검색어 ...]
출력: stdout에 JSON 배열 (news_id, timestamp, title, content, source)
"""
import os
import re
import sys
import json
import html
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))


def load_env(path=".env"):
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def strip_html(s):
    return re.sub(r"<[^>]+>", "", html.unescape(s)).strip()


def source_from_link(link):
    try:
        host = urllib.parse.urlparse(link).netloc
        return host.replace("www.", "").split(".")[0]
    except Exception:
        return "unknown"


def fetch(query, cid, secret, display=20):
    url = (
        "https://openapi.naver.com/v1/search/news.json?query="
        + urllib.parse.quote(query)
        + f"&display={display}&sort=date"
    )
    req = urllib.request.Request(url)
    req.add_header("X-Naver-Client-Id", cid)
    req.add_header("X-Naver-Client-Secret", secret)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def main():
    load_env()
    cid = os.environ.get("NAVER_CLIENT_ID")
    secret = os.environ.get("NAVER_CLIENT_SECRET")
    if not cid or not secret:
        print("ERROR: NAVER_CLIENT_ID/SECRET 없음 (.env 확인)", file=sys.stderr)
        sys.exit(1)

    queries = sys.argv[1:] or ["특징주", "급등주", "상한가"]
    seen_titles = set()
    items = []
    for q in queries:
        try:
            data = fetch(q, cid, secret)
        except Exception as e:
            print(f"WARN: '{q}' 조회 실패: {e}", file=sys.stderr)
            continue
        for it in data.get("items", []):
            title = strip_html(it["title"])
            if title in seen_titles:
                continue
            # 광고/홍보성 간단 필터
            if any(w in title for w in ["[부고]", "[인사]", "광고", "분양", "이벤트"]):
                continue
            seen_titles.add(title)
            try:
                dt = datetime.strptime(it["pubDate"], "%a, %d %b %Y %H:%M:%S %z")
                ts = dt.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                ts = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
            items.append(
                {
                    "timestamp": ts,
                    "title": title,
                    "content": strip_html(it["description"])[:200],
                    "source": source_from_link(it.get("originallink") or it.get("link", "")),
                }
            )

    # 최신순 정렬 후 news_id 부여
    items.sort(key=lambda x: x["timestamp"], reverse=True)
    today = datetime.now(KST).strftime("%Y%m%d")
    out = []
    for i, it in enumerate(items, 1):
        out.append({"news_id": f"{today}_{i:03d}", **it})

    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
