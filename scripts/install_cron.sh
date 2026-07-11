#!/usr/bin/env bash
# 하위호환 진입점. 실제 cron 생성 정책은 install_cron_kiwoom.sh 한 곳에서만 관리한다.
# 기본은 DRY이며 실발주는 반드시 --live를 명시해야 한다.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec bash "$SCRIPT_DIR/install_cron_kiwoom.sh" "$@"
