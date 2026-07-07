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
VERY_GOOD_STATE_PATH = os.path.join(REPO, ".very_good_notified.json")  # gitignore — ⭐매우좋음 알림 디둡(별도)
DIGEST_STATE_PATH = os.path.join(REPO, ".suspects_digest_notified.json")  # gitignore — 종베 다이제스트 디둡(하루 1회)
DIGEST_START_HHMM = 1505   # 종베 다이제스트 발송 창 시작 — 15:11 publish 회차가 이 창에서 발송
DIGEST_END_HHMM = 1530     # 창 끝 — 스캔 지연·백업 회차(15:21) 여유


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


REIGNITION_MAX_AGE_MIN = 30  # 완성 후 이만큼 지난 봉은 알림 안 함(뒷북 방지). publish 10분 주기라 1~2회차 여유.


def _bar_complete(bar_time_hhmm, now=None, span_min=5, max_age_min=REIGNITION_MAX_AGE_MIN):
    """버킷 시작 'HH:MM'의 분봉이 '완성됐고 아직 신선한가'. 형성 중 봉은 보류(완성 전).
    max_age_min 지정 시 완성 후 그만큼 지난 '오래된' 봉도 False — 마감 후 NXT로 새로 밴드 진입한 종목의
    14:30~15:30 옛 스파크 봉이 90분 뒤 무더기 발송되는 회귀를 차단(완성 직후 1~2회차 안에만 알림)."""
    now = now or datetime.now(KST)
    try:
        hh, mm = bar_time_hhmm.split(":")
        start = int(hh) * 60 + int(mm)
    except Exception:
        return False
    age = (now.hour * 60 + now.minute) - (start + span_min)  # 완성 시점 대비 경과(분)
    if age < 0:
        return False  # 아직 형성 중
    if max_age_min is not None and age > max_age_min:
        return False  # 완성 후 너무 오래됨 → 뒷북 알림 방지
    return True


