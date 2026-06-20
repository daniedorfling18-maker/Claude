from __future__ import annotations

import argparse
from pathlib import Path

from superbru_score_engine.game_theory.defensive import run_defensive_model


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run defensive pool-leader overlay on Superbru predictions")
    parser.add_argument("--predictions-csv", default="outputs/latest/predictions.csv")
    parser.add_argument("--out-dir", default="outputs/defensive")
    parser.add_argument("--leader-gap", type=float, default=0.0, help="Current points lead over nearest chaser")
    parser.add_argument("--chasers", type=int, default=5, help="Number of realistic chasers to defend against")
    parser.add_argument(
        "--exclude-match",
        action="append",
        default=[],
        help='Exclude a completed match. Example: --exclude-match "Turkey|Paraguay"',
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    output = run_defensive_model(
        predictions_csv=args.predictions_csv,
        out_dir=args.out_dir,
        leader_gap=args.leader_gap,
        chasers=args.chasers,
        exclude_matches=args.exclude_match,
    )

    if len(output) == 0:
        print("No matches found after filters.")
        return 0

    columns = [
        "commence_time",
        "home_team",
        "away_team",
        "model_recommended_scoreline",
        "leader_defensive_scoreline",
        "leader_expected_points",
        "ev_cost_vs_recommended",
        "crowd_pick_estimate",
        "exact_block_value",
        "outcome_block_value",
        "p_any_chaser_pressure",
        "confidence_tier",
        "risk_tier",
    ]

    print()
    print(output[columns].to_string(index=False))
    print()
    print(f"Wrote {Path(args.out_dir) / 'defensive_picks.csv'}")
    print(f"Wrote {Path(args.out_dir) / 'defensive_picks.json'}")
    print(f"Wrote {Path(args.out_dir) / 'defensive_summary.json'}")
    print(f"Total leader expected points: {output['leader_expected_points'].sum():.3f}")
    print(f"Total EV cost vs raw recommendation: {output['ev_cost_vs_recommended'].sum():.3f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
