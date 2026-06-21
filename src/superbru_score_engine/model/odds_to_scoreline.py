from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from superbru_score_engine.config import ModelConfig
from superbru_score_engine.ingest import MatchOdds

from .devig import (
    FairAsianHandicapMarket,
    FairOutcomeMarket,
    FairTotalMarket,
    extract_correct_score_market,
    extract_fair_1x2,
    extract_fair_1x2_book_level,
    extract_fair_asian_handicap,
    extract_fair_totals_all,
    primary_total_market,
)
from .dixon_coles import apply_dixon_coles, geometric_blend
from .poisson import (
    independent_poisson_matrix,
    matrix_asian_handicap_home_probability,
    matrix_over_probability,
    outcome_probabilities,
    poisson_cdf,
    solve_lambdas,
)
from .ratings import RatingsStore
from .uncertainty import market_disagreement_1x2


@dataclass(frozen=True)
class DistributionResult:
    match: MatchOdds
    matrix: np.ndarray
    lambda_home: float
    lambda_away: float
    fair_1x2: FairOutcomeMarket | None
    fair_total: FairTotalMarket | None
    diagnostics: dict[str, object]
    fair_totals: tuple[FairTotalMarket, ...] = ()
    fair_asian_handicap: FairAsianHandicapMarket | None = None


