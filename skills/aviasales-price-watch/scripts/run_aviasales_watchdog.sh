#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${OPENCLAW_WORKSPACE:-/path/to/openclaw/workspace}"
LOG_FILE="${AVIASALES_LOG_FILE:-/opt/openclaw-logs/aviasales-price-watch.log}"
SCRIPT="$WORKSPACE/skills/aviasales-price-watch/scripts/aviasales_alerts.py"

mkdir -p "$(dirname "$LOG_FILE")"
echo "{\"event\":\"watchdog_run_start\",\"at\":\"$(date -Is)\"}" >>"$LOG_FILE"
python3 "$SCRIPT" watchdog >>"$LOG_FILE" 2>&1
echo "{\"event\":\"watchdog_run_done\",\"at\":\"$(date -Is)\"}" >>"$LOG_FILE"
