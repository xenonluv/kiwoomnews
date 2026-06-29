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

# 레이더 publish는 인자 불필요(전수 스캔이라 watchlist 없어도 누락 없음).
# 10분 간격 + 1분 오프셋(:01,:11,…). 당일 폭발 종목(/forecast) + 식음·반등 수상종목(메인)을 함께 게시.
# (구 analyzer 종가베팅 잡은 폐지 — /forecast는 이제 publish.py가 만든 당일 폭발 리스트를 보여준다.)
# 9~20시: 정규장(9~15:30) + **NXT 애프터마켓(~20:00)**. 마감 후엔 reaccum '현재 등락률'을 NXT 야간가로
# 재평가(스파크는 정규장 것 유지 — KIS가 NXT 분봉 미제공). 변경 시에만 push라 야간 추가 회차 비용은 미미.
L_PUBLISH="1,11,21,31,41,51 9-20 * * 1-5 cd $REPO && $PY scripts/publish.py >> /tmp/publish.log 2>&1"
# 레이더 자가 검증(익일 적중 채점 + 가중치 튜닝 + /performance 데이터)
L_RADAR_BT="20 17 * * 1-5 cd $REPO && $PY scripts/radar_backtest.py --push >> /tmp/radar_backtest.log 2>&1"
# 추적 종목(검색 후 📌 추적) 일일 검증 — 종합판정(룰) vs Kimi(AI), radar_backtest와 10분 시차
L_TRACK_EVAL="30 17 * * 1-5 cd $REPO && $PY scripts/track_eval.py --push >> /tmp/track_eval.log 2>&1"
# AI '클릭 예측' 임계 보정 — 'AI분석하기' 누른 전 종목의 익일 등락 채점, track_eval과 5분 시차
L_AI_CLICK="35 17 * * 1-5 cd $REPO && $PY scripts/ai_click_eval.py --push >> /tmp/ai_click_eval.log 2>&1"
# AI 국면 판정(재매집/분산/중립) 검증 — 'AI 국면 판정' 누른 종목의 익일 등락 채점, ai_click과 2분 시차
L_PHASE="37 17 * * 1-5 cd $REPO && $PY scripts/phase_eval.py --push >> /tmp/phase_eval.log 2>&1"
# NXT 시간외(야간) 급락 경고 — 레이더 후보+추적종목이 마감 후 −3%↓ 빠지면 텔레그램 1회 알림.
# 애프터마켓(~20:00) 동안 30분 간격 감시(:05,:35) — 16~20시로 막판(19:35~20:00)·마감 정착가까지 포착.
# 디둡으로 종목당 밤 1회. 정규장 cron과 무관.
L_NIGHT="5,35 16-20 * * 1-5 cd $REPO && $PY scripts/night_alert.py >> /tmp/night_alert.log 2>&1"
# KIS 토큰 매일 07:00 고정 발행 — 장중 임의 시각 재발급(만료 24시간 주기로 드리프트, 예 12:52) 방지.
# force 재발급이라 07:00 발급분이 장중(9~15:30)·애프터마켓(~20시) 내내 신선(회장님 지시 2026-06-29).
L_TOKEN="0 7 * * * cd $REPO && $PY scripts/kis_client.py --issue-token >> /tmp/kis_token.log 2>&1"

NEW_CRON="$(
  crontab -l 2>/dev/null | grep -v -E "scripts/publish.py|scripts/radar_backtest.py|scripts/track_eval.py|scripts/ai_click_eval.py|scripts/phase_eval.py|scripts/night_alert.py|scripts/kis_client.py|analyzer/run.py|analyzer/backtest.py|^PATH=/usr/local/bin:/usr/bin:/bin$" || true
  echo "PATH=/usr/local/bin:/usr/bin:/bin"
  echo "$L_TOKEN"
  echo "$L_PUBLISH"
  echo "$L_RADAR_BT"
  echo "$L_TRACK_EVAL"
  echo "$L_AI_CLICK"
  echo "$L_PHASE"
  echo "$L_NIGHT"
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
crontab -l | grep -E "publish.py|radar_backtest.py|track_eval.py|ai_click_eval.py|phase_eval.py|night_alert.py" || true

echo
echo "── 점검(중요) ──"
pgrep -x cron >/dev/null 2>&1 && echo "  ✅ cron 데몬 실행 중" \
  || echo "  ⚠️  cron 미실행 → (Linux) sudo service cron start / (Mac) 시스템설정>개인정보보호>전체디스크접근에 cron 허용"
case "$(date +%Z)" in
  KST|JST) echo "  ✅ 시간대 $(date +%Z) (KST/동일오프셋)";;
  *) echo "  ⚠️  시간대 $(date +%Z) — KST 아님! Mac: sudo systemsetup -settimezone Asia/Seoul (또는 cron 시(hour) 조정)";;
esac
echo "  ⚠️  PC가 켜져 있어야 동작 — Mac: sudo pmset -a sleep 0"
echo "  ℹ️  로그: tail -f /tmp/publish.log /tmp/radar_backtest.log"
