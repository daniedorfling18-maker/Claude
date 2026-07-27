#!/usr/bin/env sh
# WO-61 durable external timestamp for the daily ledger-chain head.
#
# Unlike vps-telemetry, this branch is NEVER force-replaced. Each daily file
# is added by a child commit whose parent is the current remote vps-anchor tip,
# preserving the complete external history. The working tree and main index
# are never touched. Failures are fail-soft so the recurring VPS scheduler can
# try again without wedging the research stack.
set -u

REPO_DIR="${VPS_ANCHOR_REPO_DIR:-$HOME/Claude}"
BRANCH="${VPS_ANCHOR_BRANCH:-vps-anchor}"
HEAD_FILE="${VPS_ANCHOR_HEAD_FILE:-$REPO_DIR/outputs/performance/ledger_anchor_head.json}"
STATUS_FILE="${VPS_ANCHOR_STATUS_FILE:-$REPO_DIR/outputs/performance/anchor_push_status.json}"
LOCK_DIR="${TMPDIR:-/tmp}/push_vps_anchor.lock"
GIT_DIR="$REPO_DIR/.git"
HOST_PYTHON="${VPS_ANCHOR_HOST_PYTHON:-}"
if [ -z "$HOST_PYTHON" ]; then
  if command -v python >/dev/null 2>&1; then
    HOST_PYTHON="python"
  elif command -v python3 >/dev/null 2>&1; then
    HOST_PYTHON="python3"
  fi
fi

# WO-121 (OPS-1): every path in this script used to exit 0 with no artifact, so
# the durable-timestamp lane could stop working indefinitely with nothing to
# measure - and `external_anchor_pending` was never cleared. Stamp the outcome
# like the push_vps_archive.sh sibling does. `last_success_at_utc` advances only
# when the current head is genuinely on the remote branch (a fresh push or an
# identical anchor already present); it never advances on a skip.
stamp_push() {
  [ -n "$HOST_PYTHON" ] || return 0
  STATE="$1"
  DETAIL="${2:-}"
  COMMIT_SHA="${3:-}"
  ANCHORED_DATE="${4:-}"
  mkdir -p "$(dirname "$STATUS_FILE")" 2>/dev/null || return 0
  "$HOST_PYTHON" - "$STATUS_FILE" "$STATE" "$DETAIL" "$COMMIT_SHA" "$ANCHORED_DATE" "$BRANCH" <<'PY' || return 0
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
state, detail, commit, anchor_date, branch = sys.argv[2:7]
try:
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
except Exception:
    payload = {}
if not isinstance(payload, dict):
    payload = {}
stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
payload.update(
    {
        "work_order": "WO-61+WO-121",
        "status": state,
        "generated_at_utc": stamp,
        "anchor_branch": branch,
        "detail": detail,
        "reporting_only": True,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
)
if state in {"ok", "already_present"}:
    payload["last_success_at_utc"] = stamp
    payload["last_success_anchor_date"] = anchor_date or payload.get("last_success_anchor_date")
    payload["external_anchor_pending"] = False
    payload["last_failure_at_utc"] = payload.get("last_failure_at_utc")
    payload["failure_detail"] = ""
    if commit:
        payload["last_success_commit"] = commit
elif state == "error":
    payload["last_failure_at_utc"] = stamp
    payload["failure_detail"] = detail
    payload["external_anchor_pending"] = True
temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temp, path)
PY
}

[ -d "$GIT_DIR" ] || { echo "no git repo at $REPO_DIR" >&2; exit 0; }
if [ ! -s "$HEAD_FILE" ]; then
  echo "no ledger anchor head at $HEAD_FILE" >&2
  # Not this script's failure: the anchor PRODUCER has not run. That lane has
  # its own registered freshness ceiling.
  stamp_push "skipped_no_head" "no ledger anchor head at $HEAD_FILE"
  exit 0
fi

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "another anchor push is running; skipping" >&2
  stamp_push "skipped_locked" "another anchor push is running"
  exit 0
fi
SNAP=""
cleanup() {
  rmdir "$LOCK_DIR" 2>/dev/null || true
  [ -n "$SNAP" ] && rm -rf "$SNAP"
}
trap cleanup EXIT INT TERM

