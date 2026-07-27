"""WO-61 tamper-evident ledger anchoring tests."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from polymarket_predictive_engine.cli import COMMANDS, main as cli_main
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


def test_wo111_portfolio_members_sidecar_enrolled_append_only():
    # WO-111: the new sidecar must be enrolled append_only in the code default so a
    # future per-market gate recompute reads tamper-evident membership evidence.
    assert {
        "glob": "maker_carry/maker_carry_portfolio_members.csv",
        "mode": "append_only",
    } in DEFAULT_LEDGER_REGISTRY


def test_wo111_portfolio_members_sidecar_anchors_forward_only(tmp_path: Path):
    # Enrolling a brand-new (initially empty->one-row) append_only file is anchor-safe,
    # and appending a second run's row leaves the prior prefix bytes unchanged, so
    # verify_ledger_chain stays clean across both runs (the fixed two-column schema
    # never forces a prefix-breaking rewrite).
    cfg = _config(
        tmp_path,
        [{"glob": "maker_carry/maker_carry_portfolio_members.csv", "mode": "append_only"}],
    )
    ledger = cfg.output_root / "maker_carry" / "maker_carry_portfolio_members.csv"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(b"generated_at_utc,portfolio_members\r\n2026-07-20T00:00:00Z,[]\r\n")
    first = anchor_ledgers(cfg, anchor_date="2026-07-20")
    ledger.write_bytes(
        ledger.read_bytes()
        + b'2026-07-21T00:00:00Z,"[{""condition_id"":""0xc"",""markout_measured"":true}]"\r\n'
    )
    second = anchor_ledgers(cfg, anchor_date="2026-07-21")
    verification = verify_ledger_chain(cfg)

    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert verification["status"] == "ok"
    rows = read_csv_rows(cfg.output_root / "performance" / "ledger_anchor_chain.csv")
    manifest_first = json.loads(rows[0]["ledger_manifest_json"])[0]["byte_length"]
    manifest_second = json.loads(rows[1]["ledger_manifest_json"])[0]["byte_length"]
    assert manifest_second > manifest_first  # forward-only growth


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


def test_wo115_maker_carry_history_is_snapshot_enrolled():
    # WO-115: the study's committer legitimately REWRITES maker_carry_history.csv
    # (legacy-schema upgrade path), so append_only enrollment broke the chain at
    # the 2026-07-12 anchored prefix when the header widened. Snapshot mode
    # anchors an immutable daily copy instead, like the other regenerated sources.
    registry = {row["glob"]: row["mode"] for row in DEFAULT_LEDGER_REGISTRY}
    assert registry["maker_carry/maker_carry_history.csv"] == "snapshot"


def test_wo115_authorized_history_rewrite_keeps_chain_verifiable(tmp_path: Path):
    # Regression for the 2026-07-12 break: a full rewrite that widens the header
    # and re-serialises historical rows must no longer poison the chain.
    cfg = _config(tmp_path, [{"glob": "maker_carry/maker_carry_history.csv", "mode": "snapshot"}])
    ledger = cfg.output_root / "maker_carry" / "maker_carry_history.csv"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(b"generated_at_utc,net_carry\r\n2026-07-11T00:00:00Z,1.0\r\n")
    first = anchor_ledgers(cfg, anchor_date="2026-07-11")

    # Legacy-schema upgrade: header widens, every prior row re-serialises.
    ledger.write_bytes(
        b"generated_at_utc,net_carry,portfolio_capital_usd\r\n"
        b"2026-07-11T00:00:00Z,1.0,\r\n"
        b"2026-07-12T00:00:00Z,2.0,55.0\r\n"
    )
    second = anchor_ledgers(cfg, anchor_date="2026-07-12")
    verification = verify_ledger_chain(cfg)

    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert verification["status"] == "ok"
    snapshot_root = cfg.output_root / "performance" / "ledger_anchor_snapshots"
    assert (snapshot_root / "2026-07-11" / "maker_carry" / "maker_carry_history.csv").is_file()
    assert (snapshot_root / "2026-07-12" / "maker_carry" / "maker_carry_history.csv").is_file()


def test_wo115_blocked_chain_anchor_run_exits_nonzero(tmp_path: Path):
    # WO-115 fail-loud: blocked_broken_chain used to exit 0, so the scheduler
    # recorded success while the head stayed frozen for days (2026-07-16..25).
    cfg = _config(tmp_path, ["audit/core.csv"])
    config_path = tmp_path / "config.yaml"
    ledger = cfg.output_root / "audit" / "core.csv"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(b"alpha\n")
    assert cli_main(["anchor-ledgers", "--config", str(config_path)]) == 0

    changed = bytearray(ledger.read_bytes())
    changed[0] = ord("A")
    ledger.write_bytes(changed)
    head_path = cfg.output_root / "performance" / "ledger_anchor_head.json"
    head_before = head_path.read_bytes()
    assert cli_main(["anchor-ledgers", "--config", str(config_path)]) == 1
    summary = read_json(cfg.output_root / "performance" / "ledger_anchor_summary.json")
    assert summary["status"] == "blocked_broken_chain"
    # The head is deliberately NOT rewritten on a blocked run; the nonzero exit
    # is what surfaces the freeze to the scheduler_nonzero_exit watchdog.
    assert head_path.read_bytes() == head_before


def _write_provenance(cfg, *, prefixes, boundary):
    from polymarket_predictive_engine.ledger_anchor import RESTORE_PROVENANCE_FILE
    from polymarket_predictive_engine.utils import write_json

    write_json(
        cfg.output_root / "performance" / "ledger_restore_provenance.json",
        {
            "work_order": "WO-127",
            "restore_boundary_date": boundary,
            "excluded_path_prefixes": list(prefixes),
        },
    )
    assert RESTORE_PROVENANCE_FILE == "performance/ledger_restore_provenance.json"


def test_wo127_restore_provenance_excuses_only_registered_prefixes_before_the_boundary(
    tmp_path: Path,
):
    # WO-127 replaces WO-123's caller-supplied tolerance. That parameter was
    # opt-in, so only the DR restore check passed it: a restore verified clean and
    # the next production anchor_ledgers run - which passes nothing - read the
    # deliberately-excluded corpora as missing and froze the head. Tolerance is
    # now a property of the TREE's recorded provenance, so every caller agrees.
    cfg = _config(
        tmp_path,
        ["audit/core.csv", {"glob": "polymarket_training/historical_bid_ask_v1.csv", "mode": "append_only"}],
    )
    core = cfg.output_root / "audit" / "core.csv"
    corpus = cfg.output_root / "polymarket_training" / "historical_bid_ask_v1.csv"
    core.parent.mkdir(parents=True)
    corpus.parent.mkdir(parents=True)
    core.write_bytes(b"date,value\n2026-07-11,1\n")
    corpus.write_bytes(b"ts,bid,ask\n1784000000,0.41,0.59\n")
    assert anchor_ledgers(cfg, anchor_date="2026-07-11")["status"] == "ok"

    baseline = verify_ledger_chain(cfg, write_summary=False)
    assert baseline["status"] == "ok"
    assert baseline["ledger_prefixes_checked"] == 2
    assert baseline["restored_unverifiable_tolerated"] == 0
    assert baseline["restore_boundary_date"] is None

    # 1. Absence with NO provenance marker still breaks - the default verifies all.
    corpus.unlink()
    assert verify_ledger_chain(cfg, write_summary=False)["status"] == "broken"

    # 2. With provenance, the pre-boundary entry is excused and counted.
    _write_provenance(cfg, prefixes=["polymarket_training/"], boundary="2026-07-11")
    tolerated = verify_ledger_chain(cfg, write_summary=False)
    assert tolerated["status"] == "ok"
    assert tolerated["restored_unverifiable_tolerated"] == 1
    assert tolerated["restore_boundary_date"] == "2026-07-11"
    assert tolerated["restore_tolerated_prefixes"] == ["polymarket_training/"]

    # 3. THE PERMANENT-WEDGE CASE the audit did not reach: a re-harvest brings the
    # file back with DIFFERENT bytes. Under absence-only tolerance this flipped to
    # "anchored prefix digest changed" and wedged the chain forever. Those bytes
    # are unreproducible, so a pre-boundary entry is unverifiable by design.
    corpus.write_bytes(b"ts,bid,ask\n1784000000,0.99,0.01\n")
    reharvested = verify_ledger_chain(cfg, write_summary=False)
    assert reharvested["status"] == "ok"
    assert reharvested["restored_unverifiable_tolerated"] == 1

    # 4. Tolerance never leaks outside the registered prefix.
    core.unlink()
    outside = verify_ledger_chain(cfg, write_summary=False)
    assert outside["status"] == "broken"
    assert "audit/core.csv: anchored file is missing" in outside["issues"][0]

    # 5. A marker naming an UNREGISTERED prefix excuses nothing.
    core.write_bytes(b"date,value\n2026-07-11,1\n")
    _write_provenance(cfg, prefixes=["audit/"], boundary="2026-07-11")
    forged = verify_ledger_chain(cfg, write_summary=False)
    assert forged["status"] == "broken"
    assert forged["restore_tolerated_prefixes"] == []


def test_wo127_rows_anchored_after_the_boundary_are_verified_normally(tmp_path: Path):
    # The excuse is scoped to bytes that predate the restore. Anything anchored
    # afterwards records fresh digests that must verify, or the marker would be a
    # standing licence to delete the corpus.
    cfg = _config(
        tmp_path,
        [{"glob": "polymarket_training/historical_bid_ask_v1.csv", "mode": "append_only"}],
    )
    corpus = cfg.output_root / "polymarket_training" / "historical_bid_ask_v1.csv"
    corpus.parent.mkdir(parents=True)
    corpus.write_bytes(b"ts,bid,ask\n1784000000,0.41,0.59\n")
    assert anchor_ledgers(cfg, anchor_date="2026-07-11")["status"] == "ok"
    _write_provenance(cfg, prefixes=["polymarket_training/"], boundary="2026-07-11")

    # A post-boundary anchor of re-harvested content.
    corpus.write_bytes(b"ts,bid,ask\n1784000000,0.41,0.59\n1784000900,0.42,0.58\n")
    assert anchor_ledgers(cfg, anchor_date="2026-07-12")["status"] == "ok"
    assert verify_ledger_chain(cfg, write_summary=False)["status"] == "ok"

    # Tamper with the post-boundary bytes: that row is NOT excused.
    corpus.write_bytes(b"ts,bid,ask\n9999999999,0.01,0.99\n1784000900,0.42,0.58\n")
    tampered = verify_ledger_chain(cfg, write_summary=False)
    assert tampered["status"] == "broken"
    assert tampered["first_broken_date"] == "2026-07-12"


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


def test_wo127_invalid_or_future_restore_boundary_is_refused(tmp_path: Path):
    # Codex review of #364 (P1): the boundary was accepted on LENGTH alone, so
    # "9999-99-99" passed and — because the row comparison is lexical — sorted
    # every historical anchor as pre-boundary. That converted a boundary-scoped
    # excuse into a blanket one and voided the post-boundary tamper check, which
    # is the whole safety property of WO-127.
    cfg = _config(
        tmp_path,
        [{"glob": "polymarket_training/historical_bid_ask_v1.csv", "mode": "append_only"}],
    )
    corpus = cfg.output_root / "polymarket_training" / "historical_bid_ask_v1.csv"
    corpus.parent.mkdir(parents=True)
    corpus.write_bytes(b"ts,bid,ask\n1784000000,0.41,0.59\n")
    assert anchor_ledgers(cfg, anchor_date="2026-07-11")["status"] == "ok"
    corpus.unlink()

    # A valid past boundary excuses the pre-boundary entry.
    _write_provenance(cfg, prefixes=["polymarket_training/"], boundary="2026-07-11")
    assert verify_ledger_chain(cfg, write_summary=False)["status"] == "ok"

    # A ten-character non-date must be refused, not honoured.
    for bogus in ("9999-99-99", "2026-13-01", "2026-02-30", "not-a-date", "20260711"):
        _write_provenance(cfg, prefixes=["polymarket_training/"], boundary=bogus)
        refused = verify_ledger_chain(cfg, write_summary=False)
        assert refused["status"] == "broken", bogus
        assert refused["restore_tolerated_prefixes"] == [], bogus
        assert refused["restore_boundary_date"] is None, bogus
        # Refused, not silently dropped: the operator is told why.
        assert "not a valid YYYY-MM-DD" in (refused["restore_provenance_rejected"] or ""), bogus

    # A syntactically valid FUTURE boundary is the same blanket excuse by another
    # route — rows that do not exist yet would be pre-boundary forever.
    _write_provenance(cfg, prefixes=["polymarket_training/"], boundary="2099-01-01")
    future = verify_ledger_chain(cfg, write_summary=False)
    assert future["status"] == "broken"
    assert "is in the future" in (future["restore_provenance_rejected"] or "")

    # A marker naming no registered prefix says so rather than failing silently.
    _write_provenance(cfg, prefixes=["audit/"], boundary="2026-07-11")
    unregistered = verify_ledger_chain(cfg, write_summary=False)
    assert unregistered["status"] == "broken"
    assert "no registered excluded prefix" in (unregistered["restore_provenance_rejected"] or "")
