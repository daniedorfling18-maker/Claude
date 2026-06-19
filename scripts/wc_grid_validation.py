#!/usr/bin/env python3
"""Validate Oddspedia SmartBet correct-score grids (and our engine) against real WC 2026 results.

Data is transcribed from Oddspedia SmartBet "prematch probabilities" screenshots: each
match's de-vigged 1X2 winner probabilities, the grid's modal (most-likely) scoreline, and
the actual finished result. For each match we:

  * pick the favourite outcome from the 1X2 probs and check it against the result;
  * score the Oddspedia MODAL scoreline (the highlighted cell a casual picker copies);
  * run OUR engine on the same 1X2 (solve lambdas, Dixon-Coles, EV-optimal pick) and score it.

Key question: does taking the EV-optimal pick beat copying the grid's modal score? It does -
on real results, just as the 5,330-match league backtest showed - which is the actionable
lesson: feed the grid through the EV pick, do not copy the highlighted score.

Caveats: small sample, transcribed by hand, "our model" uses 1X2 only (no totals from the
screenshots), and these are SmartBet model probabilities, not raw market odds. Modal = None
means the grid's most-likely outcome was a bucket ("other home/away win"), which has no single
scoreline to pick, so it is excluded from the modal scoring.
"""
from __future__ import annotations

import numpy as np

from superbru_score_engine.backtest.inference import bootstrap_mean_ci, paired_bootstrap_delta
from superbru_score_engine.decision.superbru import score_actual_prediction
from superbru_score_engine.model.devig import FairOutcomeMarket
from superbru_score_engine.model.dixon_coles import apply_dixon_coles
from superbru_score_engine.model.poisson import independent_poisson_matrix, solve_lambdas

CI_CUTOFF, RHO, MODEL_GRID, CANDIDATE_GRID, SOLVER_GRID = 1.5, -0.08, 10, 6, 14

# home, away, actual_home, actual_away, p_home%, p_draw%, p_away%, oddspedia_modal_score
# modal None => grid's most-likely cell was a bucket ("other home/away win"), not a single score.
MATCHES = [
    ("HAI", "SCO", 0, 1, 33, 23, 44, (1, 1)),
    ("BRA", "MAR", 1, 1, 42, 27, 32, (1, 1)),
    ("QAT", "SUI", 1, 1, 18, 25, 56, (0, 1)),
    ("CAN", "BIH", 1, 1, 51, 27, 23, (1, 0)),
    ("KOR", "CZE", 2, 1, 42, 26, 32, (1, 1)),
    ("MEX", "RSA", 2, 0, 40, 28, 32, (1, 1)),
    ("USA", "PAR", 4, 1, 46, 26, 28, (1, 1)),
    ("NED", "JPN", 2, 2, 44, 26, 31, (1, 1)),
    ("CIV", "ECU", 1, 0, 42, 28, 30, (1, 1)),
    ("GER", "CUR", 7, 1, 66, 21, 13, (1, 0)),
    ("AUS", "TUR", 2, 0, 23, 22, 55, None),
    ("IRI", "NZL", 2, 2, 56, 23, 20, (1, 0)),
    ("BEL", "EGY", 1, 1, 54, 24, 22, (1, 0)),
    ("SWE", "TUN", 5, 1, 45, 25, 29, (1, 1)),
    ("ARG", "ALG", 3, 0, 51, 25, 24, (1, 0)),
    ("IRQ", "NOR", 1, 4, 15, 22, 63, (0, 1)),
    ("FRA", "SEN", 3, 1, 53, 23, 24, None),
    ("KSA", "URU", 1, 1, 25, 27, 48, (0, 1)),
    ("ESP", "CPV", 0, 0, 67, 21, 12, (1, 0)),
    ("AUT", "JOR", 3, 1, 61, 21, 18, None),
    ("POR", "COD", 1, 1, 61, 23, 17, (1, 0)),
    ("ENG", "CRO", 4, 2, 48, 23, 29, (1, 1)),
    ("GHA", "PAN", 1, 0, 30, 26, 44, (1, 1)),
    ("UZB", "COL", 1, 3, 20, 26, 54, (0, 1)),
    ("CZE", "RSA", 1, 1, 37, 27, 37, (1, 1)),
    ("SUI", "BIH", 4, 1, 52, 26, 22, (1, 0)),
]

