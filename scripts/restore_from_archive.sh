#!/usr/bin/env sh
# WO-65 tested ledger restore. Dry-run extracts only to a temporary directory,
# verifies every archive digest, then runs WO-61 prefix-chain verification as
# of the recorded snapshot date. Applying a restore requires an empty target.
set -u

REPO_DIR="${VPS_ARCHIVE_REPO_DIR:-$HOME/Claude}"
BRANCH="${VPS_ARCHIVE_BRANCH:-vps-archive}"
CONFIG_PATH=""
ARCHIVE_PATH=""
OUTPUT_ROOT=""
MODE=""
TEMP_DIR=""

usage() {
  echo "usage: $0 --dry-run|--apply [--archive FILE] [--config FILE] [--repo-dir DIR] [--output-root EMPTY_DIR]" >&2
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) MODE="dry-run" ;;
    --apply) MODE="apply" ;;
    --archive) shift; [ "$#" -gt 0 ] || { usage; exit 2; }; ARCHIVE_PATH="$1" ;;
    --config) shift; [ "$#" -gt 0 ] || { usage; exit 2; }; CONFIG_PATH="$1" ;;
    --repo-dir) shift; [ "$#" -gt 0 ] || { usage; exit 2; }; REPO_DIR="$1" ;;
    --output-root) shift; [ "$#" -gt 0 ] || { usage; exit 2; }; OUTPUT_ROOT="$1" ;;
    *) usage; exit 2 ;;
  esac
  shift
done

[ -n "$MODE" ] || { usage; exit 2; }
[ -d "$REPO_DIR/.git" ] || { echo "no Git repository at $REPO_DIR" >&2; exit 2; }
[ -n "$CONFIG_PATH" ] || CONFIG_PATH="$REPO_DIR/polymarket_predictive_config.example.yaml"
if [ "$MODE" = "apply" ] && [ -z "$OUTPUT_ROOT" ]; then
  echo "--apply requires --output-root; it must be empty" >&2
  exit 2
fi

cleanup() {
  [ -n "$TEMP_DIR" ] && rm -rf "$TEMP_DIR"
}
trap cleanup EXIT INT TERM

if [ -z "$ARCHIVE_PATH" ]; then
  TEMP_DIR=$(mktemp -d) || exit 2
  ARCHIVE_PATH="$TEMP_DIR/ledger_state_archive.tar.gz"
  git -C "$REPO_DIR" fetch -q origin "$BRANCH" || {
    echo "cannot fetch origin/$BRANCH" >&2
    exit 2
  }
  git -C "$REPO_DIR" show "origin/$BRANCH:archive/ledger_state_archive.tar.gz" > "$ARCHIVE_PATH" || {
    echo "origin/$BRANCH does not contain the ledger archive" >&2
    exit 2
  }
fi
[ -s "$ARCHIVE_PATH" ] || { echo "archive is missing or empty: $ARCHIVE_PATH" >&2; exit 2; }

PYTHON_BIN="${VPS_ARCHIVE_PYTHON:-}"
if [ -z "$PYTHON_BIN" ]; then
  if command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  fi
fi

run_local() {
  [ -n "$PYTHON_BIN" ] || return 1
  if ! PYTHONPATH="$REPO_DIR/src${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" -c \
    "import yaml, polymarket_predictive_engine.disaster_recovery" >/dev/null 2>&1; then
    return 1
  fi
  if [ "$MODE" = "dry-run" ]; then
    PYTHONPATH="$REPO_DIR/src${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" \
      -m polymarket_predictive_engine.cli verify-ledger-archive \
      --config "$CONFIG_PATH" --archive-path "$ARCHIVE_PATH"
  else
    PYTHONPATH="$REPO_DIR/src${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" \
      -m polymarket_predictive_engine.cli verify-ledger-archive \
      --config "$CONFIG_PATH" --archive-path "$ARCHIVE_PATH" \
      --apply-restore --restore-output-root "$OUTPUT_ROOT"
  fi
}

if run_local; then
  exit 0
else
  LOCAL_CODE=$?
  # A working local verifier that rejected the archive must stay rejected;
  # never hide a cryptographic failure behind a second runtime.
  if [ "$LOCAL_CODE" -ne 1 ]; then
    exit "$LOCAL_CODE"
  fi
fi

COMPOSE_FILE="$REPO_DIR/docker-compose.vps-paper.yml"
if ! command -v docker >/dev/null 2>&1 || [ ! -f "$COMPOSE_FILE" ]; then
  echo "no usable Python environment or Docker verifier; follow docs/RESTORE.md" >&2
  exit 2
fi

WORK_DIR="$REPO_DIR/work/disaster_recovery"
mkdir -p "$WORK_DIR"
CONTAINER_ARCHIVE="$WORK_DIR/restore_input.tar.gz"
cp "$ARCHIVE_PATH" "$CONTAINER_ARCHIVE" || exit 2
if [ "$MODE" = "apply" ] && [ "$OUTPUT_ROOT" != "$REPO_DIR/outputs" ]; then
  echo "Docker restore can apply only to $REPO_DIR/outputs; use the local Python path for another target" >&2
  exit 2
fi

DOCKER_ARGS="verify-ledger-archive --config /app/polymarket_predictive_config.example.yaml --archive-path /app/work/disaster_recovery/restore_input.tar.gz"
if [ "$MODE" = "apply" ]; then
  DOCKER_ARGS="$DOCKER_ARGS --apply-restore --restore-output-root /app/outputs"
fi
# shellcheck disable=SC2086 -- arguments are fixed tokens assembled above
docker compose -f "$COMPOSE_FILE" run --rm --no-deps vps-ops-scheduler \
  python -m polymarket_predictive_engine.cli $DOCKER_ARGS
