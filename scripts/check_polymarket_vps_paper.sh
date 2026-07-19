#!/usr/bin/env sh
# Health check for the lean Polymarket VPS paper stack.

set -eu

REPO_DIR="${PM_VPS_REPO_DIR:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}"
COMPOSE_FILE="${PM_VPS_COMPOSE_FILE:-docker-compose.vps-paper.yml}"

# A tilde produced by variable expansion is not expanded by the shell. Accept
# the documented/default ~/Claude form without dynamic command execution, and
# leave absolute/custom paths untouched.
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

dashboard_rerender_hint() {
  printf '%s\n' "  Repair: docker compose -f $COMPOSE_FILE exec -T polymarket-paper-live python scripts/render_polymarket_dashboard.py --config /app/polymarket_predictive_config.example.yaml"
}

env_value() {
  key="$1"
  file="$2"
  if [ -f "$file" ]; then
    grep -E "^${key}=" "$file" | tail -n 1 | cut -d= -f2- || true
  fi
}

file_age_seconds() {
  path="$1"
  if [ ! -f "$path" ]; then
    printf ''
    return
  fi
  now="$(date +%s)"
  mtime="$(date -r "$path" +%s)"
  printf '%s' "$((now - mtime))"
}

cd "$REPO_DIR"

port="$(env_value POLYMARKET_DASHBOARD_PORT .env)"
port="${port:-8765}"
dashboard_url="http://127.0.0.1:${port}/"
private_dashboard_url="$(env_value PM_DASHBOARD_PUBLIC_URL .env)"
heartbeat="outputs/polymarket_model_governance/local_live_loop_heartbeat.json"
forward_cycle="outputs/polymarket_model_governance/forward_paper_cycle.json"
dashboard_data="outputs/polymarket_dashboard/dashboard_data.json"
repo_head="$(git rev-parse --short HEAD 2>/dev/null || printf 'unknown')"
deployed_sha="$(env_value PM_VPS_DEPLOYED_SHA .env)"

printf '%s\n' "Polymarket VPS paper stack"
printf '%s\n' "Repo: $REPO_DIR"
printf '%s\n' "Repo HEAD: $repo_head"
if [ -n "${deployed_sha:-}" ]; then
  printf '%s\n' "Deploy SHA: $deployed_sha"
fi
printf '%s\n' "Compose: $COMPOSE_FILE"
printf '%s\n' "Dashboard backend: $dashboard_url"
printf '%s\n' "Dashboard private HTTPS: ${private_dashboard_url:-not configured}"

docker_cmd compose -f "$COMPOSE_FILE" ps

printf '%s\n' ""
printf '%s\n' "Private dashboard transport:"
transport_tmp="$(mktemp -d)"
trap 'rm -rf "$transport_tmp"' EXIT INT TERM
if ! command -v tailscale >/dev/null 2>&1; then
  printf '%s\n' "  FAIL: Tailscale is not installed; public dashboard exposure remains forbidden."
  exit_code=1
else
  if ! $SUDO tailscale status --json > "$transport_tmp/tailscale-status.json"; then
    printf '%s\n' "  FAIL: Tailscale status is unavailable or the node is logged out."
    exit_code=1
  fi
  if ! $SUDO tailscale serve status > "$transport_tmp/tailscale-serve-status.txt"; then
    printf '%s\n' "  FAIL: Tailscale Serve status is unavailable."
    exit_code=1
  fi
  $SUDO tailscale funnel status >> "$transport_tmp/tailscale-serve-status.txt" 2>/dev/null || true
  if ! docker_cmd port polymarket-dashboard 8765/tcp > "$transport_tmp/dashboard-bindings.txt"; then
    printf '%s\n' "  FAIL: dashboard Docker binding is unavailable."
    exit_code=1
  fi
  if [ -f "$transport_tmp/tailscale-status.json" ] \
    && [ -f "$transport_tmp/tailscale-serve-status.txt" ] \
    && [ -f "$transport_tmp/dashboard-bindings.txt" ]; then
    if python3 scripts/validate_dashboard_private_transport.py \
      --tailscale-status "$transport_tmp/tailscale-status.json" \
      --serve-status "$transport_tmp/tailscale-serve-status.txt" \
      --docker-bindings "$transport_tmp/dashboard-bindings.txt" \
      --expected-port "$port" \
      --configured-url "$private_dashboard_url" \
      --output outputs/performance/dashboard_private_transport.json; then
      printf '%s\n' "  PASS: loopback-only Docker binding and authenticated tailnet HTTPS verified."
    else
      printf '%s\n' "  FAIL: private HTTPS transport did not satisfy the registered checks."
      exit_code=1
    fi
  fi
fi

printf '%s\n' ""
printf '%s\n' "Secrets check (.env -> container):"
odds_key_env="$(env_value THE_ODDS_API_KEY .env)"
if [ -n "$odds_key_env" ]; then
  printf '%s\n' "  THE_ODDS_API_KEY: set in .env (${#odds_key_env} chars)"
