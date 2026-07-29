#!/usr/bin/env sh
# VPS ops scheduler: runs the recurring jobs that previously lived in GitHub
# Actions, so the system stays fully operational when Actions minutes are
# exhausted (2026-07-09: the account hit its billing wall and every scheduled
# workflow was refused). GitHub lanes remain as optional/archival duplicates.
#
# Jobs (cadences mirror the old workflow crons; all config via env):
#   governance_refresh   every 6h  - full refresh-governance (model re-score,
#                                    dashboards, verdict engine, proof files)
#   clv_snapshot         every 8h  - SuperBru CLV-vs-close snapshot + report
#   locked_card_refresh  every 12h - leaderboard scrape -> chaser profiles ->
#                                    fresh odds -> locked card rebuild
#
# Odds-spending jobs preflight the FREE /v4/sports endpoint first and skip
# cleanly when the key has no credits. Every job runs under `timeout`, is
# fail-soft (loop survives), and stamps outputs/ops_scheduler/status.json so
# the dashboard host can see last run / last exit per job.
set -u

CONFIG_PATH="${POLYMARKET_CONFIG_PATH:-/app/polymarket_predictive_config.example.yaml}"
OUT_DIR="${OPS_SCHEDULER_OUT_DIR:-outputs/ops_scheduler}"
TICK_SECONDS="${OPS_TICK_SECONDS:-300}"
GOVERNANCE_INTERVAL="${OPS_GOVERNANCE_INTERVAL_SECONDS:-21600}"
CLV_INTERVAL="${OPS_CLV_SNAPSHOT_INTERVAL_SECONDS:-28800}"
CARD_INTERVAL="${OPS_CARD_REFRESH_INTERVAL_SECONDS:-43200}"
HARVEST_INTERVAL="${OPS_TRAINING_HARVEST_INTERVAL_SECONDS:-86400}"
HARVEST_TIMEOUT="${OPS_TRAINING_HARVEST_TIMEOUT_SECONDS:-1800}"
# WO-85 (2026-07-15): registered whole-harvest start budget. The Python
# orchestrator caps this at six hours, so configuration can only tighten it.
HARVEST_DEADLINE="${OPS_TRAINING_HARVEST_DEADLINE_SECONDS:-21600}"
# A failed/interrupted attempt remains due from its last successful completion,
# but must not hammer the host in a zero-backoff retry loop. Keep retries within
# a registered 15-60 minute recovery window (30 minutes by default).
HARVEST_RETRY_INTERVAL="${OPS_TRAINING_HARVEST_RETRY_SECONDS:-1800}"
case "$HARVEST_RETRY_INTERVAL" in ''|*[!0-9]*) HARVEST_RETRY_INTERVAL=1800 ;; esac
[ "$HARVEST_RETRY_INTERVAL" -ge 900 ] 2>/dev/null || HARVEST_RETRY_INTERVAL=900
[ "$HARVEST_RETRY_INTERVAL" -le 3600 ] || HARVEST_RETRY_INTERVAL=3600
MAKER_STUDY_INTRADAY_INTERVAL="${OPS_MAKER_STUDY_INTRADAY_INTERVAL_SECONDS:-86400}"
MAKER_STUDY_INTRADAY_OFFSET_MIN="${OPS_MAKER_STUDY_INTRADAY_OFFSET_MIN_SECONDS:-39600}"
MAKER_STUDY_INTRADAY_OFFSET_MAX="${OPS_MAKER_STUDY_INTRADAY_OFFSET_MAX_SECONDS:-46800}"
PRINTS_INTERVAL="${OPS_TRADE_PRINTS_INTERVAL_SECONDS:-900}"
PRINTS_TIMEOUT="${OPS_TRADE_PRINTS_TIMEOUT_SECONDS:-300}"
# WO-85/WO-86 completion correction: safety reporting must remain live while
# a bounded long job (especially the daily harvest) is running. Configuration
# may tighten these registered maxima, never widen them.
MAKER_SAFETY_INTERVAL="${OPS_MAKER_SAFETY_INTERVAL_SECONDS:-900}"
SAFETY_PULSE_INTERVAL="${OPS_SAFETY_PULSE_INTERVAL_SECONDS:-300}"
SAFETY_POLL_SECONDS="${OPS_SAFETY_POLL_SECONDS:-5}"
case "$MAKER_SAFETY_INTERVAL" in ''|*[!0-9]*) MAKER_SAFETY_INTERVAL=900 ;; esac
case "$SAFETY_PULSE_INTERVAL" in ''|*[!0-9]*) SAFETY_PULSE_INTERVAL=300 ;; esac
case "$SAFETY_POLL_SECONDS" in ''|*[!0-9]*) SAFETY_POLL_SECONDS=5 ;; esac
[ "$MAKER_SAFETY_INTERVAL" -gt 0 ] 2>/dev/null || MAKER_SAFETY_INTERVAL=900
[ "$SAFETY_PULSE_INTERVAL" -gt 0 ] 2>/dev/null || SAFETY_PULSE_INTERVAL=300
[ "$SAFETY_POLL_SECONDS" -gt 0 ] 2>/dev/null || SAFETY_POLL_SECONDS=5
[ "$MAKER_SAFETY_INTERVAL" -le 900 ] || MAKER_SAFETY_INTERVAL=900
[ "$SAFETY_PULSE_INTERVAL" -le 300 ] || SAFETY_PULSE_INTERVAL=300
[ "$SAFETY_POLL_SECONDS" -le 5 ] || SAFETY_POLL_SECONDS=5
GOVERNANCE_TIMEOUT="${OPS_GOVERNANCE_TIMEOUT_SECONDS:-1500}"
CLV_TIMEOUT="${OPS_CLV_TIMEOUT_SECONDS:-900}"
CARD_TIMEOUT="${OPS_CARD_TIMEOUT_SECONDS:-2400}"
# WO-114 (2026-07-21): the SuperBru locked-card chain is a seasonal job. When
# the tracked tournament ends the odds feed returns zero events and the chain
# fails closed on every cycle. Set OPS_CARD_REFRESH_ENABLED=0 to quiesce it
# cleanly - recorded as an intentional skip (exit 0), which refreshes the
# job's success stamp and stays out of the overrun SLO - instead of accruing
# exit-1 incidents and burning an odds preflight until the next tournament.
CARD_REFRESH_ENABLED="${OPS_CARD_REFRESH_ENABLED:-1}"
# Ledger-anchor cadence: 12h gives three chances inside deploy-acceptance's
# 36h ledger_anchor_age SLO. Hashing the largest ledger (~17MB) is seconds;
# 600s bounds a pathological filesystem stall.
LEDGER_ANCHOR_INTERVAL="${OPS_LEDGER_ANCHOR_INTERVAL_SECONDS:-43200}"
LEDGER_ANCHOR_TIMEOUT="${OPS_LEDGER_ANCHOR_TIMEOUT_SECONDS:-600}"
LEADER_PLAYER="${SUPERBRU_PLAYER_NAME:-Danie}"
export PYTHONPATH="${PYTHONPATH:-scripts:src}"

