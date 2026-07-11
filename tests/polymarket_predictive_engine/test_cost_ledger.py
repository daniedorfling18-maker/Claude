"""WO-63 true-net investor cost-ledger tests."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from polymarket_predictive_engine import cost_ledger
from polymarket_predictive_engine.cli import COMMANDS, main as cli_main
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.cost_ledger import add_manual_cost, aggregate_costs, sync_cost_ledger
from polymarket_predictive_engine.performance_factsheet import build_performance_factsheet
from polymarket_predictive_engine.utils import read_csv_rows, read_json, write_csv


TX_HASH = "0x" + "3" * 64


class _Response:
    def __init__(self, payload: Any, *, status: int = 200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _config(tmp_path: Path):
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    raw["performance_factsheet"].update(
        {
            "min_daily_observations": 2,
            "bootstrap_iterations": 50,
            "bootstrap_seed": 7,
        }
    )
    raw["cost_ledger"].update({"enabled": True, "request_pause_seconds": 0.1})
    raw["maker_live_test"]["wallet_address"] = "0x" + "a" * 40
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def test_manual_entries_are_idempotent_append_only_and_aggregate_by_window(tmp_path: Path):
    cfg = _config(tmp_path)
    ledger = cfg.output_root / "performance" / "cost_ledger.csv"

    first = add_manual_cost(
        cfg,
        category="rail",
        usd=2.50,
        cost_ref="rail:funding-001",
        note="VALR to Polygon funding rail",
        cost_date="2026-07-10",
    )
    first_bytes = ledger.read_bytes()
    duplicate = add_manual_cost(
        cfg,
        category="rail",
        usd=99.0,
        cost_ref="RAIL:FUNDING-001",
        note="must not replace the original row",
        cost_date="2026-07-10",
    )
    assert ledger.read_bytes() == first_bytes

    second = add_manual_cost(
        cfg,
        category="subscription",
        usd=4.0,
        cost_ref="subscription:2026-07",
        note="data subscription",
        cost_date="2026-07-11",
    )
    final_bytes = ledger.read_bytes()

    assert first["new_rows"] == 1
    assert duplicate["idempotent_duplicate"] is True
    assert second["new_rows"] == 1
    assert final_bytes.startswith(first_bytes)
    assert len(final_bytes) > len(first_bytes)
    day_one = aggregate_costs(cfg, start_date="2026-07-10", end_date="2026-07-10")
    all_time = aggregate_costs(cfg)
    assert day_one["total_usd"] == 2.5
    assert day_one["by_category_usd"]["rail"] == 2.5
    assert all_time["total_usd"] == 6.5
    assert all_time["row_count"] == 2
    assert first["paper_trading_invoked"] is False
    assert first["live_trading_invoked"] is False


def test_gas_sync_books_only_investor_paid_rows_once(tmp_path: Path, monkeypatch):
    cfg = _config(tmp_path)
    gas_path = cfg.output_root / "performance" / "wallet_gas_observations.csv"
    write_csv(
        gas_path,
        [
            {
                "transaction_hash": TX_HASH,
                "activity_timestamp": 1783728000,
                "payer_address": "0x" + "a" * 40,
                "attributed_to_wallet": True,
                "gas_cost_pol": 0.00063,
            },
            {
                "transaction_hash": "0x" + "4" * 64,
                "activity_timestamp": 1783728000,
                "payer_address": "0x" + "b" * 40,
                "attributed_to_wallet": False,
                "gas_cost_pol": 2.0,
            },
        ],
    )
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, params, timeout))
        return _Response(
            {
                "polygon-ecosystem-token": {
                    "usd": 0.08,
                    "last_updated_at": 1783776528,
                }
            }
        )

    monkeypatch.setattr(cost_ledger.requests, "get", fake_get)
    monkeypatch.setattr(cost_ledger.time, "sleep", lambda _seconds: None)

    first = sync_cost_ledger(cfg)
    second = sync_cost_ledger(cfg)
    rows = read_csv_rows(cfg.output_root / "performance" / "cost_ledger.csv")

    assert first["new_rows"] == 1
    assert first["relayer_paid_observations_excluded"] == 1
    assert first["pol_usd_source"]["name"] == "CoinGecko simple-price"
    assert second["new_rows"] == 0
    assert second["pol_usd_source"]["status"] == "not_requested_no_pending_gas"
    assert len(calls) == 1
    assert len(rows) == 1
    assert rows[0]["category"] == "gas"
    assert rows[0]["cost_ref"] == f"gas:{TX_HASH}"
    assert float(rows[0]["usd"]) == pytest.approx(0.0000504)
    assert "investor-paid" in rows[0]["note"]


def test_pol_price_failure_is_fail_soft_with_manual_queue(tmp_path: Path, monkeypatch):
    cfg = _config(tmp_path)
    write_csv(
        cfg.output_root / "performance" / "wallet_gas_observations.csv",
        [
            {
                "transaction_hash": TX_HASH,
                "activity_timestamp": "2026-07-11T00:00:00Z",
                "attributed_to_wallet": True,
                "gas_cost_pol": 0.25,
            }
        ],
    )
    monkeypatch.setattr(
        cost_ledger.requests,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("CoinGecko unavailable")),
    )
    monkeypatch.setattr(cost_ledger.time, "sleep", lambda _seconds: None)

    result = sync_cost_ledger(cfg)

    assert result["status"] == "partial_manual_required"
    assert result["new_rows"] == 0
    assert result["manual_gas_entries_required"][0]["transaction_hash"] == TX_HASH
    assert result["paper_trading_invoked"] is False
    assert read_csv_rows(cfg.output_root / "performance" / "cost_ledger.csv") == []


def test_factsheet_reports_live_net_net_and_simulated_registered_drag(tmp_path: Path):
    cfg = _config(tmp_path)
    write_csv(
        cfg.output_root / "maker_carry" / "maker_live_test_history.csv",
        [
            {"generated_at_utc": "2026-07-10T08:00:00Z", "net_score_usd": 2.0, "capital_usd": 100.0},
            {"generated_at_utc": "2026-07-11T08:00:00Z", "net_score_usd": 5.0, "capital_usd": 100.0},
        ],
    )
    write_csv(
        cfg.output_root / "polymarket_portfolio" / "portfolio_snapshots.csv",
        [
            {"created_at": "2026-07-10T08:00:00Z", "equity_usdc": 100.0},
            {"created_at": "2026-07-11T08:00:00Z", "equity_usdc": 105.0},
        ],
    )
    write_csv(
        cfg.output_root / "polymarket_portfolio" / "paper_fills.csv",
        [{"created_at": "2026-07-10T09:00:00Z", "side": "BUY", "gross_notional_usdc": 20.0}],
    )
    add_manual_cost(
        cfg,
        category="rail",
        usd=1.0,
        cost_ref="rail:test",
        note="funding",
        cost_date="2026-07-10",
    )
    add_manual_cost(
        cfg,
        category="subscription",
        usd=0.5,
        cost_ref="subscription:test",
        note="data",
        cost_date="2026-07-11",
    )

    result = build_performance_factsheet(cfg)
    live = next(row for row in result["series"] if row["series_id"] == "maker_live_test")
    paper = next(row for row in result["series"] if row["series_id"] == "paper_portfolio")
    markdown = (cfg.output_root / "performance" / "performance_factsheet.md").read_text(encoding="utf-8")

    assert live["gross_cumulative_pnl_usd"] == 5.0
    assert live["booked_costs_usd"] == 1.5
    assert live["net_net_cumulative_pnl_usd"] == 3.5
    assert live["net_of_all_costs"]["by_category_usd"]["rail"] == 1.0
    assert paper["hypothetical_cost_drag"]["turnover_usd"] == 20.0
    assert paper["hypothetical_cost_drag"]["registered_ceiling_per_dollar"] == pytest.approx(0.06)
    assert paper["hypothetical_cost_drag"]["estimated_drag_usd"] == pytest.approx(1.2)
    assert paper["hypothetical_cost_drag"]["booked_to_cost_ledger"] is False
    assert "Net of all costs: gross $5.00 - booked costs $1.50 = **net-net $3.50**" in markdown
    assert "Hypothetical cost drag" in markdown
    assert result["paper_trading_invoked"] is False
    assert result["live_trading_invoked"] is False


def test_add_cost_cli_is_registered_and_writes_a_summary(tmp_path: Path, capsys):
    cfg = _config(tmp_path)

    code = cli_main(
        [
            "add-cost",
            "--config",
            str(cfg.path),
            "--cost-category",
            "other",
            "--cost-usd",
            "1.25",
            "--cost-ref",
            "other:cli-test",
            "--cost-date",
            "2026-07-11",
            "--cost-note",
            "CLI audit entry",
        ]
    )
    output = capsys.readouterr().out

    assert code == 0
    assert "add-cost" in COMMANDS
    assert "sync-cost-ledger" in COMMANDS
    assert '"status": "ok"' in output
    assert read_json(cfg.output_root / "performance" / "cost_ledger_summary.json")["new_rows"] == 1
