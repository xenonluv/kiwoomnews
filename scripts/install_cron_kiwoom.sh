#!/usr/bin/env bash
# 키움 네임스페이스 cron 설치기 (Mac 프로덕션). ⚠ KIS(stocknews) cron 무손상 —
# '# KIWOOMNEWS_BEGIN/END' 마커 블록만 sed로 교체/제거하므로 같은 파일명(publish.py 등)이어도 KIS 라인 안 건드림.
#   bash scripts/install_cron_kiwoom.sh            # 설치 (자동매매 DRY = 실발주 안 함, 게시만 활성)
#   bash scripts/install_cron_kiwoom.sh --live     # 자동매매 실발주 ON (AUTOTRADE_LIVE=1 포함) — 스모크 후에만!
#   bash scripts/install_cron_kiwoom.sh --dry-run  # 적용될 블록만 출력(설치 안 함)
#   bash scripts/install_cron_kiwoom.sh --uninstall # 키움 블록만 제거(KIS 무손상)
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="$(command -v python3 || echo /usr/bin/python3)"
BEGIN="# KIWOOMNEWS_BEGIN"
END="# KIWOOMNEWS_END"

# 플래그는 순서 무관·조합 가능(예: --live --dry-run 으로 실발주 블록 미리보기).
LIVE_ENV=""; DRY_ARG="--dry"; DRYRUN=0; UNINSTALL=0
for a in "$@"; do
  case "$a" in
    --live) LIVE_ENV="AUTOTRADE_LIVE=1 "; DRY_ARG="";;
    --dry-run) DRYRUN=1;;
    --uninstall) UNINSTALL=1;;
    *) echo "알 수 없는 옵션: $a (사용: [--live] [--dry-run] | --uninstall)"; exit 1;;
  esac
done

# PYTHONUTF8=1: Mac은 기본 UTF-8이라 사실상 불필요하나 안전용. 자동매매 라인은 --live 시에만 실발주(KV 토글과 이중 게이트).
BLOCK="$BEGIN
PATH=/usr/local/bin:/usr/bin:/bin
# KIS 토큰 — 추적/AI/국면 평가용 일봉 API 토큰 만료시각 고정.
0 7 * * * cd $REPO && PYTHONUTF8=1 ${PY} scripts/kis_client.py --issue-token >> /tmp/kiwoom_kis_token.log 2>&1
# 레이더 게시 — 평일 9~20시 10분 간격(정규장+NXT 애프터마켓). 변경 시에만 push → Vercel 재빌드.
1,11,21,31,41,51 9-20 * * 1-5 cd $REPO && PYTHONUTF8=1 ${PY} scripts/publish.py >> /tmp/kiwoom_publish.log 2>&1
# 자동매매 매수 — 15:18 KRX 종가베팅(비-NXT 종목) / 19:50 NXT(NXT 거래가능 종목, 5호가위 지정가)
18 15 * * 1-5 cd $REPO && PYTHONUTF8=1 ${LIVE_ENV}${PY} scripts/autotrade_executor.py --slot krx ${DRY_ARG} >> /tmp/kiwoom_autotrade.log 2>&1
50 19 * * 1-5 cd $REPO && PYTHONUTF8=1 ${LIVE_ENV}${PY} scripts/autotrade_executor.py --slot nxt ${DRY_ARG} >> /tmp/kiwoom_autotrade.log 2>&1
# 자동매매 청산 감시 — 1분 간격(급등주 대응). 08시=NXT프리마켓(NXT거래분 급등락 대응), 09~15:30=정규장(-5%손절/+7%50%익절/+11%익절/본전방어/14:50강제청산). 세션은 스크립트가 시각으로 자동 판정.
*/1 8-15 * * 1-5 cd $REPO && PYTHONUTF8=1 ${LIVE_ENV}${PY} scripts/autotrade_monitor.py ${DRY_ARG} >> /tmp/kiwoom_autotrade.log 2>&1
# AUTO ON 제출 불명 경고 실패 재시도 — OFF에서는 무알림, 주문/취소 없는 로컬 pending 가시성 전용.
20 16 * * 1-5 cd $REPO && PYTHONUTF8=1 ${PY} scripts/autotrade_pending_attention.py >> /tmp/kiwoom_autotrade.log 2>&1
55 20 * * 1-5 cd $REPO && PYTHONUTF8=1 ${PY} scripts/autotrade_pending_attention.py >> /tmp/kiwoom_autotrade.log 2>&1
# 익일 자가검증·통계·튜닝 — 레이더/자동매매/추적/AI 클릭/국면 평가
20 17 * * 1-5 cd $REPO && RADAR_BROKER=kiwoom RADAR_AI_PREDICT=0 PYTHONUTF8=1 ${PY} scripts/radar_backtest.py --push >> /tmp/kiwoom_backtest.log 2>&1
25 17 * * 1-5 cd $REPO && PYTHONUTF8=1 ${PY} scripts/autotrade_stats.py --push >> /tmp/kiwoom_autotrade_stats.log 2>&1
30 17 * * 1-5 cd $REPO && PYTHONUTF8=1 ${PY} scripts/track_eval.py --push >> /tmp/kiwoom_track_eval.log 2>&1
35 17 * * 1-5 cd $REPO && PYTHONUTF8=1 ${PY} scripts/ai_click_eval.py --push >> /tmp/kiwoom_ai_click_eval.log 2>&1
37 17 * * 1-5 cd $REPO && PYTHONUTF8=1 ${PY} scripts/phase_eval.py --push >> /tmp/kiwoom_phase_eval.log 2>&1
# 비게시 포함 관찰군 forward 연구 — 운영 backtest/git push와 분리.
10 21 * * 1-5 cd $REPO && PYTHONUTF8=1 ${PY} scripts/radar_observed_forward.py >> /tmp/kiwoom_observed_forward.log 2>&1
# NXT 야간 급락 텔레그램 경고 — 정규장 마감 후 30분 간격
5,35 16-20 * * 1-5 cd $REPO && PYTHONUTF8=1 ${PY} scripts/night_alert.py >> /tmp/kiwoom_night_alert.log 2>&1
$END"

