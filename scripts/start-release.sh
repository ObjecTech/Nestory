#!/usr/bin/env bash
# Start one Nestory service from a release snapshot (see docs/DEPLOY.md).
# Usage: start-release.sh model|app
# Code comes from the release directory holding this script; private
# configuration, data, models and virtualenvs stay in the base checkout.
set -euo pipefail
umask 077
role="${1:?usage: start-release.sh model|app}"
release="$(cd -- "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")/.." && pwd)"
base="${BMON_BASE_DIR:-$HOME/baby-monitor-public}"
cd "$base"
if [[ ! -f .env ]]; then
  echo "Missing $base/.env" >&2
  exit 1
fi
set -a
source .env
set +a
export BMON_APP_DIR="$base"
export BMON_STATIC_DIR="$release/static"
export BMON_ASSISTANT_ENABLED=true
export BMON_VLM_MODEL_PATH="$base/models/Qwen3-VL-2B-Instruct"
export BMON_VLM_HOST=127.0.0.1
export BMON_VLM_PORT=8010
export BMON_VLM_SERVICE_URL=http://127.0.0.1:8010
export LD_LIBRARY_PATH="$base/.venv-models/lib:/usr/local/cuda/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
case "$role" in
  model) exec "$base/.venv-models/bin/python" -B "$release/model_server.py" ;;
  app) exec "$base/.venv-models/bin/python" -B "$release/server.py" ;;
  *) echo "Unknown role: $role" >&2; exit 2 ;;
esac