mkdir -p "$OUT_DIR"
LOG_FILE="$OUT_DIR/ops_scheduler.log"
JOB_SCHEDULE_SKIP_KIND=""
# WO-114 (2026-07-21): bound ops_scheduler.log growth. The degraded-state
# watchdog serialises its full operating-state JSON every cycle, so an
# unrotated log reaches millions of lines and threatens the VPS disk budget.
# Rotate to a single .1 generation when the live log crosses the cap (50 MiB
# by default); set OPS_LOG_MAX_BYTES=0 to disable rotation.
LOG_MAX_BYTES="${OPS_LOG_MAX_BYTES:-52428800}"
case "$LOG_MAX_BYTES" in ''|*[!0-9]*) LOG_MAX_BYTES=52428800 ;; esac

log() {
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" | tee -a "$LOG_FILE"
}

rotate_log_if_needed() {
  [ "$LOG_MAX_BYTES" -gt 0 ] 2>/dev/null || return 0
  [ -f "$LOG_FILE" ] || return 0
  SIZE="$(wc -c < "$LOG_FILE" 2>/dev/null | tr -d ' ')"
  case "${SIZE:-0}" in ''|*[!0-9]*) SIZE=0 ;; esac
  if [ "$SIZE" -ge "$LOG_MAX_BYTES" ]; then
    # A single generation is retained. Rotation runs only at the top of the
    # scheduler loop, when no job subshell holds the log open, so the rename
    # and truncate cannot lose an in-flight write.
    # WO-120: a failed rotation must not be silent - the log would then grow
    # without bound, the exact disk-budget failure WO-114 exists to prevent.
    if mv -f "$LOG_FILE" "$LOG_FILE.1" 2>/dev/null; then
      : > "$LOG_FILE"
      log "log rotated at ${SIZE} bytes (cap ${LOG_MAX_BYTES}); previous log moved to ${LOG_FILE}.1"
    else
      log "log rotation FAILED at ${SIZE} bytes (cap ${LOG_MAX_BYTES}); ops_scheduler.log is growing unbounded"
    fi
  fi
}

stamp_status() {
  EXPLICIT_SKIP_KIND="${5:-}"
  if [ -z "$EXPLICIT_SKIP_KIND" ]; then
    EXPLICIT_SKIP_KIND="$JOB_SCHEDULE_SKIP_KIND"
  fi
  SUCCESS_ELIGIBLE="${6:-true}"
  JOB="$1" EXIT_CODE="$2" DETAIL="${3:-}" STARTED_AT="${4:-}" SKIP_KIND="$EXPLICIT_SKIP_KIND" SUCCESS_ELIGIBLE="$SUCCESS_ELIGIBLE" OUT_DIR="$OUT_DIR" python - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

path = Path(os.environ["OUT_DIR"]) / "status.json"
try:
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
except Exception:
    payload = {}
jobs = payload.setdefault("jobs", {})
job_name = os.environ["JOB"]
previous = jobs.get(job_name, {}) if isinstance(jobs.get(job_name), dict) else {}
now = datetime.now(timezone.utc)
started_raw = os.environ.get("STARTED_AT", "")
try:
    started = datetime.fromisoformat(started_raw.replace("Z", "+00:00")) if started_raw else None
except ValueError:
    started = None
detail = os.environ.get("DETAIL", "")
exit_code = int(os.environ["EXIT_CODE"])
success_eligible = os.environ.get("SUCCESS_ELIGIBLE") == "true"
skip_kind = os.environ.get("SKIP_KIND", "").strip().lower()
if exit_code == 124 and not skip_kind:
    skip_kind = "overrun"
if skip_kind not in {"", "intentional", "overrun"}:
    raise ValueError(f"invalid scheduler skip kind: {skip_kind}")
skipped_intentional = skip_kind == "intentional"
skipped_overrun = skip_kind == "overrun"
skipped = skipped_intentional or skipped_overrun

def count(name):
    try:
        return int(previous.get(name, 0) or 0)
    except (TypeError, ValueError):
        return 0

jobs[job_name] = {
    "last_run_utc": now.isoformat(),
    "started_at_utc": started.isoformat() if started else "",
    "duration_seconds": round((now - started).total_seconds(), 3) if started else None,
    "last_exit_code": exit_code,
    # Failed attempts must not make a periodic job look freshly successful.
    "last_success_utc": now.isoformat() if exit_code == 0 and success_eligible else str(previous.get("last_success_utc") or ""),
    "detail": detail,
    "skip_kind": skip_kind or "none",
    "skipped_intentional": skipped_intentional,
    "skipped_overrun": skipped_overrun,
    "runs_total": count("runs_total") + 1,
    "skipped_intentional_total": count("skipped_intentional_total") + int(skipped_intentional),
    "consecutive_skipped_intentional": count("consecutive_skipped_intentional") + 1 if skipped_intentional else 0,
    "skipped_overrun_total": count("skipped_overrun_total") + int(skipped_overrun),
    "consecutive_skipped_overrun": count("consecutive_skipped_overrun") + 1 if skipped_overrun else 0,
    # Backward-compatible aggregate fields. The SLO deliberately ignores
    # these because intentional quota/preflight declines are not incidents.
    "skipped_cycles_total": count("skipped_cycles_total") + int(skipped),
    "consecutive_skipped_cycles": count("consecutive_skipped_cycles") + 1 if skipped else 0,
    "failed_cycles_total": count("failed_cycles_total") + int(exit_code != 0),
}
payload["mode"] = "vps_ops_scheduler"
payload["generated_at_utc"] = now.isoformat()
payload["paper_trading_invoked"] = False
payload["live_trading_invoked"] = False
# WO-120: status.json was the one stamp file written non-atomically (every
# Python producer uses temp+fsync+replace). A torn write here wiped ALL job
# history on the next read (the reader falls back to {}) and silently
# disarmed both scheduler watchdog registrations.
tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(tmp_path, path)
PY
}

