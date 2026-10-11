#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
set -a
source .env
set +a
# Preserve the configured music library, but isolate adult observations/photos.
export BMON_PLAY_MEDIA_DIR="${BMON_PLAY_MEDIA_DIR:-${BMON_MEDIA_DIR:-${BMON_MEDIA_MOUNT:-${BMON_DATA_DIR:-$PWD/data}/media}}/陪我玩}"
export BMON_PERSON_TEST_MODE=true
export BMON_DATA_DIR="$PWD/data/person-test"
export BMON_MEDIA_MOUNT="$BMON_DATA_DIR/media"
export BMON_MEDIA_DIR="$BMON_MEDIA_MOUNT"
exec "${BMON_PYTHON:-python3}" -B server.py
