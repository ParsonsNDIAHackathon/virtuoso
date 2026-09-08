#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-multi-int-fusion-replay}"
CONTAINER_NAME="${CONTAINER_NAME:-multi-int-fusion-replay}"
HOST_PORT="${HOST_PORT:-8000}"

if ! command -v docker >/dev/null 2>&1; then
  printf 'Docker is required but was not found on PATH.\n' >&2
  exit 1
fi

exec docker run --rm \
  --name "$CONTAINER_NAME" \
  --publish "$HOST_PORT:8000" \
  "$IMAGE_NAME"
