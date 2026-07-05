#!/usr/bin/env python3
"""자동매매 공용 — KV 토글 읽기 · 포지션 저장 · 안전필터 · 로그. 표준라이브러리 전용.

포지션 파일: data/autotrade_positions.json
KV 계약(웹 토글 ↔ Windows 실행기 브리지, Upstash REST):
  autotrade:enabled = "1"|"0" (없거나 "1"아니면 OFF)
"""
import os
import sys
import json
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kiwoom_client as kw  # _load_env 재사용(.env 로드)

KST = timezone(timedelta(hours=9))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RADAR_JSON = os.path.join(REPO, "web", "data", "radar.json")
POS_PATH = os.path.join(REPO, "data", "autotrade_positions.json")
LOG_PATH = os.path.join(REPO, "autotrade.log")

BUY_KRW = 1_000_000          # 고정 매수 금액
STOP_LOSS_PCT = -5.0         # 전량 손절
TP1_PCT = 7.0                # 1차 익절(50%)
TP1_FRACTION = 0.5
TP2_PCT = 11.0               # 잔량 익절
BREAKEVEN_PCT = 0.5          # 1차 익절 후 잔량이 진입가 근처(≤+0.5%)로 재하락하면 본전 매도
FORCE_EXIT_HHMM = 1450       # 전날 이월 포지션 강제 전량 시장가 청산 시각(HHMM 이후) — 15:18 새 1위 갈아타기 준비


def log(msg):
    ts = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    sys.stderr.write(line + "\n")


def today_str():
    return datetime.now(KST).strftime("%Y%m%d")


def past_force_exit(now=None):
    """현재 KST가 강제청산 시각(FORCE_EXIT_HHMM) 이후인지. 테스트훅 AUTOTRADE_FORCE_EXIT=1이면 시각 무관 True."""
    if os.environ.get("AUTOTRADE_FORCE_EXIT") == "1":
        return True
    now = now or datetime.now(KST)
    return int(now.strftime("%H%M")) >= FORCE_EXIT_HHMM


def notify_trade(text):
    """자동매매 텔레그램 알림 — telegram_notify 재사용. fail-safe(미설정·실패여도 매매 진행)."""
    try:
        import telegram_notify as tn
        tn.load_env()
        return tn.send(text)
    except Exception as e:
        log(f"[notify] 텔레그램 실패(무시): {e}")
        return False


# ── KV(Upstash REST) 토글 ────────────────────────────────────────────
def _kv_creds():
    kw._load_env()
    url = os.environ.get("KV_REST_API_URL")
    tok = (os.environ.get("KV_REST_API_TOKEN")
           or os.environ.get("KV_REST_API_READ_ONLY_TOKEN"))
    return url, tok


def kv_get(key):
    """Upstash REST GET. 미설정/실패 시 None."""
    url, tok = _kv_creds()
    if not url or not tok:
        return None
    try:
        req = urllib.request.Request(
            url.rstrip("/") + "/get/" + urllib.parse.quote(key),
            headers={"Authorization": "Bearer " + tok})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.load(r).get("result")
    except Exception as e:
        log(f"[kv] get {key} 실패: {e}")
        return None


def autotrade_enabled():
    """웹 토글 상태. KV 미설정이면 False(안전 기본 = OFF).

    테스트 전용: AUTOTRADE_FORCE_ON=1 이면 KV 없이 ON 취급(실발주는 여전히 AUTOTRADE_LIVE=1 필요)."""
    if os.environ.get("AUTOTRADE_FORCE_ON") == "1":
        return True
    return kv_get("autotrade:enabled") == "1"


