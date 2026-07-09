#!/usr/bin/env bash
# Mac 프로덕션 머신 자동 실행 정비 스크립트.
# - 최신 main 동기화
# - macOS 시간대 Asia/Seoul 보정
# - 프로젝트 cron 재설치
# 사용: bash scripts/setup_mac_autorun.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

echo "== kiwoomnews Mac autorun setup =="
echo "repo: $REPO"

if ! command -v git >/dev/null 2>&1; then
  echo "ERROR: git command not found" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 command not found" >&2
  exit 1
fi

echo
echo "== sync main =="
git fetch origin main
git merge --ff-only FETCH_HEAD

echo
echo "== timezone check =="
TZ_NAME="$(sudo systemsetup -gettimezone 2>/dev/null | awk -F': ' '{print $2}' || true)"
UTC_OFFSET="$(date +%z)"
echo "timezone: ${TZ_NAME:-unknown}"
echo "date: $(date)"

if [ "$UTC_OFFSET" = "+0900" ]; then
  echo "timezone offset already +0900; skipping sudo timezone change."
elif [ "$TZ_NAME" != "Asia/Seoul" ]; then
  echo "Setting macOS timezone to Asia/Seoul. sudo password may be required."
  sudo systemsetup -settimezone Asia/Seoul >/dev/null
fi

if [ "$(date +%z)" != "+0900" ]; then
  echo "ERROR: timezone is still not +0900. Stop before installing cron." >&2
  exit 1
fi

echo "timezone ok: $(date)"

echo
echo "== install cron =="
bash scripts/install_cron_kiwoom.sh

echo
echo "== verify =="
crontab -l | grep -E "scripts/publish.py|scripts/radar_backtest.py|scripts/autotrade_executor.py|scripts/autotrade_monitor.py|scripts/autotrade_stats.py" || {
  echo "ERROR: project cron lines not found" >&2
  exit 1
}

echo
echo "Mac autorun setup complete."
echo "Logs:"
echo "  tail -f /tmp/kiwoom_publish.log"
echo "  tail -f /tmp/kiwoom_autotrade.log"
