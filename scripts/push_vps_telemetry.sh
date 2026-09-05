#!/usr/bin/env sh
# Push a compact, decision-relevant snapshot of VPS outputs to the
# `vps-telemetry` branch so remote orchestration sessions (which cannot reach
# the VPS network directly) can read live state through the private repo.
#
# Cost/safety properties:
#   - Zero GitHub Actions cost: operational workflows are dispatch-only and
#     WO-69's self-hosted gate triggers only on pull requests. This force-push
#     is neither, while [skip ci] remains belt-and-braces.
#   - Zero history bloat: each push is a fresh parentless commit force-pushed
#     to the branch, so `vps-telemetry` always contains exactly one commit.
#   - Read-only everywhere else: never touches the working tree, index, or
#     any other branch; uses a private temp index and git plumbing only.
#   - Fail-soft: any missing file is skipped; any error exits quietly so a
#     cron/scheduler wrapper never wedges.
#
# Install on the VPS as the repo-owning user (opc):
#   crontab -e
#   */30 * * * * /home/opc/Claude/scripts/push_vps_telemetry.sh >> "$HOME/vps_telemetry_push.log" 2>&1
#
# The push uses the repo's existing `origin` credentials. If the stored
# credential is read-only, mint a fine-grained PAT with contents:write for
# this one repository.
set -u

REPO_DIR="${VPS_TELEMETRY_REPO_DIR:-$HOME/Claude}"
BRANCH="${VPS_TELEMETRY_BRANCH:-vps-telemetry}"
MAX_FILE_KB="${VPS_TELEMETRY_MAX_FILE_KB:-300}"
CSV_TAIL_LINES="${VPS_TELEMETRY_CSV_TAIL_LINES:-200}"
LOCK_DIR="${TMPDIR:-/tmp}/push_vps_telemetry.lock"
STATUS_FILE="${VPS_TELEMETRY_STATUS_FILE:-$REPO_DIR/outputs/performance/telemetry_push_status.json}"
PUSH_STATUS="failed"
STAMP_ENABLED=1
stamp_status() {
  # A run that lost the lock race must NOT stamp: overwriting the concurrent
  # run's outcome with "lock_held" would erase a fresh "ok" and fire the
  # publication-bridge incident on a healthy host. A wedged lock dir then
  # surfaces as stamp staleness under the watchdog's fixed two-hour grace.
  [ "$STAMP_ENABLED" = "1" ] || return 0
  mkdir -p "$(dirname "$STATUS_FILE")" 2>/dev/null || return 0
  tmp="$STATUS_FILE.tmp.$$"
  printf '{"generated_at_utc":"%s","status":"%s","paper_trading_invoked":false,"live_trading_invoked":false}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$PUSH_STATUS" > "$tmp" && mv "$tmp" "$STATUS_FILE"
}
trap stamp_status EXIT

GIT_DIR="$REPO_DIR/.git"
[ -d "$GIT_DIR" ] || { echo "no git repo at $REPO_DIR" >&2; exit 0; }

# WO-73: refuse every telemetry/archive push if a credential-shaped value is
# found in a whitelisted source or archive manifest. The report contains only
# finding locations/types, never matched values. This runs before either
# sibling push, so a failure cannot leak via the archive path first.
CREDENTIAL_GUARD="$REPO_DIR/scripts/check_telemetry_credential_guard.py"
CREDENTIAL_GUARD_OUTPUT="$REPO_DIR/outputs/ops_scheduler/credential_guard.json"
if [ ! -f "$CREDENTIAL_GUARD" ] || ! PYTHONPATH="$REPO_DIR/src${PYTHONPATH:+:$PYTHONPATH}" \
  python3 "$CREDENTIAL_GUARD" --repo-root "$REPO_DIR" --output "$CREDENTIAL_GUARD_OUTPUT"; then
  echo "WO-73 credential guard failed; refusing telemetry and archive push" >&2
  exit 1
fi

# WO-61: this script already runs from the HOST (where .git credentials live),
# while the daily harvest runs inside a container without Git metadata. Push
# any pending daily chain head to the dedicated append-only branch first.
ANCHOR_SCRIPT="$REPO_DIR/scripts/push_vps_anchor.sh"
if [ "${VPS_TELEMETRY_PUSH_ANCHOR:-true}" != "false" ] && [ -f "$ANCHOR_SCRIPT" ]; then
  VPS_ANCHOR_REPO_DIR="$REPO_DIR" sh "$ANCHOR_SCRIPT" || true
fi

# WO-65: the existing host telemetry cadence also checks the active archive
# RPO. The sibling script builds only when due, force-replaces the bounded
# vps-archive branch, and stamps every failure before telemetry is collected.
ARCHIVE_SCRIPT="$REPO_DIR/scripts/push_vps_archive.sh"
if [ "${VPS_TELEMETRY_PUSH_ARCHIVE:-true}" != "false" ] && [ -f "$ARCHIVE_SCRIPT" ]; then
  VPS_ARCHIVE_REPO_DIR="$REPO_DIR" sh "$ARCHIVE_SCRIPT" || true
fi

# mkdir is atomic: poor-man's flock that works on any POSIX sh.
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  STAMP_ENABLED=0
  echo "another telemetry push is running; skipping" >&2
  exit 0
