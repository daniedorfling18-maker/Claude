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
    value="$(grep -E "^${key}=" "$file" | tail -n 1 | cut -d= -f2- || true)"
    case "$value" in
      \"*\") value="${value#\"}"; value="${value%\"}" ;;
      \'*\') value="${value#\'}"; value="${value%\'}" ;;
    esac
    printf '%s' "$value"
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

# WO-122 (OPS-15): this script computed three ages and compared NONE of them, so
# a completely frozen stack passed the gate as long as HTTP answered and the old
# JSON still contained the right keys - and this is the gate the guarded deploy
# and the automatic rollback both verify against. Ceilings are registered here;
# an environment override may only TIGHTEN one.
#
# mtime is the right signal at this layer (a shell gate on the host); the
# content-honest, timestamp-based version of the same question lives in the
# operating-state SLO block and the WO-121 watchdog registrations.
HEARTBEAT_MAX_AGE_REGISTERED=900
FORWARD_CYCLE_MAX_AGE_REGISTERED=93600
DASHBOARD_DATA_MAX_AGE_REGISTERED=1800

tighter_of() {
  # $1 = registered ceiling, $2 = requested override ("" keeps the ceiling)
  registered="$1"
  requested="$2"
  case "${requested:-}" in
    ''|*[!0-9]*) printf '%s' "$registered"; return ;;
  esac
  if [ "$requested" -lt "$registered" ] && [ "$requested" -gt 0 ]; then
    printf '%s' "$requested"
  else
    printf '%s' "$registered"
  fi
}

age_is_valid() {
  # Codex review of #363 (P2): a restored artifact with a FUTURE mtime, or a host
  # clock that moved backward, makes file_age_seconds negative - and a negative
  # age passes every `-le ceiling` test, so a frozen artifact reads fresh until
  # wall time catches up to its timestamp. A negative age is not evidence of
  # freshness, it is evidence the clock or the file is wrong.
  case "${1:-}" in
    ''|-*|*[!0-9]*) return 1 ;;
  esac
  return 0
}

assert_age_within() {
  # $1 = label, $2 = measured age ("" = missing), $3 = ceiling
  label="$1"
  measured="$2"
  ceiling="$3"
  if [ -z "${measured:-}" ]; then
    printf '%s\n' "  FAIL: $label is missing; freshness cannot be verified."
    exit_code=1
    return
  fi
  if ! age_is_valid "$measured"; then
    printf '%s\n' "  FAIL: $label reports an invalid age (${measured}s); a future mtime or clock backstep cannot prove freshness."
    exit_code=1
    return
  fi
  if [ "$measured" -gt "$ceiling" ]; then
    printf '%s\n' "  FAIL: $label is ${measured}s old, above its ${ceiling}s ceiling."
    exit_code=1
  else
    printf '%s\n' "  ok: $label ${measured}s (ceiling ${ceiling}s)"
  fi
}

# WO-122: allow the freshness helpers to be sourced and exercised directly,
# matching the OPS_SCHEDULER_LIBRARY_ONLY pattern. Nothing below this point runs
# under it, so a test never needs Docker or Tailscale to verify the comparisons.
if [ "${PM_HEALTH_LIBRARY_ONLY:-0}" = "1" ]; then
  return 0 2>/dev/null || exit 0
fi

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
printf '{}\n' > "$transport_tmp/tailscale-status.json"
: > "$transport_tmp/tailscale-serve-status.txt"
: > "$transport_tmp/dashboard-bindings.txt"
transport_inputs_ready=true
if ! command -v tailscale >/dev/null 2>&1; then
  printf '%s\n' "  FAIL: Tailscale is not installed; public dashboard exposure remains forbidden."
  exit_code=1
  transport_inputs_ready=false