seconds_since_stamp() {
  STAMP_FILE="$OUT_DIR/last_$1"
  if [ ! -f "$STAMP_FILE" ]; then
    echo 999999999
    return
  fi
  NOW=$(date -u +%s)
  THEN=$(cat "$STAMP_FILE" 2>/dev/null || echo 0)
  # WO-120: a corrupt/empty stamp used to make $((NOW - THEN)) an arithmetic
  # error, whose empty output then broke the caller's [ -ge ] test and the job
  # was never scheduled again, silently. Treat unreadable stamps as epoch 0
  # (immediately due) like the sibling readers already do.
  case "${THEN:-}" in ''|*[!0-9]*) THEN=0 ;; esac
  echo $((NOW - THEN))
}

touch_stamp() {
  date -u +%s > "$OUT_DIR/last_$1"
}

# WO-85 completion-stamp correction (2026-07-15): the daily harvest is due
# from its last successful COMPLETION, never from a start/attempt timestamp.
# A missing dedicated completion stamp is migrated read-only from status.json;
# if neither exists the job is immediately due. This makes a container restart
# during the harvest re-arm the job instead of consuming the next 24-hour slot.
successful_completion_epoch() {
  SUCCESS_STAMP="$OUT_DIR/last_success_$1"
  if [ -f "$SUCCESS_STAMP" ]; then
    cat "$SUCCESS_STAMP" 2>/dev/null || echo 0
    return
  fi
  JOB="$1" OUT_DIR="$OUT_DIR" python - <<'PY'
import json
import os
from datetime import datetime
from pathlib import Path

path = Path(os.environ["OUT_DIR"]) / "status.json"
try:
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    value = str(payload.get("jobs", {}).get(os.environ["JOB"], {}).get("last_success_utc") or "")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
    print(int(parsed.timestamp()) if parsed is not None else 0)
except (AttributeError, OSError, TypeError, ValueError, json.JSONDecodeError):
    print(0)
PY
}

seconds_since_success_stamp() {
  NOW=$(date -u +%s)
  THEN=$(successful_completion_epoch "$1")
  case "${THEN:-}" in
    ''|*[!0-9]*) THEN=0 ;;
  esac
  if [ "${THEN:-0}" -le 0 ]; then
    echo 999999999
    return
  fi
  AGE=$((NOW - THEN))
  if [ "$AGE" -lt 0 ]; then
    AGE=0
  fi
  echo "$AGE"
}

touch_success_stamp() {
  date -u +%s > "$OUT_DIR/last_success_$1"
}

touch_attempt_stamp() {
  date -u +%s > "$OUT_DIR/last_attempt_$1"
}

seconds_since_attempt_stamp() {
  ATTEMPT_STAMP="$OUT_DIR/last_attempt_$1"
  if [ ! -f "$ATTEMPT_STAMP" ]; then
    echo 999999999
    return
  fi
  NOW=$(date -u +%s)
  THEN=$(cat "$ATTEMPT_STAMP" 2>/dev/null || echo 0)
  case "${THEN:-}" in
    ''|*[!0-9]*) THEN=0 ;;
  esac
  AGE=$((NOW - THEN))
  if [ "$AGE" -lt 0 ]; then
    AGE=0
  fi
  echo "$AGE"
}

training_harvest_retry_ready() {
  [ "$(seconds_since_success_stamp training_harvest)" -ge "$HARVEST_INTERVAL" ] || return 1
  [ "$(seconds_since_attempt_stamp training_harvest)" -ge "$HARVEST_RETRY_INTERVAL" ]
}

successful_schedule_skip_kind() {
  THEN=$(successful_completion_epoch "$1")
  case "${THEN:-}" in
    ''|*[!0-9]*) THEN=0 ;;
  esac
  if [ "${THEN:-0}" -le 0 ]; then
    echo ""
    return
  fi
  INTERVAL="$2"
  AGE=$(seconds_since_success_stamp "$1")
  if [ "$AGE" -gt $((INTERVAL + TICK_SECONDS)) ]; then
    echo "overrun"
  else
    echo ""
  fi
}

schedule_skip_kind() {
  STAMP_FILE="$OUT_DIR/last_$1"
  INTERVAL="$2"
  if [ ! -f "$STAMP_FILE" ]; then
    echo ""
    return
  fi
  # A zero stamp is the registered deploy signal for "run immediately". It is
  # deliberate, not a missed cadence, so a successful forced refresh must
  # clear rather than increment the consecutive-overrun SLO counter.
  STAMP_VALUE="$(cat "$STAMP_FILE" 2>/dev/null || echo 0)"
  if [ "$STAMP_VALUE" = "0" ]; then
    echo ""
    return
  fi
  AGE="$(seconds_since_stamp "$1")"
  if [ "$AGE" -gt $((INTERVAL + TICK_SECONDS)) ]; then
    echo "overrun"
  else
    echo ""
  fi
}

