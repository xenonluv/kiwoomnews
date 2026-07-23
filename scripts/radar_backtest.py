#!/usr/bin/env python3
"""레이더 자가 검증·개선 — "당일 종가 매수 → 다음날 올랐나" 누적 백테스트.

매일(장후 17:20 cron) 실행:
  1. data/radar_history/*.json 의 미평가 수상 종목을 익일 키움 일봉과 대조
       적중 = 익일 종가 > 진입가(당일 종가) / 보조: 익일 고가 ≥ +3%, 수익률
  2. 당일 마감 카드(final) 종목의 AI 익일 예측(prob_up)을 history에 기록
       → 익일 평가와 대조해 AI 적중률·확률 보정 검증 (performance.json "ai")
  3. 점수대별 보정표(bins, 표본 n>=20만 유효) 산출
  4. 누적 표본 n>=30이면 점수 항목별 성과 상관으로 가중치 자동 튜닝
       (기본값 ±30% 제한, 변경 이력 기록) → data/radar_weights.json
  5. web/data/performance.json 생성 (대시보드 /performance 데이터)
  6. --push: history/weights/performance 변경 시 git commit+push

사용:
  python3 scripts/radar_backtest.py            # 평가+산출만
  python3 scripts/radar_backtest.py --push     # cron용
"""
import os
import sys
import glob
import json
import math
import subprocess
import urllib.request
from datetime import datetime, timezone, timedelta

# 기존 호출부가 사용하는 kis 별칭은 유지하되 운영 일봉은 키움으로 고정한다.
import kiwoom_client as kis

# 흔들기 스윗존 경계는 radar.py를 SSOT로 import(정의 드리프트 차단) — 회장님 룰 결합코호트 판정에 사용.
from radar import (SHAKEOUT_T2D_SWEET_LO, SHAKEOUT_T2D_SWEET_HI,
                   SHAKEOUT_DD_SWEET_LO, SHAKEOUT_DD_SWEET_HI,
                   SHAKEOUT_DD6_MAX, SHAKEOUT_DD6_TIER2_MAX,
                   SHAKEOUT_DD6_CANDIDATE_MAX,
                   RANK_BUCKET_BASELINES, rank_bucket_info)
from rank_policy import (RANK_BUCKET_PRIORS, RANK_MODEL_EFFECTIVE_FROM,
                         RANK_MODEL_VERSION, RANK_POLICY_NAME)
from next_high_metrics import derive_next_high_metrics

KST = timezone(timedelta(hours=9))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_DIR = os.path.join(REPO, "data", "radar_history")
WEIGHTS_PATH = os.path.join(REPO, "data", "radar_weights.json")
PERF_PATH = os.path.join(REPO, "web", "data", "performance.json")
SHAKEOUT_BACKFILL_PATH = os.path.join(REPO, "data", "shakeout_backfill.json")
LOCAL_RADAR_ROOT = os.path.join(REPO, "data", "local", "radar_raw")

# 현재 순위 모델의 실제 전진검증 하한. 구버전 신호는 모델별 평가에는 남기되
# 현행 forward bucket 성과에는 섞지 않는다.
FORWARD_MODEL_VERSION = RANK_MODEL_VERSION
FORWARD_EFFECTIVE_FROM = RANK_MODEL_EFFECTIVE_FROM
KNOWN_FORWARD_EFFECTIVE_FROM = {
    "rank4-v1": "20260713",
    FORWARD_MODEL_VERSION: FORWARD_EFFECTIVE_FROM,
}

# 수상함 점수 항목별 기본 최대치 (radar.py suspicion_score와 정합)
DEFAULT_WEIGHTS = {"spark": 15.0, "fade": 15.0, "flow": 15.0, "event": 15.0, "ma10": 10.0}
TUNE_MIN_SAMPLES = 30   # 가중치 튜닝 활성 최소 누적 표본
TUNE_BOUND = 0.30       # 기본값 대비 ±30% 제한
CALIB_MIN_N = 20        # 보정표 구간 유효 최소 표본
SCORE_BINS = [(40, 60), (60, 75), (75, 101)]
HIGH3_X = 1.03          # 보조지표: 익일 고가 +3%
EVALUATED = "EVALUATED"
EXCLUDED_UNTRADABLE = "EXCLUDED_UNTRADABLE"
PENDING_MARKET_SESSION = "PENDING_MARKET_SESSION"
PENDING_DATA_QUALITY = "PENDING_DATA_QUALITY"
EXPIRED_UNRESOLVED = "EXPIRED_UNRESOLVED"
EVALUATION_LOGIC_VERSION = "actual-krx-session-v1"
_BENCHMARK_BARS = None

# 메가스파크×수급 가설 검증 표 (radar.py MEGA_SPARK_X와 정합)
MEGA_X = 40.0
SPARK_BUCKETS = [("<10x", 0.0, 10.0), ("10~40x", 10.0, MEGA_X), ("≥40x", MEGA_X, float("inf"))]
FEATURE_MIN_N = 10      # 피처 셀 유효 최소 표본 (탐색용 — 보정표보다 낮은 임계)
MATERIAL_GRADES = ("S", "A", "B", "C", "D", "N")

# AI(prob_up) 예측 기록 — 웹과 동일한 프로덕션 엔드포인트 호출 (로직 중복 없음).
# 방향 파생 임계(상승≥54/하락≤46)와 정합하는 확률 구간으로 보정 검증 — 프로덕션 ai.ts
# PROB_BULL_MIN=54·PROB_BEAR_MAX=46(2026-06-20 58→54 하향)과 일치(track_eval·ai_click_eval도 54).
AI_ENDPOINT = os.environ.get(
    "RADAR_AI_ENDPOINT", "https://kiwoomnews-five.vercel.app/api/stock/{code}/ai")
AI_PROB_BANDS = [(0, 47), (47, 54), (54, 101)]   # 하락(≤46)/관망(47~53)/상승(≥54)
# 룰베이스 vs AI 괴리 분석: 룰 "매수 우위" 임계(/stock scoring.ts 62점)와
# AI "상승" 임계(54, 사이트 방향배지와 동일)의 일치/불일치 4분면 — 어느 쪽이 맞는지 데이터로 판별
RULE_BUY_MIN = 62
AI_UP_MIN = 54

