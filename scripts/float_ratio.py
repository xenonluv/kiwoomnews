#!/usr/bin/env python3
"""유동비율(free float ratio) 조회 — 네이버 finance coinfo의 iframe(navercomp.wisereport.co.kr) 스크랩.

KIS·네이버모바일은 상장주식수(전체)만 줘서 '유통주식 회전율'(거래대금/유통시총)을 못 낸다. 유동비율은
이 wisereport 페이지의 "발행주식수/유동비율" 행에만 있어 HTML 스크랩으로 가져온다(무인증·UTF-8).

유동비율은 분기 단위로 천천히 변하므로 data/float_ratio.json에 영속 캐시(코드별, 7일 만료)한다.
fail-safe: 네트워크·파싱 실패 시 None → 호출부가 '전체 시총 기준'으로 폴백(조용한 오작동 금지, 경고 로그).
표준라이브러리만.
"""
import os
import re
import sys
import json
import urllib.request
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_PATH = os.path.join(REPO, "data", "float_ratio.json")
CACHE_TTL_DAYS = 7  # 유동비율은 분기 변동 — 일주일 캐시면 충분
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
# "발행주식수/유동비율" 행의 **해당 td 셀만** 잡는다(.*? 가 다음 행 숫자로 넘어가 오매칭하는 것 방지).
_ROW_RE = re.compile(r"발행주식수/유동비율\s*</th>\s*<td[^>]*>(.*?)</td>", re.S)
_CELL_RE = re.compile(r"([\d,]+)\s*주\s*/\s*([\d.]+)\s*%")  # 셀 안에서 "발행주식수주 / 유동비율%"


def _log(m):
    print(m, file=sys.stderr, flush=True)


def _load_cache():
    try:
        d = json.load(open(CACHE_PATH, encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_cache(cache):
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        # 원자적 저장(tmp+replace) — 쓰기 도중 종료(Mac 잠자기 등) 시 캐시 truncate/손상 방지
        tmp = CACHE_PATH + ".tmp"
        json.dump(cache, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, CACHE_PATH)
    except Exception as e:
        _log(f"[float] 캐시 저장 실패: {e}")


def _in_band(r):
    """유효 유동비율(0.03~1.0)인지 — 캐시에 든 구버전·손상·이상치 값을 반환 전에 거른다."""
    return isinstance(r, (int, float)) and 0.03 <= r <= 1.0


def _fetch(code):
    """wisereport에서 (유동비율 0~1, 발행주식수) 파싱. 실패 시 (None, None)."""
    url = (f"https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx"
           f"?cmp_cd={code}&target=finsum_more")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                   "Referer": "https://finance.naver.com/"})
        raw = urllib.request.urlopen(req, timeout=8).read()
    except Exception as e:
        _log(f"[float] {code} 조회 실패: {e}")
        return None, None
    html = raw.decode("utf-8", "replace")
    row = _ROW_RE.search(html)
    cell = _CELL_RE.search(row.group(1)) if row else None
    if not cell:  # 셀 없음·'-'·N/A(신규상장/거래정지 등) → 폴백
        _log(f"[float] {code} 유동비율 파싱 실패(구조 변경·값 없음)")
        return None, None
    try:
        listed = int(cell.group(1).replace(",", ""))
        ratio = float(cell.group(2)) / 100.0
    except ValueError as e:  # 깨진 숫자(예 '4.9.1') — 크래시 대신 폴백
        _log(f"[float] {code} 유동비율 숫자 파싱 실패: {e}")
        return None, None
    # 3%~100%만 유효. <3%(품절주·이상치)는 유통시총이 극소→회전율 폭증(거짓 초고회전)이라 폴백시킨다.
    if not (0.03 <= ratio <= 1.0):
        _log(f"[float] {code} 유동비율 이상치 {ratio*100:.1f}% — 무시(시총 기준 폴백)")
        return None, None
    return ratio, listed


def get_float_ratio(code, cache=None):
    """유동비율(0~1) | None. 7일 캐시. None이면 호출부는 전체 시총 기준으로 폴백할 것.

    ⚠ 보통주(6자리·끝자리 0)만 조회 — wisereport는 우선주(005935 등)·ETN 코드를 **보통주 페이지로 합쳐**
    응답해 유동비율이 보통주 것으로 오귀속된다(실측). 비보통주는 None→시총 기준 폴백."""
    if not (len(code) == 6 and code.isdigit() and code[5] == "0"):
        return None
    own = cache is None
    if own:
        cache = _load_cache()
    today = datetime.now(KST).strftime("%Y%m%d")
    rec = cache.get(code)
    # 캐시 값은 반환 전 밴드 검증 — _fetch만 밴드를 체크하므로, 구버전 코드가 쓴/손상된 밴드 밖
    # 값(예 0.01)이 그대로 새어 나가면 유통회전율이 ~100배 부풀어 점수가 거짓 포화된다.
    cached = rec.get("ratio") if rec else None
    if not _in_band(cached):
        cached = None
    if cached is not None and rec.get("date"):
        try:
            age = (datetime.strptime(today, "%Y%m%d")
                   - datetime.strptime(rec["date"], "%Y%m%d")).days
            if age < CACHE_TTL_DAYS:
                return cached
        except Exception:
            pass
    ratio, listed = _fetch(code)
    if ratio is not None:  # 성공한 경우에만 캐시 갱신(실패 시 과거 캐시 보존)
        cache[code] = {"ratio": ratio, "listed": listed, "date": today}
        if own:
            _save_cache(cache)
    elif cached is not None:
        return cached  # 신선도 지났어도 밴드 내 과거 값 폴백(스크랩 일시 실패 < 무값)
    return ratio


def get_float_and_listed(code, cache=None):
    """(유동비율 0~1 | None, 발행주식수 int | None) — 폭발일 시총 복원(과거 봉)용. get_float_ratio와 동일 경로."""
    own = cache is None
    if own:
        cache = _load_cache()
    r = get_float_ratio(code, cache=cache)
    if own:
        _save_cache(cache)  # 콜드/만료 스크랩분을 디스크에 영속(없으면 bootstrap이 매 회차 재스크랩)
    if r is None:
        return None, None
    return r, (cache.get(code) or {}).get("listed")


if __name__ == "__main__":
    codes = sys.argv[1:] or ["010690"]
    for c in codes:
        r = get_float_ratio(c)
        print(f"{c}: 유동비율 = {r*100:.2f}%" if r is not None else f"{c}: 유동비율 = None(폴백)")