class OddsToScorelineModel:
    def __init__(self, config: ModelConfig, ratings: RatingsStore | None = None) -> None:
        self.config = config
        self.ratings = ratings or RatingsStore()

    def build_distribution(self, match: MatchOdds) -> DistributionResult:
        fair_books = extract_fair_1x2_book_level(match, self.config.devig_method)
        fair_1x2 = extract_fair_1x2(match, self.config.devig_method)
        fair_totals = extract_fair_totals_all(match, self.config.devig_method)
        fair_total = primary_total_market(fair_totals)
        fair_asian_handicap = extract_fair_asian_handicap(match, self.config.devig_method)
        disagreement = market_disagreement_1x2(fair_books)
        apply_manual_home_advantage = fair_1x2 is None
        prior_home, prior_away = self.ratings.prior_lambdas(
            match.home_team,
            match.away_team,
            neutral=match.neutral,
            venue_country=match.venue_country,
            host_teams=self.config.host_teams,
            home_advantage_goals=self.config.home_advantage_goals,
            apply_home_advantage=apply_manual_home_advantage,
        )
        ratings_diagnostics = self.ratings.diagnostics()

        diagnostics: dict[str, object] = {
            "ratings_lambda_home": prior_home,
            "ratings_lambda_away": prior_away,
            "distribution_source": "odds",
            "devig_method": self.config.devig_method,
            "calibration_profile": self.config.calibration_profile,
            "dixon_coles_rho": self.config.dixon_coles_rho,
            "manual_home_advantage_applied": apply_manual_home_advantage,
            "ratings_source": ratings_diagnostics.get("source"),
            "ratings_source_url": ratings_diagnostics.get("source_url"),
            "ratings_cutoff_date": ratings_diagnostics.get("cutoff_date"),
            "ratings_update_method": ratings_diagnostics.get("update_method"),
            "ratings_updated_at": ratings_diagnostics.get("updated_at"),
            "ratings_number_of_applied_results": ratings_diagnostics.get("number_of_applied_results"),
            "ratings_number_of_teams": ratings_diagnostics.get("number_of_teams"),
            "ratings_use_as_fallback_only": self.ratings.config.use_as_fallback_only,
            "asian_handicap_weight": self.config.asian_handicap_weight,
            "market_n_books": disagreement["n_books"],
            "market_disagreement_index": disagreement["disagreement_index"],
            "market_max_book_spread": disagreement["max_book_spread"],
            "market_home_prob_std": disagreement["home_std"],
            "market_draw_prob_std": disagreement["draw_std"],
            "market_away_prob_std": disagreement["away_std"],
        }

        if fair_1x2:
            lambda_home, lambda_away, solver_diagnostics = solve_lambdas(
                fair_1x2=fair_1x2,
                fair_total=fair_total,
                fair_totals=fair_totals,
                solver_grid_goals=self.config.solver_grid_goals,
                dixon_coles_rho=self.config.dixon_coles_rho,
                fair_asian_handicap=fair_asian_handicap,
                asian_handicap_weight=self.config.asian_handicap_weight,
            )
            diagnostics.update(solver_diagnostics)
            odds_weight = min(1.0, max(0.0, self.config.odds_weight))
            ratings_weight = min(1.0, max(0.0, self.config.ratings_weight))
            diagnostics["odds_weight_configured"] = self.config.odds_weight
            diagnostics["ratings_weight_configured"] = self.config.ratings_weight

            if self.ratings.config.use_as_fallback_only and ratings_weight > 0:
                diagnostics["ratings_blend_skipped_reason"] = "ratings.use_as_fallback_only is true for market-backed match"
                ratings_weight = 0.0
                odds_weight = 1.0

            total_weight = odds_weight + ratings_weight
            if total_weight > 0:
                odds_weight /= total_weight
                ratings_weight /= total_weight
                diagnostics["odds_weight_effective"] = odds_weight
                diagnostics["ratings_weight_effective"] = ratings_weight
                lambda_home = odds_weight * lambda_home + ratings_weight * prior_home
                lambda_away = odds_weight * lambda_away + ratings_weight * prior_away
        else:
            lambda_home, lambda_away = prior_home, prior_away
            diagnostics["distribution_source"] = "ratings_prior"
            diagnostics["ratings_only_warning"] = "No usable 1X2 market; ratings-only score distribution is lower confidence."
            diagnostics["odds_weight_configured"] = self.config.odds_weight
            diagnostics["ratings_weight_configured"] = self.config.ratings_weight
            diagnostics["odds_weight_effective"] = 0.0
            diagnostics["ratings_weight_effective"] = 1.0

        matrix = independent_poisson_matrix(lambda_home, lambda_away, self.config.model_grid_goals)
        matrix = apply_dixon_coles(matrix, lambda_home, lambda_away, self.config.dixon_coles_rho)

        correct_score_market = extract_correct_score_market(match, self.config.model_grid_goals, self.config.devig_method)
        if correct_score_market is not None and self.config.correct_score_blend_weight > 0:
            weight = self.config.correct_score_blend_weight
            correct_score_matrix = correct_score_market.to_matrix(self.config.model_grid_goals, model_matrix=matrix)
            pre_blend = matrix
            matrix = geometric_blend(matrix, correct_score_matrix, weight)
            diagnostics["correct_score_blended"] = True
            diagnostics.update(_correct_score_blend_diagnostics(pre_blend, matrix, correct_score_market, weight))
        else:
            diagnostics["correct_score_blended"] = False
            if correct_score_market is not None:
                diagnostics["correct_score_cells_quoted"] = len(correct_score_market.cell_probs)
                diagnostics["correct_score_other_mass"] = float(correct_score_market.other_mass)

        model_outcomes = outcome_probabilities(matrix)
        diagnostics.update(
            {
                "lambda_home": float(lambda_home),
                "lambda_away": float(lambda_away),
                "model_home_win": float(model_outcomes[0]),
                "model_draw": float(model_outcomes[1]),
                "model_away_win": float(model_outcomes[2]),
            }
        )
        diagnostics.update(scoreline_distribution_diagnostics(matrix, self.config.candidate_grid_goals))
        # The score matrix is renormalised, so matrix.sum() is always ~1 and cannot report
        # truncation loss. Estimate the true Poisson mass captured by the model grid from the
        # (independent) tail product, which is what the grid actually truncates.
        mass_inside_model_grid = float(
            poisson_cdf(self.config.model_grid_goals, lambda_home)
            * poisson_cdf(self.config.model_grid_goals, lambda_away)
        )
        diagnostics["probability_mass_inside_model_grid"] = mass_inside_model_grid
        diagnostics["probability_mass_outside_model_grid_estimated"] = max(0.0, 1.0 - mass_inside_model_grid)
        if fair_1x2:
            diagnostics.update(
                {
                    "fair_home_win": float(fair_1x2.home),
                    "fair_draw": float(fair_1x2.draw),
                    "fair_away_win": float(fair_1x2.away),
                }
            )
        if fair_total:
            diagnostics.update(
                {
                    "fair_total_line": float(fair_total.line),
                    "fair_over": float(fair_total.over),
                    "model_over": float(matrix_over_probability(matrix, fair_total.line)),
                }
            )
        if fair_totals:
            line_errors = [matrix_over_probability(matrix, market.line) - market.over for market in fair_totals]
            diagnostics.update(
                {
                    "fair_total_lines_count": len(fair_totals),
                    "fair_total_lines": ",".join(f"{market.line:g}" for market in fair_totals),
                    "model_over_rmse_across_lines": float(np.sqrt(np.mean(np.square(line_errors)))),
                }
            )
        if fair_asian_handicap:
            model_ah_home_cover = matrix_asian_handicap_home_probability(matrix, fair_asian_handicap.line)
            diagnostics.update(
                {
                    "has_asian_handicap": True,
                    "fair_ah_line": float(fair_asian_handicap.line),
                    "fair_ah_home_cover": float(fair_asian_handicap.home),
                    "fair_ah_away_cover": float(fair_asian_handicap.away),
                    "fair_ah_count": int(fair_asian_handicap.count),
                    "model_ah_home_cover": float(model_ah_home_cover),
                    "model_ah_error": float(model_ah_home_cover - fair_asian_handicap.home),
                }
            )
        else:
            diagnostics["has_asian_handicap"] = False

        return DistributionResult(
            match=match,
            matrix=matrix,
            lambda_home=float(lambda_home),
            lambda_away=float(lambda_away),
            fair_1x2=fair_1x2,
            fair_total=fair_total,
            diagnostics=diagnostics,
            fair_totals=tuple(fair_totals),
            fair_asian_handicap=fair_asian_handicap,
        )


