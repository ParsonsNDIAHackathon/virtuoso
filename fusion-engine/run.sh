#!/usr/bin/env bash
# Launch the Fusion Engine dashboard with this project's virtual environment.
# Optional Uvicorn flags are forwarded, e.g. ./run.sh --reload
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

if [[ ! -x .venv/bin/uvicorn ]]; then
  echo "Missing .venv/bin/uvicorn. Create the virtual environment and install requirements first:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

exec .venv/bin/uvicorn app.server:app \
  --host "${HOST:-0.0.0.0}" \
  --port "${PORT:-3333}" \
  "$@"
