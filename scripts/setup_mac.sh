#!/usr/bin/env bash
# 키움 시스템 Mac 원클릭 셋업. 클론 후 이 스크립트만 실행하면 .env 생성→토큰테스트→cron설치까지.
#   git clone https://github.com/xenonluv/kiwoomnews.git ~/kiwoomnews
#   cd ~/kiwoomnews && bash scripts/setup_mac.sh
# ⚠ KIS(stocknews)와 별개 폴더/저장소. cron은 네임스페이스 블록이라 KIS 무손상.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="$(command -v python3 || echo /usr/bin/python3)"
cd "$REPO"
echo "=== 키움 Mac 셋업 (repo=$REPO) ==="

# 1) .env 부트스트랩 — 없으면 값 입력받아 생성(시크릿은 화면에 안 보이게). 값 출처: kiwwom_apikey.md + KV_DATA.md.
if [ ! -f .env ]; then
  echo "--- .env 없음 → 값 입력해 생성 (입력값은 화면에 표시 안 됨) ---"
  read -rsp "KIWOOM_APP_KEY: " K_APP; echo
  read -rsp "KIWOOM_SECRET_KEY: " K_SEC; echo
  read -rp  "KV_REST_API_URL (예 https://xxx.upstash.io): " K_URL
  read -rsp "KV_REST_API_READ_ONLY_TOKEN: " K_TOK; echo
  umask 077   # .env 권한 600
  cat > .env <<EOF
KIWOOM_APP_KEY=$K_APP
KIWOOM_SECRET_KEY=$K_SEC
KIWOOM_MARKET=UN
KV_REST_API_URL=$K_URL
KV_REST_API_READ_ONLY_TOKEN=$K_TOK
EOF
  echo "✅ .env 생성됨 (gitignore됨 — 커밋 안 됨)"
else
  echo "✅ .env 이미 존재 — 그대로 사용"
fi

# 2) 키움 토큰·조회 테스트 (Mac IP에서 토큰 발급 가능한지 = 유일한 실질 관문)
echo "--- 키움 토큰·조회 테스트 (삼성전자 005930) ---"
if PYTHONUTF8=1 "$PY" scripts/kiwoom_client.py 005930 | head -6; then
  echo "✅ 키움 조회 정상 — Mac IP에서 토큰 OK"
else
  echo "❌ 조회 실패 — .env 키/네트워크/키움 포털 IP 화이트리스트 확인 후 재실행"
  exit 1
fi

# 3) 시스템 점검
echo "--- 시스템 점검 ---"
case "$(date +%Z)" in
  KST|JST) echo "  ✅ 시간대 $(date +%Z)";;
  *) echo "  ⚠️  시간대 $(date +%Z) — KST 아님. 실행: sudo systemsetup -settimezone Asia/Seoul";;
esac
echo "  ⚠️  노트북 상시가동: sudo pmset -c disablesleep 1  (AC 전원 시 뚜껑 닫아도 안 잠)"

# 4) cron 설치 (자동매매는 DRY로 안전 설치 — 실발주는 스모크 후 --live)
echo "--- cron 설치 (게시 활성 · 자동매매 DRY) ---"
bash scripts/install_cron_kiwoom.sh

echo
echo "=== 셋업 완료 ==="
echo "다음:"
echo "  1) (필요시) sudo systemsetup -settimezone Asia/Seoul; sudo pmset -c disablesleep 1"
echo "  2) Windows Task Scheduler 키움 작업 4개 해제 (이중 실매수 방지)"
echo "  3) 하루 관찰: tail -f /tmp/kiwoom_publish.log — 10분마다 게시·사이트 갱신 확인"
echo "  4) 소액 스모크 후 실발주 켜기: bash scripts/install_cron_kiwoom.sh --live + 사이트 토글 ON"
