#!/usr/bin/env bash
set -euo pipefail
umask 077
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
if [[ ! -f .env ]]; then
  echo 'Run python3 scripts/configure-auth.py first.' >&2
  exit 1
fi
# Only source the private environment file you generated and control.
set -a
source .env
set +a
exec "${BMON_PYTHON:-python3}" -B server.py
