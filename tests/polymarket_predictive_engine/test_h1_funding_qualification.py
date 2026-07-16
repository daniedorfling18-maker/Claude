"""WO-93 exact sharp-qualified H1 and Tier-0 prerequisite tests."""

from __future__ import annotations

from pathlib import Path

import yaml

from polymarket_predictive_engine.h1_funding_qualification import (
    effective_qualification_settings,
    evaluate_h1_funding_qualification,
)
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.utils import parse_timestamp, read_json, write_csv, write_json


AS_OF = parse_timestamp("2026-07-16T16:00:00Z")


def _config(tmp_path: Path):
    raw = yaml.safe_load(
        Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8")
    )
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def _study() -> dict:
    return {
        "status": "ok",
        "generated_at_utc": "2026-07-16T15:55:00Z",
        "portfolio": [
            {
                "condition_id": "0xh1",
                "token_id": "token-h1",
                "question": "Exact H1 market",
                "quote_bid_price": 0.48,
                "quote_ask_price": 0.52,
            }
        ],
    }


def _write_pass_inputs(cfg, study: dict | None = None) -> dict:
    study = study or _study()
    write_csv(
        cfg.governance_root / "sharp_anchor_mapping_audit.csv",
        [
            {
                "source": "sharp_book_a",
                "sport": "basketball_nba",
                "market_key": "h2h",
                "market_slug": "exact-h1-market",
                "outcome": "Yes",
                "anchor_timestamp_utc": "2026-07-16T15:50:00Z",
                "token_id": "token-h1",
                "anchor_probability": 0.50,
                "mapping_status": "joined",
            },
            {
                "source": "sharp_book_b",
                "sport": "basketball_nba",
                "market_key": "h2h",
                "market_slug": "exact-h1-market",
                "outcome": "Yes",
                "anchor_timestamp_utc": "2026-07-16T15:51:00Z",
                "token_id": "token-h1",
                "anchor_probability": 0.51,
                "mapping_status": "joined",
            },
        ],
    )
    write_csv(
        cfg.output_root / "polymarket_predictions" / "predictions.csv",
        [
            {
                "condition_id": "0xh1",
                "token_id": "token-h1",
                "prediction_timestamp": "2026-07-16T15:59:00Z",
                "best_bid": 0.47,
                "best_ask": 0.53,
            }
        ],
    )
    horizons = {
        horizon: {"windows_simulated": 30, "windows_covered": 24, "coverage_ratio": 0.8}
        for horizon in ("5m", "15m", "60m")
    }
    markouts = {
        horizon: {"count": 10, "mean": 0.001, "min": -0.01, "median": 0.001, "max": 0.01}
        for horizon in ("5m", "15m", "60m")
    }
    write_json(
        cfg.output_root / "maker_carry" / "maker_fill_replay.json",
        {
            "status": "ok",
            "generated_at_utc": "2026-07-16T15:59:30Z",
            "portfolio_generated_at_utc": study["generated_at_utc"],
            "primary_book_source": "official",
            "per_market_coverage": [
                {
                    "condition_id": "0xh1",
                    "asset_id": "token-h1",
                    "last_in_queue_evaluable_opportunities": 30,
                    "confirmed_fills": 10,
                    "confirmed_fill_ratio": 1 / 3,
                    "confirmed_fill_ratio_status": "observed",
                    "by_horizon": horizons,
                    "realized_markout_distribution": markouts,
                    "simulation_to_reality_haircut": 0.8,
                }
            ],
        },
    )
    return study


def _evaluate(cfg, study: dict | None = None, *, as_of=AS_OF):
    study = study or _study()
    return evaluate_h1_funding_qualification(
        cfg,
        study,
        "0xh1",
        observed_at=as_of,
    )


def test_exact_market_sharp_and_tier0_evidence_passes(tmp_path):
    cfg = _config(tmp_path)
    study = _write_pass_inputs(cfg)

    result = _evaluate(cfg, study)

    assert result["state"] == "pass"
    assert result["funding_evidence_supported"] is True
    assert result["sharp_qualification"]["fair_inside_proposed_quote_band"] is True
    assert result["tier0_qualification"]["exact_market_rows"] == 1
    assert result["paper_trading_invoked"] is False
    assert result["live_trading_invoked"] is False
    assert result["order_placement_invoked"] is False
    assert read_json(
        cfg.output_root / "maker_carry" / "h1_funding_qualification.json"
    )["state"] == "pass"


def test_aggregate_or_other_market_tier0_cannot_substitute(tmp_path):
    cfg = _config(tmp_path)
    study = _write_pass_inputs(cfg)
    replay_path = cfg.output_root / "maker_carry" / "maker_fill_replay.json"
    replay = read_json(replay_path)
    replay["confirmed_fills"] = 999
    replay["coverage"] = {"coverage_ratio": 1.0}
    replay["per_market_coverage"][0]["condition_id"] = "0xother"
    write_json(replay_path, replay)

    result = _evaluate(cfg, study)

    assert result["state"] == "fail"
    assert "tier0:tier0_exact_market_evidence_missing_or_ambiguous" in result["blockers"]


def test_pre_registration_portfolio_and_replay_are_ineligible(tmp_path):
    cfg = _config(tmp_path)
    study = _study()
    study["generated_at_utc"] = "2026-07-16T14:03:26Z"
    _write_pass_inputs(cfg, study)
    replay_path = cfg.output_root / "maker_carry" / "maker_fill_replay.json"
    replay = read_json(replay_path)
    replay["generated_at_utc"] = "2026-07-16T14:03:26Z"
    write_json(replay_path, replay)

    result = _evaluate(cfg, study)

    assert result["state"] == "fail"
    assert "tier0:maker_study_not_post_registration" in result["blockers"]
    assert "tier0:tier0_replay_not_post_registration" in result["blockers"]


