"""🎯 [종베] 오늘의 TOP 후보 텔레그램 — 텔레그램 전면 개편(회장님 지시 2026-07-02)의 핵심 신설 알림.

publish_alpha.run()(비-dry)이 게시 직후 호출 — 별도 cron 없음(15:15 잠정·15:43 확정 잡 재사용).
- 선정: 오늘 movers를 **웹 AlphaList.tsx와 1:1 정렬**(fitness.close_bet_fitness desc · value_eok desc · code asc)
  → TOP2. 부적합(<45) 제외. 전원 부적합/후보 0이면 "오늘 후보 없음" 1통(기다리지 않게).
- 시간 게이트: 14:55~16:00 KST에만 발송(17:47 보정 실행 차단 — 디둡과 이중 안전). CLOSE_BET_FORCE=1로 테스트 우회.
- 디둡: data/.close_bet_notified.json {date: {"sig": 구성서명, "n": 발송수}} — 15:15 1통 후,
  15:43 실행에서 TOP 구성(코드+점수)이 달라졌을 때만 "🔄 확정 변경" 후속 1통.
- fail-safe: 코어 telegram_notify.send/load_env 재사용, 어떤 실패도 publish를 막지 않음(호출부 try).
"""
import datetime
import json
import os
import config
import fitness
import telegram_notify as tg

STATE = os.path.join(config.DATA, ".close_bet_notified.json")
TOP_N = 2
MIN_SCORE = 45                  # 부적합(<45) 제외 — fitnessTier 경계와 동일
WINDOW = ("1455", "1600")       # 발송 허용 시간창(KST)


def _tier(s):
    return "적합" if s >= 75 else "중간" if s >= 60 else "약" if s >= 45 else "부적합"


def _load():
    try:
        d = json.load(open(STATE, encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(s):
    try:
        config.ensure_dirs()
        tmp = STATE + ".tmp"
        json.dump(s, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
        os.replace(tmp, STATE)
    except Exception:
        pass


def _crash_hit(m):
    """폭락제외 벌점(과확장붕괴·연속하락4일+) 발동 여부 — fitness.py 판정과 동일 조건."""
    r6, c, ds = m.get("run_6d_pct"), m.get("change_pct"), m.get("down_streak")
    return (r6 is not None and r6 >= 100 and c is not None and c < 0) or (ds is not None and ds >= 4)


def rank(movers):
    """웹과 1:1 정렬(fitness desc·value_eok desc·code asc) 후 (mover, score) 전체 반환."""
    scored = [(m, fitness.close_bet_fitness(m)) for m in movers]
    scored.sort(key=lambda x: (-x[1], -(x[0].get("value_eok") or 0), x[0].get("code") or ""))
    return scored


def build_message(movers):
    """movers → (발송문자열, 구성서명). 후보 0/전원 부적합이면 '없음' 메시지."""
    scored = rank(movers)
    top = [(m, s) for m, s in scored if s >= MIN_SCORE][:TOP_N]
    provisional = any(m.get("provisional") for m, _ in (top or scored[:1]))
    stamp = "15:15 잠정" if provisional else "마감 확정"
    crash_n = sum(1 for m, _ in scored if _crash_hit(m))
    sig = json.dumps([[m.get("code"), s] for m, s in top])
    if not top:
        msg = "\n".join([
            f"🎯 [종베] 오늘은 종베 후보 없음 ({stamp})",
            f"전 {len(scored)}종목 부적합(<{MIN_SCORE}) — 쉬는 것도 포지션."
            + (f" · 폭락제외 발동 {crash_n}종" if crash_n else ""),
        ])
        return msg, sig
    lines = [f"🎯 [종베] 오늘의 후보 ({stamp})"]
    for i, (m, s) in enumerate(top, 1):
        chg = m.get("change_pct")
        val = m.get("value_eok")
        ssc = m.get("spark_strong_count")
        lines.append(f"{i}위 {m.get('name')} ({m.get('code')}) — {s}점({_tier(s)})")
        lines.append(
            f"   당일 {'' if chg is None else f'{chg:+.1f}%'} · 대금 {val if val is not None else '—'}억"
            f" · 강스파크 {ssc if ssc is not None else '—'}개"
        )
        # 가점/감점 근거 칩 — 산식 SSOT(fitness.close_bet_breakdown)에서 직접, 0점 칩은 생략
        chips = " · ".join(f"{k}({v:+d})" for k, v in fitness.close_bet_breakdown(m)[1] if v)
        if chips:
            lines.append(f"   {chips}")
    if crash_n:
        lines.append(f"(폭락제외 벌점 발동 {crash_n}종 — 하위 강등)")
    lines.append("전략: 익일 장중 +7% 익절 / −5% 손절 · 잠정 휴리스틱·매수추천 아님")
    return "\n".join(lines), sig


def notify(data, now=None):
    """publish_alpha가 게시 직후 호출. 보낸 수(0/1) 반환. 모든 예외는 호출부 try가 흡수."""
    tg.load_env()
    if not (os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")):
        return 0
    now = now or datetime.datetime.now(config.KST)
    hhmm = now.strftime("%H%M")
    if os.environ.get("CLOSE_BET_FORCE") != "1" and not (WINDOW[0] <= hhmm < WINDOW[1]):
        return 0
    movers = data.get("movers") or []
    if not movers:
        return 0
    date = data.get("date") or now.strftime("%Y%m%d")
    msg, sig = build_message(movers)
    st = _load()
    prev = st.get(date) or {}
    if prev.get("sig") == sig:
        return 0                                    # 같은 구성 — 침묵(15:43 무변경 등)
    if prev.get("sig") is not None:
        msg = "🔄 [종베] 확정 변경\n" + msg          # 15:15 이후 구성이 바뀐 경우만 후속
    if not tg.send(msg):
        return 0
    st[date] = {"sig": sig, "n": (prev.get("n") or 0) + 1}
    _save(st)
    return 1
