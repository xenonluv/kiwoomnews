#!/usr/bin/env python3
"""주식 급등일보(@FastStockNews) 등 공개 텔레그램 채널 웹 미리보기에서
종목 언급을 추출 — 레이더 유니버스 보조 시드용. 표준 라이브러리만.

전략(정직한 범위 한정):
  공개 채널은 t.me/s/{channel} 웹 미리보기로 로그인 없이 최근 ~20건을 HTML로 준다.
  이 채널은 국제뉴스·시황잡담·뉴스헤드라인·공시가 섞여 있다. 두 경로로 종목을 뽑는다:
    1) **6자리 코드가 박힌 글**(DART 공시·네이버 종목링크) — 추가 네트워크 0, 노이즈 0.
    2) **헤드라인 앞머리의 종목명** — 네이버 자동완성 '정확 일치'만 코드로 해석(--no-names로 끔).
       정확 일치만 인정 → 못 잡으면 버린다(노이즈 최소). 어차피 레이더 게이트가 안전망.

격리·안전:
  - 추출된 종목은 '재료가 막 언급된' **후보일 뿐** — 레이더 재매집 게이트(폭발·식음·MA20·
    투신·재반등 봉)를 통과해야 화면 노출. 채널을 맹신하지 않는다.
  - 서드파티 채널 의존: 형식 변동·차단 가능 → 호출부는 실패해도 본작업 계속(fail-safe).

사용:
  python3 scripts/telegram_news.py                      # 기본 채널, 최근 언급 JSON
  python3 scripts/telegram_news.py FastStockNews 360 25 # 채널·max_age(분)·최대건수
"""
import urllib.request
import urllib.parse
import ssl
import re
import html
import json
import sys
import time
from datetime import datetime, timezone

DEFAULT_CHANNEL = "FastStockNews"
UA = "Mozilla/5.0 (compatible; stocknews-radar/1.0)"

MSG_RE = re.compile(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S)
TIME_RE = re.compile(r'<time[^>]*datetime="([^"]+)"')
NAME_RE = re.compile(r'기업명:\s*([^\(\n]{2,20}?)\s*\(')
# 6자리 코드: 네이버 종목링크(code=######) 또는 단축코드 표기(A######).
# A###### 정규식은 영문뉴스(VITAMIN A123456)·소수(A123456.5) 오탐을 피하려 앞뒤 문맥을 제한하고,
# _extract_codes에서 '공시 맥락'(기업명·공시 키워드)일 때만 사용한다(추가 안전망).
CODE_LINK_RE = re.compile(r'finance\.naver\.com/item/[^\s"\']*\bcode=(\d{6})')
CODE_A_RE = re.compile(r'(?<![A-Za-z])A(\d{6})(?![\d.])')

# 헤드라인 종목명 후보 토큰(한글·영문·숫자 2~12자) — 앞머리 위주로 본다.
NAME_TOKEN_RE = re.compile(r'[가-힣A-Za-z][가-힣A-Za-z0-9]{1,11}')
# 종목명일 리 없는 흔한 단어 — 자동완성 호출 절감 + 오탐 방지.
STOPWORDS = frozenset((
    "속보", "단독", "공시", "기업명", "보고서명", "공시링크", "회사정보", "시가총액",
    "미국", "이란", "중국", "일본", "한국", "이스라엘", "러시아", "우크라이나", "북한",
    "트럼프", "백악관", "대통령", "정부", "시장", "종목", "뉴스", "발표", "예정", "관련",
    "전망", "상승", "하락", "급등", "급락", "강세", "약세", "오늘", "내일", "코스피",
    "코스닥", "테마", "대장주", "특징주", "리포트", "분석", "지분", "인수", "계약", "체결",
    "출시", "개발", "공급", "수주", "확대", "투자", "실적", "매출", "영업이익", "흑자",
))


def _ac_resolve(name):
    """종목명 → 코드(네이버 자동완성, 한국 개별주 '정확 일치'만). 실패 시 None."""
    try:
        url = "https://ac.stock.naver.com/ac?q=" + urllib.parse.quote(name) + "&target=stock"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        data = json.loads(urllib.request.urlopen(req, timeout=8).read().decode("utf-8", "replace"))
    except Exception:
        return None
    for it in data.get("items") or []:
        if it.get("category") != "stock" or it.get("nationCode") != "KOR":
            continue
        if it.get("name") == name:                 # 정확 일치만 — 부분 일치 오탐 방지
            code = it.get("code") or ""
            return code if re.fullmatch(r"\d{6}", code) else None
    return None


def _get_html(channel):
    url = f"https://t.me/s/{channel}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        ctx = ssl.create_default_context()
        return urllib.request.urlopen(req, timeout=15, context=ctx).read().decode("utf-8", "replace")
    except ssl.SSLError:
        # 일부 환경 인증서 검증 실패 시 공개 읽기 전용 페이지에 한해 폴백
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return urllib.request.urlopen(req, timeout=15, context=ctx).read().decode("utf-8", "replace")


