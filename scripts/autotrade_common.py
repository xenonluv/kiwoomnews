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
import uuid
import urllib.request
import urllib.parse
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kiwoom_client as kw  # _load_env 재사용(.env 로드)

KST = timezone(timedelta(hours=9))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RADAR_JSON = os.path.join(REPO, "web", "data", "radar.json")
POS_PATH = os.path.join(REPO, "data", "autotrade_positions.json")
LOG_PATH = os.path.join(REPO, "autotrade.log")
EXECUTION_LOCK_PATH = "/tmp/kiwoomnews-autotrade.lock"

BUY_KRW = 1_000_000          # (하위호환) 단일 매수 금액
DAILY_BUDGET = 1_000_000     # 당일 총예산 — 최대 2종목 다종베 시 실제 매수 종목수로 균등분할
MAX_AUTOTRADE_STOCKS = 2     # 하루 최대 매수 종목 수(회장님 지시)
RADAR_DECISION_MAX_STALE_SECONDS = 30 * 60  # 결정시각 기준 최대 radar age
STOP_LOSS_PCT = -5.0         # 전량 손절
TP1_PCT = 7.0                # 1차 익절(50%)
TP1_FRACTION = 0.5
TP2_PCT = 11.0               # 잔량 익절
BREAKEVEN_PCT = 0.5          # 1차 익절 후 잔량이 진입가 근처(≤+0.5%)로 재하락하면 본전 매도
FORCE_EXIT_HHMM = 1450       # 전날 이월 포지션 강제 전량 시장가 청산 시각(HHMM 이후) — 15:18 새 1위 갈아타기 준비
NXT_PREMARKET_START = 800    # NXT 프리마켓 개장(HHMM). 08:00~08:59는 NXT 세션, 09:00부터 KRX 정규장.
KRX_CLOSE_HHMM = 1530        # KRX 정규장 마감(HHMM). 이후는 감시 무동작(closed).


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