else
  if ! $SUDO tailscale status --json > "$transport_tmp/tailscale-status.json"; then
    printf '%s\n' "  FAIL: Tailscale status is unavailable or the node is logged out."
    exit_code=1
    transport_inputs_ready=false
  fi
  if ! $SUDO tailscale serve status > "$transport_tmp/tailscale-serve-status.txt"; then
    printf '%s\n' "  FAIL: Tailscale Serve status is unavailable."
    exit_code=1
    transport_inputs_ready=false
  fi
  # WO-122 (OPS-17): the funnel probe was `|| true`, so a failed capture left the
  # serve-status text with no funnel evidence at all - and the validator's
  # funnel-enabled blocker looks for POSITIVE strings, meaning "could not check"
  # read exactly like "funnel is off". Public exposure is the one thing this gate
  # exists to prevent, so an unprovable funnel state now fails closed.
  #
  # `tailscale funnel status` exits nonzero on some versions when no funnel is
  # configured, which is the state we want to pass. Codex review of #363 (P1)
  # caught the first attempt at distinguishing those: merging stderr into the
  # capture and testing for non-empty output means a permissions, daemon or
  # unsupported-command error ALSO produces output, and that error text then goes
  # to a validator that only matches positive funnel phrases - so "could not
  # check" passed as "funnel is off" again.
  #
  # Keep the streams separate and accept only two provable outcomes: exit 0, or a
  # nonzero exit whose stdout is the recognised no-configuration response. Any
  # other command error fails closed.
  funnel_out="$transport_tmp/tailscale-funnel-status.txt"
  funnel_err="$transport_tmp/tailscale-funnel-status.err"
  funnel_ok=false
  if $SUDO tailscale funnel status > "$funnel_out" 2> "$funnel_err"; then
    funnel_ok=true
  elif grep -qiE 'no funnel (config|configuration)|funnel is not (configured|enabled)|no serve config' "$funnel_out"; then
    # A recognised "nothing configured" answer on stdout is a real observation.
    funnel_ok=true
  fi
  if [ "$funnel_ok" = true ]; then
    cat "$funnel_out" >> "$transport_tmp/tailscale-serve-status.txt"
  else
    printf '%s\n' "  FAIL: Tailscale funnel status could not be established; public exposure cannot be ruled out."
    printf '%s\n' "  Command error: $(head -n 1 "$funnel_err" 2>/dev/null || printf 'no stderr captured')"
    exit_code=1
    transport_inputs_ready=false
  fi
  if ! docker_cmd port polymarket-dashboard 8765/tcp > "$transport_tmp/dashboard-bindings.txt"; then
    printf '%s\n' "  FAIL: dashboard Docker binding is unavailable."
    exit_code=1
    transport_inputs_ready=false
  fi
fi

private_https_reachable=false
if [ "$transport_inputs_ready" = true ]; then
  probe_url="$(python3 - "$transport_tmp/tailscale-status.json" <<'PY'
import json
import sys

try:
    payload = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    payload = {}
dns_name = str((payload.get("Self") or {}).get("DNSName") or "").strip().rstrip(".").lower()
print(f"https://{dns_name}/" if dns_name.endswith(".ts.net") else "")
PY
)"
  if [ -n "$probe_url" ] && curl -fsS --max-time 10 "$probe_url" >/dev/null 2>&1; then
    private_https_reachable=true
  fi
fi

if [ "$private_https_reachable" = true ]; then
  probe_argument="--private-https-reachable"
else
  probe_argument=""
fi
if python3 scripts/validate_dashboard_private_transport.py \
  --tailscale-status "$transport_tmp/tailscale-status.json" \
  --serve-status "$transport_tmp/tailscale-serve-status.txt" \
  --docker-bindings "$transport_tmp/dashboard-bindings.txt" \
  --expected-port "$port" \
  --configured-url "$private_dashboard_url" \
  ${probe_argument:+$probe_argument} \
  --output outputs/performance/dashboard_private_transport.json; then
  printf '%s\n' "  PASS: loopback-only Docker binding and reachable authenticated tailnet HTTPS verified."
else
  printf '%s\n' "  FAIL: private HTTPS transport did not satisfy the registered checks."
  exit_code=1
fi

printf '%s\n' ""
printf '%s\n' "Secrets check (.env -> container):"
odds_key_env="$(env_value THE_ODDS_API_KEY .env)"
# WO-122 (OPS-16): this block printed its findings and always exited 0, so a
# stack whose sharp-anchor lane could not run at all passed the deploy gate.
if [ -n "$odds_key_env" ]; then
  printf '%s\n' "  THE_ODDS_API_KEY: set in .env (${#odds_key_env} chars)"
