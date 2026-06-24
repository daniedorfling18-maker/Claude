import itertools

from polymarket_predictive_engine.tournament import (
    GroupStage,
    derive_group_stage,
    norm,
    simulate_winner_probabilities,
)


def _empty_stage():
    teams = [f"t{i}" for i in range(8)]
    standings = {t: {"pts": 0.0, "gd": 0, "played": 0} for t in teams}
    return GroupStage(groups={"A": teams[:4], "B": teams[4:]}, teams=set(teams),
                      standings=standings, remaining=[])


def test_norm_aliases():
    assert norm("Czechia") == "czech republic"
    assert norm("Korea Republic") == "south korea"
    assert norm("Côte d'Ivoire") == "ivory coast"


def test_derive_groups_and_standings():
    a = ["A1", "A2", "A3", "A4"]
    b = ["B1", "B2", "B3", "B4"]
    fixtures = list(itertools.combinations(a, 2)) + list(itertools.combinations(b, 2))
    results = [("A1", "A2", 2, 0)]
    stage = derive_group_stage(fixtures, results)
    assert len(stage.groups) == 2
    assert all(len(g) == 4 for g in stage.groups.values())
    assert stage.standings[norm("A1")]["pts"] == 3
    assert stage.standings[norm("A2")]["pts"] == 0
    assert stage.standings[norm("A1")]["gd"] == 2
    assert len(stage.remaining) == 11  # 12 group matches minus the one completed


def test_winner_probs_sum_to_one_and_favour_strong():
    stage = _empty_stage()
    ratings = {f"t{i}": 0.0 for i in range(8)}
    ratings["t0"] = 2.0  # clearly strongest
    probs = simulate_winner_probabilities(stage, ratings, n_sims=3000, seed=1, best_thirds=0)
    assert abs(sum(probs.values()) - 1.0) < 1e-9
    assert all(0.0 <= p <= 1.0 for p in probs.values())
    assert probs["t0"] == max(probs.values())
    assert probs["t0"] > 1 / 8  # beats uniform


def test_equal_ratings_give_roughly_uniform():
    stage = _empty_stage()
    ratings = {f"t{i}": 0.0 for i in range(8)}
    probs = simulate_winner_probabilities(stage, ratings, n_sims=4000, seed=7, best_thirds=0)
    # 4 qualifiers (2 groups x top 2), each champion ~25%; every team in 0..0.4
    assert all(0.0 <= p <= 0.45 for p in probs.values())
    assert abs(sum(probs.values()) - 1.0) < 1e-9
