#!/usr/bin/env python3
"""레이더 재반등 봉 텔레그램 알림 (표준 라이브러리만).

publish.py가 게시 후보를 정한 뒤 호출 → 후보의 '완성된' 자격 5분 스파크마다 1통 전송.
봉 시각(날짜:코드:HH:MM)으로 중복 제거 → 같은 봉 재전송 안 함(회차 도배 방지),
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
YOUTONG_STATE_PATH = os.path.join(REPO, ".youtong_notified.json")  # gitignore — youtong 알림 디둡(별도)
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
        d = json.load(open(path, encoding="utf-8"))
        return d if isinstance(d, dict) else {}  # 손상(비-dict) 파일도 안전하게 빈 상태로
    except Exception:
        return {}


def _save_state(path, state):
    try:
        # 원자적 저장(tmp+replace) — 쓰기 중 종료 시 상태 파일이 truncate돼 '오늘 보낸 봉' 집합이
        # 통째로 소실되면 같은 완성 봉에 알림이 중복 발송된다(디둡 무력화). 그것을 방지.
        tmp = path + ".tmp"
        json.dump(state, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception as e:
        log(f"[telegram] 상태 저장 실패: {e}")


def _bar_complete(bar_time_hhmm, now=None, span_min=5):
    """버킷 시작 'HH:MM'의 분봉이 완성됐나(now ≥ 시작+span_min분). 형성 중 봉은 알림 보류."""
    now = now or datetime.now(KST)
    try:
        hh, mm = bar_time_hhmm.split(":")
        start = int(hh) * 60 + int(mm)
    except Exception:
        return False
    return (now.hour * 60 + now.minute) >= start + span_min


def _format(s, bar):
    code = s.get("code") or ""
    name = s.get("name") or code
    r = s.get("reaccum") or {}
    pt = r.get("peak_turnover_pct")
    cnt = (s.get("reignition") or {}).get("count")
    line3 = f"등락 {s.get('change_pct')}%"
    if cnt is not None:
        line3 += f" · 5분 스파크 {cnt}회"
    if pt is not None:
        line3 += f" · 폭발일 회전 {pt}%"
    # 5분 양봉 스파크 게이트는 거래대금 하한이 없어 value_eok이 0/소액일 수 있음 → 0이면 거래대금 표기 생략.
    bar_line = f"{bar['time']} · 몸통 {bar['body_pct']}%"
    if (bar.get("value_eok") or 0) > 0:
        bar_line += f" · 거래대금 {bar['value_eok']}억"
    return "\n".join([
        f"🚨 {name} ({code}) 재반등 봉",
        bar_line,
        line3,
        f"{BASE}/stock/{code}",
    ])


def notify_reignitions(suspects, state_path=STATE_PATH, now=None, span_min=5):
    """게시 후보의 '완성된' 자격 봉마다 1통 — 봉 시각 기준 중복 제거. 보낸 건수 반환.
    span_min = 재반등 스파크 합성 단위(radar의 --reignition-span-min, 기본 5). 봉 완성 판정에 사용."""
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
            if not _bar_complete(bar.get("time", ""), now, span_min):
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


def _format_youtong(y):
    """곧 폭발 후보(/youtong) 알림 — 재매집('🚨 재반등 봉')과 제목·이모지·지표를 달리해 한 채팅에서 구분."""
    code = y.get("code") or ""
    name = y.get("name") or code
    parts = [f"현재 {y.get('change_pct')}%", f"유통 회전율 {y.get('vol_turnover_pct')}%"]
    if (y.get("value_eok") or 0) > 0:
        parts.append(f"거래대금 {y.get('value_eok')}억")
    lines = [f"⚡ {name} ({code}) 곧 폭발 후보", " · ".join(parts)]
    if y.get("first_seen"):
        lines.append(f"포착 {y['first_seen']}")
    lines.append(f"{BASE}/stock/{code}")
    return "\n".join(lines)


def notify_youtong(youtong, state_path=YOUTONG_STATE_PATH, now=None):
    """곧 폭발 후보(youtong) 진입 알림 — 종목·일자당 1회(디둡). 보낸 건수 반환.
    youtong은 라이브 스냅샷(밴드 들락날락)이라 봉 완성 판정 없이 '오늘 처음 뜨면 1통'. 재매집 알림과
    상태 파일(.youtong_notified.json)·메시지 형식을 분리해 구분. 토큰 미설정/실패는 조용히 skip."""
    load_env()
    if not os.environ.get("TELEGRAM_BOT_TOKEN") or not os.environ.get("TELEGRAM_CHAT_ID"):
        return 0  # 미설정 → 조용히 skip(Mac만 실송)
    now = now or datetime.now(KST)
    today = now.strftime("%Y%m%d")
    state = _load_state(state_path)
    sent = set(state.get(today, []))  # 오늘 보낸 종목코드 집합(종목·일자 1회)
    n_sent = 0
    for y in youtong or []:
        code = y.get("code")
        if not code or code in sent:
            continue
        if send(_format_youtong(y)):
            sent.add(code)
            n_sent += 1
    if n_sent:
        _save_state(state_path, {today: sorted(sent)})  # 오늘 것만 유지(과거 자동 정리)
    return n_sent
