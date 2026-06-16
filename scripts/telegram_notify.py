#!/usr/bin/env python3
"""레이더 재반등 봉 텔레그램 알림 (표준 라이브러리만).

publish.py가 게시 후보를 정한 뒤 호출 → 후보의 '완성된' 자격 10분봉마다 1통 전송.
봉 시각(날짜:코드:HH:MM)으로 중복 제거 → 같은 봉 재전송 안 함(10분 회차 도배 방지),
새 자격 봉이 또 뜨면 또 전송. 토큰 미설정/전송 실패는 조용히 skip(publish 본작업 보호).

설정(Mac .env): TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""
import os
import sys
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(REPO, ".telegram_notified.json")  # gitignore
BASE = "https://stocknews-cyan.vercel.app"


def log(m):
    print(m, file=sys.stderr, flush=True)


def load_env():
    for name in (".env", os.path.join("web", ".env.local")):
        p = os.path.join(REPO, name)
        if not os.path.exists(p):
            continue
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def send(text):
    """텔레그램 sendMessage. 성공 True / 미설정·실패 False(예외 안 던짐)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False
    try:
        data = urllib.parse.urlencode({
            "chat_id": chat, "text": text, "disable_web_page_preview": "true",
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        r = json.load(urllib.request.urlopen(req, timeout=10))
        return bool(r.get("ok"))
    except Exception as e:
        log(f"[telegram] 전송 실패: {e}")
        return False


def _load_state(path):
    try:
        return json.load(open(path, encoding="utf-8")) or {}
    except Exception:
        return {}


def _save_state(path, state):
    try:
        json.dump(state, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception as e:
        log(f"[telegram] 상태 저장 실패: {e}")


def _bar_complete(bar_time_hhmm, now=None):
    """버킷 시작 'HH:MM'의 10분봉이 완성됐나(now ≥ 시작+10분). 형성 중 봉은 알림 보류."""
    now = now or datetime.now(KST)
    try:
        hh, mm = bar_time_hhmm.split(":")
        start = int(hh) * 60 + int(mm)
    except Exception:
        return False
    return (now.hour * 60 + now.minute) >= start + 10


def _format(s, bar):
    code = s.get("code") or ""
    name = s.get("name") or code
    r = s.get("reaccum") or {}
    ma20 = r.get("ma20_margin_pct")
    ivtr = r.get("ivtr_eok")
    line3 = f"등락 {s.get('change_pct')}%"
    if ma20 is not None:
        line3 += f" · MA20 +{ma20}%"
    if ivtr is not None:
        line3 += f" · 투신 +{ivtr}억"
    return "\n".join([
        f"🚨 {name} ({code}) 재반등 봉",
        f"{bar['time']} · 몸통 {bar['body_pct']}% · 거래대금 {bar['value_eok']}억",
        line3,
        f"{BASE}/stock/{code}",
    ])


def notify_reignitions(suspects, state_path=STATE_PATH, now=None):
    """게시 후보의 '완성된' 자격 10분봉마다 1통 — 봉 시각 기준 중복 제거. 보낸 건수 반환."""
    load_env()
    if not os.environ.get("TELEGRAM_BOT_TOKEN") or not os.environ.get("TELEGRAM_CHAT_ID"):
        return 0  # 미설정 → 조용히 skip
    now = now or datetime.now(KST)
    today = now.strftime("%Y%m%d")
    state = _load_state(state_path)
    sent = set(state.get(today, []))  # 오늘 보낸 "코드:HH:MM" 집합
    n_sent = 0
    for s in suspects:
        code = s.get("code")
        for bar in s.get("reignition_bars") or []:
            if not _bar_complete(bar.get("time", ""), now):
                continue  # 아직 형성 중인 봉 → 다음 회차에
            key = f"{code}:{bar['time']}"
            if key in sent:
                continue
            if send(_format(s, bar)):
                sent.add(key)
                n_sent += 1
    if n_sent:
        _save_state(state_path, {today: sorted(sent)})  # 오늘 것만 유지(과거 자동 정리)
    return n_sent
