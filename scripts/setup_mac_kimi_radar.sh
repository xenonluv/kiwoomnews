#!/usr/bin/env bash
# Mac production setup for radar + Kimi verification.
#
# Run this on the Mac that owns cron:
#   bash scripts/setup_mac_kimi_radar.sh
#
# What it does:
# - sync latest main
# - ensure MOONSHOT_* exists locally for Mac cron
# - set timezone/cron via setup_mac_autorun.sh
# - run a safe radar dry-run with Kimi disabled to verify the pipeline
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
WEB_ENV="$REPO/web/.env.local"

cd "$REPO"

echo "== kiwoomnews Mac Kimi radar setup =="
echo "repo: $REPO"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "ERROR: $1 command not found" >&2
    exit 1
  fi
}

require_cmd git
require_cmd python3

stash_dirty_worktree() {
  local status
  status="$(git status --porcelain -- . ':(exclude)scripts/setup_mac_kimi_radar.sh' ':(exclude)scripts/setup_mac_autorun.sh')"
  if [ -n "$status" ]; then
    echo "Local git changes found. Stashing them before sync."
    printf '%s\n' "$status"
    git stash push -u -m "setup_mac_kimi_radar auto-stash $(date +%Y%m%d%H%M%S)" -- . ':(exclude)scripts/setup_mac_kimi_radar.sh' ':(exclude)scripts/setup_mac_autorun.sh'
    echo "Local changes were preserved in git stash."
  elif [ -n "$(git status --porcelain -- scripts/setup_mac_kimi_radar.sh scripts/setup_mac_autorun.sh)" ]; then
    echo "Keeping local setup script changes out of auto-stash."
  fi
}

env_has_key() {
  local key="$1"
  [ -n "${!key:-}" ] && return 0
  [ -f "$REPO/.env" ] && grep -qE "^${key}=" "$REPO/.env" && return 0
  [ -f "$WEB_ENV" ] && grep -qE "^${key}=" "$WEB_ENV" && return 0
  return 1
}

read_env_value() {
  local file="$1"
  local key="$2"
  [ -f "$file" ] || return 1
  python3 - "$file" "$key" <<'PY'
import sys
path, key = sys.argv[1], sys.argv[2]
try:
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == key:
            print(v.strip().strip('"').strip("'"))
            raise SystemExit(0)
except FileNotFoundError:
    pass
raise SystemExit(1)
PY
}

read_kimi_api_key_value() {
  local file="$1"
  [ -f "$file" ] || return 1
  python3 - "$file" <<'PY'
import re
import sys

path = sys.argv[1]
aliases = {"MOONSHOT_API_KEY", "KIMI_API_KEY", "API_KEY", "KIMI API_KEY"}
try:
    with open(path, encoding="utf-8") as f:
        text = f.read()
except FileNotFoundError:
    raise SystemExit(1)

for line in text.splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    normalized = re.sub(r"[\s-]+", "_", k.strip().upper())
    if normalized in aliases:
        value = v.strip().strip('"').strip("'")
        if value:
            print(value)
            raise SystemExit(0)

m = re.search(r"\bsk-[A-Za-z0-9_-]{20,}\b", text)
if m:
    print(m.group(0))
    raise SystemExit(0)

raise SystemExit(1)
PY
}

append_or_replace_env() {
  local key="$1"
  local value="$2"
  mkdir -p "$(dirname "$WEB_ENV")"
  touch "$WEB_ENV"
  if grep -qE "^${key}=" "$WEB_ENV"; then
    local tmp
    tmp="$(mktemp)"
    awk -v k="$key" -v v="$value" 'BEGIN{FS=OFS="="} $1==k {$0=k"="v} {print}' "$WEB_ENV" > "$tmp"
    mv "$tmp" "$WEB_ENV"
  else
    printf '%s=%s\n' "$key" "$value" >> "$WEB_ENV"
  fi
}

import_env_from_file() {
  local file="$1"
  local changed=0
  [ -f "$file" ] || return 1
  for key in MOONSHOT_API_KEY MOONSHOT_BASE_URL MOONSHOT_MODEL; do
    if ! env_has_key "$key"; then
      local value
      value="$(read_env_value "$file" "$key" || true)"
      if [ -n "$value" ]; then
        append_or_replace_env "$key" "$value"
        changed=1
      fi
    fi
  done
  if ! env_has_key "MOONSHOT_API_KEY"; then
    local kimi_value
    kimi_value="$(read_kimi_api_key_value "$file" || true)"
    if [ -n "$kimi_value" ]; then
      append_or_replace_env "MOONSHOT_API_KEY" "$kimi_value"
      changed=1
    fi
  fi
  [ "$changed" = "1" ]
}

