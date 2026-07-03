#!/usr/bin/env sh
# Health check for the lean Polymarket VPS paper stack.

set -eu

REPO_DIR="${PM_VPS_REPO_DIR:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}"
COMPOSE_FILE="${PM_VPS_COMPOSE_FILE:-docker-compose.vps-paper.yml}"

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
printf '%s\n' "Dashboard: $dashboard_url"

docker_cmd compose -f "$COMPOSE_FILE" ps

printf '%s\n' ""
printf '%s\n' "Secrets check (.env -> container):"
odds_key_env="$(env_value THE_ODDS_API_KEY .env)"
if [ -n "$odds_key_env" ]; then
  printf '%s\n' "  THE_ODDS_API_KEY: set in .env (${#odds_key_env} chars)"
else
  printf '%s\n' "  THE_ODDS_API_KEY: MISSING/empty in .env - sharp-anchor pipeline cannot run."
  printf '%s\n' "  GitHub Actions secrets do NOT reach this VPS. Fix:"
  printf '%s\n' "    1. edit .env in this directory: THE_ODDS_API_KEY=<key from the-odds-api.com>"
  printf '%s\n' "    2. docker compose -f $COMPOSE_FILE up -d --force-recreate polymarket-paper-live"
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
    exit_code=1
  fi
  if grep -q "Evidence funnel" "$dashboard_tmp"; then
    printf '%s\n' "Dashboard evidence funnel: ok"
  else
    printf '%s\n' "Dashboard evidence funnel: missing (dashboard may be stale; redeploy or rerender)"
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
    exit_code=1
  fi
  if grep -q "deployment_health" "$dashboard_data"; then
    printf '%s\n' "Dashboard deployment health: ok"
  else
    printf '%s\n' "Dashboard deployment health: missing (dashboard data predates deploy-health checks)"
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

exit "${exit_code:-0}"
