"""WO-62 three-way live-wallet reconciliation tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from polymarket_predictive_engine import maker_carry_study
from polymarket_predictive_engine import wallet_reconciliation as reconciliation
from polymarket_predictive_engine.cli import COMMANDS
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.performance_factsheet import build_performance_factsheet
from polymarket_predictive_engine.utils import read_csv_rows, read_json, write_csv, write_json


WALLET = "0x" + "a" * 40
EXECUTOR_WALLET = "0x" + "b" * 40
PUSD = "0x" + "1" * 40
CTF = "0x" + "2" * 40
ASSET = "42"
TX_HASH = "0x" + "3" * 64


class _Response:
    def __init__(self, payload: Any = None, *, text: str = "", status: int = 200):
        self._payload = payload
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _config(tmp_path: Path, *, wallet: str = WALLET, executor_wallet: str = "", gas_enabled: bool = False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    raw["maker_live_test"]["wallet_address"] = wallet
    raw["maker_live_test"]["executor_wallet_address"] = executor_wallet
    raw["wallet_reconciliation"].update(
        {
            "enabled": True,
            "request_pause_seconds": 0.1,
            "max_pages": 1,
            "activity_start_epoch": 1,
            "baseline_epoch": 1,
            "gas_capture_enabled": gas_enabled,
            "gas_scan_max_transactions": 1,
        }
    )
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def _positions() -> list[dict[str, Any]]:
    return [
        {
            "proxyWallet": WALLET,
            "asset": ASSET,
            "conditionId": "0x" + "4" * 64,
            "size": 100.0,
            "avgPrice": 0.4,
            "currentValue": 40.0,
            "curPrice": 0.4,
            "cashPnl": 0.0,
        }
    ]


def _activities(*, with_tx: bool = False) -> list[dict[str, Any]]:
    rows = [
        {
            "proxyWallet": WALLET,
            "timestamp": 100,
            "type": "DEPOSIT",
            "usdcSize": 100.0,
            "transactionHash": TX_HASH if with_tx else "",
        },
        {
            "proxyWallet": WALLET,
            "timestamp": 200,
            "type": "TRADE",
            "side": "BUY",
            "usdcSize": 40.0,
            "asset": ASSET,
        },
    ]
    return rows


def _install_http(
    monkeypatch,
    *,
    pusd_balance: float = 60.0,
    position_size: float = 100.0,
    rpc_error: bool = False,
    with_tx: bool = False,
    contracts_error: bool = False,
):
    contracts_text = f"Core Trading Contracts\nConditional Tokens (CTF) {CTF}\nCollateral Contracts\npUSD — CollateralToken (proxy) {PUSD}\n"

    def fake_get(url, params=None, timeout=None):
        del timeout
        if "docs.polymarket.com" in url:
            if contracts_error:
                raise RuntimeError("contracts unavailable")
            return _Response(text=contracts_text)
        if url.endswith("/positions"):
            return _Response(_positions())
        if url.endswith("/activity"):
            return _Response(_activities(with_tx=with_tx))
        if url.endswith("/value"):
            return _Response([{"user": WALLET, "value": 40.0}])
        raise AssertionError(f"unexpected GET {url} {params}")

    def fake_post(url, json=None, timeout=None):
        del url, timeout
        if rpc_error:
            raise RuntimeError("RPC unavailable")
        method = json["method"]
        if method == "eth_chainId":
            return _Response({"jsonrpc": "2.0", "id": json["id"], "result": "0x89"})
        if method == "eth_call":
            contract = json["params"][0]["to"].lower()
            raw = round((pusd_balance if contract == PUSD.lower() else position_size) * 10**6)
            return _Response({"jsonrpc": "2.0", "id": json["id"], "result": hex(raw)})
        if method == "eth_getTransactionReceipt":
            return _Response(
                {
                    "jsonrpc": "2.0",
                    "id": json["id"],
                    "result": {"gasUsed": hex(21000), "effectiveGasPrice": hex(30_000_000_000)},
                }
            )
        if method == "eth_getTransactionByHash":
            return _Response(
                {
                    "jsonrpc": "2.0",
                    "id": json["id"],
                    "result": {"from": WALLET, "gasPrice": hex(30_000_000_000)},
                }
            )
        raise AssertionError(f"unexpected RPC method {method}")

    monkeypatch.setattr(reconciliation.requests, "get", fake_get)
    monkeypatch.setattr(reconciliation.requests, "post", fake_post)
    monkeypatch.setattr(reconciliation.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(reconciliation, "now_utc", lambda: "2026-07-11T12:00:00Z")


def _write_internal(cfg, *, cash: float = 60.0, inventory: float = 40.0, adjustment: float = 0.0):
    write_json(
        cfg.output_root / "maker_carry" / "maker_live_test.json",
        {
            "status": "ok",
            "generated_at_utc": "2026-07-11T11:59:00Z",
            "cash_usd": cash,
            "inventory_value_usd": inventory,
            "accrued_rewards_usd": 0.0,
            "reconciliation_explained_adjustment_usd": adjustment,
        },
    )


def test_synthetic_three_way_agreement_is_clean_and_provenance_stamped(tmp_path: Path, monkeypatch):
    cfg = _config(tmp_path)
    _write_internal(cfg)
    _install_http(monkeypatch)

    result = reconciliation.reconcile_wallet(cfg)

    assert result["status"] == "ok"
    assert result["reconciliation_status"] == "clean"
    assert result["internal_nav_usd"] == pytest.approx(100.0)
    assert result["data_api_nav_usd"] == pytest.approx(100.0)
    assert result["onchain_nav_usd"] == pytest.approx(100.0)
    assert result["unexplained_discrepancy_usd"] == 0.0
    assert result["contract_registry"]["status"] == "official_live"
    assert result["contract_registry"]["addresses"] == {"pusd": PUSD, "ctf": CTF}
    assert len(result["contracts_page_sha256"]) == 64
    assert result["onchain"]["rpc_chain_id"] == 137
    assert result["onchain"]["positions"][0]["quantity_delta"] == 0.0
    assert result["paper_trading_invoked"] is False
    assert result["live_trading_invoked"] is False
    history = read_csv_rows(cfg.output_root / "performance" / "wallet_reconciliation_history.csv")
    assert len(history) == 1
    assert history[0]["reconciliation_status"] == "clean"


def test_planted_five_dollar_mismatch_flags_and_renders_red_banners(tmp_path: Path, monkeypatch):
    cfg = _config(tmp_path)
    _write_internal(cfg)
    quote_path = cfg.output_root / "maker_carry" / "maker_quote_sheet.md"
    quote_path.parent.mkdir(parents=True, exist_ok=True)
    quote_path.write_text("# Maker quote sheet\n\nExisting content\n", encoding="utf-8")
    _install_http(monkeypatch, pusd_balance=55.0)

    result = reconciliation.reconcile_wallet(cfg)
    factsheet = build_performance_factsheet(cfg)

    assert result["reconciliation_status"] == "DISCREPANCY"
    assert result["unexplained_discrepancy_usd"] == pytest.approx(5.0)
    assert factsheet["wallet_reconciliation"]["reconciliation_status"] == "DISCREPANCY"
    quote = quote_path.read_text(encoding="utf-8")
    factsheet_md = (cfg.output_root / "performance" / "performance_factsheet.md").read_text(encoding="utf-8")
    assert "🔴 **WALLET RECONCILIATION DISCREPANCY — $5.00" in quote
    assert "🔴 **WALLET RECONCILIATION DISCREPANCY — $5.00" in factsheet_md

    # A later maker-sheet regeneration must keep the same reporting alert.
    maker_carry_study._write_quote_sheet(
        cfg.output_root / "maker_carry",
        {
            "generated_at_utc": "2026-07-11T12:05:00Z",
            "portfolio_net_carry_usd_per_day": 0,
            "portfolio_net_carry_usd_per_month": 0,
            "portfolio_capital_usd": 0,
            "capital_curve": [],
            "capital_for_100_per_month": None,
            "maker_gates": {
                "maker_verdict": "insufficient_evidence",
                "M_A_carry_evidence": {"state": "pending"},
                "M_B_adverse_realism": {"state": "pending"},
            },
            "portfolio": [],
        },
        {"min_daily_payout_usd": 1.0},
    )
    assert "🔴 **WALLET RECONCILIATION DISCREPANCY — $5.00" in quote_path.read_text(encoding="utf-8")


def test_missing_rpc_degrades_to_partial_without_false_discrepancy(tmp_path: Path, monkeypatch):
    cfg = _config(tmp_path)
    _write_internal(cfg)
    _install_http(monkeypatch, rpc_error=True)

    result = reconciliation.reconcile_wallet(cfg)

    assert result["reconciliation_status"] == "partial"
    assert result["onchain"]["status"] == "unavailable"
    assert result["unexplained_discrepancy_usd"] is None
    assert result["data_api_nav_usd"] == 100.0


def test_explicit_adjustment_produces_explained_status(tmp_path: Path, monkeypatch):
    cfg = _config(tmp_path)
    _write_internal(cfg, cash=55.0, adjustment=5.0)
    _install_http(monkeypatch)

    result = reconciliation.reconcile_wallet(cfg)

    assert result["reconciliation_status"] == "explained"
    assert result["max_pairwise_delta_usd"] == 5.0
    assert result["unexplained_discrepancy_usd"] == 0.0


def test_contract_cache_is_only_reused_with_official_source_provenance(tmp_path: Path, monkeypatch):
    cfg = _config(tmp_path)
    _write_internal(cfg)
    _install_http(monkeypatch)
    first = reconciliation.reconcile_wallet(cfg)
    assert first["contract_registry"]["status"] == "official_live"

    _install_http(monkeypatch, contracts_error=True)
    second = reconciliation.reconcile_wallet(cfg)
    assert second["contract_registry"]["status"] == "cached_official"
    assert second["contract_registry"]["addresses"] == {"pusd": PUSD, "ctf": CTF}


def test_gas_hook_records_idempotent_polygon_cost_observation(tmp_path: Path, monkeypatch):
    cfg = _config(tmp_path, gas_enabled=True)
    _write_internal(cfg)
    _install_http(monkeypatch, with_tx=True)

    first = reconciliation.reconcile_wallet(cfg)
    second = reconciliation.reconcile_wallet(cfg)

    assert first["gas_capture_hook"]["new_rows"] == 1
    assert second["gas_capture_hook"]["new_rows"] == 0
    rows = read_csv_rows(cfg.output_root / "performance" / "wallet_gas_observations.csv")
    assert len(rows) == 1
    assert rows[0]["transaction_hash"] == TX_HASH
    assert rows[0]["payer_address"] == WALLET
    assert rows[0]["attributed_to_wallet"] == "True"
    assert float(rows[0]["gas_cost_pol"]) == pytest.approx(0.00063)


def test_empty_wallet_is_inert_and_cli_is_registered(tmp_path: Path, monkeypatch):
    cfg = _config(tmp_path, wallet="")
    monkeypatch.setattr(
        reconciliation.requests,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network must remain inert")),
    )

    result = reconciliation.reconcile_wallet(cfg)

    assert result["status"] == "awaiting_wallet_address"
    assert result["reconciliation_status"] == "partial"
    assert "reconcile-wallet" in COMMANDS
    assert read_json(cfg.output_root / "performance" / "wallet_reconciliation.json")["live_trading_invoked"] is False


def test_two_wallet_reconciliation_reports_roles_separately_without_summing(tmp_path: Path, monkeypatch):
    cfg = _config(tmp_path, executor_wallet=EXECUTOR_WALLET)
    legacy_path = cfg.output_root / "performance" / "wallet_reconciliation_history.csv"
    write_csv(
        legacy_path,
        [
            {
                "generated_at_utc": "2026-07-12T07:54:30Z",
                "snapshot_date": "2026-07-12",
                "wallet_address": WALLET,
                "reconciliation_status": "partial",
            }
        ],
        fieldnames=reconciliation.LEGACY_HISTORY_FIELDS,
    )
    anchored_prefix = legacy_path.read_bytes()
    write_json(
        cfg.output_root / "maker_carry" / "maker_live_test.json",
        {
            "status": "ok",
            "generated_at_utc": "2026-07-11T11:59:00Z",
            "wallets_combined": False,
            "wallets": {
                role: {
                    "status": "ok",
                    "generated_at_utc": "2026-07-11T11:59:00Z",
                    "cash_usd": 60.0,
                    "inventory_value_usd": 40.0,
                    "accrued_rewards_usd": 0.0,
                }
                for role in ("operator", "executor")
            },
        },
    )
    _install_http(monkeypatch)

    result = reconciliation.reconcile_wallet(cfg)

    assert result["status"] == "ok"
    assert result["primary_wallet_role"] == "operator"
    assert result["wallets_combined"] is False
    assert result["internal_nav_usd"] == 100.0
    assert result["wallets"]["operator"]["internal_nav_usd"] == 100.0
    assert result["wallets"]["executor"]["internal_nav_usd"] == 100.0
    assert "combined_nav_usd" not in result
    assert result["wallets"]["operator"]["wallet_address"] == WALLET
    assert result["wallets"]["executor"]["wallet_address"] == EXECUTOR_WALLET
    assert legacy_path.read_bytes().startswith(anchored_prefix)
    assert "wallet_role" not in read_csv_rows(legacy_path)[0]
    history = read_csv_rows(cfg.output_root / "performance" / "wallet_reconciliation_wallet_history.csv")
    assert {row["wallet_role"] for row in history} == {"operator", "executor"}