@contextmanager
def acquire_execution_lock(kind="autotrade", nonblocking=True, timeout_seconds=0):
    """계좌 주문 프로세스를 직렬화한다. 획득 실패 시 yield False.

    운영 Mac은 fcntl을 사용한다. 지원되지 않는 플랫폼에서는 안전하게 실패해
    잠금 없이 주문 경로가 실행되지 않게 한다.
    """
    try:
        import fcntl
    except ImportError:
        log(f"[lock] {kind} fcntl 미지원 — 잠금 없이 주문할 수 없어 skip")
        yield False
        return
    fh = None
    acquired = False
    try:
        fh = open(EXECUTION_LOCK_PATH, "a+", encoding="utf-8")
        if nonblocking:
            deadline = time.monotonic() + max(0, float(timeout_seconds or 0))
            while True:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        log(f"[lock] 다른 자동매매 프로세스 실행 중 — {kind} 이번 회차 skip")
                        yield False
                        return
                    time.sleep(0.25)
        else:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            acquired = True
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps({
            "pid": os.getpid(), "kind": kind,
            "started_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        }, ensure_ascii=False))
        fh.flush()
        yield True
    finally:
        if fh is not None:
            if acquired:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            fh.close()


def past_force_exit(now=None):
    """현재 KST가 강제청산 시각(FORCE_EXIT_HHMM) 이후인지. 테스트훅 AUTOTRADE_FORCE_EXIT=1이면 시각 무관 True."""
    if os.environ.get("AUTOTRADE_FORCE_EXIT") == "1":
        return True
    now = now or datetime.now(KST)
    return int(now.strftime("%H%M")) >= FORCE_EXIT_HHMM


def market_session(now=None):
    """현재 매매 세션 판정. 테스트훅 AUTOTRADE_SESSION env로 강제 가능(nxt_premarket/krx/closed).

    nxt_premarket : 08:00~08:59 (NXT 프리마켓 — NXT 거래가능 종목만, NXT 가격·NXT 지정가 매도)
    krx           : 09:00~15:30 (정규장 — KRX 가격·KRX 시장가 매도, 현행)
    closed        : 그 외 (감시 무동작)
    """
    forced = os.environ.get("AUTOTRADE_SESSION")
    if forced in ("nxt_premarket", "krx", "closed"):
        return forced
    now = now or datetime.now(KST)
    hhmm = int(now.strftime("%H%M"))
    if NXT_PREMARKET_START <= hhmm < 900:
        return "nxt_premarket"
    if 900 <= hhmm <= KRX_CLOSE_HHMM:
        return "krx"
    return "closed"


def notify_trade(text):
    """자동매매 텔레그램 알림 — telegram_notify 재사용. fail-safe(미설정·실패여도 매매 진행)."""
    # 회귀 테스트가 실환경 .env를 읽어 실제 텔레그램을 발송하면 안 된다.
    # 테스트는 의도한 알림을 반드시 mock으로 검증하며, 누락 시 조용히 삼키지 않고 실패시킨다.
    if os.environ.get("AUTOTRADE_TEST_MODE") == "1":
        raise RuntimeError("AUTOTRADE_TEST_MODE에서 실제 텔레그램 호출 차단")
    try:
        import telegram_notify as tn
        tn.load_env()
        return tn.send(text)
    except Exception as e:
        log(f"[notify] 텔레그램 실패(무시): {e}")
        return False


def append_trade_event(ev):
    """매매 원장(data/autotrade_trades.jsonl)에 이벤트 1줄 append. 통계 분석용.
    fail-safe — 기록 실패해도 매매 진행. ts는 여기서 단일 주입."""
    try:
        path = os.path.join(REPO, "data", "autotrade_trades.jsonl")
        rec = {"ts": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"), **ev}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        log(f"[ledger] 매매기록 실패(무시): {e}")


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


BUDGET_MIN = 10_000          # 당일 총예산 하한(오타 방지)
BUDGET_MAX = 100_000_000     # 당일 총예산 상한(1억, 오타 폭주 방지)


def read_budget():
    """당일 총예산(원). KV autotrade:budget → int·clamp(1만~1억), 실패/미설정 시 DAILY_BUDGET(100만).
    테스트훅 AUTOTRADE_BUDGET(env) 우선."""
    raw = os.environ.get("AUTOTRADE_BUDGET")
    if raw is None:
        raw = kv_get("autotrade:budget")
    try:
        v = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return DAILY_BUDGET
    return max(BUDGET_MIN, min(BUDGET_MAX, v))


def read_ranks():
    """매수할 레이더 랭크 리스트(1~3, 최대 2, 기본 [1]). KV autotrade:ranks(CSV "1,2").
    테스트훅 AUTOTRADE_RANKS(env)가 있으면 그것을 우선."""
    raw = os.environ.get("AUTOTRADE_RANKS")
    if raw is None:
        raw = kv_get("autotrade:ranks")
    ranks = []
    for tok in (raw or "").split(","):
        tok = tok.strip()
        if tok.isdigit():
            r = int(tok)
            if 1 <= r <= 3 and r not in ranks:
                ranks.append(r)
    ranks = ranks[:MAX_AUTOTRADE_STOCKS]
    return ranks or [1]


# ── 포지션 파일 ──────────────────────────────────────────────────────
def load_positions():
    """포지션 로드. 파일 부재=정상 빈 상태. 파일 존재하나 읽기 실패=상태 불명 → 예외 전파(fail-closed).

    ⚠ 빈 상태로 fallback 금지 — bought_today 중복매수 방지·청산 규칙의 유일한 근거라, empty로 열리면
    (Windows 파일락 등 일시 오류에) 중복 실매수·이중 매도를 부른다. 일시 오류 대비 짧은 재시도만.
    """
    if not os.path.exists(POS_PATH):
        return {"schema_version": 2, "positions": [], "pending_entries": []}
    last = None
    for i in range(3):
        try:
            with open(POS_PATH, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or not isinstance(data.get("positions"), list):
                raise ValueError("포지션 원장 스키마 오류")
            data.setdefault("schema_version", 2)
            data.setdefault("pending_entries", [])
            if not isinstance(data["pending_entries"], list):
                raise ValueError("pending_entries 스키마 오류")
            return data
        except Exception as e:
            last = e
            time.sleep(0.3 * (i + 1))
    log(f"[pos] 로드 실패(재시도 후 {last}) — 파일 존재/읽기불가, 상태 불명 → fail-closed(매매 중단)")
    raise last


def save_positions(data):
    """검증된 원장을 fsync 후 원자 교체한다. 최종 실패는 상위로 전파한다."""
    if not isinstance(data, dict) or not isinstance(data.get("positions"), list):
        raise ValueError("포지션 원장 스키마 오류")
    data.setdefault("schema_version", 2)
    data.setdefault("pending_entries", [])
    if not isinstance(data["pending_entries"], list):
        raise ValueError("pending_entries 스키마 오류")
    order_numbers = set()
    for p in data["positions"]:
        qty = int(p.get("qty_open") or 0)
        if qty < 0:
            raise ValueError(f"{p.get('code')} qty_open 음수")
        if p.get("status") == "closed" and qty != 0:
            raise ValueError(f"{p.get('code')} closed 포지션 qty_open 비0")
        pending = p.get("pending_exit")
        if isinstance(pending, dict) and pending.get("ord_no"):
            key = ("exit", str(pending["ord_no"]))
            if key in order_numbers:
                raise ValueError("pending 주문번호 중복")
            order_numbers.add(key)
            if int(pending.get("accounted_filled") or 0) > int(pending.get("requested_qty") or 0):
                raise ValueError("pending_exit 체결수량 불변조건 위반")
    for pending in data["pending_entries"]:
        if not isinstance(pending, dict):
            raise ValueError("pending_entries 항목 스키마 오류")
        if pending.get("ord_no"):
            key = ("entry", str(pending["ord_no"]))
            if key in order_numbers:
                raise ValueError("pending 주문번호 중복")
            order_numbers.add(key)
        if int(pending.get("accounted_filled") or 0) > int(pending.get("requested_qty") or 0):
            raise ValueError("pending_entry 체결수량 불변조건 위반")
    os.makedirs(os.path.dirname(POS_PATH), exist_ok=True)
    last = None
    for i in range(3):
        tmp = f"{POS_PATH}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1, allow_nan=False)
                f.flush()
                os.fsync(f.fileno())
            with open(tmp, encoding="utf-8") as f:
                json.load(f)
            os.replace(tmp, POS_PATH)
            try:
                dfd = os.open(os.path.dirname(POS_PATH), os.O_RDONLY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
            except OSError:
                pass
            return
        except Exception as e:
            last = e
            try:
                os.unlink(tmp)
            except OSError:
                pass
            time.sleep(0.3 * (i + 1))
    log(f"[pos] 저장 실패(재시도 후): {last}")
    raise last


def open_positions(data=None):
    data = data or load_positions()
    return [p for p in data["positions"] if p.get("status") == "open"]


def bought_today(data=None):
    """오늘 이미 진입한 포지션이 있으면 True(일 1회 매수 디둡). (단일종목 레거시)"""
    data = data or load_positions()
    t = today_str()
    return (any(p.get("entry_date") == t for p in data["positions"])
            or any(p.get("entry_date") == t and p.get("state") not in ("CANCELLED", "REJECTED")
                   for p in data.get("pending_entries", [])))


def already_bought(code, data=None):
    """오늘 그 코드를 이미 매수했으면 True(다종목: 코드별 디둡)."""
    data = data or load_positions()
    t = today_str()
    return (any(p.get("code") == code and p.get("entry_date") == t for p in data["positions"])
            or any(p.get("code") == code and p.get("entry_date") == t
                   and p.get("state") not in ("CANCELLED", "REJECTED")
                   for p in data.get("pending_entries", [])))


def todays_positions(data=None):
    data = data or load_positions()
    t = today_str()
    return [p for p in data["positions"] if p.get("entry_date") == t]


def todays_pending_entries(data=None):
    data = data or load_positions()
    t = today_str()
    return [p for p in data.get("pending_entries", [])
            if p.get("entry_date") == t and p.get("state") not in ("CANCELLED", "REJECTED")]


def deployed_today(data=None):
    """오늘 이미 집행한 매수 예산 합. 슬롯 간 예산-안전 배분용.
    ⚠ alloc_krw 결측(구버전/수동 레코드)은 0 대신 실집행액(수량×진입가)으로, 그마저 없으면 BUY_KRW로 계상
      — 결측을 0으로 세면 잔여예산이 되살아나 당일 총 100만 초과(2배) 매수 위험."""
    total = 0
    for p in todays_positions(data):
        a = p.get("alloc_krw")
        if a is None:
            a = int((p.get("qty") or 0) * (p.get("entry_price") or 0)) or BUY_KRW
        total += a
    for pending in todays_pending_entries(data):
        # 주문이 미확정인 동안에는 예약예산 전액을 잠가 이중 집행을 막는다.
        total += int(pending.get("alloc_krw") or 0)
    return total


# ── 레이더 1위 + 안전필터 ────────────────────────────────────────────
def _radar_fresh(d, now=None):
    """radar.json이 오늘(KST) 게시분인지. 레이더/게시가 오늘 실패하면 어제 suspects가 남아
    stale 매수(어제 종목 오늘 매수)로 이어지므로, 날짜 불일치면 fail-closed로 매수 보류."""
    gen = (d.get("generated_at") or "")[:10].replace("-", "")  # "2026-07-06 …" → "20260706"
    expected = (now or datetime.now(KST)).strftime("%Y%m%d")
    return gen == expected


def read_radar_snapshot(now=None):
    """자동매매가 실제 읽은 radar.json root를 반환한다. 실패/stale은 기존처럼 fail-closed."""
    if not os.path.exists(RADAR_JSON):
        return None
    try:
        with open(RADAR_JSON, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        log(f"[radar] radar.json 로드 실패: {e}")
        return None
    if not _radar_fresh(data, now=now):
        expected = (now or datetime.now(KST)).strftime("%Y%m%d")
        log(f"[radar] radar.json 신선도 실패(generated_at={data.get('generated_at')} ≠ {expected}) "
            "— stale 매수 방지, 보류")
        return None
    return data


def radar_snapshot_meta(snapshot, now=None):
    """의사결정/체결 원장용 root 메타데이터. 매수 판정에는 사용하지 않는다."""
    now = now or datetime.now(KST)
    snapshot = snapshot or {}
    suspects = snapshot.get("suspects") or []
    first = suspects[0] if suspects else {}
    generated_at = snapshot.get("generated_at")
    generated_dt = None
    if generated_at:
        raw = str(generated_at).strip().replace(" KST", "")
        try:
            generated_dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if generated_dt.tzinfo is None:
                generated_dt = generated_dt.replace(tzinfo=KST)
            else:
                generated_dt = generated_dt.astimezone(KST)
        except ValueError:
            generated_dt = None
    stale_seconds = (max(0, int((now - generated_dt).total_seconds()))
                     if generated_dt is not None else None)
    before_decision = generated_dt is not None and generated_dt <= now

    def model_value(key):
        value = snapshot.get(key)
        return value if value is not None else first.get(key)

    run = snapshot.get("run")
    run_id = run.get("run_id") if isinstance(run, dict) else None
    return {
        "trade_date": ((str(generated_at)[:10].replace("-", "")) if generated_at else now.strftime("%Y%m%d")),
        "radar_generated_at": generated_at,
        "source_run_id": snapshot.get("run_id") or run_id,
        "rank_policy_name": model_value("rank_policy_name"),
        "rank_model_version": model_value("rank_model_version"),
        "rank_model_effective_from": model_value("rank_model_effective_from"),
        "rank_model_effective_at": model_value("rank_model_effective_at"),
        "rank_model_source_commit": model_value("rank_model_source_commit"),
        "stale_seconds": stale_seconds,
        "valid_for_decision": bool(
            _radar_fresh(snapshot, now=now)
            and before_decision
            and stale_seconds is not None
            and stale_seconds <= RADAR_DECISION_MAX_STALE_SECONDS
        ),
        "top_codes": [s.get("code") for s in suspects[:3] if s.get("code")],
    }


def _ensure_local_manifest(store, trade_date):
    """publish가 하루 종일 죽은 날(전 회차 radar 행 등)·주말 수동 실행에서 실행기 JSONL이
    영구 미색인 고아가 되는 것 방지(재검증 반박 2). manifest가 이미 있으면 stat 1회로 끝(no-op —
    주문 경로 재빌드 금지 원칙(M3) 유지). 없을 때만 1회 재빌드(그날 파일이 극소라 수 ms). 실패 무시."""
    try:
        day = store.normalize_trade_date(trade_date)
        if not (store.local_day_dir(day) / "manifest.json").exists():
            store.rebuild_manifest(day)
    except Exception:
        pass


def write_local_decision(slot, payload):
    """선택적 로컬 decision 기록 — trades/decisions.jsonl **전용**(보조 감사).

    ⚠ decisions/<slot>.json 불변 스냅샷은 Mac publish 파생(derive_due_decision_snapshots)의
    단독 소유다(문제7 §4.5). 실행기가 같은 파일을 선점하면 first-wins 규칙 때문에 publish
    파생본(게시 전체 배열)이 영구 차단되고, dry 테스트 실행이 그날 forward 모집단을 오염시킨다
    (적대 리뷰 2026-07-11 M2). update_manifest=False = 주문 직전 manifest 전체 재빌드
    (하루 말 실측 ~1.6초/회) 제거 — 색인은 다음 publish 회차 rebuild가 수행(M3).
    실패는 주문 흐름에 영향을 주지 않는다."""
    try:
        import radar_json_store as store
        event_result = store.append_trade_event(
            "decisions", payload, trade_date=payload.get("trade_date"),
            update_manifest=False)
        if not event_result.ok:
            log(f"[audit] decision jsonl 저장 실패(무시): {event_result.error}")
        else:
            _ensure_local_manifest(store, payload.get("trade_date") or today_str())
        return bool(event_result.ok)
    except Exception as e:
        log(f"[audit] decision 로컬 기록 실패(무시): {e}")
        return False


def append_local_trade_event(event):
    """선택적 로컬 trade event JSONL. 핵심 포지션 원장과 분리된 fail-safe 기록.

    update_manifest=False — 주문/청산 경로에서 당일 manifest 전체 재빌드 지연 제거(적대 리뷰 M3)."""
    try:
        import radar_json_store as store
        result = store.append_trade_event(
            "events", event, trade_date=event.get("entry_date") or event.get("trade_date"),
            update_manifest=False)
        if not result.ok:
            log(f"[audit] trade event 저장 실패(무시): {result.error}")
        else:
            _ensure_local_manifest(
                store, event.get("entry_date") or event.get("trade_date") or today_str())
        return bool(result.ok)
    except Exception as e:
        log(f"[audit] trade event 로컬 기록 실패(무시): {e}")
        return False


def top_suspect():
    """메인 레이더 1위(suspects[0]). 없거나 stale이면 None."""
    d = read_radar_snapshot()
    if d is None:
        return None
    sus = d.get("suspects") or []
    return sus[0] if sus else None


def top_suspects(n=1):
    """메인 레이더 상위 n종목(suspects[:n]). 없거나 stale이면 []."""
    d = read_radar_snapshot()
    if d is None:
        return []
    return (d.get("suspects") or [])[:n]


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
    try:
        import next_session_eligibility as session_eligibility
        eligibility = suspect.get("next_session_eligibility")
        if not session_eligibility.is_fresh(eligibility):
            return False, "다음 거래일 공시 판정 누락 또는 유효시간 만료"
        ok, reason = session_eligibility.safety_allowed(
            eligibility)
    except Exception as exc:
        return False, f"다음 거래일 적격성 검사 오류({type(exc).__name__})"
    if not ok:
        return False, reason
    # ⚠ 경고/위험 등 시장경보 정책은 종전대로다. 다만 확정 거래정지·추천 부적격·공시 미확인은
    #   시장경보와 별개의 신규매수 하드 게이트로 항상 차단한다.
    return True, "ok"
