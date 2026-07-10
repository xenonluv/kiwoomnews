#!/usr/bin/env python3
"""레이더 게시 자동화 — radar.py → web/data/radar.json → 변경 시에만 push.

cron 안정성을 위해 LLM 없이 순수 Python. Vercel이 push를 받아 자동 재빌드.

사용:
  python3 scripts/publish.py --dry-run                  # /tmp 미리보기, push 안 함
  python3 scripts/publish.py --max 12 --names 한온시스템  # 실제 게시
cron(운영): 1,11,21,31,41,51 9-20 * * 1-5  cd ~/kiwoomnews && python3 scripts/publish.py >> /tmp/kiwoom_publish.log 2>&1

radar.py 인자(--reignition-body-pct --reignition-span-min/min-count --explosion-* --reaccum-*
--names 등)는 그대로 전달된다. 빈 레이더(후보 0)도 유효 상태로 게시한다.
"""
import os
import sys
import json
import tempfile
import subprocess
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import telegram_notify  # noqa: E402 — scripts/ 형제 모듈(재반등 봉 텔레그램 알림)

KST = timezone(timedelta(hours=9))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RADAR_JSON = os.path.join(REPO, "web", "data", "radar.json")
HISTORY_DIR = os.path.join(REPO, "data", "radar_history")
DISCLAIMER = "본 정보는 투자 참고용이며 매수 추천이 아닙니다. 투자 판단과 책임은 본인에게 있습니다."
RADAR_PASSTHRU = ("--reignition-body-pct", "--reignition-span-min", "--reignition-min-count",
                  "--reignition-start", "--reaccum-change-min", "--reaccum-change-max",
                  "--reaccum-max", "--explosion-vol-turnover", "--explosion-high-pct",
                  "--explosion-window", "--explosion-scan-n", "--reaccum-seed")
RADAR_BOOL_PASSTHRU = ("--no-reaccum", "--no-reaccum-visible")


RADAR_TIMEOUT = 600  # 초 — KIS/Kimi 행 멈춤 시 락 쥔 채 무한 대기 → 사이트 stale 방지


def run_radar(extra_args):
    cmd = [sys.executable, os.path.join(REPO, "scripts", "radar.py")] + extra_args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO,
                           timeout=RADAR_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        if e.stderr:
            sys.stderr.write(str(e.stderr)[-2000:])
        sys.stderr.write(f"radar 타임아웃({RADAR_TIMEOUT}초) — 이번 회차 중단, 다음 cron이 재시도\n")
        sys.exit(1)
    if r.stderr:
        sys.stderr.write(r.stderr[-2000:])  # 스킵/경고 증거를 cron 로그에 남김
    if r.returncode != 0 or not r.stdout.strip():
        sys.stderr.write(f"radar 실패 (exit {r.returncode})\n")
        sys.exit(1)
    return json.loads(r.stdout)


def acquire_git_lock():
    """모든 푸셔(publish/radar_backtest/analyzer) 공용 git 직렬화 락.

    같은 작업트리에서 pull --rebase --autostash가 겹치면 다른 프로세스가 쓰는 중인
    파일까지 스태시하는 교차 오염이 가능 — git 구간을 전 푸셔가 직렬화한다.
    blocking 대기(구간이 짧아 순서 대기가 맞음). 반환 핸들을 git 구간 동안 유지할 것.
    """
    try:
        import fcntl
        fh = open("/tmp/stocknews_git.lock", "w")
        fcntl.flock(fh, fcntl.LOCK_EX)
        return fh
    except ImportError:
        return None  # fcntl 없는 환경(Windows 등)은 락 생략


def history_date_for(out, now=None):
    """자정~개장 전 재게시분은 벽시계 날짜가 아니라 최신 신호 거래일 history에 병합한다."""
    now = now or datetime.now(KST)
    wall_date = now.strftime("%Y%m%d")
    signal_dates = []
    for s in out.get("suspects", []):
        sd = str(s.get("signal_date") or s.get("snapshot_as_of") or "")
        if len(sd) == 8 and sd.isdigit() and sd <= wall_date:
            signal_dates.append(sd)
    # 09:00 전 또는 주말 재게시에는 아직 새 정규장 데이터가 없으므로 전 거래일 이력에 기록한다.
    if signal_dates and (now.weekday() >= 5 or now.strftime("%H%M") < "0900"):
        return max(signal_dates)
    return wall_date