def _correct_score_blend_diagnostics(pre: np.ndarray, post: np.ndarray, market, weight: float) -> dict[str, object]:
    pre_modal = np.unravel_index(int(np.argmax(pre)), pre.shape)
    post_modal = np.unravel_index(int(np.argmax(post)), post.shape)
    return {
        "correct_score_blend_weight": float(weight),
        "correct_score_books": int(market.count),
        "correct_score_cells_quoted": len(market.cell_probs),
        "correct_score_other_mass": float(market.other_mass),
        "correct_score_modal_before": f"{int(pre_modal[0])}-{int(pre_modal[1])}",
        "correct_score_modal_after": f"{int(post_modal[0])}-{int(post_modal[1])}",
        # Total-variation distance: how far the blend moved the scoreline distribution.
        "correct_score_total_variation_shift": float(0.5 * np.abs(post - pre).sum()),
    }


def scoreline_distribution_diagnostics(matrix: np.ndarray, candidate_grid_goals: int) -> dict[str, object]:
    max_home, max_away = matrix.shape[0] - 1, matrix.shape[1] - 1
    home_goals = np.arange(matrix.shape[0], dtype=float)
    away_goals = np.arange(matrix.shape[1], dtype=float)
    home_grid = home_goals[:, None]
    away_grid = away_goals[None, :]

    modal_idx = tuple(int(value) for value in np.unravel_index(np.argmax(matrix), matrix.shape))
    total_goals_mean = float(((home_grid + away_grid) * matrix).sum())
    expected_goal_difference = float(((home_grid - away_grid) * matrix).sum())

    candidate_limit = min(candidate_grid_goals, max_home, max_away)
    candidate_mass = float(matrix[: candidate_limit + 1, : candidate_limit + 1].sum())

    diagnostics: dict[str, object] = {
        "total_goals_mean": total_goals_mean,
        "expected_goal_difference": expected_goal_difference,
        "modal_scoreline": f"{modal_idx[0]}-{modal_idx[1]}",
        "modal_scoreline_probability": float(matrix[modal_idx]),
        "top_exact_scoreline": f"{modal_idx[0]}-{modal_idx[1]}",
        "top_exact_scoreline_probability": float(matrix[modal_idx]),
        "score_matrix_max_goals": int(max_home),
        "score_matrix_truncated_and_renormalised": True,
        "probability_mass_inside_model_grid": float(matrix.sum()),
        "probability_mass_outside_model_grid_estimated": None,
        "candidate_grid_goals": int(candidate_grid_goals),
        "probability_mass_inside_candidate_grid": candidate_mass,
        "probability_mass_outside_candidate_grid": max(0.0, 1.0 - candidate_mass),
    }
    for home, away in ((0, 0), (1, 0), (0, 1), (1, 1), (2, 1), (1, 2)):
        diagnostics[f"p_score_{home}_{away}"] = float(matrix[home, away]) if home <= max_home and away <= max_away else 0.0
    return diagnostics
