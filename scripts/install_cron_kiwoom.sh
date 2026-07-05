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
LIVE_ENV=""; DRYRUN=0; UNINSTALL=0
for a in "$@"; do
  case "$a" in
    --live) LIVE_ENV="AUTOTRADE_LIVE=1 ";;
    --dry-run) DRYRUN=1;;
    --uninstall) UNINSTALL=1;;
    *) echo "알 수 없는 옵션: $a (사용: [--live] [--dry-run] | --uninstall)"; exit 1;;
  esac
done

# PYTHONUTF8=1: Mac은 기본 UTF-8이라 사실상 불필요하나 안전용. 자동매매 라인은 --live 시에만 실발주(KV 토글과 이중 게이트).
BLOCK="$BEGIN
PATH=/usr/local/bin:/usr/bin:/bin
# 레이더 게시 — 평일 9~20시 10분 간격(정규장+NXT 애프터마켓). 변경 시에만 push → Vercel 재빌드.
1,11,21,31,41,51 9-20 * * 1-5 cd $REPO && PYTHONUTF8=1 ${PY} scripts/publish.py >> /tmp/kiwoom_publish.log 2>&1
# 자동매매 매수 — 15:18 KRX 종가베팅(비-NXT 종목) / 19:50 NXT(NXT 거래가능 종목, 5호가위 지정가)
18 15 * * 1-5 cd $REPO && PYTHONUTF8=1 ${LIVE_ENV}${PY} scripts/autotrade_executor.py --slot krx >> /tmp/kiwoom_autotrade.log 2>&1
50 19 * * 1-5 cd $REPO && PYTHONUTF8=1 ${LIVE_ENV}${PY} scripts/autotrade_executor.py --slot nxt >> /tmp/kiwoom_autotrade.log 2>&1
# 자동매매 청산 감시 — 정규장 5분 간격(-5%손절/+7%50%익절/+11%익절/본전방어)
*/5 9-15 * * 1-5 cd $REPO && PYTHONUTF8=1 ${LIVE_ENV}${PY} scripts/autotrade_monitor.py >> /tmp/kiwoom_autotrade.log 2>&1
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
echo "  ℹ️  로그: tail -f /tmp/kiwoom_publish.log /tmp/kiwoom_autotrade.log"
echo "  ⚠️  Windows Task Scheduler의 키움 작업 4개를 반드시 해제(이중 실매수 방지)."
