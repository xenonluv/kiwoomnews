"""web/data/radar.json → 오늘 movers (READ ONLY). 코어 출력만 읽음."""
import json
import config


def movers():
    """[{code, name, sector, mover_type}] — explosions/youtong/suspects 합집합(코드 디둡)."""
    try:
        d = json.load(open(config.RADAR_JSON, encoding="utf-8"))
    except Exception:
        return []
    out = []
    seen = set()
    # 💥 흔들기 코드 선수집 — 같은 코드가 explosions/youtong에 먼저 잡혀도(섹션 선점 디둡)
    # 유형은 shakeout으로 라벨(리뷰 2026-07-04: 선점에 가려 by_mover_type shakeout 축이 굶는 문제).
    shk = {s.get("code") for s in (d.get("suspects") or []) if s.get("pattern") == "shakeout"}
    for sec, typ in (("explosions", "explosion"), ("youtong", "youtong"), ("suspects", "reaccum")):
        for s in d.get(sec) or []:
            c = s.get("code")
            if not c or c in seen:
                continue
            seen.add(c)
            mt = "shakeout" if c in shk else typ
            out.append({"code": c, "name": s.get("name") or c,
                        "sector": s.get("sector", ""), "mover_type": mt})
    return out