def _format(s, bar):
    code = s.get("code") or ""
    name = s.get("name") or code
    r = s.get("reaccum") or {}
    pt = r.get("peak_turnover_pct")
    cnt = (s.get("reignition") or {}).get("count")
    # change_pct는 정규장(KRX) 기준 — notify_reignitions가 change_basis=="NXT" 종목을 스킵하므로 여기 도달하는 건 KRX뿐.
    line3 = f"등락 {s.get('change_pct')}%"
    if cnt is not None:
        line3 += f" · 5분 스파크 {cnt}회"
    if pt is not None:
        line3 += f" · 폭발일 회전 {pt}%"
    # 5분 양봉 스파크 게이트는 거래대금 하한이 없어 value_eok이 0/소액일 수 있음 → 0이면 거래대금 표기 생략.
    bar_line = f"{bar['time']} · 몸통 {bar['body_pct']}%"
    if (bar.get("value_eok") or 0) > 0:
        bar_line += f" · 거래대금 {bar['value_eok']}억"
    # 🎯 매수급소(14:30↑ 몸통 2%+ 양봉 ≥2회, 밴드 무제한) — 일반 재반등과 제목으로 구분(회장님 지시 2026-07-03)
    if s.get("geupso"):
        title = f"🎯 {name} ({code}) 매수급소 — 2%+ 재반등"
    elif s.get("low_accum"):
        title = f"🧲 {name} ({code}) 저점매집 의심 — 폭락 중 2%+ 매집봉"
    else:
        title = f"🚨 {name} ({code}) 재반등 봉"
    return "\n".join([
        title,
        bar_line,
        line3,
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
        # 마감 후 NXT 시간외가로 밴드에 '재진입'한 종목(change_basis=="NXT")은 스킵 — reignition_bars는
        # 전부 정규장(14:30~15:30) 옛 봉이라 '지금 일어나는 신선한 재분출'이 아니다. 봉 나이 상한(30분)만으론
        # 첫 post-close 회차(15:31~)에서 15:00~15:30 봉이 아직 ≤30분이라 뒷북 발송되는데, 그 근원을 차단.
        # (정규장 중엔 change_basis가 항상 "KRX"라 정상 알림엔 영향 없음.)
        if s.get("change_basis") == "NXT":
            continue
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
    parts = []
    if y.get("change_pct") is not None:   # 지속 행 현재가 재조회 실패 시 None — "현재 None%" 방지
        parts.append(f"현재 {y.get('change_pct')}%")
    parts.append(f"유통 회전율 {y.get('vol_turnover_pct')}%")
    if (y.get("value_eok") or 0) > 0:
        parts.append(f"거래대금 {y.get('value_eok')}억")
    lines = [f"⚡ {name} ({code}) 곧 폭발 후보", " · ".join(parts)]
    if y.get("first_seen"):
        lines.append(f"포착 {y['first_seen']}")
    return "\n".join(lines)


def notify_youtong(youtong, state_path=YOUTONG_STATE_PATH, now=None):
    """곧 폭발 후보(youtong) 진입 알림 — 종목·일자당 1회(디둡). 보낸 건수 반환.
    youtong은 라이브 스냅샷(밴드 들락날락)이라 봉 완성 판정 없이 '오늘 처음 뜨면 1통'. 재매집 알림과
    상태 파일(.youtong_notified.json)·메시지 형식을 분리해 구분. 토큰 미설정/실패는 조용히 skip.
    ⚠ 기본 OFF(2026-07-02 텔레그램 개편 — 포착 시점에 이미 +20% 고위험군이 대부분이라 소음.
      15:15 🎯 종베 알림으로 대체). 부활하려면 Mac .env에 TELEGRAM_YOUTONG=1."""
    load_env()   # .env의 TELEGRAM_YOUTONG 스위치를 읽기 위해 게이트보다 먼저
    if os.environ.get("TELEGRAM_YOUTONG", "0") != "1":
        return 0
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
        if y.get("exploded"):
            continue  # 이미 폭발(고가≥22·회전율≥90)한 종목 — '곧 폭발 후보' 알림은 의미 거꾸로. /forecast·🔥배지로 커버, 텔레그램 스킵
        if send(_format_youtong(y)):
            sent.add(code)
            n_sent += 1
    if n_sent:
        _save_state(state_path, {today: sorted(sent)})  # 오늘 것만 유지(과거 자동 정리)
    return n_sent


def _format_very_good(s):
    """⭐매우좋음(흔들기 AND 6일낙폭 dd6≤-30) 알림 — 재매집·youtong과 제목·이모지로 구분."""
    code = s.get("code") or ""
    name = s.get("name") or code
    tier_label = {"tier1": "Tier1", "tier2": "Tier2(과낙)"}.get(s.get("very_good_tier"), "")
    parts = [f"현재 {s.get('change_pct')}%"]
    if s.get("high_pct") is not None:
        parts.append(f"고가 {s.get('high_pct')}%")
    if s.get("dd6_pct") is not None:
        parts.append(f"6일낙폭 {s.get('dd6_pct')}%")
    if s.get("fade_pct") is not None:
        parts.append(f"페이드 {s.get('fade_pct')}%p")
    if (s.get("value_eok") or 0) > 0:
        parts.append(f"거래대금 {s.get('value_eok')}억")
    return "\n".join([
        f"⭐ {name} ({code}) 매우좋음{f' {tier_label}' if tier_label else ''} — 흔들기+깊은눌림",
        " · ".join(parts),
        "⚠ 장중 신호(회복 시 해제 가능)·전수조사 익일 +7% 터치 72%·장중 익절 참고, 매수추천 아님",
    ])


def notify_very_good(suspects, state_path=VERY_GOOD_STATE_PATH, now=None):
    """⭐매우좋음(흔들기 AND dd6≤-30) 실시간 알림 — 종목·일자당 1회(디둡). 보낸 건수 반환.
    very_good은 장중 현재가로 재계산돼 깜빡이므로 '첫 포착 1통'(재포착 무시). 재매집/youtong과 상태파일
    (.very_good_notified.json)·메시지 분리. 기본 ON(회장님 요청) — TELEGRAM_VERY_GOOD=0으로 끔.
    토큰 미설정/실패는 조용히 skip(Mac만 실송·publish 본작업 보호)."""
    load_env()
    if os.environ.get("TELEGRAM_VERY_GOOD", "1") != "1":
        return 0
    if not os.environ.get("TELEGRAM_BOT_TOKEN") or not os.environ.get("TELEGRAM_CHAT_ID"):
        return 0  # 미설정 → 조용히 skip(Mac만 실송)
    now = now or datetime.now(KST)
    today = now.strftime("%Y%m%d")
    state = _load_state(state_path)
    sent = set(state.get(today, []))  # 오늘 보낸 종목코드 집합(종목·일자 1회)
    n_sent = 0
    for s in suspects or []:
        if not s.get("very_good"):
            continue
        code = s.get("code")
        if not code or code in sent:
            continue
        if send(_format_very_good(s)):
            sent.add(code)
            n_sent += 1
    if n_sent:
        _save_state(state_path, {today: sorted(sent)})  # 오늘 것만 유지(과거 자동 정리)
    return n_sent


def _digest_badge(s):
    if s.get("very_good"):
        return "⭐"
    if s.get("very_good_candidate"):
        return "☆"
    if s.get("shakeout"):
        return "💥"
    if s.get("geupso"):
        return "🎯"
    if s.get("low_accum"):
        return "🧲"
    return "•"


def _format_digest(suspects, now):
    """종베 다이제스트 — 오늘 suspects를 순위대로(radar.json 배열순) 한 통에."""
    lines = [f"📋 오늘 종베 후보 — suspects 순위 ({now.strftime('%H:%M')})"]
    if not suspects:
        lines.append("· 오늘 후보 없음(레이더 깨끗)")
    for i, s in enumerate(suspects[:12], 1):
        code = s.get("code") or ""
        name = s.get("name") or code
        info = [f"현재 {s.get('change_pct')}%"]
        if s.get("high_pct") is not None:
            info.append(f"고가 {s.get('high_pct')}%")
        if s.get("turnover_pct") is not None:
            info.append(f"회전 {s.get('turnover_pct')}%")
        lines.append(f"{i}. {_digest_badge(s)} {name} ({code}) · " + " · ".join(info))
    lines.append("⚠ 15:18 종가베팅 참고 · 매수추천 아님")
    return "\n".join(lines)


def notify_suspects_digest(suspects, state_path=DIGEST_STATE_PATH, now=None):
    """종베 다이제스트 — 15:10대 오늘 suspects 순위 목록 1통(하루 1회). 15:18 매수 전 참고용.
    시간게이트 15:05~15:30(15:11 publish 회차가 발송·지연/백업 여유)·하루 1회 디둡(.suspects_digest_notified.json).
    기본 ON(회장님 요청) — TELEGRAM_DIGEST=0으로 끔. 테스트훅 DIGEST_FORCE=1이면 시간게이트 무시.
    토큰 미설정/실패는 조용히 skip(Mac만 실송·publish 본작업 보호). 발송 1/미발송 0 반환."""
    load_env()
    if os.environ.get("TELEGRAM_DIGEST", "1") != "1":
        return 0
    if not os.environ.get("TELEGRAM_BOT_TOKEN") or not os.environ.get("TELEGRAM_CHAT_ID"):
        return 0  # 미설정 → 조용히 skip(Mac만 실송)
    now = now or datetime.now(KST)
    hhmm = int(now.strftime("%H%M"))
    if os.environ.get("DIGEST_FORCE") != "1" and not (DIGEST_START_HHMM <= hhmm < DIGEST_END_HHMM):
        return 0  # 15:05~15:30 창에서만(15:11 회차)
    today = now.strftime("%Y%m%d")
    if _load_state(state_path).get("date") == today:
        return 0  # 오늘 이미 발송(하루 1회)
    if send(_format_digest(suspects, now)):
        _save_state(state_path, {"date": today})
        return 1
    return 0
