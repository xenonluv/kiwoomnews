#!/usr/bin/env bash
# 프로젝트 cron 일괄 설치 (idempotent). 프로덕션 머신(Mac Studio 등)에서 실행.
#   bash scripts/install_cron.sh            # 설치/갱신
#   bash scripts/install_cron.sh --dry-run  # 설치 안 하고 적용될 라인만 출력
#
# git pull 후 재실행하면 최신 스케줄로 안전하게 갱신(기존 프로젝트 라인 제거 후 재설치).
# 평일 KST 기준. 시간대가 KST가 아니면 시(hour) 필드를 환경에 맞게 조정하세요.
set -euo pipefail

DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="$(command -v python3 || echo /usr/bin/python3)"

# ── 설정(필요 시 수정) ─────────────────────────────
TOP=30; BET=5
# ───────────────────────────────────────────────

# 레이더 publish는 인자 불필요(전수 스캔이라 watchlist 없어도 누락 없음).
# 두 푸셔(publish·analyzer)를 7분 시차로 분리 → 동시 git push 충돌 방지
L_PUBLISH="0,15,30,45 9-15 * * 1-5 cd $REPO && $PY scripts/publish.py >> /tmp/publish.log 2>&1"
L_FORECAST="7,22,37,52 9-15 * * 1-5 cd $REPO && $PY analyzer/run.py --push --top $TOP --bet $BET >> /tmp/forecast.log 2>&1"
L_BACKTEST="10 17 * * 1-5 cd $REPO && $PY analyzer/backtest.py --push >> /tmp/backtest.log 2>&1"
# 레이더 자가 검증(익일 적중 채점 + 가중치 튜닝 + /performance 데이터) — backtest와 10분 시차
L_RADAR_BT="20 17 * * 1-5 cd $REPO && $PY scripts/radar_backtest.py --push >> /tmp/radar_backtest.log 2>&1"
# 추적 종목(검색 후 📌 추적) 일일 검증 — 종합판정(룰) vs Kimi(AI), radar_backtest와 10분 시차
L_TRACK_EVAL="30 17 * * 1-5 cd $REPO && $PY scripts/track_eval.py --push >> /tmp/track_eval.log 2>&1"

NEW_CRON="$(
  crontab -l 2>/dev/null | grep -v -E "scripts/publish.py|scripts/radar_backtest.py|scripts/track_eval.py|analyzer/run.py|analyzer/backtest.py|^PATH=/usr/local/bin:/usr/bin:/bin$" || true
  echo "PATH=/usr/local/bin:/usr/bin:/bin"
  echo "$L_PUBLISH"
  echo "$L_FORECAST"
  echo "$L_BACKTEST"
  echo "$L_RADAR_BT"
  echo "$L_TRACK_EVAL"
)"

if [ "$DRY" = "1" ]; then
  echo "[DRY-RUN] 설치될 crontab (repo=$REPO, python=$PY):"
  echo "----------------------------------------"
  echo "$NEW_CRON"
  echo "----------------------------------------"
  echo "(실제 설치하려면 --dry-run 없이 다시 실행)"
  exit 0
fi

# 백업 후 설치
crontab -l > "/tmp/crontab.backup.$(date +%Y%m%d%H%M%S)" 2>/dev/null || true
echo "$NEW_CRON" | crontab -

echo "✅ cron 설치 완료 (repo=$REPO, python=$PY)"
echo "── 설치된 프로젝트 cron ──"
crontab -l | grep -E "publish.py|radar_backtest.py|analyzer/" || true

echo
echo "── 점검(중요) ──"
pgrep -x cron >/dev/null 2>&1 && echo "  ✅ cron 데몬 실행 중" \
  || echo "  ⚠️  cron 미실행 → (Linux) sudo service cron start / (Mac) 시스템설정>개인정보보호>전체디스크접근에 cron 허용"
case "$(date +%Z)" in
  KST|JST) echo "  ✅ 시간대 $(date +%Z) (KST/동일오프셋)";;
  *) echo "  ⚠️  시간대 $(date +%Z) — KST 아님! Mac: sudo systemsetup -settimezone Asia/Seoul (또는 cron 시(hour) 조정)";;
esac
echo "  ⚠️  PC가 켜져 있어야 동작 — Mac: sudo pmset -a sleep 0"
echo "  ℹ️  로그: tail -f /tmp/publish.log /tmp/forecast.log /tmp/backtest.log"
