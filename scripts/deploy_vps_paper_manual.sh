#!/usr/bin/env bash
# WO-133 — guarded manual deploy for the Polymarket VPS paper stack.
#
# Path B of the two registered deploy routes. Path A (the `Deploy Polymarket VPS
# Paper` workflow) remains REQUIRED whenever GitHub Actions can run it, because
# it binds the deployed SHA to an independently reviewed acceptance run. This
# script exists for when that path is unavailable, and it is deliberately NOT a
# way around the acceptance gate:
#
#   * it refuses unless the target commit is exactly the freshly fetched
#     `origin/main` tip, so it can only ever deploy reviewed, merged code; and
#   * it records `attestation_verified: false` in its deploy record, naming the
#     check it could not perform, because `verify_independent_main_acceptance.py`
#     needs a GitHub token and the acceptance run's artifact, neither of which a
#     VPS shell has. The unprovable step is reported as unproven. It is never
#     silently treated as proven, and this script never writes an attestation.
#
# An ad-hoc `git pull` + rebuild remains forbidden: it skips every guard below.
#
# Sourcing seam: `PM_MANUAL_DEPLOY_LIBRARY_ONLY=1` loads the functions without
# running a deploy, so the guard ORDER is tested for real rather than grepped.

set -euo pipefail

REPO_DIR="${PM_VPS_REPO_DIR:-/home/opc/Claude}"
COMPOSE_FILE="${PM_COMPOSE_FILE:-docker-compose.vps-paper.yml}"
DOCKER="${PM_DOCKER:-docker}"
DEPLOY_RECORD_RELATIVE="performance/vps_manual_deploy.json"
ROLLBACK_IMAGE_TAG="polymarket-paper-vps:rollback-last-known-good"

# The deployed-SHA marker lives under outputs/, NOT at the repository root. This
# path is not a local choice: the deploy workflow, rollback_vps_paper_deploy.py,
# bootstrap_polymarket_vps_paper.sh and write_vps_telemetry_manifest.py all read
# and write exactly this file, and a Path B that used a different location would
# refuse on a correctly deployed host while leaving the real marker stale after a
# successful run. See test_wo133_marker_path_matches_every_other_consumer.
DEPLOYED_MARKER_RELATIVE="outputs/performance/deployed_git_rev"
deployed_marker() { printf '%s/%s' "$REPO_DIR" "$DEPLOYED_MARKER_RELATIVE"; }

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

