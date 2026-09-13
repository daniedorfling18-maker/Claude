"""WO-173 tests 1-10: each maker figure must carry the evidence class it actually has.

The study's +$1.68/day is a simulation. The replay's adverse-selection charge rests on three
hypothetical fills against a registered floor of ten. The scoreboard's $0 is real money. Printed
together without their classes, the modelled number reads as the measured one.

The three fixtures are the 2026-08-21 telemetry snapshot, sanitised per the README convention."""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from polymarket_predictive_engine import maker_evidence_summary as mes
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.maker_carry_study import MB_TIER0_MIN_CONFIRMED_FILLS
from polymarket_predictive_engine.utils import read_json

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "recorded" / "maker_2026-08-21"
NAMES = ("maker_carry_study", "maker_fill_replay", "maker_live_test")
# Later than every fixture's generated_at_utc, so ages are readable.
RUN_CLOCK = datetime(2026, 8, 21, 2, 0, 9, tzinfo=timezone.utc)
STUDY_STAMP = datetime(2026, 8, 20, 10, 46, 56, tzinfo=timezone.utc)


def _config(tmp_path: Path, *, raw_overrides: dict | None = None):
    raw = yaml.safe_load((REPO_ROOT / "polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    for key, value in (raw_overrides or {}).items():
        raw.setdefault(key, {})
        raw[key].update(value)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def _install(cfg, *, omit: tuple[str, ...] = (), overrides: dict[str, dict] | None = None) -> None:
    root = cfg.output_root / "maker_carry"
    root.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        if name in omit:
            continue
        payload = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
        payload.update((overrides or {}).get(name, {}))
        (root / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")


def _run(cfg, **kwargs):
    return mes.run_maker_evidence_summary(cfg, now=kwargs.pop("now", RUN_CLOCK), **kwargs)


def test_modelled_block_on_the_recorded_fixtures(tmp_path: Path):
    """The modelled carry is copied with the study's own honesty clause beside it."""
    cfg = _config(tmp_path)
    _install(cfg)
    summary = _run(cfg)
    assert summary["status"] == "ok"
    modelled = summary["modelled"]
    assert modelled["net_carry_usd_per_day"] == 1.68
    assert modelled["target_usd_per_day"] == 3.33
    assert modelled["evidence_class"] == "modeled"
    assert "upper bound" in modelled["evidence_note"]
    assert modelled["share_model"] == "published_v2"
    assert modelled["share_model_note"] == "assumed reward-sharing model, not observed receipts"
    assert modelled["portfolio_markets"] == 1
    assert modelled["portfolio_capital_usd"] == 470.0
    study = json.loads((FIXTURES / "maker_carry_study.json").read_text(encoding="utf-8"))
    assert modelled["honesty_clause"] == study["honesty_clause"]


def test_hypothetical_fills_and_coverage(tmp_path: Path):
    """Book coverage is the complement of the no-state rate, and it is NOT the registered
    markout-window coverage the replay also reports."""
    cfg = _config(tmp_path)
    _install(cfg)
    fills = _run(cfg)["hypothetical_fills"]
    assert fills["simulated_fills"] == 3
    assert fills["confirmed_fills"] == 3
    assert fills["confirmed_fill_ratio"] == 0.136364
    assert fills["last_in_queue_evaluable_opportunities"] == 22
    assert fills["no_contemporaneous_state_opportunities"] == 316
    assert fills["no_contemporaneous_state_rate"] == 0.77451
    assert fills["contemporaneous_book_coverage_rate"] == 0.22549
    assert fills["replay_days"] == 14.0
    assert fills["quoting_basis"] == "contemporaneous"
    assert "distinct from the registered markout-window" in fills["contemporaneous_book_coverage_note"]


def test_adverse_selection_status_reads_the_tier0_floor(tmp_path: Path):
    """Three fills against a floor of ten is not a measurement, and the label says which."""
    cfg = _config(tmp_path)
    _install(cfg)
    block = _run(cfg)["adverse_selection"]
    assert block["status"] == "below_tier0_minimum_3_of_10_fills"
    assert block["tier0_min_confirmed_fills"] == MB_TIER0_MIN_CONFIRMED_FILLS == 10
    assert block["tier0_floor_source"] == "registered"
    assert block["implied_usd_per_day"] == 0.682944
    assert block["markout_per_fill"]["15m"]["min"] == -0.02
    assert block["markout_per_fill"]["15m"]["max"] == 0.01
    assert block["markout_per_fill"]["15m"]["count"] == 3
    assert block["simulation_to_reality_haircut"] is None
    assert block["simulation_to_reality_haircut_status"] == "unmeasured"
    assert "do not bound adverse selection" in block["tier0_sentence"]

    for index, (fills, expected, floor_source) in enumerate((
        (0, "unmeasured", "registered"),
        (10, "measured_on_10_fills", "registered"),
        (10.0, "measured_on_10_fills", "registered"),
        ("nan", "unmeasured", "registered"),
        (2.5, "unmeasured", "registered"),
    )):
        root = tmp_path / f"fills{index}"
        root.mkdir()
        scoped = _config(root)
        _install(scoped, overrides={"maker_fill_replay": {"confirmed_fills": fills}})
        block = _run(scoped)["adverse_selection"]
        assert block["status"] == expected, fills
        assert block["tier0_floor_source"] == floor_source

    # The floor is tighten-only: a config may raise it, never lower it.
    tight = tmp_path / "tight"
    tight.mkdir()
    scoped = _config(tight, raw_overrides={"maker_carry_study": {"mb_tier0_min_confirmed_fills": 12}})
    _install(scoped, overrides={"maker_fill_replay": {"confirmed_fills": 10}})
    block = _run(scoped)["adverse_selection"]
    assert block["status"] == "below_tier0_minimum_10_of_12_fills"
    assert block["tier0_min_confirmed_fills"] == 12
    assert block["tier0_floor_source"] == "config_tightened"

    loose = tmp_path / "loose"
    loose.mkdir()
    scoped = _config(loose, raw_overrides={"maker_carry_study": {"mb_tier0_min_confirmed_fills": 5}})
    _install(scoped, overrides={"maker_fill_replay": {"confirmed_fills": 6}})
    block = _run(scoped)["adverse_selection"]
    assert block["tier0_min_confirmed_fills"] == 10
    assert block["status"] == "below_tier0_minimum_6_of_10_fills"


def test_realized_block_reads_the_scoreboard(tmp_path: Path):
    """The only real-money evidence in this lane is zero, and it is labelled as real money."""
    cfg = _config(tmp_path)
    _install(cfg)
    realized = _run(cfg)["realized"]
    assert realized["rewards_usd_total"] == 0
    assert realized["fills"] == 0
    assert realized["fills_last_24h"] == 0
    assert realized["scoreboard"] == "flat_no_net_evidence"
    assert realized["read_only"] is True
    assert realized["evidence_class"] == "live-real-money"
    assert "no order path" in realized["evidence_note"]


def test_capacity_flag_is_model_bounded_on_a_one_market_portfolio(tmp_path: Path):
    """A curve that stops rising because the sizing model caps it is not a measured capacity."""
    cfg = _config(tmp_path)
    _install(cfg)
    capacity = _run(cfg)["capacity"]
    assert capacity["max_size_multiple"] == 5
    assert capacity["max_size_multiple_source"] == "policy_settings"
    assert capacity["cap_binding"] is True
    assert capacity["curve_state"] == "readable"
    assert capacity["curve_flat_beyond_usd"] == 500.0
    assert capacity["flat_curve_is_model_bounded"] is True
    assert "modelling constraint, not a measured market capacity" in capacity["note"]

    # A curve that keeps rising through its last two entries says nothing: null, never false.
    rising = tmp_path / "rising"
    rising.mkdir()
    scoped = _config(rising)
    curve = [
        {"capital_cap_usd": cap, "capital_used_usd": cap * 0.9, "portfolio_markets": 4}
        for cap in (250.0, 500.0, 1000.0, 2000.0, 5000.0)
    ]
    _install(scoped, overrides={"maker_carry_study": {"capital_curve": curve, "portfolio": [{"size_multiple": 4}] * 4}})
    capacity = _run(scoped)["capacity"]
    assert capacity["curve_state"] == "readable"
    assert capacity["curve_flat_beyond_usd"] is None
    assert capacity["flat_curve_is_model_bounded"] is None

    # A flat curve with four markets none of which is at the cap: genuinely not model-bounded.
    flat = tmp_path / "flat"
    flat.mkdir()
    scoped = _config(flat)
    curve = [
        {"capital_cap_usd": cap, "capital_used_usd": 900.0, "portfolio_markets": 4}
        for cap in (1000.0, 2000.0, 5000.0)
    ]
    _install(scoped, overrides={"maker_carry_study": {"capital_curve": curve, "portfolio": [{"size_multiple": 2}] * 4}})
    capacity = _run(scoped)["capacity"]
    assert capacity["cap_binding"] is False
    assert capacity["curve_flat_beyond_usd"] == 1000.0
    assert capacity["flat_curve_is_model_bounded"] is False


def test_gates_block_is_copied_never_recomputed(tmp_path: Path):
    """This artifact can neither pass nor fail a maker gate."""
    cfg = _config(tmp_path)
    _install(cfg)
    study = json.loads((FIXTURES / "maker_carry_study.json").read_text(encoding="utf-8"))
    summary = _run(cfg)
    assert summary["gates_reference"] == study["maker_gates"]
    assert json.dumps(summary["gates_reference"], sort_keys=True) == json.dumps(study["maker_gates"], sort_keys=True)

    altered = tmp_path / "altered"
    altered.mkdir()
    scoped = _config(altered)
    gates = json.loads(json.dumps(study["maker_gates"]))
    gates["maker_verdict"] = "altered_in_the_fixture"
    _install(scoped, overrides={"maker_carry_study": {"maker_gates": gates}})
    assert _run(scoped)["gates_reference"]["maker_verdict"] == "altered_in_the_fixture"


def test_missing_sources_fail_closed(tmp_path: Path):
    """Every absent, unparseable or out-of-range reading is a null and a partial status."""
    cfg = _config(tmp_path)
    _install(cfg, omit=("maker_live_test",))
    summary = _run(cfg)
    assert summary["status"] == "partial"
    assert summary["realized"]["state"] == "source_absent"
    for key in ("rewards_usd_total", "fills", "fills_last_24h", "inventory_pnl_usd", "scoreboard", "read_only"):
        assert summary["realized"][key] is None

    nonfinite = tmp_path / "nonfinite"
    nonfinite.mkdir()
    scoped = _config(nonfinite)
    _install(scoped, overrides={"maker_carry_study": {"portfolio_net_carry_usd_per_day": "nan"}})
    assert _run(scoped)["modelled"]["net_carry_usd_per_day"] is None

    empty = tmp_path / "empty"
    empty.mkdir()
    scoped = _config(empty)
    _install(scoped, overrides={"maker_carry_study": {"capital_curve": []}})
    summary = _run(scoped)
    assert summary["capacity"]["curve_state"] == "curve_unreadable"
    assert summary["capacity"]["curve_flat_beyond_usd"] is None
    assert summary["capacity"]["flat_curve_is_model_bounded"] is None
    assert summary["status"] == "partial"

    no_study = tmp_path / "no_study"
    no_study.mkdir()
    scoped = _config(no_study)
    _install(scoped, omit=("maker_carry_study",))
    summary = _run(scoped)
    assert summary["capacity"]["state"] == "source_absent"
    for key in ("cap_binding", "capital_curve", "curve_flat_beyond_usd", "flat_curve_is_model_bounded"):
        assert summary["capacity"][key] is None
    assert summary["gates_reference"] is None

    out_of_range = tmp_path / "range"
    out_of_range.mkdir()
    scoped = _config(out_of_range)
    _install(scoped, overrides={"maker_fill_replay": {"no_contemporaneous_state_rate": 1.5}})
    assert _run(scoped)["hypothetical_fills"]["contemporaneous_book_coverage_rate"] is None

    future = tmp_path / "future"
    future.mkdir()
    scoped = _config(future)
    _install(scoped, overrides={"maker_live_test": {"generated_at_utc": "2027-01-01T00:00:00Z"}})
    summary = _run(scoped)
    assert summary["sources"]["maker_live_test"]["state"] == "timestamp_unreadable"
    assert summary["status"] == "partial"


def test_source_age_advances_with_the_run_clock(tmp_path: Path):
    """Age is measured from one clock, the run's, and an unreadable stamp yields no age."""
    cfg = _config(tmp_path)
    _install(cfg)
    first = _run(cfg)["sources"]["maker_carry_study"]
    assert first["state"] == "present"
    assert first["age_seconds"] == pytest.approx((RUN_CLOCK - STUDY_STAMP).total_seconds())
    later = _run(cfg, now=RUN_CLOCK + timedelta(seconds=3600))["sources"]["maker_carry_study"]
    assert later["age_seconds"] == pytest.approx(first["age_seconds"] + 3600)

    for value in (None, "not-a-time", "2027-01-01T00:00:00Z"):
        root = tmp_path / f"stamp{abs(hash(value)) % 10_000}"
        root.mkdir(exist_ok=True)
        scoped = _config(root)
        _install(scoped, overrides={"maker_carry_study": {"generated_at_utc": value}})
        source = _run(scoped)["sources"]["maker_carry_study"]
        assert source["age_seconds"] is None, value
        assert source["state"] == "timestamp_unreadable", value


def test_cli_registers_maker_evidence_summary():
    from polymarket_predictive_engine.cli import COMMANDS

    assert "maker-evidence-summary" in COMMANDS


def test_capacity_flag_is_never_false_on_an_unreadable_curve(tmp_path: Path):
    """Property: a curve with any absent or non-finite field can never assert a capacity.

    `false` on this flag is a claim that the flattening is real. An unreadable curve supports no
    such claim, so the only admissible readings are `null`."""
    import random

    cfg = _config(tmp_path)
    rng = random.Random(20260913)
    for trial in range(60):
        entries = []
        for index in range(rng.randint(2, 6)):
            entries.append(
                {
                    "capital_cap_usd": float(250 * (index + 1)),
                    "capital_used_usd": rng.choice([0.0, 470.0, 900.0]),
                    "portfolio_markets": rng.randint(0, 5),
                }
            )
        broken = rng.randrange(len(entries))
        field = rng.choice(["capital_cap_usd", "capital_used_usd", "portfolio_markets"])
        entries[broken][field] = rng.choice([None, float("nan"), "", "nan", float("inf")])
        root = tmp_path / f"trial{trial}"
        root.mkdir()
        scoped = _config(root)
        _install(scoped, overrides={"maker_carry_study": {"capital_curve": entries}})
        capacity = _run(scoped)["capacity"]
        assert capacity["curve_state"] == "curve_unreadable", entries
        assert capacity["flat_curve_is_model_bounded"] is None, entries
        assert capacity["curve_flat_beyond_usd"] is None, entries


def test_summary_is_written_and_declares_it_invokes_no_trading(tmp_path: Path):
    cfg = _config(tmp_path)
    _install(cfg)
    payload = _run(cfg)
    written = read_json(cfg.output_root / "maker_carry" / "maker_evidence_summary.json")
    assert written["status"] == payload["status"] == "ok"
    assert written["paper_trading_invoked"] is False and written["live_trading_invoked"] is False
    assert written["modelled"]["evidence_class"] == "modeled"
    assert written["realized"]["evidence_class"] == "live-real-money"
    assert math.isclose(written["modelled"]["net_carry_usd_per_day"], 1.68)


def test_recorded_maker_fixture_provenance_and_no_credentials():
    """WO-173 item 2: sanitised per the README convention, with provenance recorded."""
    import hashlib

    from polymarket_predictive_engine.credential_guard import _scan_json

    readme = (REPO_ROOT / "tests" / "fixtures" / "recorded" / "README.md").read_text(encoding="utf-8")
    assert "maker_2026-08-21/" in readme
    assert "fcebaa2" in readme
    for stamp in ("2026-08-20T10:46:56Z", "2026-08-21T01:42:15Z", "2026-08-21T01:41:31Z"):
        assert stamp in readme, stamp
    for name in NAMES:
        path = FIXTURES / f"{name}.json"
        assert hashlib.sha256(path.read_bytes()).hexdigest() in readme, name
        assert _scan_json(path, REPO_ROOT) == [], name