def record_history(out):
    """당일 수상 종목을 검증용 이력에 누적 (radar_backtest.py가 익일 평가).

    같은 날 여러 회차가 코드별로 merge — 마지막 회차(15:45)의 price가 종가 entry로 남는다.
    자정~개장 전 재게시분은 최신 신호 거래일에 병합해 익일 평가 기준일 오염을 막는다.
    수상 종목 0건인 날도 기록(표본 일수 카운트). 거래일에만 호출할 것.
    """
    os.makedirs(HISTORY_DIR, exist_ok=True)
    today = history_date_for(out)
    path = os.path.join(HISTORY_DIR, f"{today}.json")
    hist = {"date": today, "suspects": {}}
    if os.path.exists(path):
        try:
            hist = json.load(open(path, encoding="utf-8"))
        except Exception as e:
            # 손상 파일은 백업 후 재생성 — 조용한 전손 대신 흔적을 남긴다
            sys.stderr.write(f"[warn] history 손상 {path}: {e} — .corrupt 백업\n")
            try:
                os.replace(path, path + ".corrupt")
            except OSError:
                pass
    for rank, s in enumerate(out.get("suspects", []), 1):
        prev = hist["suspects"].get(s["code"], {})
        hist["suspects"][s["code"]] = {
            "name": s["name"],
            "sector": s.get("sector", ""),
            "entry": s["price"],          # 당일 종가 매수 가정 (백테스트가 일봉 종가로 재정합)
            # 통계용은 raw(가중치 적용 전) — 튜닝 체제가 바뀌어도 표본 일관성 유지
            "score": s.get("score_raw", s["suspicion_score"]),
            "breakdown": s.get("score_breakdown_raw") or s.get("score_breakdown", {}),
            # 표시 전용 — "그날 화면에서 몇 위·몇 점이었나" 추적용(통계는 위 raw score 사용).
            # reaccum은 raw=0이라 위 score/breakdown만으론 순위 재현 불가 → 표시값을 함께 남긴다.
            "suspicion_score": s.get("suspicion_score"),
            "score_breakdown_display": s.get("score_breakdown", {}),
            "rank": rank,                 # 이번 회차 게시 순위(1=최상단). 매 회차 덮어써 마지막 회차 확정값
            "change_pct": s.get("change_pct"),
            # 마감 후 NXT 시간외가로 재평가된 등락률이면 "NXT"(없거나 "KRX"=정규장). change_band_stats가
            # KRX 종가 기준 hit과 기준이 어긋나는 NXT 표본을 거르도록 기록(없으면 KRX로 간주 — 구표본 호환).
            "change_basis": s.get("change_basis"),
            "high_pct": s.get("high_pct"),
            "pattern": s.get("pattern"),
            "prime": s.get("prime", False),  # 핵심 조건 모두 충족(유력) — 향후 적중률 분리 검증용
            "theme": s.get("theme", ""),  # 상위 테마 — by_theme 성과 집계용(표시 전용, 점수 미반영)
            "material": s.get("material"),  # 뉴스/공시 재료 등급 — 전진검증용(정렬·자동매매 미반영)
            "news": s.get("news", [])[:6],  # 재료 등급 근거 재검토용. 과거 백필 불가하므로 오늘 이후만 누적.
            "value_eok": s.get("value_eok"),         # 당일 거래대금(억) — 테마 대장 판별·기록용
            # 장중 판단값(snapshot_*)과 공식 일봉 원천값(signal_*)을 분리 저장한다.
            "snapshot_open": s.get("snapshot_open"),
            "snapshot_high": s.get("snapshot_high"),
            "snapshot_low": s.get("snapshot_low"),
            "snapshot_close": s.get("snapshot_close"),
            "snapshot_volume": s.get("snapshot_volume"),
            "snapshot_value": s.get("snapshot_value"),
            "snapshot_value_eok": s.get("snapshot_value_eok"),
            "snapshot_as_of": s.get("snapshot_as_of"),
            # 신호일 원천값 — 매우좋음/흔들기 튜닝 때 dd6·MA·고점낙폭 경계를 재계산하기 위한 영구 기록.
            "signal_date": s.get("signal_date"),
            "signal_open": s.get("signal_open"),
            "signal_high": s.get("signal_high"),
            "signal_low": s.get("signal_low"),
            "signal_close": s.get("signal_close"),
            "signal_prev_close": s.get("signal_prev_close"),
            "signal_volume": s.get("signal_volume"),
            "signal_value": s.get("signal_value"),
            "signal_value_eok": s.get("signal_value_eok"),
            "signal_peak6_price": s.get("signal_peak6_price"),
            "signal_peak60_price": s.get("signal_peak60_price"),
            "signal_ma20": s.get("signal_ma20"),
            "signal_ma10": s.get("signal_ma10"),
            "signal_source": s.get("signal_source"),
            "run_6d_pct": s.get("run_6d_pct"),
            "ma20_gap_pct": s.get("ma20_gap_pct"),
            "ma10_margin_pct": s.get("ma10_margin_pct"),
            "float_ratio": s.get("float_ratio"),
            "turnover_pct": s.get("turnover_pct"),    # 당일 회전율(거래량/유통주식수 %)
            "peak_turnover_pct": s.get("peak_turnover_pct"),  # 폭발일 회전율(거래량/유통주식수 %) — backtest 구간 검증 입력
            "turnover_basis": s.get("turnover_basis"),  # "float"(유통)|"cap"(미상) — 당일 회전율 산출 기준
            # 폭발일 회전율 메트릭 버전 — 개편 전(거래대금/유통시총) 표본과 섞이지 않게 backtest 밴드가 필터.
            "turnover_metric": "vol_float",
            "theme_leader": s.get("theme_leader", False),  # 같은 테마 거래대금 1위 여부(표시 전용)
            # 메가스파크×수급 가설 검증용 피처 (radar_backtest spark_flow 표가 사용)
            "spark_max_x": s.get("spark_max_x"),
            "mega_flow": s.get("mega_flow", False),
            "flow_today_buy": bool((s.get("flow") or {}).get("today_buy")),
            "deep_shake": s.get("deep_shake"),
            "visible_experimental": s.get("visible_experimental", False),
            "reaccum": s.get("reaccum"),
            "reignition": s.get("reignition"),  # 재반등(오늘) 신호: 5분 스파크 몸통%·시각·스파크수
            "geupso": s.get("geupso", False),          # 🎯 매수급소 — 전진검증용(분봉은 당일만이라 소급 불가)
            "geupso_bars": s.get("geupso_bars"),
            "low_accum": s.get("low_accum", False),    # 🧲 저점매집 — 전진검증용
            "low_accum_bars": s.get("low_accum_bars"),
            "alert_now": s.get("alert_now"),           # KRX 시장경보 지정(주의/경고/위험) — 경고/위험 후순위 강등
            "alert_release": s.get("alert_release"),   # 🔓 투자경고 내일 해제 예정 예측 — True면 최상단 승격
            "alert_risk_released": s.get("alert_risk_released"),  # 🔓 위험→경고 강등 직후(해제공시 3일 내) — 승격 신호·전진검증용(서산 원형 2026-07-10)
            "alert_elapsed_days": s.get("alert_elapsed_days"),    # 경고 지정 경과 매매일수(1=첫날·999=오래된 지정·None=판정불가) — 경과일별 성과 전진검증용
            "shakeout": s.get("shakeout", False),      # 💥 흔들기(고가+20%↑·페이드15%p↑·회전40%↑·MA20위) — 전진검증용
            "fade_pct": s.get("fade_pct"),
            # 흔들기 강도 튜닝용 변별 변수 — 익일결과와 상관분석해 회전/낙폭 스윗존·티어 경계 최적화(회장님 20년룰 검증).
            "turnover_2d_pct": s.get("turnover_2d_pct"),   # 2일 합산 회전율(핵심 신호: 스윗90~140 vs 과열)
            "peak_dd_pct": s.get("peak_dd_pct"),           # 고점 대비 낙폭%(깊은 눌림 스윗 -30~-45)
            "strength_tier": s.get("strength_tier"),       # 결합 축(0~4) — 과거 호환 키, 강도 순위 아님(정렬 미사용)
            "strength": s.get("strength"),                 # 통계 해석 라벨(조합A~D)
            "turnover_band": s.get("turnover_band"),        # 회전율 밴드(0=스윗/1/2=과열)
            "dd_band": s.get("dd_band"),                    # 낙폭 밴드(0=스윗/1=얕음)
            "dd6_pct": s.get("dd6_pct"),                    # ⭐ 6일 고점 대비 낙폭 — 전진검증용
            "very_good": s.get("very_good", False),         # ⭐ 매우좋음(흔들기 AND dd6≤-30) — 전진검증용
            "very_good_tier": s.get("very_good_tier"),      # tier1/tier2/candidate — dd6 전용 티어
            "very_good_candidate": s.get("very_good_candidate", False),  # ⭐후보(-30<dd6≤-25) — 표시·검증용
            "tp_hint": s.get("tp_hint"),               # 회전밴드별 익절선 힌트(90~120→+7~10 등)
            "forecast": s.get("forecast"),  # 3일내+7% 확률 라벨 — 라이브 calibration 누적용

            "matched_events": [m.get("id") for m in s.get("matched_events", [])],
            "first_seen": prev.get("first_seen") or out.get("generated_at"),
            "evaluated": prev.get("evaluated", False),
            "result": prev.get("result"),
        }
    # 최종 카드 마킹: 이번 회차 "게시 카드"(--max 컷 적용 후 = 사이트에 실제 표시된
    # 종목)에 있으면 True, 없으면 False. 매 회차 덮어쓰므로 마지막 회차(15:45)가 확정값.
    # 정의: final = 마감 시 사용자가 카드에서 보고 종가 매수할 수 있었던 종목.
    # (--max 컷에 밀린 13위 이하도 False — 사용자가 볼 수 없었으므로 의도된 동작)
    current = {s["code"] for s in out.get("suspects", [])}
    for code, rec in hist["suspects"].items():
        rec["final"] = code in current
    hist["as_of"] = out.get("generated_at")
    json.dump(hist, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return path


def market_session(now=None):
    now = now or datetime.now(KST)
    if now.weekday() >= 5:
        return "closed"
    hm = now.strftime("%H%M")
    return "open" if "0900" <= hm <= "1530" else "closed"


def git(*args):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)


