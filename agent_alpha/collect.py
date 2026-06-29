"""EOD 전진수집 — 오늘 movers의 정량 행(+있으면 당일 LLM 판단) → agent_alpha/data/forward/{date}.json.
당일 분봉이 필요해 **마감(15:30) 후** 실행. forward/{date}.json이 정본(코드 키, 덮어쓰기 안전).
forward_samples.jsonl 평탄화는 label.py가 담당. --dry-run = 미기록 미리보기.
"""
import json
import os
import sys
import config
import float_ratio
import movers as movers_mod
import regime as regime_mod
import quant as quant_mod


def _load_judgments(date):
    try:
        return json.load(open(os.path.join(config.JUDGMENTS_DIR, f"{date}.json"), encoding="utf-8"))
    except Exception:
        return {}


def _load_fcache():
    try:
        return json.load(open(config.FLOAT_CACHE, encoding="utf-8"))
    except Exception:
        try:                      # 코어 캐시를 '읽기 시드'로(있으면). 쓰기는 우리 파일에만.
            return dict(float_ratio._shared_cache())
        except Exception:
            return {}


def _save_fcache(c):
    try:
        config.ensure_dirs()
        tmp = config.FLOAT_CACHE + ".tmp"
        json.dump(c, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
        os.replace(tmp, config.FLOAT_CACHE)
    except Exception:
        pass


def run(dry=False, max_movers=None):
    config.ensure_dirs()
    date = config.today_yyyymmdd()
    # 장중(정규장 마감 15:30 전) 수집이면 '잠정' — close·종가강도·회전율·스파크가 미확정 장중값.
    # 회장님 15:30 종가베팅 전 표시(15:15 수집)용. 15:40 마감후 확정 수집이 같은 sig_date 파일을 덮어써 정본.
    # label.py는 provisional 행을 라벨 보류(잠정 종가로 익일검증 오염 방지).
    from datetime import datetime
    provisional = datetime.now(config.KST).strftime("%H%M") < "1530"
    mv = movers_mod.movers()[: (max_movers or config.MAX_MOVERS)]
    if not mv:
        print(f"[alpha-collect] {date} movers 0 — 스킵")
        return []
    reg = regime_mod.regime()
    fcache = _load_fcache()
    judg = _load_judgments(date)
    rows = []
    for m in mv:
        r = quant_mod.build(m, fcache, reg)
        r["provisional"] = provisional
        j = judg.get(m["code"])
        if j:
            for k in ("catalyst", "real_likelihood", "sustainability", "manipulation_risk",
                      "prob_up", "confidence", "redteam_flag", "evidence", "llm_model"):
                r[k] = j.get(k)
        rows.append(r)
        print(f"  {m['code']} {r.get('name')}: 회전2d {r.get('turnover_2d_pct')}% · "
              f"스파크{r.get('spark_1430_count')}({r.get('spark_source')}) · 종가강도{r.get('close_strength')} · "
              f"외인+기관 {(r.get('frgn_net') or 0) + (r.get('orgn_net') or 0) if r.get('frgn_net') is not None else 'n/a'} · "
              f"키움{r.get('kiwoom_buy_concentration')} · {'음봉' if r.get('is_eumbong') else '양봉'}")
    _save_fcache(fcache)
    rows = [r for r in rows if r.get("data_ok")]   # 일봉<2 퇴화행 제외(신호일 어긋남·degenerate 방지)
    if not rows:
        print("[alpha-collect] 유효 행 0(일봉 결측) — 스킵")
        return []
    if dry:
        print(f"[alpha-collect] DRY {len(rows)}행(미기록)")
        return rows
    # 신호일 = 다수결(대부분 동일한 마지막 거래일). 첫 행이 과거일이어도 과거 파일 덮어쓰기 방지.
    from collections import Counter
    sig_date = Counter(r["date"] for r in rows).most_common(1)[0][0]
    daymap = {r["code"]: r for r in rows}
    tmp = os.path.join(config.FORWARD_DIR, f"{sig_date}.json.tmp")
    json.dump({"date": sig_date, "rows": daymap}, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(config.FORWARD_DIR, f"{sig_date}.json"))
    print(f"[alpha-collect] {sig_date} {len(rows)}행 기록 → {config.FORWARD_DIR}")
    return rows


if __name__ == "__main__":
    run(dry="--dry-run" in sys.argv)