_CANDIDATES = [(h, a) for h in range(CANDIDATE_GRID + 1) for a in range(CANDIDATE_GRID + 1)]


def our_ev_pick(p_home: float, p_draw: float, p_away: float) -> tuple[int, int]:
    total = p_home + p_draw + p_away
    fair = FairOutcomeMarket(p_home / total, p_draw / total, p_away / total)
    lambda_home, lambda_away, _ = solve_lambdas(fair, None, SOLVER_GRID, dixon_coles_rho=RHO)
    matrix = apply_dixon_coles(independent_poisson_matrix(lambda_home, lambda_away, MODEL_GRID), lambda_home, lambda_away, RHO)
    best, best_ev = None, -1.0
    for (ph, pa) in _CANDIDATES:
        ev = sum(
            matrix[i, j] * score_actual_prediction(ph, pa, i, j, CI_CUTOFF)
            for i in range(matrix.shape[0])
            for j in range(matrix.shape[1])
        )
        if ev > best_ev + 1e-12:
            best_ev, best = ev, (ph, pa)
    return best


def main() -> int:
    outcome_hits = draws = 0
    our_pts: list[float] = []
    rows = []
    paired_our, paired_modal = [], []  # only on matches with a specific modal score
    for (home, away, ah, aa, ph, pd, pa, modal) in MATCHES:
        favourite = ("home", "draw", "away")[int(np.argmax([ph, pd, pa]))]
        actual = "home" if ah > aa else ("away" if ah < aa else "draw")
        outcome_hits += favourite == actual
        draws += actual == "draw"
        pick = our_ev_pick(ph, pd, pa)
        our = score_actual_prediction(pick[0], pick[1], ah, aa, CI_CUTOFF)
        our_pts.append(our)
        modal_pts = None
        if modal is not None:
            modal_pts = score_actual_prediction(modal[0], modal[1], ah, aa, CI_CUTOFF)
            paired_our.append(our)
            paired_modal.append(modal_pts)
        rows.append((f"{home} {ah}-{aa} {away}", f"{ph}/{pd}/{pa}", actual, "ok" if favourite == actual else "MISS",
                     f"{pick[0]}-{pick[1]}", our, (f"{modal[0]}-{modal[1]}" if modal else "bucket"), modal_pts))

    n = len(MATCHES)
    print(f"WC 2026 grid validation | {n} matches | rho={RHO} ci={CI_CUTOFF}")
    print(f"{'match':16}{'1X2%':11}{'actual':7}{'fav':5}{'ourPick':8}{'ourP':6}{'odpModal':9}{'odpP':5}")
    print("-" * 71)
    for r in rows:
        print(f"{r[0]:16}{r[1]:11}{r[2]:7}{r[3]:5}{r[4]:8}{r[5]:<6}{r[6]:9}{'' if r[7] is None else r[7]:<5}")

    print(f"\ndraws: {draws}/{n} ({draws/n:.0%})   favourite/outcome hits: {outcome_hits}/{n} ({outcome_hits/n:.0%})")
    our_ci = bootstrap_mean_ci(our_pts)
    print(f"OUR engine EV pick:   {np.mean(our_pts):.3f} pts/match  95% CI [{our_ci['ci_low']:.3f}, {our_ci['ci_high']:.3f}]  (all {n})")
    modal_ci = bootstrap_mean_ci(paired_modal)
    our_sub = float(np.mean(paired_our))
    print(f"Oddspedia modal pick: {np.mean(paired_modal):.3f} pts/match  95% CI [{modal_ci['ci_low']:.3f}, {modal_ci['ci_high']:.3f}]  ({len(paired_modal)} with a specific modal)")
    print(f"  (our engine on those same {len(paired_modal)}: {our_sub:.3f})")
    delta = paired_bootstrap_delta(paired_our, paired_modal)
    print(f"  paired EV-pick minus modal: {delta['mean_delta']:+.3f}  95% CI [{delta['ci_low']:+.3f}, {delta['ci_high']:+.3f}]  p={delta['p_value']:.3f}  sig={delta['significant']}")
    print(f"exact hits: ours={sum(1 for p in our_pts if p == 3.0)}  modal={sum(1 for p in paired_modal if p == 3.0)}")
    print("\nSmall sample, hand-transcribed; treat the EV-vs-modal GAP as the robust signal, not the absolute levels.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