def main():
    args = sys.argv[1:]
    # 동시 실행 방지: 겹친 cron 회차의 git race 차단
    lock_fh = None
    try:
        import fcntl
        lock_fh = open("/tmp/stocknews_publish.lock", "w")
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("이미 실행 중(다른 publish 진행 중) — skip")
            return
    except ImportError:
        pass  # fcntl 없는 환경은 락 생략

    dry = "--dry-run" in args
    # --max 와 (구 cron 호환) --max-candidates 둘 다 허용
    max_key = "--max" if "--max" in args else ("--max-candidates" if "--max-candidates" in args else None)
    maxn = int(args[args.index(max_key) + 1]) if max_key else 12
    # reaccum이 유일 산출물이므로 기본 슬롯 = 게시 상한(maxn)과 동일 (3 슬롯 제한 해제)
    reaccum_max = int(args[args.index("--reaccum-max") + 1]) if "--reaccum-max" in args else maxn

    passthru = []
    for k in RADAR_PASSTHRU:
        if k in args:
            passthru += [k, args[args.index(k) + 1]]
    for k in RADAR_BOOL_PASSTHRU:
        if k in args:
            passthru.append(k)
    if "--names" in args:
        i = args.index("--names")
        names = []
        for nm in args[i + 1:]:
            if nm.startswith("--"):
                break
            names.append(nm)
        if names:
            passthru += ["--names"] + names
    if dry:
        passthru.append("--dry-run")

    radar = run_radar(passthru)
    regular = [s for s in radar.get("suspects", []) if not s.get("visible_experimental")]
    reaccum = [s for s in radar.get("suspects", []) if s.get("visible_experimental")]
    slots = min(max(0, reaccum_max), maxn)
    keep_reg = maxn - min(len(reaccum), slots)
    suspects = regular[:keep_reg] + reaccum[:slots]
    # 테마 대장: '실제 게시되는 집합' 기준으로 태깅(거래대금 1위) — radar.py는 컷 이전 전체라
    # 컷 후 대장 누락/외톨이(1종목 테마에 🏆)가 생김. 같은 테마 2개+일 때만.
    # 표시 전용(점수·통계 미반영). record_history·radar.json 모두 이 값을 SSOT로 사용.
    # (개편 후 reaccum이 유일 산출물 = 전부 visible_experimental이므로 실험 여부로 거르지 않는다.)
    for s in suspects:
        s["theme_leader"] = False
    theme_groups = {}
    for s in suspects:
        t = s.get("theme")
        if t:
            theme_groups.setdefault(t, []).append(s)
    for grp in theme_groups.values():
        if len(grp) >= 2:
            max(grp, key=lambda x: x.get("value_eok") or 0)["theme_leader"] = True
    # 텔레그램: 게시 후보의 새(완성된) 재반등 5분 스파크마다 알림. 봉 시각 디둡(도배 방지).
    # git 락 밖에서 먼저 호출 + 실패해도 publish 본작업 안 깨짐. push 여부와 무관히 매 회차 점검.
    # 봉 완성 판정 단위는 radar가 쓴 reignition_span_min(--reignition-span-min)과 정확히 맞춘다.
    if not dry:
        try:
            span = int(radar.get("params", {}).get("reignition_span_min") or 5)
            n = telegram_notify.notify_reignitions(suspects, span_min=span)
            if n:
                print(f"[telegram] 재반등 봉 알림 {n}건 전송")
        except Exception as e:
            print(f"[warn] 텔레그램 알림 실패(무시): {e}", file=sys.stderr)
        # 곧 폭발 후보(youtong) 진입 알림 — 종목·일자 1회 디둡(.youtong_notified.json, 재매집과 별도).
        # 별도 try로 격리(youtong 알림 실패가 재매집 알림/게시를 막지 않게).
        try:
            ny = telegram_notify.notify_youtong(radar.get("youtong", []))
            if ny:
                print(f"[telegram] 곧 폭발 후보 알림 {ny}건 전송")
        except Exception as e:
            print(f"[warn] youtong 텔레그램 알림 실패(무시): {e}", file=sys.stderr)
        # ⭐매우좋음(흔들기 AND dd6≤-30) 실시간 알림 — 종목·일자 1회 디둡(.very_good_notified.json). 별도 try 격리.
        try:
            nvg = telegram_notify.notify_very_good(suspects)
            if nvg:
                print(f"[telegram] ⭐매우좋음 알림 {nvg}건 전송")
        except Exception as e:
            print(f"[warn] 매우좋음 텔레그램 알림 실패(무시): {e}", file=sys.stderr)
        # 종베 다이제스트 — 15:10대 오늘 suspects 순위 1통(하루 1회·시간게이트는 telegram_notify 내부). 별도 try 격리.
        try:
            nd = telegram_notify.notify_suspects_digest(suspects)
            if nd:
                print("[telegram] 종베 suspects 다이제스트 전송")
        except Exception as e:
            print(f"[warn] 종베 다이제스트 알림 실패(무시): {e}", file=sys.stderr)
    out = {
        "generated_at": radar.get("generated_at"),
        "market_session": market_session(),
        "disclaimer": DISCLAIMER,
        "params": radar.get("params", {}),
        "universe_count": radar.get("universe_count", 0),
        "events": radar.get("events", []),
        "explosions": radar.get("explosions", []),  # 당일 폭발 종목(/forecast 게시용)
        "youtong": radar.get("youtong", []),         # 곧 폭발할 후보(/youtong 게시용)
        "suspects": suspects,
    }
    new = json.dumps(out, ensure_ascii=False, indent=1)

    if not dry:
        # 추적 파일(history·radar.json) 첫 쓰기 전에 공용 git 락 — 락 밖에서 쓴 미커밋
        # 변경을 타 푸셔의 autostash가 스태시/충돌로 날리는 것 방지 (쓰기~push가 보호 단위).
        # 느린 radar 스캔은 락 밖(이미 완료) — 락 보유는 쓰기+git 수 초.
        git_lock = acquire_git_lock()  # noqa: F841 — 프로세스 종료까지 유지
        # 게시 여부와 무관하게 매 회차 검증용 이력 기록 (push는 radar_backtest가 담당)
        record_history(out)
        # 이력은 쓴 즉시 로컬 커밋 — 미커밋 dirty로 남기면 락 해제 후 7분 뒤 forecast의
        # pull --rebase --autostash가 매번 스태시/팝 (충돌 시 회차 이력 유실). 커밋된
        # 변경은 autostash 무관. push는 다음 push 회차(레이더 변경 시/17:20)에 함께 실림.
        git("add", "data/radar_history")
        if git("diff", "--cached", "--quiet").returncode != 0:
            git("commit", "-q", "-m", "data: 레이더 회차 이력 기록")

    old = open(RADAR_JSON, encoding="utf-8").read() if os.path.exists(RADAR_JSON) else ""

    def strip_volatile(s):
        # generated_at만 제외 — market_session(open/closed)은 변경으로 취급해야
        # 마감 후 사이트가 "장중 스캔 중"으로 고착되지 않는다 (하루 최대 2회 push 추가).
        return "\n".join(l for l in s.splitlines() if '"generated_at"' not in l)

    if strip_volatile(new) == strip_volatile(old):
        print(f"변경 없음(수상종목 {len(out['suspects'])}건 동일) — push skip")
        return

    if dry:
        path = os.path.join(tempfile.gettempdir(), "radar_preview.json")
        open(path, "w", encoding="utf-8").write(new)
        print(f"[DRY-RUN] 수상종목 {len(out['suspects'])}건, 이벤트 {len(out['events'])}건 → {path}")
        for s in out["suspects"]:
            print(f"  - {s['name']} score={s['suspicion_score']} "
                  f"고가{s['high_pct']}% 현재{s['change_pct']}%")
        return

    os.makedirs(os.path.dirname(RADAR_JSON), exist_ok=True)
    open(RADAR_JSON, "w", encoding="utf-8").write(new)  # git 락 보유 중 (위에서 획득)
    git("add", "web/data/radar.json")
    git("commit", "-q", "-m", f"data: 레이더 자동 게시 (수상종목 {len(out['suspects'])}건)")
    # push 전 원격 변경 먼저 통합 (다중 머신 공존). 동시 푸셔(backtest·track·forecast) 경합으로
    # push가 non-fast-forward 거부되면 1회 재시도 — 단발 실패로 radar.json 커밋이 로컬에만 남아
    # 사이트가 stale해지는 것 방지(push_state retry 패턴과 정합).
    for _attempt in range(2):
        pl = git("pull", "--rebase", "--autostash", "origin", "main")
        if pl.returncode != 0:
            sys.stderr.write("pull --rebase 실패(충돌 가능) — 수동 확인 필요:\n" + pl.stderr[-500:])
            git("rebase", "--abort")
            sys.exit(1)
        pr = git("push", "origin", "main")
        if pr.returncode == 0:
            print(f"게시 완료: 수상종목 {len(out['suspects'])}건 push")
            for s in out["suspects"]:
                print(f"  - {s['name']} score={s['suspicion_score']}")
            return
    sys.stderr.write("push 실패(재시도 후):\n" + pr.stderr[-500:])
    sys.exit(1)


if __name__ == "__main__":
    main()
