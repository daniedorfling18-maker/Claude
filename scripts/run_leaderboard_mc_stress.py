from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import numpy as np
import pandas as pd

from superbru_score_engine.config import load_config
from superbru_score_engine.ingest.offline import OfflineJsonProvider
from superbru_score_engine.model import OddsToScorelineModel
from superbru_score_engine.model.ratings import RatingsStore


WEIGHT_COLS = [
    "raw_weight",
    "top1_weight",
    "top2_weight",
    "top3_weight",
    "modal_weight",
    "chase_weight",
    "chalk_weight",
]


STRESS_SCENARIOS: list[dict[str, Any]] = [
    {"scenario": "base", "description": "Current Monte Carlo assumptions."},
    {
        "scenario": "chasers_conservative",
        "description": "Chasers lean more towards raw, top1 and chalk picks.",
        "profile_mult": {"raw_weight": 1.25, "top1_weight": 1.15, "chalk_weight": 1.25, "chase_weight": 0.70, "top3_weight": 0.80},
    },
    {
        "scenario": "chasers_aggressive",
        "description": "Chasers chase exacts and differentials more aggressively.",
        "profile_mult": {"raw_weight": 0.80, "top1_weight": 0.90, "top2_weight": 1.20, "top3_weight": 1.35, "modal_weight": 0.85, "chase_weight": 1.50, "chalk_weight": 0.80},
    },
    {
        "scenario": "favourite_heavy",
        "description": "Chasers overweight favourite/chalk scorelines.",
        "profile_mult": {"raw_weight": 1.20, "top1_weight": 1.20, "chalk_weight": 1.60, "chase_weight": 0.80},
    },
    {
        "scenario": "exact_chase_heavy",
        "description": "Chasers overweight modal and top exact scorelines.",
        "profile_mult": {"modal_weight": 1.50, "top1_weight": 1.25, "top2_weight": 1.10, "raw_weight": 0.90, "chalk_weight": 0.85},
    },
    {"scenario": "goals_plus_5pct", "description": "Model total-goals expectation tilted 5 percent higher.", "matrix_total_factor": 1.05},
    {"scenario": "goals_minus_5pct", "description": "Model total-goals expectation tilted 5 percent lower.", "matrix_total_factor": 0.95},
    {"scenario": "rho_plus_0_02", "description": "Dixon-Coles rho increased by 0.02.", "rho_delta": 0.02},
    {"scenario": "rho_minus_0_02", "description": "Dixon-Coles rho decreased by 0.02.", "rho_delta": -0.02},
    {"scenario": "draw_heavy", "description": "Draw scorelines receive 8 percent more mass.", "draw_multiplier": 1.08},
    {"scenario": "draw_light", "description": "Draw scorelines receive 8 percent less mass.", "draw_multiplier": 0.92},
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run robustness stress tests around the leaderboard Monte Carlo defensive layer.")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--fixtures", default="data/fixtures_real.csv")
    parser.add_argument("--odds-json", default="data/odds_snapshot_real.json")
    parser.add_argument("--predictions-csv", default="outputs/latest/predictions.csv")
    parser.add_argument("--leaderboard-csv", default="inputs/pool_leaderboard.csv")
    parser.add_argument("--chaser-profiles-csv", default="inputs/chaser_profiles.csv")
    parser.add_argument("--leader-player", default="Danie")
    parser.add_argument("--chasers", default="Dheben,Bijal,Peter,rob87,Leonard,Kea")
    parser.add_argument("--simulations", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=20260620)
    parser.add_argument("--exclude-match", action="append", default=[])
    parser.add_argument("--support-threshold", type=float, default=0.70)
    parser.add_argument("--max-ev-loss-per-switch", type=float, default=0.025)
    parser.add_argument("--max-total-ev-loss", type=float, default=0.10)
    parser.add_argument("--min-lead-prob-gain", type=float, default=0.005)
    parser.add_argument("--high-risk-min-lead-prob-gain", type=float, default=0.015)
    parser.add_argument("--different-outcome-min-gain", type=float, default=0.020)
    parser.add_argument("--min-z", type=float, default=2.0)
    parser.add_argument("--out-dir", default="outputs/leaderboard_mc_stress")
    return parser


