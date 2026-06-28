"""agent_alpha 공용 설정 — 경로 부트스트랩 + 임계.

이 모듈을 import 하면 sys.path에 agent_alpha/·/sources·/agents·repo/scripts 가 추가돼
core(kis_client·float_ratio)를 **읽기전용**으로 import 할 수 있다.
삭제안전: core는 agent_alpha를 모른다(단방향). 이 모듈은 어떤 core 상태도 쓰지 않는다.
"""
import os
import sys
from datetime import datetime, timezone, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))   # repo/agent_alpha
REPO = os.path.dirname(_HERE)                          # repo
SCRIPTS = os.path.join(REPO, "scripts")
for _p in (_HERE, os.path.join(_HERE, "sources"), os.path.join(_HERE, "agents"), SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

KST = timezone(timedelta(hours=9))

# ── 경로 (쓰기는 전부 agent_alpha/data/ 안. RADAR_JSON은 읽기, ALPHA_JSON은 publish_alpha만) ──
DATA = os.path.join(_HERE, "data")
FORWARD_DIR = os.path.join(DATA, "forward")
FORWARD_JSONL = os.path.join(DATA, "forward_samples.jsonl")
JUDGMENTS_DIR = os.path.join(DATA, "judgments")
CALIBRATION = os.path.join(DATA, "calibration.json")
SNAPSHOTS_DIR = os.path.join(DATA, "snapshots")
FLOAT_CACHE = os.path.join(DATA, "float_cache.json")   # 코어 data/float_ratio.json 미오염용 자체 캐시
NOTIFIED = os.path.join(DATA, ".alpha_notified.json")
RADAR_JSON = os.path.join(REPO, "web", "data", "radar.json")   # READ ONLY
ALPHA_JSON = os.path.join(REPO, "web", "data", "alpha.json")   # publish_alpha만 씀

# ── 임계 ──
MAX_MOVERS = 30
SPARK_BODY_PCT = 1.5            # 5분 양봉 몸통%(=종가-시가/시가) 하한 (2026-06-28 회장님 매매신호 정합: 2.0→1.5)
SPARK_SPAN_MIN = 5
SPARK_START_HHMM = "14:30"      # 14:30↑ 5분 양봉 스파크
SPARK_MIN = 2
TURNOVER_2D_BANDS = [(0, 100), (100, 200), (200, 400), (400, 1e9)]
CLOSE_STRENGTH_BANDS = [(0.0, 0.3), (0.3, 0.6), (0.6, 1.01)]
CALIB_MIN_N = 20               # 셀 표본 < 이 값이면 "관찰중"

LLM_MODEL = os.environ.get("AGENT_ALPHA_MODEL", "kimi")


def today_yyyymmdd():
    return datetime.now(KST).strftime("%Y%m%d")


def now_iso():
    return datetime.now(KST).strftime("%Y-%m-%dT%H:%M:%S+09:00")


def ensure_dirs():
    for d in (DATA, FORWARD_DIR, JUDGMENTS_DIR, SNAPSHOTS_DIR):
        os.makedirs(d, exist_ok=True)
