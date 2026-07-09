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

# 현재 운영 crontab의 KIWOOMNEWS 블록과 일치시킨다.
# 레이더 게시 — 평일 9~20시 10분 간격(정규장+NXT 애프터마켓). 변경 시에만 push → Vercel 재빌드.
L_PUBLISH="1,11,21,31,41,51 9-20 * * 1-5 cd $REPO && PYTHONUTF8=1 $PY scripts/publish.py >> /tmp/kiwoom_publish.log 2>&1"
# 자동매매 매수 — 15:18 KRX 종가베팅 / 19:50 NXT.
L_AUTOTRADE_KRX="18 15 * * 1-5 cd $REPO && PYTHONUTF8=1 AUTOTRADE_LIVE=1 $PY scripts/autotrade_executor.py --slot krx >> /tmp/kiwoom_autotrade.log 2>&1"
L_AUTOTRADE_NXT="50 19 * * 1-5 cd $REPO && PYTHONUTF8=1 AUTOTRADE_LIVE=1 $PY scripts/autotrade_executor.py --slot nxt >> /tmp/kiwoom_autotrade.log 2>&1"
# 자동매매 청산 감시 — 1분 간격. 세션은 스크립트가 시각으로 자동 판정.
L_AUTOTRADE_MONITOR="*/1 8-15 * * 1-5 cd $REPO && PYTHONUTF8=1 AUTOTRADE_LIVE=1 $PY scripts/autotrade_monitor.py >> /tmp/kiwoom_autotrade.log 2>&1"
# 익일 자가검증·통계·튜닝.
L_RADAR_BT="20 17 * * 1-5 cd $REPO && RADAR_BROKER=kiwoom RADAR_AI_PREDICT=0 PYTHONUTF8=1 $PY scripts/radar_backtest.py --push >> /tmp/kiwoom_backtest.log 2>&1"
L_AUTOTRADE_STATS="25 17 * * 1-5 cd $REPO && PYTHONUTF8=1 $PY scripts/autotrade_stats.py --push >> /tmp/kiwoom_autotrade_stats.log 2>&1"

NEW_CRON="$(
  crontab -l 2>/dev/null | awk '
    /^# KIWOOMNEWS_BEGIN$/ { skip=1; next }
    /^# KIWOOMNEWS_END$/ { skip=0; next }
    skip { next }
    /scripts\/publish.py|scripts\/radar_backtest.py|scripts\/autotrade_executor.py|scripts\/autotrade_monitor.py|scripts\/autotrade_stats.py|scripts\/track_eval.py|scripts\/ai_click_eval.py|scripts\/phase_eval.py|scripts\/night_alert.py|scripts\/kis_client.py|analyzer\/run.py|analyzer\/backtest.py|^PATH=\/usr\/local\/bin:\/usr\/bin:\/bin$/ { next }
    { print }
  ' || true
  echo "# KIWOOMNEWS_BEGIN"
  echo "PATH=/usr/local/bin:/usr/bin:/bin"
  echo "# 레이더 게시 — 평일 9~20시 10분 간격(정규장+NXT 애프터마켓). 변경 시에만 push → Vercel 재빌드."
  echo "$L_PUBLISH"
  echo "# 자동매매 매수 — 15:18 KRX 종가베팅(비-NXT 종목) / 19:50 NXT(NXT 거래가능 종목, 5호가위 지정가)"
  echo "$L_AUTOTRADE_KRX"
  echo "$L_AUTOTRADE_NXT"
  echo "# 자동매매 청산 감시 — 1분 간격. 세션은 스크립트가 시각으로 자동 판정."
  echo "$L_AUTOTRADE_MONITOR"
  echo "# 익일 자가검증·통계·튜닝 — 17:20 레이더 백테스트 + 17:25 자동매매 실현손익 통계"
  echo "$L_RADAR_BT"
  echo "$L_AUTOTRADE_STATS"
  echo "# KIWOOMNEWS_END"
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
crontab -l | grep -E "publish.py|radar_backtest.py|autotrade_executor.py|autotrade_monitor.py|autotrade_stats.py" || true

echo
echo "── 점검(중요) ──"
pgrep -x cron >/dev/null 2>&1 && echo "  ✅ cron 데몬 실행 중" \
  || echo "  ⚠️  cron 미실행 → (Linux) sudo service cron start / (Mac) 시스템설정>개인정보보호>전체디스크접근에 cron 허용"
case "$(date +%Z)" in
  KST|JST) echo "  ✅ 시간대 $(date +%Z) (KST/동일오프셋)";;
  *) echo "  ⚠️  시간대 $(date +%Z) — KST 아님! Mac: sudo systemsetup -settimezone Asia/Seoul (또는 cron 시(hour) 조정)";;
esac
echo "  ⚠️  PC가 켜져 있어야 동작 — Mac: sudo pmset -a sleep 0"
echo "  ℹ️  로그: tail -f /tmp/kiwoom_publish.log /tmp/kiwoom_backtest.log /tmp/kiwoom_autotrade.log"
