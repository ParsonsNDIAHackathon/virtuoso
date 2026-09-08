#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_NAME="${IMAGE_NAME:-multi-int-fusion-replay}"

if ! command -v docker >/dev/null 2>&1; then
  printf 'Docker is required but was not found on PATH.\n' >&2
  exit 1
fi

cd "$ROOT_DIR"
exec docker build --tag "$IMAGE_NAME" .
