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
MAKER_STUDY_INTRADAY_INTERVAL="${OPS_MAKER_STUDY_INTRADAY_INTERVAL_SECONDS:-86400}"
MAKER_STUDY_INTRADAY_OFFSET_MIN="${OPS_MAKER_STUDY_INTRADAY_OFFSET_MIN_SECONDS:-39600}"
MAKER_STUDY_INTRADAY_OFFSET_MAX="${OPS_MAKER_STUDY_INTRADAY_OFFSET_MAX_SECONDS:-46800}"
PRINTS_INTERVAL="${OPS_TRADE_PRINTS_INTERVAL_SECONDS:-900}"
PRINTS_TIMEOUT="${OPS_TRADE_PRINTS_TIMEOUT_SECONDS:-300}"
GOVERNANCE_TIMEOUT="${OPS_GOVERNANCE_TIMEOUT_SECONDS:-1500}"
CLV_TIMEOUT="${OPS_CLV_TIMEOUT_SECONDS:-900}"
CARD_TIMEOUT="${OPS_CARD_TIMEOUT_SECONDS:-2400}"
LEADER_PLAYER="${SUPERBRU_PLAYER_NAME:-Danie}"
export PYTHONPATH="${PYTHONPATH:-scripts:src}"

mkdir -p "$OUT_DIR"
LOG_FILE="$OUT_DIR/ops_scheduler.log"

log() {
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" | tee -a "$LOG_FILE"
}

stamp_status() {
  JOB="$1" EXIT_CODE="$2" DETAIL="${3:-}" OUT_DIR="$OUT_DIR" python - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

path = Path(os.environ["OUT_DIR"]) / "status.json"
try:
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
except Exception:
    payload = {}
payload.setdefault("jobs", {})[os.environ["JOB"]] = {
    "last_run_utc": datetime.now(timezone.utc).isoformat(),
    "last_exit_code": int(os.environ["EXIT_CODE"]),
    "detail": os.environ.get("DETAIL", ""),
}
payload["mode"] = "vps_ops_scheduler"
payload["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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
  echo $((NOW - THEN))
}

touch_stamp() {
  date -u +%s > "$OUT_DIR/last_$1"
}

odds_quota_available() {
  # The /v4/sports endpoint is free and returns x-requests-remaining.
  if [ -z "${THE_ODDS_API_KEY:-}" ]; then
    log "quota preflight: THE_ODDS_API_KEY unset"
    return 1
  fi
  REMAINING=$(curl -fsS -D - -o /dev/null "https://api.the-odds-api.com/v4/sports/?apiKey=${THE_ODDS_API_KEY}" 2>/dev/null | tr -d '\r' | awk -F': ' 'tolower($1)=="x-requests-remaining" {print $2}')
  log "quota preflight: x-requests-remaining=${REMAINING:-unknown}"
  awk "BEGIN{exit !((${REMAINING:-0}) > 0)}"
}

run_governance_refresh() {
  log "governance_refresh: starting"
  timeout "$GOVERNANCE_TIMEOUT" python -m polymarket_predictive_engine.cli refresh-governance --config "$CONFIG_PATH" >> "$LOG_FILE" 2>&1
  CODE=$?
  stamp_status governance_refresh "$CODE" "refresh-governance via ops scheduler"
  log "governance_refresh: exit $CODE"
}

run_clv_snapshot() {
  if ! odds_quota_available; then
    stamp_status clv_snapshot 0 "skipped: odds quota exhausted"
    log "clv_snapshot: skipped (no odds credits)"
    return
  fi
  log "clv_snapshot: starting"
  (
    set -e
    timeout "$CLV_TIMEOUT" python scripts/superbru_clv_experiment.py snapshot
    timeout "$CLV_TIMEOUT" python scripts/superbru_clv_experiment.py extract-picks || true
    timeout "$CLV_TIMEOUT" python scripts/superbru_clv_experiment.py report
  ) >> "$LOG_FILE" 2>&1
  CODE=$?
  stamp_status clv_snapshot "$CODE" "snapshot + report (extract-picks best-effort: VPS-side card edits are uncommitted)"
  log "clv_snapshot: exit $CODE"
}

run_locked_card_refresh() {
  if ! odds_quota_available; then
    stamp_status locked_card_refresh 0 "skipped: odds quota exhausted"
    log "locked_card_refresh: skipped (no odds credits)"
    return
  fi
  log "locked_card_refresh: starting"
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
  ) >> "$LOG_FILE" 2>&1
  CODE=$?
  stamp_status locked_card_refresh "$CODE" "full locked-card chain (see ops_scheduler.log)"
  log "locked_card_refresh: exit $CODE"
}

