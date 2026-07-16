import csv

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.models.skill_model import (
    _edge_roi,
    _executable_edge_roi,
    _temporal_market_split,
    build_design,
    select_feature_columns,
    train_skill_model,
)


def _write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def _synthetic_dataset(tmp_path, n_markets=40, snaps=4, signal=True):
    """Market price is uninformative (0.5); a feature reveals the target when signal=True."""
    feat_rows, label_rows = [], []
    for mi in range(n_markets):
        target = mi % 2
        split = "train" if mi < max(1, int(n_markets * 0.7)) else "validation"
        for s in range(snaps):
            ts = f"2026-01-{1 + mi // 24:02d}T{(mi % 24):02d}:{s * 10:02d}:00Z"
            row_id = f"m{mi}-s{s}"
            feat_rows.append({
                "training_row_id": row_id,
                "market_id": f"m{mi}", "token_id": "t", "prediction_timestamp": ts,
                "split": split,
                "midpoint": 0.5, "implied_probability": 0.5,
                "best_bid": 0.49, "best_ask": 0.51, "spread": 0.02,
                "executable_buy_price": 0.51, "executable_sell_price": 0.49,
                "signal_feat": float(target if signal else 0), "hours_to_close": 10 - s,
            })
            label_rows.append({
                "training_row_id": row_id,
                "market_id": f"m{mi}", "token_id": "t", "prediction_timestamp": ts,
                "split": split, "target": target, "horizon": "terminal_resolution",
            })
    train = tmp_path / "outputs" / "polymarket_training"
    _write_csv(train / "resolved_corpus_features_v1.csv", feat_rows,
               ["training_row_id", "market_id", "token_id", "prediction_timestamp", "split", "midpoint", "implied_probability", "best_bid", "best_ask", "spread", "executable_buy_price", "executable_sell_price", "signal_feat", "hours_to_close"])
    _write_csv(train / "resolved_corpus_labels_v1.csv", label_rows,
               ["training_row_id", "market_id", "token_id", "prediction_timestamp", "split", "target", "horizon"])
    return EngineConfig(raw={"paths": {"output_root": str(tmp_path / "outputs")}}, path=tmp_path / "cfg.yaml")


def test_temporal_split_holds_out_latest_markets_without_overlap():
    rows = [{"market_id": f"m{i}", "prediction_timestamp": f"2026-01-{i+1:02d}T00:00:00Z"} for i in range(10)]
    train_idx, test_idx = _temporal_market_split(rows, test_fraction=0.3)
    train_markets = {rows[i]["market_id"] for i in train_idx}
    test_markets = {rows[i]["market_id"] for i in test_idx}
    assert not (train_markets & test_markets)          # no market spans the split
    assert test_markets == {"m7", "m8", "m9"}          # latest markets held out


def test_select_feature_columns_excludes_market_price_and_labels():
    rows = [{"midpoint": "0.5", "implied_probability": "0.5", "signal_feat": "1",
             "target": "1", "market_id": "m", "feature_source": "websocket"}]
    cols = select_feature_columns(rows)
    assert "signal_feat" in cols
    for excluded in ("midpoint", "implied_probability", "target", "market_id", "feature_source"):
        assert excluded not in cols


def test_build_design_drops_rows_without_market_price():
    rows = [{"target": 1, "implied_probability": "0.6", "signal_feat": "1"},
            {"target": 0, "implied_probability": "", "midpoint": "", "signal_feat": "0"}]
    design = build_design(rows, ["signal_feat"])
    assert len(design) == 1
    assert len(design[0]["_x"]) == 3                   # [intercept, market_logit, signal_feat]


def test_edge_roi_pays_off_when_model_beats_market():
    roi = _edge_roi(model_p=[0.9, 0.1], market_p=[0.5, 0.5], y=[1, 0],
                    markets=["a", "b"], threshold=0.05, fee=0.0, n_boot=200)
    assert roi["n_bets"] == 2
    assert roi["roi"] == 1.0                            # both bets win at price 0.5


def test_executable_edge_roi_uses_ask_and_category_fee_not_midpoint():
    rows = [
        {"best_bid": 0.40, "best_ask": 0.45, "midpoint": 0.425, "category": "sports"},
        {"best_bid": 0.50, "best_ask": 0.55, "midpoint": 0.525, "category": "sports"},
    ]
    result = _executable_edge_roi(
        model_p=[0.9, 0.1],
        rows=rows,
        y=[1, 0],
        markets=["a", "b"],
        threshold=0.05,
        n_boot=100,
    )
    assert result["n_bets"] == 1
    assert result["entry_price"] == "recorded_best_ask"
    assert result["mean_fee_per_share"] > 0


def test_skill_model_beats_market_when_feature_carries_signal(tmp_path):
    cfg = _synthetic_dataset(tmp_path, signal=True)
    summary = train_skill_model(cfg, test_fraction=0.3, l2=0.1)
    assert summary["status"] == "ok"
    assert "signal_feat" in summary["feature_columns"]
    oos = summary["oos_vs_market"]
    # market is uninformative (0.5 -> brier 0.25); the model should learn the signal
    assert oos["market_brier"] > 0.2
    assert oos["brier_skill_vs_market"] > 0.3
    assert oos["beats_market_significantly"] is True


def test_skill_model_no_edge_when_feature_is_noise(tmp_path):
    cfg = _synthetic_dataset(tmp_path, signal=False)
    summary = train_skill_model(cfg, test_fraction=0.3, l2=5.0)
    assert summary["status"] == "ok"
    # with no real signal the model cannot beat the market by a significant margin
    assert summary["oos_vs_market"]["beats_market_significantly"] is False


def test_skill_model_reports_insufficient_data(tmp_path):
    cfg = _synthetic_dataset(tmp_path, n_markets=3, snaps=2, signal=True)
    summary = train_skill_model(cfg)
    assert summary["status"] == "insufficient_data"