def test_sharp_fair_outside_quote_band_fails(tmp_path):
    cfg = _config(tmp_path)
    study = _write_pass_inputs(cfg)
    audit_path = cfg.governance_root / "sharp_anchor_mapping_audit.csv"
    write_csv(
        audit_path,
        [
            {
                "source": "sharp_book",
                "anchor_timestamp_utc": "2026-07-16T15:50:00Z",
                "token_id": "token-h1",
                "anchor_probability": 0.60,
                "mapping_status": "joined",
            }
        ],
    )

    result = _evaluate(cfg, study)

    assert result["state"] == "fail"
    assert "sharp:sharp_fair_outside_proposed_maker_quote_band" in result["blockers"]


def test_timestamp_boundaries_advance_and_future_data_fails(tmp_path):
    cfg = _config(tmp_path)
    study = _write_pass_inputs(cfg)
    audit_path = cfg.governance_root / "sharp_anchor_mapping_audit.csv"
    write_csv(
        audit_path,
        [
            {
                "source": "sharp_book",
                "anchor_timestamp_utc": "2026-07-16T14:03:27Z",
                "token_id": "token-h1",
                "anchor_probability": 0.50,
                "mapping_status": "joined",
            }
        ],
    )

    write_csv(
        cfg.output_root / "polymarket_predictions" / "predictions.csv",
        [
            {
                "condition_id": "0xh1",
                "token_id": "token-h1",
                "prediction_timestamp": "2026-07-16T20:03:00Z",
                "best_bid": 0.47,
                "best_ask": 0.53,
            }
        ],
    )
    at_boundary = _evaluate(cfg, study, as_of=parse_timestamp("2026-07-16T20:03:27Z"))
    after_boundary = _evaluate(cfg, study, as_of=parse_timestamp("2026-07-16T20:03:28Z"))

    assert at_boundary["sharp_qualification"]["state"] == "pass"
    assert after_boundary["sharp_qualification"]["state"] == "fail"
    assert "sharp:no_fresh_valid_exact_token_sharp_join" in after_boundary["blockers"]

    write_csv(
        audit_path,
        [
            {
                "source": "sharp_book",
                "anchor_timestamp_utc": "2026-07-16T16:02:00Z",
                "token_id": "token-h1",
                "anchor_probability": 0.50,
                "mapping_status": "joined",
            }
        ],
    )
    future = _evaluate(cfg, study)
    assert future["sharp_qualification"]["state"] == "fail"


def test_tier0_sample_coverage_markout_and_haircut_floors_fail_closed(tmp_path):
    cfg = _config(tmp_path)
    study = _write_pass_inputs(cfg)
    replay_path = cfg.output_root / "maker_carry" / "maker_fill_replay.json"
    replay = read_json(replay_path)
    market = replay["per_market_coverage"][0]
    market["last_in_queue_evaluable_opportunities"] = 29
    market["confirmed_fills"] = 9
    market["by_horizon"]["60m"]["coverage_ratio"] = 0.79
    market["realized_markout_distribution"]["60m"]["count"] = 9
    market["simulation_to_reality_haircut"] = 1.01
    write_json(replay_path, replay)

    result = _evaluate(cfg, study)

    assert result["state"] == "fail"
    assert "tier0:tier0_evaluable_opportunity_floor_not_met" in result["blockers"]
    assert "tier0:tier0_confirmed_fill_floor_not_met" in result["blockers"]
    assert "tier0:tier0_60m_coverage_floor_not_met" in result["blockers"]
    assert "tier0:tier0_60m_markout_floor_not_met" in result["blockers"]
    assert "tier0:tier0_market_haircut_exceeds_registered_maximum" in result["blockers"]


def test_config_overrides_can_only_tighten(tmp_path):
    cfg = _config(tmp_path)
    cfg.raw["decision_policy"].update(
        {
            "h1_max_anchor_age_seconds": 99_999,
            "h1_max_price_age_seconds": 99_999,
            "h1_tier0_max_replay_age_seconds": 99_999,
            "h1_max_anchor_disagreement": 0.50,
            "h1_tier0_min_evaluable_opportunities": 1,
            "h1_tier0_min_confirmed_fills": 1,
            "h1_tier0_min_horizon_coverage_ratio": 0.1,
            "h1_tier0_max_simulation_to_reality_haircut": 9.0,
        }
    )
    settings = effective_qualification_settings(cfg)
    assert settings["max_anchor_age_seconds"] == 21600
    assert settings["max_price_age_seconds"] == 300
    assert settings["max_replay_age_seconds"] == 1800
    assert settings["max_anchor_disagreement"] == 0.03
    assert settings["min_tier0_evaluable_opportunities"] == 30
    assert settings["min_tier0_confirmed_fills"] == 10
    assert settings["min_tier0_horizon_coverage_ratio"] == 0.8
    assert settings["max_tier0_simulation_to_reality_haircut"] == 1.0

    cfg.raw["decision_policy"].update(
        {
            "h1_max_anchor_age_seconds": 3600,
            "h1_tier0_min_evaluable_opportunities": 40,
            "h1_tier0_min_horizon_coverage_ratio": 0.9,
            "h1_tier0_max_simulation_to_reality_haircut": 0.8,
        }
    )
    tightened = effective_qualification_settings(cfg)
    assert tightened["max_anchor_age_seconds"] == 3600
    assert tightened["min_tier0_evaluable_opportunities"] == 40
    assert tightened["min_tier0_horizon_coverage_ratio"] == 0.9
    assert tightened["max_tier0_simulation_to_reality_haircut"] == 0.8