def load_mc_module():
    path = ROOT / "scripts" / "run_leaderboard_monte_carlo.py"
    if not path.exists():
        raise FileNotFoundError("scripts/run_leaderboard_monte_carlo.py not found. Pull the Monte Carlo script first.")

    spec = importlib.util.spec_from_file_location("leaderboard_mc", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import {path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def scenario_config(base_config, scenario: dict[str, Any]):
    rho_delta = float(scenario.get("rho_delta", 0.0) or 0.0)
    if rho_delta == 0.0:
        return base_config
    return replace(
        base_config,
        model=replace(base_config.model, dixon_coles_rho=float(base_config.model.dixon_coles_rho) + rho_delta),
    )


def apply_profile_stress(profiles: pd.DataFrame, scenario: dict[str, Any]) -> pd.DataFrame:
    stressed = profiles.copy()
    multipliers = scenario.get("profile_mult", {}) or {}
    for col in WEIGHT_COLS:
        if col not in stressed.columns:
            continue
        stressed[col] = pd.to_numeric(stressed[col], errors="coerce").fillna(0.0)
        stressed[col] = stressed[col] * float(multipliers.get(col, 1.0))
    return stressed


def matrix_total_mean(matrix: np.ndarray) -> float:
    home_goals = np.arange(matrix.shape[0], dtype=float)[:, None]
    away_goals = np.arange(matrix.shape[1], dtype=float)[None, :]
    return float(((home_goals + away_goals) * matrix).sum())


def tilt_total_goals(matrix: np.ndarray, factor: float) -> np.ndarray:
    if factor == 1.0:
        return matrix

    matrix = np.asarray(matrix, dtype=float)
    base_mean = matrix_total_mean(matrix)
    target = max(0.0, base_mean * float(factor))
    total_goals = np.arange(matrix.shape[0], dtype=float)[:, None] + np.arange(matrix.shape[1], dtype=float)[None, :]

    lo, hi = -3.0, 3.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        tilted = matrix * np.exp(mid * total_goals)
        tilted = tilted / tilted.sum()
        mean = matrix_total_mean(tilted)
        if mean < target:
            lo = mid
        else:
            hi = mid

    gamma = (lo + hi) / 2.0
    tilted = matrix * np.exp(gamma * total_goals)
    return tilted / tilted.sum()


def tilt_draw_mass(matrix: np.ndarray, multiplier: float) -> np.ndarray:
    if multiplier == 1.0:
        return matrix
    stressed = np.array(matrix, dtype=float, copy=True)
    limit = min(stressed.shape[0], stressed.shape[1])
    for idx in range(limit):
        stressed[idx, idx] *= float(multiplier)
    return stressed / stressed.sum()


def apply_matrix_stress(matrix: np.ndarray, scenario: dict[str, Any]) -> np.ndarray:
    stressed = np.asarray(matrix, dtype=float)
    if "matrix_total_factor" in scenario:
        stressed = tilt_total_goals(stressed, float(scenario["matrix_total_factor"]))
    if "draw_multiplier" in scenario:
        stressed = tilt_draw_mass(stressed, float(scenario["draw_multiplier"]))
    return stressed / stressed.sum()


def build_score_matrices_with_config(mc, args: argparse.Namespace, predictions: pd.DataFrame, config):
    ratings = RatingsStore(config.paths.ratings_store, config.ratings)
    matches = OfflineJsonProvider(args.odds_json).fetch_odds()
    matches = mc.merge_fixture_metadata(matches, args.fixtures)

    wanted = {mc.row_key(row) for _, row in predictions.iterrows()}
    wanted_pairs = {(key[1], key[2]) for key in wanted}
    model = OddsToScorelineModel(config.model, ratings)
    matrices = {}

    for match in matches:
        key = mc.match_key(match)
        pair = (key[1], key[2])
        if key not in wanted and pair not in wanted_pairs:
            continue
        distribution = model.build_distribution(match)
        matrices[key] = np.asarray(distribution.matrix, dtype=float)
    return matrices


def switch_label(row: dict[str, Any]) -> str:
    return f"{row['home_team']} vs {row['away_team']}: {row['raw_pick']} -> {row['candidate_pick']}"


def run_scenario(mc, args: argparse.Namespace, scenario: dict[str, Any], scenario_idx: int):
    predictions, leaderboard, profiles = mc.load_inputs(args)
    rows = [row for _, row in predictions.iterrows() if not mc.exclude_row(row, args.exclude_match)]
    if not rows:
        raise ValueError("No prediction rows remain after exclusions.")

    base_config = load_config(args.config)
    config = scenario_config(base_config, scenario)
    ci_cutoff = float(config.superbru.ci_cutoff)

    matrices_by_key = build_score_matrices_with_config(mc, args, predictions, config)
    rng = np.random.default_rng(int(args.seed) + scenario_idx * 10_009)

    matrices = {}
    actuals = {}
    for idx, row in enumerate(rows):
        matrix = mc.find_matrix(row, matrices_by_key)
        matrix = apply_matrix_stress(matrix, scenario)
        matrices[idx] = matrix
        actuals[idx] = mc.sample_actual_scores(matrix, int(args.simulations), rng)

    stressed_profiles = apply_profile_stress(profiles, scenario)
    points_lookup = {mc.txt(row.get("player")): mc.fnum(row.get("current_points")) for _, row in leaderboard.iterrows()}
    if args.leader_player not in points_lookup:
        raise ValueError(f"Leader player {args.leader_player!r} not found in leaderboard CSV.")

    chaser_names = [name.strip() for name in args.chasers.split(",") if name.strip()]
    chaser_totals = mc.build_chaser_totals(
        rows=rows,
        actuals=actuals,
        profiles=stressed_profiles,
        leaderboard=leaderboard,
        chaser_names=chaser_names,
        simulations=int(args.simulations),
        ci_cutoff=ci_cutoff,
        rng=rng,
    )

    picks, candidates, summary = mc.optimise(
        rows=rows,
        matrices=matrices,
        actuals=actuals,
        chaser_totals=chaser_totals,
        leader_start=points_lookup[args.leader_player],
        ci_cutoff=ci_cutoff,
        args=args,
    )

    scenario_name = scenario["scenario"]
    description = scenario.get("description", "")
    picks.insert(0, "scenario", scenario_name)
    picks.insert(1, "scenario_description", description)
    candidates.insert(0, "scenario", scenario_name)
    candidates.insert(1, "scenario_description", description)
    summary["scenario"] = scenario_name
    summary["scenario_description"] = description
    summary["accepted_switch_labels"] = [switch_label(item) for item in summary["accepted_switches"]]
    return picks, candidates, summary


def build_switch_support(candidate_tests: pd.DataFrame, scenario_count: int, support_threshold: float) -> pd.DataFrame:
    if candidate_tests.empty:
        return pd.DataFrame()
    tested = candidate_tests[candidate_tests["candidate_pick"] != candidate_tests["raw_pick"]].copy()
    if tested.empty:
        return pd.DataFrame()

    tested["passes_hard_rule_int"] = tested["passes_hard_rule"].astype(bool).astype(int)
    tested["positive_delta_int"] = (pd.to_numeric(tested["delta_p_finish_first"], errors="coerce") > 0).astype(int)
    tested["delta_numeric"] = pd.to_numeric(tested["delta_p_finish_first"], errors="coerce")
    tested["ev_loss_numeric"] = pd.to_numeric(tested["ev_loss"], errors="coerce")
    tested["z_numeric"] = pd.to_numeric(tested["z_score"], errors="coerce")

    group_cols = ["home_team", "away_team", "raw_pick", "candidate_pick"]
    support = (
        tested.groupby(group_cols)
        .agg(
            scenarios_tested=("scenario", "nunique"),
            scenarios_passed=("passes_hard_rule_int", "sum"),
            scenarios_positive_delta=("positive_delta_int", "sum"),
            mean_delta_p_finish_first=("delta_numeric", "mean"),
            min_delta_p_finish_first=("delta_numeric", "min"),
            max_delta_p_finish_first=("delta_numeric", "max"),
            mean_ev_loss=("ev_loss_numeric", "mean"),
            max_ev_loss=("ev_loss_numeric", "max"),
            mean_z_score=("z_numeric", "mean"),
        )
        .reset_index()
    )
    support["pass_rate"] = support["scenarios_passed"] / float(scenario_count)
    support["positive_delta_rate"] = support["scenarios_positive_delta"] / float(scenario_count)
    support["robust_switch"] = (
        (support["pass_rate"] >= float(support_threshold))
        & (support["positive_delta_rate"] >= float(support_threshold))
        & (support["mean_delta_p_finish_first"] > 0)
    )
    return support.sort_values(["robust_switch", "pass_rate", "mean_delta_p_finish_first"], ascending=[False, False, False])


def main() -> int:
    args = build_parser().parse_args()
    mc = load_mc_module()

    all_picks = []
    all_candidates = []
    summaries = []
    for idx, scenario in enumerate(STRESS_SCENARIOS):
        print(f"Running stress scenario {idx + 1}/{len(STRESS_SCENARIOS)}: {scenario['scenario']}")
        picks, candidates, summary = run_scenario(mc, args, scenario, idx)
        all_picks.append(picks)
        all_candidates.append(candidates)
        summaries.append(summary)

    picks_df = pd.concat(all_picks, ignore_index=True)
    candidates_df = pd.concat(all_candidates, ignore_index=True)
    scenario_summary_df = pd.DataFrame(
        [
            {
                "scenario": item["scenario"],
                "description": item["scenario_description"],
                "baseline_p_finish_first": item["baseline_p_finish_first"],
                "final_p_finish_first": item["final_p_finish_first"],
                "delta_p_finish_first": item["delta_p_finish_first"],
                "baseline_p_finish_first_or_tied": item["baseline_p_finish_first_or_tied"],
                "final_p_finish_first_or_tied": item["final_p_finish_first_or_tied"],
                "total_ev_loss": item["total_ev_loss"],
                "accepted_switch_count": len(item["accepted_switches"]),
                "accepted_switch_labels": " | ".join(item["accepted_switch_labels"]),
            }
            for item in summaries
        ]
    )
    support_df = build_switch_support(candidates_df, len(STRESS_SCENARIOS), float(args.support_threshold))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    picks_path = out_dir / "stress_picks_by_scenario.csv"
    candidates_path = out_dir / "stress_candidate_tests.csv"
    scenarios_path = out_dir / "stress_scenario_summary.csv"
    support_path = out_dir / "stress_switch_support.csv"
    summary_path = out_dir / "stress_summary.json"

    picks_df.to_csv(picks_path, index=False)
    candidates_df.to_csv(candidates_path, index=False)
    scenario_summary_df.to_csv(scenarios_path, index=False)
    support_df.to_csv(support_path, index=False)

    robust_switches = support_df[support_df["robust_switch"] == True].to_dict(orient="records") if not support_df.empty else []
    payload = {
        "simulations_per_scenario": int(args.simulations),
        "scenario_count": len(STRESS_SCENARIOS),
        "support_threshold": float(args.support_threshold),
        "robust_switches": robust_switches,
        "scenario_summaries": summaries,
    }
    summary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("\nScenario summary")
    print(scenario_summary_df.to_string(index=False))
    print("\nSwitch robustness support")
    if support_df.empty:
        print("No non-raw candidate switches were tested.")
    else:
        display_cols = [
            "home_team",
            "away_team",
            "raw_pick",
            "candidate_pick",
            "pass_rate",
            "positive_delta_rate",
            "mean_delta_p_finish_first",
            "mean_ev_loss",
            "robust_switch",
        ]
        print(support_df[display_cols].to_string(index=False))
    print()
    print(f"Wrote {picks_path}")
    print(f"Wrote {candidates_path}")
    print(f"Wrote {scenarios_path}")
    print(f"Wrote {support_path}")
    print(f"Wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
