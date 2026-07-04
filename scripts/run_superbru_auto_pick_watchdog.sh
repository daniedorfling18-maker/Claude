#!/usr/bin/env sh
set -u

OUT_DIR="${SUPERBRU_AUTO_PICK_OUT_DIR:-outputs/pregame_checks/auto_pick_vps}"
INTERVAL_SECONDS="${SUPERBRU_AUTO_PICK_INTERVAL_SECONDS:-900}"
WINDOW_MINUTES="${SUPERBRU_AUTO_PICK_WINDOW_MINUTES:-260}"
TIMEOUT_SECONDS="${SUPERBRU_AUTO_PICK_TIMEOUT_SECONDS:-1500}"
LOGIN_URL="${SUPERBRU_LOGIN_URL:-https://www.superbru.com/login}"
POOL_URL="${SUPERBRU_POOL_URL:-https://www.superbru.com/worldcup_predictor/pool_view.php?t=1296&p=13236623&view=matches}"
PAGE_TIMEZONE="${SUPERBRU_PAGE_TIMEZONE:-Africa/Johannesburg}"
LEADER_PLAYER="${SUPERBRU_PLAYER_NAME:-Danie}"
POOL_KEYWORDS="${SUPERBRU_POOL_KEYWORDS:-Moore Infinity,FIFA WC 26,World Cup 2026}"
PICK_CARD_CSV="${SUPERBRU_PICK_CARD_CSV:-outputs/final_locked_picks/superbru_final_card.csv}"
CONFIG_PATH="${SUPERBRU_CONFIG_PATH:-config.yaml}"
LOG_FILE="${SUPERBRU_AUTO_PICK_LOG_FILE:-${OUT_DIR}/watchdog.log}"

mkdir -p "$OUT_DIR"
export OUT_DIR INTERVAL_SECONDS WINDOW_MINUTES PAGE_TIMEZONE

write_watchdog_status() {
  STATUS="$1" DETAIL="$2" EXIT_CODE="${3:-0}" python - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

out_dir = Path(os.environ.get("OUT_DIR", "outputs/pregame_checks/auto_pick_vps"))
out_dir.mkdir(parents=True, exist_ok=True)
payload = {
    "status": os.environ.get("STATUS", "unknown"),
    "detail": os.environ.get("DETAIL", ""),
    "exit_code": int(os.environ.get("EXIT_CODE", "0") or 0),
    "run_at_utc": datetime.now(timezone.utc).isoformat(),
    "mode": "vps_superbru_auto_pick_watchdog",
    "window_minutes": int(os.environ.get("WINDOW_MINUTES", "260") or 260),
    "interval_seconds": int(os.environ.get("INTERVAL_SECONDS", "900") or 900),
    "page_timezone": os.environ.get("PAGE_TIMEZONE", "Africa/Johannesburg"),
    "dry_run": False,
    "credentials_present": {
        "SUPERBRU_EMAIL_or_USERNAME": bool(os.environ.get("SUPERBRU_EMAIL") or os.environ.get("SUPERBRU_USERNAME")),
        "SUPERBRU_PASSWORD": bool(os.environ.get("SUPERBRU_PASSWORD")),
        "SUPERBRU_POOL_URL": bool(os.environ.get("SUPERBRU_POOL_URL")),
        "THE_ODDS_API_KEY": bool(os.environ.get("THE_ODDS_API_KEY")),
    },
}
summary_path = out_dir / "latest_auto_pick_match_scoped.json"
if summary_path.exists():
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        payload["last_result"] = {
            "status": summary.get("status"),
            "run_at_utc": summary.get("run_at_utc"),
            "queued_count": summary.get("queued_count"),
            "card_fallback_queued": summary.get("card_fallback_queued"),
            "submitted": summary.get("submitted"),
            "already_current_count": summary.get("already_current_count"),
            "submission_failed": summary.get("submission_failed"),
            "page_timezone": summary.get("page_timezone"),
        }
        scan_results = summary.get("scan_results") or []
        future = [
            row for row in scan_results
            if row.get("status") in {"queued", "not_in_window"}
            and row.get("kickoff_utc")
        ]
        future.sort(key=lambda row: row.get("kickoff_utc") or "")
        if future:
            row = future[0]
            payload["next_fixture"] = {
                "game": row.get("game"),
                "kickoff_utc": row.get("kickoff_utc"),
                "minutes_until": row.get("minutes_until"),
                "status": row.get("status"),
                "inputs_found": row.get("inputs_found"),
            }
    except Exception as exc:
        payload["last_result_error"] = str(exc)
(out_dir / "latest_watchdog_status.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
PY
}

if [ "${SUPERBRU_AUTO_PICK_ENABLED:-true}" != "true" ]; then
  write_watchdog_status "disabled" "SUPERBRU_AUTO_PICK_ENABLED is not true" 0
  echo "SuperBru auto-pick watchdog disabled."
  tail -f /dev/null
fi

while true; do
  if { [ -z "${SUPERBRU_EMAIL:-}" ] && [ -z "${SUPERBRU_USERNAME:-}" ]; } || [ -z "${SUPERBRU_PASSWORD:-}" ] || [ -z "${SUPERBRU_POOL_URL:-}" ] || [ -z "${THE_ODDS_API_KEY:-}" ]; then
    write_watchdog_status "missing_credentials" "VPS watchdog is idle until SuperBru and odds secrets are present in .env" 1
    echo "$(date -u +%FT%TZ) SuperBru watchdog idle: missing one or more required environment variables." >> "$LOG_FILE"
    sleep "$INTERVAL_SECONDS"
    continue
  fi

  echo "$(date -u +%FT%TZ) SuperBru watchdog run starting." >> "$LOG_FILE"
  timeout "$TIMEOUT_SECONDS" python scripts/auto_pick_match_scoped_smart_odds.py \
    --login-url "$LOGIN_URL" \
    --pool-url "$POOL_URL" \
    --page-timezone "$PAGE_TIMEZONE" \
    --config "$CONFIG_PATH" \
    --window-minutes "$WINDOW_MINUTES" \
    --pick-card-csv "$PICK_CARD_CSV" \
    --late-card-grace-minutes 0 \
    --odds-lookup-window-minutes 90 \
    --leader-player "$LEADER_PLAYER" \
    --leaderboard-pool-keywords "$POOL_KEYWORDS" \
    --leader-safe-buffer 5.0 \
    --chaser-range 8.0 \
    --headless \
    --require-submission \
    --out-dir "$OUT_DIR" >> "$LOG_FILE" 2>&1
  code="$?"

  if [ "$code" -eq 0 ]; then
    write_watchdog_status "ok" "Last auto-pick cycle completed" 0
  else
    write_watchdog_status "error" "Last auto-pick cycle exited non-zero; inspect watchdog.log and latest_auto_pick_match_scoped.json" "$code"
  fi
  echo "$(date -u +%FT%TZ) SuperBru watchdog run finished with exit code $code." >> "$LOG_FILE"
  sleep "$INTERVAL_SECONDS"
done