run_training_harvest() {
  # Resolved-market corpus: Gamma closed markets + CLOB price histories are
  # free (no API key, no odds credits) and give outcome-LABELLED training
  # sequences across thousands of markets - the direct attack on the
  # validation-gap and cohort-transfer blockers. Harvest accrues to outputs;
  # trainer wiring is a separate leakage-reviewed work order (WO-33).
  log "training_harvest: starting"
  (
    set -e
    timeout "$HARVEST_TIMEOUT" python -m polymarket_predictive_engine.cli backfill-resolved-markets --config "$CONFIG_PATH"
    timeout "$HARVEST_TIMEOUT" python -m polymarket_predictive_engine.cli collect-price-history --config "$CONFIG_PATH"
    # WO-55 reconstructed sharp-anchor CLV study: retrospective research only,
    # explicitly non-verdict and non-trading; runs after price histories exist.
    timeout "$HARVEST_TIMEOUT" python -m polymarket_predictive_engine.cli reconstructed-clv-study --config "$CONFIG_PATH"
    # WO-43 martingale drift scan: study-only timing-edge diagnostics from
    # harvested price histories; no lane, no gate, no orders.
    timeout "$HARVEST_TIMEOUT" python -m polymarket_predictive_engine.cli drift-scan --config "$CONFIG_PATH"
    # WO-37 wallet-intelligence collection: leaderboard + holders for tracked
    # markets. Collection only; later leakage-reviewed work decides whether
    # any wallet signal becomes a feature.
    timeout "$HARVEST_TIMEOUT" python -m polymarket_predictive_engine.cli collect-wallet-intel --config "$CONFIG_PATH"
    # WO-36 maker-carry actuarial study: daily measurement (never trading) of
    # reward pots, band competition, and pick-off costs. Free public APIs.
    timeout "$HARVEST_TIMEOUT" python -m polymarket_predictive_engine.cli maker-carry-study --config "$CONFIG_PATH"
    # WO-40 maker-fill realism replay: recorded book archive + trade prints,
    # last-in-queue fills. Measurement only; never alters the study charge.
    timeout "$HARVEST_TIMEOUT" python -m polymarket_predictive_engine.cli maker-fill-replay --config "$CONFIG_PATH"
    # WO-49 flow toxicity: VPIN-lite + wallet-tier markouts for quote-sheet
    # conditioning only. Never alters adverse charges, gates, or order paths.
    timeout "$HARVEST_TIMEOUT" python -m polymarket_predictive_engine.cli flow-toxicity --config "$CONFIG_PATH"
    # WO-50 registered maker live-test decision policy: advisory-only
    # funding/stand-down indication after the daily maker evidence refresh.
    timeout "$HARVEST_TIMEOUT" python -m polymarket_predictive_engine.cli decision-policy --config "$CONFIG_PATH"
    # WO-42 favourite/longshot calibration bias study. Corpus-bound and
    # study-only; flags are candidates for future pre-registration, not trades.
    timeout "$HARVEST_TIMEOUT" python -m polymarket_predictive_engine.cli calibration-bias-study --config "$CONFIG_PATH"
  ) >> "$LOG_FILE" 2>&1
  CODE=$?
  stamp_status training_harvest "$CODE" "gamma resolved-markets backfill + clob price histories + wallet intelligence + maker-carry study + maker-fill replay + flow toxicity + decision policy + reconstructed CLV study + calibration-bias study + martingale drift scan"
  log "training_harvest: exit $CODE"
}

