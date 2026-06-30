from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the daily Superbru robust odds-update pipeline.")
    parser.add_argument("--sport", default="soccer_fifa_world_cup")
    parser.add_argument("--regions", default="eu")
    parser.add_argument("--markets", default="h2h,totals")
    parser.add_argument("--out-root", default="outputs")
    parser.add_argument("--snapshot-id", default="", help="Optional snapshot id. Defaults to current UTC timestamp.")
    parser.add_argument("--require-two-day-support", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--allow-first-snapshot", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--skip-final-simulation", action="store_true")
    parser.add_argument(
        "--skip-market-odds-fetch",
        action="store_true",
        help="Use the already-fetched market odds files and do not call The Odds API.",
    )
    parser.add_argument(
        "--match-odds-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fetch odds only for the immediate pre-match kickoff window. Defaults to true.",
    )
    parser.add_argument("--match-window-before-minutes", type=int, default=10)
    parser.add_argument("--match-window-after-minutes", type=int, default=30)
    parser.add_argument("--match-commence-from", default="")
    parser.add_argument("--match-commence-to", default="")
    parser.add_argument("--fixtures", default="data/fixtures_real.csv")
    parser.add_argument("--odds-json", default="outputs/market_odds/worldcup_market_odds_raw.json")
    parser.add_argument("--leaderboard-csv", default="outputs/superbru_pool/live_pool_leaderboard.csv")
    parser.add_argument("--chaser-profiles-csv", default="outputs/superbru_pool/live_chaser_profiles.csv")
    parser.add_argument("--chasers", default="")
    parser.add_argument("--leader-player", default=os.environ.get("SUPERBRU_PLAYER_NAME", "Danie"))
    parser.add_argument("--manual-flags-csv", default="outputs/superbru_pool/live_manual_match_flags.csv")
    parser.add_argument(
        "--run-fresh-final-simulation",
        action="store_true",
        help=(
            "Run the expensive fresh final-leader Monte Carlo simulation when cached outputs are incomplete. "
            "Scheduled daily runs leave this off so the odds card still completes inside the CI timeout."
        ),
    )
    return parser


def run(cmd: list[str], env: dict[str, str] | None = None, *, warn_only: bool = False) -> int:
    print("\n$ " + " ".join(cmd), flush=True)
    completed = subprocess.run(cmd, cwd=ROOT, env=env, check=False)
    if completed.returncode != 0:
        if warn_only:
            print(f"warning: command exited with {completed.returncode}; continuing")
            return completed.returncode
        raise subprocess.CalledProcessError(completed.returncode, cmd)
    return 0


def require_file(path: str | Path, label: str) -> None:
    p = ROOT / Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"Missing {label}: {p}. Generate this input before running the scheduled pipeline."
        )


def final_simulation_cache_complete(out_dir: str | Path = "outputs/final_leader_decision_daily_robust") -> bool:
    base = ROOT / Path(out_dir)
    required = [
        base / "base_mc" / "leader_mc_summary.json",
        base / "stress" / "stress_summary.json",
        base / "confirmation_500k" / "leader_mc_summary.json",
        base / "confirmation_500k" / "leader_mc_picks.csv",
    ]
    return all(path.exists() for path in required)


def market_odds_cache_available() -> bool:
    return all(
        (ROOT / path).exists()
        for path in [
            "outputs/market_odds/worldcup_market_odds_raw.json",
            "outputs/market_odds/worldcup_market_odds_flat.csv",
        ]
    )


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def resolve_match_odds_scope(args: argparse.Namespace) -> dict[str, object]:
    if not args.match_odds_only:
        return {"mode": "full_tournament_odds", "commence_from": "", "commence_to": ""}

    if args.match_commence_from and args.match_commence_to:
        return {
            "mode": "explicit_match_window",
            "commence_from": args.match_commence_from,
            "commence_to": args.match_commence_to,
        }

    now = datetime.now(timezone.utc)
    return {
        "mode": "rolling_pre_match_window",
        "commence_from": iso_z(now - timedelta(minutes=args.match_window_before_minutes)),
        "commence_to": iso_z(now + timedelta(minutes=args.match_window_after_minutes)),
        "window_before_minutes": args.match_window_before_minutes,
        "window_after_minutes": args.match_window_after_minutes,
    }


