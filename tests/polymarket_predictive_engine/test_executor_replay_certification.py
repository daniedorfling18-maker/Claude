from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path

import pytest
import yaml

from polymarket_predictive_engine.cli import COMMANDS, build_parser
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.executor_replay_certification import (
    REQUIRED_SYNTHETIC_KINDS,
    build_replay_scenario_corpus,
    certify_executor_replay,
    write_reference_stub_log,
)
from polymarket_predictive_engine.utils import read_json, write_csv, write_json


ROOT = Path(__file__).resolve().parents[2]


def _config(tmp_path: Path):
    raw = yaml.safe_load((ROOT / "polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def _seed_official_window(cfg) -> None:
    path = cfg.output_root / "maker_carry" / "official_books" / "condition-1.csv.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "condition_id",
        "asset_id",
        "source_timestamp",
        "hash",
        "best_bid",
        "best_ask",
        "midpoint",
        "top_bid_size",
        "top_ask_size",
    ]
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(
            [
                {
                    "condition_id": "condition-1",
                    "asset_id": "token-1",
                    "source_timestamp": 1000,
                    "hash": "public-book-hash-1",
                    "best_bid": 0.48,
                    "best_ask": 0.52,
                    "midpoint": 0.50,
                    "top_bid_size": 50,
                    "top_ask_size": 50,
                },
                {
                    "condition_id": "condition-1",
                    "asset_id": "token-1",
                    "source_timestamp": 1300,
                    "hash": "public-book-hash-2",
                    "best_bid": 0.49,
                    "best_ask": 0.51,
                    "midpoint": 0.50,
                    "top_bid_size": 50,
                    "top_ask_size": 50,
                },
            ]
        )


def _prepared_stub(tmp_path: Path):
    cfg = _config(tmp_path)
    _seed_official_window(cfg)
    corpus = build_replay_scenario_corpus(cfg)
    decision_path, ledger_path = write_reference_stub_log(cfg, corpus)
    return cfg, corpus, decision_path, ledger_path


def test_reference_stub_passes_every_registered_contract_without_authorising_canary(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    _seed_official_window(cfg)

    result = certify_executor_replay(cfg, generate_reference_stub=True)

    assert result["status"] == "PASS"
    assert result["candidate_kind"] == "reference_stub"
    assert result["reference_stub_used"] is True
    assert result["official_book_scenario_count"] == 1
    assert result["synthetic_scenario_count"] == len(REQUIRED_SYNTHETIC_KINDS)
    assert result["scenario_count"] == 1 + len(REQUIRED_SYNTHETIC_KINDS)
    assert all(row["status"] == "PASS" for row in result["contract_checks"].values())
    assert result["blocks_canary_by_certification"] is False
    assert result["canary_authorised_by_this_artifact"] is False
    assert result["credential_loading_invoked"] is False
    assert result["paper_trading_invoked"] is False
    assert result["live_trading_invoked"] is False
    persisted = read_json(cfg.output_root / "execution" / "replay_certification.json")
    assert persisted == result
    corpus = read_json(cfg.output_root / "execution" / "replay_scenario_corpus.json")
    assert {row["kind"] for row in corpus["scenarios"] if row["source"] == "synthetic"} == set(REQUIRED_SYNTHETIC_KINDS)


def test_missing_recorded_official_book_window_fails_closed(tmp_path: Path) -> None:
    cfg = _config(tmp_path)

    result = certify_executor_replay(cfg, generate_reference_stub=True)

    assert result["status"] == "FAIL"
    assert result["blocks_canary_by_certification"] is True
    assert result["contract_checks"]["scenario_coverage"]["status"] == "FAIL"
    assert "no recorded WO-44 official-book window" in json.dumps(result["violations"])


@pytest.mark.parametrize(
    ("mutation", "failed_rule"),
    [
        ("outside_sheet", "quote_sheet_boundary"),
        ("above_cap", "policy_caps"),
        ("not_multiple", "five_share_multiple"),
        ("stop_not_cancelled", "stop_cancellation"),
        ("stale_not_flat", "flat_on_missing_or_stale_input"),
        ("dead_man_missing", "dead_man_flatten"),
        ("ledger_row_missing", "action_ledger_completeness"),
    ],
)
def test_each_executor_contract_defect_fails_certification(tmp_path: Path, mutation: str, failed_rule: str) -> None:
    cfg, _, decision_path, ledger_path = _prepared_stub(tmp_path)
    decisions = read_json(decision_path)
    ledger = list(csv.DictReader(ledger_path.open("r", encoding="utf-8", newline="")))

    if mutation in {"outside_sheet", "above_cap", "not_multiple"}:
        decision = next(row for row in decisions if any(action["action_type"] == "place" for action in row["actions"]))
        action = next(action for action in decision["actions"] if action["action_type"] == "place")
        if mutation == "outside_sheet":
            action["price"] = round(float(action["price"]) + 0.01, 6)
        elif mutation == "above_cap":
            decision["state_after"]["capital_at_risk_usd"] = 1000.0
        else:
            action["shares"] = 6.0
    elif mutation == "stop_not_cancelled":
        decision = next(row for row in decisions if row["scenario_id"] == "synthetic_kill_criteria_day")
        decision["actions"] = []
        decision["state_after"] = decision["state_before"]
    elif mutation == "stale_not_flat":
        decision = next(row for row in decisions if row["scenario_id"] == "synthetic_stale_websocket")
        decision["state_after"] = decision["state_before"]
    elif mutation == "dead_man_missing":
        decision = next(row for row in decisions if row["scenario_id"] == "synthetic_heartbeat_gap")
        decision["dead_man_triggered"] = False
    elif mutation == "ledger_row_missing":
        ledger = ledger[1:]

    write_json(decision_path, decisions)
    write_csv(ledger_path, ledger, fieldnames=list(ledger[0]) if ledger else [
        "candidate_build_id",
        "scenario_id",
        "cycle",
        "action_id",
        "action_type",
        "condition_id",
        "token_id",
        "side",
        "price",
        "shares",
        "recorded_at_utc",
    ])
    result = certify_executor_replay(
        cfg,
        decision_log_path=decision_path,
        execution_ledger_path=ledger_path,
        candidate_build_id="wo74-reference-stub",
    )

    assert result["status"] == "FAIL"
    assert result["contract_checks"][failed_rule]["status"] == "FAIL"
    assert result["blocks_canary_by_certification"] is True


def test_aggregate_place_notional_must_remain_within_stage_cap(tmp_path: Path) -> None:
    cfg, corpus, decision_path, ledger_path = _prepared_stub(tmp_path)
    decisions = read_json(decision_path)
    ledger = list(csv.DictReader(ledger_path.open("r", encoding="utf-8", newline="")))
    decision = next(row for row in decisions if any(action["action_type"] == "place" for action in row["actions"]))
    original = next(action for action in decision["actions"] if action["action_type"] == "place")
    scenario = next(
        row
        for row in corpus["scenarios"]
        if row["scenario_id"] == decision["scenario_id"]
    )
    cycle = next(
        row
        for row in scenario["cycles"]
        if int(row["cycle"]) == int(decision["cycle"])
    )
    cap = float(cycle["stage_cap_usd"])
    notional = float(original["price"]) * float(original["shares"])
    action_count = int(cap // notional) + 1
    decision["actions"] = [
        {**original, "action_id": f"{original['action_id']}-aggregate-{index}"}
        for index in range(action_count)
    ]
    original_ledger = next(row for row in ledger if row["action_id"] == original["action_id"])
    ledger = [row for row in ledger if row["action_id"] != original["action_id"]]
    ledger.extend(
        {**original_ledger, "action_id": action["action_id"]}
        for action in decision["actions"]
    )
    write_json(decision_path, decisions)
    write_csv(ledger_path, ledger, fieldnames=list(ledger[0]))

    result = certify_executor_replay(
        cfg,
        decision_log_path=decision_path,
        execution_ledger_path=ledger_path,
        candidate_build_id="wo74-reference-stub",
    )

    assert result["status"] == "FAIL"
    assert result["contract_checks"]["policy_caps"]["status"] == "FAIL"
    assert any(
        violation["detail"] == "aggregate place notional exceeds stage cap"
        for violation in result["violations"]
    )


def test_cli_and_source_remain_keyless_and_executor_free() -> None:
    assert "executor-replay-certification" in COMMANDS
    parsed = build_parser().parse_args(
        ["executor-replay-certification", "--generate-replay-stub", "--candidate-build-id", "stub-build"]
    )
    assert parsed.generate_replay_stub is True
    assert parsed.candidate_build_id == "stub-build"
    source = (ROOT / "src" / "polymarket_predictive_engine" / "executor_replay_certification.py").read_text(encoding="utf-8")
    assert "import requests" not in source
    assert "POLYMARKET_LIVE_TRADING" not in source
    assert "private_key" not in source
    assert "api_secret" not in source
