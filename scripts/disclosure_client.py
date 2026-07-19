# -*- coding: utf-8 -*-
"""KOSCOM 전달 공시 목록/본문의 작은 공용 클라이언트.

네이버 금융은 KRX/KOSCOM 공시를 종목별 목록으로 전달한다. 이 모듈은 전송과 HTML 정리만
담당하며, 투자경고 해제나 거래정지 여부 같은 업무 판정은 호출자가 맡는다.
"""
import html
import re
from urllib.parse import parse_qs, urljoin, urlparse

from net import get_bytes


UA_PC = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}
NOTICE_BASE = "https://finance.naver.com"
_NOTICE_ROW_RE = re.compile(
    r'<a(?=[^>]*class=["\']tit["\'])(?=[^>]*href=["\']([^"\']+)["\'])[^>]*>'
    r'(.*?)</a>.*?<td[^>]*class=["\']date["\'][^>]*>\s*'
    r'(\d{4})\.(\d{2})\.(\d{2})',
    re.S | re.I,
)


class DisclosureUnavailable(RuntimeError):
    """목록이나 본문을 신뢰할 수 있게 읽지 못한 경우."""


def clean_title(value):
    return html.unescape(re.sub(r"<[^>]+>", "", value or "")).strip()


def parse_notice_rows(raw):
    rows = []
    for href, title, year, month, day in _NOTICE_ROW_RE.findall(raw or ""):
        url = urljoin(NOTICE_BASE, html.unescape(href))
        query = parse_qs(urlparse(url).query)
        rows.append({
            "href": url,
            "title": clean_title(title),
            "date": f"{year}{month}{day}",
            "notice_id": str((query.get("no") or [""])[0]),
            "code": str((query.get("code") or [""])[0]),
        })
    return rows


def html_to_text(raw):
    text = re.sub(r"(?is)<style.*?</style>|<script.*?</script>", " ", raw or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text).replace("\xa0", " ")
    return "\n".join(
        re.sub(r"[ \t]+", " ", line).strip()
        for line in text.splitlines() if line.strip()
    )


def fetch_notice_rows(code, max_pages=2, fetcher=None):
    """종목 공시 목록. 첫 페이지 파싱 실패를 '공시 없음'으로 위장하지 않는다."""
    fetcher = fetcher or get_bytes
    code = str(code).strip().lstrip("A").zfill(6)
    result = []
    for page in range(1, max_pages + 1):
        try:
            raw = fetcher(
                f"{NOTICE_BASE}/item/news_notice.naver?code={code}&page={page}",
                UA_PC,
            ).decode("euc-kr", "ignore")
        except Exception as exc:
            raise DisclosureUnavailable(f"notice_list_fetch:{type(exc).__name__}") from exc
        rows = parse_notice_rows(raw)
        if not rows:
            if page == 1:
                raise DisclosureUnavailable("notice_list_empty")
            break
        result.extend(row for row in rows if not row.get("code") or row["code"] == code)
    return result


def fetch_notice_body(row, fetcher=None):
    """목록 행의 공시 본문을 평문과 원문으로 반환한다."""
    fetcher = fetcher or get_bytes
    notice_id = str((row or {}).get("notice_id") or "")
    if not notice_id:
        href = str((row or {}).get("href") or "")
        notice_id = str((parse_qs(urlparse(href).query).get("no") or [""])[0])
    if not notice_id:
        raise DisclosureUnavailable("notice_id_missing")
    url = f"{NOTICE_BASE}/item/news_notice_read_content.naver?no={notice_id}"
    try:
        raw = fetcher(url, UA_PC).decode("euc-kr", "ignore")
    except Exception as exc:
        raise DisclosureUnavailable(f"notice_body_fetch:{type(exc).__name__}") from exc
    text = html_to_text(raw)
    if not text:
        raise DisclosureUnavailable("notice_body_empty")
    return {"text": text, "raw": raw, "content_url": url}