odds_quota_available() {
  # The /v4/sports endpoint is free and returns x-requests-remaining.
  # WO-120: distinguish "quota is genuinely 0" from "the preflight itself
  # failed" (DNS/TLS/401/timeout). Both used to collapse to an intentional
  # skip that REFRESHED the job's success stamp, so a dead network or revoked
  # key kept the odds lanes green forever. Return codes:
  #   0 = quota available; 1 = quota exhausted (intentional skip);
  #   2 = preflight error (callers stamp a loud failure).
  # An UNSET key stays an intentional skip: it is a deliberate, already
  # distinguishable configuration state, not a laundered failure.
  if [ -z "${THE_ODDS_API_KEY:-}" ]; then
    log "quota preflight: THE_ODDS_API_KEY unset"
    return 1
  fi
  HEADERS=$(curl -fsS -D - -o /dev/null "https://api.the-odds-api.com/v4/sports/?apiKey=${THE_ODDS_API_KEY}" 2>/dev/null) || {
    log "quota preflight: request FAILED (network/TLS/credential)"
    return 2
  }
  REMAINING=$(printf '%s' "$HEADERS" | tr -d '\r' | awk -F': ' 'tolower($1)=="x-requests-remaining" {print $2}')
  log "quota preflight: x-requests-remaining=${REMAINING:-unknown}"
  case "${REMAINING:-}" in
    ''|*[!0-9.]*)
      log "quota preflight: quota header missing/unparseable"
      return 2
      ;;
  esac
  awk "BEGIN{exit !((${REMAINING}) > 0)}"
}

run_governance_refresh() {
  log "governance_refresh: starting"
  GOVERNANCE_STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  (
    set -e
    timeout "$GOVERNANCE_TIMEOUT" python -m polymarket_predictive_engine.cli refresh-governance --config "$CONFIG_PATH"
    timeout 300 python -m polymarket_predictive_engine.cli operating-state --config "$CONFIG_PATH"
    timeout 300 python scripts/render_polymarket_dashboard.py --config "$CONFIG_PATH"
  ) >> "$LOG_FILE" 2>&1 &
  JOB_PID=$!
  wait_with_safety_pulses "$JOB_PID" governance_refresh
  CODE=$?
  stamp_status governance_refresh "$CODE" "refresh-governance + operating-state + dashboard via ops scheduler" "$GOVERNANCE_STARTED_AT"
  log "governance_refresh: exit $CODE"
}

run_clv_snapshot() {
  odds_quota_available
  QUOTA_STATE=$?
  if [ "$QUOTA_STATE" -eq 2 ]; then
    # WO-120: a broken preflight is a FAILURE (nonzero, no success-stamp
    # refresh), not an intentional skip - the lane must go loud, not green.
    stamp_status clv_snapshot 1 "odds preflight failed (network/TLS/credential); see ops_scheduler.log"
    log "clv_snapshot: odds preflight FAILED"
    return
  fi
  if [ "$QUOTA_STATE" -ne 0 ]; then
    stamp_status clv_snapshot 0 "skipped: odds quota exhausted" "" intentional
    log "clv_snapshot: skipped (no odds credits)"
    return
  fi
  log "clv_snapshot: starting"
  CLV_STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  (
    set -e
    timeout "$CLV_TIMEOUT" python scripts/superbru_clv_experiment.py snapshot
    timeout "$CLV_TIMEOUT" python scripts/superbru_clv_experiment.py extract-picks || true
    timeout "$CLV_TIMEOUT" python scripts/superbru_clv_experiment.py report
  ) >> "$LOG_FILE" 2>&1 &
  JOB_PID=$!
  wait_with_safety_pulses "$JOB_PID" clv_snapshot
  CODE=$?
  stamp_status clv_snapshot "$CODE" "snapshot + report (extract-picks best-effort: VPS-side card edits are uncommitted)" "$CLV_STARTED_AT"
  log "clv_snapshot: exit $CODE"
}