fi
SNAP=""
cleanup() {
  rmdir "$LOCK_DIR" 2>/dev/null || true
  [ -n "$SNAP" ] && rm -rf "$SNAP"
}
trap 'cleanup; stamp_status' EXIT
trap 'cleanup; exit 1' INT TERM

SNAP=$(mktemp -d) || exit 0
STAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# WO-68: persist the same host-derived deployment marker that is published on
# vps-telemetry. The guarded deploy calls this same writer before post-deploy
# operating-state acceptance, so definitions cannot drift between paths.
HOST_MANIFEST="$REPO_DIR/outputs/performance/vps_telemetry_manifest.json"
MANIFEST_WRITER="$REPO_DIR/scripts/write_vps_telemetry_manifest.py"
if [ ! -f "$MANIFEST_WRITER" ] || ! python3 "$MANIFEST_WRITER" \
  --repo-root "$REPO_DIR" --output "$HOST_MANIFEST" --as-of "$STAMP"; then
  echo "WO-68 host manifest refresh failed; refusing to publish stale telemetry" >&2
  exit 0
fi

# Whitelisted output directories: decision summaries only, never the heavy
# collection corpora (training archive, websocket features, trade prints,
# official book snapshots stay on the VPS).
# h2_dutch and h3_smart_flow carry the REGISTERED verdict artifacts for two of
# the three primary hypotheses - docs/EXPERIMENT_REGISTRY.md states that only
# outputs/h2_dutch/h2_evaluation.json may state the registered H2 verdict. They
# were absent from this list, so those verdicts were unreadable from any session
# not on the VPS, which is exactly what this mirror exists to prevent. Both hold
# small summary JSON of the same class as the entries around them.
# Keep comments OUTSIDE the string: it is newline-split by the for loop below,
# so a "#" line would be consumed as a directory path.
TELEMETRY_DIRS="
outputs/ops_scheduler
outputs/execution
outputs/polymarket_model_governance
outputs/maker_carry
outputs/wallet_intelligence
outputs/implication_consistency
outputs/calibration_bias
outputs/drift_scan
outputs/event_group_consistency
outputs/h2_dutch
outputs/h3_smart_flow
outputs/reconstructed_signal_clv
outputs/polymarket_shadow
outputs/performance
"

copy_capped() {
  # $1 = repo-relative path. Copies whole file if small, else skips (JSON) or
  # keeps header + tail (CSV) so ledgers stay readable without getting huge.
  rel="$1"
  src="$REPO_DIR/$rel"
  [ -f "$src" ] || return 0
  dst="$SNAP/telemetry/$rel"
  mkdir -p "$(dirname "$dst")"
  size_kb=$(du -k "$src" 2>/dev/null | cut -f1)
  [ -n "$size_kb" ] || return 0
  if [ "$size_kb" -le "$MAX_FILE_KB" ]; then
    cp "$src" "$dst"
    return 0
  fi
  case "$rel" in
    *.csv)
      { head -n 1 "$src"; tail -n "$CSV_TAIL_LINES" "$src"; } > "$dst"
      ;;
    *)
      : # oversized non-CSV summaries are skipped rather than truncated
      ;;
  esac
}

for dir in $TELEMETRY_DIRS; do
  [ -d "$REPO_DIR/$dir" ] || continue
  find "$REPO_DIR/$dir" -maxdepth 2 -type f \
    \( -name '*.json' -o -name '*.md' -o -name '*.csv' -o -name '*.log' \) \
    2>/dev/null | while IFS= read -r abs; do
      case "$abs" in
        *official_books*) continue ;;
        # WO-121: the scheduler's own append-only log is never usefully
        # published - it is permanently past MAX_FILE_KB, so copy_capped
        # already skipped it - and it is an unbounded source of 64-hex venue
        # identifiers that the WO-73 guard's text heuristic cannot classify,
        # which blocked every telemetry push for >22h. Purpose-built logs such
        # as vps_diagnostic.log (trimmed to fit the cap) still publish.
        # Mirrored by credential_guard.PAYLOAD_EXCLUDED_FRAGMENTS.
        *ops_scheduler.log*) continue ;;
      esac
      copy_capped "${abs#"$REPO_DIR"/}"
    done
done

mkdir -p "$SNAP/telemetry"
cp "$HOST_MANIFEST" "$SNAP/telemetry/manifest.json"

# Build a parentless commit with plumbing: temp index, no working-tree touch.
export GIT_INDEX_FILE="$SNAP/.gitindex"
( cd "$SNAP" && git --git-dir="$GIT_DIR" --work-tree="$SNAP" add -f telemetry ) || exit 0
TREE=$(git --git-dir="$GIT_DIR" write-tree) || exit 0
COMMIT=$(git --git-dir="$GIT_DIR" \
  -c user.name="vps-telemetry" -c user.email="vps-telemetry@localhost" \
  commit-tree -m "vps telemetry snapshot $STAMP [skip ci]" "$TREE") || exit 0
if git --git-dir="$GIT_DIR" push -q origin "+$COMMIT:refs/heads/$BRANCH"; then
  PUSH_STATUS="ok"
  echo "$STAMP pushed telemetry snapshot $COMMIT to $BRANCH"
else
  echo "$STAMP push failed (network or credential scope); will retry next cycle" >&2
fi
