"""WO-172 tests 5-11: an empty lane must say WHY it is empty.

Four research paths report zero in every snapshot. "Zero" is not one fact: a lane whose collector
never ran is untested, and a lane whose complete inputs were scanned and found nothing is a
measured negative. Reading the first as the second turns a broken pipe into evidence.

The five JSONs are the committed 2026-08-21 telemetry snapshot, verbatim."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from polymarket_predictive_engine import data_coverage_report as dcr
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.degraded_state_watchdog import REGISTERED_JOB_FRESHNESS_MAX_SECONDS
from polymarket_predictive_engine.utils import read_json, write_csv

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "recorded" / "coverage_2026-08-21"
# The snapshot's own clock, so ages in these tests are the real ones.
RUN_CLOCK = "2026-08-21T02:00:09Z"

DESTINATIONS = {
    "smart_flow_clv.json": "governance",
    "sharp_anchor_coverage.json": "governance",
    "family_calibration_scorecard.json": "governance",
    "implication_scan.json": "implication_consistency",
    "event_group_scan.json": "event_group_consistency",
}


def _config(tmp_path: Path):
    raw = yaml.safe_load((REPO_ROOT / "polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def _install(cfg, names=None, overrides: dict[str, dict] | None = None) -> None:
    """Copy the recorded fixtures into the places the report reads."""
    for name, destination in DESTINATIONS.items():
        if names is not None and name not in names:
            continue
        payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        payload.update((overrides or {}).get(name, {}))
        target = (cfg.governance_root if destination == "governance" else cfg.output_root / destination) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload), encoding="utf-8")


def _by_artifact(payload: dict) -> dict[str, dict]:
    return {entry["artifact"]: entry for entry in payload["paths"]}


def _report(cfg, monkeypatch, *, clock: str = RUN_CLOCK, **kwargs) -> dict:
    monkeypatch.setattr(dcr, "now_utc", lambda: clock)
    return dcr.build_data_coverage_report(cfg, **kwargs)


def test_smart_flow_classifies_ingestion_failure_on_the_recorded_fixture(tmp_path, monkeypatch):
    """H3 did not fail to find an edge. Its input was never collected."""
    cfg = _config(tmp_path)
    _install(cfg)
    paths = _by_artifact(_report(cfg, monkeypatch))
    flow = paths["polymarket_model_governance/smart_flow_clv.json"]
    assert flow["classification"] == "ingestion_failure"
    assert flow["producer_state"] == "manual_only"
    assert flow["evidence_rule"] == "absence of evidence: untested"
    fills = paths["inputs/polymarket/public_wallet_fills.csv"]
    assert fills["producer_state"] == "no_producer_registered"
    assert fills["classification"] == "ingestion_failure"
    assert fills["population"] == 0


def test_sharp_anchor_classifies_coverage_limitation(tmp_path, monkeypatch):
    """Rows were fetched and none could be joined: the anchor was never tested against a price."""
    cfg = _config(tmp_path)
    _install(cfg)
    entry = _by_artifact(_report(cfg, monkeypatch))["polymarket_model_governance/sharp_anchor_coverage.json"]
    assert entry["classification"] == "coverage_limitation"
    assert entry["population"] == 30
    assert entry["eligible"] == 0
    assert entry["excluded"] == {"stale_rows": 30}
    assert entry["producer"] == "refresh-governance"
    assert entry["producer_cadence_seconds"] == 21600
    assert entry["evidence_rule"] == "absence of evidence: untested"


def test_calibration_classifies_join_failure(tmp_path, monkeypatch):
    """Every one of 17,420 joins was rejected; no calibration was measured."""
    cfg = _config(tmp_path)
    _install(cfg)
    entry = _by_artifact(_report(cfg, monkeypatch))["polymarket_model_governance/family_calibration_scorecard.json"]
    assert entry["classification"] == "join_failure"
    assert entry["population"] == 17420
    assert entry["eligible"] == 0
    assert entry["missing_outcomes"] == 17420


def test_implication_is_coverage_limitation_and_event_group_is_measured_negative(tmp_path, monkeypatch):
    """The two scans read differently, and the difference is the whole point: 300 events with no
    classified leg is untested; 67 complete-ask groups with no deviation is a measured negative,
    inside that scope and no wider."""
    cfg = _config(tmp_path)
    _install(cfg)
    paths = _by_artifact(_report(cfg, monkeypatch))
    implication = paths["implication_consistency/implication_scan.json"]
    assert implication["classification"] == "coverage_limitation"
    assert implication["population"] == 300
    assert implication["eligible"] == 0
    assert implication["evidence_rule"] == "absence of evidence: untested"
    group = paths["event_group_consistency/event_group_scan.json"]
    assert group["classification"] == "measured_negative"
    assert group["population"] == 67
    assert group["scope"] == "within 67 complete-ask groups"
    assert group["evidence_rule"] == "measured negative within stated scope"


def test_missing_or_non_finite_reads_unknown_and_stale_is_a_second_flag(tmp_path, monkeypatch):
    """`unknown` is first and wins, and staleness never becomes a classification."""
    cfg = _config(tmp_path)
    # Nothing installed at all.
    paths = _by_artifact(_report(cfg, monkeypatch))
    for artifact in (
        "polymarket_model_governance/sharp_anchor_coverage.json",
        "polymarket_model_governance/family_calibration_scorecard.json",
        "implication_consistency/implication_scan.json",
        "event_group_consistency/event_group_scan.json",
    ):
        assert paths[artifact]["classification"] == "unknown"
        assert paths[artifact]["stale"] is True
        assert paths[artifact]["age_seconds"] is None

    # A non-finite field is unknown, not a measured zero.
    (tmp_path / "nan").mkdir(exist_ok=True)
    cfg2 = _config(tmp_path / "nan")
    _install(cfg2, overrides={"smart_flow_clv.json": {"fills_seen": "nan"}})
    assert _by_artifact(_report(cfg2, monkeypatch))["polymarket_model_governance/smart_flow_clv.json"]["classification"] == "unknown"

    # An unparseable clock is stale with a null age, and the classification is untouched.
    (tmp_path / "clock").mkdir(exist_ok=True)
    cfg3 = _config(tmp_path / "clock")
    _install(cfg3, overrides={"sharp_anchor_coverage.json": {"generated_at_utc": "not-a-time"}})
    entry = _by_artifact(_report(cfg3, monkeypatch))["polymarket_model_governance/sharp_anchor_coverage.json"]
    assert entry["stale"] is True and entry["age_seconds"] is None
    assert entry["classification"] == "coverage_limitation"

    # The ceilings come from the watchdog's registered table, not from literals here.
    assert REGISTERED_JOB_FRESHNESS_MAX_SECONDS["governance_refresh"] == 25200
    assert REGISTERED_JOB_FRESHNESS_MAX_SECONDS["trade_prints"] == 1200
    run = datetime(2026, 8, 21, 2, 0, 9, tzinfo=timezone.utc)
    for lane, artifact, name, fresh, stale in (
        ("governance", "polymarket_model_governance/sharp_anchor_coverage.json", "sharp_anchor_coverage.json",
         timedelta(hours=6), timedelta(hours=8)),
        ("trade_prints", "implication_consistency/implication_scan.json", "implication_scan.json",
         timedelta(minutes=15), timedelta(minutes=25)),
    ):
        for age, expected in ((fresh, False), (stale, True)):
            root = tmp_path / f"{lane}-{int(age.total_seconds())}"
            root.mkdir(exist_ok=True)
            scoped = _config(root)
            stamp = (run - age).strftime("%Y-%m-%dT%H:%M:%SZ")
            _install(scoped, overrides={name: {"generated_at_utc": stamp}})
            assert _by_artifact(_report(scoped, monkeypatch))[artifact]["stale"] is expected, (lane, age)


def _ledger(cfg, rows: list[dict]) -> None:
    write_csv(
        cfg.output_root / "polymarket_shadow" / "shadow_positions.csv",
        rows,
        fieldnames=["shadow_position_id", "status", "signal_cohort", "realised_pnl_usdc"],
    )


def _closed_rows() -> list[dict]:
    """Closed rows summing to -218.01, of which blank-cohort rows sum to -18.01."""
    rows = [{"shadow_position_id": f"a{i}", "status": "closed", "signal_cohort": "sharp_anchor_wc", "realised_pnl_usdc": -100.0} for i in range(2)]
    rows.append({"shadow_position_id": "b0", "status": "closed", "signal_cohort": "", "realised_pnl_usdc": -18.01})
    rows.append({"shadow_position_id": "c0", "status": "open", "signal_cohort": "sharp_anchor_wc", "realised_pnl_usdc": 999.0})
    return rows


def _manifest(tmp_path: Path, *, truncated: bool) -> Path:
    payload = {
        "files": [
            {
                "path": "outputs/polymarket_shadow/shadow_positions.csv",
                "truncated": truncated,
                "source_lines_after_header": 1000 if truncated else 4,
                "exported_lines_after_header": 200 if truncated else 4,
            }
        ]
    }
    path = tmp_path / "export_manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_pnl_attribution_check_uses_the_ledger_and_refuses_a_truncated_one(tmp_path, monkeypatch):
    """Missing attribution must not remove losses from the total, and a truncated ledger yields
    no figure at all."""
    cfg = _config(tmp_path)
    _install(cfg)
    _ledger(cfg, _closed_rows())

    truncated = _report(cfg, monkeypatch, export_manifest_path=_manifest(tmp_path, truncated=True))["pnl_attribution_check"]
    assert truncated["state"] == "unknown_truncated_input"
    assert truncated["source_lines_after_header"] == 1000
    assert truncated["exported_lines_after_header"] == 200
    assert "closed_total_pnl_usdc" not in truncated

    whole = _report(cfg, monkeypatch, export_manifest_path=_manifest(tmp_path, truncated=False))["pnl_attribution_check"]
    assert whole["state"] == "ok"
    assert whole["ledger_completeness_basis"] == "manifest"
    assert whole["closed_total_pnl_usdc"] == pytest.approx(-218.01)
    assert whole["unattributed_pnl_usdc"] == pytest.approx(-18.01)
    assert whole["attributed_by_cohort"] == {"sharp_anchor_wc": pytest.approx(-200.0)}
    assert sum(whole["attributed_by_cohort"].values()) + whole["unattributed_pnl_usdc"] == pytest.approx(whole["closed_total_pnl_usdc"])
    assert whole["comparable"] is False

    # A closed row with no usable P&L yields no figure rather than a wrong one.
    _ledger(cfg, _closed_rows() + [{"shadow_position_id": "d0", "status": "closed", "signal_cohort": "x", "realised_pnl_usdc": ""}])
    malformed = _report(cfg, monkeypatch, export_manifest_path=_manifest(tmp_path, truncated=False))["pnl_attribution_check"]
    assert malformed["state"] == "unknown_malformed_row"
    assert malformed["malformed_closed_rows"] == 1
    assert "closed_total_pnl_usdc" not in malformed


def test_no_manifest_means_no_figure_unless_the_source_is_declared(tmp_path, monkeypatch):
    """Without evidence that the ledger is whole, no attribution figure is stated."""
    cfg = _config(tmp_path)
    _install(cfg)
    _ledger(cfg, _closed_rows())

    blind = _report(cfg, monkeypatch)["pnl_attribution_check"]
    assert blind["state"] == "unknown_no_manifest"
    assert blind["ledger_completeness_basis"] == "unknown"
    assert "closed_total_pnl_usdc" not in blind

    declared = _report(cfg, monkeypatch, ledger_source="vps")["pnl_attribution_check"]
    assert declared["state"] == "ok"
    assert declared["ledger_completeness_basis"] == "vps"
    assert declared["closed_total_pnl_usdc"] == pytest.approx(-218.01)


def test_report_is_written_atomically_and_declares_it_invokes_no_trading(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    _install(cfg)
    payload = _report(cfg, monkeypatch)
    written = read_json(cfg.governance_root / "data_coverage_report.json")
    assert written["counts_by_classification"] == payload["counts_by_classification"]
    assert written["paper_trading_invoked"] is False and written["live_trading_invoked"] is False
    assert written["classification_set"] == ["unknown", "ingestion_failure", "coverage_limitation", "join_failure", "measured_negative"]
    assert written["counts_by_classification"]["measured_negative"] == 1
    assert written["counts_by_classification"]["ingestion_failure"] == 2


def test_recorded_coverage_fixture_provenance_and_no_credentials():
    """WO-172 item 4: the fixtures are the snapshot, and the README says where they came from."""
    import hashlib

    from polymarket_predictive_engine.credential_guard import _scan_json

    readme = (REPO_ROOT / "tests" / "fixtures" / "recorded" / "README.md").read_text(encoding="utf-8")
    assert "coverage_2026-08-21/" in readme
    assert "fcebaa2" in readme and "2026-08-21T02:00:09Z" in readme
    for name in DESTINATIONS:
        path = FIXTURES / name
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest in readme, name
        assert _scan_json(path, REPO_ROOT) == [], name


def test_fills_seen_with_none_scored_is_a_coverage_limitation_not_a_measured_negative(tmp_path, monkeypatch):
    """WO-172 delta 1, from the build line audit: `measured_negative` was reachable on two paths
    the register does not register it for, once with a null scope.

    "5,000 fills seen, none scored" is "input rows exist but none could be used" — the registered
    definition of a coverage limitation. Reading it as a measured negative would publish the H3
    lane as evidence of no edge on the day its collector finally runs and every row is skipped,
    which is the exact reading this work order exists to prevent."""
    cfg = _config(tmp_path)
    _install(cfg, overrides={"smart_flow_clv.json": {"fills_seen": 5000, "fills_scored": 0}})
    entry = _by_artifact(_report(cfg, monkeypatch))["polymarket_model_governance/smart_flow_clv.json"]
    assert entry["classification"] == "coverage_limitation"
    assert entry["evidence_rule"] == "absence of evidence: untested"

    # A measured negative must always carry its scope; the guard is asserted at the one place
    # every entry passes through.
    with pytest.raises(ValueError, match="must carry its scope"):
        dcr._entry(
            artifact="x", fields_read=[], producer=None, producer_state="scheduled", cadence=None,
            payload={}, lane=None, run_clock=RUN_CLOCK, classification="measured_negative", scope=None,
        )
    scoped_ok = dcr._entry(
        artifact="x", fields_read=[], producer=None, producer_state="scheduled", cadence=None,
        payload={}, lane=None, run_clock=RUN_CLOCK, classification="measured_negative", scope="within 3 things",
    )
    assert scoped_ok["scope"] == "within 3 things"


def test_a_manifest_entry_without_a_truncated_key_yields_no_figure(tmp_path, monkeypatch):
    """WO-172 delta 1: `bool(None)` is False, so an entry with no `truncated` key — or an explicit
    null — read as whole and produced a figure from a ledger whose own line counts, held in the
    same entry, showed it was an extract."""
    cfg = _config(tmp_path)
    _install(cfg)
    _ledger(cfg, _closed_rows())
    for entry in (
        {"path": "outputs/polymarket_shadow/shadow_positions.csv", "source_lines_after_header": 1000, "exported_lines_after_header": 200},
        {"path": "outputs/polymarket_shadow/shadow_positions.csv", "truncated": None, "source_lines_after_header": 1000, "exported_lines_after_header": 200},
        {"path": "outputs/polymarket_shadow/shadow_positions.csv", "truncated": False, "source_lines_after_header": 1000, "exported_lines_after_header": 200},
    ):
        path = tmp_path / "manifest_variant.json"
        path.write_text(json.dumps({"files": [entry]}), encoding="utf-8")
        block = _report(cfg, monkeypatch, export_manifest_path=path)["pnl_attribution_check"]
        assert block["state"] == "unknown_truncated_input", entry
        assert "closed_total_pnl_usdc" not in block

    # An explicit false whose counts agree still yields the figure.
    path = tmp_path / "whole.json"
    path.write_text(json.dumps({"files": [{"path": "outputs/polymarket_shadow/shadow_positions.csv", "truncated": False, "source_lines_after_header": 4, "exported_lines_after_header": 4}]}), encoding="utf-8")
    assert _report(cfg, monkeypatch, export_manifest_path=path)["pnl_attribution_check"]["state"] == "ok"


def test_status_is_partial_when_any_path_reads_unknown(tmp_path, monkeypatch):
    """WO-172 delta 1: `status` was an unconditional literal, so an artifact whose every path read
    `unknown` still published `ok`."""
    cfg = _config(tmp_path)
    payload = _report(cfg, monkeypatch)  # nothing installed at all
    assert payload["status"] == "partial"
    assert len(payload["unknown_paths"]) == payload["counts_by_classification"]["unknown"] > 0
    _install(cfg)
    full = _report(cfg, monkeypatch)
    assert full["status"] == "ok"
    assert full["unknown_paths"] == []


def test_the_configured_fills_path_is_read_not_only_the_registered_literal(tmp_path, monkeypatch):
    """WO-172 delta 1: the reader this report cites resolves its own `fills_input` setting and
    falls back to a path with no `inputs/` segment. Reading only the registered literal reported a
    confident ingestion failure about a file that exists somewhere else."""
    cfg = _config(tmp_path)
    _install(cfg)
    elsewhere = tmp_path / "somewhere" / "fills.csv"
    elsewhere.parent.mkdir(parents=True, exist_ok=True)
    write_csv(elsewhere, [{"a": 1}, {"a": 2}], fieldnames=["a"])
    cfg.raw.setdefault("smart_flow_clv", {})["fills_input"] = str(elsewhere)
    entry = _by_artifact(_report(cfg, monkeypatch))["inputs/polymarket/public_wallet_fills.csv"]
    assert entry["population"] == 2
    assert entry["classification"] == "unknown"
    assert str(elsewhere) in entry["detail"]
