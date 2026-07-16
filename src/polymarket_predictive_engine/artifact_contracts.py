"""WO-79 producer/consumer contracts for registered runtime interfaces.

The registry is deliberately small.  It declares the fields, freshness clock,
and coverage obligation for interfaces that have already failed in production.
It is reporting/test infrastructure only and cannot alter a gate or trade.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .config import EngineConfig, load_config
from .utils import now_utc, safe_float, write_json


WORK_ORDER = "WO-79"
OUTPUT_FILE = "ops_scheduler/producer_consumer_contracts.json"

CONTRACT_REGISTRY: tuple[dict[str, Any], ...] = (
    {
        "id": "quote_sheet_to_requote_alerts",
        "producer": {
            "component": "maker_carry_study",
            "artifact": "maker_carry/maker_carry_study.json",
            "record_path": "portfolio[]",
            "declared_fields": [
                "condition_id",
                "market_url",
                "outcome",
                "token_id",
                "order_price_min_tick_size",
                "quote_bid_price",
                "quote_ask_price",
            ],
            "freshness": {"field": "generated_at_utc", "maximum_age_seconds": 93600},
            "coverage": "every portfolio ticket, including markets absent from the websocket subscription set",
        },
        "consumer": {
            "component": "requote_alerts",
            "required_fields": [
                "condition_id",
                "market_url",
                "outcome",
                "token_id",
                "order_price_min_tick_size",
                "quote_bid_price",
                "quote_ask_price",
            ],
            "coverage": "one evaluated requote row per portfolio condition_id",
            "current_book_fallback": "websocket bid/ask OR bounded public CLOB REST book",
        },
    },
    {
        "id": "scheduler_jobs_to_status_alerting",
        "producer": {
            "component": "vps_ops_scheduler",
            "artifact": "ops_scheduler/status.json",
            "record_path": "jobs{job_name}",
            "declared_fields": ["job_name", "last_run_utc", "last_exit_code", "detail"],
            "freshness": {"field": "generated_at_utc", "maximum_age_seconds": 300},
            "coverage": "every job stamped by the scheduler",
        },
        "consumer": {
            "component": "degraded_state_watchdog",
            "required_fields": ["job_name", "last_run_utc", "last_exit_code"],
            "coverage": "every stamped scheduler job is inspected; every non-zero exit alerts immediately",
        },
    },
    {
        "id": "reconciliation_legs_to_nav",
        "producer": {
            "component": "wallet_reconciliation",
            "artifact": "performance/wallet_reconciliation.json",
            "record_path": "internal|data_api|onchain",
            "declared_fields": ["leg_name", "status", "nav_usd"],
            "freshness": {"field": "generated_at_utc", "maximum_age_seconds": 93600},
            "coverage": "exactly the internal, Data API, and on-chain legs on every enabled reconciliation",
        },
        "consumer": {
            "component": "reconciled_nav",
            "required_fields": ["leg_name", "status", "nav_usd"],
            "output_fields": [
                "internal_nav_usd",
                "data_api_nav_usd",
                "onchain_nav_usd",
                "reconciliation_status",
            ],
            "coverage": "all three legs are represented before NAV status is reported",
        },
    },
    {
        "id": "maker_replay_collection_to_fill_replay",
        "producer": {
            "component": "collect_maker_replay_data",
            "artifact": "maker_carry/maker_replay_collection.json",
            "record_path": "market_windows[]",
            "declared_fields": [
                "condition_id",
                "asset_id",
                "collected_at_utc",
                "book_poll_status",
                "trade_poll_status",
                "covered",
            ],
            "freshness": {"field": "generated_at_utc", "maximum_age_seconds": 1800},
            "coverage": "exactly one matched collection result per current quote-sheet condition on each run",
        },
        "consumer": {
            "component": "maker_fill_replay",
            "required_fields": [
                "condition_id",
                "asset_id",
                "collected_at_utc",
                "book_poll_status",
                "trade_poll_status",
                "covered",
            ],
            "coverage": "every current quote-sheet condition is represented and failed polls remain explicit",
        },
    },
    {
        "id": "executor_runtime_to_ops_status",
        "producer": {
            "component": "future_executor",
            "artifact": "execution/execution_ledger.csv + execution/executor_heartbeat.json",
            "record_path": "execution_ledger[] + heartbeat{}",
            "declared_fields": [
                "recorded_at_utc",
                "mode",
                "action_type",
                "open_orders_after",
                "exposure_usd_after",
                "heartbeat_at_utc",
                "cycle_id",
                "executor_build_id",
            ],
            "freshness": {"field": "heartbeat_at_utc", "maximum_age_seconds": 600},
            "coverage": "every ledger-enabled executor cycle emits a heartbeat and every action appends one ledger row",
        },
        "consumer": {
            "component": "executor_ops_monitor",
            "required_fields": [
                "recorded_at_utc",
                "mode",
                "action_type",
                "open_orders_after",
                "exposure_usd_after",
                "heartbeat_at_utc",
                "cycle_id",
                "executor_build_id",
            ],
            "coverage": "every ledger-enabled runtime receives a status evaluation; no ledger renders ABSENT",
        },
    },
    {
        "id": "resolution_corpus_to_leakage_safe_training",
        "producer": {
            "component": (
                "resolution_collector + historical_backfill + "
                "websocket_resolution_collector"
            ),
            "artifact": "polymarket_training/resolution_corpus_v1.csv",
            "record_path": "rows[]",
            "declared_fields": [
                "resolution_observation_id",
                "resolution_observed_at_utc",
                "condition_id",
                "token_id",
                "close_time",
                "resolution_time",
                "resolution_quality",
                "target",
            ],
            "freshness": {"field": "resolution_observed_at_utc", "maximum_age_seconds": 172800},
            "coverage": "every distinct Gamma token-level resolution state seen by any of the three producers is appended; conflicting clean states remain visible",
        },
        "consumer": {
            "component": "leakage_safe_training",
            "required_fields": [
                "resolution_observation_id",
                "resolution_observed_at_utc",
                "condition_id",
                "token_id",
                "close_time",
                "resolution_time",
                "resolution_quality",
                "target",
            ],
            "coverage": "only clean, closed, non-conflicting token labels observed by the run clock may join; missing coverage remains excluded",
        },
    },
    {
        "id": "websocket_quotes_to_leakage_safe_training",
        "producer": {
            "component": "websocket_normaliser + historical_bid_ask",
            "artifact": "polymarket_training/historical_bid_ask_v1.csv",
            "record_path": "rows[]",
            "declared_fields": [
                "quote_observation_id",
                "observed_at_utc",
                "market_id",
                "token_id",
                "best_bid",
                "best_ask",
                "top_bid_size",
                "top_ask_size",
            ],
            "freshness": {"field": "observed_at_utc", "maximum_age_seconds": 345600},
            "coverage": "every changed live/archive producer file is streamed; every valid two-sided quote is appended exactly once",
        },
        "consumer": {
            "component": "leakage_safe_training",
            "required_fields": [
                "quote_observation_id",
                "observed_at_utc",
                "market_id",
                "token_id",
                "best_bid",
                "best_ask",
            ],
            "coverage": "midpoint-only, one-sided, crossed, future, post-close, unmatched, and out-of-window rows remain excluded",
        },
    },
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _field_missing(record: Mapping[str, Any], field: str) -> bool:
    if field not in record:
        return True
    value = record.get(field)
    return value is None or (isinstance(value, str) and not value.strip())


def _contract(identifier: str) -> dict[str, Any]:
    for row in CONTRACT_REGISTRY:
        if row["id"] == identifier:
            return row
    raise KeyError(f"unknown producer/consumer contract: {identifier}")


def validate_contract_declarations() -> list[dict[str, Any]]:
    """Return declaration defects; an empty list is the PR-gate condition."""
    defects: list[dict[str, Any]] = []
    seen: set[str] = set()
    for contract in CONTRACT_REGISTRY:
        identifier = str(contract.get("id") or "")
        if not identifier or identifier in seen:
            defects.append({"contract_id": identifier or "missing", "reason": "missing_or_duplicate_id"})
        seen.add(identifier)
        producer = _mapping(contract.get("producer"))
        consumer = _mapping(contract.get("consumer"))
        declared = set(producer.get("declared_fields") or [])
        required = set(consumer.get("required_fields") or [])
        missing = sorted(required - declared)
        if missing:
            defects.append(
                {
                    "contract_id": identifier,
                    "reason": "consumer_requirement_not_declared_by_producer",
                    "fields": missing,
                }
            )
        freshness = _mapping(producer.get("freshness"))
        if not freshness.get("field") or safe_float(freshness.get("maximum_age_seconds")) is None:
            defects.append({"contract_id": identifier, "reason": "freshness_contract_missing"})
        if not str(producer.get("coverage") or "").strip() or not str(consumer.get("coverage") or "").strip():
            defects.append({"contract_id": identifier, "reason": "coverage_contract_missing"})
    return defects


def validate_contract_fixture(identifier: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one synthetic or runtime payload against its registered shape."""
    contract = _contract(identifier)
    defects: list[dict[str, Any]] = []
    if identifier == "quote_sheet_to_requote_alerts":
        rows = payload.get("portfolio") if isinstance(payload.get("portfolio"), list) else []
        required = _mapping(contract.get("consumer")).get("required_fields") or []
        for index, raw in enumerate(rows):
            row = _mapping(raw)
            missing = [field for field in required if _field_missing(row, str(field))]
            bid = safe_float(row.get("quote_bid_price"))
            ask = safe_float(row.get("quote_ask_price"))
            tick = safe_float(row.get("order_price_min_tick_size"))
            if bid is not None and ask is not None and not 0 < bid < ask < 1:
                missing.append("valid_quote_bid_lt_quote_ask")
            if tick is not None and not 0 < tick < 1:
                missing.append("valid_order_price_min_tick_size")
            if missing:
                defects.append({"record": index, "condition_id": row.get("condition_id"), "missing_or_invalid": sorted(set(missing))})
    elif identifier == "scheduler_jobs_to_status_alerting":
        jobs = payload.get("jobs") if isinstance(payload.get("jobs"), Mapping) else {}
        for job_name, raw in jobs.items():
            row = _mapping(raw)
            missing = [field for field in ("last_run_utc", "last_exit_code") if _field_missing(row, field)]
            if not str(job_name).strip():
                missing.append("job_name")
            if missing:
                defects.append({"record": str(job_name), "missing_or_invalid": sorted(set(missing))})
    elif identifier == "reconciliation_legs_to_nav":
        top_fields = _mapping(contract.get("consumer")).get("output_fields") or []
        for field in top_fields:
            if field not in payload:
                defects.append({"record": "reconciled_nav", "missing_or_invalid": [field]})
        top_name = {"internal": "internal_nav_usd", "data_api": "data_api_nav_usd", "onchain": "onchain_nav_usd"}
        for leg, top_field in top_name.items():
            row = _mapping(payload.get(leg))
            missing = [field for field in ("status", "nav_usd") if field not in row]
            if not row:
                missing.append("leg_name")
            nested_nav = safe_float(row.get("nav_usd"))
            top_nav = safe_float(payload.get(top_field))
            if nested_nav is not None and top_nav != nested_nav:
                missing.append(f"{top_field}_matches_{leg}.nav_usd")
            if missing:
                defects.append({"record": leg, "missing_or_invalid": sorted(set(missing))})
    elif identifier == "maker_replay_collection_to_fill_replay":
        portfolio = payload.get("portfolio") if isinstance(payload.get("portfolio"), list) else []
        windows = payload.get("market_windows") if isinstance(payload.get("market_windows"), list) else []
        expected = {
            str(row.get("condition_id") or "")
            for raw in portfolio
            if isinstance(raw, Mapping)
            for row in [_mapping(raw)]
            if str(row.get("condition_id") or "")
        }
        required = _mapping(contract.get("consumer")).get("required_fields") or []
        observed: list[str] = []
        for index, raw in enumerate(windows):
            row = _mapping(raw)
            condition_id = str(row.get("condition_id") or "")
            observed.append(condition_id)
            missing = [field for field in required if _field_missing(row, str(field))]
            expected_covered = row.get("book_poll_status") == "ok" and row.get("trade_poll_status") == "ok"
            if row.get("covered") is not expected_covered:
                missing.append("covered_matches_poll_statuses")
            if missing:
                defects.append(
                    {
                        "record": f"market_windows[{index}]",
                        "condition_id": condition_id,
                        "missing_or_invalid": sorted(set(missing)),
                    }
                )
        observed_set = {condition_id for condition_id in observed if condition_id}
        if observed_set != expected or len(observed) != len(expected):
            defects.append(
                {
                    "record": "market_windows",
                    "missing_or_invalid": ["exact_current_portfolio_coverage"],
                    "missing_conditions": sorted(expected - observed_set),
                    "extra_conditions": sorted(observed_set - expected),
                }
            )
    elif identifier == "executor_runtime_to_ops_status":
        ledger = payload.get("execution_ledger") if isinstance(payload.get("execution_ledger"), list) else []
        heartbeat = _mapping(payload.get("heartbeat"))
        ledger_fields = (
            "recorded_at_utc",
            "mode",
            "action_type",
            "open_orders_after",
            "exposure_usd_after",
        )
        heartbeat_fields = ("heartbeat_at_utc", "mode", "cycle_id", "executor_build_id")
        if not ledger:
            defects.append({"record": "execution_ledger", "missing_or_invalid": ["at_least_one_action_row"]})
        for index, raw in enumerate(ledger):
            row = _mapping(raw)
            missing = [field for field in ledger_fields if _field_missing(row, field)]
            if str(row.get("mode") or "").lower() not in {"replay", "canary", "portfolio"}:
                missing.append("registered_mode")
            if missing:
                defects.append({"record": f"execution_ledger[{index}]", "missing_or_invalid": sorted(set(missing))})
        heartbeat_missing = [field for field in heartbeat_fields if _field_missing(heartbeat, field)]
        if heartbeat_missing:
            defects.append({"record": "heartbeat", "missing_or_invalid": sorted(set(heartbeat_missing))})
        ledger_mode = str(_mapping(ledger[-1] if ledger else {}).get("mode") or "").lower()
        heartbeat_mode = str(heartbeat.get("mode") or "").lower()
        if ledger_mode and heartbeat_mode and ledger_mode != heartbeat_mode:
            defects.append({"record": "heartbeat", "missing_or_invalid": ["mode_matches_latest_ledger"]})
    return {
        "contract_id": identifier,
        "status": "PASS" if not defects else "FAIL",
        "defects": defects,
    }


def build_contract_registry(cfg: EngineConfig) -> dict[str, Any]:
    defects = validate_contract_declarations()
    payload = {
        "work_order": WORK_ORDER,
        "generated_at_utc": now_utc(),
        "status": "PASS" if not defects else "FAIL",
        "contracts": deepcopy(list(CONTRACT_REGISTRY)),
        "declaration_defects": defects,
        "scope": [row["id"] for row in CONTRACT_REGISTRY],
        "decision_use": "schema/freshness/coverage audit and PR-gate tests only",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    write_json(cfg.output_root / OUTPUT_FILE, payload)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return build_contract_registry(load_config(config_path))
