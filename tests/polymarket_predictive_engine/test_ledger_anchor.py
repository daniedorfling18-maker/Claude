"""WO-61 tamper-evident ledger anchoring tests."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from polymarket_predictive_engine.cli import COMMANDS
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.ledger_anchor import (
    DEFAULT_LEDGER_REGISTRY,
    anchor_ledgers,
    verify_ledger_chain,
)
from polymarket_predictive_engine.utils import read_csv_rows, read_json


def _config(tmp_path: Path, registry: list[object], *, enabled: bool = True):
    tmp_path.mkdir(parents=True, exist_ok=True)
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    raw["ledger_anchor"] = {
        "enabled": enabled,
        "ledger_globs": registry,
        "external_anchor_branch": "vps-anchor",
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(config_path)


def test_untouched_append_only_ledgers_verify_across_daily_appends(tmp_path: Path):
    cfg = _config(tmp_path, ["audit/core.csv"])
    ledger = cfg.output_root / "audit" / "core.csv"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(b"date,value\n2026-07-11,1\n")

    first = anchor_ledgers(cfg, anchor_date="2026-07-11")
    ledger.write_bytes(ledger.read_bytes() + b"2026-07-12,2\n")
    second = anchor_ledgers(cfg, anchor_date="2026-07-12")
    verification = verify_ledger_chain(cfg)

    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert verification["status"] == "ok"
    assert verification["links_checked"] == 2
    assert verification["ledger_prefixes_checked"] == 2
    rows = read_csv_rows(cfg.output_root / "performance" / "ledger_anchor_chain.csv")
    assert len(rows) == 2
    assert rows[1]["previous_chain_head"] == rows[0]["chain_head"]
    assert int(rows[0]["ledger_count"]) == 1
    assert json.loads(rows[0]["ledger_manifest_json"])[0]["byte_length"] < json.loads(rows[1]["ledger_manifest_json"])[0]["byte_length"]
    assert read_json(cfg.output_root / "performance" / "ledger_anchor_head.json")["chain_head"] == rows[1]["chain_head"]
    assert first["paper_trading_invoked"] is False
    assert first["live_trading_invoked"] is False


def test_single_byte_retroactive_edit_reports_correct_first_broken_date(tmp_path: Path):
    cfg = _config(tmp_path, ["audit/core.csv"])
    ledger = cfg.output_root / "audit" / "core.csv"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(b"alpha\n")
    anchor_ledgers(cfg, anchor_date="2026-07-10")
    ledger.write_bytes(ledger.read_bytes() + b"beta\n")
    anchor_ledgers(cfg, anchor_date="2026-07-11")

    changed = bytearray(ledger.read_bytes())
    changed[0] = ord("A")
    ledger.write_bytes(changed)
    verification = verify_ledger_chain(cfg)

    assert verification["status"] == "broken"
    assert verification["first_broken_date"] == "2026-07-10"
    assert "anchored prefix digest changed" in verification["issues"][0]


def test_missing_registered_file_is_tolerated_and_recorded(tmp_path: Path):
    cfg = _config(tmp_path, ["audit/not-created.csv"])

    result = anchor_ledgers(cfg, anchor_date="2026-07-11")
    verification = verify_ledger_chain(cfg)
    row = read_csv_rows(cfg.output_root / "performance" / "ledger_anchor_chain.csv")[0]
    manifest = json.loads(row["ledger_manifest_json"])

    assert result["status"] == "ok"
    assert result["missing_count"] == 1
    assert manifest[0]["status"] == "missing_at_anchor"
    assert verification["status"] == "ok"
    assert verification["missing_at_anchor_tolerated"] == 1


def test_mutable_source_uses_immutable_daily_snapshots(tmp_path: Path):
    cfg = _config(tmp_path, [{"glob": "audit/policy.json", "mode": "snapshot"}])
    source = cfg.output_root / "audit" / "policy.json"
    source.parent.mkdir(parents=True)
    source.write_text('{"decision":"wait"}\n', encoding="utf-8")
    anchor_ledgers(cfg, anchor_date="2026-07-10")
    source.write_text('{"decision":"proceed"}\n', encoding="utf-8")
    anchor_ledgers(cfg, anchor_date="2026-07-11")

    assert verify_ledger_chain(cfg)["status"] == "ok"
    first_snapshot = cfg.output_root / "performance" / "ledger_anchor_snapshots" / "2026-07-10" / "audit" / "policy.json"
    assert read_json(first_snapshot)["decision"] == "wait"
    first_snapshot.write_text('{"decision":"tampered"}\n', encoding="utf-8")
    verification = verify_ledger_chain(cfg)
    assert verification["status"] == "broken"
    assert verification["first_broken_date"] == "2026-07-10"


def test_as_of_verification_ignores_bytes_anchored_only_later(tmp_path: Path):
    cfg = _config(tmp_path, ["audit/core.csv"])
    ledger = cfg.output_root / "audit" / "core.csv"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(b"alpha\n")
    anchor_ledgers(cfg, anchor_date="2026-07-10")
    ledger.write_bytes(b"alpha\nbeta\n")
    anchor_ledgers(cfg, anchor_date="2026-07-11")

    changed = bytearray(ledger.read_bytes())
    changed[len(b"alpha\n")] = ord("B")
    ledger.write_bytes(changed)

    assert verify_ledger_chain(cfg, as_of_date="2026-07-10")["status"] == "ok"
    full = verify_ledger_chain(cfg)
    assert full["status"] == "broken"
    assert full["first_broken_date"] == "2026-07-11"


def test_same_day_anchor_is_idempotent_and_disabled_mode_always_summarises(tmp_path: Path):
    cfg = _config(tmp_path, ["audit/core.csv"])
    ledger = cfg.output_root / "audit" / "core.csv"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(b"one\n")

    anchor_ledgers(cfg, anchor_date="2026-07-11")
    duplicate = anchor_ledgers(cfg, anchor_date="2026-07-11")
    assert duplicate["status"] == "already_anchored"
    assert len(read_csv_rows(cfg.output_root / "performance" / "ledger_anchor_chain.csv")) == 1

    disabled = _config(tmp_path / "disabled", ["audit/core.csv"], enabled=False)
    result = anchor_ledgers(disabled, anchor_date="2026-07-11")
    assert result["status"] == "disabled"
    assert read_json(disabled.output_root / "performance" / "ledger_anchor_summary.json")["status"] == "disabled"


def test_cli_and_external_anchor_script_preserve_append_only_history():
    assert "anchor-ledgers" in COMMANDS
    assert "verify-ledger-chain" in COMMANDS
    script = Path("scripts/push_vps_anchor.sh").read_text(encoding="utf-8")
    assert 'commit-tree -p "$PARENT"' in script
    assert 'push -q origin "$COMMIT:refs/heads/$BRANCH"' in script
    assert 'push -q origin "+$COMMIT:refs/heads/$BRANCH"' not in script
    telemetry = Path("scripts/push_vps_telemetry.sh").read_text(encoding="utf-8")
    assert 'VPS_ANCHOR_REPO_DIR="$REPO_DIR" sh "$ANCHOR_SCRIPT"' in telemetry


def test_exact_h2_ledgers_are_enrolled_with_correct_mutability_modes():
    registry = {row["glob"]: row["mode"] for row in DEFAULT_LEDGER_REGISTRY}
    assert registry["h2_dutch/h2_scan_observations_v1.csv"] == "append_only"
    assert registry["h2_dutch/h2_final_episodes_v1.csv"] == "append_only"
    assert registry["h2_dutch/h2_final_sample_manifest.json"] == "snapshot"


def test_wo101_point_in_time_corpora_are_append_only_enrolled():
    registry = {row["glob"]: row["mode"] for row in DEFAULT_LEDGER_REGISTRY}
    assert registry["polymarket_training/resolution_corpus_v1.csv"] == "append_only"
    assert registry["polymarket_training/historical_bid_ask_v1.csv"] == "append_only"


def test_current_sharp_qualification_is_snapshot_enrolled():
    registry = {row["glob"]: row["mode"] for row in DEFAULT_LEDGER_REGISTRY}
    assert registry["maker_carry/sharp_linking_qualification.json"] == "snapshot"


def test_deployed_config_covers_every_default_ledger_enrollment():
    # #269 Codex-review P1 (confirmed): the example config's explicit
    # ledger_globs list replaces DEFAULT_LEDGER_REGISTRY wholesale, so a
    # code-default enrollment absent from the config is silently inert in
    # production. Both paper_fills_v2 (WO-110) and reward_epoch_samples
    # (WO-106) had this gap. Pin the deployed config as a mode-matched
    # superset of the code default so the two surfaces can never drift again.
    from polymarket_predictive_engine import ledger_anchor as mod
    from polymarket_predictive_engine.config import load_config

    cfg = load_config("polymarket_predictive_config.example.yaml")
    settings = mod._settings(cfg)
    effective = {str(e["glob"]): str(e["mode"]) for e in settings["ledger_globs"]}
    for entry in mod.DEFAULT_LEDGER_REGISTRY:
        assert effective.get(str(entry["glob"])) == str(entry["mode"]), entry