run_maker_study_intraday() {
  # WO-53: a second maker-carry snapshot ~12h after the daily harvest samples
  # intraday competition without fast-forwarding M-A, because M-A counts
  # distinct UTC days in maker_carry_study.py.
  TRAINING_AGE="$(seconds_since_stamp training_harvest)"
  log "maker_study_intraday: starting (training_harvest_age=${TRAINING_AGE}s)"
  timeout "$HARVEST_TIMEOUT" python -m polymarket_predictive_engine.cli maker-carry-study --config "$CONFIG_PATH" >> "$LOG_FILE" 2>&1
  CODE=$?
  stamp_status maker_study_intraday "$CODE" "intraday maker-carry-study; training_harvest_age=${TRAINING_AGE}s; 11-13h offset guard"
  log "maker_study_intraday: exit $CODE"
}

run_trade_prints() {
  # Public data-API, no key, no odds credits: executed trades (price/size/side)
  # for the markets the websocket collector already tracks. Signed flow is
  # training substrate; nothing here trades or gates.
  log "trade_prints: starting"
  (
    set -e
    timeout "$PRINTS_TIMEOUT" python -m polymarket_predictive_engine.cli collect-trade-prints --config "$CONFIG_PATH"
    # WO-34: negRisk sum-constraint scan rides the same 15-min cadence -
    # deviation persistence is only measurable at print-level frequency.
    timeout "$PRINTS_TIMEOUT" python -m polymarket_predictive_engine.cli scan-event-groups --config "$CONFIG_PATH"
    # WO-41: implication-network Frechet/Boole consistency scan rides the same
    # cadence. Measurement only; no signals, gates, or order paths.
    timeout "$PRINTS_TIMEOUT" python -m polymarket_predictive_engine.cli scan-implication-networks --config "$CONFIG_PATH"
    # WO-36 step 4: read-only scoreboard of the human's live maker test
    # (inert until maker_live_test.wallet_address is configured).
    timeout "$PRINTS_TIMEOUT" python -m polymarket_predictive_engine.cli maker-live-test --config "$CONFIG_PATH"
  ) >> "$LOG_FILE" 2>&1
  CODE=$?
  stamp_status trade_prints "$CODE" "data-api /trades for tracked markets + event-group and implication consistency scans"
  log "trade_prints: exit $CODE"
}

log "vps-ops-scheduler starting: governance=${GOVERNANCE_INTERVAL}s clv=${CLV_INTERVAL}s card=${CARD_INTERVAL}s harvest=${HARVEST_INTERVAL}s tick=${TICK_SECONDS}s"
stamp_status scheduler 0 "started"

while :; do
  if [ "$(seconds_since_stamp governance_refresh)" -ge "$GOVERNANCE_INTERVAL" ]; then
    touch_stamp governance_refresh
    run_governance_refresh
  fi
  if [ "$(seconds_since_stamp clv_snapshot)" -ge "$CLV_INTERVAL" ]; then
    touch_stamp clv_snapshot
    run_clv_snapshot
  fi
  if [ "$(seconds_since_stamp locked_card_refresh)" -ge "$CARD_INTERVAL" ]; then
    touch_stamp locked_card_refresh
    run_locked_card_refresh
  fi
  if [ "$(seconds_since_stamp training_harvest)" -ge "$HARVEST_INTERVAL" ]; then
    touch_stamp training_harvest
    run_training_harvest
  fi
  if [ "$(seconds_since_stamp maker_study_intraday)" -ge "$MAKER_STUDY_INTRADAY_INTERVAL" ]; then
    TRAINING_AGE="$(seconds_since_stamp training_harvest)"
    if [ "$TRAINING_AGE" -ge "$MAKER_STUDY_INTRADAY_OFFSET_MIN" ] && [ "$TRAINING_AGE" -le "$MAKER_STUDY_INTRADAY_OFFSET_MAX" ]; then
      touch_stamp maker_study_intraday
      run_maker_study_intraday
    fi
  fi
  if [ "$(seconds_since_stamp trade_prints)" -ge "$PRINTS_INTERVAL" ]; then
    touch_stamp trade_prints
    run_trade_prints
  fi
  sleep "$TICK_SECONDS"
done
