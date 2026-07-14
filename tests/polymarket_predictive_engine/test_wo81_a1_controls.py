from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from polymarket_predictive_engine.a1_controls import (
    REGISTERED_RECONCILIATION_MAX_AGE_SECONDS,
    REGISTERED_SWEEP_THRESHOLD_USD,
    build_a1_sweep_advisory,
)
from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.executor_ops_monitor import build_executor_ops_status
from polymarket_predictive_engine.ledger_anchor import DEFAULT_LEDGER_REGISTRY
from polymarket_predictive_engine.utils import read_json, write_json


AS_OF = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)


def _cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "decision_policy": {"stage0_capital_usd": 100.0},
            "a1_balance_discipline": {
                # Attempts to widen the registered controls must be clamped.
                "sweep_threshold_usd": 999.0,
                "reconciliation_max_age_seconds": 999999.0,
            },
        },
        path=tmp_path / "config.yaml",
    )


def _reconciliation(cfg: EngineConfig, *, nav: float, cash: float, state: str = "clean") -> None:
    write_json(
        cfg.output_root / "performance" / "wallet_reconciliation.json",
        {
            "generated_at_utc": "2026-07-14T11:55:00Z",
            "reconciliation_status": state,
            "internal_nav_usd": nav,
            "data_api_nav_usd": nav + 0.25,
            "onchain_nav_usd": nav + 0.1,
            "internal": {"status": "ok", "nav_usd": nav, "cash_usd": cash},
            "data_api": {
                "status": "ok",
                "nav_usd": nav + 0.25,
                "reconstructed_cash_usd": cash + 0.2,
            },
            "onchain": {
                "status": "ok",
                "nav_usd": nav + 0.1,
                "pusd_balance_usd": cash + 0.1,
            },
        },
    )


def test_a1_sweep_advisory_is_tighten_only_human_only_and_anchor_enrolled(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _reconciliation(cfg, nav=112.0, cash=20.0)

    result = build_a1_sweep_advisory(cfg, as_of=AS_OF)

    assert result["status"] == "sweep_advised"
    assert result["active_stage_cap_usd"] == 100.0
    assert result["sweep_threshold_usd"] == REGISTERED_SWEEP_THRESHOLD_USD == 5.0
    assert result["suggested_sweep_usd"] == 12.0
    assert result["advice"] == "sweep $12.00 to VALR manually"
    assert result["notification_eligible"] is True
    assert result["transfer_invoked"] is False
    assert result["withdrawal_invoked"] is False
    assert result["credential_loading_invoked"] is False
    assert result["live_trading_invoked"] is False
    assert read_json(cfg.output_root / "execution" / "a1_sweep_advisory.json") == result
    markdown = (cfg.output_root / "execution" / "a1_sweep_advisory.md").read_text(encoding="utf-8")
    assert "sweep $12.00 to VALR manually" in markdown
    assert "cannot transfer, withdraw" in markdown
    assert {row["glob"] for row in DEFAULT_LEDGER_REGISTRY} >= {"execution/a1_sweep_advisory.json"}


def test_a1_sweep_advisory_fails_closed_on_stale_or_unclean_reconciliation(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _reconciliation(cfg, nav=112.0, cash=20.0, state="DISCREPANCY")

    unclean = build_a1_sweep_advisory(cfg, as_of=AS_OF)
    assert unclean["status"] == "unknown"
    assert unclean["suggested_sweep_usd"] == 0.0

    payload = read_json(cfg.output_root / "performance" / "wallet_reconciliation.json")
    payload["reconciliation_status"] = "clean"
    payload["generated_at_utc"] = "2026-07-12T00:00:00Z"
    write_json(cfg.output_root / "performance" / "wallet_reconciliation.json", payload)
    stale = build_a1_sweep_advisory(cfg, as_of=AS_OF)
    assert stale["status"] == "unknown"
    assert stale["reconciliation_age_seconds"] > REGISTERED_RECONCILIATION_MAX_AGE_SECONDS
    assert stale["withdrawal_invoked"] is False


def test_executor_ops_notification_carries_a1_advice_without_inventing_executor_presence(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _reconciliation(cfg, nav=112.0, cash=20.0)

    result = build_executor_ops_status(cfg, as_of=AS_OF)

    assert result["executor_present"] is False
    assert result["status"] == "A1_ADVISORY"
    assert result["a1_sweep_advisory"]["suggested_sweep_usd"] == 12.0
    assert result["notification"]["notify"] is True
    assert [row["id"] for row in result["owner_alerts"]] == ["a1_sweep_advisory"]
    assert result["order_placement_invoked"] is False
    body = Path(result["body_file"]).read_text(encoding="utf-8")
    assert "sweep $12.00 to VALR manually" in body
