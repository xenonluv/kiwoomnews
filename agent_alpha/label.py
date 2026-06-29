"""익일 라벨 — forward/{date}.json(date<오늘)의 미라벨 행에 익일 종가/등락/hit 채움.
그 후 forward_samples.jsonl(평탄 플랫)을 전체 재생성. radar_backtest 식: hit=익일종가>신호종가, 정지봉 close==0 스킵.
"""
import json
import os
import glob
from datetime import datetime
import config
import kis_client as kis


def _age_days(signal_date):
    try:
        return (datetime.now(config.KST)
                - datetime.strptime(signal_date, "%Y%m%d").replace(tzinfo=config.KST)).days
    except Exception:
        return 0


def _next_bar(code, signal_date):
    """signal_date '바로 다음' 완성 거래일 일봉(J 공식) | None. 오늘(미완성)·정지봉(close==0) 제외.
    ⚠ 윈도(최근 40거래일)가 신호일을 못 덮으면(너무 오래된 행) after[0]가 진짜 익일봉이 아니므로 None 반환 —
    그래야 신호일이 윈도 안일 때 첫 '신호일<봉<오늘' 봉이 곧 T+1임이 보장된다(aged 행 far-future 오라벨 방지)."""
    today = config.today_yyyymmdd()
    try:
        d = kis.daily_prices(code, days=40, market="J")
    except Exception:
        return None
    dates = [x["date"] for x in d if x.get("date")]
    if not dates or min(dates) > signal_date:
        return None  # 윈도가 신호일 이전까지 못 내려감 → 익일봉 보장 불가 → 보류(run에서 만료 처리)
    after = [x for x in d if signal_date < x["date"] < today and (x.get("close") or 0) > 0]
    return after[0] if after else None


def run():
    config.ensure_dirs()
    today = config.today_yyyymmdd()
    changed = 0
    for fp in sorted(glob.glob(os.path.join(config.FORWARD_DIR, "*.json"))):
        try:
            day = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        if day.get("date", "") >= today:
            continue  # 오늘분은 익일봉 아직 없음
        rows = day.get("rows", {})
        dirty = False
        for code, r in rows.items():
            if r.get("labeled"):
                continue
            if r.get("provisional"):
                continue  # 장중 잠정 수집(close 미확정) — 마감후 확정 수집이 덮어쓸 때까지 라벨 보류(검증 오염 방지)
            sig = r.get("close")
            if not sig:
                r["labeled"] = True
                r["label_basis"] = "no_signal_close"
                dirty = True
                continue
            nb = _next_bar(code, r["date"])
            if not nb:
                if _age_days(r.get("date", "")) > 30:   # 30일+ 미라벨(장기 정지 등) → 만료(hit=None → calibrate 제외·무한재시도 중단)
                    r.update({"labeled": True, "hit": None, "label_basis": "expired_unlabeled"})
                    dirty = True
                continue  # 익일봉 아직(주말/공휴일) — 다음 실행에 재시도
            r.update({"labeled": True, "next_date": nb["date"], "next_close": nb["close"],
                      "next_open": nb.get("open"),
                      "next_high_pct": round((nb["high"] / sig - 1) * 100, 2) if nb.get("high") else None,
                      "hit": nb["close"] > sig,
                      "next_return_pct": round((nb["close"] / sig - 1) * 100, 2),
                      "label_basis": "kis_daily_J"})
            dirty = True
            changed += 1
        if dirty:
            tmp = fp + ".tmp"
            json.dump(day, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            os.replace(tmp, fp)

    # forward_samples.jsonl 전체 재생성(평탄)
    allrows = []
    for fp in sorted(glob.glob(os.path.join(config.FORWARD_DIR, "*.json"))):
        try:
            day = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        allrows.extend(day.get("rows", {}).values())
    tmp = config.FORWARD_JSONL + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in allrows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, config.FORWARD_JSONL)
    print(f"[alpha-label] 라벨 {changed}행 갱신 · 총 {len(allrows)}행 평탄화 → {config.FORWARD_JSONL}")
    return changed


if __name__ == "__main__":
    run()
