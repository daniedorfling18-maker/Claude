import csv

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.readiness import count_clean_resolved_labels, paper_live_promotion_gate
from polymarket_predictive_engine.utils import write_json


def _cfg(tmp_path):
    return EngineConfig(
        raw={"paths": {"data_root": str(tmp_path), "output_root": str(tmp_path / "outputs")}},
        path=tmp_path / "cfg.yaml",
    )


def _write_resolutions(tmp_path, n_markets):
    train = tmp_path / "outputs" / "polymarket_training"
    train.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(n_markets):
        for tok, target in ((f"y{i}", 1), (f"n{i}", 0)):
            rows.append({"resolution_quality": "clean_settlement", "token_id": tok,
                         "target": target, "condition_id": f"c{i}", "market_slug": f"m{i}"})
    with (train / "market_resolutions.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["resolution_quality", "token_id", "target", "condition_id", "market_slug"])
        w.writeheader()
        w.writerows(rows)


def _write_skill(tmp_path, significant):
    gov = tmp_path / "outputs" / "polymarket_model_governance"
    write_json(gov / "skill_model_summary.json",
               {"status": "ok", "oos_vs_market": {"beats_market_significantly": significant, "brier_skill_vs_market": 0.08}})


def test_count_clean_resolved_labels(tmp_path):
    _write_resolutions(tmp_path, 60)
    assert count_clean_resolved_labels(_cfg(tmp_path)) == 120


def test_promotion_blocked_when_labels_insufficient(tmp_path):
    _write_resolutions(tmp_path, 10)          # 20 labels < 100
    _write_skill(tmp_path, significant=True)
    gate = paper_live_promotion_gate(_cfg(tmp_path))
    assert gate["resolved_labels"] == 20
    assert gate["approved_for_paper_trading"] is False
    assert any("insufficient resolved labels" in r for r in gate["paper_blockers"])


def test_promotion_blocked_when_skill_not_significant(tmp_path):
    _write_resolutions(tmp_path, 60)          # 120 labels >= 100
    _write_skill(tmp_path, significant=False)
    gate = paper_live_promotion_gate(_cfg(tmp_path))
    assert gate["approved_for_paper_trading"] is False
    assert any("out-of-sample skill" in r for r in gate["paper_blockers"])


def test_paper_approved_but_live_needs_more_labels_and_approval(tmp_path):
    _write_resolutions(tmp_path, 60)          # 120 labels: >= paper min (100), < live target (300)
    _write_skill(tmp_path, significant=True)
    gate = paper_live_promotion_gate(_cfg(tmp_path))
    assert gate["approved_for_paper_trading"] is True
    assert gate["approved_for_live_trading"] is False
    assert any("below live target" in r for r in gate["live_blockers"])
