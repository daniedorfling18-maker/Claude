#!/usr/bin/env sh
# WO-65 weekly full WO-61 ledger archive.
#
# The archive branch deliberately mirrors vps-telemetry's single parentless
# commit pattern: every successful push force-replaces the prior snapshot, so
# remote storage stays bounded.  The archive itself is capped at 240MB and is
# built from the WO-61 chain manifest; collection/training corpora never enter.
set -u

REPO_DIR="${VPS_ARCHIVE_REPO_DIR:-$HOME/Claude}"
BRANCH="${VPS_ARCHIVE_BRANCH:-vps-archive}"
CONFIG_PATH="${VPS_ARCHIVE_CONFIG_PATH:-$REPO_DIR/polymarket_predictive_config.example.yaml}"
CONTAINER_CONFIG_PATH="${VPS_ARCHIVE_CONTAINER_CONFIG_PATH:-/app/polymarket_predictive_config.example.yaml}"
COMPOSE_FILE="${VPS_ARCHIVE_COMPOSE_FILE:-$REPO_DIR/docker-compose.vps-paper.yml}"
ARCHIVE_FILE="${VPS_ARCHIVE_FILE:-$REPO_DIR/outputs/performance/ledger_state_archive.tar.gz}"
MANIFEST_FILE="${VPS_ARCHIVE_MANIFEST_FILE:-$REPO_DIR/outputs/performance/ledger_state_archive_manifest.json}"
STATUS_FILE="${VPS_ARCHIVE_STATUS_FILE:-$REPO_DIR/outputs/performance/disaster_recovery_status.json}"
LOCK_DIR="${TMPDIR:-/tmp}/push_vps_archive.lock"
MAX_ARCHIVE_BYTES=251658240  # 240MB (2026-07-26 owner amendment; was 50MB)
GIT_DIR="$REPO_DIR/.git"
HOST_PYTHON="${VPS_ARCHIVE_HOST_PYTHON:-}"
if [ -z "$HOST_PYTHON" ]; then
  if command -v python >/dev/null 2>&1; then
    HOST_PYTHON="python"
  elif command -v python3 >/dev/null 2>&1; then
    HOST_PYTHON="python3"
  fi
fi

[ -d "$GIT_DIR" ] || { echo "no git repo at $REPO_DIR" >&2; exit 1; }
[ -n "$HOST_PYTHON" ] || { echo "python/python3 is required to stamp archive status" >&2; exit 1; }

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "another ledger archive push is running; skipping" >&2
  exit 0
fi
SNAP=""
cleanup() {
  rmdir "$LOCK_DIR" 2>/dev/null || true
  [ -n "$SNAP" ] && rm -rf "$SNAP"
}
trap cleanup EXIT INT TERM

stamp_remote() {
  state="$1"
  commit="${2:-}"
  error="${3:-}"
  mkdir -p "$(dirname "$STATUS_FILE")"
  "$HOST_PYTHON" - "$STATUS_FILE" "$state" "$commit" "$error" "$BRANCH" <<'PY'
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

path = Path(sys.argv[1])
state, commit, error, branch = sys.argv[2:6]
try:
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
except Exception:
    payload = {}
stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
payload.update(
    {
        "status": "ok" if state == "ok" else "error",
        "generated_at_utc": stamp,
        "work_order": "WO-65",
        "archive_branch": branch,
        "remote_push_status": state,
        "remote_commit": commit or payload.get("remote_commit"),
        "remote_error": error,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
)
if state == "ok":
    payload["last_remote_success_at_utc"] = stamp
    payload["last_remote_snapshot_date"] = payload.get("snapshot_date")
    payload["last_remote_archive_age_hours"] = 0.0
    active_rpo = (payload.get("rpo") or {}).get("active_rpo_hours")
    if active_rpo is not None:
        payload["next_archive_due_at_utc"] = (
            datetime.now(timezone.utc) + timedelta(hours=float(active_rpo))
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload["failure_stamped"] = False
else:
    payload["last_remote_failure_at_utc"] = stamp
    payload["failure_stamped"] = True
path.parent.mkdir(parents=True, exist_ok=True)
temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temp, path)
PY
}

run_builder() {
  if command -v docker >/dev/null 2>&1 && [ -f "$COMPOSE_FILE" ]; then
    if docker compose -f "$COMPOSE_FILE" exec -T vps-ops-scheduler \
      python -m polymarket_predictive_engine.cli snapshot-ledger-archive \
      --config "$CONTAINER_CONFIG_PATH"; then
      return 0
    fi
    if docker compose -f "$COMPOSE_FILE" run --rm --no-deps vps-ops-scheduler \
      python -m polymarket_predictive_engine.cli snapshot-ledger-archive \
      --config "$CONTAINER_CONFIG_PATH"; then
      return 0
    fi
  fi
  # WO-122: the engine declares requires-python >=3.10 (pyproject). This host
  # runs 3.9, where importing the CLI dies on a runtime `X | None` type alias
  # with an opaque TypeError - which is what "local Python paths were
  # exhausted" was really reporting on 2026-07-16..26. Say so precisely
  # instead of attempting an import that cannot succeed; the container path
  # above is the supported builder.
  if ! "$HOST_PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
    HOST_PYTHON_VERSION="$("$HOST_PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo unknown)"
    echo "host $HOST_PYTHON is ${HOST_PYTHON_VERSION}; the archive builder requires >=3.10 (see pyproject requires-python), so only the container path can build it" >&2
    return 2
  fi
  if PYTHONPATH="$REPO_DIR/src${PYTHONPATH:+:$PYTHONPATH}" "$HOST_PYTHON" -m polymarket_predictive_engine.cli \
    snapshot-ledger-archive --config "$CONFIG_PATH"; then
    return 0
  fi
  return 1
}

