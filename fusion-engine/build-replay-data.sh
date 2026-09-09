#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

if [[ -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
  PYTHON="$SCRIPT_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
else
  echo "Python 3 was not found. Create .venv or install python3." >&2
  exit 1
fi

# With no arguments, build the days configured for the included Hormuz replay.
# Any supplied arguments are passed directly to scripts/build_replay_data.py.
if [[ $# -eq 0 ]]; then
  set -- 2026-08-17 2026-08-18
fi

exec "$PYTHON" "$SCRIPT_DIR/scripts/build_replay_data.py" "$@"
