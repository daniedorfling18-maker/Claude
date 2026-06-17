#!/usr/bin/env python3
"""Selection-policy audit: is the PICK RULE (not the model) leaving Superbru points behind?

The EV-argmax pick is optimal ONLY if the scoreline distribution is calibrated. This
rebuilds each match's distribution from the backtest lambdas (no re-solving) and scores
several pick policies against the actual result, then paired-bootstraps each policy's
points gap vs raw EV. If a different policy beats raw EV the distribution is miscalibrated
for picking; if raw EV wins, the pick rule is already optimal and the leverage is elsewhere.

Superbru scoring (faithful): 3 exact; else if SAME OUTCOME -> 1.5 if closeness_index
(|d goal-diff| + |d total|/2) <= cutoff else 1.0; else 0. Note close/outcome points are
gated on getting the result side right.

Also reports the model's expected-vs-realised points gap (aggregate EV calibration) and
per-scoreline predicted-vs-observed frequency.
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from superbru_score_engine.backtest.inference import paired_bootstrap_delta
from superbru_score_engine.config import load_config
from superbru_score_engine.decision.superbru import score_actual_prediction
from superbru_score_engine.model.dixon_coles import apply_dixon_coles
from superbru_score_engine.model.poisson import independent_poisson_matrix

COMMON = ["0-0", "1-0", "0-1", "1-1", "2-0", "0-2", "2-1", "1-2", "2-2", "3-1", "1-3"]


def _points_cells(cand_grid: int, model_grid: int, ci: float) -> dict[tuple[int, int], np.ndarray]:
    """For each candidate pick, the Superbru points it scores against every actual cell.

    Depends only on the grid + cutoff (not the match), so precompute once and reuse.
    """
    rows = model_grid + 1
    actual_h = np.arange(rows)[:, None]
    actual_a = np.arange(rows)[None, :]
    actual_gd = actual_h - actual_a
    actual_tot = actual_h + actual_a
    actual_sign = np.sign(actual_gd)
    cells: dict[tuple[int, int], np.ndarray] = {}
    for ph in range(cand_grid + 1):
        for pa in range(cand_grid + 1):
            exact = (actual_h == ph) & (actual_a == pa)
            same_outcome = actual_sign == np.sign(ph - pa)
            close_idx = np.abs((ph - pa) - actual_gd) + np.abs((ph + pa) - actual_tot) / 2.0
            close = same_outcome & (~exact) & (close_idx <= ci)
            outcome_only = same_outcome & (~exact) & (~close)
            cells[(ph, pa)] = 3.0 * exact + 1.5 * close + 1.0 * outcome_only
    return cells


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-csv", default="outputs/bt-multiline/football_data_league_backtest_results.csv")
    ap.add_argument("--market-mode", default="h2h_totals")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    cfg = load_config(args.config)
    grid, cand_g, rho, ci = (
        cfg.model.model_grid_goals,
        cfg.model.candidate_grid_goals,
        cfg.model.dixon_coles_rho,
        cfg.superbru.ci_cutoff,
    )
    df = pd.read_csv(args.results_csv)
    if args.market_mode and "market_mode" in df.columns:
        df = df[df["market_mode"] == args.market_mode]
    df = df.reset_index(drop=True)

    cells = _points_cells(cand_g, grid, ci)
    candidates = list(cells)
    ge15_masks = {c: (cells[c] >= 1.5) for c in candidates}

    policies = ["raw_ev", "max_p_close", "modal_exact", "max_p_outcome", "heuristic_canonical"]
    pts: dict[str, list[float]] = {p: [] for p in policies}
    pred_ev_sum = realized_sum = 0.0
    pred_score = {s: 0.0 for s in COMMON}
    obs_score = {s: 0 for s in COMMON}

    for row in df.itertuples():
        lh, la, ah, aa = float(row.lambda_home), float(row.lambda_away), int(row.home_goals), int(row.away_goals)
        matrix = apply_dixon_coles(independent_poisson_matrix(lh, la, grid), lh, la, rho)

        evs = {c: float((cells[c] * matrix).sum()) for c in candidates}
        p_ge15 = {c: float((matrix * ge15_masks[c]).sum()) for c in candidates}
        p_exact = {c: float(matrix[c[0], c[1]]) for c in candidates}
        # P(right outcome) for a pick = mass on cells sharing its result sign.
        home = float(np.tril(matrix, -1).sum())
        draw = float(np.trace(matrix))
        away = float(np.triu(matrix, 1).sum())
        p_outcome_of = {1: home, 0: draw, -1: away}

        raw_ev_pick = max(candidates, key=lambda c: evs[c])
        picks = {
            "raw_ev": raw_ev_pick,
            "max_p_close": max(candidates, key=lambda c: p_ge15[c]),
            "modal_exact": max(candidates, key=lambda c: p_exact[c]),
            "max_p_outcome": max(candidates, key=lambda c: p_outcome_of[int(np.sign(c[0] - c[1]))]),
            "heuristic_canonical": {"home": (2, 1), "draw": (1, 1), "away": (1, 2)}[
                max((("home", home), ("draw", draw), ("away", away)), key=lambda kv: kv[1])[0]
            ],
        }
        for name, (ph, pa) in picks.items():
            pts[name].append(score_actual_prediction(ph, pa, ah, aa, ci))

        pred_ev_sum += evs[raw_ev_pick]
        realized_sum += pts["raw_ev"][-1]
        for s in COMMON:
            hh, aaa = (int(x) for x in s.split("-"))
            if hh <= grid and aaa <= grid:
                pred_score[s] += float(matrix[hh, aaa])
            obs_score[s] += 1 if (ah == hh and aa == aaa) else 0

    n = len(df)
    base = np.array(pts["raw_ev"])
    print(f"Selection-policy audit | {n} matches ({args.market_mode}) | rho={rho} ci={ci} cand_grid={cand_g}")
    if "model_points" in df.columns:
        print(f"sanity: recomputed raw_ev mean {base.mean():.4f} vs recorded model_points {df['model_points'].mean():.4f}\n")
    print(f"{'policy':22}{'mean_pts':>9}{'vs raw_ev':>11}{'95% CI':>22}{'p':>8}{'sig':>6}")
    print("-" * 78)
    for p in policies:
        arr = np.array(pts[p])
        if p == "raw_ev":
            print(f"{p:22}{arr.mean():9.4f}{'(baseline)':>11}")
            continue
        d = paired_bootstrap_delta(arr, base)
        ci_txt = f"[{d['ci_low']:+.4f},{d['ci_high']:+.4f}]"
        print(f"{p:22}{arr.mean():9.4f}{d['mean_delta']:+11.4f}{ci_txt:>22}{d['p_value']:8.3f}{str(d['significant']):>6}")

    print(f"\nAggregate EV calibration: mean predicted EV {pred_ev_sum / n:.4f} vs realised {realized_sum / n:.4f} "
          f"(gap {pred_ev_sum / n - realized_sum / n:+.4f})")
    print("\nPer-scoreline calibration (model predicted prob vs observed frequency):")
    for s in COMMON:
        pred, obs = pred_score[s] / n, obs_score[s] / n
        print(f"  {s}: pred={pred:.3f} obs={obs:.3f} gap={obs - pred:+.3f}")
    print("\nRead: if a non-raw_ev policy shows a positive, significant delta, the pick RULE is")
    print("leaving points behind (distribution miscalibrated for picking). If raw_ev wins or ties,")
    print("the pick is already optimal and the leverage is in inputs/structure, not selection.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
