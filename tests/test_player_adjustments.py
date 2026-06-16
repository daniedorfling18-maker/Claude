"""Player-adjustment extension point: zero by default, shrunk when supplied."""
from superbru_score_engine.model.player_adjustments import PlayerAdjustmentStore


def test_zero_by_default():
    store = PlayerAdjustmentStore()
    assert store.applied is False
    adj = store.get("Brazil")
    assert adj.effective_attack == 0.0
    assert adj.effective_defence == 0.0


def test_supplied_adjustment_is_confidence_shrunk(tmp_path):
    csv = tmp_path / "adj.csv"
    csv.write_text(
        "team,attack_adjustment_goals,defence_adjustment_goals,goalkeeper_adjustment_goals,"
        "confidence,source,source_timestamp,note\n"
        "Brazil,0.40,-0.20,-0.10,0.50,unit-test,2026-06-16,example\n"
    )
    store = PlayerAdjustmentStore.from_csv(csv)
    assert store.applied is True
    brazil = store.get("Brazil")
    assert abs(brazil.effective_attack - 0.20) < 1e-9            # 0.5 * 0.40
    assert abs(brazil.effective_defence - (0.5 * -0.30)) < 1e-9  # 0.5 * (-0.20 + -0.10)
    # teams not present remain zero
    assert store.get("Wales").effective_attack == 0.0