# ── 포지션 파일 ──────────────────────────────────────────────────────
def load_positions():
    """포지션 로드. 파일 부재=정상 빈 상태. 파일 존재하나 읽기 실패=상태 불명 → 예외 전파(fail-closed).

    ⚠ 빈 상태로 fallback 금지 — bought_today 중복매수 방지·청산 규칙의 유일한 근거라, empty로 열리면
    (Windows 파일락 등 일시 오류에) 중복 실매수·이중 매도를 부른다. 일시 오류 대비 짧은 재시도만.
    """
    if not os.path.exists(POS_PATH):
        return {"positions": []}
    last = None
    for i in range(3):
        try:
            return json.load(open(POS_PATH, encoding="utf-8"))
        except Exception as e:
            last = e
            time.sleep(0.3 * (i + 1))
    log(f"[pos] 로드 실패(재시도 후 {last}) — 파일 존재/읽기불가, 상태 불명 → fail-closed(매매 중단)")
    raise last


def save_positions(data):
    """원자적 저장 + 일시 오류 재시도. 최종 실패 시 예외 전파(호출부가 후속 발주 차단)."""
    os.makedirs(os.path.dirname(POS_PATH), exist_ok=True)
    tmp = POS_PATH + ".tmp"
    last = None
    for i in range(3):
        try:
            json.dump(data, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            os.replace(tmp, POS_PATH)
            return
        except Exception as e:
            last = e
            time.sleep(0.3 * (i + 1))
    log(f"[pos] 저장 실패(재시도 후): {last}")
    raise last


def open_positions(data=None):
    data = data or load_positions()
    return [p for p in data["positions"] if p.get("status") == "open"]


def bought_today(data=None):
    """오늘 이미 진입한 포지션이 있으면 True(일 1회 매수 디둡)."""
    data = data or load_positions()
    t = today_str()
    return any(p.get("entry_date") == t for p in data["positions"])


# ── 레이더 1위 + 안전필터 ────────────────────────────────────────────
def top_suspect():
    """메인 레이더 1위(suspects[0]). 없으면 None."""
    if not os.path.exists(RADAR_JSON):
        return None
    try:
        d = json.load(open(RADAR_JSON, encoding="utf-8"))
    except Exception as e:
        log(f"[radar] radar.json 로드 실패: {e}")
        return None
    sus = d.get("suspects") or []
    return sus[0] if sus else None


def reconcile(data=None, acct=None):
    """봇 오픈 포지션 vs 실계좌 보유 대조(읽기전용).

    acct: 미리 조회한 kiwoom_trade.account_holdings() 결과(없으면 조회).
    반환: {rows[], manual_holdings[], summary}. rows status:
      OK                = 실계좌 매도가능 ≥ 봇 기록수량
      QTY_SHORT         = 실계좌 매도가능 < 봇 기록(수동매도 등으로 부족)
      MISSING_IN_ACCOUNT= 봇은 보유로 아는데 실계좌에 없음(수동매도/미체결)
    manual_holdings = 실계좌엔 있으나 봇이 안 산 종목(회장님 수동 보유 — 봇이 절대 안 건드림).
    """
    data = data or load_positions()
    if acct is None:
        import kiwoom_trade as kt
        acct = kt.account_holdings()
    acct_by_code = {h["code"]: h for h in acct["holdings"]}
    bot = {p["code"]: p for p in open_positions(data)}
    rows = []
    for code, p in bot.items():
        h = acct_by_code.get(code)
        avail = h["tradable_qty"] if h else 0
        need = p.get("qty_open", 0)
        status = "MISSING_IN_ACCOUNT" if avail <= 0 else ("QTY_SHORT" if avail < need else "OK")
        rows.append({"code": code, "name": p.get("name", ""), "status": status,
                     "bot_qty": need, "acct_tradable": avail})
    manual = [h for c, h in acct_by_code.items() if c not in bot]
    return {"rows": rows, "manual_holdings": manual, "summary": acct["summary"]}


def safety_ok(suspect):
    """자동매매 안전 게이트. (ok, reason)."""
    if not suspect:
        return False, "레이더 1위 없음(빈 레이더)"
    if suspect.get("change_basis") == "NXT":
        return False, "change_basis=NXT(야간가 기준 — 정규장 실거래 아님)"
    if suspect.get("alert_now") in ("경고", "위험"):
        return False, f"시장경보 {suspect.get('alert_now')} 지정(매매정지 리스크)"
    return True, "ok"