ANCHOR_DATE=$("$HOST_PYTHON" - "$HEAD_FILE" <<'PY'
import json
import sys
from pathlib import Path

try:
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    value = str(payload.get("anchor_date") or "")
    if len(value) != 10:
        raise ValueError("invalid anchor_date")
    print(value)
except Exception:
    raise SystemExit(2)
PY
) || {
  echo "invalid ledger anchor head" >&2
  stamp_push "error" "ledger anchor head is unreadable or has no anchor_date"
  exit 1
}

# Refresh only the dedicated anchor ref. A fetch failure leaves any existing
# remote-tracking tip available; the non-force push will still reject a stale
# parent and the next scheduler cycle will retry safely.
git --git-dir="$GIT_DIR" fetch -q origin \
  "refs/heads/$BRANCH:refs/remotes/origin/$BRANCH" 2>/dev/null || true
PARENT=$(git --git-dir="$GIT_DIR" rev-parse -q --verify "refs/remotes/origin/$BRANCH" 2>/dev/null || true)

if [ -n "$PARENT" ]; then
  EXISTING=$(mktemp) || { stamp_push "error" "mktemp failed while comparing the existing anchor"; exit 1; }
  if git --git-dir="$GIT_DIR" show "$PARENT:anchors/$ANCHOR_DATE.json" > "$EXISTING" 2>/dev/null; then
    if cmp -s "$EXISTING" "$HEAD_FILE"; then
      rm -f "$EXISTING"
      echo "$ANCHOR_DATE already present on $BRANCH"
      # The current head IS durably timestamped; nothing to do is a success.
      stamp_push "already_present" "$ANCHOR_DATE already present on $BRANCH" "" "$ANCHOR_DATE"
      exit 0
    fi
    rm -f "$EXISTING"
    echo "refusing to replace existing $ANCHOR_DATE anchor" >&2
    # A DIFFERENT head for an already-anchored date means the local chain and
    # the durable timestamp disagree. Refusing is correct; silence is not.
    stamp_push "error" "local head differs from the existing $ANCHOR_DATE anchor; history rewrite refused"
    exit 1
  fi
  rm -f "$EXISTING"
fi

SNAP=$(mktemp -d) || { stamp_push "error" "mktemp -d failed"; exit 1; }
mkdir -p "$SNAP/anchors"
cp "$HEAD_FILE" "$SNAP/anchors/$ANCHOR_DATE.json" || { stamp_push "error" "could not stage the anchor head"; exit 1; }

export GIT_INDEX_FILE="$SNAP/.gitindex"
if [ -n "$PARENT" ]; then
  git --git-dir="$GIT_DIR" read-tree "$PARENT^{tree}" || { stamp_push "error" "read-tree of the remote anchor tip failed"; exit 1; }
fi
git --git-dir="$GIT_DIR" --work-tree="$SNAP" add -f "anchors/$ANCHOR_DATE.json" \
  || { stamp_push "error" "git add of the anchor snapshot failed"; exit 1; }
TREE=$(git --git-dir="$GIT_DIR" write-tree) || { stamp_push "error" "write-tree failed"; exit 1; }

if [ -n "$PARENT" ]; then
  COMMIT=$(git --git-dir="$GIT_DIR" \
    -c user.name="vps-anchor" -c user.email="vps-anchor@localhost" \
    commit-tree -p "$PARENT" -m "ledger anchor $ANCHOR_DATE [skip ci]" "$TREE") \
    || { stamp_push "error" "commit-tree failed"; exit 1; }
else
  COMMIT=$(git --git-dir="$GIT_DIR" \
    -c user.name="vps-anchor" -c user.email="vps-anchor@localhost" \
    commit-tree -m "ledger anchor $ANCHOR_DATE [skip ci]" "$TREE") \
    || { stamp_push "error" "commit-tree failed"; exit 1; }
fi

# Deliberately no leading '+' refspec: history rewrites are forbidden here.
if git --git-dir="$GIT_DIR" push -q origin "$COMMIT:refs/heads/$BRANCH"; then
  echo "$ANCHOR_DATE pushed durable ledger anchor $COMMIT to $BRANCH"
  stamp_push "ok" "pushed $ANCHOR_DATE to $BRANCH" "$COMMIT" "$ANCHOR_DATE"
else
  echo "$ANCHOR_DATE anchor push failed; will retry next cycle" >&2
  stamp_push "error" "push of $ANCHOR_DATE to $BRANCH failed (network or credential scope)" "$COMMIT" ""
  exit 1
fi