run_locked_card_refresh() {
  if [ "$CARD_REFRESH_ENABLED" = "0" ]; then
    stamp_status locked_card_refresh 0 "skipped: locked-card refresh disabled (OPS_CARD_REFRESH_ENABLED=0)" "" intentional
    log "locked_card_refresh: skipped (disabled)"
    return
  fi
  odds_quota_available
  QUOTA_STATE=$?
  if [ "$QUOTA_STATE" -eq 2 ]; then
    stamp_status locked_card_refresh 1 "odds preflight failed (network/TLS/credential); see ops_scheduler.log"
    log "locked_card_refresh: odds preflight FAILED"
    return
  fi
  if [ "$QUOTA_STATE" -ne 0 ]; then
    stamp_status locked_card_refresh 0 "skipped: odds quota exhausted" "" intentional
    log "locked_card_refresh: skipped (no odds credits)"
    return
  fi
  log "locked_card_refresh: starting"
  CARD_STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  (
    set -e
    mkdir -p outputs/superbru_pool outputs/market_odds outputs/latest \
      outputs/final_leader_decision_round_summary_profiles data

    python scripts/scrape_superbru_leaderboard.py \
      --out-csv outputs/superbru_pool/live_pool_leaderboard.csv \
      --summary-json outputs/superbru_pool/live_pool_leaderboard_summary.json \
      --headless

    python scripts/build_live_chaser_profiles.py \
      --leaderboard-csv outputs/superbru_pool/live_pool_leaderboard.csv \
      --leader-player "$LEADER_PLAYER" \
      --chaser-range 8.0 --min-chasers 3 --max-chasers 10 \
      --out-csv outputs/superbru_pool/live_chaser_profiles.csv \
      --out-chasers outputs/superbru_pool/live_chasers.txt \
      --summary-json outputs/superbru_pool/live_chaser_profiles_summary.json

    printf 'home_team,away_team,flag_type,severity,notes,block_switch\n' > outputs/superbru_pool/live_manual_match_flags.csv
    python scripts/assert_fresh_superbru_refresh_inputs.py --mode pre-odds

    rm -f outputs/market_odds/worldcup_market_odds_raw.error.json
    python scripts/fetch_market_odds_theoddsapi.py \
      --sport soccer_fifa_world_cup \
      --regions eu \
      --markets h2h,totals \
      --out-json outputs/market_odds/worldcup_market_odds_raw.json \
      --out-csv outputs/market_odds/worldcup_market_odds_flat.csv \
      --no-allow-stale-on-failure \
      --no-allow-empty-on-failure
    test ! -f outputs/market_odds/worldcup_market_odds_raw.error.json
    test -s outputs/market_odds/worldcup_market_odds_flat.csv

    python - <<'PY'
import csv
from pathlib import Path

flat_path = Path('outputs/market_odds/worldcup_market_odds_flat.csv')
fixture_path = Path('data/fixtures_real.csv')
by_event = {}
with flat_path.open(encoding='utf-8-sig', newline='') as handle:
    for row in csv.DictReader(handle):
        event_id = (row.get('event_id') or '').strip()
        if not event_id or event_id in by_event:
            continue
        by_event[event_id] = {
            'match_id': event_id,
            'commence_time': row.get('commence_time', ''),
            'home_team': row.get('home_team', ''),
            'away_team': row.get('away_team', ''),
            'neutral': 'true',
            'venue_country': '',
            'stage': '',
        }
fixtures = sorted(by_event.values(), key=lambda item: item.get('commence_time', ''))
if not fixtures:
    raise SystemExit('No fixtures could be derived from fresh market odds.')
with fixture_path.open('w', encoding='utf-8', newline='') as handle:
    writer = csv.DictWriter(handle, fieldnames=['match_id', 'commence_time', 'home_team', 'away_team', 'neutral', 'venue_country', 'stage'])
    writer.writeheader()
    writer.writerows(fixtures)
print(f'Wrote {fixture_path} with {len(fixtures)} fixtures')
PY

    LIVE_CHASERS="$(cat outputs/superbru_pool/live_chasers.txt)"
    python -m superbru_score_engine predict \
      --config config.yaml \
      --fixtures data/fixtures_real.csv \
      --odds-json outputs/market_odds/worldcup_market_odds_raw.json \
      --out-dir outputs/latest

    python scripts/run_final_leader_decision.py \
      --fixtures data/fixtures_real.csv \
      --odds-json outputs/market_odds/worldcup_market_odds_raw.json \
      --predictions-csv outputs/latest/predictions.csv \
      --leaderboard-csv outputs/superbru_pool/live_pool_leaderboard.csv \
      --chaser-profiles-csv outputs/superbru_pool/live_chaser_profiles.csv \
      --leader-player "$LEADER_PLAYER" \
      --chasers "$LIVE_CHASERS" \
      --out-dir outputs/final_leader_decision_round_summary_profiles

    python scripts/run_daily_robust_pipeline.py \
      --skip-final-simulation \
      --skip-market-odds-fetch \
      --fixtures data/fixtures_real.csv \
      --odds-json outputs/market_odds/worldcup_market_odds_raw.json \
      --leaderboard-csv outputs/superbru_pool/live_pool_leaderboard.csv \
      --chaser-profiles-csv outputs/superbru_pool/live_chaser_profiles.csv \
      --leader-player "$LEADER_PLAYER" \
      --chasers "$LIVE_CHASERS" \
      --manual-flags-csv outputs/superbru_pool/live_manual_match_flags.csv

    python scripts/assert_fresh_superbru_refresh_inputs.py --mode pre-commit
    test -s outputs/final_locked_picks/superbru_final_card.csv

    python scripts/log_prediction_snapshots.py \
      --flat-odds-csv outputs/market_odds/worldcup_market_odds_flat.csv \
      --log-csv outputs/backtesting/prediction_log.csv

    # Oddspedia validation is best-effort: it needs a node runtime and an
    # external source; --allow-source-unavailable already tolerates outages.
    python scripts/run_oddspedia_pipeline.py \
      --locked-picks-csv outputs/final_locked_picks/superbru_final_card.csv \
      --results-csv outputs/superbru_pool/superbru_match_results_auto.csv \
      --allow-source-unavailable || echo "oddspedia validation unavailable (non-blocking)"

    python scripts/audit_superbru_validation_data_freshness.py || true
  ) >> "$LOG_FILE" 2>&1 &
  JOB_PID=$!
  wait_with_safety_pulses "$JOB_PID" locked_card_refresh
  CODE=$?
  stamp_status locked_card_refresh "$CODE" "full locked-card chain (see ops_scheduler.log)" "$CARD_STARTED_AT"
  log "locked_card_refresh: exit $CODE"
}