run_builder
BUILDER_STATE=$?
if [ "$BUILDER_STATE" -ne 0 ]; then
  if [ "$BUILDER_STATE" -eq 2 ]; then
    BUILDER_REASON="archive builder failed: the container path did not build it and this host's python is older than the required 3.10, so no local fallback exists"
  else
    BUILDER_REASON="archive builder failed; Docker and local Python paths were exhausted"
  fi
  stamp_remote "error" "" "$BUILDER_REASON" || true
  echo "ledger archive build failed; status stamped for telemetry" >&2
  exit 1
fi

BUILD_STATUS=$("$HOST_PYTHON" - "$STATUS_FILE" <<'PY'
import json
import sys
from pathlib import Path
try:
    print(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")).get("status", "missing"))
except Exception:
    print("missing")
PY
)
case "$BUILD_STATUS" in
  not_due|disabled|skipped_locked)
    echo "ledger archive: $BUILD_STATUS"
    exit 0
    ;;
  ok)
    ;;
  *)
    stamp_remote "error" "" "archive builder returned status $BUILD_STATUS" || true
    echo "ledger archive status is $BUILD_STATUS" >&2
    exit 1
    ;;
esac

if [ ! -s "$ARCHIVE_FILE" ] || [ ! -s "$MANIFEST_FILE" ]; then
  stamp_remote "error" "" "archive or manifest missing after successful builder status" || true
  echo "archive output is missing" >&2
  exit 1
fi
ARCHIVE_BYTES=$(wc -c < "$ARCHIVE_FILE" | tr -d ' ')
if [ -z "$ARCHIVE_BYTES" ] || [ "$ARCHIVE_BYTES" -gt "$MAX_ARCHIVE_BYTES" ]; then
  stamp_remote "error" "" "archive exceeds hard 240MB remote cap" || true
  echo "archive exceeds hard 240MB cap" >&2
  exit 1
fi

SNAP=$(mktemp -d) || exit 1
mkdir -p "$SNAP/archive"
cp "$ARCHIVE_FILE" "$SNAP/archive/ledger_state_archive.tar.gz" || exit 1
cp "$MANIFEST_FILE" "$SNAP/archive/archive_manifest.json" || exit 1

export GIT_INDEX_FILE="$SNAP/.gitindex"
( cd "$SNAP" && git --git-dir="$GIT_DIR" --work-tree="$SNAP" add -f archive ) || exit 1
TREE=$(git --git-dir="$GIT_DIR" write-tree) || exit 1
STAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
COMMIT=$(git --git-dir="$GIT_DIR" \
  -c user.name="vps-archive" -c user.email="vps-archive@localhost" \
  commit-tree -m "VPS ledger archive $STAMP [skip ci]" "$TREE") || exit 1

# Deliberate force push: vps-archive is a one-snapshot recovery branch, unlike
# the append-only vps-anchor timestamp branch.
if git --git-dir="$GIT_DIR" push -q origin "+$COMMIT:refs/heads/$BRANCH"; then
  stamp_remote "ok" "$COMMIT" "" || true
  echo "$STAMP pushed ledger archive $COMMIT to $BRANCH"
else
  stamp_remote "error" "$COMMIT" "archive push failed; will retry on the next telemetry cycle" || true
  echo "$STAMP archive push failed; status is telemetry-visible" >&2
  exit 1
fi
