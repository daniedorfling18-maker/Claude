"""Synthetic public-pick model: normalisation and known public biases."""
from superbru_score_engine.decision.public_pick import estimate_public_pick_shares

CANDS = [(h, a) for h in range(4) for a in range(4)]


def _shares(home="Wales", away="Foo"):
    return estimate_public_pick_shares(CANDS, favourite_outcome="home", home_team=home, away_team=away)


def test_shares_sum_to_one():
    est = _shares()
    assert abs(sum(e.public_pick_share for e in est.values()) - 1.0) < 1e-9


def test_common_favourite_score_beats_obscure_equivalent():
    est = _shares()
    assert est[(2, 0)].public_pick_share > est[(3, 3)].public_pick_share


def test_draw_is_under_picked_vs_favourite_win():
    est = _shares()
    assert est[(1, 1)].public_pick_share < est[(2, 0)].public_pick_share
    assert est[(0, 0)].public_pick_share < est[(1, 1)].public_pick_share  # 0-0 most under-picked


def test_famous_team_raises_its_win_share():
    famous = _shares(home="Brazil")
    plain = _shares(home="Wales")
    assert famous[(2, 0)].public_pick_share > plain[(2, 0)].public_pick_share