run_training_harvest() {
  # Resolved-market corpus: Gamma closed markets + CLOB price histories are
  # free (no API key, no odds credits) and give outcome-LABELLED training
  # sequences across thousands of markets - the direct attack on the
  # validation-gap and cohort-transfer blockers. Harvest accrues to outputs;
  # trainer wiring is a separate leakage-reviewed work order (WO-33).
  log "training_harvest: starting"
  HARVEST_STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  # WO-85: every child result is persisted, earlier failures do not starve
  # later evidence, ordinary work not started before the whole-job deadline
  # is skipped explicitly, and retention + anchoring are always attempted last.
  # The registered child list includes scripts/audit_polymarket_local_history.py
  # before operating-state; see training_harvest.py for the complete order.
  # Contract markers retained for the scheduler audit: backfill-resolved-markets,
  # collect-price-history, maker-carry-study, decision-policy, and
  # backfill-trade-prints are all registered children of that orchestrator.
  # Its exact safety/report children include:
  # python -m polymarket_predictive_engine.cli stage-day
  # python -m polymarket_predictive_engine.cli corpus-retention
  # python -m polymarket_predictive_engine.cli anchor-ledgers
  (
    OPS_TRAINING_HARVEST_TIMEOUT_SECONDS="$HARVEST_TIMEOUT" \
    OPS_TRADE_PRINTS_TIMEOUT_SECONDS="$PRINTS_TIMEOUT" \
    OPS_TRAINING_HARVEST_DEADLINE_SECONDS="$HARVEST_DEADLINE" \
      python -m polymarket_predictive_engine.cli training-harvest --config "$CONFIG_PATH"
  ) >> "$LOG_FILE" 2>&1 &
  JOB_PID=$!
  wait_with_safety_pulses "$JOB_PID" training_harvest
  CODE=$?
  # WO-128: the Python orchestrator attempts anchoring last, but an anchor-only
  # failure must not cause hours of successful collection to run again. Derive
  # independent outcomes from its per-step artifact and stamp both loudly.
  set -- $(OUT_DIR="$OUT_DIR" OVERALL_CODE="$CODE" python - <<'PY'
import json
import os
from pathlib import Path

overall = int(os.environ["OVERALL_CODE"])
try:
    payload = json.loads((Path(os.environ["OUT_DIR"]) / "training_harvest.json").read_text(encoding="utf-8"))
    rows = payload.get("steps", [])
    anchor = next(row for row in rows if row.get("step") == "anchor_ledgers")
    anchor_code = int(anchor.get("exit_code"))
    tolerated_steps = {"collect_price_history", "maker_carry_study"}
    def tolerated_timeout(row):
        return (
            row.get("step") in tolerated_steps
            and row.get("status") == "timed_out"
            and row.get("timeout_degrades_coverage") is True
        )
    harvest_failed = any(
        row.get("step") != "anchor_ledgers"
        and (
            row.get("status") in {"failed", "skipped_deadline"}
            or (row.get("status") == "timed_out" and not tolerated_timeout(row))
        )
        for row in rows
    )
    coverage_steps = payload.get("coverage_degraded_steps")
    coverage_degraded = (
        not isinstance(coverage_steps, list)
        or bool(coverage_steps)
        or any(tolerated_timeout(row) for row in rows)
    )
    print(1 if harvest_failed else 0, anchor_code, 1 if coverage_degraded else 0)
except (AttributeError, KeyError, OSError, StopIteration, TypeError, ValueError, json.JSONDecodeError):
    # Missing or malformed accounting fails closed under the process outcome.
    print(overall, overall, 1)
PY
  )
  HARVEST_CODE="$1"
  ANCHOR_TAIL_CODE="$2"
  COVERAGE_DEGRADED="$3"
  SUCCESS_ELIGIBLE=true
  if [ "$COVERAGE_DEGRADED" -ne 0 ]; then SUCCESS_ELIGIBLE=false; fi
  stamp_status training_harvest "$HARVEST_CODE" "WO-85 resilient per-step harvest excluding independently stamped anchor tail; see ops_scheduler/training_harvest.json" "$HARVEST_STARTED_AT" "" "$SUCCESS_ELIGIBLE"
  stamp_status training_harvest_anchor_tail "$ANCHOR_TAIL_CODE" "WO-128 independent anchor-ledgers tail from training harvest; failure remains visible without repeating collection" "$HARVEST_STARTED_AT"
  if [ "$HARVEST_CODE" -eq 0 ] && [ "$COVERAGE_DEGRADED" -eq 0 ]; then
    touch_success_stamp training_harvest
  fi
  log "training_harvest: harvest exit $HARVEST_CODE; anchor tail exit $ANCHOR_TAIL_CODE; process exit $CODE"
}

run_maker_study_intraday() {
  # WO-53: a second maker-carry snapshot ~12h after the daily harvest samples
  # intraday competition without fast-forwarding M-A, because M-A counts
  # distinct UTC days in maker_carry_study.py.
  TRAINING_AGE="$(seconds_since_success_stamp training_harvest)"
  log "maker_study_intraday: starting (training_harvest_age=${TRAINING_AGE}s)"
  (
    set -e
    timeout "$HARVEST_TIMEOUT" python -m polymarket_predictive_engine.cli maker-carry-study --config "$CONFIG_PATH"
    python -m polymarket_predictive_engine.cli reward-epoch-sample --config "$CONFIG_PATH"
    timeout "$PRINTS_TIMEOUT" python -m polymarket_predictive_engine.cli collect-maker-replay-data --config "$CONFIG_PATH"
    timeout "$PRINTS_TIMEOUT" python -m polymarket_predictive_engine.cli maker-fill-replay --config "$CONFIG_PATH"
  ) >> "$LOG_FILE" 2>&1 &
  JOB_PID=$!
  wait_with_safety_pulses "$JOB_PID" maker_study_intraday
  CODE=$?
  stamp_status maker_study_intraday "$CODE" "intraday maker-carry-study; training_harvest_age=${TRAINING_AGE}s; 11-13h offset guard"
  log "maker_study_intraday: exit $CODE"
}

run_trade_prints() {
  # Public data-API, no key, no odds credits: executed trades (price/size/side)
  # for the markets the websocket collector already tracks. Signed flow is
  # training substrate; nothing here trades or gates.
  log "trade_prints: starting"
  PRINTS_STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  (
    set -e
    timeout "$PRINTS_TIMEOUT" python -m polymarket_predictive_engine.cli collect-trade-prints --config "$CONFIG_PATH"
    # WO-83: exact quote-sheet coverage, independent of websocket discovery.
    # A successful zero-print poll is evidence; a failed poll is a visible gap.
    timeout "$PRINTS_TIMEOUT" python -m polymarket_predictive_engine.cli collect-maker-replay-data --config "$CONFIG_PATH"
    timeout "$PRINTS_TIMEOUT" python -m polymarket_predictive_engine.cli maker-fill-replay --config "$CONFIG_PATH"
    # WO-34: negRisk sum-constraint scan rides the same 15-min cadence -
    # deviation persistence is only measurable at print-level frequency.
    timeout "$PRINTS_TIMEOUT" python -m polymarket_predictive_engine.cli scan-event-groups --config "$CONFIG_PATH"
    # WO-41: implication-network Frechet/Boole consistency scan rides the same
    # cadence. Measurement only; no signals, gates, or order paths.
    timeout "$PRINTS_TIMEOUT" python -m polymarket_predictive_engine.cli scan-implication-networks --config "$CONFIG_PATH"
  ) >> "$LOG_FILE" 2>&1 &
  JOB_PID=$!
  wait_with_safety_pulses "$JOB_PID" trade_prints
  CODE=$?
  stamp_status trade_prints "$CODE" "data-api /trades + exact Tier-0 maker book/print coverage + fill replay + consistency scans" "$PRINTS_STARTED_AT"
  log "trade_prints: exit $CODE"
}