def _clean(t):
    t = re.sub(r'<br\s*/?>', '\n', t)
    t = re.sub(r'<[^>]+>', '', t)
    return html.unescape(t).strip()


def _extract_codes(text):
    """메시지 텍스트에서 (code, name) 목록 추출. name은 공시 '기업명:'에서, 없으면 ""."""
    codes = set()
    for m in CODE_LINK_RE.finditer(text):
        codes.add(m.group(1))
    if "기업명" in text or "공시" in text:   # A###### 단축코드는 공시 맥락에서만 신뢰(영문뉴스 오탐 방지)
        for m in CODE_A_RE.finditer(text):
            codes.add(m.group(1))
    if not codes:
        return []
    nm = NAME_RE.search(text)
    name = nm.group(1).strip() if nm else ""
    return [(c, name) for c in codes]


def _name_candidates(text, max_tokens=3):
    """헤드라인 앞머리에서 종목명 후보 토큰 추출(순서 유지, 중복·스톱워드 제외)."""
    head = text.lstrip()
    # 이모지·국기·기호로 시작하는 국제뉴스 줄은 앞 기호를 떼고 본다.
    head = head.split("\n", 1)[0][:60]
    out = []
    seen = set()
    for m in NAME_TOKEN_RE.finditer(head):
        tok = m.group(0)
        if tok in STOPWORDS or tok in seen or len(tok) < 2:
            continue
        if tok.isascii() and tok.isalpha() and tok.isupper():  # MOU·EO 등 약어 제외
            continue
        seen.add(tok)
        out.append(tok)
        if len(out) >= max_tokens:
            break
    return out


def _nearest_time_after(times, pos):
    for tpos, ts in times:
        if tpos >= pos:
            return ts
    return times[-1][1] if times else None


def fetch_mentions(channel=DEFAULT_CHANNEL, max_age_min=None, limit=40, resolve_names=True):
    """채널 웹 미리보기에서 최근 종목 언급 추출.

    반환: [{code, name, time(ISO), age_min, via, source:"telegram"}] — 코드 기준 dedup(최신 우선).
    via = "code"(공시·링크) | "name"(헤드라인 종목명 정확 일치).
    실패 시 예외 raise(호출부에서 fail-safe 처리). max_age_min이 주어지면 그보다 오래된 글 제외.
    resolve_names=False면 코드 박힌 글만(자동완성 호출 0).
    """
    raw = _get_html(channel)
    texts = [(m.start(), m.group(1)) for m in MSG_RE.finditer(raw)]
    times = [(m.start(), m.group(1)) for m in TIME_RE.finditer(raw)]
    now = datetime.now(timezone.utc)
    mentions = {}
    name_cache = {}  # 토큰 → 코드/None (런 내 자동완성 중복 호출 방지)

    def _age(pos):
        ts = _nearest_time_after(times, pos)
        if not ts:
            return ts, None
        try:
            return ts, round((now - datetime.fromisoformat(ts)).total_seconds() / 60, 1)
        except ValueError:
            return ts, None

    # 최신이 HTML 뒤쪽 → 뒤에서부터 보면 최신 우선 dedup
    for pos, tblock in reversed(texts):
        text = _clean(tblock)
        ts, age_min = _age(pos)
        if max_age_min is not None and age_min is not None and age_min > max_age_min:
            continue
        pairs = [(c, n, "code") for c, n in _extract_codes(text)]
        if not pairs and resolve_names:                 # 코드 없으면 헤드라인 종목명 해석
            for tok in _name_candidates(text):
                if tok not in name_cache:
                    if len(name_cache) >= 40:           # 자동완성 호출 총량 하드캡(최악 행 방지)
                        break
                    name_cache[tok] = _ac_resolve(tok)
                    time.sleep(0.1)                     # 자동완성 레이트리밋 예의
                code = name_cache[tok]
                if code:
                    pairs.append((code, tok, "name"))
                    break                               # 글당 1종목(앞머리 대표)
        for code, name, via in pairs:
            if code not in mentions:
                mentions[code] = {"code": code, "name": name, "time": ts,
                                  "age_min": age_min, "via": via, "source": "telegram"}
            elif name and not mentions[code]["name"]:
                mentions[code]["name"] = name
    return list(mentions.values())[:limit]


if __name__ == "__main__":
    argv = [a for a in sys.argv[1:] if a != "--no-names"]
    resolve = "--no-names" not in sys.argv
    ch = argv[0] if len(argv) > 0 else DEFAULT_CHANNEL
    age = float(argv[1]) if len(argv) > 1 else None
    lim = int(argv[2]) if len(argv) > 2 else 40
    try:
        out = fetch_mentions(ch, age, lim, resolve_names=resolve)
    except Exception as e:
        print(f"[error] 채널 수집 실패: {e}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\n총 {len(out)}건 종목 언급 추출", file=sys.stderr)
