#!/usr/bin/env python3
"""이벤트 매집 레이더 — "당일 폭발 → 오늘 5분 양봉 스파크(재매집)" 스캐너 (2026-06 정의 개편).

목적: 최근 6거래일 큰 폭발(고가 +22% AND 거래량이 유통주식수의 90%+)이 난 종목이, (하락 여부 무관)
      당일 5분봉 양봉(몸통%≥2%)으로 3회 이상 다시 분출하는(재매집) 것을 탐지.

파이프라인:
  [폭발 캐치] 매 회차 시장별 네이버 up(등락률) 랭킹을 훑어 '고가 +22% AND 당일 거래량/유통주식수 ≥90%'
              폭발을 레지스트리(.explosion_registry.json)에 기록 → 최근 6거래일 폭발만 추적.
              (거래대금 순위·등락률 합집합 유니버스는 폐지. 당일 폭발은 /forecast 페이지에 게시.)
   ▼
  [재매집(반등)] 전일 폭발 종목이 당일 5분봉 양봉(몸통%≥2%) 3회 이상 스파크.   ← KIS 현재가·일봉·분봉
                 (식음=고점 대비 하락 게이트는 폐지 — 하락 등락률은 보지 않는다.)
   ▼
  [조건1·5] 이벤트 캘린더 × 뉴스 민감도 → event_calendar/theme_map (표시 가점)

reaccum/explosion 트랙은 고정 표시 점수(REACCUM_SCORE base)로 게시(검증중, score_raw=0으로 통계 격리).
빈 레이더(후보 0)도 정상 상태.

사용:
  python3 scripts/radar.py                          # 기본 임계값
  python3 scripts/radar.py --names 한온시스템        # 특정 종목 강제 시드
출력: stdout JSON {generated_at, params, events[], explosions[], suspects[]}
"""
import os
import sys
import json
import argparse
from datetime import datetime, timezone, timedelta

from net import get_bytes
from team1_collect import resolve_code, fetch_news, is_individual_stock, UA
from team2_relevance import score_news, make_aliases
from event_calendar import upcoming_events
from theme_map import match_events, match_sensitivity, THEMES
import kis_client as kis
import float_ratio

KST = timezone(timedelta(hours=9))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPLOSION_REGISTRY_PATH = os.path.join(REPO, ".explosion_registry.json")
REACCUM_SEED_PATH = os.path.join(REPO, "data", "reaccum_seed.json")
PERFORMANCE_PATH = os.path.join(REPO, "web", "data", "performance.json")

# ---- 기본 임계값 (2026-06 폭발 정의 전면 개편) ----
# 가격(OHLC)은 항상 J(KRX 공식), 거래대금·거래량·수급만 UN(KRX+NXT 통합, kis_client.MONEY_MARKET).
# ── 폭발 정의: 당일 고가등락률 ≥22% AND 당일 거래량 / 유통주식수 ≥90%(유통주식이 통째로 손바뀜).
#    거래대금 순위·등락률 합집합 유니버스는 폐지 — 폭발 스캔 소스는 네이버 up(등락률) 랭킹뿐.
EXPLOSION_HIGH_PCT = 22.0          # 폭발 당일 고가 등락률 하한(%)
EXPLOSION_VOL_TURNOVER = 90.0      # 폭발 당일 거래량 / 유통주식수 회전율 하한(%) — 유동비율 없으면 미확정(스킵)
EXPLOSION_WINDOW = 6               # 최근 6거래일 폭발만 재매집(반등) 후보로 추적
EXPLOSION_SCAN_N = 50              # 시장별 네이버 up(등락률) 상위 N에서 폭발 감시(22%+ 누락 방지)
# ── 반등(재매집) 정의: 최근 6거래일 폭발 종목이 (하락 여부 무관) 당일 5분봉 '양봉 몸통%≥2%'가 3회 이상
#    스파크. 식음(고점 대비 하락) 게이트는 폐지 — 하락 등락률 퍼센트는 보지 않는다.
REIGNITION_SPAN_MIN = 5            # 재반등 스파크 판정 분봉 합성 단위(분)
REIGNITION_BODY_PCT = 2.0          # 5분 양봉 몸통%(|종가−시가|/시가) 하한
REIGNITION_MIN_COUNT = 3           # 당일 자격 양봉(스파크)이 이 수 이상이어야 반등 인정
REACCUM_SCORE = 62                 # 검증중 노출용 고정 표시 점수 base(raw 통계와 분리, score_raw=0)
# "예전 대장"(was_theme_leader) 판정 — regex 테마가 아니라 권위 업종(sector) 기준.
LEADER_MIN_GROUP = 3               # 같은 (폭발일, 업종) 폭발군이 이 수 이상일 때만 대장 판정
LEADER_MARGIN = 1.5               # 1위 거래대금이 2위의 이 배 이상이어야 대장 인정(근소차 가짜 대장 방지)


def log(msg):
    print(msg, file=sys.stderr, flush=True)



# ---------- 재반등 신호: 5분봉 양봉 몸통 스파크(거래대금 게이트 없음) ----------

def aggregate_minute_bars(bars, span_min):
    """당일 1분봉을 장 시작(09:00) 기준 span_min분봉으로 합성.

    open=구간 첫봉 시가, high/low=극값, close=마지막봉 종가, vol=합.
    value=Σ(close×vol) — KIS 1분봉엔 거래대금 필드가 없어 종가×거래량으로 근사(원).
    """
    buckets = {}
    order = []
    for b in bars:
        t = b.get("time", "")
        if len(t) != 6 or not b.get("close"):
            continue
        hh, mm = int(t[:2]), int(t[2:4])
        mins = hh * 60 + mm
        start = 9 * 60
        if mins < start:
            continue
        idx = (mins - start) // span_min
        key_m = start + idx * span_min
        key = f"{key_m // 60:02d}{key_m % 60:02d}00"
        val = b["close"] * b["vol"]
        row = buckets.get(key)
        if row is None:
            buckets[key] = {"time": key, "open": b["open"], "high": b["high"],
                            "low": b["low"], "close": b["close"], "vol": b["vol"],
                            "value": val}
            order.append(key)
        else:
            row["high"] = max(row["high"], b["high"])
            row["low"] = min(row["low"], b["low"])
            row["close"] = b["close"]
            row["vol"] += b["vol"]
            row["value"] += val
    return [buckets[k] for k in sorted(order)]


def reignition_bars(bars, body_pct_min=REIGNITION_BODY_PCT,
                    span_min=REIGNITION_SPAN_MIN):
    """오늘 재반등 자격 5분봉(스파크) '전체'를 시각순으로.

    자격: 당일 '상승' 5분봉(종가>시가) 중 몸통%((종가−시가)/시가×100) ≥ body_pct_min 인 봉.
    → "큰 상승 몸통" 스파크 = 폭발 종목에 돈이 다시 들어오는 재매집 신호. 거래대금 게이트는 없다(횟수만으로 판정).
    하락·도지 봉은 제외(폭락 중 오탐 방지). 각 봉 {body_pct, time("HH:MM"=버킷 시작), value_eok,
    close, open}. 게이트(3회+)·표시(대표봉)와 텔레그램 봉단위 알림이 공용으로 쓴다(value_eok는 표시용).
    """
    out = []
    for bar in aggregate_minute_bars(bars, span_min):
        if bar["open"] <= 0 or bar["close"] <= bar["open"]:
            continue  # 상승 몸통만 (하락·도지 제외)
        body = (bar["close"] - bar["open"]) / bar["open"] * 100
        if body < body_pct_min:
            continue
        out.append({"body_pct": round(body, 2),
                    "time": f"{bar['time'][:2]}:{bar['time'][2:4]}",
                    "value_eok": round(bar["value"] / 1e8),
                    "close": bar["close"], "open": bar["open"]})
    return out


# ---------- 재매집: 거래대금 폭발 레지스트리 ----------

def _today_yyyymmdd():
    return datetime.now(KST).strftime("%Y%m%d")


def _empty_registry():
    return {"trading_days": [], "records": {}, "window_scanned": {}}


