#!/usr/bin/env python3
"""How much *can* multi-line totals move the pick? A controlled magnitude probe.

No free historical dataset quotes more than the over/under 2.5 line, so a points
backtest cannot measure the multi-line lever - on 2.5-only data the solver
provably collapses to the single-line fit. This probe instead isolates the
*mechanism*: when a market's totals ladder (1.5 / 2.5 / 3.5) carries shape
information that a single 2.5 line does not, how far does the recovered lambda
and the EV-optimal Superbru pick move?

Method (SYNTHETIC, clearly not a performance claim):
  * Fix a representative fair 1X2 and a baseline Poisson total mean mu0.
  * Read the Poisson-consistent over-probabilities at 1.5 / 2.5 / 3.5.
  * Perturb the 1.5 and 3.5 over-probs by +/- delta while holding the 2.5
    over-prob fixed and the ladder monotone - i.e. a market whose totals
    distribution is fatter/thinner-tailed than Poisson but agrees on the main
    line. delta=0 reproduces the single-line case exactly (sanity check).
  * Solve lambdas two ways: single line (2.5 only) vs the full perturbed ladder.
  * Report the total-goals shift and whether the EV-optimal pick changes.

A pick that barely moves even under aggressive shape divergence confirms the
lever is marginal; large moves would justify sourcing a real multi-line feed.
"""
from __future__ import annotations

import argparse

import numpy as np

from superbru_score_engine.config import load_config
from superbru_score_engine.model.devig import FairOutcomeMarket, FairTotalMarket
from superbru_score_engine.model.dixon_coles import apply_dixon_coles
from superbru_score_engine.model.poisson import (
    independent_poisson_matrix,
    over_probability,
    solve_lambdas,
)
from superbru_score_engine.decision.superbru import score_actual_prediction

# (label, fair_home, fair_draw, fair_away, baseline total mean)
FIXTURES = [
    ("even / mid total", 0.40, 0.28, 0.32, 2.6),
    ("clear favourite / mid", 0.62, 0.23, 0.15, 2.7),
    ("strong favourite / low", 0.70, 0.20, 0.10, 2.2),
    ("even / high total", 0.42, 0.22, 0.36, 3.3),
]


def ev_pick(lambda_home: float, lambda_away: float, rho: float, model_grid: int, cand_grid: int, ci: float):
    matrix = apply_dixon_coles(independent_poisson_matrix(lambda_home, lambda_away, model_grid), lambda_home, lambda_away, rho)
    actual_h = np.arange(matrix.shape[0])[:, None]
    actual_a = np.arange(matrix.shape[1])[None, :]
    best, best_ev = None, -1.0
    for ph in range(cand_grid + 1):
        for pa in range(cand_grid + 1):
            points = np.vectorize(lambda ah, aa: score_actual_prediction(ph, pa, int(ah), int(aa), ci))(actual_h, actual_a)
            ev = float((points * matrix).sum())
            if ev > best_ev + 1e-12:
                best_ev, best = ev, (ph, pa)
    return best, best_ev


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--deltas", default="0.0,0.03,0.06", help="over-prob perturbations to probe")
    args = ap.parse_args()

    cfg = load_config(args.config)
    rho = cfg.model.dixon_coles_rho
    solver_grid = cfg.model.solver_grid_goals
    model_grid = cfg.model.model_grid_goals
    cand_grid = cfg.model.candidate_grid_goals
    ci = cfg.superbru.ci_cutoff
    deltas = [float(x) for x in args.deltas.split(",")]

    print(f"Multi-line totals magnitude probe (SYNTHETIC) | rho={rho} grid={solver_grid} ci={ci}")
    print("delta is the over-prob shift applied to the 1.5 (down) and 3.5 (up) lines, 2.5 held fixed.\n")
    header = f"{'fixture':24} {'shape':14} {'lam_tot_1line':>13} {'lam_tot_multi':>13} {'d_lam_tot':>9} {'pick_1line':>10} {'pick_multi':>10} {'changed':>7}"
    print(header)
    print("-" * len(header))

    rows = []
    for label, fh, fd, fa, mu0 in FIXTURES:
        fair_1x2 = FairOutcomeMarket(home=fh, draw=fd, away=fa)
        o15, o25, o35 = (over_probability(mu0, line) for line in (1.5, 2.5, 3.5))

        # Single-line fit (2.5 only) is the production baseline for this fixture.
        lh1, la1, _ = solve_lambdas(fair_1x2, FairTotalMarket(2.5, o25, 1 - o25, count=3), solver_grid, dixon_coles_rho=rho)
        pick1, _ = ev_pick(lh1, la1, rho, model_grid, cand_grid, ci)
        lam_tot_1 = lh1 + la1

        for delta in deltas:
            # Fatter tails: more low-scoring mass (lower over 1.5) AND more high mass
            # (higher over 3.5), with the 2.5 main line unchanged. Keep monotone & in (0,1).
            o15p = min(0.999, max(o25 + 1e-3, o15 - delta))
            o35p = max(0.001, min(o25 - 1e-3, o35 + delta))
            ladder = [
                FairTotalMarket(1.5, o15p, 1 - o15p, count=2),
                FairTotalMarket(2.5, o25, 1 - o25, count=3),
                FairTotalMarket(3.5, o35p, 1 - o35p, count=2),
            ]
            lhm, lam, _ = solve_lambdas(fair_1x2, None, solver_grid, dixon_coles_rho=rho, fair_totals=ladder)
            pickm, _ = ev_pick(lhm, lam, rho, model_grid, cand_grid, ci)
            lam_tot_m = lhm + lam
            shape = "poisson" if delta == 0 else f"fat+/-{delta:g}"
            changed = "YES" if pickm != pick1 else "-"
            print(f"{label:24} {shape:14} {lam_tot_1:13.3f} {lam_tot_m:13.3f} {lam_tot_m - lam_tot_1:9.3f} "
                  f"{f'{pick1[0]}-{pick1[1]}':>10} {f'{pickm[0]}-{pickm[1]}':>10} {changed:>7}")
            rows.append((label, delta, lam_tot_1, lam_tot_m, pick1, pickm, pickm != pick1))

    moved = sum(1 for *_, ch in rows if ch)
    nonzero = [r for r in rows if r[1] > 0]
    moved_nz = sum(1 for *_, ch in nonzero if ch)
    max_shift = max((abs(b - a) for _, d, a, b, *_ in rows if d > 0), default=0.0)
    print(f"\nPicks changed: {moved}/{len(rows)} overall; {moved_nz}/{len(nonzero)} of shape-divergent ladders.")
    print(f"Max total-goals shift from adding lines (delta>0): {max_shift:.3f} goals.")
    print("delta=0 rows must show d_lam_tot~0 and 'changed=-' (multi-line == single-line sanity check).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
