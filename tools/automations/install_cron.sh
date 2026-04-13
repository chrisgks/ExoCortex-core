#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/env python3}"
CRON_EXPR="${CRON_EXPR:-*/15 * * * *}"
MARKER_BEGIN="# BEGIN EXOCORTEX AUTOMATIONS"
MARKER_END="# END EXOCORTEX AUTOMATIONS"
COMMAND="cd \"$ROOT\" && $PYTHON_BIN tools/automations/refresh_status.py >/tmp/exocortex_automation_status.log 2>&1"

TMP_FILE="$(mktemp)"
trap 'rm -f "$TMP_FILE"' EXIT

{
  crontab -l 2>/dev/null || true
} | awk -v begin="$MARKER_BEGIN" -v end="$MARKER_END" '
  $0 == begin {skip=1; next}
  $0 == end {skip=0; next}
  !skip {print}
' > "$TMP_FILE"

{
  cat "$TMP_FILE"
  echo "$MARKER_BEGIN"
  echo "$CRON_EXPR $COMMAND"
  echo "$MARKER_END"
} | crontab -

echo "Installed ExoCortex automation cron job:"
echo "$CRON_EXPR $COMMAND"
