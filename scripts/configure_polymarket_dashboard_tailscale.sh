#!/usr/bin/env sh
# Configure authenticated, tailnet-only HTTPS for the VPS oversight dashboard.

set -eu

REPO_DIR="${PM_VPS_REPO_DIR:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}"
COMPOSE_FILE="${PM_VPS_COMPOSE_FILE:-docker-compose.vps-paper.yml}"

case "$REPO_DIR" in
  "~") REPO_DIR="$HOME" ;;
  "~/"*) REPO_DIR="$HOME${REPO_DIR#?}" ;;
esac

if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
else
  SUDO="sudo"
fi

docker_cmd() {
  if docker info >/dev/null 2>&1; then
    docker "$@"
  else
    $SUDO docker "$@"
  fi
}

if ! command -v tailscale >/dev/null 2>&1; then
  printf '%s\n' "Tailscale is not installed. Install it from https://tailscale.com/download/linux, run 'sudo tailscale up', then rerun this script." >&2
  exit 2
fi

cd "$REPO_DIR"
if [ ! -f .env ]; then
  printf '%s\n' "Missing $REPO_DIR/.env; deploy/bootstrap the paper stack first." >&2
  exit 2
fi

tmp_dir="$(mktemp -d)"
env_backup="$tmp_dir/.env.pre-private-transport"
install -m 0600 .env "$env_backup"
env_committed=false

restore_env_on_failure() {
  status=$?
  trap - EXIT INT TERM
  if [ "$status" -ne 0 ] && [ "$env_committed" != true ]; then
    printf '%s\n' "Private dashboard setup failed; restoring the pre-setup environment." >&2
    install -m 0600 "$env_backup" .env 2>/dev/null || true
  fi
  rm -rf "$tmp_dir"
  exit "$status"
}

trap 'restore_env_on_failure' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
status_json="$tmp_dir/tailscale-status.json"
serve_status="$tmp_dir/tailscale-serve-status.txt"
docker_bindings="$tmp_dir/dashboard-bindings.txt"

$SUDO tailscale status --json > "$status_json"
state_and_dns="$(python3 - "$status_json" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
print(str(payload.get("BackendState") or ""))
print(str((payload.get("Self") or {}).get("DNSName") or "").strip().rstrip("."))
PY
)"
backend_state="$(printf '%s\n' "$state_and_dns" | sed -n '1p')"
dns_name="$(printf '%s\n' "$state_and_dns" | sed -n '2p')"
if [ "$backend_state" != "Running" ] || [ -z "$dns_name" ]; then
  printf '%s\n' "Tailscale is not authenticated (state=${backend_state:-unknown}). Run 'sudo tailscale up', complete the browser login, then rerun." >&2
  exit 3
fi
case "$dns_name" in
  *.ts.net) ;;
  *) printf '%s\n' "Refusing non-Tailscale dashboard DNS name: $dns_name" >&2; exit 3 ;;
esac

port="$(grep -E '^POLYMARKET_DASHBOARD_PORT=' .env | tail -n 1 | cut -d= -f2- || true)"
port="${port:-8765}"
case "$port" in
  *[!0-9]*|'') printf '%s\n' "Invalid POLYMARKET_DASHBOARD_PORT: $port" >&2; exit 2 ;;
esac
if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
  printf '%s\n' "POLYMARKET_DASHBOARD_PORT must be between 1 and 65535: $port" >&2
  exit 2
fi
private_url="https://${dns_name}/"

ENV_PATH="$REPO_DIR/.env" PRIVATE_URL="$private_url" python3 - <<'PY'
import os
from pathlib import Path

path = Path(os.environ["ENV_PATH"])
updates = {
    "POLYMARKET_DASHBOARD_HOST": "127.0.0.1",
    "PM_DASHBOARD_PUBLIC_URL": os.environ["PRIVATE_URL"],
}
lines = path.read_text(encoding="utf-8").splitlines()
rendered = []
seen = set()
for line in lines:
    if not line.lstrip().startswith("#") and "=" in line:
        key = line.split("=", 1)[0]
        if key in updates:
            rendered.append(f"{key}={updates[key]}")
            seen.add(key)
            continue
    rendered.append(line)
for key, value in updates.items():
    if key not in seen:
        rendered.append(f"{key}={value}")
temporary = path.with_name(f".{path.name}.private-transport.tmp")
temporary.write_text("\n".join(rendered) + "\n", encoding="utf-8")
temporary.chmod(0o600)
temporary.replace(path)
PY

# Recreate only the reporting container so the old public host binding closes
# before private Serve is enabled. No collector, scheduler, broker, or model is
# restarted by this setup action.
docker_cmd compose -f "$COMPOSE_FILE" up -d --no-build --force-recreate polymarket-dashboard
if ! curl -fsS --max-time 10 "http://127.0.0.1:${port}/" >/dev/null; then
  printf '%s\n' "Dashboard backend did not answer on loopback; private Serve was not configured." >&2
  exit 4
fi

# A Serve command is tailnet-only. Explicitly turn Funnel off first; the final
# command for port 443 is Serve, never Funnel.
$SUDO tailscale funnel --https=443 off >/dev/null 2>&1 || true
$SUDO tailscale serve --bg --yes --https=443 "http://127.0.0.1:${port}"
$SUDO tailscale status --json > "$status_json"
$SUDO tailscale serve status > "$serve_status"
$SUDO tailscale funnel status >> "$serve_status" 2>/dev/null || true
docker_cmd port polymarket-dashboard 8765/tcp > "$docker_bindings"

python3 scripts/validate_dashboard_private_transport.py \
  --tailscale-status "$status_json" \
  --serve-status "$serve_status" \
  --docker-bindings "$docker_bindings" \
  --expected-port "$port" \
  --configured-url "$private_url" \
  --output outputs/performance/dashboard_private_transport.json

attempt=0
while [ "$attempt" -lt 30 ]; do
  if curl -fsS --max-time 10 "$private_url" >/dev/null 2>&1; then
    printf '%s\n' "Private dashboard ready: $private_url"
    printf '%s\n' "Install Tailscale on your phone, join the same tailnet, then open that HTTPS URL."
    env_committed=true
    exit 0
  fi
  attempt=$((attempt + 1))
  sleep 2
done
printf '%s\n' "Tailscale Serve is configured but $private_url did not answer within 60 seconds." >&2
exit 5