DISCLAIMER = ("백테스트는 '당일 종가 매수 → 익일 종가 매도' 가정의 참고 지표이며 "
              "수수료·슬리피지 미반영. 매수 추천이 아닙니다.")


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def atomic_write_json(path, payload):
    """Git 파생 JSON/history를 검증한 임시파일에서 원자 교체한다."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        with open(tmp, encoding="utf-8") as f:
            json.load(f)
        os.replace(tmp, path)
        try:
            dfd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_history():
    files = sorted(glob.glob(os.path.join(HISTORY_DIR, "*.json")))
    out = []
    for f in files:
        try:
            out.append((f, json.load(open(f, encoding="utf-8"))))
        except Exception as e:
            log(f"[warn] history 로드 실패 {f}: {e}")
    return out


def actual_next_market_session(signal_date, benchmark_bars):
    """삼성전자 KRX 실거래봉으로 신호일 다음 시장 세션을 확정한다."""
    def traded(bar):
        try:
            return float(bar.get("volume")) > 0
        except (TypeError, ValueError, OverflowError):
            return False
    valid = sorted((b for b in benchmark_bars or [] if b.get("date")), key=lambda b: b["date"])
    signal = next((b for b in valid if b["date"] == signal_date and traded(b)), None)
    if signal is None:
        return None
    return next((b["date"] for b in valid
                 if b["date"] > signal_date and traded(b)), None)


def _market_bars():
    global _BENCHMARK_BARS
    if _BENCHMARK_BARS is None:
        try:
            _BENCHMARK_BARS = kis.daily_prices("005930", days=40, market="J")
        except Exception as exc:
            log(f"  [warn] 시장 기준봉 실패: {exc}")
            _BENCHMARK_BARS = []
    return _BENCHMARK_BARS


def _is_positive_volume(value):
    try:
        return float(value) > 0
    except (TypeError, ValueError, OverflowError):
        return False


def classify_next_session_bar(signal_date, stock_bars, benchmark_bars):
    """실제 다음 시장일의 종목 봉을 평가/거래불능/보류로 분류한다."""
    expected = actual_next_market_session(signal_date, benchmark_bars)
    if expected is None:
        return {"status": PENDING_MARKET_SESSION, "signal": None, "next": None,
                "expected_market_date": None, "reason_code": "MARKET_SESSION_MISSING"}
    bars = sorted((b for b in stock_bars or [] if b.get("date")), key=lambda b: b["date"])
    signal = next((b for b in bars if b["date"] == signal_date and b.get("close")), None)
    if signal is None:
        return {"status": PENDING_DATA_QUALITY, "signal": None, "next": None,
                "expected_market_date": expected, "reason_code": "SIGNAL_BAR_MISSING"}
    target = next((b for b in bars if b["date"] == expected), None)
    if target is None:
        later = next((b for b in bars if b["date"] > expected
                      and _is_positive_volume(b.get("volume"))), None)
        if later:
            return {"status": EXCLUDED_UNTRADABLE, "signal": signal, "next": None,
                    "expected_market_date": expected, "observed_stock_date": later["date"],
                    "reason_code": "LATE_RESUME_BAR"}
        return {"status": PENDING_DATA_QUALITY, "signal": signal, "next": None,
                "expected_market_date": expected, "reason_code": "TARGET_BAR_MISSING"}
    ohlc = [target.get(key) for key in ("open", "high", "low", "close")]
    volume = target.get("volume")
    try:
        volume = float(volume)
    except (TypeError, ValueError, OverflowError):
        return {"status": PENDING_DATA_QUALITY, "signal": signal, "next": target,
                "expected_market_date": expected, "reason_code": "VOLUME_INVALID"}
    if not math.isfinite(volume):
        return {"status": PENDING_DATA_QUALITY, "signal": signal, "next": target,
                "expected_market_date": expected, "reason_code": "VOLUME_INVALID"}
    if any(value is None for value in ohlc):
        return {"status": PENDING_DATA_QUALITY, "signal": signal, "next": target,
                "expected_market_date": expected, "reason_code": "OHLC_MISSING"}
    try:
        numeric_ohlc = [float(value) for value in ohlc]
    except (TypeError, ValueError, OverflowError):
        return {"status": PENDING_DATA_QUALITY, "signal": signal, "next": target,
                "expected_market_date": expected, "reason_code": "OHLC_INVALID"}
    if any(not math.isfinite(value) for value in numeric_ohlc):
        return {"status": PENDING_DATA_QUALITY, "signal": signal, "next": target,
                "expected_market_date": expected, "reason_code": "OHLC_INVALID"}
    same_ohlc = len(set(numeric_ohlc)) == 1
    if volume <= 0:
        return {"status": EXCLUDED_UNTRADABLE if same_ohlc else PENDING_DATA_QUALITY,
                "signal": signal, "next": target, "expected_market_date": expected,
                "observed_stock_date": target["date"],
                "reason_code": "HALT_PLACEHOLDER" if same_ohlc else "ZERO_VOLUME_INCONSISTENT_OHLC"}
    return {"status": EVALUATED, "signal": signal, "next": target,
            "expected_market_date": expected, "reason_code": "TRADED"}


def next_day_evaluation(code, date):
    try:
        bars = kis.daily_prices(code, days=40, market="J")
    except Exception as e:
        log(f"  [warn] {code} 일봉 실패: {e}")
        return {"status": PENDING_DATA_QUALITY, "signal": None, "next": None,
                "expected_market_date": None, "reason_code": "DAILY_API_ERROR"}
    return classify_next_session_bar(date, bars, _market_bars())


def next_day_bar(code, date):
    """date(YYYYMMDD)의 신호일 봉과 바로 다음 거래일 봉 → (signal_bar, next_bar).

    신호일 봉이 조회 윈도우에 없으면 (None, None) — 엉뚱한 후일봉으로
    오평가하는 것을 막는다 (평가 보류, 오래되면 호출부에서 만료 처리).
    """
    result = next_day_evaluation(code, date)
    return ((result.get("signal"), result.get("next"))
            if result.get("status") == EVALUATED else (None, None))


def fill_signal_snapshot(code, date, s):
    """신호일 OHLC/MA/peak 원천 필드 누락 표본 자가치유.

    패치 이전 같은 날 장중 탈락한 흔들기 표본처럼 history에 핵심 파생값은 있으나
    signal_* 원천 스냅샷이 없는 경우, 일봉으로 복원 가능한 값만 채운다.
    """
    if not s.get("shakeout"):
        return False
    snapshot_keys = (
        "signal_open", "signal_high", "signal_low", "signal_close",
        "signal_volume", "signal_value", "signal_peak6_price", "signal_peak60_price",
        "signal_ma20", "signal_ma10", "run_6d_pct", "ma20_gap_pct", "ma10_margin_pct",
    )
    signal_source = s.get("signal_source")
    live_source = signal_source not in (None, "", "daily_final")
    needs_finalize = signal_source != "daily_final" and not s.get("daily_final_filled")
    # daily_latest 표본은 화면/알림/자동매매가 본 라이브 라벨이 전진검증의 기준이다.
    # 공식 일봉 값은 daily_final_*로만 보존하고, dd6/very_good/change/fade 본 필드는 건드리지 않는다.
    needs_snapshot = any(s.get(k) is None for k in snapshot_keys) and not (
        live_source and s.get("daily_final_filled")
    )
    needs_dd6 = s.get("dd6_pct") is None and not live_source
    if not needs_snapshot and not needs_dd6 and not needs_finalize:
        return False
    try:
        bars = kis.daily_prices_jmoney_un(code, days=90)
    except Exception as e:
        log(f"  [warn] {code} 신호일 스냅샷 보강 실패: {e}")
        return False
    bars = sorted((b for b in bars if b.get("close") and b.get("high")), key=lambda b: b["date"])
    idx = next((i for i, b in enumerate(bars) if b["date"] == date), None)
    if idx is None or idx < 19:
        return False
    bar = bars[idx]
    prev = bars[idx - 1] if idx > 0 else {}
    closes = [b["close"] for b in bars[:idx + 1]]
    highs = [b["high"] for b in bars[:idx + 1]]
    ma20 = sum(closes[-20:]) / 20
    ma10 = sum(closes[-10:]) / 10
    peak60 = max(highs[max(0, idx - 59):idx + 1])
    peak6 = max(highs[max(0, idx - 5):idx + 1])
    close = bar["close"]
    prev_close = prev.get("close")
    change_pct = (close / prev_close - 1) * 100 if prev_close else None
    high_pct = (bar["high"] / prev_close - 1) * 100 if prev_close else None
    dd6 = (close / peak6 - 1) * 100 if peak6 else None
    peak_dd = (close / peak60 - 1) * 100 if peak60 else None
    run6 = (close / closes[-7] - 1) * 100 if len(closes) >= 7 and closes[-7] else None

    def put(k, v, overwrite=False):
        if overwrite or s.get(k) is None:
            if s.get(k) == v:
                return False
            s[k] = v
            return True
        return False

    changed = False
    overwrite_signal = needs_finalize and not live_source
    if needs_finalize:
        changed |= put("snapshot_open", s.get("snapshot_open") or s.get("signal_open"))
        changed |= put("snapshot_high", s.get("snapshot_high") or s.get("signal_high"))
        changed |= put("snapshot_low", s.get("snapshot_low") or s.get("signal_low"))
        changed |= put("snapshot_close", s.get("snapshot_close") or s.get("signal_close"))
        changed |= put("snapshot_volume", s.get("snapshot_volume") or s.get("signal_volume"))
        changed |= put("snapshot_value", s.get("snapshot_value") or s.get("signal_value"))
        changed |= put("snapshot_value_eok", s.get("snapshot_value_eok") or s.get("signal_value_eok"))
        changed |= put("snapshot_as_of", s.get("snapshot_as_of") or s.get("signal_date") or date)
        if live_source:
            for k in ("change_pct", "high_pct", "fade_pct", "peak_dd_pct", "dd6_pct",
                      "very_good", "very_good_tier", "very_good_candidate"):
                if s.get(k) is not None:
                    changed |= put(f"live_{k}", s.get(k))
            changed |= put("live_signal_source", signal_source)

    final_values = {
        "date": date,
        "open": bar.get("open"),
        "high": bar.get("high"),
        "low": bar.get("low"),
        "close": close,
        "prev_close": prev_close,
        "volume": bar.get("volume"),
        "value": bar.get("value"),
        "value_eok": round((bar.get("value") or 0) / 1e8, 1),
        "peak6_price": peak6,
        "peak60_price": peak60,
        "ma20": round(ma20, 1),
        "ma10": round(ma10, 1),
        "run_6d_pct": round(run6, 1) if run6 is not None else None,
        "ma20_gap_pct": round((close / ma20 - 1) * 100, 1) if ma20 else None,
        "ma10_margin_pct": round((close / ma10 - 1) * 100, 2) if ma10 else None,
        "change_pct": round(change_pct, 2) if change_pct is not None else None,
        "high_pct": round(high_pct, 2) if high_pct is not None else None,
        "fade_pct": (round(high_pct - change_pct, 1)
                     if high_pct is not None and change_pct is not None else None),
        "peak_dd_pct": round(peak_dd, 1) if peak_dd is not None else None,
    }
    if needs_finalize and live_source:
        for k, v in final_values.items():
            changed |= put(f"daily_final_{k}", v, True)

    if not live_source:
        changed |= put("signal_date", date, overwrite_signal)
        changed |= put("signal_open", bar.get("open"), overwrite_signal)
        changed |= put("signal_high", bar.get("high"), overwrite_signal)
        changed |= put("signal_low", bar.get("low"), overwrite_signal)
        changed |= put("signal_close", close, overwrite_signal)
        changed |= put("signal_prev_close", prev_close, overwrite_signal)
        changed |= put("signal_volume", bar.get("volume"), overwrite_signal)
        changed |= put("signal_value", bar.get("value"), overwrite_signal)
        changed |= put("signal_value_eok", round((bar.get("value") or 0) / 1e8, 1), overwrite_signal)
        changed |= put("signal_peak6_price", peak6, overwrite_signal)
        changed |= put("signal_peak60_price", peak60, overwrite_signal)
        changed |= put("signal_ma20", round(ma20, 1), overwrite_signal)
        changed |= put("signal_ma10", round(ma10, 1), overwrite_signal)
        changed |= put("run_6d_pct", round(run6, 1) if run6 is not None else None, overwrite_signal)
        changed |= put("ma20_gap_pct", round((close / ma20 - 1) * 100, 1) if ma20 else None, overwrite_signal)
        changed |= put("ma10_margin_pct", round((close / ma10 - 1) * 100, 2) if ma10 else None, overwrite_signal)
        changed |= put("change_pct", final_values["change_pct"], needs_finalize)
        changed |= put("high_pct", final_values["high_pct"], needs_finalize)
        changed |= put("fade_pct", final_values["fade_pct"], needs_finalize)
        changed |= put("peak_dd_pct", final_values["peak_dd_pct"], needs_finalize)
    if dd6 is not None:
        final_dd6 = round(dd6, 1)
        if dd6 <= SHAKEOUT_DD6_TIER2_MAX:
            tier = "tier2"
        elif dd6 <= SHAKEOUT_DD6_MAX:
            tier = "tier1"
        elif dd6 <= SHAKEOUT_DD6_CANDIDATE_MAX:
            tier = "candidate"
        else:
            tier = None
        if needs_finalize and live_source:
            changed |= put("daily_final_dd6_pct", final_dd6, True)
            changed |= put("daily_final_very_good_tier", tier, True)
            changed |= put("daily_final_very_good", tier in ("tier1", "tier2"), True)
            changed |= put("daily_final_very_good_candidate", tier == "candidate", True)
        elif s.get("dd6_pct") is None or needs_finalize:
            old_dd6 = s.get("dd6_pct")
            s["dd6_pct"] = final_dd6
            s["very_good_tier"] = tier
            s["very_good"] = tier in ("tier1", "tier2")
            s["very_good_candidate"] = tier == "candidate"
            changed = True if old_dd6 != s["dd6_pct"] else changed
    if live_source:
        changed |= put("daily_final_filled", True, True)
        changed |= put("daily_final_as_of", date, True)
    else:
        changed |= put("signal_source", "daily_final", True)
    return changed


def evaluate():
    """미평가 종목을 익일 일봉과 대조해 history 파일에 결과 역기록."""
    today = datetime.now(KST).strftime("%Y%m%d")
    n_eval = n_excluded = 0
    for path, hist in load_history():
        if hist.get("date", "") >= today:
            continue  # 당일분은 익일에 평가
        changed = False
        age_days = (datetime.now(KST).date()
                    - datetime.strptime(hist["date"], "%Y%m%d").date()).days
        n_signal_heal = 0
        n_heal = 0
        for code, s in hist.get("suspects", {}).items():
            if fill_signal_snapshot(code, hist["date"], s):
                changed = True
                n_signal_heal += 1
            # 자가치유: 이미 평가됐지만 파생 필드가 빈 표본 소급 충전.
            #   (next_high_pct / next_open·next_low 필드 추가 이전에 평가된 표본 — 종가·적중은
            #    멀쩡하나 익절 도달폭·저가 낙폭·시가 갭이 누락됨. 필드 추가 시마다 여기서 소급 채운다.)
            r0 = s.get("result")
            if s.get("evaluated") and r0 and r0.get("metrics_version") is None:
                raw_entry = r0.get("entry") or s.get("entry")
                if raw_entry is not None and r0.get("next_high") is not None:
                    r0.update(derive_next_high_metrics(raw_entry, r0.get("next_high")))
                    changed = True
                    n_heal += 1
                else:
                    r0.update(derive_next_high_metrics(None, None))
                    changed = True
            if (s.get("evaluated") and r0 and s.get("entry")
                    and (r0.get("next_high_pct") is None or r0.get("next_low") is None)):
                sig0, nb0 = next_day_bar(code, hist["date"])
                if sig0 and nb0:
                    e0 = float(sig0["close"]) if sig0.get("close") else float(s["entry"])
                    if e0:
                        if r0.get("next_high_pct") is None:
                            r0.update(derive_next_high_metrics(e0, nb0.get("high")))
                        if r0.get("next_low") is None:
                            r0["next_open"] = nb0["open"]
                            r0["next_low"] = nb0["low"]
                            r0["next_open_pct"] = round((nb0["open"] / e0 - 1) * 100, 2)
                            r0["next_low_pct"] = round((nb0["low"] / e0 - 1) * 100, 2)
                        changed = True
                        n_heal += 1
            if s.get("evaluated") or not s.get("entry"):
                continue
            evaluation = next_day_evaluation(code, hist["date"])
            sig, nb = evaluation.get("signal"), evaluation.get("next")
            if evaluation["status"] == EXCLUDED_UNTRADABLE:
                s["evaluated"] = True
                s["evaluation_status"] = EXCLUDED_UNTRADABLE
                s["result"] = None
                s["evaluation_exclusion"] = {
                    "reason_code": evaluation["reason_code"],
                    "signal_date": hist["date"],
                    "expected_market_date": evaluation.get("expected_market_date"),
                    "observed_stock_date": evaluation.get("observed_stock_date"),
                    "source_volume": nb.get("volume") if nb else None,
                    "source_ohlc": ({key: nb.get(key) for key in ("open", "high", "low", "close")}
                                    if nb else None),
                    "previous_result": None,
                    "corrected_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
                    "logic_version": EVALUATION_LOGIC_VERSION,
                }
                changed = True; n_excluded += 1
                log(f"  [exclude] {hist['date']} {s.get('name')} — {evaluation['reason_code']}")
                continue
            if evaluation["status"] != EVALUATED or not sig or not nb:
                if age_days > 25:
                    # 조회 윈도우를 벗어난 오래된 미평가 — 영구 재조회 방지 위해 만료
                    s["evaluated"] = True
                    s["evaluation_status"] = EXPIRED_UNRESOLVED
                    s["result"] = None
                    changed = True
                    log(f"  [expire] {hist['date']} {s.get('name')} — 평가 불가(만료)")
                continue  # 익일봉 미존재(연휴 등) — 다음 실행에서 재시도
            # entry는 신호일 일봉 종가로 재정합 (장중 마지막 회차 가격 ≠ 확정 종가 대비)
            entry = float(sig["close"]) if sig.get("close") else float(s["entry"])
            ret = (nb["close"] / entry - 1) * 100
            high_metrics = derive_next_high_metrics(entry, nb.get("high"))
            s["result"] = {
                "date": nb["date"],
                "entry": entry,
                "entry_basis": "KRX_CLOSE",
                "next_close": nb["close"],
                "next_high": nb["high"],
                "next_open": nb["open"],
                "next_low": nb["low"],
                "hit": nb["close"] > entry,
                "high3": nb["high"] >= entry * HIGH3_X,
                "return_pct": round(ret, 2),
                # 익일 고가 도달폭(entry 대비 %) — 회장님 실제 익절선(+7/+13%) 검증·흔들기 밴드 튜닝 핵심 지표
                **high_metrics,
                # 익일 시가 갭·저가 도달폭(entry 대비 %) — 손절(-5%) 실발동·최대낙폭·갭 리스크 튜닝용
                "next_open_pct": round((nb["open"] / entry - 1) * 100, 2),
                "next_low_pct": round((nb["low"] / entry - 1) * 100, 2),
            }
            s["evaluated"] = True
            s["evaluation_status"] = EVALUATED
            changed = True
            n_eval += 1
            log(f"  [eval] {hist['date']} {s['name']} entry={entry:.0f} "
                f"→ 익일종가 {nb['close']:.0f} ({'적중' if s['result']['hit'] else '미적중'}, "
                f"{ret:+.1f}%)")
        if n_signal_heal:
            log(f"  [heal] {hist['date']} 신호일 스냅샷 소급 충전 {n_signal_heal}건")
        if n_heal:
            log(f"  [heal] {hist['date']} 파생필드(고가폭·시가·저가) 소급 충전 {n_heal}건")
        if changed:
            atomic_write_json(path, hist)
    log(f"[backtest] 신규 평가 {n_eval}건 · 거래불능 제외 {n_excluded}건")


def ai_predict():
    """당일 마감 카드(final) 수상 종목의 AI 익일 예측(prob_up)을 history에 기록.

    웹 AI 라우트(3샘플 중앙값 합의)를 그대로 호출해 로직 중복 없이 동일 예측을 남긴다.
    익일 evaluate()의 result와 대조해 AI 적중률·확률 보정을 검증하는 루프의 입력.
    종목 단위 실패는 건너뜀(백테스트 본 작업 보호). RADAR_AI_PREDICT=0으로 비활성.

    실험(visible_experimental=재매집) 카드도 기록 대상 — 현 파이프라인의 유일 산출물이라
    AI 자료를 모아야 추후 코어 승격·괴리 분석이 가능. 단 ai_stats/divergence 표시는 여전히
    코어(write_performance에 core만 전달)만 집계하므로, 지금은 history에 '기록만' 쌓인다(#2a).
    """
    if os.environ.get("RADAR_AI_PREDICT", "").strip() == "0":
        return
    today = datetime.now(KST).strftime("%Y%m%d")
    path = os.path.join(HISTORY_DIR, f"{today}.json")
    if not os.path.exists(path):
        return
    try:
        hist = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        log(f"[warn] ai_predict history 로드 실패: {e}")
        return
    changed = False
    n_ok = 0
    for code, s in hist.get("suspects", {}).items():
        if not s.get("final") or s.get("ai_pred"):
            continue  # 마감 카드 잔존 종목만(실험 재매집 포함), 이미 기록된 건 재호출 안 함
        try:
            req = urllib.request.Request(
                AI_ENDPOINT.format(code=code), headers={"User-Agent": "radar-backtest"})
            r = json.load(urllib.request.urlopen(req, timeout=90))
            if not isinstance(r.get("probUp"), (int, float)):
                raise ValueError(str(r.get("error", {}).get("code") or "probUp 없음"))
            s["ai_pred"] = {
                "prob_up": round(float(r["probUp"])),
                # LLM 원시 확률 — 코드 산출(prob_up)과 어느 쪽 보정이 좋은지 비교 적립
                "model_prob": r.get("modelProbUp"),
                "direction": r.get("direction"),
                "model": r.get("model"),
                "as_of": r.get("asOf"),
                # 같은 시점의 /stock 룰베이스 판정 — AI와의 괴리 분석용 동시 기록
                "verdict_score": r.get("verdictScore"),
                "verdict_level": r.get("verdictLevel"),
            }
            changed = True
            n_ok += 1
            log(f"  [ai] {s.get('name')} prob_up={s['ai_pred']['prob_up']} {r.get('direction')}")
        except Exception as e:
            log(f"  [ai-skip] {s.get('name')}: {e}")
    if changed:
        atomic_write_json(path, hist)
    log(f"[backtest] AI 예측 기록 {n_ok}건")


_DECISION_FILES = {
    "krx_decision": ("krx_1518.json",),
    "nxt_decision": ("nxt_1950.json",),
    "eod": ("operational_eod.json", "eod.json"),
}


def _local_day_path(date):
    """YYYYMMDD 로컬 원본 디렉터리. 경로만 계산하고 생성하지 않는다."""
    return os.path.join(LOCAL_RADAR_ROOT, date[:4], date[4:6], date[6:8])


def _decision_rows(payload):
    """decision snapshot의 후보 배열을 하위호환 키까지 포함해 읽는다."""
    for key in ("ordered_candidates", "published_candidates", "candidates", "suspects"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return rows
    return []


def load_decision_memberships(date):
    """KRX/NXT/EOD 로컬 decision 원본을 읽어 code별 명시적 모집단을 반환한다.

    파일이 존재할 때만 비포함 종목을 False로 판정한다. 파일 자체가 없으면 None으로
    남겨 legacy `final`과 진짜 decision 모집단을 섞지 않는다.
    """
    decision_dir = os.path.join(_local_day_path(date), "decisions")
    out = {}
    for population, filenames in _DECISION_FILES.items():
        path = next((os.path.join(decision_dir, name) for name in filenames
                     if os.path.exists(os.path.join(decision_dir, name))), None)
        if path is None:
            out[population] = {"recorded": False, "rows": {}, "root": {}}
            continue
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
            if not isinstance(payload, dict):
                raise ValueError("root가 object가 아님")
        except Exception as e:
            log(f"[warn] decision 로드 실패 {path}: {e}")
            out[population] = {"recorded": False, "rows": {}, "root": {}}
            continue
        rows = {}
        valid_for_decision = payload.get("valid_for_decision") is not False
        for index, row in enumerate(_decision_rows(payload) if valid_for_decision else [], 1):
            if not isinstance(row, dict) or not row.get("code"):
                continue
            item = dict(row)
            item["_decision_rank"] = (
                row.get("decision_rank") or row.get("published_rank") or row.get("rank") or index
            )
            rows[str(row["code"])] = item
        # stale/무효 snapshot은 파일이 존재해도 성과 모집단을 확정하지 않는다.
        # 이때 비포함(False)으로 만들면 모든 장중 후보가 거짓 dropout이 된다.
        out[population] = {"recorded": valid_for_decision,
                           "file_recorded": True, "valid": valid_for_decision,
                           "rows": rows, "root": payload}
    return out


def is_evaluated_result(record):
    """legacy 정상 result는 포함하되 명시적 거래불능/만료는 제외한다."""
    return bool(record.get("evaluated") and isinstance(record.get("result"), dict)
                and record.get("result")
                and record.get("evaluation_status") in (None, EVALUATED))


def collect_samples():
    """평가 완료 표본 전체 (날짜순)."""
    samples = []
    for _, hist in load_history():
        decisions = load_decision_memberships(hist["date"])
        for code, s in hist.get("suspects", {}).items():
            if is_evaluated_result(s):
                r = {**s["result"], **derive_next_high_metrics(
                    s["result"].get("entry"), s["result"].get("next_high"))}
                decision_meta = {}
                for population, snapshot in decisions.items():
                    row = snapshot["rows"].get(str(code))
                    decision_meta[f"{population}_present"] = (
                        row is not None if snapshot["recorded"] else None
                    )
                    decision_meta[f"{population}_rank"] = (
                        row.get("_decision_rank") if row is not None else None
                    )
                    decision_meta[f"{population}_bucket"] = (
                        row.get("rank_bucket") if row is not None else None
                    )
                    decision_meta[f"{population}_model_version"] = (
                        ((row or {}).get("rank_model_version")
                         or snapshot["root"].get("rank_model_version"))
                        if row is not None else None
                    )
                # history에 명시적으로 저장된 값은 우선한다. 로컬 decision 파일은 과거
                # snapshot을 사후 덮어쓰지 않으므로 history 미기록 시점의 SSOT fallback이다.
                krx_present = s.get("krx_decision_present") if "krx_decision_present" in s else decision_meta["krx_decision_present"]
                nxt_present = s.get("nxt_decision_present") if "nxt_decision_present" in s else decision_meta["nxt_decision_present"]
                eod_present = s.get("eod_present") if "eod_present" in s else decision_meta["eod_present"]
                samples.append({"date": hist["date"],  # 신호일 (result의 평가일과 별개)
                                "code": code, "name": s.get("name"),
                                "score": s.get("score", 0),
                                # 표시 점수(reaccum은 score_raw=0이라 분리) — publish가 기록한 신규 history만 존재
                                "suspicion_score": s.get("suspicion_score"),
                                "breakdown": s.get("breakdown", {}),
                                "pattern": s.get("pattern", "unknown"),
                                "sector": s.get("sector") or "unknown",
                                "theme": s.get("theme") or "unknown",  # 구표본 미영속 → unknown
                                "theme_leader": s.get("theme_leader", False),  # 그날 테마 거래대금 1위
                                "material": s.get("material"),
                                # 마감 시 게시 카드 잔존 여부(= 종가 매수 가능했던 종목).
                                # 키 없는 과거(장후 실행) 기록은 True
                                "final": s.get("final", True),
                                "final_recorded": "final" in s,
                                "rank_bucket": s.get("rank_bucket"),
                                # forward는 아래 신호시점 불변 필드만 사용한다. 최신
                                # rank_bucket으로 절대 fallback하지 않는다.
                                "rank_bucket_at_signal": s.get("rank_bucket_at_signal"),
                                "rank_reason_at_signal": s.get("rank_reason_at_signal"),
                                "rank_model_version": (
                                    s.get("rank_model_version")
                                    or decision_meta["krx_decision_model_version"]
                                    or decision_meta["nxt_decision_model_version"]
                                    or decision_meta["eod_model_version"]
                                ),
                                "rank_policy_name": s.get("rank_policy_name"),
                                "rank_model_effective_from": s.get("rank_model_effective_from"),
                                "rank_model_source_commit": s.get("rank_model_source_commit"),
                                "rank_reason": s.get("rank_reason"),
                                "shadow_bucket": s.get("shadow_bucket") or [],
                                "shadow_bucket_at_signal": s.get("shadow_bucket_at_signal") or [],
                                "precut_rank": s.get("precut_rank"),
                                "published_rank": s.get("published_rank"),
                                "published": s.get("published") if "published" in s else None,
                                "first_seen_rank": s.get("first_seen_rank"),
                                "latest_published_rank": s.get("latest_published_rank"),
                                "legacy_rank": s.get("rank"),
                                "krx_decision_present": krx_present,
                                "krx_decision_rank": (s.get("krx_decision_rank")
                                                      if s.get("krx_decision_rank") is not None
                                                      else decision_meta["krx_decision_rank"]),
                                "krx_decision_bucket": decision_meta["krx_decision_bucket"],
                                "nxt_decision_present": nxt_present,
                                "nxt_decision_rank": (s.get("nxt_decision_rank")
                                                      if s.get("nxt_decision_rank") is not None
                                                      else decision_meta["nxt_decision_rank"]),
                                "nxt_decision_bucket": decision_meta["nxt_decision_bucket"],
                                "eod_present": eod_present,
                                "eod_rank": (s.get("eod_rank")
                                             if s.get("eod_rank") is not None
                                             else decision_meta["eod_rank"]),
                                "eod_bucket": decision_meta["eod_bucket"],
                                "expected_touch7_rate": s.get("expected_touch7_rate"),
                                "expected_high_pct": s.get("expected_high_pct"),
                                "rank_bucket_stats_snapshot": s.get("rank_bucket_stats_snapshot"),
                                # 화면에는 노출하지만 기존 성과·튜닝 기준선에서는 제외할 실험 표본.
                                "visible_experimental": s.get("visible_experimental", False),
                                "reaccum": s.get("reaccum"),  # peak_ibs·peak_uppertail 포함(마감강도 밴드용)
                                "reignition": s.get("reignition"),  # 5분 스파크 count(스파크 횟수 밴드용)
                                "geupso": s.get("geupso", False),
                                "low_accum": s.get("low_accum", False),
                                "alert_now": s.get("alert_now"),
                                "alert_release": s.get("alert_release"),
                                "alert_risk_released": s.get("alert_risk_released"),
                                "next_session_eligibility": s.get("next_session_eligibility"),
                                # AI 익일 예측 (ai_predict가 기록 — 없으면 None/"none")
                                "ai_prob": (s.get("ai_pred") or {}).get("prob_up"),
                                "ai_dir": (s.get("ai_pred") or {}).get("direction") or "none",
                                # AI 기록 시점의 /stock 룰베이스 판정 점수 (괴리 분석용)
                                "verdict_score": (s.get("ai_pred") or {}).get("verdict_score"),
                                # 메가스파크×수급 검증용 피처. spark_max_x는 신규 기록만 존재
                                # (구버전 history는 복원 불가 → None = unknown 처리).
                                # flow_today_buy 폴백: flow = net_days*2 + today_buy*5 이므로
                                # 홀수 ⇔ 당일 순매수 (캡 15도 홀수라 안전).
                                "spark_max_x": s.get("spark_max_x"),
                                "flow_today_buy": s.get(
                                    "flow_today_buy",
                                    int(round(s.get("breakdown", {}).get("flow", 0) or 0)) % 2 == 1),
                                "mega_flow": s.get("mega_flow", False),
                                # 신호일 당일 등락률 — 등락률 구간별 익일 상승확률 분석용
                                "change_pct": s.get("change_pct"),
                                "change_basis": s.get("change_basis"),  # "NXT"면 야간가 기준 — change_band 필터로 제외(KRX hit과 기준 불일치)
                                # 신호일 원천 스냅샷 — 매우좋음/흔들기 경계 재튜닝용.
                                "entry": s.get("entry"),
                                "signal_date": s.get("signal_date") or hist["date"],
                                "signal_open": s.get("signal_open"),
                                "signal_high": s.get("signal_high"),
                                "signal_low": s.get("signal_low"),
                                "signal_close": s.get("signal_close") or s.get("entry"),
                                "daily_final_close": s.get("daily_final_close"),
                                "signal_prev_close": s.get("signal_prev_close"),
                                "signal_volume": s.get("signal_volume"),
                                "signal_value": s.get("signal_value"),
                                "signal_value_eok": s.get("signal_value_eok"),
                                "signal_peak6_price": s.get("signal_peak6_price"),
                                "signal_peak60_price": s.get("signal_peak60_price"),
                                "signal_ma20": s.get("signal_ma20"),
                                "signal_ma10": s.get("signal_ma10"),
                                "run_6d_pct": s.get("run_6d_pct"),
                                "ma20_gap_pct": s.get("ma20_gap_pct"),
                                "ma10_margin_pct": s.get("ma10_margin_pct"),
                                "float_ratio": s.get("float_ratio"),
                                # 폭발일 회전율(폭발일 거래량/유통주식수 %) — 구간별 익일 상승확률 검증용
                                "peak_turnover_pct": s.get("peak_turnover_pct"),
                                "turnover_basis": s.get("turnover_basis"),  # float|cap — 당일 회전율 산출 기준
                                "turnover_metric": s.get("turnover_metric"),  # "vol_float" — 밴드 필터(구 척도 분리)
                                # 흔들기 강도 튜닝용 변별 변수 (신규 history만 존재 — 구표본 None=unknown)
                                "shakeout": s.get("shakeout", False),
                                "turnover_2d_pct": s.get("turnover_2d_pct"),
                                "peak_dd_pct": s.get("peak_dd_pct"),
                                "strength_tier": s.get("strength_tier"),
                                "turnover_band": s.get("turnover_band"),
                                "dd_band": s.get("dd_band"),
                                "dd6_pct": s.get("dd6_pct"),
                                "very_good": s.get("very_good", False),
                                "very_good_tier": s.get("very_good_tier"),
                                "very_good_candidate": s.get("very_good_candidate", False),
                                "eval_date": r.get("date"),
                                "evaluated_entry": r.get("entry"),
                                "evaluated_entry_basis": r.get("entry_basis"),
                                "next_open": r.get("next_open"),
                                "next_high": r.get("next_high"),
                                "next_low": r.get("next_low"),
                                "next_close": r.get("next_close"),
                                "hit": r.get("hit", False),
                                "high3": r.get("high3", False),
                                "return_pct": r.get("return_pct", 0.0),
                                # 익일 고가 도달폭 — 흔들기 밴드별 익절터치율(+7/+13%) 집계용
                                "next_open_pct": r.get("next_open_pct"),
                                "next_high_pct": r.get("next_high_pct"),
                                "next_high_pct_raw": r.get("next_high_pct_raw"),
                                "touch7": r.get("touch7"),
                                "touch11": r.get("touch11"),
                                "touch13": r.get("touch13"),
                                "touch15": r.get("touch15"),
                                "metrics_status": r.get("metrics_status"),
                                "metrics_version": r.get("metrics_version"),
                                "next_low_pct": r.get("next_low_pct")})
    samples.sort(key=lambda x: (x["date"], x["code"]))  # 동일 신호일 내 순서 안정화
    return samples


def collect_evaluation_exclusions():
    """성과 분모와 분리된 명시적 거래불능 감사행."""
    rows = []
    for _, hist in load_history():
        decisions = load_decision_memberships(hist["date"])
        for code, s in hist.get("suspects", {}).items():
            if s.get("evaluation_status") != EXCLUDED_UNTRADABLE:
                continue
            row = {
                "date": hist["date"], "signal_date": s.get("signal_date") or hist["date"],
                "code": code, "name": s.get("name"), "entry": s.get("entry"),
                "rank_model_version": s.get("rank_model_version"),
                "rank_bucket_at_signal": s.get("rank_bucket_at_signal"),
                "final": s.get("final", True), "final_recorded": "final" in s,
                "published_rank": s.get("published_rank"), "latest_published_rank": s.get("latest_published_rank"),
                "first_seen_rank": s.get("first_seen_rank"), "legacy_rank": s.get("rank"),
                "exclusion": s.get("evaluation_exclusion") or {},
            }
            for population, snapshot in decisions.items():
                item = snapshot["rows"].get(str(code))
                row[f"{population}_present"] = item is not None if snapshot["recorded"] else None
                row[f"{population}_rank"] = item.get("_decision_rank") if item else None
                row[f"{population}_bucket"] = item.get("rank_bucket") if item else None
            rows.append(row)
    return rows


def build_series(samples):
    """일별 + 누적 적중률 시계열 (대시보드 라인차트 원천).

    표본 0인 history 날짜도 포함(누적선 유지) — '수집 일수'를 정직하게 보여준다.
    """
    by_date = {}
    for s in samples:
        by_date.setdefault(s["date"], []).append(s)
    all_dates = sorted({h.get("date") for _, h in load_history() if h.get("date")})
    series = []
    cum_n = cum_hits = 0
    for d in all_dates:
        day = by_date.get(d, [])
        hits = sum(1 for x in day if x["hit"])
        cum_n += len(day)
        cum_hits += hits
        series.append({
            "date": d,
            "n": len(day),
            "hits": hits,
            "hit_rate": round(hits / len(day) * 100) if day else None,
            "cum_n": cum_n,
            "cum_hit_rate": round(cum_hits / cum_n * 100, 1) if cum_n else None,
        })
    return series


def build_bins(samples):
    bins = []
    for lo, hi in SCORE_BINS:
        grp = [s for s in samples if lo <= s["score"] < hi]
        hits = sum(1 for s in grp if s["hit"])
        bins.append({"lo": lo, "hi": hi, "n": len(grp),
                     "actual_rate": round(hits / len(grp) * 100) if grp else None,
                     "valid": len(grp) >= CALIB_MIN_N})
    return bins


def group_stats(samples, key):
    out = []
    vals = sorted({s.get(key) or "unknown" for s in samples})
    for v in vals:
        grp = [s for s in samples if (s.get(key) or "unknown") == v]
        if not grp:
            continue
        hits = sum(1 for s in grp if s["hit"])
        rets = [s["return_pct"] for s in grp]
        out.append({"key": v, "n": len(grp),
                    "hit_rate": round(hits / len(grp) * 100, 1),
                    "avg_return": round(sum(rets) / len(rets), 2),
                    "high3_rate": round(sum(1 for s in grp if s["high3"]) / len(grp) * 100, 1)})
    return out


def group_stats_gated(samples, key, min_n=FEATURE_MIN_N):
    """group_stats + 표본부족 게이트(valid). n<min_n 행은 웹이 수치 숨기고 '수집 중' 표기.
    소표본으로 테마/섹터 우선순위를 단정하지 않게 하는 안전장치(현 n 적어 대부분 valid=false 정상)."""
    rows = group_stats(samples, key)
    for r in rows:
        r["valid"] = r["n"] >= min_n
    return rows


def _material_grade(s):
    mat = s.get("material")
    if not isinstance(mat, dict):
        return "unknown"
    grade = mat.get("grade")
    return grade if grade in MATERIAL_GRADES else "unknown"


def _material_stat(grp):
    hits = sum(1 for s in grp if s["hit"])
    rets = [s["return_pct"] for s in grp]
    return {
        "n": len(grp),
        "hit_rate": round(hits / len(grp) * 100, 1) if grp else None,
        "avg_return": round(sum(rets) / len(rets), 2) if rets else None,
        "valid": len(grp) >= FEATURE_MIN_N,
    }


def material_grade_stats(samples):
    """뉴스/공시 재료 등급별 전진검증. N=재료뉴스 없음, unknown=구표본/미기록."""
    labels = [
        ("재료 S(정책·상폐·M&A급)", "S"),
        ("재료 A(공시·대형이벤트)", "A"),
        ("재료 B(실적·재무개선)", "B"),
        ("재료 C(테마·간접수혜)", "C"),
        ("재료 D(악재·희석우세)", "D"),
        ("재료 없음/미확인", "N"),
    ]
    known = [s for s in samples if _material_grade(s) != "unknown"]
    return {
        "min_n": FEATURE_MIN_N,
        "unknown_n": len(samples) - len(known),
        "cells": [{"band": label, **_material_stat([s for s in known if _material_grade(s) == grade])}
                  for label, grade in labels],
    }


def material_signal_stats(samples):
    """재료 강도와 매우좋음/흔들기 축을 결합한 전진검증."""
    known = [s for s in samples if _material_grade(s) != "unknown"]

    def strong_material(s):
        return _material_grade(s) in ("S", "A")

    defs = [
        ("매우좋음 Tier1 + 재료 S/A", lambda s: _very_good_tier_of(s) == "tier1" and strong_material(s)),
        ("매우좋음 후보 + 재료 S/A", lambda s: _very_good_tier_of(s) == "candidate" and strong_material(s)),
        ("흔들기 + 재료 S/A", lambda s: s.get("shakeout") and strong_material(s)),
        ("흔들기 + 재료 C/D/N", lambda s: s.get("shakeout") and _material_grade(s) in ("C", "D", "N")),
        ("재매집 + 재료 S/A", lambda s: s.get("pattern") == "reaccum" and strong_material(s)),
        ("재료 없음/미확인", lambda s: _material_grade(s) == "N"),
    ]
    return {
        "min_n": FEATURE_MIN_N,
        "unknown_n": len(samples) - len(known),
        "cells": [{"band": label, **_material_stat([s for s in known if pred(s)])}
                  for label, pred in defs],
    }


def fill_theme_leaders(rows, samples):
    """by_theme 각 행에 leader_name/leader_count 부여 = 그 테마에서 '테마 대장'(거래대금 1위)으로
    가장 자주 뽑힌 종목(표시 전용). 동률은 이름순. 대장 표본 없으면 미부여."""
    for r in rows:
        cnt = {}
        for s in samples:
            if (s.get("theme") or "unknown") == r["key"] and s.get("theme_leader"):
                nm = s.get("name") or "?"
                cnt[nm] = cnt.get(nm, 0) + 1
        if cnt:
            top = sorted(cnt, key=lambda n: (-cnt[n], n))[0]  # 최빈 → 동률은 이름 오름차순
            r["leader_name"], r["leader_count"] = top, cnt[top]
    return rows


def ai_stats(samples):
    """AI(prob_up) 예측 검증 — ai_pred가 기록된 평가 완료 표본만.

    방향별 적중률 + 확률 구간별 실측 적중률(보정 검증) + Brier 점수(낮을수록 좋음,
    0.25 = 항상 50%라 답한 무정보 기준선).
    """
    grp = [s for s in samples if s.get("ai_prob") is not None]
    if not grp:
        return {"n": 0, "by_direction": [], "prob_bands": [], "avg_prob": None,
                "actual_rate": None, "brier": None, "divergence": divergence_stats([])}
    hits = sum(1 for s in grp if s["hit"])
    bands = []
    for lo, hi in AI_PROB_BANDS:
        b = [s for s in grp if lo <= s["ai_prob"] < hi]
        bands.append({
            "lo": lo, "hi": hi, "n": len(b),
            "avg_prob": round(sum(s["ai_prob"] for s in b) / len(b), 1) if b else None,
            "actual_rate": round(sum(1 for s in b if s["hit"]) / len(b) * 100) if b else None,
            "valid": len(b) >= CALIB_MIN_N,
        })
    brier = sum((s["ai_prob"] / 100 - (1 if s["hit"] else 0)) ** 2 for s in grp) / len(grp)
    return {
        "n": len(grp),
        "by_direction": group_stats(grp, "ai_dir"),
        "prob_bands": bands,
        "avg_prob": round(sum(s["ai_prob"] for s in grp) / len(grp), 1),
        "actual_rate": round(hits / len(grp) * 100, 1),
        "brier": round(brier, 3),
        "divergence": divergence_stats(grp),
    }


def divergence_stats(samples):
    """룰베이스 판정 vs AI 예측의 일치/불일치 4분면 적중률 — 괴리 시 어느 쪽이 맞는지 검증.

    예: 룰 79점 "강한 매수신호" vs AI 46% 관망 (스피어 2026-06-12) → "룰만 강세" 셀.
    이 셀의 실측 적중률이 높으면 룰이, 낮으면 AI가 옳았던 것 — 표본 누적으로 판별해
    가중치·프롬프트 튜닝 근거로 쓴다. verdict_score 없는 구표본은 제외(unknown_n).
    """
    known = [s for s in samples if s.get("verdict_score") is not None]
    cells = []
    for label, rule_buy, ai_up in (
        ("동행 강세 (룰 매수 + AI 상승)", True, True),
        ("룰만 강세 (룰 매수 + AI 비상승)", True, False),
        ("AI만 강세 (룰 비매수 + AI 상승)", False, True),
        ("동반 약세 (룰 비매수 + AI 비상승)", False, False),
    ):
        grp = [s for s in known
               if (s["verdict_score"] >= RULE_BUY_MIN) == rule_buy
               and (s["ai_prob"] >= AI_UP_MIN) == ai_up]
        hits = sum(1 for s in grp if s["hit"])
        rets = [s["return_pct"] for s in grp]
        cells.append({
            "key": label, "rule_buy": rule_buy, "ai_up": ai_up, "n": len(grp),
            "hit_rate": round(hits / len(grp) * 100, 1) if grp else None,
            "avg_return": round(sum(rets) / len(rets), 2) if rets else None,
            "valid": len(grp) >= FEATURE_MIN_N,
        })
    return {"rule_buy_min": RULE_BUY_MIN, "ai_up_min": AI_UP_MIN,
            "min_n": FEATURE_MIN_N, "unknown_n": len(samples) - len(known),
            "cells": cells}


def spark_flow_matrix(samples):
    """스파크 배율 구간 × 당일 수급매수 적중률 표 — 메가스파크 가설 검증.

    가설(2026-06-12 관찰): 스파크 ≥40배 + 외인/기관 매수 동반 종목은 회복력이 강함
    (HPSP 136x→상한가, 스피어 44x→반등). 표본 충분 시 MEGA_BONUS의 raw 승격 근거가 된다.
    spark_max_x 미기록 구표본은 unknown_n으로 분리 (셀 통계에서 제외).
    """
    known = [s for s in samples if s.get("spark_max_x") is not None]
    cells = []
    for label, lo, hi in SPARK_BUCKETS:
        for flow_buy in (True, False):
            grp = [s for s in known
                   if lo <= s["spark_max_x"] < hi and s["flow_today_buy"] == flow_buy]
            hits = sum(1 for s in grp if s["hit"])
            rets = [s["return_pct"] for s in grp]
            cells.append({
                "spark_bucket": label, "flow_buy": flow_buy, "n": len(grp),
                "hit_rate": round(hits / len(grp) * 100, 1) if grp else None,
                "avg_return": round(sum(rets) / len(rets), 2) if rets else None,
                "high3_rate": (round(sum(1 for s in grp if s["high3"]) / len(grp) * 100, 1)
                               if grp else None),
                "valid": len(grp) >= FEATURE_MIN_N,
            })
    return {"mega_x": MEGA_X, "min_n": FEATURE_MIN_N,
            "unknown_n": len(samples) - len(known), "cells": cells}


# 신호일 당일 등락률 구간 — 반등 게이트에 등락률 제한이 없어졌으므로(개편) 음수~상한가 전 구간을 커버.
# '식음 후 반등 신호일에 몇 % 구간 종가매수가 익일 더 오르나'를 구간별로 검증(표시 전용).
CHANGE_BANDS = [("≤−5%", -100.0, -5.0), ("−5~0%", -5.0, 0.0), ("0~+5%", 0.0, 5.0),
                ("+5~+15%", 5.0, 15.0), ("+15%+", 15.0, 100.0)]


def change_band_stats(samples):
    """등락률 구간별 익일 상승확률(적중률)·평균수익 — '몇 % 구간 종가베팅이 익일 더 오르나'.

    hit_rate = 익일 종가 > 신호일 종가 비율 = 실측 상승확률. change_pct 미기록 구표본은 제외.
    ⚠ change_basis=="NXT"(마감 후 NXT 야간가로 재평가된 등락률) 표본도 제외 — hit은 KRX 정규장 종가 기준이라
       야간가 등락률을 같은 구간에 넣으면 x축(등락률)·결과축(KRX hit)의 가격 기준이 어긋난다. 구표본은
       change_basis 미기록(None)=KRX로 간주해 유지(turnover_metric=="vol_float" 필터와 동일한 분리 원칙).
    valid 게이트(n>=FEATURE_MIN_N)로 소표본 단정 방지.
    """
    known = [s for s in samples
             if s.get("change_pct") is not None and s.get("change_basis") in (None, "KRX")]
    cells = []
    for label, lo, hi in CHANGE_BANDS:
        grp = [s for s in known if lo <= s["change_pct"] < hi]
        hits = sum(1 for s in grp if s["hit"])
        rets = [s["return_pct"] for s in grp]
        cells.append({
            "band": label, "n": len(grp),
            "hit_rate": round(hits / len(grp) * 100, 1) if grp else None,
            "avg_return": round(sum(rets) / len(rets), 2) if rets else None,
            "valid": len(grp) >= FEATURE_MIN_N,
        })
    return {"min_n": FEATURE_MIN_N, "unknown_n": len(samples) - len(known), "cells": cells}


# 폭발일 회전율 = 폭발일 거래량/유통주식수(%). 폭발 게이트가 ≥90%를 강제하므로 분포는 90%+에서 시작 →
# 구간을 90% 이상에서 변별. (구 메트릭=거래대금/유통시총 ~40~200% 표본은 90% 미만이면 자연 배제, 90%+면
# 섞이나 25일 만료로 자가 소거 — 표시 전용·score_raw=0 격리 풀이라 코어 통계·튜닝엔 무영향.)
TURNOVER_BANDS = [("90~120%", 90.0, 120.0), ("120~160%", 120.0, 160.0), ("160~220%", 160.0, 220.0),
                  ("220~300%", 220.0, 300.0), ("300%+", 300.0, 1e9)]


def peak_turnover_band_stats(samples):
    """폭발일 회전율(폭발일 거래량/유통주식수 %) 구간별 익일 상승확률·평균수익 — '유통주식이 더 크게
    손바뀐 폭발일수록 익일 더 오르나'를 데이터로 검증(재매집 실험 풀, 코어 통계·튜닝과 격리). peak_turnover_pct
    미기록 구표본은 제외. change_band_stats와 동일 셀 구조(웹 ChangeBandStats 타입 재사용).
    ⚠ 메트릭 버전 표본(turnover_metric=="vol_float")만 — 개편 전 거래대금/유통시총 척도(태그 없음)와
    섞지 않는다. turnover_basis(당일 회전율의 float/cap)가 라이브 스크랩 실패로 흔들려도 영향 없음."""
    known = [s for s in samples
             if s.get("peak_turnover_pct") is not None and s.get("turnover_metric") == "vol_float"]
    cells = []
    for label, lo, hi in TURNOVER_BANDS:
        grp = [s for s in known if lo <= s["peak_turnover_pct"] < hi]
        hits = sum(1 for s in grp if s["hit"])
        rets = [s["return_pct"] for s in grp]
        cells.append({
            "band": label, "n": len(grp),
            "hit_rate": round(hits / len(grp) * 100, 1) if grp else None,
            "avg_return": round(sum(rets) / len(rets), 2) if rets else None,
            "valid": len(grp) >= FEATURE_MIN_N,
        })
    return {"min_n": FEATURE_MIN_N, "unknown_n": len(samples) - len(known), "cells": cells}


# 5분 스파크 횟수·폭발일 마감강도(IBS) 구간 — 주식분석.md ③·7일 표본 가설의 전진 검증용.
REIGNITION_COUNT_BANDS = [("2회", 2, 3), ("3~4회", 3, 5), ("5회+", 5, 1e9)]  # 게이트가 14:30↑ ≥2회로 변경
PEAK_IBS_BANDS = [("약마감 <0.4", 0.0, 0.4), ("중간 0.4~0.7", 0.4, 0.7), ("강마감 ≥0.7", 0.7, 2.0)]


def _hit_band_cells(known, keyfn, bands):
    """구간별 익일 적중률·평균수익 셀 — change_band/peak_turnover_band과 동일 셀 구조 공용."""
    cells = []
    for label, lo, hi in bands:
        grp = [s for s in known if lo <= keyfn(s) < hi]
        hits = sum(1 for s in grp if s["hit"])
        rets = [s["return_pct"] for s in grp]
        cells.append({
            "band": label, "n": len(grp),
            "hit_rate": round(hits / len(grp) * 100, 1) if grp else None,
            "avg_return": round(sum(rets) / len(rets), 2) if rets else None,
            "valid": len(grp) >= FEATURE_MIN_N,
        })
    return cells


def reignition_count_band_stats(samples):
    """14:30~장종료 5분봉 양봉 스파크 횟수 구간별 익일 상승확률 — '마감 직전 재분출이 많을수록 익일 더 오르나'
    전진 검증. 게이트가 14:30↑ ≥2회라 분포는 2 이상(구표본=당일 전체 count는 정의가 달라 만료까지 혼재).
    ChangeBandStats 구조 재사용(웹 패널 공용)."""
    known = [s for s in samples if (s.get("reignition") or {}).get("count") is not None]
    return {"min_n": FEATURE_MIN_N, "unknown_n": len(samples) - len(known),
            "cells": _hit_band_cells(known, lambda s: s["reignition"]["count"], REIGNITION_COUNT_BANDS)}


def peak_ibs_band_stats(samples):
    """폭발일 마감강도(IBS=(종가−저가)/(고가−저가)) 구간별 익일 상승확률 — 7일 표본 반직관 가설('약마감
    [윗꼬리 큰]이 익일 연속성↑·상한가류 강마감은 식음↑')의 전진 검증. peak_ibs는 신규 history만 존재(구표본 제외)."""
    known = [s for s in samples if (s.get("reaccum") or {}).get("peak_ibs") is not None]
    return {"min_n": FEATURE_MIN_N, "unknown_n": len(samples) - len(known),
            "cells": _hit_band_cells(known, lambda s: s["reaccum"]["peak_ibs"], PEAK_IBS_BANDS)}


# 💥 흔들기 결합축 튜닝 밴드 — 회장님 20년룰(회전 스윗90~140 + 깊은눌림 -30~-45 = 급등) 전진 검증축.
# strength_tier 숫자는 과거 호환을 위해 유지한다. 최근 통계상 기존 Tier4가 약하지 않아
# 강/약 라벨 대신 조합 라벨로 표시한다.
SHAKEOUT_T2D_BANDS = [("부족 <90", 0, 90), ("스윗 90~140", 90, 140),
                      ("과열 140~180", 140, 180), ("극과열 ≥180", 180, 1e12)]
SHAKEOUT_DD_BANDS = [("깊음 ≤-45", -1e12, -45), ("스윗 -45~-30", -45, -30), ("얕음 >-30", -30, 1e12)]
SHAKEOUT_TIER_BANDS = [("조합A(스윗가설·검증중)", 0, 1), ("조합B(인접·검증중)", 1, 2),
                       ("조합C(중립)", 2, 3), ("조합D(통계상 고가강)", 3, 5)]


def _shakeout_stat(grp):
    """흔들기 표본군의 익일 성적 — 적중률·평균수익·평균고가 + 익절터치율(+7 부분·+11 전량·+13 참고).
    터치선(+7/+11)은 회장님 실제 매도 기준(+7% 50%익절/+11% 잔량익절)에 정렬."""
    hits = sum(1 for s in grp if s["hit"])
    rets = [s["return_pct"] for s in grp]
    highs = [s["next_high_pct"] for s in grp if s.get("next_high_pct") is not None]
    def touch_rows(x):
        return [s for s in grp if s.get(f"touch{x}") is not None]
    def touch(x):
        known = touch_rows(x)
        return (round(sum(s.get(f"touch{x}") is True for s in known) / len(known) * 100, 1)
                if known else None)
    return {
        "n": len(grp),
        "hit_rate": round(hits / len(grp) * 100, 1) if grp else None,
        "avg_return": round(sum(rets) / len(rets), 2) if rets else None,
        "avg_high": round(sum(highs) / len(highs), 2) if highs else None,   # 익일 평균 고가 도달폭
        "touch7_rate": touch(7), "touch11_rate": touch(11), "touch13_rate": touch(13),
        "touch7_n": len(touch_rows(7)), "touch11_n": len(touch_rows(11)),
        "touch13_n": len(touch_rows(13)),
        "valid": len(grp) >= FEATURE_MIN_N,
    }


def _shakeout_cells(known, keyfn, bands):
    """흔들기 밴드 셀(단일 축)."""
    return [{"band": label, **_shakeout_stat([s for s in known
                                              if keyfn(s) is not None and lo <= keyfn(s) < hi])}
            for label, lo, hi in bands]


def _rule_flags(s):
    """(적정회전 여부, 깊은눌림 여부) — radar 스윗존 상수 SSOT. 결측이면 None."""
    t, dd = s.get("turnover_2d_pct"), s.get("peak_dd_pct")
    if t is None or dd is None:
        return None
    return (SHAKEOUT_T2D_SWEET_LO <= t <= SHAKEOUT_T2D_SWEET_HI,
            SHAKEOUT_DD_SWEET_LO <= dd <= SHAKEOUT_DD_SWEET_HI)


def shakeout_rule_cohorts(known):
    """💥 회장님 20년룰 결합(AND) 코호트 — 축을 쪼개지 않고 '적정회전 AND 깊은눌림'을 있는 그대로 검정.
    룰충족(둘 다) vs 적정회전만 vs 깊은눌림만 vs 그외 — 4코호트 익일 성적. 매도 기준(+7/+11) 터치율로 판정."""
    defs = [("룰충족(적정회전+깊은눌림)", (True, True)), ("적정회전만", (True, False)),
            ("깊은눌림만", (False, True)), ("그외", (False, False))]
    return [{"cohort": label, **_shakeout_stat([s for s in known if _rule_flags(s) == flags])}
            for label, flags in defs]


def _very_good_tier_of(s):
    """history/backfill 호환용 very_good tier 산출."""
    tier = s.get("very_good_tier")
    if tier in ("tier1", "tier2", "candidate"):
        return tier
    dd6 = s.get("dd6_pct")
    if dd6 is None:
        return None
    if dd6 <= SHAKEOUT_DD6_TIER2_MAX:
        return "tier2"
    if dd6 <= SHAKEOUT_DD6_MAX:
        return "tier1"
    if dd6 <= SHAKEOUT_DD6_CANDIDATE_MAX:
        return "candidate"
    return "other"


def very_good_tier_stats(shakeout_samples):
    """⭐ 매우좋음 전용 성과표 — dd6 기준 Tier1/Tier2/후보/일반 흔들기 분리.

    ChangeBandStats 구조로 웹 HitBandTable 재사용. 후보는 승격키 없이 배지·전진검증만 유지한다.
    """
    known = [s for s in shakeout_samples if _very_good_tier_of(s) is not None]
    defs = [
        ("매우좋음 Tier1(-45<dd6≤-30)", "tier1"),
        ("매우좋음 Tier2(≤-45 과낙)", "tier2"),
        ("매우좋음 후보(-30<dd6≤-25)", "candidate"),
        ("일반 흔들기(dd6>-25)", "other"),
    ]
    cells = []
    for label, tier in defs:
        grp = [s for s in known if _very_good_tier_of(s) == tier]
        hits = sum(1 for s in grp if s["hit"])
        rets = [s["return_pct"] for s in grp]
        cells.append({
            "band": label,
            "n": len(grp),
            "hit_rate": round(hits / len(grp) * 100, 1) if grp else None,
            "avg_return": round(sum(rets) / len(rets), 2) if rets else None,
            "valid": len(grp) >= FEATURE_MIN_N,
        })
    return {"min_n": FEATURE_MIN_N, "unknown_n": len(shakeout_samples) - len(known), "cells": cells}


def load_shakeout_backfill():
    """💥 과거 흔들기 소급 재구성 표본(shakeout_backfill.py 산출) 로드 — 튜닝 표본 조기 확보.
    일봉 기반 재현(정의는 라이브와 1:1)·익일결과는 실제 일봉. 유니버스 한계(생존편향)는 파일 note에 명시."""
    try:
        d = json.load(open(SHAKEOUT_BACKFILL_PATH, encoding="utf-8"))
        return d.get("samples", [])
    except Exception:
        return []   # 파일 없으면 라이브 표본만


def shakeout_band_stats(shakeout_samples):
    """💥 흔들기 결합축 튜닝표 — 2일회전율·고점낙폭·결합축별 익일 상승확률·평균수익·고가터치율.
    회장님 20년룰(회전 적정 + 깊은눌림 = 급등, 과회전 = 물량소진) 전진 검증. 흔들기 변수는 신규 history만
    영속(2026-07-06~) → 표본 성숙 전엔 valid=False('관찰중')."""
    known = [s for s in shakeout_samples if s.get("turnover_2d_pct") is not None]
    backfill_n = sum(1 for s in known if s.get("backfill"))
    return {
        "min_n": FEATURE_MIN_N, "n": len(known),
        # 표본 출처 구분(정직) — live=오늘부터 라이브 게시분, backfill=일봉 소급 재구성분(생존편향 주의).
        "live_n": len(known) - backfill_n, "backfill_n": backfill_n,
        # 🎯 회장님 룰 = 결합(AND) 코호트 — 주 판정축(축 분리 밴드는 참고).
        "by_rule_cohort": shakeout_rule_cohorts(known),
        "by_turnover_2d": _shakeout_cells(known, lambda s: s.get("turnover_2d_pct"), SHAKEOUT_T2D_BANDS),
        "by_peak_dd": _shakeout_cells(known, lambda s: s.get("peak_dd_pct"), SHAKEOUT_DD_BANDS),
        "by_strength_tier": _shakeout_cells(known, lambda s: s.get("strength_tier"), SHAKEOUT_TIER_BANDS),
    }


def leader_reaccum_stats(reaccum_experimental):
    """'예전 대장' 재매집 엣지 검증 — was_theme_leader 코호트 A/B.

    가설: 폭발일에 업종 거래대금 1위(예전 대장)였던 종목이 재매집 시 익일 더 잘 오른다.
    leader(was_theme_leader=true) vs nonleader(false) vs all(전체 reaccum baseline)의
    익일 적중률·평균수익·고가3% 비교 + lift(=leader.hit_rate − nonleader.hit_rate).
    reaccum 실험 풀만 입력(코어 통계·가중치 튜닝과 격리). reaccum 블록 없거나 플래그가
    None인 표본은 unknown_n. min_n 게이트로 소표본 단정 방지.

    데이터 주의: sector 기반 대장 로직이 최근 개선돼 그 전 표본은 was_theme_leader가
    거짓일 수 있다 → 신뢰 표본은 배포 이후 forward로만 누적(valid 게이트가 자연 처리)."""
    def _flag(s):
        return (s.get("reaccum") or {}).get("was_theme_leader")
    leader = [s for s in reaccum_experimental if _flag(s) is True]
    nonleader = [s for s in reaccum_experimental if _flag(s) is False]
    unknown_n = sum(1 for s in reaccum_experimental if _flag(s) not in (True, False))

    def _cohort(subset):
        st = sample_stats(subset)
        st["valid"] = st["n"] >= FEATURE_MIN_N
        return st

    lc, nlc = _cohort(leader), _cohort(nonleader)
    lift = (round(lc["hit_rate"] - nlc["hit_rate"], 1)
            if lc["valid"] and nlc["valid"]
            and lc["hit_rate"] is not None and nlc["hit_rate"] is not None else None)
    return {
        "min_n": FEATURE_MIN_N,
        "unknown_n": unknown_n,
        "leader": lc,
        "nonleader": nlc,
        "all": _cohort(reaccum_experimental),
        "lift": lift,
    }


def tune_weights(samples):
    """항목별 정규화 기여도의 적중군-미적중군 평균 차(lift)로 가중치 조정.

    표본 < TUNE_MIN_SAMPLES 이면 None (기본값 유지). 결과는 ±TUNE_BOUND 제한.
    """
    if len(samples) < TUNE_MIN_SAMPLES:
        return None
    hits = [s for s in samples if s["hit"]]
    misses = [s for s in samples if not s["hit"]]
    if not hits or not misses:
        return None  # 전승/전패 표본으론 상관 계산 무의미

    def avg_norm(grp, comp):
        vals = [min(1.0, (s["breakdown"].get(comp, 0) or 0) / DEFAULT_WEIGHTS[comp])
                for s in grp]
        return sum(vals) / len(vals)

    weights = {}
    lifts = {}
    for comp, base in DEFAULT_WEIGHTS.items():
        lift = avg_norm(hits, comp) - avg_norm(misses, comp)  # -1 ~ +1
        factor = max(-TUNE_BOUND, min(TUNE_BOUND, lift))
        weights[comp] = round(base * (1 + factor), 1)
        lifts[comp] = round(lift, 3)
    return {"weights": weights, "lifts": lifts, "basis_n": len(samples)}


def sample_stats(samples):
    n = len(samples)
    hits = sum(1 for s in samples if s["hit"])
    rets = [s["return_pct"] for s in samples]
    return {
        "n": n,
        "hit_rate": round(hits / n * 100, 1) if n else None,
        "avg_return": round(sum(rets) / n, 2) if n else None,
        "high3_rate": round(sum(1 for s in samples if s["high3"]) / n * 100, 1) if n else None,
    }


def _median(xs):
    if not xs:
        return None
    vals = sorted(xs)
    mid = len(vals) // 2
    if len(vals) % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2


def _wilson_lower(successes, n, z=1.96):
    if n <= 0:
        return None
    p = successes / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * ((p * (1 - p) + z * z / (4 * n)) / n) ** 0.5
    return max(0.0, (centre - margin) / denom * 100)


def _ranked_copy(s):
    """현재 정책으로 재분류한 retro 전용 복사본."""
    out = dict(s)
    info = rank_bucket_info(out)
    out.update(info)
    return out


def _int_rank(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isfinite(number) and number > 0 and number.is_integer():
        return int(number)
    return None


def _int_bucket(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isfinite(number) and number >= 0 and number.is_integer():
        return int(number)
    return None


def _stored_ranked_copy(s, population=None):
    """신호/decision 당시 저장된 bucket을 쓰는 forward 전용 복사본."""
    out = dict(s)
    decision_bucket = s.get(f"{population}_bucket") if population else None
    bucket = _int_bucket(decision_bucket)
    if bucket is None:
        bucket = _int_bucket(s.get("rank_bucket_at_signal"))
    out["rank_bucket"] = bucket
    out["rank_reason"] = s.get("rank_reason_at_signal")
    out["shadow_bucket"] = s.get("shadow_bucket_at_signal") or []
    return out


def _rank_group_stat(grp):
    n = len(grp)
    highs = [s["next_high_pct"] for s in grp if s.get("next_high_pct") is not None]
    rets = [s["return_pct"] for s in grp if s.get("return_pct") is not None]
    touch7_known = [s for s in grp if s.get("touch7") is not None]
    touch13_known = [s for s in grp if s.get("touch13") is not None]
    touch7 = sum(s.get("touch7") is True for s in touch7_known)
    touch13 = sum(s.get("touch13") is True for s in touch13_known)
    return {
        "n": n,
        "unique_n": len({s.get("code") for s in grp if s.get("code")}),
        "touch7_rate": round(touch7 / len(touch7_known) * 100, 1) if touch7_known else None,
        "touch13_rate": round(touch13 / len(touch13_known) * 100, 1) if touch13_known else None,
        "wilson7_lower": round(_wilson_lower(touch7, len(touch7_known)), 1) if touch7_known else None,
        "avg_high": round(sum(highs) / len(highs), 2) if highs else None,
        "median_high": round(_median(highs), 2) if highs else None,
        "min_high": round(min(highs), 2) if highs else None,
        "avg_return": round(sum(rets) / len(rets), 2) if rets else None,
        "up_close_rate": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1) if rets else None,
        "valid": n >= FEATURE_MIN_N,
    }


SHADOW_BUCKET_LABELS = {
    "S1": "저점매집+회전150",
    "S2": "경고지정+70점",
    "S3": "흔들기+75점+peakDD≤-30",
    "S4": "흔들기+조합D+80점",
    "S5": "급소+회전90",
}


def _kill_switch_rows(ranked, actionable=False):
    rows = []

    def add(key, label, status, reasons, n):
        rows.append({"key": key, "label": label, "status": status,
                     "reasons": reasons, "n": n,
                     "actionable": bool(actionable and status in ("하향상신", "재승격검토"))})

    b1 = [s for s in ranked if s.get("rank_bucket") == 1]
    b1_stat = _rank_group_stat(b1)
    b1_reasons = []
    if b1_stat["min_high"] is not None and b1_stat["min_high"] < 7:
        b1_reasons.append("최저 익일고가 +7% 미달")
    if b1_stat["n"] >= 10:
        if b1_stat["wilson7_lower"] is not None and b1_stat["wilson7_lower"] < 60:
            b1_reasons.append("n=10 재판정 Wilson 하단 60% 미만")
        if b1_stat["avg_return"] is not None and b1_stat["avg_return"] < 3:
            b1_reasons.append("n=10 재판정 종가평균 +3% 미만")
    add("bucket1", "급소+회전150",
        ("수집 중" if actionable else "정상") if not b1
        else ("하향상신" if b1_reasons else "정상"),
        b1_reasons, len(b1))

    low = [s for s in ranked if s.get("rank_bucket") in (2, 3)]
    low_stat = _rank_group_stat(low)
    low_reasons = []
    if low_stat["wilson7_lower"] is not None and low_stat["wilson7_lower"] < 55:
        low_reasons.append("Wilson 하단 55% 미만")
    if low_stat["avg_return"] is not None and low_stat["avg_return"] < 3:
        low_reasons.append("종가평균 +3% 미만")
    if low_stat["up_close_rate"] is not None and low_stat["up_close_rate"] < 55:
        low_reasons.append("상승마감률 55% 미만")
    add("bucket2_3", "저점매집",
        ("수집 중" if actionable else "정상") if not low
        else ("하향상신" if len(low_reasons) >= 2 else "정상"),
        low_reasons, len(low))

    cand = [s for s in ranked if s.get("very_good_candidate")]
    cand_stat = _rank_group_stat(cand)
    restore = (
        cand_stat["n"] >= 15
        and (cand_stat["wilson7_lower"] or 0) >= 60
        and (cand_stat["avg_return"] or -999) > 3
        and (cand_stat["up_close_rate"] or 0) >= 55
    )
    add("very_good_candidate", "매우좋음후보",
        ("수집 중" if actionable else "관찰") if not cand
        else ("재승격검토" if restore else "관찰"),
        [], len(cand))
    return rows


def _rank_bucket_table(ranked, *, basis, population, include_kill=False,
                       actionable=False, model_version=None):
    cells = []
    bucket_ids = set(RANK_BUCKET_BASELINES)
    bucket_ids.update(_int_bucket(s.get("rank_bucket")) for s in ranked
                      if _int_bucket(s.get("rank_bucket")) is not None)
    for bucket in sorted(bucket_ids):
        grp = [s for s in ranked if _int_bucket(s.get("rank_bucket")) == bucket]
        base = RANK_BUCKET_BASELINES.get(bucket, {})
        cells.append({
            "bucket": bucket,
            "band": base.get("label") or f"bucket {bucket}",
            **_rank_group_stat(grp),
        })
    shadow_cells = []
    for key, label in SHADOW_BUCKET_LABELS.items():
        grp = [s for s in ranked if key in (s.get("shadow_bucket") or [])]
        shadow_cells.append({"shadow": key, "band": label, **_rank_group_stat(grp)})
    return {
        "basis": basis,
        "population": population,
        "model_version": model_version,
        "sample_n": len(ranked),
        "tracking_days": len({s.get("date") for s in ranked if s.get("date")}),
        "min_n": FEATURE_MIN_N,
        "cells": cells,
        "shadow_cells": shadow_cells,
        "kill_switches": (_kill_switch_rows(ranked, actionable=actionable)
                          if include_kill else []),
    }


def rank_bucket_stats(samples):
    """하위호환용 기존 표: 전체 suspects를 현재 규칙으로 소급 재분류한다."""
    table = _rank_bucket_table(
        [_ranked_copy(s) for s in samples],
        basis="retro",
        population="legacy_all",
        include_kill=True,
        actionable=False,
        model_version=FORWARD_MODEL_VERSION,
    )
    table["deprecated"] = True
    return table


def _population_member(s, population):
    """명시적으로 저장된 모집단만 선택한다. 필드가 없으면 포함하지 않는다."""
    if population == "all":
        return True
    if population == "final":
        return bool(s.get("final_recorded") and s.get("final") is True)
    if population == "krx_decision":
        return s.get("krx_decision_present") is True
    if population == "nxt_decision":
        return s.get("nxt_decision_present") is True
    if population == "eod":
        return s.get("eod_present") is True
    if population == "dropout":
        if s.get("eod_present") is False:
            return any(_int_rank(s.get(k)) is not None for k in (
                "first_seen_rank", "latest_published_rank", "legacy_rank"))
        return bool(s.get("final_recorded") and s.get("final") is False)
    raise ValueError(f"알 수 없는 rank 모집단: {population}")


def _retro_population_table(samples, population):
    grp = [s for s in samples if _population_member(s, population)]
    return _rank_bucket_table(
        [_ranked_copy(s) for s in grp],
        basis="retro",
        population=population,
        include_kill=False,
        actionable=False,
        model_version=FORWARD_MODEL_VERSION,
    )


def rank_bucket_stats_retro(samples):
    """현재 rank4 규칙을 과거 피처에 적용한 소급표. forward와 절대 혼합하지 않는다."""
    return {
        "basis": "retro_current_policy",
        "model_version": FORWARD_MODEL_VERSION,
        "exclusive_all": _retro_population_table(samples, "all"),
        "exclusive_final": _retro_population_table(samples, "final"),
        "exclusive_krx_decision": _retro_population_table(samples, "krx_decision"),
        "exclusive_nxt_decision": _retro_population_table(samples, "nxt_decision"),
        "exclusive_eod": _retro_population_table(samples, "eod"),
        "dropout": _retro_population_table(samples, "dropout"),
    }


def _date_digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


def _is_forward_sample(s, model_version=None):
    version = s.get("rank_model_version")
    if not version or (model_version is not None and version != model_version):
        return False
    signal_date = _date_digits(s.get("signal_date") or s.get("date"))
    if signal_date == "20260710":
        return False
    expected_from = KNOWN_FORWARD_EFFECTIVE_FROM.get(version)
    effective = _date_digits(s.get("rank_model_effective_from"))
    if not effective:
        effective = expected_from
    if not effective or (expected_from and effective < expected_from) or signal_date < effective:
        return False
    return _int_bucket(s.get("rank_bucket_at_signal")) is not None


def _forward_population_table(samples, population, model_version=FORWARD_MODEL_VERSION,
                              include_kill=False):
    grp = [s for s in samples
           if _is_forward_sample(s, model_version) and _population_member(s, population)]
    ranked = [_stored_ranked_copy(s, population) for s in grp]
    return _rank_bucket_table(
        ranked,
        basis="forward_saved_signal",
        population=population,
        include_kill=include_kill,
        actionable=include_kill,
        model_version=model_version,
    )


def rank_bucket_stats_forward(samples):
    """현행 모델 발효 후 저장된 버킷만 집계한다. 실제 상신 판정도 이 표만 수행한다."""
    eod = _forward_population_table(samples, "eod", include_kill=True)
    return {
        "basis": "forward_saved_signal",
        "model_version": FORWARD_MODEL_VERSION,
        "effective_from": FORWARD_EFFECTIVE_FROM,
        "krx_decision": _forward_population_table(samples, "krx_decision"),
        "nxt_decision": _forward_population_table(samples, "nxt_decision"),
        "eod": eod,
        "final": _forward_population_table(samples, "final"),
        "dropout": _forward_population_table(samples, "dropout"),
        # 이 root 목록만 자동 판정·수동 상신 대상이다.
        "kill_switches": eod["kill_switches"],
    }


def rank_prior():
    """성과와 섞지 않는 회장님 40년 경험칙 prior 메타데이터."""
    return {
        "policy_name": RANK_POLICY_NAME,
        "model_version": FORWARD_MODEL_VERSION,
        "effective_from": FORWARD_EFFECTIVE_FROM,
        "source": "chairman_40y_rule",
        "strength": "strong",
        "auto_reorder": False,
        "buckets": [
            {"bucket": bucket,
             "label": (RANK_BUCKET_BASELINES.get(bucket) or {}).get("label") or f"bucket {bucket}",
             **(RANK_BUCKET_PRIORS.get(bucket) or {})}
            for bucket in sorted(RANK_BUCKET_BASELINES)
        ],
    }


def _population_rank(s, population):
    if population == "krx_decision":
        return _int_rank(s.get("krx_decision_rank"))
    if population == "nxt_decision":
        return _int_rank(s.get("nxt_decision_rank"))
    if population == "eod":
        return (_int_rank(s.get("eod_rank"))
                or _int_rank(s.get("latest_published_rank")))
    if population == "final":
        return (_int_rank(s.get("latest_published_rank"))
                or _int_rank(s.get("published_rank"))
                or _int_rank(s.get("legacy_rank")))
    if population == "dropout":
        return (_int_rank(s.get("latest_published_rank"))
                or _int_rank(s.get("first_seen_rank"))
                or _int_rank(s.get("legacy_rank")))
    raise ValueError(f"알 수 없는 rank 모집단: {population}")


def _population_bucket(s, population):
    bucket = _int_bucket(s.get(f"{population}_bucket"))
    return bucket if bucket is not None else _int_bucket(s.get("rank_bucket_at_signal"))


def _average_ranks(values, *, higher_is_better):
    """동률에 평균 순위를 부여한다(1이 최상)."""
    order = sorted(range(len(values)), key=lambda i: values[i], reverse=higher_is_better)
    ranks = [0.0] * len(values)
    pos = 0
    while pos < len(order):
        end = pos + 1
        while end < len(order) and values[order[end]] == values[order[pos]]:
            end += 1
        avg_rank = ((pos + 1) + end) / 2
        for idx in order[pos:end]:
            ranks[idx] = avg_rank
        pos = end
    return ranks


def _pearson(xs, ys):
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    dx = [x - mx for x in xs]
    dy = [y - my for y in ys]
    denom = math.sqrt(sum(x * x for x in dx) * sum(y * y for y in dy))
    if denom == 0:
        return None
    return sum(x * y for x, y in zip(dx, dy)) / denom


def _ndcg(predicted_order, actual_ranks):
    """실제 고가순위를 relevance로 쓴 NDCG. 음수 고가일에도 정의된다."""
    n = len(predicted_order)
    if n < 2:
        return None
    relevance = [n + 1 - rank for rank in actual_ranks]

    def dcg(rels):
        return sum((2 ** rel - 1) / math.log2(pos + 2)
                   for pos, rel in enumerate(rels))

    observed = dcg([relevance[i] for i in predicted_order])
    ideal = dcg(sorted(relevance, reverse=True))
    return observed / ideal if ideal > 0 else None


def _rank_eval_population(samples, population):
    eligible = []
    for s in samples:
        if not _population_member(s, population):
            continue
        rank = _population_rank(s, population)
        high = s.get("next_high_pct")
        if rank is None or not isinstance(high, (int, float)):
            continue
        row = dict(s)
        row["_eval_rank"] = rank
        eligible.append(row)

    by_date = {}
    for s in eligible:
        by_date.setdefault(s.get("date"), []).append(s)

    multi_days = []
    single_days = []
    rank_highs = {}
    winner_ranks = {}
    count_groups = {}
    for date in sorted(by_date):
        day = sorted(by_date[date], key=lambda s: (s["_eval_rank"], str(s.get("code") or "")))
        for s in day:
            rank_highs.setdefault(s["_eval_rank"], []).append(float(s["next_high_pct"]))
        if len(day) == 1:
            single_days.append({"date": date, "high": float(day[0]["next_high_pct"])})
            continue

        highs = [float(s["next_high_pct"]) for s in day]
        actual_ranks = _average_ranks(highs, higher_is_better=True)
        max_high = max(highs)
        winner_indexes = {i for i, high in enumerate(highs) if high == max_high}
        top1_hit = 0 in winner_indexes
        top3_hit = bool(set(range(min(3, len(day)))) & winner_indexes)
        spearman = _pearson([float(s["_eval_rank"]) for s in day], actual_ranks)
        ndcg = _ndcg(list(range(len(day))), actual_ranks)
        winner_rank = min(day[i]["_eval_rank"] for i in winner_indexes)
        winner_ranks[winner_rank] = winner_ranks.get(winner_rank, 0) + 1
        row = {
            "date": date,
            "candidate_count": len(day),
            "top1_hit": top1_hit,
            "top3_contains_winner": top3_hit,
            "spearman": spearman,
            "ndcg": ndcg,
            "winner_published_rank": winner_rank,
        }
        multi_days.append(row)
        count_groups.setdefault(len(day), []).append(row)

    def rate(rows, key):
        return round(sum(1 for row in rows if row[key]) / len(rows) * 100, 1) if rows else None

    spearmans = [row["spearman"] for row in multi_days if row["spearman"] is not None]
    ndcgs = [row["ndcg"] for row in multi_days if row["ndcg"] is not None]
    rank_rows = []
    for rank in sorted(rank_highs):
        vals = rank_highs[rank]
        rank_rows.append({
            "rank": rank,
            "n": len(vals),
            "avg_high_pct": round(sum(vals) / len(vals), 2),
            "median_high_pct": round(_median(vals), 2),
        })
    candidate_counts = []
    for count in sorted(count_groups):
        rows = count_groups[count]
        candidate_counts.append({
            "candidate_count": count,
            "days": len(rows),
            "top1_hit": rate(rows, "top1_hit"),
            "top3_contains_winner": rate(rows, "top3_contains_winner"),
        })
    singleton_highs = [row["high"] for row in single_days]
    return {
        "population": population,
        "candidate_n": len(eligible),
        "day_n": len(by_date),
        "multi_candidate_days": len(multi_days),
        "single_candidate": {
            "days": len(single_days),
            "avg_high_pct": (round(sum(singleton_highs) / len(singleton_highs), 2)
                             if singleton_highs else None),
        },
        # 단일후보 날은 아래 top-k 분모에서 제외한다.
        "top1_hits": sum(1 for row in multi_days if row["top1_hit"]),
        "top1_n": len(multi_days),
        "top1_hit": rate(multi_days, "top1_hit"),
        "top3_hits": sum(1 for row in multi_days if row["top3_contains_winner"]),
        "top3_n": len(multi_days),
        "top3_contains_winner": rate(multi_days, "top3_contains_winner"),
        "spearman": round(sum(spearmans) / len(spearmans), 4) if spearmans else None,
        "spearman_n": len(spearmans),
        "ndcg": round(sum(ndcgs) / len(ndcgs), 4) if ndcgs else None,
        "ndcg_n": len(ndcgs),
        "rank_avg_high": rank_rows,
        "winner_published_rank": [
            {"rank": rank, "days": count} for rank, count in sorted(winner_ranks.items())
        ],
        "candidate_counts": candidate_counts,
        "valid": len(multi_days) >= FEATURE_MIN_N,
    }


def rank_eval(samples):
    """저장된 forward 모델·시점별 후보 순위와 실제 익일 고가 순위를 비교한다."""
    forward = [s for s in samples if _is_forward_sample(s)]
    versions = sorted({s.get("rank_model_version") for s in forward if s.get("rank_model_version")})
    by_model = {}
    for version in versions:
        model_samples = [s for s in forward if s.get("rank_model_version") == version]
        by_model[version] = {
            "effective_from": min(
                (_date_digits(s.get("rank_model_effective_from")) or FORWARD_EFFECTIVE_FROM)
                for s in model_samples
            ),
            "tie_policy": "actual_high_ties_share_winner; predicted_ties_code_asc",
            "ndcg_relevance": "actual_next_high_rank",
            "populations": {
                population: _rank_eval_population(model_samples, population)
                for population in ("krx_decision", "nxt_decision", "eod", "final", "dropout")
            },
        }
    return {
        "basis": "forward_saved_rank_only",
        "by_model_version": by_model,
        "reference": {
            "legacy_mixed_models": {
                "multi_candidate_days": 8,
                "top1_hits": 3,
                "top3_hits": 6,
                "note": f"구정렬 혼합 기준선이며 {FORWARD_MODEL_VERSION} 성과에 포함하지 않음",
            }
        },
    }


def write_evaluation_json(samples, exclusions=None):
    """forward 익일 결과를 날짜별 Mac 로컬 JSON에 파생 저장한다."""
    try:
        from radar_json_store import write_evaluation
    except Exception as e:
        log(f"[warn] evaluation JSON store 사용 불가: {e}")
        return

    by_date = {}
    for s in samples:
        if _is_forward_sample(s):
            by_date.setdefault(s["date"], []).append(s)
    excluded_by_date = {}
    for row in exclusions or []:
        excluded_by_date.setdefault(row["date"], []).append(row)
    for date in sorted(set(by_date) | set(excluded_by_date)):
        day = by_date.get(date, [])
        actual_by_population = {}
        for population in ("krx_decision", "nxt_decision", "eod", "final", "dropout"):
            rows = [s for s in day if _population_member(s, population)
                    and isinstance(s.get("next_high_pct"), (int, float))]
            ranks = _average_ranks([float(s["next_high_pct"]) for s in rows],
                                   higher_is_better=True)
            actual_by_population[population] = {
                str(s["code"]): rank for s, rank in zip(rows, ranks)
            }
        payload = {
            "schema_version": 1,
            "record_type": "next_day_evaluation",
            "signal_date": date,
            "evaluated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
            "results": [],
        }
        for s in sorted(day, key=lambda row: str(row.get("code") or "")):
            entry_price = (s.get("evaluated_entry") or s.get("daily_final_close")
                           or s.get("signal_close") or s.get("entry"))
            payload["results"].append({
                "code": s.get("code"),
                "name": s.get("name"),
                "rank_model_version": s.get("rank_model_version"),
                "rank_bucket_at_signal": s.get("rank_bucket_at_signal"),
                "entry_basis": s.get("evaluated_entry_basis") or "KRX_CLOSE",
                "entry_price": entry_price,
                "populations": {
                    population: {
                        "present": _population_member(s, population),
                        "decision_rank": _population_rank(s, population),
                        "rank_bucket": _population_bucket(s, population),
                        "actual_high_rank": actual_by_population[population].get(str(s.get("code"))),
                    }
                    for population in ("krx_decision", "nxt_decision", "eod", "final", "dropout")
                },
                "next_day": {
                    "date": s.get("eval_date"),
                    "open": s.get("next_open"),
                    "high": s.get("next_high"),
                    "low": s.get("next_low"),
                    "close": s.get("next_close"),
                    "open_pct": s.get("next_open_pct"),
                    "high_pct": s.get("next_high_pct"),
                    "low_pct": s.get("next_low_pct"),
                    "close_pct": s.get("return_pct"),
                },
                "status": "evaluated",
            })
        for s in sorted(excluded_by_date.get(date, []), key=lambda row: str(row.get("code") or "")):
            payload["results"].append({
                "code": s.get("code"), "name": s.get("name"),
                "rank_model_version": s.get("rank_model_version"),
                "rank_bucket_at_signal": s.get("rank_bucket_at_signal"),
                "entry_basis": "KRX_CLOSE", "entry_price": s.get("entry"),
                "populations": {
                    population: {"present": _population_member(s, population),
                                 "decision_rank": _population_rank(s, population),
                                 "rank_bucket": _population_bucket(s, population),
                                 "actual_high_rank": None}
                    for population in ("krx_decision", "nxt_decision", "eod", "final", "dropout")
                },
                "next_day": None, "status": "excluded_untradable",
                "exclusion": s.get("exclusion"),
            })
        result = write_evaluation(payload, trade_date=date)
        if not result.ok:
            log(f"[warn] evaluation JSON 저장 실패 {date}: {result.error}")


def save_weights(tuned):
    today = datetime.now(KST).strftime("%Y-%m-%d")
    prev = {}
    if os.path.exists(WEIGHTS_PATH):
        try:
            prev = json.load(open(WEIGHTS_PATH, encoding="utf-8"))
        except Exception:
            pass
    if tuned is None:
        return prev or None
    hist = prev.get("history", [])
    if not hist or hist[-1].get("weights") != tuned["weights"]:
        hist.append({"date": today, "weights": tuned["weights"], "basis_n": tuned["basis_n"]})
    out = {"weights": tuned["weights"], "default": DEFAULT_WEIGHTS,
           "lifts": tuned["lifts"], "basis_n": tuned["basis_n"],
           "updated": today, "history": hist[-30:]}
    atomic_write_json(WEIGHTS_PATH, out)
    return out


# ── 분할 전략 실측 트래커 — 레이더 신호를 20/30/50 분할 + 7%익절/-5%손절로 매매 가정,
#    forward 일봉으로 실현 net 수익 누적(표시 전용·라이브 보정). 손절=종가기준(저가 데이터 없음).
STRAT = {"tranches": [0.2, 0.3, 0.5], "tp": 7.0, "sl": 5.0, "fee": 0.3, "hold": 10, "addwin": 4}
STRATEGY_MIN_N = 30  # 분할전략 패널 유효 최소 표본(미만은 "수집 중")


def _strategy_outcome(closes, highs, i):
    """20/30/50 분할(하락일 추가)+7%익절/-5%손절(종가)+수수료 차감 → (outcome, ret_net)|None(forward 부족)."""
    if i < 0 or i + STRAT["hold"] >= len(closes):
        return None
    w = STRAT["tranches"]
    bought = [(closes[i], w[0])]

    def avg():
        return sum(p * x for p, x in bought) / sum(x for _, x in bought)

    out = None
    ret = 0.0
    for t in range(i + 1, i + STRAT["hold"] + 1):
        a = avg()
        if a <= 0:
            return None
        if highs[t] >= a * (1 + STRAT["tp"] / 100):
            out, ret = "win", STRAT["tp"]; break
        if closes[t] <= a * (1 - STRAT["sl"] / 100):
            out, ret = "stop", (closes[t] / a - 1) * 100; break
        if (t - i) <= STRAT["addwin"] and len(bought) < len(w) and closes[t] < closes[t - 1]:
            bought.append((closes[t], w[len(bought)]))
    if out is None:
        out, ret = "time", (closes[i + STRAT["hold"]] / avg() - 1) * 100
    return out, round(ret - STRAT["fee"], 2)


def strategy_eval():
    """미시뮬 reaccum 신호를 forward 일봉으로 분할전략 시뮬 → history에 s['strategy'] 기록.
    10거래일 보유라 신호 후 ~16일(달력) 지난 것만 처리(그 전엔 보류). 40일 초과·신호일봉 부재는 None 만료."""
    today = datetime.now(KST).strftime("%Y%m%d")
    n_done = 0
    for path, hist in load_history():
        if hist.get("date", "") >= today:
            continue
        try:
            age = (datetime.now(KST).date()
                   - datetime.strptime(hist["date"], "%Y%m%d").date()).days
        except Exception:
            continue
        if age < 16:
            continue  # forward 10거래일 미확보 — 다음 실행에서 재시도
        changed = False
        for code, s in hist.get("suspects", {}).items():
            if s.get("pattern") != "reaccum" or "strategy" in s:
                continue
            try:
                bars = kis.daily_prices(code, days=40)
            except Exception:
                continue
            idx = next((k for k, b in enumerate(bars) if b.get("date") == hist["date"]), None)
            if idx is None:
                if age > 40:
                    s["strategy"] = None; changed = True  # 더는 못 받음 → 만료
                continue
            closes = [b.get("close") for b in bars]
            highs = [b.get("high") for b in bars]
            win = closes[idx:idx + STRAT["hold"] + 1] + highs[idx:idx + STRAT["hold"] + 1]
            if any(x is None for x in win):   # close·high 어느 쪽이라도 결측이면 채점 보류/만료
                if age > 40:
                    s["strategy"] = None; changed = True
                continue
            oc = _strategy_outcome(closes, highs, idx)
            if oc is None:
                if age > 40:
                    s["strategy"] = None; changed = True
                continue
            s["strategy"] = {"outcome": oc[0], "ret_net": oc[1]}
            changed = True; n_done += 1
        if changed:
            atomic_write_json(path, hist)
    log(f"[backtest] 분할전략 시뮬 신규 {n_done}건")


def strategy_sim_stats():
    """history에 누적된 분할전략 실현 결과 집계(표시 전용)."""
    rows = [s["strategy"] for _, hist in load_history()
            for s in hist.get("suspects", {}).values()
            if isinstance(s.get("strategy"), dict) and "ret_net" in s["strategy"]]
    n = len(rows)
    base = {"n": n, "min_n": STRATEGY_MIN_N, "tp": STRAT["tp"], "sl": STRAT["sl"],
            "fee": STRAT["fee"], "tranches": STRAT["tranches"]}
    if not n:
        return {**base, "win_rate": None, "stop_rate": None, "avg_net": None,
                "profit_rate": None, "worst": None}
    rets = sorted(r["ret_net"] for r in rows)
    return {**base,
            "win_rate": round(sum(1 for r in rows if r["outcome"] == "win") / n * 100, 1),
            "stop_rate": round(sum(1 for r in rows if r["outcome"] == "stop") / n * 100, 1),
            "avg_net": round(sum(rets) / n, 3),
            "profit_rate": round(sum(1 for x in rets if x > 0) / n * 100, 1),
            "worst": rets[0]}


def write_performance(samples, series, bins, weights, dropouts=None,
                      experimental=None, experimental_dropouts=None, exclusions=None):
    n = len(samples)
    hits = sum(1 for s in samples if s["hit"])
    rets = [s["return_pct"] for s in samples]
    dropouts = dropouts or []
    experimental = experimental or []
    experimental_dropouts = experimental_dropouts or []
    material_all = samples + dropouts + experimental + experimental_dropouts
    final_high_population = samples + experimental
    rank_all = material_all
    reaccum_experimental = [s for s in experimental + experimental_dropouts
                            if s.get("pattern") == "reaccum"]
    reaccum_sorted = sorted(reaccum_experimental, key=lambda s: (s.get("date", ""), s.get("code", "")))
    # 💥 흔들기 표본 — 마감카드 잔존/탈락 무관 전량(shakeout 플래그 기준). 강도 밴드 튜닝축 전진검증.
    # + 과거 소급 재구성분(shakeout_backfill.json)을 (code,date) 디둡 병합 — 라이브 표본 우선(중복 시 라이브 유지).
    shakeout_live = [s for s in experimental + experimental_dropouts if s.get("shakeout")]
    _seen = {(s.get("code"), s.get("date")) for s in shakeout_live}
    shakeout_backfill = [b for b in load_shakeout_backfill()
                         if (b.get("code"), b.get("date")) not in _seen]
    shakeout_experimental = shakeout_live + shakeout_backfill
    dn = len(dropouts)
    exclusions = exclusions or []
    halt_n = sum((row.get("exclusion") or {}).get("reason_code") == "HALT_PLACEHOLDER"
                 for row in exclusions)
    late_n = sum((row.get("exclusion") or {}).get("reason_code") == "LATE_RESUME_BAR"
                 for row in exclusions)
    def high_target(level):
        known = [row for row in final_high_population if row.get(f"touch{level}") is not None]
        hits = sum(row.get(f"touch{level}") is True for row in known)
        return {"population": "final_suspects", "hits": hits, "n": len(known),
                "rate": round(hits / len(known) * 100, 1) if known else None,
                "unique_n": len({row.get("code") for row in known if row.get("code")}),
                "signal_day_n": len({row.get("date") for row in known if row.get("date")})}
    out = {
        "as_of": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "summary": {
            "n": n,
            "hit_rate": round(hits / n * 100, 1) if n else None,
            "avg_return": round(sum(rets) / n, 2) if n else None,
            "high3_rate": round(sum(1 for s in samples if s["high3"]) / n * 100) if n else None,
            "tracking_days": len(series),
            "excluded_untradable_n": len(exclusions),
            "excluded_halt_placeholder_n": halt_n,
            "excluded_late_resume_n": late_n,
            "legacy_eligibility_unknown_n": sum(
                1 for s in material_all if s.get("next_session_eligibility") is None),
            # 장중 탈락군(마감 카드 미잔존) 참고 성적 — 탈락 필터의 효용 검증용.
            # 주 통계·튜닝에는 포함되지 않는다.
            "dropout": ({"n": dn,
                         "hit_rate": round(sum(1 for s in dropouts if s["hit"]) / dn * 100, 1)}
                        if dn else None),
        },
        "next_session_high_touch_7": high_target(7),
        "next_session_high_touch_13": high_target(13),
        "series": series,
        "bins": bins,
        "by_pattern": group_stats(samples, "pattern"),
        # 테마/섹터별 성과 — "어느 테마가 강한 반등인가" 데이터 근거. valid 게이트로 소표본 단정 방지.
        "by_sector": group_stats_gated(samples, "sector"),
        "by_theme": fill_theme_leaders(group_stats_gated(samples, "theme"), samples),
        # AI 익일 예측(prob_up) 검증 루프 — ai_predict()가 기록한 표본의 적중·보정 통계
        "ai": ai_stats(samples),
        # 메가스파크×수급 가설 검증 표 (스파크 배율 구간 × 당일 수급매수)
        "spark_flow": spark_flow_matrix(samples),
        # 등락률 구간별 익일 상승확률 — '몇 % 구간 종가매수가 익일 더 오르나'(재매집 실험 풀)
        "change_bands": change_band_stats(reaccum_experimental),
        # 폭발일 회전율 구간별 익일 상승확률 — '시총 대비 폭발이 클수록 더 오르나'(재매집 실험 풀, peak_turnover 비중 검증)
        "peak_turnover_bands": peak_turnover_band_stats(reaccum_experimental),
        # 5분 스파크 횟수 구간별 익일 상승확률 — 주식분석.md ③ '스파크 많을수록 오르나' 전진 검증(재매집 실험 풀)
        "reignition_count_bands": reignition_count_band_stats(reaccum_experimental),
        # 폭발일 마감강도(IBS) 구간별 익일 상승확률 — 7일 표본 반직관 가설('약마감↑') 전진 검증(재매집 실험 풀)
        "peak_ibs_bands": peak_ibs_band_stats(reaccum_experimental),
        # 💥 흔들기 결합축 튜닝표 — 2일회전율·고점낙폭·결합축별 익일 상승확률·고가터치율(회장님 20년룰 검증)
        "shakeout_bands": shakeout_band_stats(shakeout_experimental),
        # ⭐ 매우좋음 전용 성과표 — dd6 기준 Tier1/Tier2/후보/일반 흔들기 분리(정렬·배지 검증용)
        "very_good_bands": very_good_tier_stats(shakeout_experimental),
        # 정렬4 rank_bucket 성과표 + kill switch 자동 판정. core/실험/탈락 전체 suspects 표본으로 검증한다.
        "rank_bucket_stats": rank_bucket_stats(rank_all),
        # prior/소급/전진을 분리한다. 실제 상신 가능한 kill switch는 forward.eod에만 있다.
        "rank_prior": rank_prior(),
        "rank_bucket_stats_retro": rank_bucket_stats_retro(rank_all),
        "rank_bucket_stats_forward": rank_bucket_stats_forward(rank_all),
        # 신호일 후보끼리 실제 익일 고가 winner를 맞혔는지 모델·의사결정 시점별 평가.
        "rank_eval": rank_eval(rank_all),
        # 📰 뉴스/공시 재료 등급 전진검증 — 오늘 이후 material 기록 표본만 known, 정렬·자동매매 미반영.
        "material_bands": material_grade_stats(material_all),
        "material_signal_bands": material_signal_stats(material_all),
        # 분할 전략 실측 — 20/30/50 분할+7%익절/-5%손절 실현 net 누적(라이브 보정)
        "strategy_sim": strategy_sim_stats(),
        "experimental": {
            # 재매집(reaccum) = 현 파이프라인 주력 산출물. core(fade/shakeout)와 격리(score_raw=0)돼
            # 메인 통계엔 미반영이나, 실제 매일 쌓이는 트랙이라 자체 적중률 추세·최근표를 노출한다.
            "reaccum": {
                **sample_stats(reaccum_experimental),
                "tracking_days": len(series),
                "series": build_series(reaccum_experimental),
                "recent": [{"date": s["date"], "name": s["name"],
                            "score": s.get("suspicion_score") or 0,  # 표시점수(구표본 미기록=0)
                            "hit": s["hit"], "return_pct": s["return_pct"]}
                           for s in reaccum_sorted[-20:]][::-1],
            },
            # '예전 대장' 재매집 엣지 검증(대장 vs 비대장 익일 적중률 A/B·lift) — 코어 격리
            "leader_reaccum": leader_reaccum_stats(reaccum_experimental),
        },
        "weights": {
            "current": (weights or {}).get("weights") or DEFAULT_WEIGHTS,
            "default": DEFAULT_WEIGHTS,
            "tuned": bool(weights and weights.get("basis_n", 0) >= TUNE_MIN_SAMPLES),
            "basis_n": (weights or {}).get("basis_n", 0),
            "tune_min_samples": TUNE_MIN_SAMPLES,
            "history": (weights or {}).get("history", []),
        },
        "recent": [{"date": s["date"], "name": s["name"], "score": s["score"],
                    "hit": s["hit"], "return_pct": s["return_pct"]}
                   for s in samples[-20:]][::-1],
        "disclaimer": DISCLAIMER,
    }
    os.makedirs(os.path.dirname(PERF_PATH), exist_ok=True)
    atomic_write_json(PERF_PATH, out)
    return out


def git(*args):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)


def acquire_git_lock():
    """전 푸셔 공용 git 직렬화 락 (publish.py와 동일 패턴) — autostash 교차 오염 방지."""
    try:
        import fcntl
        fh = open("/tmp/stocknews_git.lock", "w")
        fcntl.flock(fh, fcntl.LOCK_EX)
        return fh
    except ImportError:
        return None


def push_state():
    # 공용 git 락은 main()이 첫 추적 파일 쓰기 전에 이미 보유 (이중 획득 = flock 자기 데드락)
    files = glob.glob(os.path.join(HISTORY_DIR, "*.json")) + [PERF_PATH]
    if os.path.exists(WEIGHTS_PATH):
        files.append(WEIGHTS_PATH)
    git("add", "--", *files)
    if git("diff", "--cached", "--quiet").returncode == 0:
        print("변경 없음 — push skip")
        return
    r = git("commit", "-q", "-m", "data: 레이더 성과 검증 갱신")
    if r.returncode != 0:
        sys.stderr.write("commit 실패:\n" + r.stderr[-300:])
        sys.exit(1)
    for attempt in range(2):  # 다른 푸셔(publish 등)와 경합 시 1회 재시도
        pl = git("pull", "--rebase", "--autostash", "origin", "main")
        if pl.returncode != 0:
            sys.stderr.write("pull --rebase 실패 — 수동 확인 필요:\n" + pl.stderr[-300:])
            git("rebase", "--abort")
            sys.exit(1)
        pr = git("push", "origin", "main")
        if pr.returncode == 0:
            print("push 완료")
            return
    sys.stderr.write("push 실패:\n" + pr.stderr[-300:])
    sys.exit(1)


def main():
    # 추적 파일(history·weights·performance) 쓰기 전 공용 git 락 — 락 밖 미커밋 변경을
    # 타 푸셔 autostash가 스태시/충돌로 날리는 것 방지. publish가 9~20시로 확장돼 17:2x publish와
    # 락이 겹칠 수 있으나 공용 블로킹 락이라 정합성 보존(겹치면 publish가 잠깐 대기 후 다음 회차 자가복구).
    git_lock = acquire_git_lock()  # noqa: F841 — 프로세스 종료까지 유지
    evaluate()
    ai_predict()  # 당일 마감 카드의 AI 예측 기록 (익일 evaluate가 채점)
    strategy_eval()  # 분할 전략 실측 시뮬(forward 10일 충족분) — history에 누적
    samples = collect_samples()
    exclusions = collect_evaluation_exclusions()
    write_evaluation_json(samples, exclusions)
    # 주 통계·튜닝 = 마감 카드 잔존(final) 표본만 — 정석 사용법(종가 매수)과 모집단 일치.
    core = [s for s in samples if s["final"] and not s.get("visible_experimental")]
    experimental = [s for s in samples if s["final"] and s.get("visible_experimental")]
    dropouts = [s for s in samples if (not s["final"]) and not s.get("visible_experimental")]
    experimental_dropouts = [s for s in samples
                             if (not s["final"]) and s.get("visible_experimental")]
    series = build_series(core)
    bins = build_bins(core)
    weights = save_weights(tune_weights(core))
    perf = write_performance(core, series, bins, weights, dropouts,
                             experimental, experimental_dropouts, exclusions)
    s = perf["summary"]
    print(f"[backtest] 최종카드 표본 {s['n']}건 · 적중률 {s['hit_rate']}% · "
          f"평균수익 {s['avg_return']}% · 고가+3% {s['high3_rate']}% · "
          f"추적 {s['tracking_days']}일 · 장중탈락 {len(dropouts)}건(참고) · "
          f"실험표본 {len(experimental)}건 · AI평가표본 {perf['ai']['n']}건")
    if "--push" in sys.argv[1:]:
        push_state()


if __name__ == "__main__":
    main()