else
  printf '%s\n' "  FAIL: THE_ODDS_API_KEY is MISSING/empty in .env - the sharp-anchor pipeline cannot run."
  printf '%s\n' "  Fix either by running the GitHub deploy workflow after PM_VPS_SSH_PRIVATE_KEY is set,"
  printf '%s\n' "  or by editing .env in this directory by hand:"
  printf '%s\n' "    THE_ODDS_API_KEY=<key from the-odds-api.com>"
  printf '%s\n' "    docker compose -f $COMPOSE_FILE up -d --force-recreate polymarket-paper-live"
  exit_code=1
fi
if docker_cmd exec polymarket-paper-live sh -c 'test -n "$THE_ODDS_API_KEY"' >/dev/null 2>&1; then
  printf '%s\n' "  THE_ODDS_API_KEY: visible inside polymarket-paper-live container"
else
  # A key present on the host but absent in the container is a deploy defect,
  # not a configuration preference: env_file was not reloaded.
  printf '%s\n' "  FAIL: THE_ODDS_API_KEY is NOT visible inside the container (set .env then force-recreate; a restart alone does not reload env_file)"
  exit_code=1
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

printf '%s\n' ""
printf '%s\n' "Freshness ceilings (WO-122):"
# Codex review of #363 (P1): WO-122 taught the dashboard container to record a
# failed startup render in render_failed.json and keep serving the stale
# generation - but nothing read the marker, so the health gate could still pass a
# stale cockpit during bootstrap or rollback whenever the old dashboard_data.json
# was recent enough and still carried the expected schema keys. A marker with no
# consumer is the exact defect class this batch exists to remove.
render_marker="outputs/polymarket_dashboard/render_failed.json"
if [ -f "$render_marker" ]; then
  printf '%s\n' "  FAIL: the dashboard container's startup render failed; the served generation is stale."
  printf '%s\n' "  Marker: $render_marker"
  dashboard_rerender_hint
  exit_code=1
else
  printf '%s\n' "  ok: no dashboard render failure recorded for the current container start"
fi
heartbeat_ceiling="$(tighter_of "$HEARTBEAT_MAX_AGE_REGISTERED" "${PM_HEALTH_HEARTBEAT_MAX_AGE_SECONDS:-}")"
forward_ceiling="$(tighter_of "$FORWARD_CYCLE_MAX_AGE_REGISTERED" "${PM_HEALTH_FORWARD_CYCLE_MAX_AGE_SECONDS:-}")"
dashboard_ceiling="$(tighter_of "$DASHBOARD_DATA_MAX_AGE_REGISTERED" "${PM_HEALTH_DASHBOARD_MAX_AGE_SECONDS:-}")"
# The live loop and the daily forward cycle are alternative liveness evidence on
# a freshly started stack, so require the FRESHER of the two to be inside its
# own ceiling rather than demanding both.
if age_is_valid "${heartbeat_age:-}" && [ "$heartbeat_age" -le "$heartbeat_ceiling" ]; then
  printf '%s\n' "  ok: live heartbeat ${heartbeat_age}s (ceiling ${heartbeat_ceiling}s)"
elif age_is_valid "${forward_age:-}" && [ "$forward_age" -le "$forward_ceiling" ]; then
  printf '%s\n' "  ok: forward paper cycle ${forward_age}s (ceiling ${forward_ceiling}s)"
  printf '%s\n' "  note: live heartbeat is ${heartbeat_age:-missing}s (above ${heartbeat_ceiling}s)"
else
  printf '%s\n' "  FAIL: neither the live heartbeat (${heartbeat_age:-missing}s / ${heartbeat_ceiling}s) nor the forward paper cycle (${forward_age:-missing}s / ${forward_ceiling}s) is fresh."
  exit_code=1
fi
assert_age_within "dashboard data" "${dashboard_age:-}" "$dashboard_ceiling"

rm -rf "$transport_tmp"
trap - EXIT INT TERM
exit "${exit_code:-0}"
