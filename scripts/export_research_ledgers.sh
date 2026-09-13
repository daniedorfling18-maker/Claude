#!/bin/sh
# WO-172: a complete, verified copy of the research ledgers, for off-box analysis.
#
# The telemetry mirror carries the last 200 rows of every large CSV, so a figure computed from it
# describes an extract. This is the other half: whole ledgers, hashed and line-counted against
# their sources, manifested with truncated = false asserted on every entry, and credential-scanned
# before anything is named. Nothing here pushes; the owner copies the directory.
#
# VPS-only, no cadence, owner-run. The training corpora are NOT exported: the charter's 2026-07-11
# record keeps them on the box, and this script never names them.
#
# Fail-closed: any copy, verification, manifest or scan failure deletes only the temporary
# directory this run created and renames nothing into place.
set -eu

REPO_DIR="${VPS_RESEARCH_EXPORT_REPO_DIR:-$HOME/Claude}"
OUT_ROOT="$REPO_DIR/outputs/research_exports"
STAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
TMP_DIR="$OUT_ROOT/.$STAMP.tmp"

# The ledgers, literal. Each line is a repo-relative path.
LEDGERS="
outputs/polymarket_shadow/shadow_positions.csv
outputs/polymarket_model_governance/closing_line_final_history.csv
outputs/polymarket_model_governance/closing_line_value_positions.csv
outputs/maker_carry/maker_carry_history.csv
outputs/maker_carry/maker_live_test_history.csv
outputs/polymarket_model_governance/edge_strategy_search.csv
"

cleanup_failed() {
  # Only ever the directory this run created.
  [ -n "${TMP_DIR:-}" ] && [ -d "$TMP_DIR" ] && rm -rf "$TMP_DIR"
  echo "$STAMP research export failed; nothing renamed into place" >&2
}
trap cleanup_failed EXIT

mkdir -p "$TMP_DIR/telemetry"

copy_verified() {
  # $1 = repo-relative path. Copies whole, then verifies hash and line count against the source.
  # One retry: maker_live_test_history.csv is append-only and can be torn mid-copy, while the
  # other five are rewritten atomically by utils.write_csv, so a copy sees old-or-new.
  rel="$1"
  src="$REPO_DIR/$rel"
  dst="$TMP_DIR/telemetry/$rel"
  [ -f "$src" ] || { echo "$STAMP missing ledger $rel" >&2; return 1; }
  mkdir -p "$(dirname "$dst")"
  attempt=1
  while [ "$attempt" -le 2 ]; do
    cp "$src" "$dst"
    src_sha=$(sha256sum "$src" | cut -d' ' -f1)
    dst_sha=$(sha256sum "$dst" | cut -d' ' -f1)
    src_lines=$(wc -l < "$src")
    dst_lines=$(wc -l < "$dst")
    if [ "$src_sha" = "$dst_sha" ] && [ "$src_lines" -eq "$dst_lines" ]; then
      return 0
    fi
    attempt=$((attempt + 1))
  done
  echo "$STAMP copy of $rel did not verify after 2 attempts" >&2
  return 1
}

for rel in $LEDGERS; do
  copy_verified "$rel"
done

python3 "$REPO_DIR/scripts/write_telemetry_export_manifest.py" \
  --snapshot-dir "$TMP_DIR" \
  --repo-root "$REPO_DIR" \
  --as-of "$STAMP" \
  --mode full

python3 "$REPO_DIR/scripts/write_telemetry_export_manifest.py" \
  --scan-credentials "$TMP_DIR" \
  --repo-root "$REPO_DIR"

trap - EXIT
mv "$TMP_DIR" "$OUT_ROOT/$STAMP"
echo "$STAMP research export complete at $OUT_ROOT/$STAMP"
