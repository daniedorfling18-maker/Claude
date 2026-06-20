from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the daily Superbru robust odds-update pipeline.")
    parser.add_argument("--sport", default="soccer_fifa_world_cup")
    parser.add_argument("--regions", default="uk,eu,us,au")
    parser.add_argument("--markets", default="h2h,spreads,totals")
    parser.add_argument("--out-root", default="outputs")
    parser.add_argument("--snapshot-id", default="", help="Optional snapshot id. Defaults to current UTC timestamp.")
    parser.add_argument("--require-two-day-support", action="store_true", default=True)
    parser.add_argument("--allow-first-snapshot", action="store_true", default=True)
    parser.add_argument("--skip-final-simulation", action="store_true")
    return parser


def run(cmd: list[str], env: dict[str, str] | None = None) -> None:
    print("\n$ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT, env=env, check=True)


def require_file(path: str | Path, label: str) -> None:
    p = ROOT / Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"Missing {label}: {p}. Commit/generate this input before running the scheduled pipeline."
        )


def main() -> int:
    args = build_parser().parse_args()
    snapshot_id = args.snapshot_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")

    if not env.get("THE_ODDS_API_KEY"):
        raise EnvironmentError("THE_ODDS_API_KEY is not set. Add it as a GitHub Actions repository secret.")

    # Static/generated inputs that must exist before an unattended run.
    require_file("outputs/final_leader_decision_round_summary_profiles/final_picks.csv", "base final picks")
    require_file("outputs/pick_validation_report/pick_validation_report.csv", "pick validation report")
    require_file("outputs/pick_validation_report/review_candidate_alternatives.csv", "review candidate alternatives")
    require_file("outputs/latest/predictions.csv", "base predictions")

    run([sys.executable, "scripts/verify_validation_stack.py"], env=env)

    run(
        [
            sys.executable,
            "scripts/fetch_market_odds_theoddsapi.py",
            "--sport",
            args.sport,
            "--regions",
            args.regions,
            "--markets",
            args.markets,
            "--out-json",
            "outputs/market_odds/worldcup_market_odds_raw.json",
            "--out-csv",
            "outputs/market_odds/worldcup_market_odds_flat.csv",
        ],
        env=env,
    )

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
        "inputs/manual_match_flags.csv",
        "--out-dir",
        "outputs/daily_robust_card",
    ]
    if args.require_two_day_support:
        robust_cmd.append("--require-two-day-support")
    if args.allow_first_snapshot:
        robust_cmd.append("--allow-first-snapshot")
    run(robust_cmd, env=env)

    run(
        [
            sys.executable,
            "scripts/build_predictions_from_locked_card.py",
            "--base-predictions-csv",
            "outputs/latest/predictions.csv",
            "--locked-card-csv",
            "outputs/daily_robust_card/daily_robust_superbru_card.csv",
            "--out-csv",
            "outputs/daily_robust_card/predictions_for_final_simulation.csv",
        ],
        env=env,
    )

    if not args.skip_final_simulation:
        run(
            [
                sys.executable,
                "scripts/run_final_leader_decision.py",
                "--predictions-csv",
                "outputs/daily_robust_card/predictions_for_final_simulation.csv",
                "--out-dir",
                "outputs/final_leader_decision_daily_robust",
                "--reuse-existing",
            ],
            env=env,
        )

    summary: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "snapshot_id": snapshot_id,
        "final_card": "outputs/daily_robust_card/daily_robust_superbru_card.csv",
        "daily_summary": "outputs/daily_robust_card/daily_robust_summary.json",
        "market_history_summary": "outputs/market_odds_history/market_odds_history_summary.json",
        "final_simulation_dir": "outputs/final_leader_decision_daily_robust",
    }
    out_path = ROOT / "outputs/daily_robust_card/daily_pipeline_summary.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
