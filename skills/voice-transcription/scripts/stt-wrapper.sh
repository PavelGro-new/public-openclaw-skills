#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: stt-wrapper.sh AUDIO_FILE" >&2
  exit 64
fi

INPUT="$1"
if [ ! -f "$INPUT" ]; then
  echo "audio file not found: $INPUT" >&2
  exit 66
fi

if [ -n "${STT_ENV_FILE:-}" ] && [ -f "$STT_ENV_FILE" ]; then
  # shellcheck disable=SC1090
  set -a && . "$STT_ENV_FILE" && set +a
fi

TRANSCRIBE_CLI="${STT_TRANSCRIBE_CLI:-}"
MODEL="${STT_MODEL_PATH:-}"
CACHE_DIR="${STT_CACHE_DIR:-/opt/openclaw-state/voice-transcription/cache}"
LOG_DIR="${STT_LOG_DIR:-/opt/openclaw-state/voice-transcription/logs}"
CHUNK_SECONDS="${STT_CHUNK_SECONDS:-600}"
THREADS="${STT_THREADS:-2}"
LOCK_WAIT_SECONDS="${STT_LOCK_WAIT_SECONDS:-3600}"
MAX_ATTEMPTS="${STT_MAX_ATTEMPTS:-3}"

if [ -z "$TRANSCRIBE_CLI" ] || [ ! -x "$TRANSCRIBE_CLI" ]; then
  echo "STT_TRANSCRIBE_CLI is not set or not executable" >&2
  exit 69
fi

if [ -z "$MODEL" ] || [ ! -s "$MODEL" ]; then
  echo "STT_MODEL_PATH is not set or model file is missing" >&2
  exit 69
fi

mkdir -p "$CACHE_DIR" "$LOG_DIR"
LOCK_FILE="$CACHE_DIR/stt-wrapper.lock"
QUEUE_LOG="$LOG_DIR/stt-queue.tsv"
METRIC_LOG="$LOG_DIR/stt-last.log"

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

timestamp() {
  date -Is
}

log_state() {
  local state="$1"
  local detail="${2:-}"
  printf '%s\t%s\t%s\t%s\n' "$(timestamp)" "$state" "$INPUT" "$detail" >>"$QUEUE_LOG"
}

on_error() {
  local code="$?"
  local line="${1:-unknown}"
  log_state "error" "line=$line exit=$code"
  exit "$code"
}
trap 'on_error $LINENO' ERR

INPUT_SHA="$(sha256sum "$INPUT" | awk '{print $1}')"
CACHE_TEXT="$CACHE_DIR/${INPUT_SHA}.txt"
if [ -s "$CACHE_TEXT" ]; then
  log_state "cache_hit" "sha=$INPUT_SHA"
  tr '\n' ' ' <"$CACHE_TEXT" | sed -E 's/[[:space:]]+/ /g; s/^ //; s/ $//'
  printf '\n'
  exit 0
fi

exec 9>"$LOCK_FILE"
log_state "queued" "waiting_for_lock"
if ! flock -w "$LOCK_WAIT_SECONDS" 9; then
  log_state "error" "lock_timeout_${LOCK_WAIT_SECONDS}s"
  echo "stt queue lock timeout after ${LOCK_WAIT_SECONDS}s" >&2
  exit 75
fi

if [ -s "$CACHE_TEXT" ]; then
  log_state "cache_hit_after_lock" "sha=$INPUT_SHA"
  tr '\n' ' ' <"$CACHE_TEXT" | sed -E 's/[[:space:]]+/ /g; s/^ //; s/ $//'
  printf '\n'
  exit 0
fi

WAV="$TMP_DIR/input-16k-mono.wav"
TRANSCRIPT_ALL="$TMP_DIR/transcript-all.txt"
RUN_LOG="$TMP_DIR/run.log"

ffmpeg -nostdin -hide_banner -loglevel error -i "$INPUT" -ar 16000 -ac 1 -y "$WAV" >&2

DURATION="$(ffprobe -v error -show_entries format=duration -of default=nk=1:nw=1 "$WAV" 2>/dev/null || printf '0')"
NEEDS_CHUNKING="$(awk -v d="$DURATION" -v c="$CHUNK_SECONDS" 'BEGIN { print (d > c ? 1 : 0) }')"

CHUNK_LIST="$TMP_DIR/chunks.txt"
if [ "$NEEDS_CHUNKING" = "1" ]; then
  mkdir -p "$TMP_DIR/chunks"
  ffmpeg -nostdin -hide_banner -loglevel error -i "$WAV" -f segment -segment_time "$CHUNK_SECONDS" -reset_timestamps 1 "$TMP_DIR/chunks/chunk-%03d.wav" >&2
  find "$TMP_DIR/chunks" -type f -name 'chunk-*.wav' | sort >"$CHUNK_LIST"
else
  printf '%s\n' "$WAV" >"$CHUNK_LIST"
fi

CHUNK_COUNT="$(wc -l <"$CHUNK_LIST" | tr -d ' ')"
log_state "transcribing" "duration=${DURATION}s chunks=$CHUNK_COUNT"
: >"$TRANSCRIPT_ALL"

chunk_index=0
while IFS= read -r CHUNK; do
  chunk_index="$((chunk_index + 1))"
  OUT="$TMP_DIR/transcript-${chunk_index}.txt"
  attempt=1
  while true; do
    : >"$RUN_LOG"
    log_state "transcribing" "chunk=$chunk_index/$CHUNK_COUNT attempt=$attempt"
    set +e
    /usr/bin/time -f "chunk=${chunk_index}/${CHUNK_COUNT} attempt=${attempt} elapsed_sec=%e max_rss_kb=%M" \
      "$TRANSCRIBE_CLI" \
      --quiet \
      --threads "$THREADS" \
      -m "$MODEL" \
      --output "$OUT" \
      "$CHUNK" >"$RUN_LOG" 2>>"$METRIC_LOG"
    code="$?"
    set -e
    if [ "$code" -eq 0 ] && [ -s "$OUT" ]; then
      tr '\n' ' ' <"$OUT" | sed -E 's/[[:space:]]+/ /g; s/^ //; s/ $//' >>"$TRANSCRIPT_ALL"
      printf '\n' >>"$TRANSCRIPT_ALL"
      log_state "done_chunk" "chunk=$chunk_index/$CHUNK_COUNT attempt=$attempt"
      break
    fi
    log_state "retry" "chunk=$chunk_index/$CHUNK_COUNT attempt=$attempt exit=$code"
    if [ "$attempt" -ge "$MAX_ATTEMPTS" ]; then
      echo "[ERROR: chunk ${chunk_index}/${CHUNK_COUNT} failed after ${MAX_ATTEMPTS} attempts]" >>"$TRANSCRIPT_ALL"
      log_state "error_chunk" "chunk=$chunk_index/$CHUNK_COUNT exit=$code"
      break
    fi
    sleep "$((attempt * 2))"
    attempt="$((attempt + 1))"
  done
done <"$CHUNK_LIST"

if [ ! -s "$TRANSCRIPT_ALL" ]; then
  log_state "error" "empty_transcript"
  echo "empty transcript" >&2
  exit 65
fi

tr '\n' ' ' <"$TRANSCRIPT_ALL" | sed -E 's/[[:space:]]+/ /g; s/^ //; s/ $//' >"$CACHE_TEXT"
chmod 0600 "$CACHE_TEXT"
log_state "done" "chunks=$CHUNK_COUNT"
tr '\n' ' ' <"$CACHE_TEXT" | sed -E 's/[[:space:]]+/ /g; s/^ //; s/ $//'
printf '\n'
