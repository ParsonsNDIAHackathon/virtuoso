#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

ANCHOR="${ANCHOR:-$(date -u '+%Y-%m-%dT%H:%M:00Z')}"


BEFORE_MINUTES="${BEFORE_MINUTES:-720}"
AFTER_MINUTES="${AFTER_MINUTES:-720}"
KEYWORDS="${KEYWORDS:-shipping tanker drone iran}"
KEYWORD_MODE="all" #"${KEYWORD_MODE:-matching}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"
LATITUDE="${LATITUDE:-26.57}"
LONGITUDE="${LONGITUDE:-56.25}"
RADIUS_KM="${RADIUS_KM:-300}"
OUTPUT_DIR="${OUTPUT_DIR:-data/gdelt}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  printf 'Python executable not found: %s\n' "$PYTHON_BIN" >&2
  exit 1
fi

read -r -a KEYWORD_ARGS <<< "$KEYWORDS"

cd "$ROOT_DIR"
exec "$PYTHON_BIN" -m app.sources.gdelt.prepare \
  --anchor "$ANCHOR" \
  --before-minutes "$BEFORE_MINUTES" \
  --after-minutes "$AFTER_MINUTES" \
  --keywords "${KEYWORD_ARGS[@]}" \
  --keyword-mode "$KEYWORD_MODE" \
  --latitude "$LATITUDE" \
  --longitude "$LONGITUDE" \
  --radius-km "$RADIUS_KM" \
  --output-dir "$OUTPUT_DIR"
