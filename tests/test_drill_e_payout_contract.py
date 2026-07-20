from pathlib import Path


def test_drill_e_cannot_run_below_the_reward_payout_floor() -> None:
    text = Path("docs/MICRO_DRILL_RUNBOOK.md").read_text(encoding="utf-8")

    assert "expected eligible reward of at least" in text
    assert "$1.00" in text
    assert "BLOCKED_NO_FLOOR_CLEARING_TICKET" in text
    assert "A zero payment on" in text
    assert "INCONCLUSIVE, not a pipeline failure" in text