else
  printf '%s\n' "  THE_ODDS_API_KEY: MISSING/empty in .env - sharp-anchor pipeline cannot run."
  printf '%s\n' "  Fix either by running the GitHub deploy workflow after PM_VPS_SSH_PRIVATE_KEY is set,"
  printf '%s\n' "  or by editing .env in this directory by hand:"
  printf '%s\n' "    THE_ODDS_API_KEY=<key from the-odds-api.com>"
  printf '%s\n' "    docker compose -f $COMPOSE_FILE up -d --force-recreate polymarket-paper-live"
fi
if docker_cmd exec polymarket-paper-live sh -c 'test -n "$THE_ODDS_API_KEY"' >/dev/null 2>&1; then
  printf '%s\n' "  THE_ODDS_API_KEY: visible inside polymarket-paper-live container"
else
  printf '%s\n' "  THE_ODDS_API_KEY: NOT visible inside the container (set .env then force-recreate; a restart alone does not reload env_file)"
fi

printf '%s\n' ""
printf '%s\n' "Container memory snapshot:"
docker_cmd stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}' || true

printf '%s\n' ""
dashboard_tmp="${TMPDIR:-/tmp}/polymarket-dashboard-check.$$"
if curl -fsS --max-time 5 "$dashboard_url" -o "$dashboard_tmp" >/dev/null 2>&1; then
  printf '%s\n' "Dashboard: ok"
  if grep -q "Proof status" "$dashboard_tmp"; then
    printf '%s\n' "Dashboard proof gate: ok"
  else
    printf '%s\n' "Dashboard proof gate: missing (dashboard may be stale; redeploy or rerender)"
    dashboard_rerender_hint
    exit_code=1
  fi
  if grep -q "Evidence funnel" "$dashboard_tmp"; then
    printf '%s\n' "Dashboard evidence funnel: ok"
  else
    printf '%s\n' "Dashboard evidence funnel: missing (dashboard may be stale; redeploy or rerender)"
    dashboard_rerender_hint
    exit_code=1
  fi
else
  printf '%s\n' "Dashboard: not responding locally"
  exit_code=1
fi
rm -f "$dashboard_tmp"

if [ -f "$dashboard_data" ]; then
  if grep -q "profit_target_proof_status" "$dashboard_data"; then
    printf '%s\n' "Dashboard proof data: ok"
  else
    printf '%s\n' "Dashboard proof data: missing (dashboard data predates the verified-profit gate)"
    dashboard_rerender_hint
    exit_code=1
  fi
  if grep -q "deployment_health" "$dashboard_data"; then
    printf '%s\n' "Dashboard deployment health: ok"
  else
    printf '%s\n' "Dashboard deployment health: missing (dashboard data predates deploy-health checks)"
    dashboard_rerender_hint
    exit_code=1
  fi
  if grep -q "mispricing_alpha_bridge" "$dashboard_data"; then
    printf '%s\n' "Dashboard alpha bridge data: ok"
  else
    printf '%s\n' "Dashboard alpha bridge data: missing (dashboard data predates sharp-alpha bridge diagnostics)"
    dashboard_rerender_hint
    exit_code=1
  fi
  if grep -q "coverage_by_sport_market" "$dashboard_data"; then
    printf '%s\n' "Dashboard sharp-anchor coverage data: ok"
  else
    printf '%s\n' "Dashboard sharp-anchor coverage data: missing (dashboard data predates per-sport anchor diagnostics)"
    dashboard_rerender_hint
    exit_code=1
  fi
  if grep -q "alpha_validated_anchor_rows" "$dashboard_data"; then
    printf '%s\n' "Dashboard alpha-validated anchor data: ok"
  else
    printf '%s\n' "Dashboard alpha-validated anchor data: missing (dashboard data predates broad sharp-anchor Strategy V2 diagnostics)"
    dashboard_rerender_hint
    exit_code=1
  fi
  if grep -q "sharp_sports_funnel" "$dashboard_data"; then
    printf '%s\n' "Dashboard sharp-sports funnel data: ok"
  else
    printf '%s\n' "Dashboard sharp-sports funnel data: missing (dashboard data predates sharp sports edge funnel diagnostics)"
    dashboard_rerender_hint
    exit_code=1
  fi
else
  printf '%s\n' "Dashboard proof data: missing"
  exit_code=1
fi

heartbeat_age="$(file_age_seconds "$heartbeat")"
forward_age="$(file_age_seconds "$forward_cycle")"
dashboard_age="$(file_age_seconds "$dashboard_data")"

printf '%s\n' "Live heartbeat age: ${heartbeat_age:-missing}s"
printf '%s\n' "Forward paper cycle age: ${forward_age:-missing}s"
printf '%s\n' "Dashboard data age: ${dashboard_age:-missing}s"

if [ -z "${heartbeat_age:-}" ] && [ -z "${forward_age:-}" ]; then
  printf '%s\n' "No paper heartbeat files exist yet."
  exit_code=1
fi

rm -rf "$transport_tmp"
trap - EXIT INT TERM
exit "${exit_code:-0}"