# 기존 키움 블록만 제거(다른 cron·KIS 라인 보존)
EXISTING="$(crontab -l 2>/dev/null | sed "/$BEGIN/,/$END/d" || true)"

if [ "$UNINSTALL" = "1" ]; then
  printf '%s\n' "$EXISTING" | crontab -
  echo "✅ 키움 cron 블록 제거됨 (KIS cron 무손상)."
  exit 0
fi

if [ "$DRYRUN" = "1" ]; then
  echo "[DRY-RUN] 설치될 키움 블록 (repo=$REPO, python=$PY):"
  echo "----------------------------------------"
  echo "$BLOCK"
  echo "----------------------------------------"
  echo "자동매매 실발주: $([ -n "$LIVE_ENV" ] && echo 'ON (--live)' || echo 'OFF = DRY (기본)')"
  echo "(실제 설치: --dry-run 없이 실행)"
  exit 0
fi

# 백업 후 설치
crontab -l > "/tmp/crontab.backup.$(date +%Y%m%d%H%M%S)" 2>/dev/null || true
printf '%s\n' "$EXISTING
$BLOCK" | crontab -

echo "✅ 키움 cron 설치 완료 (repo=$REPO, 자동매매 실발주=$([ -n "$LIVE_ENV" ] && echo ON || echo 'OFF=DRY'))"
echo "── 설치된 키움 블록 ──"
crontab -l | sed -n "/$BEGIN/,/$END/p" || true
echo
echo "── 점검 ──"
case "$(date +%Z)" in
  KST|JST) echo "  ✅ 시간대 $(date +%Z)";;
  *) echo "  ⚠️  시간대 $(date +%Z) — KST 아님! sudo systemsetup -settimezone Asia/Seoul";;
esac
echo "  ⚠️  노트북 잠자기 차단: sudo pmset -c disablesleep 1 (안 하면 뚜껑 닫을 때 cron 멈춤)"
echo "  ℹ️  로그: tail -f /tmp/kiwoom_publish.log /tmp/kiwoom_backtest.log /tmp/kiwoom_autotrade.log /tmp/kiwoom_track_eval.log /tmp/kiwoom_ai_click_eval.log /tmp/kiwoom_phase_eval.log /tmp/kiwoom_night_alert.log"
echo "  ⚠️  Windows Task Scheduler의 키움 작업 4개를 반드시 해제(이중 실매수 방지)."
