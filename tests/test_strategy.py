"""Pick types, strategy modes, and risk-adjusted scoring (Parts 3 & 5).

The overriding contract: default strategy preserves the raw expected-points pick.
"""
from dataclasses import dataclass

import numpy as np

from superbru_score_engine.config import PublicPickConfig, SuperbruConfig
from superbru_score_engine.decision.superbru import SuperbruDecisionEngine, score_prediction
from superbru_score_engine.model.dixon_coles import apply_dixon_coles
from superbru_score_engine.model.poisson import independent_poisson_matrix


@dataclass
class _Match:
    match_id: str = "t"
    home_team: str = "Brazil"
    away_team: str = "Foo"
    commence_time: str = ""


@dataclass
class _Dist:
    matrix: object
    match: _Match
    diagnostics: dict
    lambda_home: float
    lambda_away: float


def _dist(lh=1.6, la=1.0, rho=-0.08, grid=10):
    m = apply_dixon_coles(independent_poisson_matrix(lh, la, grid), lh, la, rho)
    return _Dist(matrix=m, match=_Match(), diagnostics={"dixon_coles_rho": rho}, lambda_home=lh, lambda_away=la)


def _engine(**superbru):
    return SuperbruDecisionEngine(SuperbruConfig(**superbru), 6, PublicPickConfig())


def _manual_dist(matrix):
    matrix = np.asarray(matrix, dtype=float)
    matrix = matrix / matrix.sum()
    return _Dist(matrix=matrix, match=_Match(), diagnostics={"dixon_coles_rho": 0.0}, lambda_home=1.0, lambda_away=1.0)


def test_default_mode_recommends_raw_ev():
    p = _engine().predict(_dist())
    assert p.strategy_mode == "raw_ev"
    assert p.recommended.scoreline == p.raw_ev_pick.scoreline


def test_raw_ev_pick_is_ev_optimal():
    cfg = SuperbruConfig()
    dist = _dist()
    p = SuperbruDecisionEngine(cfg, 6).predict(dist)
    max_adj = max(
        score_prediction(dist.matrix, h, a, cfg.ci_cutoff).adjusted_expected_points
        for h in range(7)
        for a in range(7)
    )
    assert max_adj - p.raw_ev_pick.adjusted_expected_points <= cfg.tie_epsilon + 1e-9


def test_modal_pick_maximises_p_exact():
    dist = _dist()
    p = SuperbruDecisionEngine(SuperbruConfig(), 6).predict(dist)
    max_pexact = max(score_prediction(dist.matrix, h, a, 1.5).p_exact for h in range(7) for a in range(7))
    assert abs(p.modal_score_pick.p_exact - max_pexact) < 1e-12


def test_exact_chase_weight_one_equals_raw_ev():
    p = _engine(exact_chase_weight=1.0).predict(_dist())
    assert p.exact_chase_pick.scoreline == p.raw_ev_pick.scoreline


def test_exact_chase_high_weight_does_not_lower_p_exact():
    base = _engine().predict(_dist())
    chased = _engine(exact_chase_weight=3.0).predict(_dist())
    assert chased.exact_chase_pick.p_exact >= base.raw_ev_pick.p_exact - 1e-9


def test_risk_adjusted_zero_weights_equals_raw_ev():
    # all strategic weights default to 0 -> risk_adjusted_score == adjusted EV
    p = _engine(strategy_mode="risk_adjusted").predict(_dist())
    assert p.recommended.scoreline == p.raw_ev_pick.scoreline


def test_risk_adjusted_pick_is_exposed_as_first_class_pick():
    p = _engine(strategy_mode="risk_adjusted", risk_aversion=0.1).predict(_dist())
    assert p.recommended.scoreline == p.risk_adjusted_pick.scoreline
    assert p.diagnostics["risk_adjusted_scoreline"] == p.risk_adjusted_pick.scoreline


def test_private_chase_pick_is_ev_capped_and_exposed():
    matrix = [
        [0.029330271370256244, 0.010491929049973842, 0.0521012379001362, 0.05341123879599326],
        [0.0717435549525183, 0.010330830768131722, 0.14948826677753432, 0.10714590378393628],
        [0.027917605664044793, 0.03699852894806276, 0.20134151575164974, 0.040561350351612444],
        [0.02508528701167184, 0.0176093174952106, 0.07125574661746364, 0.09518741476180383],
    ]
    p = _engine(private_chase_max_ev_loss=0.05).predict(_manual_dist(matrix))

    assert p.raw_ev_pick.scoreline == "1-2"
    assert p.private_chase_pick.scoreline == "2-2"
    assert p.raw_ev_pick.expected_points - p.private_chase_pick.expected_points <= 0.05
    assert p.private_chase_pick.p_exact > p.raw_ev_pick.p_exact
    assert p.diagnostics["private_chase_scoreline"] == "2-2"


def test_private_chase_strategy_can_be_selected_explicitly():
    p = _engine(strategy_mode="private_chase", private_chase_max_ev_loss=0.05).predict(_dist())

    assert p.strategy_mode == "private_chase"
    assert p.recommended.scoreline == p.private_chase_pick.scoreline


def test_contrarian_pick_is_not_a_joke_pick():
    p = _engine().predict(_dist())
    # EV floor: contrarian pick keeps at least half the raw-EV pick's expected points
    assert p.contrarian_pick.expected_points >= 0.5 * p.raw_ev_pick.expected_points - 1e-9


def test_risk_fields_are_sane():
    p = _engine().predict(_dist())
    rec = p.recommended
    assert abs(rec.p_zero_points - (1.0 - rec.p_outcome)) < 1e-9
    assert rec.variance_points >= 0.0
    assert 0.0 <= rec.public_pick_share <= 1.0