run_maker_safety_refresh() {
  # WO-85/WO-86/WO-88: keep the human maker scoreboard, fail-safe kill
  # decision, and pull/STOP advice on their own short cadence. This lane is
  # reporting-only and remains live while collection/research jobs run.
  log "maker_safety_refresh: starting"
  # WO-120: job-local start stamp - the shared STARTED_AT global was clobbered
  # by concurrent safety pulses, mismeasuring durations.
  MAKER_SAFETY_STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  (
    set -e
    timeout "$PRINTS_TIMEOUT" python -m polymarket_predictive_engine.cli maker-live-test --config "$CONFIG_PATH"
    timeout "$PRINTS_TIMEOUT" python -m polymarket_predictive_engine.cli decision-policy --config "$CONFIG_PATH"
    timeout "$PRINTS_TIMEOUT" python -m polymarket_predictive_engine.cli requote-alerts --config "$CONFIG_PATH"
    # WO-105: sharp-linking funding evaluator (fail-closed) BEFORE WO-99 so the
    # qualification artifact is fresh when the eligibility gate reads it.
    timeout "$PRINTS_TIMEOUT" python -m polymarket_predictive_engine.cli sharp-linking-evaluator --config "$CONFIG_PATH"
    # WO-99: owner stage-ticket eligibility + transition-only ntfy push.
    timeout "$PRINTS_TIMEOUT" python -m polymarket_predictive_engine.cli stage-ticket-eligibility --config "$CONFIG_PATH"
  ) >> "$LOG_FILE" 2>&1
  CODE=$?
  stamp_status maker_safety_refresh "$CODE" "read-only maker attribution + kill decision + requote advice" "$MAKER_SAFETY_STARTED_AT"
  log "maker_safety_refresh: exit $CODE"
}

run_degraded_state_watchdog() {
  # WO-75: the executor monitor is a scheduler-owned process, independent of
  # the future executor that owns the ledger and heartbeat. It runs every tick
  # and only reports/alerts; it never writes the heartbeat or controls orders.
  # WO-78 then inspects the freshly stamped scheduler result. Operating state
  # and the dashboard are refreshed regardless, so an alerting failure cannot
  # leave the oversight surface stale.
  log "executor_ops_monitor: starting"
  PULSE_STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  timeout 120 python -m polymarket_predictive_engine.cli executor-ops-monitor --config "$CONFIG_PATH" >> "$LOG_FILE" 2>&1
  MONITOR_CODE=$?
  stamp_status executor_ops_monitor "$MONITOR_CODE" "independent future-executor ledger/heartbeat monitor; read-only" "$PULSE_STARTED_AT"
  log "executor_ops_monitor: exit $MONITOR_CODE"

  log "degraded_state_watchdog: starting"
  timeout 120 python -m polymarket_predictive_engine.cli degraded-state-watchdog --config "$CONFIG_PATH" >> "$LOG_FILE" 2>&1
  WATCHDOG_CODE=$?
  timeout 120 python -m polymarket_predictive_engine.cli operating-state --config "$CONFIG_PATH" >> "$LOG_FILE" 2>&1
  OPERATING_CODE=$?
  timeout 120 python scripts/render_polymarket_dashboard.py --config "$CONFIG_PATH" >> "$LOG_FILE" 2>&1
  DASHBOARD_CODE=$?

  CODE=0
  for COMPONENT_CODE in "$MONITOR_CODE" "$WATCHDOG_CODE" "$OPERATING_CODE" "$DASHBOARD_CODE"; do
    if [ "$COMPONENT_CODE" -ne 0 ] && [ "$CODE" -eq 0 ]; then
      CODE="$COMPONENT_CODE"
    fi
  done
  if [ "$WATCHDOG_CODE" -ne 0 ]; then
    log "degraded_state_watchdog: watchdog exit $WATCHDOG_CODE"
  fi
  stamp_status degraded_state_watchdog "$CODE" "executor monitor + semantic watchdog + operating state + dashboard; reporting only" "$PULSE_STARTED_AT"
  log "degraded_state_watchdog: exit $CODE"
}

run_ledger_anchor() {
  # 2026-07-19 deploy-acceptance fix: the ledger-anchor lane (WO-61) had NO
  # recurring owner — the last head predated acceptance's 36h SLO by days and
  # failed deploy run #99. The CLI refreshes the chained head + snapshots
  # (in-container safe: writes only under outputs/). The external git push is
  # host-only (needs the checkout's .git and push credentials); inside the
  # container push_vps_anchor.sh exits 0 with a log line, so it is best-effort
  # here and functional when the scheduler runs on the host.
  log "ledger_anchor: starting"
  LEDGER_ANCHOR_STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  (
    set -e
    timeout "$LEDGER_ANCHOR_TIMEOUT" python -m polymarket_predictive_engine.cli anchor-ledgers --config "$CONFIG_PATH"
    sh scripts/push_vps_anchor.sh || true
  ) >> "$LOG_FILE" 2>&1 &
  JOB_PID=$!
  wait_with_safety_pulses "$JOB_PID" ledger_anchor
  CODE=$?
  stamp_status ledger_anchor "$CODE" "WO-61 ledger-chain head refresh; external vps-anchor push best-effort" "$LEDGER_ANCHOR_STARTED_AT"
  log "ledger_anchor: exit $CODE"
}

