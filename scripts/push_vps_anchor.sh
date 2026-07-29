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
LOCK_DIR="${TMPDIR:-/tmp}/push_vps_anchor.lock"
GIT_DIR="$REPO_DIR/.git"
STATUS_FILE="${VPS_ANCHOR_STATUS_FILE:-$REPO_DIR/outputs/performance/anchor_push_status.json}"
PUSH_STATUS="failed"
stamp_status() {
  mkdir -p "$(dirname "$STATUS_FILE")" 2>/dev/null || return 0
  tmp="$STATUS_FILE.tmp.$$"
  printf '{"generated_at_utc":"%s","status":"%s","paper_trading_invoked":false,"live_trading_invoked":false}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$PUSH_STATUS" > "$tmp" && mv "$tmp" "$STATUS_FILE"
}
trap stamp_status EXIT

[ -d "$GIT_DIR" ] || { echo "no git repo at $REPO_DIR" >&2; exit 0; }
[ -s "$HEAD_FILE" ] || { echo "no ledger anchor head at $HEAD_FILE" >&2; exit 0; }

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  PUSH_STATUS="lock_held"
  echo "another anchor push is running; skipping" >&2
  exit 0
fi
SNAP=""
cleanup() {
  rmdir "$LOCK_DIR" 2>/dev/null || true
  [ -n "$SNAP" ] && rm -rf "$SNAP"
}
trap 'cleanup; stamp_status' EXIT
trap 'cleanup; exit 1' INT TERM

ANCHOR_DATE=$(python - "$HEAD_FILE" <<'PY'
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
) || { echo "invalid ledger anchor head" >&2; exit 0; }

# Refresh only the dedicated anchor ref. A fetch failure leaves any existing
# remote-tracking tip available; the non-force push will still reject a stale
# parent and the next scheduler cycle will retry safely.
git --git-dir="$GIT_DIR" fetch -q origin \
  "refs/heads/$BRANCH:refs/remotes/origin/$BRANCH" 2>/dev/null || true
PARENT=$(git --git-dir="$GIT_DIR" rev-parse -q --verify "refs/remotes/origin/$BRANCH" 2>/dev/null || true)

if [ -n "$PARENT" ]; then
  EXISTING=$(mktemp) || exit 0
  if git --git-dir="$GIT_DIR" show "$PARENT:anchors/$ANCHOR_DATE.json" > "$EXISTING" 2>/dev/null; then
    if cmp -s "$EXISTING" "$HEAD_FILE"; then
      rm -f "$EXISTING"
      echo "$ANCHOR_DATE already present on $BRANCH"
      exit 0
    fi
    rm -f "$EXISTING"
    echo "refusing to replace existing $ANCHOR_DATE anchor" >&2
    exit 0
  fi
  rm -f "$EXISTING"
fi

SNAP=$(mktemp -d) || exit 0
mkdir -p "$SNAP/anchors"
cp "$HEAD_FILE" "$SNAP/anchors/$ANCHOR_DATE.json" || exit 0

export GIT_INDEX_FILE="$SNAP/.gitindex"
if [ -n "$PARENT" ]; then
  git --git-dir="$GIT_DIR" read-tree "$PARENT^{tree}" || exit 0
fi
git --git-dir="$GIT_DIR" --work-tree="$SNAP" add -f "anchors/$ANCHOR_DATE.json" || exit 0
TREE=$(git --git-dir="$GIT_DIR" write-tree) || exit 0

if [ -n "$PARENT" ]; then
  COMMIT=$(git --git-dir="$GIT_DIR" \
    -c user.name="vps-anchor" -c user.email="vps-anchor@localhost" \
    commit-tree -p "$PARENT" -m "ledger anchor $ANCHOR_DATE [skip ci]" "$TREE") || exit 0
else
  COMMIT=$(git --git-dir="$GIT_DIR" \
    -c user.name="vps-anchor" -c user.email="vps-anchor@localhost" \
    commit-tree -m "ledger anchor $ANCHOR_DATE [skip ci]" "$TREE") || exit 0
fi

# Deliberately no leading '+' refspec: history rewrites are forbidden here.
if git --git-dir="$GIT_DIR" push -q origin "$COMMIT:refs/heads/$BRANCH"; then
  PUSH_STATUS="ok"
  echo "$ANCHOR_DATE pushed durable ledger anchor $COMMIT to $BRANCH"
else
  echo "$ANCHOR_DATE anchor push failed; will retry next cycle" >&2
fi
