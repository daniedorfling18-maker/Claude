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

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
fail() { log "ERROR: $*" >&2; exit 1; }

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
  marker_file="$REPO_DIR/deployed_git_rev"
  [ -f "$marker_file" ] || fail "deployed marker $marker_file is missing; refusing to quiesce"
  marker_sha="$(tr -d '[:space:]' < "$marker_file")"
  head_sha="$(git -C "$REPO_DIR" rev-parse HEAD)"
  [ -n "$marker_sha" ] || fail "deployed marker is empty; refusing to quiesce"
  if [ "$marker_sha" != "$head_sha" ]; then
    fail "checkout $head_sha does not match deployed marker $marker_sha; \
resolve the drift before deploying"
  fi
}

# --- guard 3: capacity ---------------------------------------------------------
run_capacity_preflight() {
  python3 "$REPO_DIR/scripts/preflight_vps_capacity.py" --repo-dir "$REPO_DIR" \
    || fail "capacity preflight refused the deploy"
}

# --- guard 4: private transport proof, BEFORE the stack is quiesced ------------
# Public exposure is the single thing this gate exists to prevent, and proving it
# after quiescing would mean tearing down a healthy stack to discover the new one
# must not be started. An uncapturable transport state fails closed.
assert_private_transport() {
  python3 "$REPO_DIR/scripts/validate_dashboard_private_transport.py" --repo-dir "$REPO_DIR" \
    || fail "dashboard private-transport proof failed; refusing to deploy"
}

# --- the arming boundary -------------------------------------------------------
# Everything after this can be undone by rollback_vps_paper_deploy.py, and
# everything before it has changed nothing on the host.
arm_rollback() {
  ROLLBACK_DIR="$(mktemp -d)"
  chmod 0700 "$ROLLBACK_DIR"
  install -m 0600 "$REPO_DIR/.env" "$ROLLBACK_DIR/.env.last-known-good" \
    || fail "could not snapshot .env"
  install -m 0600 "$REPO_DIR/deployed_git_rev" "$ROLLBACK_DIR/deployed_git_rev.last-known-good" \
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
    python3 "$REPO_DIR/scripts/rollback_vps_paper_deploy.py" \
      --repo "$REPO_DIR" \
      --rollback-ref "$ORIGINAL_HEAD" \
      --branch main \
      --env-backup "$ROLLBACK_DIR/.env.last-known-good" \
      --marker-backup "$ROLLBACK_DIR/deployed_git_rev.last-known-good" \
      --image-tag "$ROLLBACK_IMAGE_TAG" \
      || log "ERROR: rollback itself failed; recovery material retained in $ROLLBACK_DIR"
  fi
  exit "$status"
}

# --- source update -------------------------------------------------------------
update_checkout() {
  target_sha="$1"
  python3 "$REPO_DIR/scripts/update_vps_checkout_preserving_runtime.py" \
    --repo "$REPO_DIR" --target "$target_sha" \
    || fail "runtime-preserving checkout update refused"
}

# --- markers BEFORE container recreation ---------------------------------------
# Ordering defect caught in review of the earlier draft: if the containers are
# recreated first, a failure between recreation and the marker write leaves the
# stack running new code while every marker still claims the old SHA, so the
# deployed-state report is a lie exactly when it matters most.
write_deploy_markers() {
  target_sha="$1"
  printf '%s\n' "$target_sha" > "$REPO_DIR/deployed_git_rev"
  python3 - "$REPO_DIR" "$target_sha" <<'PY'
import re
import sys
from pathlib import Path

repo, target = Path(sys.argv[1]), sys.argv[2]
env_path = repo / ".env"
text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
for key in ("PM_VPS_DEPLOYED_SHA", "PM_IMAGE_BUILD_SHA"):
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
run_deploy_acceptance() {
  $DOCKER compose -f "$REPO_DIR/$COMPOSE_FILE" stop vps-ops-scheduler || true
  $DOCKER compose -f "$REPO_DIR/$COMPOSE_FILE" --profile deploy-acceptance run \
    --rm --no-deps --no-build vps-deploy-acceptance </dev/null \
    || fail "deploy acceptance failed"
}

run_health_gate() {
  PM_VPS_REPO_DIR="$REPO_DIR" bash "$REPO_DIR/scripts/check_polymarket_vps_paper.sh" \
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
  assert_checkout_matches_marker
  run_capacity_preflight
  assert_private_transport
  arm_rollback
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