def load_explosion_registry(path=EXPLOSION_REGISTRY_PATH):
    """untracked 로컬 폭발 레지스트리 로드. 손상/부재 시 빈 상태로 시작."""
    if not os.path.exists(path):
        return _empty_registry()
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        log(f"[warn] 폭발 레지스트리 로드 실패(빈 상태 사용): {e}")
        return _empty_registry()
    if not isinstance(data, dict):
        return _empty_registry()
    data.setdefault("trading_days", [])
    data.setdefault("records", {})
    data.setdefault("window_scanned", {})  # 6일 백필 비용 가드(code→YYYYMMDD)
    if not isinstance(data["trading_days"], list) or not isinstance(data["records"], dict):
        return _empty_registry()
    if not isinstance(data["window_scanned"], dict):
        data["window_scanned"] = {}
    return data


def save_explosion_registry(reg, path=EXPLOSION_REGISTRY_PATH):
    tmp = path + ".tmp"
    reg["updated_at"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
    json.dump(reg, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _merge_trading_days(reg, dates):
    days = {d for d in reg.get("trading_days", []) if isinstance(d, str)}
    days.update(d for d in dates if isinstance(d, str) and len(d) == 8)
    reg["trading_days"] = sorted(days)[-120:]


def _upsert_explosion(reg, rec):
    key = f"{rec['peak_date']}:{rec['code']}"
    old = reg["records"].get(key)
    if old:
        rec["peak_value_eok"] = max(float(old.get("peak_value_eok", 0) or 0),
                                    float(rec.get("peak_value_eok", 0) or 0))
        rec["peak_high_pct"] = max(float(old.get("peak_high_pct", 0) or 0),
                                   float(rec.get("peak_high_pct", 0) or 0))
        # 폭발일 거래량 회전율도 max 병합 — 장중 누적이라 단조증가지만, 부분 회차·소스 혼용
        # (seed 종일 vs live 장중)에서 더 큰(완결된) 값을 보존(회전율 점수 안정·게이트 재검증 일관).
        rec["vol_turnover_pct"] = max(float(old.get("vol_turnover_pct", 0) or 0),
                                      float(rec.get("vol_turnover_pct", 0) or 0)) or None
        # source는 권위 순(live>seed>telegram)으로 승격 — 텔레그램 시드가 bootstrap에서 먼저
        # 기록돼도, 같은 폭발이 이후 live 랭킹/seed로 확인되면 그쪽이 이김(가짜 '채널포착' 배지 방지).
        _SRC_RANK = {"live": 3, "seed": 2, "telegram": 1}
        rec["source"] = max(old.get("source") or "live", rec.get("source") or "live",
                            key=lambda s: _SRC_RANK.get(s, 0))
        # 원인/테마 메타는 기존 비어있을 때만 신규로 채움 — 폭발 당일 신선 catalyst가
        # 이후 회차의 stale 값으로 덮이지 않게(source와 동일 철학). cause_done이 캡처 동결 마커.
        for k in ("sector", "theme", "cause_summary", "cause_titles", "cause_done"):
            if old.get(k) and not rec.get(k):
                rec[k] = old[k]
    reg["records"][key] = rec


def _recent_active_explosions(reg, window):
    dates = sorted(d for d in reg.get("trading_days", []) if isinstance(d, str))
    if dates:
        active_dates = set(dates[-window:])
    else:
        active_dates = set(sorted({r.get("peak_date") for r in reg.get("records", {}).values()
                                   if r.get("peak_date")})[-window:])
    latest = {}
    for rec in reg.get("records", {}).values():
        if rec.get("peak_date") not in active_dates:
            continue
        code = rec.get("code")
        if not code:
            continue
        prev = latest.get(code)
        if prev is None or _explosion_supersedes(rec, prev):
            latest[code] = rec
    return latest


def _explosion_supersedes(rec, prev):
    """code별 대표 폭발 레코드 선정 우선순위: ① 새 정의로 검증된(vol_turnover_pct 적재) 레코드가
    구 정의(None) 레코드를 항상 이긴다 — 마이그레이션 윈도(~6일) 동안 더 최근 peak_date의 legacy
    레코드가 유효한 옛 폭발을 가려(shadow) reaccum 후보를 억누르는 것 방지. ② 동급이면 최근 peak_date."""
    rec_valid = rec.get("vol_turnover_pct") is not None
    prev_valid = prev.get("vol_turnover_pct") is not None
    if rec_valid != prev_valid:
        return rec_valid
    return rec.get("peak_date", "") > prev.get("peak_date", "")


def _snapshot_trade_date(code, now):
    trade_date = (now.get("date") or "").strip()
    if trade_date:
        return trade_date
    try:
        daily = kis.daily_prices(code, days=1)
    except Exception as e:
        log(f"[warn] 스냅샷 영업일 확인 실패 {code}: {e}")
        return ""
    return (daily[-1].get("date") if daily else "") or ""


def _load_seed_items(path):
    """data/reaccum_seed.json을 유연하게 읽는다: list 또는 {names,codes,items} 허용."""
    if not path or not os.path.exists(path):
        return []
    try:
        raw = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        log(f"[warn] reaccum seed 로드 실패: {e}")
        return []
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        items = []
        items.extend(raw.get("items") or [])
        items.extend(raw.get("names") or [])
        for c in raw.get("codes") or []:
            items.append({"code": c} if isinstance(c, str) else c)
    else:
        return []
    out = []
    for item in items:
        if isinstance(item, str):
            out.append({"name": item})
        elif isinstance(item, dict):
            out.append(item)
    return out


def _resolve_seed_items(names, seed_path):
    items = [{"name": nm} for nm in (names or [])]
    items.extend(_load_seed_items(seed_path))
    resolved = []
    seen = set()
    for item in items:
        name = (item.get("name") or item.get("title") or "").strip()
        code = (item.get("code") or item.get("symbol") or "").strip()
        if not code and name and name.isdigit() and len(name) == 6:
            code, name = name, item.get("name") or name
        if not code and name:
            try:
                code = resolve_code(name) or ""
            except Exception as e:
                log(f"[warn] seed 종목 코드 해석 실패 {name}: {e}")
                code = ""
        if not code or code in seen:
            continue
        seen.add(code)
        resolved.append({"code": code, "name": name or code, "source": item.get("source", "seed")})
    return resolved


def _telegram_seed_items(p):
    """텔레그램 채널 언급 종목을 재매집 보조 시드로 해석. 실패해도 본작업 계속(fail-safe).

    채널은 '재료가 막 언급된' 후보만 제공 — 폭발·식음·투신·재반등 게이트는 그대로 적용된다."""
    if not getattr(p, "telegram_seed", False):
        return []
    try:
        import telegram_news
        mentions = telegram_news.fetch_mentions(
            p.telegram_channel, max_age_min=p.telegram_max_age, limit=p.telegram_max)
    except Exception as e:
        log(f"[warn] 텔레그램 시드 수집 실패(건너뜀): {e}")
        return []
    out = [{"code": m["code"], "name": m.get("name") or m["code"], "source": "telegram"}
           for m in mentions if m.get("code")]
    if out:
        log(f"[radar] 텔레그램 채널({p.telegram_channel}) 언급 {len(out)}건 시드 추가: "
            + ", ".join(m["name"] for m in out[:8]))
    return out


def _close_strength(high, low, close):
    """폭발일 마감 강도 → (IBS, 윗꼬리%). IBS=(종가−저가)/(고가−저가)[1=고가마감, 0=저가마감],
    윗꼬리%=(고가−종가)/종가×100. 7일 표본 실증: 약마감(IBS↓·윗꼬리↑=장중 찍고 종가 밀림)이 익일
    연속성↑, 강마감(상한가류, IBS↑)은 익일 식음↑ 경향 — 표시·전진검증용(소표본이라 점수·게이트 미반영)."""
    hi, lo, cl = float(high or 0), float(low or 0), float(close or 0)
    # 클램프: 일봉 EOD에선 lo≤cl≤hi이지만, 혹시 모를 글리치(cl>hi 등)에도 0..1 / 윗꼬리≥0 보장.
    ibs = round(min(1.0, max(0.0, (cl - lo) / (hi - lo))), 2) if hi > lo else 1.0
    uppertail = round(max(0.0, (hi - cl) / cl * 100), 1) if cl > 0 else 0.0
    return ibs, uppertail


def _scan_code_window(reg, code, name, source, p):
    """code의 최근 윈도(explosion_window 거래일) 일봉을 훑어 새 정의(고가≥22% AND 거래량/유통주식수≥90%)
    폭발일을 찾아 registry에 upsert(vol_turnover_pct 포함). seed 부트스트랩·6일 소급 백필 공용 per-stock 스캔.

    반환: 완료된 스캔이면 등록 폭발일 수(≥0). **일봉 fetch 실패(KIS 장애)면 None** — 스캔 미완료이므로
    호출부(백필)가 'scanned' 마킹을 건너뛰고 다음 회차 재시도하게 한다(일시 장애가 그날 종일 스킵으로 굳는 것 방지).
    유동비율 스크랩은 윈도에 22% 고가 날이 있을 때만(비용 절감 — 라이브 경로처럼 22% 게이트 후 유동비율 조회)."""
    try:
        daily = kis.daily_prices_jmoney_un(code, days=max(12, p.explosion_window + 6))  # 거래량=UN 통합
    except Exception as e:
        log(f"[warn] {name} 일봉 실패: {e}")
        return None   # 스캔 미완료(일시 장애) — 재시도 대상
    if len(daily) < 2:
        return 0
    _merge_trading_days(reg, [d["date"] for d in daily[-p.explosion_window:]])
    recent_dates = {d["date"] for d in daily[-p.explosion_window:]}
    # 사전패스(cheap): 윈도 내 고가≥22% 날 후보 추출 — 없으면 유동비율 스크랩 없이 종료(비용 절감).
    cand = []
    for i, bar in enumerate(daily):
        if bar["date"] not in recent_dates or i == 0:
            continue
        prev_close = daily[i - 1].get("close") or 0
        if prev_close <= 0:
            continue
        high_pct = (bar["high"] / prev_close - 1) * 100
        if high_pct >= p.explosion_high_pct:    # 게이트 ①: 고가등락률 ≥22%
            cand.append((bar, prev_close, high_pct))
    if not cand:
        return 0   # 22% 날 없음 — 자격 없음(완료된 스캔, 유동비율 불필요)
    fratio, flisted = float_ratio.get_float_and_listed(code)  # 22% 날이 있는 코드만 유동비율 조회
    if not (fratio and fratio > 0 and flisted and flisted > 0):
        return 0  # 유통주식수 미상 → 90% 회전율 확정 불가, 폭발 미인정(fail-safe)
    qualifying = []
    for bar, prev_close, high_pct in cand:
        vol_turnover = float_ratio.vol_turnover(float(bar.get("volume") or 0), fratio, flisted)
        if vol_turnover is None or vol_turnover < p.explosion_vol_turnover:  # 게이트 ②: 거래량/유통주식수 ≥90%
            continue
        qualifying.append({
            "code": code, "name": name, "peak_date": bar["date"],
            "peak_value_eok": round(bar["value"] / 1e8),
            "peak_high_pct": round(high_pct, 2),
            "vol_turnover_pct": round(vol_turnover, 1),
            "source": source,
        })
    if not qualifying:
        return 0
    # 업종(sector) 백필: '예전 대장' 판정이 권위 업종으로 묶이려면 레코드도 sector 필요.
    # 레지스트리에 이미 있으면 재사용(중복 price_now 회피), 없을 때만 1콜. dry-run은 저장 안 하므로 생략.
    sector = _known_sector(reg, code)
    if not sector and not p.dry_run:
        try:
            sector = (kis.price_now(code) or {}).get("sector", "") or ""
        except Exception as e:
            log(f"[warn] {name} 업종 조회 실패: {e}")
            sector = ""
    for rec in qualifying:
        rec["sector"] = sector
        _upsert_explosion(reg, rec)
    return len(qualifying)


def bootstrap_seed_explosions(reg, p):
    """지정 종목 + 텔레그램 채널 언급 종목의 최근 일봉으로 과거 폭발(고가22%+거래량/유통주식수90%)을
    즉시 부트스트랩. per-stock 스캔은 _scan_code_window 공용 헬퍼."""
    count = 0
    resolved = _resolve_seed_items(p.names, p.reaccum_seed)
    seen = {r["code"] for r in resolved}
    for it in _telegram_seed_items(p):       # 채널 언급 종목 합치기(코드 중복 제외)
        if it["code"] not in seen:
            seen.add(it["code"])
            resolved.append(it)
    for item in resolved:
        count += _scan_code_window(reg, item["code"], item["name"], item.get("source", "seed"), p) or 0
    return count


def _known_sector(reg, code):
    """레지스트리에 이미 기록된 이 종목의 업종(있으면) — 중복 price_now 회피용."""
    for rec in reg.get("records", {}).values():
        if rec.get("code") == code and rec.get("sector"):
            return rec["sector"]
    return ""


def _up_ranking_rows(p):
    """시장별 네이버 up(등락률) 랭킹 상위 explosion_scan_n을 code 기준 dedup → (rows, 총행수, 실패 시장 수).
    폭발 라이브 감시·6일 소급 백필이 공용으로 쓰는 스캔 유니버스(현 등락률 정렬).
    fail_count>0 = 한 시장 랭킹이 비정상(그 시장 폭발 누락 가능) → scan_ok 판정에 반영(거짓 '깨끗' 방지)."""
    rows, seen, total, fail = [], set(), 0, 0
    for market in ("KOSPI", "KOSDAQ"):
        try:
            up_rows, _ = _rank_page("up", market, 1)
            total += len(up_rows)
            for row in up_rows[:p.explosion_scan_n]:
                c = row.get("code")
                if c and c not in seen:
                    seen.add(c)
                    rows.append(row)
        except Exception as e:
            fail += 1
            log(f"[warn] {market} 등락률 랭킹 수집 실패: {e}")
    return rows, total, fail


def backfill_window_explosions(reg, p):
    """6일 소급 폭발 백필 — 오늘 등락률 상위 ∪ 기존 레지스트리 활성 코드(재검증)의 지난 윈도 일봉을 스캔해
    새 정의(22%/90%) 폭발일을 registry에 채운다(vol_turnover_pct). 라이브 스캔(오늘)으로만 쌓이던 후보 풀을
    소급 보강 → 전일 폭발 종목이 오늘 5분 양봉 3회면 즉시 수상종목으로 노출. 등록한 폭발일 수 반환.

    비용 가드: ① 이미 검증된(활성 vol_turnover_pct 있는) code 스킵 ② reg['window_scanned'][code]==오늘이면
    재스캔 안 함(10분 cron 매 회차 전체 재스캔 방지 — 첫 회차만 풀, 이후 신규 진입분만)."""
    today = _today_yyyymmdd()
    scanned = reg.setdefault("window_scanned", {})
    if not isinstance(scanned, dict):
        scanned = reg["window_scanned"] = {}
    active = _recent_active_explosions(reg, p.explosion_window)
    validated = {c for c, r in active.items() if r.get("vol_turnover_pct") is not None}
    # 유니버스: 오늘 등락률 상위 + 활성 레지스트리 코드(이름은 레코드/랭킹에서). 시드는 bootstrap이 이미 처리.
    universe = {}  # code -> name
    try:
        for row in _up_ranking_rows(p)[0]:
            if row.get("code"):
                universe[row["code"]] = row.get("name") or row["code"]
    except Exception as e:
        log(f"[warn] 백필 등락률 유니버스 수집 실패: {e}")
    for c, r in active.items():
        universe.setdefault(c, r.get("name") or c)
    count = scanned_now = 0
    for code, name in universe.items():
        if code in validated or scanned.get(code) == today:
            continue  # 이미 검증됨 / 오늘 이미 스캔함 → 재스캔 비용 절약
        n = _scan_code_window(reg, code, name, "backfill", p)
        if n is None:
            continue  # 일봉 fetch 실패(일시 장애) — scanned 마킹 안 함, 다음 회차 재시도
        scanned[code] = today
        scanned_now += 1
        count += n
    # window_scanned는 오늘 것만 유지(과거 자동 정리 — 다음 거래일 재스캔 허용)
    reg["window_scanned"] = {c: d for c, d in scanned.items() if d == today}
    log(f"[radar] 6일 소급 백필: 신규 폭발 {count}건(스캔 {scanned_now}코드, 검증완료 {len(validated)} 스킵)")
    return count


def update_live_explosions(reg, p):
    """당일 폭발(고가등락률 ≥22% AND 거래량/유통주식수 ≥90%)을 감시해 registry에 적재.

    유니버스(스캔 소스) = 시장별 네이버 up(등락률) 랭킹뿐(거래대금 순위·합집합 유니버스는 폐지).
    ⚠ up 랭킹은 '현재 등락률' 정렬이라, 장중 고가 +22%를 찍고 종가로 크게 밀린 종목은 순위 하위로
    내려가 상위 explosion_scan_n(기본 50) 밖이면 누락될 수 있다(기본값으로 대부분 커버되나 한계 존재).
    게이트는 KIS price_now(가격=J 공식,
    거래량=UN 통합)와 float_ratio(유통비율·발행주식수)로 판정한다. 유동비율이 없으면 90% 회전율을
    확정할 수 없어 폭발 미확정으로 스킵한다(22% 단독으로는 폭발로 보지 않음).

    반환 (count, scan_ok, today_explosions) — today_explosions는 /forecast '당일 폭발 종목' 리스트.
    """
    rows, up_rank_total, rank_fail = _up_ranking_rows(p)  # 네이버 up(등락률) 상위 dedup + 도달성·실패시장 카운트
    count = 0
    attempted = price_errors = 0  # price_now 도달성 — 전수 실패면 KIS 부분장애 신호
    high_pass = float_missing = 0  # 22% 고가 게이트 통과 수 / 그중 유동비율 결측으로 탈락한 수
    live_dates = set()
    today_explosions = []
    today = _today_yyyymmdd()
    for row in rows:
        attempted += 1
        try:
            now = kis.price_now_jmoney_un(row["code"])  # 가격=J 공식 / 거래량=UN 통합(회전율 게이트)
        except Exception as e:
            price_errors += 1
            log(f"[warn] 폭발 현재가 조회 실패 {row.get('name')}: {e}")
            continue
        trade_date = _snapshot_trade_date(row["code"], now)
        if trade_date != today:
            continue
        if not now.get("high") or not now.get("prev_close"):
            continue
        high_pct = (now["high"] / now["prev_close"] - 1) * 100
        if high_pct < p.explosion_high_pct:    # 게이트 ①: 고가등락률 ≥22%
            continue
        high_pass += 1
        # 게이트 ②: 당일 거래량 / 유통주식수 ≥ 90%. 유통주식수 = 발행주식수 × 유동비율.
        # 유동비율(또는 발행주식수)이 없으면 회전율 확정 불가 → 폭발 미확정으로 스킵(fail-safe).
        volume = float(now.get("volume") or 0)
        fr, flisted = float_ratio.get_float_and_listed(row["code"])
        vt = float_ratio.vol_turnover(volume, fr, flisted)  # 공유 산식(거래량/유통주식수 %)
        if vt is None:
            # 거래량 0/결측은 게이트 미충족일 뿐 — 유동비율(wisereport) 결측만 '소스 장애' 카운트.
            if not (fr and fr > 0 and flisted and flisted > 0):
                float_missing += 1
            continue
        if vt < p.explosion_vol_turnover:
            continue
        vol_turnover = vt
        value_won = float(now.get("value") or 0)   # 당일 거래대금(UN, 표시용)
        # 마감강도(peak_ibs)는 장중 현재가로 산출하면 종가와 달라 오염되므로 여기서 저장하지 않는다.
        # 수상종목(전일 폭발)으로 노출될 때 scan_reaccum_candidate가 '완결된 폭발일 일봉'에서 산출(EOD 정확).
        rec_new = {
            "code": row["code"],
            "name": row["name"],
            "peak_date": trade_date,
            "peak_value_eok": round(value_won / 1e8),
            "peak_high_pct": round(high_pct, 2),
            "vol_turnover_pct": round(vol_turnover, 1),     # 폭발일 거래량 회전율(유통주식수 대비 %)
            "source": "live",
            "sector": now.get("sector", ""),  # 0 API
        }
        # 신규 폭발만 catalyst 1회 캡처(폭발 당일=신선). 시도하면 cause_done=True로 동결 →
        # 무뉴스 종목(cause_summary="")도 매 회차 재fetch 안 함. cause_titles는 재매집 시점
        # match_events 0 API 재계산용. dry-run은 생략(저장 안 하므로 무의미한 fetch 방지).
        key = f"{trade_date}:{row['code']}"
        if not p.dry_run and not (reg["records"].get(key) or {}).get("cause_done"):
            _, theme, cause_summary, raw_titles = _explain_cause(row["code"], row["name"], now.get("sector", ""))
            rec_new["theme"] = theme  # 순수 테마 카테고리(없으면 "") — fade와 동일, sector 폴백 안 함
            rec_new["cause_summary"] = cause_summary
            # 원본 제목 전체(필터 전) — 재매집 match_events가 scan_one(raw_titles)과 동일 매칭하도록
            rec_new["cause_titles"] = raw_titles
            rec_new["cause_done"] = True
        _upsert_explosion(reg, rec_new)
        live_dates.add(trade_date)
        today_explosions.append({
            "code": row["code"],
            "name": row["name"],
            "sector": now.get("sector", ""),
            "high_pct": round(high_pct, 2),
            "vol_turnover_pct": round(vol_turnover, 1),
            "value_eok": round(value_won / 1e8),
            "price": now.get("price"),
            "change_pct": round(float(now.get("change_pct") or 0), 2),
        })
        count += 1
    _merge_trading_days(reg, live_dates)
    # 유동비율 소스(wisereport) 광역 장애 가드: 22% 고가를 통과한 종목이 다수인데 전부 유동비율
    # 결측으로 폭발 0건이면, '조용한 깨끗한 레이더'가 실은 float 소스 장애일 수 있다 → 로그로 surface
    # (캐시 7일 보호로 흔치 않아 exit은 하지 않고 경고만 — 운영자가 cron 로그에서 인지).
    if count == 0 and high_pass >= 3 and float_missing == high_pass:
        log(f"[warn] 22% 고가 통과 {high_pass}종목 전부 유동비율 결측 → 폭발 0건. "
            f"wisereport(float) 소스 장애 의심(캐시 만료/차단). data/float_ratio.json 확인 권장.")
    # KIS/네이버 도달성: 한 시장이라도 랭킹 수집이 실패(rank_fail>0)했거나, 양 시장 모두 빈손이거나,
    # price_now가 절반 이상 실패하면 '부분장애'로 본다 — 한 시장만 죽고 다른 시장 행이 있어 거짓 '깨끗'
    # 게시(그 시장 폭발 누락)되는 것을 방지(개편 전 단일시장 실패 전파 동작 복원).
    scan_ok = up_rank_total > 0 and rank_fail == 0 and not (
        attempted > 0 and price_errors >= max(2, (attempted + 1) // 2))
    return count, scan_ok, today_explosions


def _backfill_today_explosions(today_explosions, reg, today):
    """/forecast '당일 폭발' 리스트 안정화: 오전에 폭발해 registry에 든 종목이 오후 네이버 up 랭킹
    상위에서 밀려 이번 회차 라이브 스캔에 안 잡혀도(또는 스캔이 예외로 실패해도), registry의
    오늘(peak_date==today) 레코드로 백필해 리스트가 장중에 깜빡이지 않게 한다(저장된 폭발 시점 값
    사용 — 현재가는 표시 안 함). 회전율 내림차순 정렬한 리스트를 반환. 라이브 스캔 try 밖에서 호출."""
    seen_today = {e["code"] for e in today_explosions}
    for r in reg.get("records", {}).values():
        if r.get("peak_date") != today or r.get("code") in seen_today:
            continue
        if r.get("vol_turnover_pct") is None:  # 새 정의로 적재된 레코드만(구 게이트 레코드 제외)
            continue
        today_explosions.append({
            "code": r["code"],
            "name": r.get("name") or r["code"],
            "sector": r.get("sector", ""),
            "high_pct": round(float(r.get("peak_high_pct") or 0), 2),
            "vol_turnover_pct": r.get("vol_turnover_pct"),
            "value_eok": int(float(r.get("peak_value_eok") or 0)),
            "price": None,   # 랭킹에서 밀린 백필 종목 — 현재가 미표시(폭발 사실만)
            # 백필 행은 등락률 미표시(None) — 폭발 후 랭킹에서 밀려 신뢰할 현재가/종가가 없다(price=None).
            # 거짓 등락률 표기를 막으려 아예 노출 안 함. 폭발 사실은 high_pct·회전율로 충분히 전달.
            "change_pct": None,
        })
        seen_today.add(r["code"])
    # 회전율 내림차순, 단 라이브 행(price!=None) 우선 — 밀려난 백필 행(stale)이 활발히 거래되는
    # 라이브 폭발보다 #1 자리(Flame 배지)를 차지하지 않게(시각적 위계 = 현재 거래 우선).
    today_explosions.sort(key=lambda e: (e.get("price") is None, -(e.get("vol_turnover_pct") or 0)))
    return today_explosions


def prepare_reaccum_registry(p):
    """(active_explosions, live_scan_ok, today_explosions) 반환. live_scan_ok=False면 폭발감시
    자체가 전면 실패 = KIS/네이버 장애 신호 → 호출부의 수집장애 가드가 사용한다.
    today_explosions = 당일 폭발 종목 리스트(/forecast 게시용)."""
    if not p.reaccum_enabled:
        return {}, True, []
    reg = load_explosion_registry()
    seed_count = bootstrap_seed_explosions(reg, p)
    live_scan_ok = True
    today_explosions = []
    try:
        live_count, live_scan_ok, today_explosions = update_live_explosions(reg, p)
    except Exception as e:
        live_count = 0
        live_scan_ok = False  # 폭발 스캔 전면 실패(raise) — KIS/네이버 장애 의심
        log(f"[warn] 라이브 폭발 감시 실패(기존 registry/seed만 사용): {e}")
    # 6일 소급 폭발 백필 — 등락률 상위∪레지스트리 재검증으로 prior-day 폭발 후보 풀을 채운다(오늘 수상종목용).
    # 실패해도 본작업 안 깨지게 격리(표시 전용 후보 풀 보강).
    try:
        backfill_window_explosions(reg, p)
    except Exception as e:
        log(f"[warn] 6일 소급 백필 실패(라이브/시드만 사용): {e}")
    # 라이브 스캔 성공/예외 무관하게 registry 기반 백필 — 예외 경로에서도 /forecast가 비지 않게(try 밖).
    today_explosions = _backfill_today_explosions(today_explosions, reg, _today_yyyymmdd())
    if not p.dry_run:
        save_explosion_registry(reg)
    elif seed_count or live_count:
        log("[radar] dry-run: 폭발 레지스트리 저장 생략")
    active = _recent_active_explosions(reg, p.explosion_window)
    log(f"[radar] reaccum registry active={len(active)} seed={seed_count} live={live_count} 당일폭발={len(today_explosions)}")
    return active, live_scan_ok, today_explosions


# ── 익일~3일 상승확률 예측(동결 모델) — 전종목 6개월 백테스트로 보정, holdout 검증치 ──
# 정직: "보장"이 아니라 과거 실측 확률. 강 모멘텀 상위군(z≥경계)만 holdout서 유의(53% TEST),
# 중·하는 분리 안 돼 base로 둠. 표시 전용(score_raw 미반영). 라이브로 calibration 지속 보정 예정.
FORECAST_MEAN = {"mom3": 2.1624, "mom5": 5.2731, "ma20_gap": 11.9678, "vol_surge": 1.0054}
FORECAST_STD = {"mom3": 11.5152, "mom5": 17.6902, "ma20_gap": 17.5081, "vol_surge": 1.4395}
FORECAST_STRONG_Z = 0.3617   # 상위 tercile 경계(train)
FORECAST_BASE_3D7 = 46       # 재매집 후보 전체 3일내+7% 터치(과거 실측, 시장 19% 대비)
FORECAST_STRONG_3D7 = 52     # 강 모멘텀 상위군(holdout TEST ~53, 보수적 52)
FORECAST_NEXT7 = 25          # 내일(1일) +7% 터치 — 정직 공개(낮음)


def forecast_prob(mom3, mom5, ma20_gap, vol_surge):
    """동결 모델로 '3일 내 +7% 터치' 과거 실측 확률 라벨 산출(표시 전용·보장 아님)."""
    feats = {"mom3": mom3, "mom5": mom5, "ma20_gap": ma20_gap, "vol_surge": vol_surge}
    z = sum((feats[k] - FORECAST_MEAN[k]) / FORECAST_STD[k] for k in FORECAST_MEAN)
    strong = z >= FORECAST_STRONG_Z
    return {
        "horizon": "3일 내 +7%",
        "prob_pct": FORECAST_STRONG_3D7 if strong else FORECAST_BASE_3D7,
        "base_pct": FORECAST_BASE_3D7,
        "strong": strong,            # 강 모멘텀 상위군(holdout 검증)
        "next_day_7_pct": FORECAST_NEXT7,  # 내일 1일 기준은 낮음 — 정직 표기
        "note": "과거 실측 확률·보장 아님",
    }


def _reaccum_eligible(rec, p):
    """이 레코드가 reaccum(반등조짐) 후보 대상인가 — 새 폭발 정의로 검증(vol_turnover_pct 적재)되고
    고가등락률 ≥ 임계인 레코드만. 구 게이트(거래대금 1,500억/13%) legacy 레코드는 vol_turnover_pct가
    없어 제외. scan_reaccum_candidate의 KIS 호출 직전 게이트와 high_fail 가드 분모가 공유한다."""
    return (rec.get("vol_turnover_pct") is not None
            and float(rec.get("peak_high_pct") or 0) >= p.explosion_high_pct)


def scan_reaccum_candidate(rec, p, events):
    """최근 6거래일 폭발(고가≥22% AND 거래량/유통주식수≥90%) 종목이 (하락 여부 무관) 당일 5분봉
    양봉(몸통%≥2%)이 3회 이상 스파크한 '재매집' 후보를 만든다. 식음(고점 대비 하락)·등락률·MA20·
    투신 수급 게이트는 모두 폐지(전일 폭발 + 당일 5분 양봉 스파크 횟수만 본다)."""
    code, name = rec["code"], rec.get("name") or rec["code"]
    peak_date = rec.get("peak_date")
    if not peak_date:
        return None
    # 새 폭발 정의(고가≥22% AND 거래량/유통주식수≥90%)로 적재된 레코드만 후보로 본다. 개편 전(거래대금
    # 1,500억/13% 게이트) 레코드는 vol_turnover_pct가 없어 — 재검증 없이 식음·반등 후보로 새는 걸 차단
    # (마이그레이션 윈도 ~6일간 정의 불일치 후보 방지. 구 레코드는 윈도 만료로 자가 소거).
    if not _reaccum_eligible(rec, p):
        return None
    try:
        now = kis.price_now_jmoney_un(code)  # 가격=J 공식 / 거래대금·거래량=UN 통합(표시·vsurge)
    except Exception as e:
        log(f"  [skip] {name}: reaccum 현재가 조회 실패 {e}")
        return "ERR"
    if not now.get("price") or not now.get("prev_close"):
        return None
    high = now.get("high") or now["price"]
    high_pct = (high / now["prev_close"] - 1) * 100
    try:
        daily = kis.daily_prices_jmoney_un(code, days=25)  # MA20·vsurge·forecast 피처용(가격=J / 거래대금=UN)
    except Exception as e:
        log(f"  [skip] {name}: reaccum 일봉 실패 {e}")
        return "ERR"
    closes = [d["close"] for d in daily if d.get("close")]
    if len(closes) < 20:
        return None
    signal_date = (now.get("date") or (daily[-1].get("date") if daily else "") or "").strip()
    if signal_date != _today_yyyymmdd() or signal_date <= peak_date:
        return None
    ma20 = sum(closes[-20:]) / 20
    ma10 = sum(closes[-10:]) / 10
    # ── 반등 게이트: 당일 5분봉 양봉 몸통%≥2% 스파크가 REIGNITION_MIN_COUNT(3)회 이상.
    #    (식음 게이트 폐지 — 하락 등락률 무관. 전일 폭발 종목이 오늘 다시 분출하는지만 본다.)
    # 분봉도 거래대금·수급과 동일하게 MONEY_MARKET(기본 UN). 정규장 시간창 가드(kis_client)로 NXT 장 밖 봉 배제.
    try:
        bars = kis.minute_bars_today(code, market=kis.MONEY_MARKET)
    except Exception as e:
        log(f"  [skip] {name}: reaccum 분봉 실패 {e}")
        return "ERR"
    rbars = reignition_bars(bars, p.reignition_body_pct, p.reignition_span_min)
    if len(rbars) < p.reignition_min_count:
        return None
    reignition = max(rbars, key=lambda b: b["body_pct"])  # 대표(최대 몸통) 봉 — 표시용

    ma10_margin = (now["price"] / ma10 - 1) * 100 if ma10 else 0.0
    ma20_margin = (now["price"] / ma20 - 1) * 100 if ma20 else 0.0
    # ── 변별 점수(표시 전용 '강도') — 검증된 적중확률이 아니라 셋업을 얼마나 강하게 충족했나의
    #    순위. raw(score_raw)는 0 유지 = 실험 격리라 코어 튜닝에 미반영(표본 쌓이면 데이터로 검증).
    re_body_max = max((b["body_pct"] for b in rbars), default=0.0)
    # 회전율(유통주식수 대비, 거래량 기준) — 폭발일 회전율은 registry 저장값(vol_turnover_pct, 위 게이트로
    # 항상 존재). 당일 회전율 = 당일 거래량 / 유통주식수(float_ratio.vol_turnover 공유 산식).
    fr, flisted = float_ratio.get_float_and_listed(code)  # 유동비율·발행주식수(보통주만, 캐시)
    turnover_pct = float_ratio.vol_turnover(float(now.get("volume") or 0), fr, flisted)
    # basis는 '실제 산출된 값'에 맞춘다 — 거래량 0 글리치(turnover_pct=None)면 "cap"(값 없음과 일관).
    turnover_basis = "float" if turnover_pct is not None else "cap"
    peak_turnover_pct = rec.get("vol_turnover_pct")  # 새 게이트 통과 레코드라 항상 non-None
    # 폭발일 마감강도 — 항상 '완결된 폭발일 일봉'(EOD)에서 산출. peak_date < signal_date(가드)라 그 일봉은
    # 완성된 종가다 → 장중 proxy로 인한 오염 없음(전진검증·표시 전용, 점수 미반영).
    peak_ibs = peak_uppertail = None
    pbar = next((d for d in daily if d.get("date") == peak_date), None)
    if pbar:
        peak_ibs, peak_uppertail = _close_strength(pbar.get("high"), pbar.get("low"), pbar.get("close"))
    breakdown = {
        "base": REACCUM_SCORE,
        # 게이트 최소 횟수(min_count)를 0점 기준으로 — 최소통과=baseline, 초과분만 가점(임계 바꿔도 정합).
        "re_count": min(10, max(0, (len(rbars) - p.reignition_min_count + 1) * 2)),  # min→2·+1→4·+2→6·+3→8·+4↑→10
        "re_body": round(min(6, max(0, (re_body_max - 2) / 4 * 6))),       # 최대 몸통% 2~6%→0~6
        "peak_turnover": round(min(10, max(0, ((peak_turnover_pct or 0) - 90) / 110 * 10))),  # 폭발일 회전율 90~200%→0~10
        "re_turnover": round(min(6, max(0, ((turnover_pct or 0) - 30) / 70 * 6))),  # 당일 회전율 30~100%→0~6
    }
    score = min(95, REACCUM_SCORE + breakdown["re_count"]
                + breakdown["re_body"] + breakdown["peak_turnover"] + breakdown["re_turnover"])
    raw_breakdown = {k: 0 for k in breakdown}
    # 익일~3일 상승확률 라벨(표시 전용·보장 아님) — 동결 모델. daily/now에서 0 API로 피처 산출.
    vals_d = [d.get("value") or 0 for d in daily]
    nz = [x for x in vals_d[-21:-1] if x > 0]   # 0/결측 거래일 제외 — 희박 거래 시 vsurge 폭증→오라벨 방지
    av20v = sum(nz) / len(nz) if len(nz) >= 10 else 0.0   # 유효 거래일 10일+ 일 때만
    vsurge = (now.get("value") or 0) / av20v if av20v > 0 else 1.0
    mom3 = (closes[-1] / closes[-4] - 1) * 100 if len(closes) >= 4 and closes[-4] else 0.0
    mom5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 and closes[-6] else 0.0
    forecast = forecast_prob(mom3, mom5, ma20_margin, vsurge)
    # 원인/테마("왜 올랐나"): 폭발시점 캡처본(registry) 우선 — 폭발 당일 catalyst라 신선(0 API).
    # 없으면(seed/과거폭발) 재매집 시점 보강 fetch. 표시 전용 — 점수 미반영.
    theme = rec.get("theme") or ""
    cause_summary = rec.get("cause_summary") or ""
    if rec.get("cause_done"):  # 폭발시점 캡처됨 — 0 API(캐시된 원본 제목으로 이벤트만 재계산)
        news_items, titles = [], (rec.get("cause_titles") or [])
    else:                      # seed/과거폭발 — 재매집 시점 보강 fetch (titles=원본, scan_one과 동일 매칭)
        news_items, theme, cause_summary, titles = _explain_cause(code, name, now.get("sector", ""))
    try:  # 이벤트 민감도 — 캐시/보강 경로 무관하게 항상 계산(표시 일관). 실패해도 빈값.
        matched_events, _ = match_events(events, titles, now.get("sector", ""))
    except Exception:
        matched_events = []
    # theme는 순수 카테고리(없으면 "") — fade(scan_one)와 동일, sector 폴백 안 함(그룹핑 대칭)
    return {
        "code": code,
        "name": name,
        "sector": now.get("sector", ""),
        "pattern": "reaccum",
        "shake": None,
        "deep_shake": None,
        "suspicion_score": score,
        "calibrated_prob": None,
        "score_breakdown": breakdown,
        "score_raw": 0,
        "score_breakdown_raw": raw_breakdown,
        "forecast": forecast,   # 3일내 +7% 과거 실측 확률 라벨(표시 전용·보장 아님)
        "price": now["price"],
        "change_pct": round(now["change_pct"], 2),
        "high_pct": round(high_pct, 2),
        "value_eok": round(float(now.get("value") or 0) / 1e8),
        "turnover_pct": turnover_pct,   # 당일 회전율(거래량/유통주식수 %) — 손바뀜 강도
        "peak_turnover_pct": peak_turnover_pct,  # 폭발일 회전율(거래량/유통주식수 %) — 폭발의 자명함
        "float_ratio": fr,              # 유동비율(0~1) — None이면 회전율 미산출
        "turnover_basis": turnover_basis,  # "float"(유통 기준) | "cap"(미상)
        "ma10": round(ma10, 1),
        "ma10_margin_pct": round(ma10_margin, 2),
        "spark": {"clusters": []},
        "spark_max_x": None,   # reaccum은 분봉 스파크 클러스터 없음(None=미산출) — 레거시 spark_flow 통계가 제외
        "spark_max_pct": None,
        "mega_flow": False,
        # 재반등(오늘) 신호 — 대표(최대 몸통) 5분 스파크
        "reignition": {
            "body_pct": reignition["body_pct"],
            "time": reignition["time"],
            "value_eok": reignition["value_eok"],
            "count": len(rbars),   # 당일 자격 양봉 스파크 수(게이트 ≥3)
        },
        # 당일 자격 5분 스파크 전체 — 텔레그램 봉단위 알림용(표시는 reignition 대표봉만 사용)
        "reignition_bars": [{"time": b["time"], "body_pct": b["body_pct"], "value_eok": b["value_eok"]}
                            for b in rbars],
        "news": news_items,
        "matched_events": matched_events,
        "theme": theme,
        "visible_experimental": True,
        "reaccum": {
            "peak_date": peak_date,
            "peak_value_eok": int(float(rec.get("peak_value_eok") or 0)),
            "peak_high_pct": round(float(rec.get("peak_high_pct") or 0), 2),
            "peak_turnover_pct": peak_turnover_pct,  # 폭발일 거래량 회전율(유통주식수 대비 %)
            "peak_ibs": peak_ibs,         # 폭발일 마감강도(0=저가마감·1=고가마감) — 표시·전진검증용
            "peak_uppertail": peak_uppertail,  # 폭발일 윗꼬리%((고가−종가)/종가) — 약마감일수록 큼
            "ma20": round(ma20, 1),
            "ma20_margin_pct": round(ma20_margin, 2),
            "cause_summary": cause_summary,  # 폭발 catalyst 한 줄("왜 올랐나")
        },
    }


def _explosion_day_value_eok(code, peak_date, cache):
    """폭발일 일봉 종일 거래대금(억) — 대장 판정용 단위 통일(seed=종일/live=장중 혼용 편향 제거).
    실패하거나 그날 봉이 없으면 None(호출부가 저장값으로 폴백)."""
    key = (code, peak_date)
    if key in cache:
        return cache[key]
    val = None
    try:
        for bar in kis.daily_prices_jmoney_un(code, days=12):  # 대장 판정=폭발일 UN 거래대금
            if bar.get("date") == peak_date:
                eok = round(float(bar.get("value") or 0) / 1e8)
                val = eok if eok > 0 else None  # 0/결측 봉은 글리치 → None으로 폴백 유도
                break
    except Exception as e:
        log(f"  [warn] {code} 폭발일 거래대금 재조회 실패: {e}")
    cache[key] = val
    return val


def _theme_leader_codes(active_explosions):
    """'예전 대장'(was_theme_leader) 코드 집합. 권위 업종(sector) 기준:
    같은 (폭발일, 업종) 폭발군이 LEADER_MIN_GROUP개+ 일 때, 폭발일 일봉 종일 거래대금 1위가
    2위의 LEADER_MARGIN배 이상이면 그 1종목만 대장. regex 테마 오분류·seed/live 단위 혼용·
    근소차 가짜 대장을 모두 차단. sector 없는 폭발은 그룹에서 제외(보수적).
    거래대금은 폭발일 일봉을 재조회해 그룹(=같은 폭발일) 내 완결도를 통일한다(seed 종일값 vs
    live 장중 캡처값의 편향 제거). 표시 전용 뱃지라 호출은 그룹 ≥3 종목에 한정돼 비용은 제한적."""
    groups = {}
    for c, rec in active_explosions.items():
        sec, pdate = rec.get("sector"), rec.get("peak_date")
        if sec and pdate:
            groups.setdefault((pdate, sec), []).append((c, rec))
    leader_codes = set()
    val_cache = {}
    for grp in groups.values():
        if len(grp) < LEADER_MIN_GROUP:
            continue
        ranked = []
        for c, rec in grp:
            v = _explosion_day_value_eok(c, rec.get("peak_date"), val_cache)
            if v is None:
                v = float(rec.get("peak_value_eok") or 0)  # 폴백: 저장값
            ranked.append((c, v))
        ranked.sort(key=lambda cv: cv[1], reverse=True)
        top_code, top_v = ranked[0]
        second_v = ranked[1][1] if len(ranked) > 1 else 0.0
        # second_v>0 필수: 2위 값이 0/결측이면 마진을 가늠할 수 없어(0*1.5=0 무력화) 보수적으로 미인정.
        if second_v > 0 and top_v >= second_v * LEADER_MARGIN:
            leader_codes.add(top_code)
    return leader_codes


def _leader_cohort_prob(path=PERFORMANCE_PATH):
    """performance.json에서 '예전 대장' 코호트 실측 익일 상승률 {rate,n} — 표본 충분(valid)할
    때만, 아니면 None. 표시 전용(수상카드 한 줄). 파일 부재·파싱 실패는 None(격리)."""
    try:
        perf = json.load(open(path, encoding="utf-8"))
        lr = ((perf.get("experimental") or {}).get("leader_reaccum") or {}).get("leader") or {}
        if lr.get("valid") and lr.get("hit_rate") is not None:
            return {"rate": lr["hit_rate"], "n": lr["n"]}
    except Exception:
        pass
    return None


def attach_reaccum_candidates(suspects, active_explosions, p, events):
    """반등조짐 후보를 suspects에 추가한다(반환: (추가 수, 조회실패 수)). reaccum이 유일 산출물이라
    호출 시 suspects는 비어 있어 항상 신규 append — 구 fade 트랙과의 뱃지 병합 경로는 폐지."""
    if not p.reaccum_enabled or not p.reaccum_visible or not active_explosions:
        return 0, 0
    try:  # 대장 판정 실패가 게시 자체를 막지 않게 격리(표시 전용 뱃지) — 모듈 설계 계약
        leader_codes = _theme_leader_codes(active_explosions)
    except Exception as e:
        log(f"  [warn] 예전 대장 판정 실패(뱃지 생략): {e}")
        leader_codes = set()
    cohort_prob = _leader_cohort_prob()  # 대장 코호트 실측률(표본 충분할 때만, 표시 전용)
    added = err_count = 0
    for code, rec in active_explosions.items():
        try:
            r = scan_reaccum_candidate(rec, p, events)
        except Exception as e:  # reaccum의 어떤 실패도 게시 자체를 막지 않게 격리
            log(f"  [skip] {rec.get('name', code)}: reaccum 예외 {e}")
            continue
        if r == "ERR":
            err_count += 1
            continue
        if not r:
            continue
        is_leader = code in leader_codes
        r["reaccum"]["was_theme_leader"] = is_leader  # 폭발일 업종 거래대금 1위(예전 대장)였나
        r["reaccum"]["source"] = rec.get("source", "live")  # live|seed|telegram(채널 언급發)
        # 예전 대장이면 코호트 실측률 한 줄 표시(표본 충분할 때만, 표시 전용·점수 무관)
        r["leader_cohort_prob"] = cohort_prob if is_leader else None
        suspects.append(r)
        added += 1
    return added, err_count


# ---------- 메인 깔때기 ----------

def derive_theme(titles, sector=""):
    """뉴스 제목들+업종 → 상위 테마 1개(없으면 ""). match_sensitivity 단일 소스.
    결정론: hit 최다 → 동률이면 THEMES 선언 순서(우선순위) 앞선 테마. 예: 업종 '전기·전자'가
    SECTOR_HINTS상 반도체·환율 둘 다 매칭(각 hit=1)이면 먼저 선언된 반도체를 택한다(환율 오선택 방지).
    뉴스가 테마에 기여하면 그 count가 더 커 자연히 우선됨. fade·reaccum·폭발 세 경로 공용."""
    hits = match_sensitivity(titles, sector)
    if not hits:
        return ""
    order = list(THEMES)  # 선언 순서 = 우선순위
    return max(hits, key=lambda c: (hits[c], -order.index(c)))


def _explain_cause(code, name, sector=""):
    """'왜 올랐나' — 종목뉴스 fetch → 재료뉴스(score_news, 표시용) + 테마 + 원인요약 + raw_titles.
    (news_items, theme, cause_summary, raw_titles) 반환. **어떤 예외든 빈값(graceful)** — 표시 전용
    메타가 코어 파이프라인을 중단시키지 않게 전체 try(scan_one의 try/except:pass와 동일 철학).
    raw_titles = fetch 원본 제목 전체(score_news 필터·6컷 전) — match_events/derive_theme가 scan_one과
    동일하게 원본으로 매칭하도록(재매집 이벤트/테마 과소매칭 방지). 추가 API = fetch_news 1콜."""
    try:
        raw = [n for n in fetch_news(code, 10) if n.get("title")]
        raw_titles = [n["title"] for n in raw]
        news_items = score_news(raw, make_aliases(name)).get("relevant", [])[:6]
        theme = derive_theme(raw_titles, sector)
        cause_summary = news_items[0].get("title", "") if news_items else ""
        return news_items, theme, cause_summary, raw_titles
    except Exception:
        return [], "", "", []


_RANK_CACHE = {}  # per-run 캐시: (direction,market,page)→(rows,total). radar는 1회성 프로세스라 stale 무관.
                  # update_live_explosions가 같은 (up,market,1)을 여러 번 보므로 중복 네이버 호출 제거.


def _rank_page(direction, market, page):
    ckey = (direction, market, page)
    if ckey in _RANK_CACHE:
        return _RANK_CACHE[ckey]
    url = (f"https://m.stock.naver.com/api/stocks/{direction}/{market}"
           f"?page={page}&pageSize=100")
    d = json.loads(get_bytes(url, UA))
    if "stocks" not in d:  # 응답 스키마 변화 — 조용한 빈 결과 대신 명시 실패 (실패는 캐시 안 함)
        raise RuntimeError(f"네이버 랭킹 응답에 stocks 없음: {direction}/{market}")
    rows = []
    for s in d.get("stocks", []):
        name, code = s.get("stockName"), s.get("itemCode") or s.get("reutersCode")
        if not is_individual_stock(name, code, s.get("stockEndType")):
            continue
        try:
            rate = float(str(s.get("fluctuationsRatio", "0")).replace(",", ""))
            value_mn = float(str(s.get("accumulatedTradingValue", "0")).replace(",", ""))
        except ValueError:
            continue
        rows.append({"name": name, "code": code, "change_pct": rate, "value_mn": value_mn})
    result = (rows, int(d.get("totalCount") or 0))
    _RANK_CACHE[ckey] = result
    return result


def main():
    ap = argparse.ArgumentParser(
        description="이벤트 매집 레이더 — 과거 폭발 → 오늘 5분 양봉 스파크(재매집) 탐지")
    # 반등(재매집) 게이트 = 전일 폭발 종목 + 당일 5분 양봉 스파크 횟수 — 식음·등락률·MA20·투신 게이트 폐지
    ap.add_argument("--reignition-body-pct", type=float, default=REIGNITION_BODY_PCT,
                    help="5분 양봉 몸통%% 하한(기본 2)")
    ap.add_argument("--reignition-span-min", type=int, default=REIGNITION_SPAN_MIN,
                    help="재반등 스파크 판정 분봉 합성 단위(분, 기본 5)")
    ap.add_argument("--reignition-min-count", type=int, default=REIGNITION_MIN_COUNT,
                    help="당일 자격 양봉 스파크 최소 횟수(기본 3)")
    ap.add_argument("--names", nargs="*", default=[], help="watchlist 강제 포함")
    ap.add_argument("--no-reaccum", dest="reaccum_enabled", action="store_false",
                    help="재매집(reaccum) registry 감시와 후보 생성을 비활성화")
    ap.set_defaults(reaccum_enabled=True)
    ap.add_argument("--no-reaccum-visible", dest="reaccum_visible", action="store_false",
                    help="재매집 후보를 registry에는 기록하되 suspects 화면 노출은 비활성화")
    ap.set_defaults(reaccum_visible=True)
    ap.add_argument("--reaccum-max", type=int, default=12,
                    help="게시 단계에서 예약할 재매집 후보 슬롯 수(파라미터 기록용)")
    ap.add_argument("--explosion-vol-turnover", type=float, default=EXPLOSION_VOL_TURNOVER,
                    help="폭발 게이트: 당일 거래량/유통주식수 회전율 하한(%%, 기본 90)")
    ap.add_argument("--explosion-high-pct", type=float, default=EXPLOSION_HIGH_PCT,
                    help="폭발 당일 고가 등락률 하한(%%, 기본 22)")
    ap.add_argument("--explosion-window", type=int, default=EXPLOSION_WINDOW,
                    help="폭발 유효 거래일 수(식음·반등 추적 윈도)")
    ap.add_argument("--explosion-scan-n", type=int, default=EXPLOSION_SCAN_N,
                    help="시장별 네이버 up(등락률) 상위 N종목에서 폭발 감시")
    ap.add_argument("--reaccum-seed", default=REACCUM_SEED_PATH,
                    help="즉시 부트스트랩용 재매집 seed JSON 경로")
    ap.add_argument("--no-telegram-seed", dest="telegram_seed", action="store_false",
                    help="텔레그램 채널 언급 종목을 재매집 보조 시드로 쓰지 않음")
    ap.set_defaults(telegram_seed=True)
    ap.add_argument("--telegram-channel", default="FastStockNews",
                    help="보조 시드용 공개 텔레그램 채널 username(@제외)")
    ap.add_argument("--telegram-max-age", type=float, default=360.0,
                    help="이 분(min) 이내 채널 글만 시드로 사용(기본 360=6h)")
    ap.add_argument("--telegram-max", type=int, default=25,
                    help="채널 시드 최대 종목 수")
    ap.add_argument("--dry-run", action="store_true",
                    help="폭발 레지스트리를 저장하지 않고 stdout만 생성")
    p = ap.parse_args()
    p.explosion_window = max(1, int(p.explosion_window))
    p.explosion_scan_n = max(1, int(p.explosion_scan_n))
    p.reignition_span_min = max(1, int(p.reignition_span_min))
    p.reignition_min_count = max(1, int(p.reignition_min_count))
    p.reaccum_max = max(0, int(p.reaccum_max))
    active_explosions, live_scan_ok, today_explosions = prepare_reaccum_registry(p)

    # 조건 1: D-10 이벤트 캘린더 (재반등 후보의 이벤트 민감도 표시용)
    events = upcoming_events(10)
    log(f"[radar] D-10 이벤트 {len(events)}건")

    # 유일 산출물 = 재매집 후보 (과거 폭등 → 식음 → 오늘 재반등)
    suspects = []
    reaccum_added, err_count = attach_reaccum_candidates(
        suspects, active_explosions, p, events)
    total = len(active_explosions)
    # high_fail 분모는 '실제 KIS 조회를 시도하는' 적격(새 정의 검증) 레코드 수만 — legacy(vol_turnover_pct
    # None) 레코드는 scan_reaccum_candidate가 KIS 호출 전에 None 반환이라 ERR로 안 잡힌다. 마이그레이션
    # 윈도 동안 legacy가 다수면 total이 부풀어 '절반 실패' 비율이 희석돼 부분장애를 놓치는 걸 방지.
    eligible = sum(1 for r in active_explosions.values() if _reaccum_eligible(r, p))
    log(f"[radar] reaccum 후보 {reaccum_added}건 (폭발감시 {total}종목·적격 {eligible}, 조회실패 {err_count}, live_ok={live_scan_ok})")
    # 데이터 수집 장애 가드 — KIS 토큰/키 장애 시 거짓 '빈 레이더' 게시 방지(구 유니버스 exit(2) 대체).
    #  (a) 폭발감시 전면 실패 + 수상종목 0 + 당일 폭발(/forecast)도 0 → 시장 도달 확인 불가(빈 레지스트리 포함).
    #  (b) 적격 후보가 있는데 절반 이상 조회 실패 → KIS 장애로 반쪽 게시 방지(소형 레지스트리도 floor=2로 포착).
    # 빈 레지스트리·조건 미달로 인한 0건은 정상('레이더 깨끗') — live_scan_ok=True면 종료하지 않는다.
    # ⚠ today_explosions(/forecast)가 registry 백필로 채워졌으면 보여줄 실데이터가 있으므로 중단하지 않는다.
    collection_dead = (not live_scan_ok) and reaccum_added == 0 and len(today_explosions) == 0
    high_fail = eligible > 0 and err_count >= max(2, (eligible + 1) // 2)
    if collection_dead or high_fail:
        log(f"[error] 데이터 수집 장애 의심(live_ok={live_scan_ok}, 실패 {err_count}/{eligible}적격, 후보 {reaccum_added}) — 게시 중단")
        sys.exit(3)
    suspects.sort(key=lambda x: -x["suspicion_score"])

    out = {
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "params": {"market": kis.MONEY_MARKET,  # 거래대금/수급 시장구분: UN=KRX+NXT 통합 / J=KRX 단독(가격은 항상 J)
                   "reignition_body_pct": p.reignition_body_pct,
                   "reignition_span_min": p.reignition_span_min,
                   "reignition_min_count": p.reignition_min_count,
                   "reaccum_enabled": p.reaccum_enabled,
                   "reaccum_visible": p.reaccum_visible,
                   "reaccum_max": p.reaccum_max,
                   "explosion_vol_turnover": p.explosion_vol_turnover,  # 폭발 게이트: 거래량/유통주식수 회전율 하한(%)
                   "explosion_high_pct": p.explosion_high_pct,
                   "explosion_window": p.explosion_window,
                   "explosion_scan_n": p.explosion_scan_n,
                   "telegram_seed": p.telegram_seed,
                   "telegram_channel": p.telegram_channel if p.telegram_seed else None},
        "universe_count": len(active_explosions),
        "events": events,
        "explosions": today_explosions,   # 당일 폭발 종목(/forecast 게시용)
        "suspects": suspects,
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