extract_kimi_key_from_notes() {
  python3 - "$REPO" "$HOME" <<'PY'
import re
import sys
from pathlib import Path
roots = [Path(sys.argv[1]), Path(sys.argv[2])]
names = ["kimiapi.md", "apikey.md", ".kimiapi", ".stocknews_kimi", ".env"]
for root in roots:
    for name in names:
        path = root / name
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"\bsk-[A-Za-z0-9_-]{20,}\b", text)
        if m:
            print(m.group(0))
            raise SystemExit(0)
raise SystemExit(1)
PY
}

try_vercel_env_pull() {
  local tmp
  tmp="$(mktemp)"
  echo "Trying to pull MOONSHOT_* from Vercel project env..."
  if command -v vercel >/dev/null 2>&1; then
    (cd "$REPO/web" && vercel env pull "$tmp" --yes --environment=production >/dev/null)
  elif command -v npx >/dev/null 2>&1; then
    (cd "$REPO/web" && npx --yes vercel env pull "$tmp" --yes --environment=production >/dev/null)
  else
    rm -f "$tmp"
    return 1
  fi
  import_env_from_file "$tmp" || true
  rm -f "$tmp"
  env_has_key "MOONSHOT_API_KEY"
}

echo
echo "== sync main =="
stash_dirty_worktree
git fetch origin main
git merge --ff-only FETCH_HEAD

echo
echo "== Kimi local env =="
if env_has_key "MOONSHOT_API_KEY"; then
  echo "MOONSHOT_API_KEY: already configured locally"
else
  echo "MOONSHOT_API_KEY: not found locally. Trying automatic sources."
  for f in "$REPO/.env" "$WEB_ENV" "$REPO/kimiapi.md" "$REPO/apikey.md" "$HOME/kimiapi.md" "$HOME/apikey.md" "$HOME/.env"; do
    import_env_from_file "$f" && echo "Imported MOONSHOT_* from $f" && break || true
  done
  if ! env_has_key "MOONSHOT_API_KEY"; then
    KIMI_KEY="$(extract_kimi_key_from_notes || true)"
    if [ -n "${KIMI_KEY:-}" ]; then
      append_or_replace_env "MOONSHOT_API_KEY" "$KIMI_KEY"
      echo "MOONSHOT_API_KEY: imported from local secret note"
    fi
  fi
  if ! env_has_key "MOONSHOT_API_KEY"; then
    try_vercel_env_pull || true
  fi
  if ! env_has_key "MOONSHOT_API_KEY"; then
    echo "ERROR: MOONSHOT_API_KEY could not be configured automatically." >&2
    echo "Fix one of these, then rerun:" >&2
    echo "  1) log in/link Vercel CLI so 'vercel env pull' works from web/" >&2
    echo "  2) put MOONSHOT_API_KEY in web/.env.local or .env" >&2
    echo "  3) put the sk-... key in ~/kimiapi.md" >&2
    exit 1
  fi
  echo "MOONSHOT_API_KEY: configured locally"
fi

if ! env_has_key "MOONSHOT_BASE_URL"; then
  append_or_replace_env "MOONSHOT_BASE_URL" "https://api.moonshot.ai/v1"
  echo "MOONSHOT_BASE_URL: added default"
else
  echo "MOONSHOT_BASE_URL: already configured"
fi

if ! env_has_key "MOONSHOT_MODEL"; then
  append_or_replace_env "MOONSHOT_MODEL" "kimi-k2.6"
  echo "MOONSHOT_MODEL: added default kimi-k2.6"
else
  echo "MOONSHOT_MODEL: already configured"
fi

echo
echo "== install Mac autorun =="
bash scripts/setup_mac_autorun.sh

echo
echo "== verify radar pipeline =="
RADAR_KIMI_VERIFY=0 python3 scripts/publish.py --dry-run --max 3

echo
echo "== current cron =="
crontab -l | grep -E "scripts/publish.py|scripts/radar_backtest.py|scripts/autotrade_stats.py" || {
  echo "ERROR: project cron lines not found" >&2
  exit 1
}

echo
echo "Setup complete."
echo "Kimi radar verification runs only during 14:45~15:30 KST when candidates exist."
echo "Logs:"
echo "  tail -f /tmp/kiwoom_publish.log"
echo "  tail -f /tmp/kiwoom_backtest.log"
