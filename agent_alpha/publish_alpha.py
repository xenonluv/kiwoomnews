"""웹 게시 — 최근 forward(라벨된 익일결과 포함) + calibration → web/data/alpha.json, 변경 시에만 git push.
agent_alpha가 web/에 쓰는 유일 지점. flock으로 코어 publish와 git 직렬화. --dry-run = 미기록.
"""
import json
import os
import sys
import glob
import subprocess
import fcntl
import config

GIT_LOCK = "/tmp/stocknews_git.lock"   # 코어 publish.py와 공유(직렬화)
RECENT_DAYS = 3                         # 라벨된 익일결과가 보이도록 최근 N개 forward 파일 합침

_MOVER_FIELDS = ("code", "name", "sector", "mover_type", "date", "file_date", "provisional", "change_pct", "is_eumbong", "below_prev",
                 "turnover_pct", "turnover_2d_pct", "close_strength", "upper_wick_pct", "lower_wick_pct",
                 "spark_1430_count", "spark_source", "frgn_net", "orgn_net", "prsn_net",
                 "kiwoom_buy_concentration", "kiwoom_is_top_buyer", "glob_net_qty",
                 "hidden_foreign_level", "combined_score",
                 "kospi_chg", "kosdaq_chg", "catalyst", "real_likelihood", "sustainability",
                 "manipulation_risk", "prob_up", "confidence", "redteam_flag",
                 "labeled", "hit", "next_return_pct", "next_high_pct", "next_date")


def _recent_forward(n=RECENT_DAYS, cap=120):
    """최근 n개 forward 파일의 행을 '전부' 합침(코드 디둡 안 함) — 같은 코드의 오늘 미라벨 + 어제 라벨(익일결과)
    행이 각각 날짜와 함께 보이게(디둡하면 최신 미라벨이 어제 라벨 결과를 가려 목적 위배). 날짜·회전율 내림차순, cap개.
    cap은 런어웨이 가드일 뿐 — n(3)일 × MAX_MOVERS(30)=90을 덮도록 120(꽉 찬 주에 오래된 날의 라벨행이 잘려
    '어제 익일결과 노출' 목적이 깨지지 않게). 실제 reaccum 종목은 보통 수십 미만이라 평시엔 무영향."""
    files = sorted(glob.glob(os.path.join(config.FORWARD_DIR, "*.json")))[-n:]
    if not files:
        return None, []
    rows = []
    latest = None
    for fp in files:
        try:
            day = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        # 파일 sig_date(파일명) = 행의 유니크 출처축. 행 내부 date는 정지종목 등에서 stale 고정될 수 있어
        # (code,date)만으론 두 파일 행이 충돌 → 웹 React key 충돌·카드 무음 누락. file_date로 갈라줌.
        fdate = os.path.splitext(os.path.basename(fp))[0]
        latest = day.get("date") or latest
        for r in (day.get("rows") or {}).values():
            r = dict(r)
            r["file_date"] = fdate
            rows.append(r)
    rows.sort(key=lambda r: ((r.get("date") or ""), (r.get("turnover_2d_pct") or 0)), reverse=True)
    return latest, rows[:cap]


def build_alpha():
    date, rows = _recent_forward()
    movers = [{k: r.get(k) for k in _MOVER_FIELDS} for r in rows]
    try:
        calib = json.load(open(config.CALIBRATION, encoding="utf-8"))
    except Exception:
        calib = None
    return {
        "generated_at": config.now_iso(),
        "date": date,
        "movers": movers,
        "calibration": calib,
        "disclaimer": ("측정·실험용 — 매수 추천이 아닙니다. 스파크/거래원은 약신호(창구≠주체)이며, "
                       "전진검증 표본이 충분(min_n)할 때까지 calibration은 '관찰중'입니다."),
    }


def _cmp_key(data):
    """변경 판정용 — 매번 바뀌는 타임스탬프(top-level + 중첩 calibration.generated_at) 제외."""
    calib = data.get("calibration")
    if isinstance(calib, dict):
        calib = {k: v for k, v in calib.items() if k != "generated_at"}
    return json.dumps({"movers": data.get("movers"), "calibration": calib}, ensure_ascii=False, sort_keys=True)


