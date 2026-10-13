#!/usr/bin/env bash
set -euo pipefail

jetson_host="${BMON_JETSON_HOST:-jetson}"
local_port="${BMON_PREVIEW_PORT:-18080}"

if [[ ! "$local_port" =~ ^[0-9]+$ ]] || (( local_port < 1024 || local_port > 65535 )); then
  echo "BMON_PREVIEW_PORT must be a number between 1024 and 65535." >&2
  exit 2
fi

echo "Starting the baby monitor on ${jetson_host}..."
ssh -o BatchMode=yes -o ConnectTimeout=10 "$jetson_host" 'bash -s' <<'REMOTE'
set -euo pipefail

project_dir="$HOME/baby-monitor-public"
cd "$project_dir"

if [[ ! -f .env ]]; then
  echo "Missing $project_dir/.env on the Jetson." >&2
  exit 1
fi

mkdir -p logs

model_is_ready() {
  curl --fail --silent --show-error --max-time 3 \
    http://127.0.0.1:8010/health 2>/dev/null \
    | grep -Eq '"ok"[[:space:]]*:[[:space:]]*true'
}

if ! model_is_ready; then
  if ! pgrep -u "$(id -u)" -f '/baby-monitor-public/.*python.*-B model_server\.py' >/dev/null; then
    echo "Starting Qwen model service..."
    nohup bash scripts/run-model.sh >>logs/model-preview.log 2>&1 </dev/null &
  else
    echo "Qwen model service is loading..."
  fi

  for _ in $(seq 1 90); do
    if model_is_ready; then
      break
    fi
    sleep 1
  done
fi

if ! model_is_ready; then
  echo "Qwen model service did not become ready. Recent log output:" >&2
  tail -n 30 logs/model-preview.log >&2 || true
  exit 1
fi

# Read only the origin needed for the application's Host check. The run script
# loads the complete private environment when it starts the application.
public_origin="$(sed -n 's/^BMON_PUBLIC_ORIGIN=//p' .env | tail -n 1)"
if [[ -z "$public_origin" ]]; then
  echo "BMON_PUBLIC_ORIGIN is missing from $project_dir/.env." >&2
  exit 1
fi
origin_authority="${public_origin#*://}"
origin_authority="${origin_authority%%/*}"
bind_host="$(sed -n 's/^BMON_HOST=//p' .env | tail -n 1)"
bind_port="$(sed -n 's/^BMON_PORT=//p' .env | tail -n 1)"
bind_host="${bind_host:-127.0.0.1}"
bind_port="${bind_port:-8080}"

web_is_ready() {
  local status
  status="$(curl --silent --show-error --max-time 3 -o /dev/null \
    -w '%{http_code}' -H "Host: $origin_authority" \
    "http://${bind_host}:${bind_port}/" 2>/dev/null || true)"
  [[ "$status" == "200" || "$status" == "302" || "$status" == "303" ]]
}

if ! web_is_ready; then
  if ! pgrep -u "$(id -u)" -f '/baby-monitor-public/.*python.*-B server\.py' >/dev/null; then
    echo "Starting camera and web application..."
    nohup bash scripts/run.sh >>logs/app-preview.log 2>&1 </dev/null &
  else
    echo "Web application is loading..."
  fi

  for _ in $(seq 1 30); do
    if web_is_ready; then
      break
    fi
    sleep 1
  done
fi

if ! web_is_ready; then
  echo "Web application did not become ready. Recent log output:" >&2
  tail -n 30 logs/app-preview.log >&2 || true
  exit 1
fi

echo "Jetson services are ready."
REMOTE

public_origin="$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$jetson_host" \
  "sed -n 's/^BMON_PUBLIC_ORIGIN=//p' \"\$HOME/baby-monitor-public/.env\" | tail -n 1")"
origin_authority="${public_origin#*://}"
origin_authority="${origin_authority%%/*}"
origin_hostname="${origin_authority%%:*}"

if [[ "$origin_hostname" == "localhost" || "$origin_hostname" == "127.0.0.1" ]]; then
  preview_url="http://localhost:${local_port}/"
  if curl --fail --silent --max-time 2 \
    -H "Host: localhost:${local_port}" "$preview_url" >/dev/null 2>&1; then
    echo "SSH preview tunnel is already ready."
  else
    if command -v lsof >/dev/null 2>&1 \
      && lsof -nP -iTCP:"$local_port" -sTCP:LISTEN 2>/dev/null | grep -q .; then
      echo "Local port ${local_port} is already used by another process." >&2
      echo "Choose another port, for example:" >&2
      echo "  BMON_PREVIEW_PORT=18081 bash scripts/start-jetson-preview.sh" >&2
      exit 1
    fi

    echo "Opening the SSH preview tunnel..."
    ssh -fN -o BatchMode=yes -o ConnectTimeout=10 \
      -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
      -o ServerAliveCountMax=3 \
      -L "127.0.0.1:${local_port}:127.0.0.1:8080" "$jetson_host"
  fi
else
  preview_url="${public_origin%/}/"
  echo "Using the Jetson LAN address."
fi

echo "Baby monitor: $preview_url"
if command -v open >/dev/null 2>&1; then
  open "$preview_url"
fi
