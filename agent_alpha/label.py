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
    """signal_date '바로 다음' 완성 거래일 일봉(J 공식) | None. 정지봉(close==0) 제외.
    ⚠ 장 마감(15:30) 후면 '오늘 일봉'도 완성이므로 익일결과로 포함 — 신호 익일 결과를 그날 마감 후 16시에
    바로 라벨(다음날 아침까지 안 기다림, 회장님 지시 2026-06-29). 장중이면 오늘봉 미완성이라 제외.
    ⚠ 윈도(최근 40거래일)가 신호일을 못 덮으면 after[0]가 진짜 익일봉이 아니므로 None(aged 행 far-future 오라벨 방지)."""
    now = datetime.now(config.KST)
    today = now.strftime("%Y%m%d")
    # 종가 확정은 동시호가(15:20~15:30) 정산 후 보통 15:40~16:00에 KIS 일봉에 반영 → '1600'부터 today봉 채택.
    # (마감 직후 15:30~16:00 수동 실행 시 미확정 종가로 오라벨되는 창을 차단. cron 16:00은 게이트 통과·정산 후 안전.)
    inc_today = now.strftime("%H%M") >= "1600"
    try:
        d = kis.daily_prices(code, days=40, market="J")
    except Exception:
        return None
    dates = [x["date"] for x in d if x.get("date")]
    if not dates or min(dates) > signal_date:
        return None  # 윈도가 신호일 이전까지 못 내려감 → 익일봉 보장 불가 → 보류(run에서 만료 처리)
    after = [x for x in d if signal_date < x["date"] and (x["date"] < today or (inc_today and x["date"] == today))
             and (x.get("close") or 0) > 0]
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
                # 이 루프는 date<today 파일만(위 45행 가드) — 잠정 행이 여기 왔다 = 마감일 당일 15:40 확정
                # 수집이 그 파일을 못 덮어씀(KIS 장애 등 실패) = stale. 잠정(장중 미확정) 종가로 익일라벨하면
                # 오염되므로 라벨하지 않되, 만료 처리해 영구 고착·웹 '🕒 장중 잠정' stale 배지를 끊는다.
                r.update({"labeled": True, "hit": None, "provisional": False,
                          "label_basis": "expired_provisional_unconfirmed"})
                dirty = True
                continue
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
            # today봉으로 라벨한 경우(마감 후 16:00 즉시) 추적용 basis 구분 — 정산 직후라 향후 재검증 식별 가능.
            basis = "kis_daily_J_sameday" if nb["date"] == today else "kis_daily_J"
            r.update({"labeled": True, "next_date": nb["date"], "next_close": nb["close"],
                      "next_open": nb.get("open"),
                      "next_high_pct": round((nb["high"] / sig - 1) * 100, 2) if nb.get("high") else None,
                      "hit": nb["close"] > sig,
                      "next_return_pct": round((nb["close"] / sig - 1) * 100, 2),
                      "label_basis": basis})
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
