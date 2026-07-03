# -*- coding: utf-8 -*-
"""투자경고 지정해제 예측 (회장님 지시 2026-07-03 — "내일부터 해제될 예정인 경고 종목은 최대 가산점·최상위로").

KRX 공식 (KIND 공시 원문 실측 — SK스퀘어 2025-12-24 '투자경고종목 지정해제'):
  지정일부터 기산 10매매거래일 이상 경과한 판단일(T)에 아래 3요건 '모두' 충족 시 다음 매매거래일 지정해제.
    ① T 종가 < T-5(5매매일 전) 종가 × 1.45   (5일 +45% 미만)
    ② T 종가 < T-15(15매매일 전) 종가 × 1.75 (15일 +75% 미만)
    ③ T 종가가 최근 15매매일 종가 중 최고가가 아님
  → 장중 15:1x 시점의 현재가를 '가상 종가'로 넣어 "오늘 마감 시 요건 충족 → 내일 해제"를 선제 예측.

지정일 소스 = 네이버 PC 공시목록(finance.naver.com/item/news_notice.naver, EUC-KR 서버렌더·제목 뒤 날짜)의
'투자경고종목 지정' 공시(예고·해제 제외) 공시일. KRX는 지정 전일 장마감 후 공시하므로
**지정일 = 공시일 다음 매매거래일**(금호건설 실측: 6/30 공시 → 7/1 지정 → 7/2 "지정중" 정지·재개 정합).
공시가 스캔 페이지(기본 8p) 밖이면 '오래된 지정'(OLD)으로 보고 10일 경과 충족으로 간주 — 단 이는
**페이지들이 정상 파싱됐을 때만**. 1페이지부터 행이 안 읽히면(차단·개편) None(예측 포기·강등 유지) —
신규 지정 종목을 '오래된 지정'으로 오판해 가산점 오폭하는 실패 모드 차단.

⚠ 투자'위험' 해제는 경고로 강등되는 별개 절차라 미지원(경고 전용). 예측이지 보장 아님 — KRX 최종 판단.
코어 모듈(scripts/) — agent_alpha가 읽기전용 import(Option A). 코어는 agent_alpha를 참조하지 않음.
"""
import re
from net import get_bytes

UA_PC = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}
_NOTICE_CACHE = {}   # code -> "YYYYMMDD"(지정 공시일) | "OLD" | None — 실행당 캐시

RELEASE_MIN_ELAPSED = 10   # 지정일 기산 최소 매매일
RELEASE_5D_X = 1.45        # ① 5일 상승 배수 상한
RELEASE_15D_X = 1.75       # ② 15일 상승 배수 상한
RELEASE_MAX_WIN = 15       # ③ 최고가 판정 창

_ROW_RE = re.compile(r'<a[^>]*class="tit"[^>]*>([^<]+)</a>.*?(\d{4})\.(\d{2})\.(\d{2})', re.S)


def designation_notice_date(code, max_pages=8):
    """최근 '투자경고종목 지정' 공시일 "YYYYMMDD" | "OLD"(스캔 밖=오래된 지정) | None(실패/해제됨).

    목록은 최신순·제목 뒤 날짜 — 첫 매치가 최근 이벤트. '지정해제'가 '지정'보다 먼저 나오면
    이미 해제된 것(alert_now와 모순)이라 방어적으로 None."""
    if code in _NOTICE_CACHE:
        return _NOTICE_CACHE[code]
    found = None
    try:
        for page in range(1, max_pages + 1):
            raw = get_bytes(
                f"https://finance.naver.com/item/news_notice.naver?code={code}&page={page}",
                UA_PC).decode("euc-kr", "ignore")
            rows = _ROW_RE.findall(raw)
            if not rows:
                # 1페이지부터 빈 파싱 = 차단/레이아웃 변경 의심 → None. 2페이지 이후 빈 목록 = 공시 소진 → OLD.
                found = "OLD" if page > 1 else None
                break
            hit = False
            for title, y, m, d in rows:
                # '매매거래 정지 및 재개(투자경고종목 지정중)' 같은 파생 공시 오매칭 방지 —
                # 지정 이벤트 원공시만: 예고/재지정/지정중/매매거래(정지·재개) 전부 제외.
                if ("투자경고종목" not in title or "지정예고" in title or "재지정" in title
                        or "지정중" in title or "매매거래" in title):
                    continue
                if "지정해제" in title:
                    found, hit = None, True     # 최근 이벤트가 해제 — 현재 미지정으로 판단(방어)
                    break
                if "지정" in title:
                    found, hit = f"{y}{m}{d}", True
                    break
            if hit:
                break
        else:
            found = "OLD"                       # max_pages 전부 정상 파싱했는데 지정 공시 없음 = 오래된 지정
    except Exception:
        found = None                            # 네트워크/파싱 실패 — 예측 포기(fail-safe)
    _NOTICE_CACHE[code] = found
    return found


def forecast_release(daily, current_price, notice_date):
    """KRX 3요건 + 10일 경과 판정 → True(내일 해제 예상) / False / None(판정불가).

    daily = [{"date":"YYYYMMDD","close":...}, ...] 오름차순(마지막 행=오늘이면 종가를 가상 종가로 치환),
    current_price = 가상 종가(장중 현재가/마감 후 종가), notice_date = designation_notice_date() 반환값."""
    if notice_date is None or not current_price:
        return None
    closes = [(b.get("date"), b.get("close")) for b in (daily or []) if b.get("close")]
    if len(closes) < RELEASE_MAX_WIN + 1:
        return None                                   # T-15 판정 불가(신규상장 등) — 날조 금지
    dates = [d for d, _ in closes]
    vals = [c for _, c in closes[:-1]] + [float(current_price)]   # 마지막 행 종가 → 가상 종가
    # 10매매일 경과(지정일 기산·판단일 포함) — 지정일 = 공시일 '다음' 매매거래일
    if notice_date == "OLD":
        elapsed_ok = True
    else:
        desig_idx = next((i for i, d in enumerate(dates) if d > notice_date), None)
        if desig_idx is None:
            return False                              # 지정일이 아직 미래(공시 당일 저녁 등) — 해제 불가
        elapsed_ok = (len(dates) - desig_idx) >= RELEASE_MIN_ELAPSED
    if not elapsed_ok:
        return False
    c_t = vals[-1]
    c_5, c_15 = vals[-6], vals[-(RELEASE_MAX_WIN + 1)]
    if not (c_5 and c_15):
        return None
    return bool(c_t < c_5 * RELEASE_5D_X
                and c_t < c_15 * RELEASE_15D_X
                and c_t < max(vals[-RELEASE_MAX_WIN:]))   # 동률 최고가도 '최고가'로 보아 보수적 불충족


def forecast_release_for(code, daily, current_price):
    """지정공시 조회 + 판정 원샷 — radar/quant 공용 진입점. 실패 None(fail-safe)."""
    try:
        return forecast_release(daily, current_price, designation_notice_date(code))
    except Exception:
        return None
