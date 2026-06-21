#!/usr/bin/env python3
"""이벤트 매집 레이더 — "과거 폭등 → 식음 → 오늘 재반등" 스캐너.

목적: 과거 어느 날 큰돈이 들어와 폭등(1,500억+고가+13%, UN 통합거래대금)했다가 식은 종목이,
      오늘 거래대금을 동반해 다시 상승 초입에 들어선(재매집 의심) 것을 탐지.

파이프라인:
  [폭등 캐치] 매 회차 시장별 거래대금·등락률 랭킹을 훑어 1,500억 + 고가 +13% 폭발을
              레지스트리(.explosion_registry.json)에 기록 → 최근 6거래일 폭발만 후보.
   ▼
  [식음(중간)] 신호일(오늘) 현재가 ≥ MA20 생존 + 폭발 전후 창에서 투신 순매수(매집).
   ▼
  [재반등(오늘)] 당일 종가/현재 등락률 −4~+10% + 10분봉 몸통%≥2%(당일 한 번이라도)
                + 그 10분봉 1개의 거래대금 ≥ 30억.   ← KIS 현재가·일봉·분봉·수급
   ▼
  [조건1·5] 이벤트 캘린더 × 뉴스 민감도 → event_calendar/theme_map (표시 가점)

reaccum 후보는 고정 표시 점수(REACCUM_SCORE)로 게시(검증중, raw 통계와 분리).
빈 레이더(후보 0)도 정상 상태.

사용 (WSL):
  python3 scripts/radar.py                          # 기본 임계값
  python3 scripts/radar.py --names 한온시스템        # 특정 종목 강제 시드
출력: stdout JSON {generated_at, params, suspects[]}
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
import kimi_client

KST = timezone(timedelta(hours=9))
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPLOSION_REGISTRY_PATH = os.path.join(REPO, ".explosion_registry.json")
REACCUM_SEED_PATH = os.path.join(REPO, "data", "reaccum_seed.json")
PERFORMANCE_PATH = os.path.join(REPO, "web", "data", "performance.json")

# ---- 기본 임계값 ----
# 거래대금은 KRX+NXT 통합(UN) 기준(kis_client.MONEY_MARKET, 가격은 항상 J 공식). NXT 포함으로 J 단독 대비 게이트
# 구간(700~1000억)에서 실측 중앙값 ~1.5배 커져, J기준 1천억을 1,500억으로 ×1.5 재산정(통계 격리·드리프트
# 방지: 같은 '진짜 큰돈' 엄격도를 통합 기준으로 정합. 신규 포착은 유니버스 J∪NX 합집합이 담당).
EXPLOSION_VALUE = 150_000_000_000  # 재매집 폭발 거래대금 하한: 1,500억(원, UN 통합기준 = J 1천억×1.5)
EXPLOSION_HIGH_PCT = 13.0          # 재매집 폭발 당일 고가 등락률 하한(%)
EXPLOSION_WINDOW = 6               # 최근 6거래일 폭발만 재매집 후보
EXPLOSION_RANK_N = 30              # 시장별 거래대금 상위 N에서 라이브 폭발 감시
REACCUM_SCORE = 62                 # 검증중 노출용 고정 표시 점수(raw 통계와 분리)
REACCUM_PRE_DAYS = 4               # 투신 매집 창: 폭발일 −N칼렌더일 ~ 신호일(폭발 전후 — 백테스트 검증)
# 재반등(오늘) 트리거 — 과거 폭등 종목이 식었다가 오늘 거래대금 동반 재상승 초입인지 판정
REACCUM_CHANGE_MIN = -4.0          # 당일 종가/현재 등락률 하한(%) — 폭등이 아닌 "살짝 반등"
REACCUM_CHANGE_MAX = 10.0          # 당일 종가/현재 등락률 상한(%)
REIGNITION_BODY_PCT = 2.0          # 10분봉 몸통%(|종가−시가|/시가) 하한 — 당일 한 번이라도
REIGNITION_VALUE_10M = 3_000_000_000  # 해당 10분봉 1개의 거래대금 하한(원, 30억)
# "예전 대장"(was_theme_leader) 판정 — regex 테마가 아니라 권위 업종(sector) 기준.
LEADER_MIN_GROUP = 3               # 같은 (폭발일, 업종) 폭발군이 이 수 이상일 때만 대장 판정
LEADER_MARGIN = 1.5               # 1위 거래대금이 2위의 이 배 이상이어야 대장 인정(근소차 가짜 대장 방지)


def log(msg):
    print(msg, file=sys.stderr, flush=True)



# ---------- 수급: 외국인/기관 매집 신호 ----------

def accumulation_signal(inv, days=5):
    """최근 N일 외국인+기관 순매수 → {days_net_buy, today, streak}."""
    if not inv:
        return {"net_days": 0, "today_buy": False, "streak": 0, "detail": []}
    recent = inv[-days:]

    def _fo(r):  # 외국인+기관 순매수(키 결측에도 안전)
        return (r.get("frgn") or 0) + (r.get("orgn") or 0)

    detail = [{"date": (r.get("date") or "")[4:],
               "frgn": int(r.get("frgn") or 0), "orgn": int(r.get("orgn") or 0)}
              for r in recent]
    net_days = sum(1 for r in recent if _fo(r) > 0)
    streak = 0
    for r in reversed(recent):
        if _fo(r) > 0:
            streak += 1
        else:
            break
    today_buy = bool(recent) and _fo(recent[-1]) > 0
    return {"net_days": net_days, "today_buy": today_buy, "streak": streak,
            "detail": detail}


# ---------- 재반등 신호: 10분봉 몸통 + 10분봉 거래대금 ----------

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
                    value_min=REIGNITION_VALUE_10M):
    """오늘 재반등 자격 10분봉 '전체'를 시각순으로.

    자격: 당일 '상승' 10분봉(종가>시가) 중 몸통%((종가−시가)/시가×100) ≥ body_pct_min 이고
    그 10분봉 1개의 거래대금(Σ종가×거래량) ≥ value_min(원) 인 봉. → "큰 상승 몸통 + 실거래대금
    동반" = 돈이 다시 들어오는 재상승. 하락·도지 봉은 제외(폭락 중 오탐 방지).
    각 봉 {body_pct, time("HH:MM"=버킷 시작), value_eok, close, open}. 게이트·표시(대표봉)와
    텔레그램 봉단위 알림이 공용으로 쓴다(1분봉이 1개의 10분봉도 못 채우면 빈 리스트)."""
    out = []
    for b10 in aggregate_minute_bars(bars, 10):
        if b10["open"] <= 0 or b10["close"] <= b10["open"]:
            continue  # 상승 몸통만 (하락·도지 제외)
        body = (b10["close"] - b10["open"]) / b10["open"] * 100
        if body < body_pct_min or b10["value"] < value_min:
            continue
        out.append({"body_pct": round(body, 2),
                    "time": f"{b10['time'][:2]}:{b10['time'][2:4]}",
                    "value_eok": round(b10["value"] / 1e8),
                    "close": b10["close"], "open": b10["open"]})
    return out


# ---------- 재매집: 거래대금 폭발 레지스트리 ----------

def _today_yyyymmdd():
    return datetime.now(KST).strftime("%Y%m%d")


def _empty_registry():
    return {"trading_days": [], "records": {}}


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
    if not isinstance(data["trading_days"], list) or not isinstance(data["records"], dict):
        return _empty_registry()
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
        rec["peak_change_pct"] = max(float(old.get("peak_change_pct", 0) or 0),
                                     float(rec.get("peak_change_pct", 0) or 0))
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
        if prev is None or rec.get("peak_date", "") > prev.get("peak_date", ""):
            latest[code] = rec
    return latest


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


def bootstrap_seed_explosions(reg, p):
    """지정 종목 + 텔레그램 채널 언급 종목의 최근 일봉으로 과거 1,500억+고가13% 폭발을 즉시 부트스트랩."""
    count = 0
    resolved = _resolve_seed_items(p.names, p.reaccum_seed)
    seen = {r["code"] for r in resolved}
    for it in _telegram_seed_items(p):       # 채널 언급 종목 합치기(코드 중복 제외)
        if it["code"] not in seen:
            seen.add(it["code"])
            resolved.append(it)
    for item in resolved:
        try:
            daily = kis.daily_prices_jmoney_un(item["code"], days=max(12, p.explosion_window + 6))  # 폭발 게이트=UN 거래대금
        except Exception as e:
            log(f"[warn] seed 일봉 실패 {item['name']}: {e}")
            continue
        if len(daily) < 2:
            continue
        _merge_trading_days(reg, [d["date"] for d in daily[-p.explosion_window:]])
        recent_dates = {d["date"] for d in daily[-p.explosion_window:]}
        qualifying = []
        for i, bar in enumerate(daily):
            if bar["date"] not in recent_dates or i == 0:
                continue
            prev_close = daily[i - 1].get("close") or 0
            if prev_close <= 0:
                continue
            high_pct = (bar["high"] / prev_close - 1) * 100
            if bar["value"] < p.explosion_value or high_pct < p.explosion_high_pct:
                continue
            qualifying.append({
                "code": item["code"],
                "name": item["name"],
                "peak_date": bar["date"],
                "peak_value_eok": round(bar["value"] / 1e8),
                "peak_high_pct": round(high_pct, 2),
                "peak_change_pct": round((bar["close"] / prev_close - 1) * 100, 2),
                "source": item.get("source", "seed"),
            })
        if not qualifying:
            continue
        # 업종(sector) 백필: '예전 대장' 판정이 권위 업종으로 묶이려면 seed 레코드도 sector 필요.
        # 레지스트리에 이미 있으면 재사용(중복 price_now 회피), 없을 때만 1콜.
        # dry-run은 레지스트리를 저장하지 않으므로 fetch 무의미 → 생략(live cause 캡처와 동일 철학).
        sector = _known_sector(reg, item["code"])
        if not sector and not p.dry_run:
            try:
                sector = (kis.price_now(item["code"]) or {}).get("sector", "") or ""
            except Exception as e:
                log(f"[warn] seed 업종 조회 실패 {item['name']}: {e}")
                sector = ""
        for rec in qualifying:
            rec["sector"] = sector
            _upsert_explosion(reg, rec)
            count += 1
    return count


def _known_sector(reg, code):
    """레지스트리에 이미 기록된 이 종목의 업종(있으면) — 중복 price_now 회피용."""
    for rec in reg.get("records", {}).values():
        if rec.get("code") == code and rec.get("sector"):
            return rec["sector"]
    return ""


def update_live_explosions(reg, p):
    """당일 1,500억+고가13% 폭발을 감시해 registry에 적재.

    유니버스 = 시장별 거래대금 순위 ∪ 등락률(네이버 up) 순위. 거래대금 순위만 보면
    등락률로만 잡힌 종목이 누락돼 6일 재매집 윈도에서 사라지므로 합집합으로 본다.
    폭발 거래대금 게이트는 사전필터 없이 KIS price_now 실거래대금(권위)으로 판정. price_now
    value가 0/결측인 일시 글리치일 때만 랭킹값으로 폴백해 확정 폭발 누락을 막고, 비-KIS(네이버)
    랭킹값이 정상 경로에서 폭발 거래대금 하한을 단독 충족해 sub-하한을 오기록하지 않게 한다.
    """
    rows = []
    seen_codes = set()
    value_rank_total = 0  # KIS 거래대금 랭킹이 양 시장에서 돌려준 행 수 (KIS 도달성 신호)

    def _add(row):
        code = row.get("code")
        if not code or code in seen_codes:
            return
        seen_codes.add(code)
        rows.append(row)

    for market in ("KOSPI", "KOSDAQ"):
        vr = list(kis.value_rank_union(market, p.explosion_rank_n))  # 거래대금 순위 J∪NX 합집합(NXT-헤비 누락 방지)
        value_rank_total += len(vr)
        for row in vr:
            _add(row)
        try:                                                         # 등락률 순위(네이버 up) 합집합
            up_rows, _ = _rank_page("up", market, 1)
            for row in up_rows[:p.explosion_rank_n]:
                _add(row)
        except Exception as e:                                       # 네이버 실패 시 거래대금만으로 계속
            log(f"[warn] {market} 등락률 랭킹 폭발 감시 실패(거래대금만 사용): {e}")
    count = 0
    attempted = price_errors = 0  # price_now 도달성 — 전수 실패면 KIS 부분장애 신호
    live_dates = set()
    today = _today_yyyymmdd()
    for row in rows:
        rank_value_won = float(row.get("value_mn") or 0) * 1e6
        attempted += 1
        try:
            now = kis.price_now_jmoney_un(row["code"])  # 가격=J 공식 / 거래대금=UN 통합(폭발 게이트)
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
        if high_pct < p.explosion_high_pct:
            continue
        # 폭발 거래대금 게이트: KIS price_now 실거래대금(권위)으로 판정 — scan_one 거래대금 하한과 동일 철학.
        # price_now value가 0/결측인 일시 글리치일 때만 랭킹값으로 폴백해 확정 폭발 누락을 막고,
        # 비-KIS(네이버) 랭킹값이 정상 경로에서 폭발 거래대금 하한을 단독 충족해 sub-하한을 오기록하지 않게 한다.
        now_value = float(now.get("value") or 0)
        value_won = now_value if now_value > 0 else rank_value_won
        if value_won < p.explosion_value:
            continue
        rec_new = {
            "code": row["code"],
            "name": row["name"],
            "peak_date": trade_date,
            "peak_value_eok": round(value_won / 1e8),
            "peak_high_pct": round(high_pct, 2),
            "peak_change_pct": round(float(now.get("change_pct") or 0), 2),
            "source": "live",
            "sector": now.get("sector", ""),  # 0 API
        }
        # 신규 폭발만 catalyst 1회 캡처(폭발 당일=신선). 시도하면 cause_done=True로 동결 →
        # 무뉴스 종목(cause_summary="")도 매 15분 재fetch 안 함. cause_titles는 재매집 시점
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
        count += 1
    _merge_trading_days(reg, live_dates)
    # KIS 도달성: 거래대금 랭킹이 양 시장 모두 빈손이거나 price_now가 절반 이상 실패하면
    # 'KIS 부분장애'로 본다(value_rank만 성공하고 price_now 전수 실패하는 케이스 포착).
    scan_ok = value_rank_total > 0 and not (
        attempted > 0 and price_errors >= max(2, (attempted + 1) // 2))
    return count, scan_ok


def prepare_reaccum_registry(p):
    """(active_explosions, live_scan_ok) 반환. live_scan_ok=False면 폭발감시(KIS 랭킹)
    자체가 전면 실패 = KIS 장애 신호 → 호출부의 수집장애 가드가 사용한다."""
    if not p.reaccum_enabled:
        return {}, True
    reg = load_explosion_registry()
    seed_count = bootstrap_seed_explosions(reg, p)
    live_scan_ok = True
    try:
        live_count, live_scan_ok = update_live_explosions(reg, p)
    except Exception as e:
        live_count = 0
        live_scan_ok = False  # 랭킹 스캔 전면 실패(raise) — KIS 장애 의심
        log(f"[warn] 라이브 폭발 감시 실패(기존 registry/seed만 사용): {e}")
    if not p.dry_run:
        save_explosion_registry(reg)
    elif seed_count or live_count:
        log("[radar] dry-run: 폭발 레지스트리 저장 생략")
    active = _recent_active_explosions(reg, p.explosion_window)
    log(f"[radar] reaccum registry active={len(active)} seed={seed_count} live={live_count}")
    return active, live_scan_ok


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


def scan_reaccum_candidate(rec, p, events):
    """폭발 이후 식은 구간에서 기관 순매수+MA20 생존 재매집 후보를 만든다."""
    code, name = rec["code"], rec.get("name") or rec["code"]
    peak_date = rec.get("peak_date")
    if not peak_date:
        return None
    try:
        now = kis.price_now_jmoney_un(code)  # 가격=J 공식 / 거래대금·거래량=UN 통합(표시·vsurge)
    except Exception as e:
        log(f"  [skip] {name}: reaccum 현재가 조회 실패 {e}")
        return "ERR"
    if not now.get("price") or not now.get("prev_close"):
        return None
    # 재반등 게이트 ①: 당일 종가/현재 등락률 [−4,+10].
    # 고가는 폭발 후 표시·fade 계산용으로만 쓰고, 재반등 허용 범위는 종가/현재가 기준이다.
    if not (p.reaccum_change_min <= now["change_pct"] <= p.reaccum_change_max):
        return None
    high = now.get("high") or now["price"]
    high_pct = (high / now["prev_close"] - 1) * 100
    try:
        daily = kis.daily_prices_jmoney_un(code, days=25)  # MA=J 종가 / 거래대금=UN(vsurge·표시)
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
    if now["price"] < ma20:
        return None
    try:
        inv = kis.investor_trade_daily(code)   # 투신(ivtr) 포함 일별 수급
    except Exception as e:
        log(f"  [skip] {name}: reaccum 수급 실패 {e}")
        return "ERR"
    # 투신 매집: 폭발 '전후' 창(폭발일 −REACCUM_PRE_DAYS ~ 신호일)에서 투신 순매수 합.
    # (백테스트: '폭발 이후'만 보면 매집 놓침 / 기관계보다 투신이 깨끗 / 일수·금액 임계는
    #  역효과라 느슨하게 순매수>0만 조건. 일수·금액은 카드 정보로만 표시.)
    lo = (datetime.strptime(peak_date, "%Y%m%d") - timedelta(days=REACCUM_PRE_DAYS)).strftime("%Y%m%d")
    # 커버리지 가드: 수급 응답이 창 시작(lo)까지 못 닿으면 투신 합이 과소계산 → unknown 처리(탈락)
    if not inv or min(r.get("date", "") for r in inv) > lo:
        return None
    win = [r for r in inv if lo <= r.get("date", "") <= signal_date]
    ivtr_net = sum(float(r.get("ivtr") or 0) for r in win)
    ivtr_days = sum(1 for r in win if (r.get("ivtr") or 0) > 0)
    ivtr_eok = round(sum(float(r.get("ivtr_won") or 0) for r in win) / 100)  # 백만원→억
    if ivtr_net <= 0:
        return None

    # 재반등 게이트 ②③: 10분봉 몸통%≥2% + 그 10분봉 거래대금≥30억
    try:
        bars = kis.minute_bars_today(code)
    except Exception as e:
        log(f"  [skip] {name}: reaccum 분봉 실패 {e}")
        return "ERR"
    rbars = reignition_bars(bars, p.reignition_body_pct, p.reignition_value_10m)
    if not rbars:
        return None
    reignition = max(rbars, key=lambda b: b["body_pct"])  # 대표(최대 몸통) 봉 — 게이트·표시용

    acc = accumulation_signal(inv)
    denom = now["high"] - now["prev_close"] if now.get("high") else 0.0
    fade_pct = (now["high"] - now["price"]) / denom * 100 if denom > 0 else 0.0
    ma10_margin = (now["price"] / ma10 - 1) * 100 if ma10 else 0.0
    ma20_margin = (now["price"] / ma20 - 1) * 100 if ma20 else 0.0
    # ── 변별 점수(표시 전용 '강도') — 검증된 적중확률이 아니라 셋업을 얼마나 강하게 충족했나의
    #    순위. raw(score_raw)는 0 유지 = 실험 격리라 코어 튜닝에 미반영(B: 표본 쌓이면 데이터로 검증).
    re_value_max = max((b["value_eok"] for b in rbars), default=0)
    re_body_max = max((b["body_pct"] for b in rbars), default=0.0)
    peak_eok = int(float(rec.get("peak_value_eok") or 0))
    breakdown = {
        "base": REACCUM_SCORE,
        "re_value": round(min(12, max(0, (re_value_max - 30) / 270 * 12))),   # 재반등 거래대금 30~300억→0~12
        "re_body": round(min(6, max(0, (re_body_max - 2) / 4 * 6))),          # 재반등 몸통% 2~6%→0~6
        "re_count": min(6, max(0, (len(rbars) - 1) * 3)),                     # 자격 봉 1→0·2→3·3+→6
        "flow": round(min(8, max(0, ivtr_eok / 500 * 8))),                    # 투신 매집 ~500억→8 (UN/J≈1.0 실측 → 불변)
        "explosion": round(min(6, max(0, (peak_eok - 1500) / 13500 * 6))),    # 폭발 규모 1,500억~1.5조→0~6 (UN기준 ×1.5)
    }
    score = min(95, REACCUM_SCORE + breakdown["re_value"] + breakdown["re_body"]
                + breakdown["re_count"] + breakdown["flow"] + breakdown["explosion"])
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
        "fade_pct": round(fade_pct, 1),
        "value_eok": round(float(now.get("value") or 0) / 1e8),
        "ma10": round(ma10, 1),
        "ma10_margin_pct": round(ma10_margin, 2),
        "spark": {"clusters": []},
        "spark_max_x": 0.0,
        "spark_max_pct": None,
        "mega_flow": False,
        # 재반등(오늘) 신호 — 10분봉 몸통% + 그 10분봉 거래대금(억) + 시각
        "reignition": {
            "body_pct": reignition["body_pct"],
            "time": reignition["time"],
            "value_10m_eok": reignition["value_eok"],
        },
        # 당일 자격 10분봉 전체 — 텔레그램 봉단위 알림용(표시는 reignition 대표봉만 사용)
        "reignition_bars": [{"time": b["time"], "body_pct": b["body_pct"], "value_eok": b["value_eok"]}
                            for b in rbars],
        "flow": acc,
        "news": news_items,
        "matched_events": matched_events,
        "theme": theme,
        "visible_experimental": True,
        "reaccum": {
            "peak_date": peak_date,
            "peak_value_eok": int(float(rec.get("peak_value_eok") or 0)),
            "peak_high_pct": round(float(rec.get("peak_high_pct") or 0), 2),
            "ma20": round(ma20, 1),
            "ma20_margin_pct": round(ma20_margin, 2),
            # 투신 매집 (조건=순매수>0 느슨, 일수·금액은 정보 표시용)
            "ivtr_net": int(ivtr_net),
            "ivtr_days": ivtr_days,
            "ivtr_eok": ivtr_eok,
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
    if not p.reaccum_enabled or not p.reaccum_visible or not active_explosions:
        return 0, 0, 0
    try:  # 대장 판정 실패가 게시 자체를 막지 않게 격리(표시 전용 뱃지) — 모듈 설계 계약
        leader_codes = _theme_leader_codes(active_explosions)
    except Exception as e:
        log(f"  [warn] 예전 대장 판정 실패(뱃지 생략): {e}")
        leader_codes = set()
    cohort_prob = _leader_cohort_prob()  # 대장 코호트 실측률(표본 충분할 때만, 표시 전용)
    by_code = {s["code"]: s for s in suspects}
    added = badges = err_count = 0
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
        if code in by_code:
            by_code[code]["reaccum_badge"] = True
            by_code[code]["reaccum"] = r["reaccum"]
            by_code[code]["leader_cohort_prob"] = r["leader_cohort_prob"]
            badges += 1
        else:
            suspects.append(r)
            added += 1
    return added, badges, err_count


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



def _parse_hhmmss(v):
    s = str(v or "").replace(":", "").strip()
    if len(s) == 4:
        s += "00"
    if len(s) != 6 or not s.isdigit():
        raise ValueError(f"invalid HHMMSS value: {v}")
    return s


def _kimi_in_auto_window(now=None, start="144500", end="153000"):
    now = now or datetime.now(KST)
    hm = now.strftime("%H%M%S")
    return _parse_hhmmss(start) <= hm <= _parse_hhmmss(end)


def apply_kimi_verification(suspects, mode="auto", max_items=5, timeout=60,
                            window_start="144500", window_end="153000"):
    """상위 후보에 Kimi 검증을 붙인다. 실패해도 후보 게시는 유지한다."""
    if mode == "auto" and not _kimi_in_auto_window(start=window_start, end=window_end):
        for s in suspects:
            if s.get("pattern") == "deep_shakeout":
                s["ai_verdict"] = {"status": "outside_window",
                                   "window": [window_start, window_end]}
        return
    if not kimi_client.enabled(mode):
        status = "disabled" if mode == "off" or os.environ.get("RADAR_KIMI_VERIFY") == "0" else "not_configured"
        for s in suspects:
            if s.get("pattern") == "deep_shakeout":
                s["ai_verdict"] = {"status": status}
        return
    eligible = [s for s in suspects if not s.get("visible_experimental")]
    targets = [s for s in eligible if s.get("pattern") == "deep_shakeout"]
    targets += [s for s in eligible if s.get("pattern") != "deep_shakeout"]
    seen, unique = set(), []
    for s in targets:
        if s["code"] in seen:
            continue
        seen.add(s["code"])
        unique.append(s)
        if len(unique) >= max_items:
            break
    for s in unique:
        try:
            verdict = kimi_client.verify_candidate(s, timeout=timeout)
            s["ai_verdict"] = verdict
            if verdict["verdict"] == "CONFIRM":
                s["suspicion_score"] = min(100, s["suspicion_score"] + 5)
                s["score_breakdown"]["ai"] = 5
            elif verdict["verdict"] == "REJECT":
                s["suspicion_score"] = max(0, s["suspicion_score"] - 10)
                s["score_breakdown"]["ai"] = -10
        except Exception as e:
            s["ai_verdict"] = {"status": "unavailable", "error": str(e)[:120]}


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
        description="이벤트 매집 레이더 — 과거 폭등 → 식음 → 오늘 재반등 탐지")
    # 재반등(오늘) 트리거 — 과거 폭등 종목이 식었다가 오늘 거래대금 동반 재상승 초입인지
    ap.add_argument("--reaccum-change-min", "--reaccum-high-min",
                    dest="reaccum_change_min", type=float, default=REACCUM_CHANGE_MIN,
                    help="당일 종가/현재 등락률 하한(%%, 기본 -4). --reaccum-high-min은 하위호환 별칭")
    ap.add_argument("--reaccum-change-max", "--reaccum-high-max",
                    dest="reaccum_change_max", type=float, default=REACCUM_CHANGE_MAX,
                    help="당일 종가/현재 등락률 상한(%%, 기본 +10). --reaccum-high-max는 하위호환 별칭")
    ap.add_argument("--reignition-body-pct", type=float, default=REIGNITION_BODY_PCT,
                    help="10분봉 몸통%% 하한 — 당일 한 번이라도(기본 2)")
    ap.add_argument("--reignition-value-10m", type=float, default=REIGNITION_VALUE_10M,
                    help="해당 10분봉 1개의 거래대금 하한(원, 기본 30억)")
    ap.add_argument("--kimi-mode", choices=("auto", "on", "off"), default="auto",
                    help="Kimi 상위 후보 검증 모드(auto=키 있으면 자동)")
    ap.add_argument("--kimi-max", type=int, default=5,
                    help="Kimi 검증 최대 후보 수")
    ap.add_argument("--kimi-timeout", type=int, default=60,
                    help="Kimi 후보별 타임아웃(초)")
    ap.add_argument("--kimi-window-start", default="144500",
                    help="Kimi auto 검증 시작 시각(HHMMSS, 기본 14:45)")
    ap.add_argument("--kimi-window-end", default="153000",
                    help="Kimi auto 검증 종료 시각(HHMMSS, 기본 15:30)")
    ap.add_argument("--names", nargs="*", default=[], help="watchlist 강제 포함")
    ap.add_argument("--no-reaccum", dest="reaccum_enabled", action="store_false",
                    help="재매집(reaccum) registry 감시와 후보 생성을 비활성화")
    ap.set_defaults(reaccum_enabled=True)
    ap.add_argument("--no-reaccum-visible", dest="reaccum_visible", action="store_false",
                    help="재매집 후보를 registry에는 기록하되 suspects 화면 노출은 비활성화")
    ap.set_defaults(reaccum_visible=True)
    ap.add_argument("--reaccum-max", type=int, default=12,
                    help="게시 단계에서 예약할 재매집 후보 슬롯 수(파라미터 기록용)")
    ap.add_argument("--explosion-value", type=float, default=EXPLOSION_VALUE,
                    help="재매집 폭발 거래대금 하한(원, 기본 1,500억=UN 통합기준)")
    ap.add_argument("--explosion-high-pct", type=float, default=EXPLOSION_HIGH_PCT,
                    help="재매집 폭발 당일 고가 등락률 하한(%%)")
    ap.add_argument("--explosion-window", type=int, default=EXPLOSION_WINDOW,
                    help="재매집 폭발 유효 거래일 수")
    ap.add_argument("--explosion-rank-n", type=int, default=EXPLOSION_RANK_N,
                    help="시장별 거래대금 상위 N종목에서 라이브 폭발 감시")
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
    p.explosion_rank_n = max(1, int(p.explosion_rank_n))
    p.reaccum_max = max(0, int(p.reaccum_max))
    active_explosions, live_scan_ok = prepare_reaccum_registry(p)

    # 조건 1: D-10 이벤트 캘린더 (재반등 후보의 이벤트 민감도 표시용)
    events = upcoming_events(10)
    log(f"[radar] D-10 이벤트 {len(events)}건")

    # 유일 산출물 = 재매집 후보 (과거 폭등 → 식음 → 오늘 재반등)
    suspects = []
    reaccum_added, reaccum_badges, err_count = attach_reaccum_candidates(
        suspects, active_explosions, p, events)
    total = len(active_explosions)
    log(f"[radar] reaccum 후보 {reaccum_added}건 (폭발감시 {total}종목, 조회실패 {err_count}, live_ok={live_scan_ok})")
    # 데이터 수집 장애 가드 — KIS 토큰/키 장애 시 거짓 '빈 레이더' 게시 방지(구 유니버스 exit(2) 대체).
    #  (a) 폭발감시(KIS 랭킹)가 전면 실패 + 후보도 0건 → 시장 도달 확인 불가(빈 레지스트리 포함).
    #  (b) 후보가 있는데 절반 이상 조회 실패 → KIS 장애로 반쪽 게시 방지(소형 레지스트리도 floor=2로 포착).
    # 빈 레지스트리·조건 미달로 인한 0건은 정상('레이더 깨끗') — live_scan_ok=True면 종료하지 않는다.
    collection_dead = (not live_scan_ok) and reaccum_added == 0
    high_fail = total > 0 and err_count >= max(2, (total + 1) // 2)
    if collection_dead or high_fail:
        log(f"[error] 데이터 수집 장애 의심(live_ok={live_scan_ok}, 실패 {err_count}/{total}, 후보 {reaccum_added}) — 게시 중단")
        sys.exit(3)
    suspects.sort(key=lambda x: -x["suspicion_score"])
    apply_kimi_verification(suspects, p.kimi_mode, p.kimi_max, p.kimi_timeout,
                            p.kimi_window_start, p.kimi_window_end)
    suspects.sort(key=lambda x: -x["suspicion_score"])

    out = {
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "params": {"market": kis.MONEY_MARKET,  # 거래대금/수급 시장구분: UN=KRX+NXT 통합 / J=KRX 단독(가격은 항상 J)
                   "reaccum_change_range": [p.reaccum_change_min, p.reaccum_change_max],
                   "reignition_body_pct": p.reignition_body_pct,
                   "reignition_value_10m_eok": round(p.reignition_value_10m / 1e8),
                   "kimi_mode": p.kimi_mode,
                   "kimi_max": p.kimi_max,
                   "kimi_window": [p.kimi_window_start, p.kimi_window_end],
                   "reaccum_enabled": p.reaccum_enabled,
                   "reaccum_visible": p.reaccum_visible,
                   "reaccum_max": p.reaccum_max,
                   "explosion_value_eok": round(p.explosion_value / 1e8),
                   "explosion_high_pct": p.explosion_high_pct,
                   "explosion_window": p.explosion_window,
                   "explosion_rank_n": p.explosion_rank_n,
                   "telegram_seed": p.telegram_seed,
                   "telegram_channel": p.telegram_channel if p.telegram_seed else None},
        "universe_count": len(active_explosions),
        "events": events,
        "suspects": suspects,
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