def _committed_key():
    """git HEAD에 커밋된 alpha.json의 변경키(내용 기준). 미존재/실패 시 None."""
    try:
        out = subprocess.run(["git", "show", "HEAD:web/data/alpha.json"],
                             cwd=config.REPO, capture_output=True, text=True)
        if out.returncode != 0 or not out.stdout.strip():
            return None
        return _cmp_key(json.loads(out.stdout))
    except Exception:
        return None


def _ahead_of_origin():
    """origin/main보다 앞선 로컬 커밋이 있나(이전 회차 push 실패로 묶인 커밋 재시도용)."""
    try:
        out = subprocess.run(["git", "rev-list", "--count", "origin/main..HEAD"],
                             cwd=config.REPO, capture_output=True, text=True)
        return out.returncode == 0 and int((out.stdout or "0").strip() or "0") > 0
    except Exception:
        return False


def run(dry=False):
    data = build_alpha()
    if dry:
        print(f"[alpha-publish] DRY movers={len(data['movers'])} date={data['date']} (미기록)")
        print(json.dumps(data, ensure_ascii=False, indent=1)[:600])
        return
    new_key = _cmp_key(data)
    # 변경 판정은 'on-disk'가 아니라 'git HEAD 커밋본' 기준 — 이전 회차가 썼지만 commit/push 못 한 변경이
    # 묶여도(on-disk만 갱신) 다음 회차가 재커밋/재푸시하도록(상태 드리프트 방지). 내용 동일하면 파일도 안 씀(청결).
    need_commit = (new_key != _committed_key())
    with open(GIT_LOCK, "w") as lk:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX)
        except Exception:
            pass
        if need_commit:
            os.makedirs(os.path.dirname(config.ALPHA_JSON), exist_ok=True)
            tmp = config.ALPHA_JSON + ".tmp"
            open(tmp, "w", encoding="utf-8").write(json.dumps(data, ensure_ascii=False, indent=1))
            os.replace(tmp, config.ALPHA_JSON)
            try:
                subprocess.run(["git", "add", "web/data/alpha.json"], cwd=config.REPO, check=True)
                # pathspec 한정 commit — 인덱스에 남은 코어/타 푸셔의 staged 엔트리를 alpha 커밋에 섞지 않음(격리).
                subprocess.run(["git", "commit", "-m", f"data(alpha): {data['date']} movers {len(data['movers'])}",
                                "--", "web/data/alpha.json"], cwd=config.REPO, check=True)
            except subprocess.CalledProcessError as e:
                # commit 실패(신원 미설정·훅 거부 등) → staged 해제하고 push 진행 금지(거짓 '성공' 로그·staged 누수 방지)
                print(f"[alpha-publish] commit 실패 — 스테이징 해제·push 생략(다음 회차 재시도): {e}")
                subprocess.run(["git", "reset", "-q", "HEAD", "--", "web/data/alpha.json"], cwd=config.REPO, check=False)
                return
        if not (need_commit or _ahead_of_origin()):
            print("[alpha-publish] 변경 없음·이미 동기화 — push 생략")
            return
        for _ in range(2):
            pl = subprocess.run(["git", "pull", "--rebase", "--autostash", "origin", "main"], cwd=config.REPO)
            if pl.returncode != 0:   # rebase 충돌 → 진행중 상태 정리하고 다음 회차 재시도(코어 publish.py와 동일)
                subprocess.run(["git", "rebase", "--abort"], cwd=config.REPO, check=False)
                print("[alpha-publish] pull --rebase 충돌 — abort·다음 회차 재시도")
                return
            if subprocess.run(["git", "push", "origin", "main"], cwd=config.REPO).returncode == 0:
                print(f"[alpha-publish] alpha.json push (movers {len(data['movers'])})")
                return
        print("[alpha-publish] push 실패(2회) — 다음 회차 재시도")


if __name__ == "__main__":
    run(dry="--dry-run" in sys.argv)
