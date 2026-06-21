"""
run_oddspedia_pipeline.py

Orchestrates the full Oddspedia correct-score pipeline in order:

  1. scrape_oddspedia_curl.py                — fetch grids + BTTS/OU from Oddspedia
  2. build_oddspedia_score_shape_features.py — shape features + market diffs
  3. check_oddspedia_grid_quality.py         — diagnostic validation
  4. compare_locked_picks_to_oddspedia.py    — flag picks worth reviewing
  5. build_oddspedia_superbru_ev.py          — EV-ranked candidate scorelines
  6. build_superbru_backtest_from_results.py — score picks against completed results
  7. build_oddspedia_model_independence.py   — grid vs market independence check
  8. build_oddspedia_synthetic_pool_crowding.py — synthetic pool crowding estimate
  9. build_superbru_pool_intelligence.py     — leaderboard leverage + chaser exposure
 10. build_oddspedia_signal_archive.py       — snapshot signals for future backtesting

Usage:
    python scripts/run_oddspedia_pipeline.py
    python scripts/run_oddspedia_pipeline.py --max-matches 3
    python scripts/run_oddspedia_pipeline.py --max-workers 8
    python scripts/run_oddspedia_pipeline.py --skip-scrape
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run the full Oddspedia correct-score pipeline.")
    p.add_argument("--urls-csv", default="inputs/oddspedia_match_urls.csv")
    p.add_argument("--locked-picks-csv", default="outputs/final_locked_picks/superbru_final_card.csv")
    p.add_argument("--max-workers", type=int, default=4)
    p.add_argument("--max-matches", type=int, default=0, help="0 = all (for testing)")
    p.add_argument("--impersonate", default="chrome124")
    p.add_argument("--skip-scrape", action="store_true", help="Re-use existing grid CSVs, skip step 1")
    return p


def run(label: str, cmd: list[str]) -> float:
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print("$ " + " ".join(cmd), flush=True)
    t0 = time.time()
    subprocess.run(cmd, cwd=ROOT, check=True)
    elapsed = round(time.time() - t0, 1)
    print(f"  → done in {elapsed}s")
    return elapsed


def main() -> int:
    args = build_parser().parse_args()
    t_start = time.time()
    timings: dict[str, float] = {}

    py = sys.executable

    if not args.skip_scrape:
        scrape_cmd = [
            py, "scripts/scrape_oddspedia_curl.py",
            "--urls-csv", args.urls_csv,
            "--max-workers", str(args.max_workers),
            "--impersonate", args.impersonate,
        ]
        if args.max_matches:
            scrape_cmd += ["--max-matches", str(args.max_matches)]
        timings["scrape"] = run("STEP 1 — Scrape Oddspedia grids + BTTS/OU", scrape_cmd)
    else:
        print("\nSkipping step 1 (--skip-scrape).")

    timings["shape"] = run(
        "STEP 2 — Build score-shape features",
        [py, "scripts/build_oddspedia_score_shape_features.py"],
    )

    timings["quality"] = run(
        "STEP 3 — Check grid quality",
        [py, "scripts/check_oddspedia_grid_quality.py",
         "--locked-picks-csv", args.locked_picks_csv],
    )

    timings["compare"] = run(
        "STEP 4 — Compare locked picks to grid",
        [py, "scripts/compare_locked_picks_to_oddspedia.py",
         "--locked-picks-csv", args.locked_picks_csv],
    )

    timings["ev"] = run(
        "STEP 5 — Superbru EV recommendations",
        [py, "scripts/build_oddspedia_superbru_ev.py",
         "--locked-picks-csv", args.locked_picks_csv],
    )

    timings["backtest"] = run(
        "STEP 6 — Backtest picks against completed results",
        [py, "scripts/build_superbru_backtest_from_results.py",
         "--picks-csv", args.locked_picks_csv],
    )

    timings["independence"] = run(
        "STEP 7 — Model independence check (grid vs market)",
        [py, "scripts/build_oddspedia_model_independence.py"],
    )

    timings["crowding"] = run(
        "STEP 8 — Synthetic pool crowding estimate",
        [py, "scripts/build_oddspedia_synthetic_pool_crowding.py"],
    )

    timings["pool"] = run(
        "STEP 9 — Pool intelligence (leaderboard leverage)",
        [py, "scripts/build_superbru_pool_intelligence.py",
         "--locked-picks-csv", args.locked_picks_csv],
    )

    timings["archive"] = run(
        "STEP 10 — Signal archive (snapshot for future backtesting)",
        [py, "scripts/build_oddspedia_signal_archive.py"],
    )

    total = round(time.time() - t_start, 1)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_elapsed_seconds": total,
        "step_timings": timings,
        "outputs": {
            "grids": "inputs/smartbet_grids/oddspedia_probability_grids_auto.csv",
            "markets_summary": "inputs/smartbet_grids/oddspedia_markets_summary_auto.csv",
            "score_shape_features": "inputs/smartbet_grids/oddspedia_score_shape_features.csv",
            "grid_quality": "outputs/oddspedia_probability_extract/oddspedia_grid_quality.csv",
            "pick_comparison": "outputs/oddspedia_pick_validation/oddspedia_pick_comparison.csv",
            "ev_by_score": "outputs/oddspedia_pick_validation/oddspedia_superbru_ev_by_score.csv",
            "ev_recommendations": "outputs/oddspedia_pick_validation/oddspedia_ev_recommendations.csv",
            "backtest": "outputs/backtesting/superbru_pick_backtest.csv",
            "backtest_summary": "outputs/backtesting/backtest_summary.json",
            "model_independence": "outputs/oddspedia_pick_validation/oddspedia_model_independence.csv",
            "synthetic_crowding": "outputs/superbru_pool/superbru_synthetic_crowding.csv",
            "pool_leverage": "outputs/superbru_pool/superbru_remaining_fixture_leverage.csv",
            "signal_archive_rolling": "outputs/backtesting/signal_archive_rolling.csv",
        },
    }
    print(f"\n{'='*60}")
    print(f"  PIPELINE COMPLETE  ({total}s total)")
    print(f"{'='*60}")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
