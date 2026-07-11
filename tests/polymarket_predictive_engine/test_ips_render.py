"""WO-64 generated investment policy statement tests."""

from __future__ import annotations

from pathlib import Path

import yaml

from polymarket_predictive_engine import live_test_decision_policy as decision_policy
from polymarket_predictive_engine import maker_carry_study
from polymarket_predictive_engine import profit_verdict
from polymarket_predictive_engine.cli import COMMANDS
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.ips_render import (
    _sha256,
    effective_policy_bundle,
    render_ips,
    source_defaults_bundle,
)
from polymarket_predictive_engine.utils import read_json, write_json


def _config(tmp_path: Path, *, enabled: bool = True, override: dict | None = None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    raw["ips_render"] = {
        "enabled": enabled,
        "output_file": "performance/investment_policy_statement.md",
        "summary_file": "performance/investment_policy_statement_summary.json",
    }
    for section, values in (override or {}).items():
        raw.setdefault(section, {}).update(values)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def _minimal_quote_summary() -> dict:
    return {
        "generated_at_utc": "2026-07-11T12:00:00Z",
        "portfolio_net_carry_usd_per_day": 0.0,
        "portfolio_net_carry_usd_per_month": 0.0,
        "portfolio_capital_usd": 0.0,
        "capital_curve": [],
        "capital_for_100_per_month": None,
        "maker_gates": {
            "maker_verdict": "insufficient_evidence",
            "M_A_carry_evidence": {"state": "pending"},
            "M_B_adverse_realism": {"state": "pending"},
        },
        "portfolio": [],
    }


def test_ips_source_defaults_and_rendered_values_track_live_module_constants(tmp_path: Path):
    cfg = _config(
        tmp_path,
        override={
            "decision_policy": {"composition_stable_days": 9, "kelly_full_weight_days": 2},
            "maker_carry_study": {"min_daily_payout_usd": 1.25},
            "profit_verdict": {"minimum_final_samples": 15},
            "risk": {"maximum_spread": 0.04},
        },
    )

    payload = render_ips(cfg)
    markdown = Path(payload["output_path"]).read_text(encoding="utf-8")
    source = payload["source_defaults"]
    effective = payload["effective_policy"]

    assert source["decision_policy"]["settings"] == decision_policy.DEFAULT_SETTINGS
    assert source["decision_policy"]["action_policy"] == decision_policy.REGISTERED_ACTION_POLICY
    assert source["decision_policy"]["kill_criteria"] == decision_policy.KILL_CRITERIA_REGISTRATION
    assert source["maker_policy"]["gates"] == maker_carry_study.MAKER_GATE_REGISTRATION
    assert source["maker_policy"]["quote_sheet_standing_rules"] == maker_carry_study.QUOTE_SHEET_STANDING_RULES
    assert source["profit_verdict"]["gates"] == profit_verdict.VERDICT_GATE_DEFINITIONS
    assert source["profit_verdict"]["amendments"] == profit_verdict.REGISTERED_AMENDMENTS
    assert payload["source_defaults_sha256"] == _sha256(source_defaults_bundle())
    assert effective == effective_policy_bundle(cfg)
    assert effective["decision_policy"]["composition_stable_days"] == 9
    assert effective["decision_policy"]["kelly_full_weight_days"] == 20
    assert effective["maker_policy"]["min_daily_payout_usd"] == 1.25
    assert effective["profit_verdict"]["minimum_final_samples"] == 15
    assert effective["risk"]["maximum_spread"] == 0.04

    # Drift contract: every frozen setting and every amendment rendered from
    # the live constants must be visible in the document.
    for key in decision_policy.DEFAULT_SETTINGS:
        assert key in markdown
    for row in profit_verdict.REGISTERED_AMENDMENTS:
        assert row["effect"] in markdown
    assert payload["source_defaults_sha256"] in markdown
    assert payload["effective_vps_config_sha256"] in markdown

    kill = decision_policy._kill_criteria({}, [], decision_policy.effective_policy_settings(cfg))
    assert set(kill["criteria"]) == {row["id"] for row in decision_policy.KILL_CRITERIA_REGISTRATION}
    for row in maker_carry_study.MAKER_GATE_REGISTRATION:
        assert maker_carry_study.maker_gate_registration(row["id"])["rule"] == row["rule"]
    verdict = profit_verdict.build_profit_verdict(cfg)
    for row in profit_verdict.VERDICT_GATE_DEFINITIONS:
        assert verdict["gates"][row["id"]]["registered_rule"] == row["rule"]


def test_quote_sheet_and_ips_render_the_same_structured_standing_rules(tmp_path: Path):
    cfg = _config(tmp_path, override={"maker_carry_study": {"min_daily_payout_usd": 1.5}})
    out = cfg.output_root / "maker_carry"
    out.mkdir(parents=True)
    maker_settings = maker_carry_study.maker_policy_settings(cfg)
    maker_carry_study._write_quote_sheet(out, _minimal_quote_summary(), maker_settings)
    payload = render_ips(cfg)

    sheet = (out / "maker_quote_sheet.md").read_text(encoding="utf-8")
    ips = Path(payload["output_path"]).read_text(encoding="utf-8")
    rules = maker_carry_study.quote_sheet_standing_rules(maker_settings)
    for rule in rules:
        assert f"{rule['number']}. {rule['title']}: {rule['text']}" in sheet
        assert rule["text"] in ips
    assert "$1.5/market/day" in sheet
    assert "$1.5/market/day" in ips


def test_hashes_are_stable_and_effective_hash_changes_with_config_override(tmp_path: Path):
    cfg = _config(tmp_path / "base")
    first = render_ips(cfg)
    second = render_ips(cfg)

    assert first["source_defaults_sha256"] == second["source_defaults_sha256"]
    assert first["effective_vps_config_sha256"] == second["effective_vps_config_sha256"]
    assert len(first["source_defaults_sha256"]) == 64
    assert len(first["effective_vps_config_sha256"]) == 64

    changed = _config(tmp_path / "changed", override={"decision_policy": {"composition_stable_days": 8}})
    changed_payload = render_ips(changed)
    assert changed_payload["source_defaults_sha256"] == first["source_defaults_sha256"]
    assert changed_payload["effective_vps_config_sha256"] != first["effective_vps_config_sha256"]


def test_risk_and_capacity_annexes_render_current_values(tmp_path: Path):
    cfg = _config(tmp_path)
    write_json(
        cfg.output_root / "polymarket_portfolio" / "risk_state.json",
        {
            "portfolio_risk": {
                "open_positions": 3,
                "marked_positions": 3,
                "unmarked_positions": 0,
                "total_cost_usdc": 18.0,
                "var_95_usdc": 3.42,
                "cvar_95_usdc": 3.6,
                "var_95_return": 0.19,
                "cvar_95_return": 0.20,
                "worst_position_return_pct": -20.0,
                "mark_source": "latest executable marks",
                "exposure_by_correlation_key": [{"key": "fed-july", "cost_basis_usdc": 10.0}],
                "exposure_by_category": [{"key": "macro_rates", "cost_basis_usdc": 10.0}],
            }
        },
    )
    write_json(
        cfg.output_root / "maker_carry" / "maker_carry_study.json",
        {
            "status": "ok",
            "generated_at_utc": "2026-07-11T12:00:00Z",
            "portfolio_capital_usd": 500.0,
            "portfolio_net_carry_usd_per_day": 4.0,
            "portfolio_net_carry_usd_per_month": 120.0,
            "capital_for_100_per_month": 500.0,
            "capital_curve": [
                {"capital_cap_usd": 250.0, "capital_used_usd": 250.0, "net_usd_per_day": 2.0},
                {"capital_cap_usd": 500.0, "capital_used_usd": 500.0, "net_usd_per_day": 4.0},
            ],
        },
    )

    payload = render_ips(cfg)
    markdown = Path(payload["output_path"]).read_text(encoding="utf-8")

    assert payload["risk_annex"]["status"] == "ok"
    assert payload["risk_annex"]["var_95_usdc"] == 3.42
    assert payload["risk_annex"]["exposure_by_correlation_key"][0]["key"] == "fed-july"
    assert payload["capacity_annex"]["capital_for_100_per_month"] == 500.0
    assert "fed-july" in markdown
    assert "macro_rates" in markdown
    assert "3.42" in markdown
    assert "modeled_upper_bound_not_gate_input" in markdown


def test_missing_annex_inputs_and_disabled_mode_fail_soft(tmp_path: Path):
    cfg = _config(tmp_path / "missing")
    payload = render_ips(cfg)
    markdown = Path(payload["output_path"]).read_text(encoding="utf-8")

    assert payload["risk_annex"]["status"] == "unavailable"
    assert payload["capacity_annex"]["status"] == "unavailable"
    assert "No correlation concentration rows available" in markdown
    assert payload["paper_trading_invoked"] is False
    assert payload["live_trading_invoked"] is False

    disabled_cfg = _config(tmp_path / "disabled", enabled=False)
    disabled = render_ips(disabled_cfg)
    assert disabled["status"] == "disabled"
    summary = read_json(disabled_cfg.output_root / "performance" / "investment_policy_statement_summary.json")
    assert summary["status"] == "disabled"


def test_cli_exposes_ips_renderer():
    assert "render-ips" in COMMANDS
