#!/usr/bin/env python3
"""Pool-simulation backtest: do the rank strategies actually beat the field?

A points backtest can't evaluate contrarian/exact_chase, because those trade
expected points for pool RANK. This Monte Carlo simulates a Superbru pool:

  * Each fixture's market scoreline distribution is rebuilt from the backtest
    results' lambda_home/lambda_away.
  * A synthetic field of N players each picks a scoreline sampled from the
    SYNTHETIC public-pick model (favourite/famous/clean-sheet over-picked,
    draws under-picked).
  * Each strategy mode (raw_ev / conservative / exact_chase / contrarian /
    risk_adjusted) plays as one extra entrant, picking per that mode.
  * Everyone is scored with real Superbru rules against the ACTUAL result.

Over many simulated pools we report each strategy's mean points AND its rank
distribution vs the field (mean rank, % of field beaten, P(win), P(top decile)).

Input: the per-fixture CSV written by `football-data-backtest`
(default outputs/bt-wc/football_data_backtest_results.csv).
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from superbru_score_engine.config import SensitivityConfig, load_config
from superbru_score_engine.decision.public_pick import estimate_public_pick_shares
from superbru_score_engine.decision.superbru import SuperbruDecisionEngine, score_actual_prediction
from superbru_score_engine.model.dixon_coles import apply_dixon_coles
from superbru_score_engine.model.odds_to_scoreline import DistributionResult
from superbru_score_engine.model.poisson import independent_poisson_matrix


@dataclass(frozen=True)
class _Match:
    match_id: str
    home_team: str
    away_team: str
    commence_time: str = ""
    neutral: bool = True
    venue_country: str | None = None


def _favourite_outcome(matrix: np.ndarray) -> str:
    home = float(np.tril(matrix, -1).sum())
    draw = float(np.trace(matrix))
    away = float(np.triu(matrix, 1).sum())
    return max({"home": home, "draw": draw, "away": away}.items(), key=lambda kv: kv[1])[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-csv", default="outputs/bt-wc/football_data_backtest_results.csv")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--players", type=int, default=29, help="synthetic field size")
    ap.add_argument("--sims", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=20260616)
    ap.add_argument("--exact-chase-weight", type=float, default=2.0)
    ap.add_argument("--field-concentration", type=float, default=8.0,
                    help="exponent on public-pick shares; >1 concentrates the field on popular scorelines")
    ap.add_argument("--pool-size", type=int, default=0,
                    help="fixtures per simulated pool (0 = all; smaller = shorter tournament, more variance)")
    ap.add_argument("--deficit", type=float, default=0.0,
                    help="points the strategy entrant starts BEHIND the field (models chasing from behind)")
    ap.add_argument("--out", default="outputs/pool_simulation.csv")
    args = ap.parse_args()

    config = load_config(args.config)
    grid = config.model.model_grid_goals
    cand_g = config.model.candidate_grid_goals
    rho = config.model.dixon_coles_rho
    pp = config.public_pick
    no_sens = SensitivityConfig(enabled=False)
    candidates = [(h, a) for h in range(cand_g + 1) for a in range(cand_g + 1)]

    modes = {
        "raw_ev": replace(config.superbru, strategy_mode="raw_ev"),
        "conservative": replace(config.superbru, strategy_mode="conservative"),
        "exact_chase": replace(config.superbru, strategy_mode="exact_chase", exact_chase_weight=args.exact_chase_weight),
        "contrarian": replace(config.superbru, strategy_mode="contrarian"),
        "risk_adjusted": replace(
            config.superbru, strategy_mode="risk_adjusted", public_pick_weight=1.0, differentiation_weight=0.5
        ),
    }
    engines = {m: SuperbruDecisionEngine(cfg, cand_g, pp, no_sens) for m, cfg in modes.items()}

    df = pd.read_csv(args.results_csv)
    fixtures = []
    for r in df.itertuples():
        lh, la = float(r.lambda_home), float(r.lambda_away)
        ah, aa = int(r.home_goals), int(r.away_goals)
        matrix = apply_dixon_coles(independent_poisson_matrix(lh, la, grid), lh, la, rho)
        cand_points = np.array(
            [score_actual_prediction(h, a, ah, aa, config.superbru.ci_cutoff) for (h, a) in candidates]
        )
        fav = _favourite_outcome(matrix)
        shares = estimate_public_pick_shares(candidates, favourite_outcome=fav,
                                              home_team=r.home_team, away_team=r.away_team, config=pp)
        share_vec = np.array([shares[c].public_pick_share for c in candidates])
        # concentrate the field onto the popular scorelines (real players pick a
        # single sensible favourite score, not a draw from the full tail)
        share_vec = share_vec ** args.field_concentration
        share_vec = share_vec / share_vec.sum()
        dist = DistributionResult(match=_Match(str(r.match_id), str(r.home_team), str(r.away_team)),
                                  matrix=matrix, lambda_home=lh, lambda_away=la,
                                  fair_1x2=None, fair_total=None, diagnostics={})
        strat_points = {}
        for m, eng in engines.items():
            pick = eng.predict(dist).recommended
            strat_points[m] = score_actual_prediction(pick.home_goals, pick.away_goals, ah, aa, config.superbru.ci_cutoff)
        fixtures.append((cand_points, share_vec, strat_points))

    mode_list = list(modes)
    cand_mat = np.array([cp for cp, _, _ in fixtures])                          # (F, K)
    share_mat = np.array([sv for _, sv, _ in fixtures])                         # (F, K)
    strat_mat = np.array([[sp[m] for m in mode_list] for _, _, sp in fixtures]) # (F, n_modes)
    F, K = cand_mat.shape
    pool = args.pool_size if 0 < args.pool_size <= F else F
    cumshare = np.clip(share_mat.cumsum(axis=1), 0.0, 1.0)
    rng = np.random.default_rng(args.seed)
    M, N = args.sims, args.players
    decile = max(1, round(0.1 * (N + 1)))
    wins = {m: 0 for m in mode_list}
    rank_sum = {m: 0 for m in mode_list}
    topdec = {m: 0 for m in mode_list}
    beaten_sum = {m: 0 for m in mode_list}
    field_mean_acc = 0.0
    for _ in range(M):
        fix = rng.choice(F, size=pool, replace=False) if pool < F else np.arange(F)
        u = rng.random((pool, N))
        field_pts = np.zeros(N)
        for i, f in enumerate(fix):
            idx = np.clip(np.searchsorted(cumshare[f], u[i]), 0, K - 1)
            field_pts += cand_mat[f][idx]
        field_mean_acc += float(field_pts.mean())
        for mi, m in enumerate(mode_list):
            total = float(strat_mat[fix, mi].sum()) - args.deficit
            rank = 1 + int((field_pts > total).sum())
            rank_sum[m] += rank
            beaten_sum[m] += int((field_pts < total).sum())
            wins[m] += int(rank == 1)
            topdec[m] += int(rank <= decile)

    n_fix = pool
    field_mean = field_mean_acc / (M * pool)
    rows = []
    for mi, m in enumerate(mode_list):
        rows.append({
            "strategy": m,
            "mean_points": round(float(strat_mat[:, mi].mean()), 4),
            "mean_rank_of_%d" % (N + 1): round(rank_sum[m] / M, 2),
            "pct_field_beaten": round(beaten_sum[m] / (M * N), 3),
            "p_win": round(wins[m] / M, 3),
            "p_top_decile": round(topdec[m] / M, 3),
        })
    out = pd.DataFrame(rows).sort_values("p_win", ascending=False)
    import os
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    out.to_csv(args.out, index=False)
    print(f"Pool simulation: {n_fix} fixtures, field of {N}, {M} simulated pools, seed {args.seed}")
    print(f"Synthetic field mean points/match: {field_mean:.4f}\n")
    print(out.to_string(index=False))
    print("\nRead: mean_points is the EV picture (raw_ev should top it). p_win / mean_rank are the")
    print("RANK picture - whether trading points for differentiation actually wins pools.")
    print("The field is a SYNTHETIC estimate of public behaviour, not real pool picks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