# --- guard helpers come from the TARGET revision, not the old checkout ---------
# The checkout being deployed FROM is by definition out of date - that is why a
# deploy is running. Reading the guards out of it would run the previous
# revision's preflight, transport proof, health gate and rollback against the new
# code, silently skipping any hardening the target added. Path A avoids this by
# extracting each helper from the target SHA; Path B does the same.
STAGE_DIR=""
stage_target_scripts() {
  target_sha="$1"
  STAGE_DIR="$(mktemp -d)"
  chmod 0700 "$STAGE_DIR"
  for helper in \
    preflight_vps_capacity.py \
    validate_dashboard_private_transport.py \
    update_vps_checkout_preserving_runtime.py \
    rollback_vps_paper_deploy.py \
    write_vps_telemetry_manifest.py \
    check_polymarket_vps_paper.sh
  do
    git -C "$REPO_DIR" show "$target_sha:scripts/$helper" > "$STAGE_DIR/$helper" \
      || fail "cannot stage scripts/$helper from $target_sha"
  done
  # Capacity must be measured against the TARGET revision's compose commitments,
  # not the running stack's - a deploy that adds a service or raises a memory
  # reservation has to be refused BEFORE it is built, which is why Path A stages
  # the compose file the same way.
  git -C "$REPO_DIR" show "$target_sha:$COMPOSE_FILE" > "$STAGE_DIR/compose.yml" \
    || fail "cannot stage $COMPOSE_FILE from $target_sha"
  chmod 0700 "$STAGE_DIR"/*.py "$STAGE_DIR"/*.sh
  log "staged target-revision guards from $target_sha in $STAGE_DIR"
}
fail() { log "ERROR: $*" >&2; exit 1; }

if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi

# Same reader check_polymarket_vps_paper.sh uses, so Path B resolves the
# dashboard port and private URL exactly as the health gate does.
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

# --- guard 1: the target must be the reviewed, merged main tip -----------------
# This is what keeps Path B from becoming a way to ship unreviewed code. A
# detached local commit, a stale checkout, or a feature branch all refuse here,
# BEFORE anything on the host is touched.
assert_target_is_origin_main() {
  git -C "$REPO_DIR" fetch --quiet origin main || fail "cannot fetch origin/main"
  remote_sha="$(git -C "$REPO_DIR" rev-parse origin/main)"
  target_sha="${PM_DEPLOY_TARGET_SHA:-$remote_sha}"
  if [ "$target_sha" != "$remote_sha" ]; then
    fail "target $target_sha is not the current origin/main tip $remote_sha; \
Path B deploys reviewed merged main only"
  fi
  printf '%s' "$target_sha"
}

# --- guard 2: checkout and deployed marker must agree before quiescing ---------
assert_checkout_matches_marker() {
  marker_file="$(deployed_marker)"
  [ -f "$marker_file" ] || fail "deployed marker $marker_file is missing; refusing to quiesce"
  marker_sha="$(tr -d '[:space:]' < "$marker_file")"
  head_sha="$(git -C "$REPO_DIR" rev-parse HEAD)"
  [ -n "$marker_sha" ] || fail "deployed marker is empty; refusing to quiesce"
  if [ "$marker_sha" != "$head_sha" ]; then
    fail "checkout $head_sha does not match deployed marker $marker_sha; \
resolve the drift before deploying"
  fi
  # write_deploy_markers runs AFTER the arming boundary, so an unwritable marker
  # or output directory would arm the deploy, quiesce the stack, then fail and
  # roll back. The containers write under outputs/ as root, so this is a real
  # possibility rather than a theoretical one. Prove the write here, where a
  # refusal is free.
  [ -w "$marker_file" ] || fail "deployed marker $marker_file is not writable by $(id -un); \
the marker write happens after the arming boundary, so this refuses now instead of rolling back"
  [ -w "$(dirname "$marker_file")" ] || fail "$(dirname "$marker_file") is not writable by $(id -un); \
the marker write happens after the arming boundary, so this refuses now instead of rolling back"
  [ -w "$REPO_DIR/.env" ] || fail "$REPO_DIR/.env is not writable by $(id -un); \
PM_VPS_DEPLOYED_SHA is stamped after the arming boundary, so this refuses now instead of rolling back"
}

# --- guard 3: capacity ---------------------------------------------------------
# Argument shape is Path A's (workflow :297-301), against the STAGED target
# compose. Unlike Path A this does NOT prune the Docker builder cache or dangling
# images first: pruning mutates the host, and everything before the arming
# boundary must leave the host untouched so a refusal costs nothing. The refusal
# therefore names the reclaim commands instead of running them behind your back.
run_capacity_preflight() {
  ( cd "$REPO_DIR" && python3 "$STAGE_DIR/preflight_vps_capacity.py" \
      --compose "$STAGE_DIR/compose.yml" \
      --env-file .env \
      --output outputs/performance/vps_capacity_preflight.json \
      --root . ) \
    || fail "capacity preflight refused the deploy. Path A reclaims space first and \
Path B deliberately does not (it must not mutate the host before arming). If the \
shortfall is dangling build layers, reclaim them yourself and re-run: \
'$DOCKER builder prune --force && $DOCKER image prune --force'"
}

# --- guard 4: private transport proof, BEFORE the stack is quiesced ------------
# Public exposure is the single thing this gate exists to prevent, and proving it
# after quiescing would mean tearing down a healthy stack to discover the new one
# must not be started. An uncapturable transport state fails closed.
#
# The validator does not gather anything itself - it judges three captured files.
# The capture below is check_polymarket_vps_paper.sh's (:85-145), because that is
# the caller already proven against this validator. The report is written into the
# staging directory rather than outputs/, so this guard leaves the host untouched.
DASHBOARD_PRIVATE_URL=""
DASHBOARD_PORT=""
assert_private_transport() {
  transport_tmp="$STAGE_DIR/transport"
  mkdir -p "$transport_tmp"
  printf '{}\n' > "$transport_tmp/tailscale-status.json"
  : > "$transport_tmp/tailscale-serve-status.txt"
  : > "$transport_tmp/dashboard-bindings.txt"

  command -v tailscale >/dev/null 2>&1 \
    || fail "Tailscale is not installed; public dashboard exposure remains forbidden"
  $SUDO tailscale status --json > "$transport_tmp/tailscale-status.json" \
    || fail "Tailscale status is unavailable or the node is logged out; refusing to deploy"
  $SUDO tailscale serve status > "$transport_tmp/tailscale-serve-status.txt" \
    || fail "Tailscale Serve status is unavailable; refusing to deploy"
  # Funnel state is appended for the validator's public-exposure check. A funnel
  # probe that cannot be captured must not read as "funnel is off".
  $SUDO tailscale funnel status >> "$transport_tmp/tailscale-serve-status.txt" 2>/dev/null || true
  $DOCKER port polymarket-dashboard 8765/tcp > "$transport_tmp/dashboard-bindings.txt" \
    || fail "dashboard Docker binding is unavailable; refusing to deploy"

  DASHBOARD_PORT="$(env_value POLYMARKET_DASHBOARD_PORT "$REPO_DIR/.env")"
  DASHBOARD_PORT="${DASHBOARD_PORT:-8765}"
  case "$DASHBOARD_PORT" in
    *[!0-9]*|'') fail "invalid dashboard port '$DASHBOARD_PORT' in .env" ;;
  esac
  DASHBOARD_PRIVATE_URL="$(env_value PM_DASHBOARD_PUBLIC_URL "$REPO_DIR/.env")"
  [ -n "$DASHBOARD_PRIVATE_URL" ] \
    || fail "PM_DASHBOARD_PUBLIC_URL is not set in .env; the private transport cannot be verified"

  probe_argument=""
  if curl -fsS --max-time 10 "$DASHBOARD_PRIVATE_URL" >/dev/null 2>&1; then
    probe_argument="--private-https-reachable"
  fi

  python3 "$STAGE_DIR/validate_dashboard_private_transport.py" \
    --tailscale-status "$transport_tmp/tailscale-status.json" \
    --serve-status "$transport_tmp/tailscale-serve-status.txt" \
    --docker-bindings "$transport_tmp/dashboard-bindings.txt" \
    --expected-port "$DASHBOARD_PORT" \
    --configured-url "$DASHBOARD_PRIVATE_URL" \
    ${probe_argument:+$probe_argument} \
    --output "$transport_tmp/dashboard_private_transport.json" \
    || fail "dashboard private-transport proof failed; refusing to deploy"
}

# --- the arming boundary -------------------------------------------------------
# Everything after this can be undone by rollback_vps_paper_deploy.py, and
# everything before it has changed nothing on the host.
FAILED_TARGET_SHA=""
arm_rollback() {
  FAILED_TARGET_SHA="$1"
  ROLLBACK_DIR="$(mktemp -d)"
  chmod 0700 "$ROLLBACK_DIR"
  install -m 0600 "$REPO_DIR/.env" "$ROLLBACK_DIR/.env.last-known-good" \
    || fail "could not snapshot .env"
  install -m 0600 "$(deployed_marker)" "$ROLLBACK_DIR/deployed_git_rev.last-known-good" \
    || fail "could not snapshot the deployed marker"
  ORIGINAL_HEAD="$(git -C "$REPO_DIR" rev-parse HEAD)"
  $DOCKER image tag polymarket-paper-vps:latest "$ROLLBACK_IMAGE_TAG" \
    || fail "could not tag the running image for rollback"
  ROLLBACK_ARMED=true
  log "rollback armed at $ORIGINAL_HEAD (recovery material mode 0700 in $ROLLBACK_DIR)"
}

rollback_if_armed() {
  status="$1"
  if [ "$status" -ne 0 ] && [ "${ROLLBACK_ARMED:-false}" = true ]; then
    log "deploy failed after the arming boundary; restoring $ORIGINAL_HEAD"
    # Argument shape is Path A's (workflow :437-456). --marker-was-present is
    # unconditional here because guard 2 refuses a deploy whose marker is absent,
    # so by this point it always existed.
    #
    # Probe window. Observed 2026-07-28: the rollback restored the checkout,
    # .env, marker, image and all four containers correctly, then reported FAIL
    # because a just-recreated dashboard behind Tailscale Serve had not answered
    # within the default 5 x 2s. The same URL returned 200 shortly after. A cold
    # start is not a failed rollback, and reporting one as
    # MANUAL_INTERVENTION_REQUIRED sends an operator to inspect a healthy stack.
    #
    # The window is bought with the INTERVAL, not the attempt count, because
    # _verify_private_dashboard_transport clamps both:
    # range(max(1, min(10, probe_attempts))) and min(10.0, probe_interval_seconds).
    # Asking for 30 attempts silently yields 10. 10 attempts at 7s is a ~63s
    # retry span and stays inside the helper's own bounds, so Path A's shared
    # defaults are untouched. See
    # test_wo133_rollback_probe_window_is_at_least_sixty_effective_seconds, which
    # reads those clamps rather than trusting the flags.
    #
    # This does not weaken the check: it still fails closed if the dashboard
    # genuinely never answers.
    python3 "$STAGE_DIR/rollback_vps_paper_deploy.py" \
      --repo "$REPO_DIR" \
      --rollback-ref "$ORIGINAL_HEAD" \
      --branch main \
      --compose-file "$COMPOSE_FILE" \
      --env-backup "$ROLLBACK_DIR/.env.last-known-good" \
      --marker-backup "$ROLLBACK_DIR/deployed_git_rev.last-known-good" \
      --marker-was-present \
      --image-backup-tag "$ROLLBACK_IMAGE_TAG" \
      --docker-command "$DOCKER" \
      --telemetry-writer "$STAGE_DIR/write_vps_telemetry_manifest.py" \
      --tailscale-command "${SUDO:+$SUDO }tailscale" \
      --transport-validator "$STAGE_DIR/validate_dashboard_private_transport.py" \
      --private-dashboard-url "$DASHBOARD_PRIVATE_URL" \
      --failed-target-sha "$FAILED_TARGET_SHA" \
      --https-probe-attempts 10 \
      --https-probe-interval-seconds 7.0 \
      --required-service polymarket-paper-live \
      --required-service polymarket-dashboard \
      --required-service superbru-auto-pick-watchdog \
      --required-service vps-ops-scheduler \
      --report "$REPO_DIR/outputs/performance/vps_deploy_rollback.json" \
      || log "ERROR: rollback itself failed; recovery material retained in $ROLLBACK_DIR"
  fi
  exit "$status"
}

# --- source update -------------------------------------------------------------
update_checkout() {
  target_sha="$1"
  # Argument shape is Path A's (workflow :536-540). The flag is --target-ref.
  python3 "$STAGE_DIR/update_vps_checkout_preserving_runtime.py" \
    --repo "$REPO_DIR" \
    --target-ref "$target_sha" \
    --branch main \
    --report "$REPO_DIR/outputs/performance/vps_checkout_update.json" \
    || fail "runtime-preserving checkout update refused"
}

# --- markers BEFORE container recreation ---------------------------------------
# Ordering defect caught in review of the earlier draft: if the containers are
# recreated first, a failure between recreation and the marker write leaves the
# stack running new code while every marker still claims the old SHA, so the
# deployed-state report is a lie exactly when it matters most.
write_deploy_markers() {
  target_sha="$1"
  marker_file="$(deployed_marker)"
  mkdir -p "$(dirname "$marker_file")"
  marker_tmp="$marker_file.tmp.$$"
  printf '%s\n' "$target_sha" > "$marker_tmp"
  mv "$marker_tmp" "$marker_file"
  # Only PM_VPS_DEPLOYED_SHA. PM_IMAGE_BUILD_SHA is DERIVED by compose as
  # ${PM_VPS_DEPLOYED_SHA:-unknown} for every service, so writing it here would
  # plant a .env value no container ever sees - and the next host-side reader that
  # trusts .env the way check_polymarket_vps_paper.sh already does would compare
  # the deployed SHA against itself. Path A writes this one key too.
  python3 - "$REPO_DIR" "$target_sha" <<'PY'
import re
import sys
from pathlib import Path

repo, target = Path(sys.argv[1]), sys.argv[2]
env_path = repo / ".env"
text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
key = "PM_VPS_DEPLOYED_SHA"
line = f"{key}={target}"
if re.search(rf"^{key}=.*$", text, flags=re.MULTILINE):
    text = re.sub(rf"^{key}=.*$", line, text, flags=re.MULTILINE)
else:
    text = text.rstrip("\n") + "\n" + line + "\n"
temporary = env_path.with_name(env_path.name + ".tmp")
temporary.write_text(text, encoding="utf-8")
temporary.chmod(0o600)
temporary.replace(env_path)
PY
}

recreate_stack() {
  $DOCKER compose -f "$REPO_DIR/$COMPOSE_FILE" up -d --build \
    || fail "stack recreation failed"
}

# --- acceptance with the scheduler stopped -------------------------------------
# The scheduler must be absent before acceptance runs, or a concurrent governance
# pass writes the artifacts acceptance is trying to judge.
#
# NO --no-build HERE. `docker compose run` has no such flag - it belongs to
# `up` - and passing it aborts acceptance with "unknown flag: --no-build",
# which is exactly how the 2026-07-28 deploy failed past the arming boundary.
# Dropping it changes nothing: `run` does not build by default, and
# recreate_stack has already built the image two steps earlier.
#
# Bounded like Path A, because a hung acceptance run holds the deploy open past
# the arming boundary with the scheduler stopped.
run_deploy_acceptance() {
  $DOCKER compose -f "$REPO_DIR/$COMPOSE_FILE" stop vps-ops-scheduler || true
  timeout --signal=TERM --kill-after=30s 900 \
    $DOCKER compose -f "$REPO_DIR/$COMPOSE_FILE" --profile deploy-acceptance run \
    --rm --no-deps vps-deploy-acceptance </dev/null \
    || fail "deploy acceptance failed"
}

run_health_gate() {
  PM_VPS_REPO_DIR="$REPO_DIR" bash "$STAGE_DIR/check_polymarket_vps_paper.sh" \
    || fail "post-deploy health check failed"
}

restart_scheduler() {
  $DOCKER compose -f "$REPO_DIR/$COMPOSE_FILE" up -d --no-build vps-ops-scheduler \
    || fail "scheduler restart failed"
}

# --- the honest deploy record --------------------------------------------------
write_deploy_record() {
  target_sha="$1"
  output_root="${PM_OUTPUT_ROOT:-$REPO_DIR/outputs}"
  python3 - "$output_root/$DEPLOY_RECORD_RELATIVE" "$target_sha" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

target = Path(sys.argv[1])
target.parent.mkdir(parents=True, exist_ok=True)
payload = {
    "work_order": "WO-133",
    "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "deploy_path": "B_manual_guarded_script",
    "deployed_sha": sys.argv[2],
    # The irreducible gap, named rather than skipped. Path A binds the deployed
    # SHA to an independently reviewed acceptance run; a VPS shell cannot, because
    # verify_independent_main_acceptance.py needs a GitHub token and the run's
    # artifact. This deploy is bounded instead by the origin/main-tip refusal
    # above: it can only ship reviewed, merged code, but it cannot prove a second
    # identity reviewed it.
    "attestation_verified": False,
    "attestation_unverifiable_reason": (
        "verify_independent_main_acceptance.py requires GitHub API credentials and the "
        "acceptance run artifact; neither is available from the VPS shell"
    ),
    "target_equals_origin_main_tip": True,
    "authorised_by": "owner",
    "guard_order": [
        "assert_target_is_origin_main",
        "stage_target_scripts",
        "assert_checkout_matches_marker",
        "run_capacity_preflight",
        "assert_private_transport",
        "arm_rollback",
        "update_checkout",
        "write_deploy_markers",
        "recreate_stack",
        "run_deploy_acceptance",
        "run_health_gate",
        "restart_scheduler",
    ],
    "paper_trading_invoked": False,
    "live_trading_invoked": False,
}
temporary = target.with_name(target.name + ".tmp")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
temporary.replace(target)
PY
}

main() {
  log "WO-133 manual deploy starting; Path A (Actions workflow) is preferred when available"
  target_sha="$(assert_target_is_origin_main)"
  stage_target_scripts "$target_sha"
  assert_checkout_matches_marker
  run_capacity_preflight
  assert_private_transport
  arm_rollback "$target_sha"
  trap 'rollback_if_armed $?' EXIT
  update_checkout "$target_sha"
  write_deploy_markers "$target_sha"
  recreate_stack
  run_deploy_acceptance
  run_health_gate
  restart_scheduler
  trap - EXIT
  write_deploy_record "$target_sha"
  log "manual deploy complete at $target_sha (attestation_verified=false by construction)"
}

if [ "${PM_MANUAL_DEPLOY_LIBRARY_ONLY:-0}" != "1" ]; then
  main "$@"
fi