run_safety_pulse() {
  # Preserve the parent job's schedule classification. Safety jobs are
  # independent cadence owners and must never inherit an overrun/intentional
  # marker from the long job whose child process is being observed.
  PARENT_SCHEDULE_SKIP_KIND="$JOB_SCHEDULE_SKIP_KIND"
  JOB_SCHEDULE_SKIP_KIND=""
  if [ "$(seconds_since_stamp maker_safety_refresh)" -ge "$MAKER_SAFETY_INTERVAL" ]; then
    touch_stamp maker_safety_refresh
    run_maker_safety_refresh
  fi
  run_degraded_state_watchdog
  JOB_SCHEDULE_SKIP_KIND="$PARENT_SCHEDULE_SKIP_KIND"
}

wait_with_safety_pulses() {
  # Long jobs remain serialized exactly as before, but they run as a bounded
  # child so the parent can keep the fail-safe reporting loop alive. Polling
  # only observes/reaps this scheduler-owned child; it never starts a second
  # copy of the long job.
  OBSERVED_PID="$1"
  OBSERVED_JOB="$2"
  LAST_SAFETY_PULSE=0
  while kill -0 "$OBSERVED_PID" 2>/dev/null; do
    NOW_EPOCH=$(date -u +%s)
    if [ $((NOW_EPOCH - LAST_SAFETY_PULSE)) -ge "$SAFETY_PULSE_INTERVAL" ]; then
      log "$OBSERVED_JOB: long job active; running independent safety pulse"
      run_safety_pulse
      LAST_SAFETY_PULSE=$(date -u +%s)
    fi
    sleep "$SAFETY_POLL_SECONDS"
  done
  wait "$OBSERVED_PID"
}

if [ "${OPS_SCHEDULER_LIBRARY_ONLY:-0}" = "1" ]; then
  return 0 2>/dev/null || exit 0
fi

log "vps-ops-scheduler starting: governance=${GOVERNANCE_INTERVAL}s clv=${CLV_INTERVAL}s card=${CARD_INTERVAL}s harvest=${HARVEST_INTERVAL}s tick=${TICK_SECONDS}s"
stamp_status scheduler 0 "started"

while :; do
  # Top-of-loop rotation: no job subshell holds the log open at this point.
  rotate_log_if_needed
  if [ "$(seconds_since_stamp governance_refresh)" -ge "$GOVERNANCE_INTERVAL" ]; then
    JOB_SCHEDULE_SKIP_KIND="$(schedule_skip_kind governance_refresh "$GOVERNANCE_INTERVAL")"
    touch_stamp governance_refresh
    run_governance_refresh
    JOB_SCHEDULE_SKIP_KIND=""
  fi
  if [ "$(seconds_since_stamp clv_snapshot)" -ge "$CLV_INTERVAL" ]; then
    JOB_SCHEDULE_SKIP_KIND="$(schedule_skip_kind clv_snapshot "$CLV_INTERVAL")"
    touch_stamp clv_snapshot
    run_clv_snapshot
    JOB_SCHEDULE_SKIP_KIND=""
  fi
  if [ "$(seconds_since_stamp locked_card_refresh)" -ge "$CARD_INTERVAL" ]; then
    JOB_SCHEDULE_SKIP_KIND="$(schedule_skip_kind locked_card_refresh "$CARD_INTERVAL")"
    touch_stamp locked_card_refresh
    run_locked_card_refresh
    JOB_SCHEDULE_SKIP_KIND=""
  fi
  if training_harvest_retry_ready; then
    JOB_SCHEDULE_SKIP_KIND="$(successful_schedule_skip_kind training_harvest "$HARVEST_INTERVAL")"
    touch_attempt_stamp training_harvest
    run_training_harvest
    JOB_SCHEDULE_SKIP_KIND=""
  fi
  if [ "$(seconds_since_stamp maker_study_intraday)" -ge "$MAKER_STUDY_INTRADAY_INTERVAL" ]; then
    TRAINING_AGE="$(seconds_since_success_stamp training_harvest)"
    if [ "$TRAINING_AGE" -ge "$MAKER_STUDY_INTRADAY_OFFSET_MIN" ] && [ "$TRAINING_AGE" -le "$MAKER_STUDY_INTRADAY_OFFSET_MAX" ]; then
      # WO-117 measurement correctness: this job may only FIRE inside the
      # registered 11-13h harvest-age window, and that window's daily
      # recurrence drifts by more than TICK_SECONDS (harvest runtime plus loop
      # slack), so judging lateness against the bare 24h interval stamped
      # every legitimate on-window run "overrun" (10 consecutive by
      # 2026-07-25, the sole source of the scheduler_overrun_cycles breach).
      # Allow one window width (OFFSET_MAX - OFFSET_MIN) of gate-induced
      # drift before calling a run overrun; a run later than that genuinely
      # missed a window and still stamps overrun. The SLO target itself is
      # untouched and still fires on real starvation.
      MAKER_STUDY_WINDOW_TOLERANCE=$((MAKER_STUDY_INTRADAY_OFFSET_MAX - MAKER_STUDY_INTRADAY_OFFSET_MIN))
      JOB_SCHEDULE_SKIP_KIND="$(schedule_skip_kind maker_study_intraday $((MAKER_STUDY_INTRADAY_INTERVAL + MAKER_STUDY_WINDOW_TOLERANCE)))"
      touch_stamp maker_study_intraday
      run_maker_study_intraday
      JOB_SCHEDULE_SKIP_KIND=""
    fi
  fi
  if [ "$(seconds_since_stamp trade_prints)" -ge "$PRINTS_INTERVAL" ]; then
    JOB_SCHEDULE_SKIP_KIND="$(schedule_skip_kind trade_prints "$PRINTS_INTERVAL")"
    touch_stamp trade_prints
    run_trade_prints
    JOB_SCHEDULE_SKIP_KIND=""
  fi
  if [ "$(seconds_since_stamp ledger_anchor)" -ge "$LEDGER_ANCHOR_INTERVAL" ]; then
    JOB_SCHEDULE_SKIP_KIND="$(schedule_skip_kind ledger_anchor "$LEDGER_ANCHOR_INTERVAL")"
    touch_stamp ledger_anchor
    run_ledger_anchor
    JOB_SCHEDULE_SKIP_KIND=""
  fi
  run_safety_pulse
  sleep "$TICK_SECONDS"
done
