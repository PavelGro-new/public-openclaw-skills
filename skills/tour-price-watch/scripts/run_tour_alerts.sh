#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${OPENCLAW_WORKSPACE:-/path/to/openclaw/workspace}"
LOG_FILE="${TOUR_WATCH_LOG_FILE:-/opt/openclaw-logs/tour-price-watch.log}"
SCRIPT="$WORKSPACE/skills/tour-price-watch/scripts/tour_alerts.py"

mkdir -p "$(dirname "$LOG_FILE")"
echo "{\"event\":\"tour_scheduled_run_start\",\"at\":\"$(date -Is)\"}" >>"$LOG_FILE"
python3 "$SCRIPT" run >>"$LOG_FILE" 2>&1
echo "{\"event\":\"tour_scheduled_run_done\",\"at\":\"$(date -Is)\"}" >>"$LOG_FILE"