def main() -> int:
    args = build_parser().parse_args()
    snapshot_id = args.snapshot_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    final_simulation_skipped = bool(args.skip_final_simulation)
    final_simulation_failed = False
    final_simulation_status = "skipped_by_flag" if args.skip_final_simulation else "pending"
    final_simulation_dir: str | None = None
    final_simulation_cache_reused = False
    predictions_for_final_simulation: str | None = None
    market_odds_scope = resolve_match_odds_scope(args)

    if not args.skip_market_odds_fetch and not env.get("THE_ODDS_API_KEY"):
        raise EnvironmentError("THE_ODDS_API_KEY is not set. Add it as a GitHub Actions repository secret.")

    require_file("outputs/final_leader_decision_round_summary_profiles/final_picks.csv", "base final picks")

    run([sys.executable, "scripts/verify_validation_stack.py"], env=env)

    run(
        [
            sys.executable,
            "scripts/build_pick_validation_report.py",
            "--final-picks-csv",
            "outputs/final_leader_decision_round_summary_profiles/final_picks.csv",
            "--final-report-json",
            "outputs/final_leader_decision_round_summary_profiles/final_decision_report.json",
            "--out-dir",
            "outputs/pick_validation_report",
        ],
        env=env,
    )

    if args.skip_market_odds_fetch:
        if not market_odds_cache_available():
            raise FileNotFoundError(
                "--skip-market-odds-fetch was passed, but current-run market odds files are missing. "
                "Run the fresh odds fetch step first."
            )
        print("Skipping The Odds API fetch and using current-run market odds files.")
    else:
        fetch_cmd = [
            sys.executable,
            "scripts/fetch_market_odds_theoddsapi.py",
            "--sport", args.sport,
            "--regions", args.regions,
            "--markets", args.markets,
            "--out-json", "outputs/market_odds/worldcup_market_odds_raw.json",
            "--out-csv", "outputs/market_odds/worldcup_market_odds_flat.csv",
            "--allow-stale-on-failure",
            "--allow-empty-on-failure",
        ]
        if args.match_odds_only:
            fetch_cmd.extend(["--commence-from", str(market_odds_scope["commence_from"])])
            fetch_cmd.extend(["--commence-to", str(market_odds_scope["commence_to"])])
            print("Fetching market odds for pre-match window only:")
            print(json.dumps(market_odds_scope, indent=2))
        else:
            print("Fetching full tournament odds because --no-match-odds-only was supplied.")
        run(fetch_cmd, env=env)

    run(
        [
            sys.executable,
            "scripts/build_market_odds_validation.py",
            "--final-picks-csv",
            "outputs/final_leader_decision_round_summary_profiles/final_picks.csv",
            "--market-odds-csv",
            "outputs/market_odds/worldcup_market_odds_flat.csv",
            "--validation-report-csv",
            "outputs/pick_validation_report/pick_validation_report.csv",
            "--out-dir",
            "outputs/market_odds_validation",
        ],
        env=env,
    )

    run(
        [
            sys.executable,
            "scripts/update_market_odds_history.py",
            "--market-validation-csv",
            "outputs/market_odds_validation/market_odds_validation_report.csv",
            "--market-odds-flat-csv",
            "outputs/market_odds/worldcup_market_odds_flat.csv",
            "--out-dir",
            "outputs/market_odds_history",
            "--snapshot-id",
            snapshot_id,
        ],
        env=env,
    )

    run(
        [
            sys.executable,
            "scripts/run_component_validation_rescore.py",
            "--market-validation-csv",
            "outputs/market_odds_validation/market_odds_validation_report.csv",
            "--pick-validation-csv",
            "outputs/pick_validation_report/pick_validation_report.csv",
            "--candidate-alternatives-csv",
            "outputs/pick_validation_report/review_candidate_alternatives.csv",
            "--out-dir",
            "outputs/component_validation",
        ],
        env=env,
    )

    run(
        [
            sys.executable,
            "scripts/build_final_locked_picks.py",
            "--final-picks-csv",
            "outputs/final_leader_decision_round_summary_profiles/final_picks.csv",
            "--rescore-csv",
            "outputs/component_validation/review_rescore_report.csv",
            "--out-dir",
            "outputs/final_locked_picks",
        ],
        env=env,
    )

    robust_cmd = [
        sys.executable,
        "scripts/build_daily_robust_card.py",
        "--locked-picks-csv",
        "outputs/final_locked_picks/final_picks_locked.csv",
        "--rescore-csv",
        "outputs/component_validation/review_rescore_report.csv",
        "--movement-csv",
        "outputs/market_odds_history/market_odds_movement_report.csv",
        "--manual-flags-csv",
        args.manual_flags_csv,
        "--out-dir",
        "outputs/daily_robust_card",
    ]
    if args.require_two_day_support:
        robust_cmd.append("--require-two-day-support")
    if args.allow_first_snapshot:
        robust_cmd.append("--allow-first-snapshot")
    run(robust_cmd, env=env)

    if not args.skip_final_simulation:
        predictions_for_final_simulation = "outputs/daily_robust_card/predictions_for_final_simulation.csv"
        run(
            [
                sys.executable,
                "scripts/build_predictions_from_locked_card.py",
                "--base-predictions-csv",
                "outputs/latest/predictions.csv",
                "--locked-card-csv",
                "outputs/daily_robust_card/daily_robust_locked_picks.csv",
                "--out-csv",
                predictions_for_final_simulation,
                "--allow-card-only-fallback",
            ],
            env=env,
        )

        final_decision_cmd = [
            sys.executable,
            "scripts/run_final_leader_decision.py",
            "--fixtures",
            args.fixtures,
            "--odds-json",
            args.odds_json,
            "--predictions-csv",
            predictions_for_final_simulation,
            "--leaderboard-csv",
            args.leaderboard_csv,
            "--chaser-profiles-csv",
            args.chaser_profiles_csv,
            "--leader-player",
            args.leader_player,
            "--out-dir",
            "outputs/final_leader_decision_daily_robust",
        ]
        if args.chasers:
            final_decision_cmd.extend(["--chasers", args.chasers])
        if final_simulation_cache_complete():
            final_decision_cmd.append("--reuse-existing")
            final_simulation_cache_reused = True
            print("Reusing cached final leader simulation outputs.")
            rc = run(final_decision_cmd, env=env, warn_only=True)
            final_simulation_failed = rc != 0
            final_simulation_skipped = False
            final_simulation_dir = "outputs/final_leader_decision_daily_robust"
            final_simulation_status = "failed_non_blocking" if final_simulation_failed else "completed"
        elif args.run_fresh_final_simulation:
            print("Cached final leader simulation outputs are incomplete; running fresh final leader simulation.")
            rc = run(final_decision_cmd, env=env, warn_only=True)
            final_simulation_failed = rc != 0
            final_simulation_skipped = False
            final_simulation_dir = "outputs/final_leader_decision_daily_robust"
            final_simulation_status = "failed_non_blocking" if final_simulation_failed else "completed"
        else:
            print(
                "Cached final leader simulation outputs are incomplete; skipping expensive fresh final simulation. "
                "Run with --run-fresh-final-simulation when you want to refresh Monte Carlo outputs."
            )
            final_simulation_skipped = True
            final_simulation_status = "skipped_missing_cache"

    summary: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "snapshot_id": snapshot_id,
        "final_card": "outputs/daily_robust_card/daily_robust_superbru_card.csv",
        "daily_summary": "outputs/daily_robust_card/daily_robust_summary.json",
        "market_history_summary": "outputs/market_odds_history/market_odds_history_summary.json",
        "market_odds_fetch_skipped": bool(args.skip_market_odds_fetch),
        "market_odds_scope": market_odds_scope,
        "fixtures": args.fixtures,
        "odds_json": args.odds_json,
        "leaderboard_csv": args.leaderboard_csv,
        "chaser_profiles_csv": args.chaser_profiles_csv,
        "chasers": args.chasers,
        "manual_flags_csv": args.manual_flags_csv,
        "final_simulation_dir": final_simulation_dir,
        "predictions_for_final_simulation": predictions_for_final_simulation,
        "final_simulation_status": final_simulation_status,
        "final_simulation_skipped": final_simulation_skipped,
        "final_simulation_failed_non_blocking": final_simulation_failed,
        "final_simulation_cache_reused": final_simulation_cache_reused,
    }
    out_path = ROOT / "outputs/daily_robust_card/daily_pipeline_summary.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
