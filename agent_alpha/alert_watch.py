"""시장경보 감시·예측 — 투자주의(현재 지정 표시) + 투자경고 예고/지정 요건 계산 (회장님 지시 2026-07-03).

KRX 시장경보는 공개 가격 공식으로 지정된다(KIND 공시 원문 추출):
- 투자경고 지정예고(가격형): 당일 종가가 3거래일 전일 대비 +100%↑ / 5거래일 전일 대비 +60%↑ / 15거래일 전일 대비 +100%↑
- 투자경고 지정: [단기급등] 5일 전 대비 +60%↑ AND 당일 종가가 최근 15일 종가 최고 AND 주가상승률이 지수 상승률의 5배↑
              [중장기급등] 15일 전 대비 +100%↑ AND 15일 신고가 AND 지수의 3배↑
→ 15:10 수집 시점의 현재가를 '가상 종가'로 넣으면 마감 직전 예측 완성.
⚠ 투자주의(1단계)는 소수계좌 집중 등 비공개 계좌 데이터 유형이 많아 예측 불가 — 네이버 marketAlertType로
  '현재 지정 상태'만 표시. 예측은 가격형 주 유형만 구현(불건전요건 결합 등 예외 미구현) — "예상"이지 보장 아님.
점수·정렬 무반영(정보 배지 전용 — 회피가 아니라 "알고 들어가 폭락을 추가매수 기회로" 쓰는 용도).
fail-safe: 모든 실패는 None(날조 금지)."""
import json
import urllib.request

_IDX_CACHE = {}


def _get(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                               "Referer": "https://m.stock.naver.com/"})
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")


def alert_now(code):
    """네이버 basic → {"level": "주의"/"경고"/"위험"/None, "halted": bool, "sosok": "0"/"1"} (실패 시 None)."""
    try:
        b = json.loads(_get(f"https://m.stock.naver.com/api/stock/{code}/basic"))
        m = (b.get("marketAlertType") or {}).get("code") if isinstance(b.get("marketAlertType"), dict) else None
        level = {"01": "주의", "02": "경고", "03": "위험"}.get(m)
        ts = b.get("tradeStopType")
        halted = isinstance(ts, dict) and ts.get("name") == "HALTED"
        return {"level": level, "halted": halted, "sosok": str(b.get("sosok", ""))}
    except Exception:
        return None


def index_closes(sosok):
    """지수 일봉 종가(오름차순, 최근 ~25개) — 네이버 fchart. 실패 시 None."""
    sym = "KOSDAQ" if str(sosok) == "1" else "KOSPI"
    if sym not in _IDX_CACHE:
        try:
            import re
            raw = _get(f"https://fchart.stock.naver.com/sise.nhn?symbol={sym}&timeframe=day&count=25&requestType=0")
            closes = []
            for it in re.findall(r'<item data="([^"]+)"', raw):
                p = it.split("|")
                if p[4] not in ("null", ""):
                    closes.append(float(p[4]))
            _IDX_CACHE[sym] = closes if len(closes) >= 16 else None
        except Exception:
            _IDX_CACHE[sym] = None
    return _IDX_CACHE[sym]


def forecast_warning(closes, idx=None, level=None):
    """closes: 일봉 종가 리스트(오름차순, 마지막 = 오늘의 실제/가상 종가). level: 현재 지정 상태("주의"/"경고"/None).
    반환 None | "경고예고 예상" | "경고지정 요건충족".
    KRX 절차상 무경보 상태면 '예고'가 먼저 나오므로, 지정 요건을 충족해도 현재 무경보면 "경고예고 예상"으로 캡.
    지수(idx)는 위치 기준 근사 정렬(개별 종목 정지일 어긋남 가능 — '예상' 배지 용도라 허용)."""
    try:
        c = [x for x in closes if x]
        if len(c) < 16:
            return None                     # 이력 부족(신규상장 등) — 판정 불가
        cur = c[-1]
        r3 = cur / c[-4] - 1
        r5 = cur / c[-6] - 1
        r15 = cur / c[-16] - 1
        high15 = cur >= max(c[-15:])
        i5 = i15 = None
        if idx and len(idx) >= 16:
            i5 = idx[-1] / idx[-6] - 1
            i15 = idx[-1] / idx[-16] - 1
        # 지정 요건(지수 조건: 지수 데이터 없으면 보수적으로 배수 조건 충족 간주 — 예고보다 강한 신호 우선 표기)
        short_ok = r5 >= 0.60 and high15 and (i5 is None or i5 <= 0 or r5 >= 5 * i5)
        long_ok = r15 >= 1.00 and high15 and (i15 is None or i15 <= 0 or r15 >= 3 * i15)
        if (short_ok or long_ok) and level in ("주의", "경고"):
            return "경고지정 요건충족"      # 이미 경보 단계 진입 종목이 지정 요건까지 충족
        if short_ok or long_ok or r3 >= 1.00 or r5 >= 0.60 or r15 >= 1.00:
            return "경고예고 예상"
        return None
    except Exception:
        return None
