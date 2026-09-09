#!/usr/bin/env bash
# Rebuild and restart the Docker stack stamped with the current commit.
set -e
cd "$(dirname "$0")"
export BUILD="$(git rev-parse --short HEAD)"
docker compose up --build -d
echo "stack built from $BUILD"
