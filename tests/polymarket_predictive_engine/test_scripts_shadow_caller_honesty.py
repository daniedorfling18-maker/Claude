"""WO-143b.1 F4 — caller-honesty coverage for the `scripts/` call sites.

`scripts/run_alpha_candidate_shadow_evidence.py` and
`scripts/run_promoted_rule_shadow_scan.py` both forward rows into
``update_shadow_cohort_evidence`` and had no test file of their own, which is
why neither noticed that a contended (skipped) update was still being reported
as a successful forward.

`scripts/run_polymarket_local_live_loop.py`'s per-tick call site -- the
unguarded caller that motivated WO-143b in the first place -- is covered here
too rather than in `test_local_live_loop.py`, so the change stays inside the
registered touched-file list for this work order.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

from polymarket_predictive_engine.config import EngineConfig, load_config
from polymarket_predictive_engine.runtime_lock import acquire_runtime_lock, release_runtime_lock
from polymarket_predictive_engine.utils import read_csv_rows, write_csv


ROOT = Path(__file__).resolve().parents[2]
ALPHA_SCRIPT = ROOT / "scripts" / "run_alpha_candidate_shadow_evidence.py"
PROMOTED_SCRIPT = ROOT / "scripts" / "run_promoted_rule_shadow_scan.py"


def _load_script(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_config(tmp_path: Path) -> Path:
    raw = yaml.safe_load((ROOT / "polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"] = {
        "data_root": str(tmp_path),
        "output_root": str(tmp_path / "outputs"),
        "database_path": str(tmp_path / "work" / "paper.sqlite"),
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return config_path


def _alpha_candidate_row() -> dict[str, object]:
    """A row that passes `_valid_alpha_shadow_candidate` and opens shadow evidence."""
    return {
        "market_id": "m-alpha-honesty",
        "token_id": "t-alpha-honesty",
        "market_slug": "will-the-alpha-candidate-resolve",
        "question": "Will the alpha candidate resolve?",
        "outcome": "Yes",
        "category": "macro_rates",
        "signal_cohort": "alpha_probe",
        "alpha_trade_candidate": "True",
        "validation_layer_pass": "True",
        "microstructure_filter_pass": "True",
        "crypto_model_gate_pass": "True",
        "bookmaker_cross_check_pass": "True",
        "edge_lower_bound": "0.05",
        "executable_price": "0.40",
        "best_bid": "0.39",
        "spread": "0.01",
        "liquidity": "1000",
        "prediction_timestamp": "2026-06-27T04:00:00Z",
        "close_time": "2026-12-31T00:00:00Z",
    }


def test_alpha_candidate_shadow_evidence_reports_zero_forwarded_when_shadow_lock_held(tmp_path):
    # WO-143b.1 F4 (test 2): every count that claims rows were FORWARDED or
    # SENT to the shadow ledger must be 0 when the update was skipped, and the
    # skip status must be surfaced. Asserted with a NON-EMPTY prepared list.
    module = _load_script(ALPHA_SCRIPT, "wo143b1_alpha_shadow_evidence")
    config_path = _write_config(tmp_path)
    cfg = load_config(config_path)
    write_csv(
        cfg.output_root / "polymarket_predictions" / "mispricing_alpha_scores.csv",
        [_alpha_candidate_row()],
    )

    held = acquire_runtime_lock(cfg, "shadow_cohort", stale_after_seconds=999999)
    assert held.acquired is True
    try:
        payload = module.run(str(config_path))
    finally:
        release_runtime_lock(held)

    # The input really was non-empty, so the zeros below are the fix, not a
    # vacuous pass.
    assert payload["alpha_shadow_input_rows"] == 1
    assert payload["prepared_rows_sent_to_shadow_ledger"] == 0
    assert payload["existing_shadow_rows_forwarded"] == 0
    assert payload["existing_near_miss_rows_forwarded"] == 0
    assert payload["shadow_update_status"] == "skipped_shadow_lock_held"
    assert payload["shadow_summary"]["status"] == "skipped_shadow_lock_held"
    positions = cfg.output_root / "polymarket_shadow" / "shadow_positions.csv"
    assert not positions.exists() or read_csv_rows(positions) == []


def test_alpha_candidate_shadow_evidence_reports_the_full_count_when_uncontended(tmp_path):
    # WO-143b.1 F4 (test 3): the honesty fix must not zero a real forward.
    module = _load_script(ALPHA_SCRIPT, "wo143b1_alpha_shadow_evidence_ok")
    config_path = _write_config(tmp_path)
    cfg = load_config(config_path)
    write_csv(
        cfg.output_root / "polymarket_predictions" / "mispricing_alpha_scores.csv",
        [_alpha_candidate_row()],
    )

    payload = module.run(str(config_path))

    assert payload["alpha_shadow_input_rows"] == 1
    assert payload["prepared_rows_sent_to_shadow_ledger"] == 1
    assert payload["shadow_update_status"] != "skipped_shadow_lock_held"
    assert not payload["shadow_update_status"].startswith("skipped_")


def _promoted_cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {
                "data_root": str(tmp_path),
                "output_root": str(tmp_path / "outputs"),
                "database_path": str(tmp_path / "work" / "paper.sqlite"),
            },
            "risk": {"minimum_entry_price": 0.05, "maximum_entry_price": 0.90},
            "shadow_cohort_validation": {
                "enabled": True,
                "stake_usdc": 10,
                "candidate_limit_per_cycle": 4,
                "maximum_open_positions": 10,
                "settle_resolved_markets": False,
            },
        },
        path=tmp_path / "cfg.yaml",
    )


def test_promoted_rule_shadow_scan_surfaces_the_skip_status_when_shadow_lock_held(tmp_path):
    # WO-143b.1 F4 (test 2): this call site reports NO forwarded-candidate
    # count -- `candidates`/`rejected` are rule-scan output written to their
    # own CSVs regardless of the shadow outcome -- so per the registered
    # contract it needs only the returned status surfaced. Assert exactly that,
    # and that the scan's own counts are NOT zeroed (they are not forward
    # claims and must keep reporting the real scan result).
    module = _load_script(PROMOTED_SCRIPT, "wo143b1_promoted_rule_shadow_scan")
    cfg = _promoted_cfg(tmp_path)
    write_csv(
        cfg.output_root / "polymarket_predictions" / "predictions.csv",
        [
            {
                "market_id": "m-promoted",
                "token_id": "t-promoted",
                "shadow_trade_candidate": "True",
                "shadow_source": "test_shadow",
                "prediction_timestamp": "2026-06-27T04:00:00Z",
            }
        ],
    )

    held = acquire_runtime_lock(cfg, "shadow_cohort", stale_after_seconds=999999)
    assert held.acquired is True
    try:
        payload = module.run_promoted_rule_shadow_scan(cfg)
    finally:
        release_runtime_lock(held)

    assert payload["shadow_update_status"] == "skipped_shadow_lock_held"
    assert payload["shadow"]["status"] == "skipped_shadow_lock_held"
    positions = cfg.output_root / "polymarket_shadow" / "shadow_positions.csv"
    assert not positions.exists() or read_csv_rows(positions) == []


LIVE_LOOP_SCRIPT = ROOT / "scripts" / "run_polymarket_local_live_loop.py"


def test_live_loop_shadow_maintenance_reports_zero_rows_when_shadow_lock_held(tmp_path):
    # WO-143b.1 F4 (test 2): the live loop's per-tick `_lightweight_shadow_maintenance`
    # is the unguarded caller WO-143b exists to serialise. When a foreign
    # writer holds the internal `shadow_cohort` lock nothing is written, so
    # `prediction_rows` -- previously `len(rows)` regardless of outcome -- must
    # be 0, with the status surfaced verbatim. Non-empty predictions.csv, so
    # the 0 is not incidental.
    loop = _load_script(LIVE_LOOP_SCRIPT, "wo143b1_local_live_loop")
    cfg = EngineConfig(
        raw={
            "paths": {
                "output_root": str(tmp_path / "outputs"),
                "database_path": str(tmp_path / "work" / "paper.sqlite"),
            }
        },
        path=tmp_path / "cfg.yaml",
    )
    write_csv(
        cfg.output_root / "polymarket_predictions" / "predictions.csv",
        [
            {
                "market_id": "m-shadow-tick",
                "token_id": "t-shadow-tick",
                "shadow_trade_candidate": "True",
                "shadow_source": "test_shadow",
                "prediction_timestamp": "2026-06-27T04:00:00Z",
            }
        ],
    )

    held = acquire_runtime_lock(cfg, "shadow_cohort", stale_after_seconds=999999)
    assert held.acquired is True
    try:
        result = loop._lightweight_shadow_maintenance(cfg, [])
    finally:
        release_runtime_lock(held)

    assert result["prediction_rows"] == 0
    assert result["shadow"]["status"] == "skipped_shadow_lock_held"
    assert not (cfg.output_root / "polymarket_shadow" / "shadow_positions.csv").exists()
    assert not (cfg.output_root / "polymarket_shadow" / "shadow_fills.csv").exists()


def test_live_loop_shadow_maintenance_reports_the_real_row_count_when_uncontended(tmp_path):
    # WO-143b.1 F4 (test 3): the uncontended tick still reports the real count.
    loop = _load_script(LIVE_LOOP_SCRIPT, "wo143b1_local_live_loop_ok")
    cfg = EngineConfig(
        raw={
            "paths": {
                "output_root": str(tmp_path / "outputs"),
                "database_path": str(tmp_path / "work" / "paper.sqlite"),
            }
        },
        path=tmp_path / "cfg.yaml",
    )
    write_csv(
        cfg.output_root / "polymarket_predictions" / "predictions.csv",
        [
            {
                "market_id": "m-shadow-tick",
                "token_id": "t-shadow-tick",
                "shadow_trade_candidate": "True",
                "shadow_source": "test_shadow",
                "prediction_timestamp": "2026-06-27T04:00:00Z",
            }
        ],
    )

    result = loop._lightweight_shadow_maintenance(cfg, [])

    assert result["prediction_rows"] == 1
    assert not str(result["shadow"]["status"]).startswith("skipped_")


def _disabled_config(tmp_path: Path) -> Path:
    """The same config with `shadow_cohort_validation.enabled` turned off."""
    config_path = _write_config(tmp_path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw.setdefault("shadow_cohort_validation", {})["enabled"] = False
    config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return config_path


def test_alpha_candidate_shadow_evidence_reports_zero_forwarded_when_shadow_is_disabled(tmp_path):
    """`disabled` writes nothing either (Codex P2 wave-42).

    The honesty predicate was `startswith("skipped_")`, which does not match
    the `disabled` status `update_shadow_cohort_evidence` returns when
    `shadow_cohort_validation.enabled` is false -- so a disabled deployment
    produced a receipt claiming delivery of evidence that was never recorded.
    """
    module = _load_script(ALPHA_SCRIPT, "wo143b1_alpha_shadow_evidence_disabled")
    config_path = _disabled_config(tmp_path)
    cfg = load_config(config_path)
    write_csv(
        cfg.output_root / "polymarket_predictions" / "mispricing_alpha_scores.csv",
        [_alpha_candidate_row()],
    )

    payload = module.run(str(config_path))

    assert payload["shadow_update_status"] == "disabled"
    assert payload["prepared_rows_sent_to_shadow_ledger"] == 0
    assert payload["existing_shadow_rows_forwarded"] == 0
    assert payload["existing_near_miss_rows_forwarded"] == 0
    # The script's own CSV output is written regardless, and still says so.
    assert payload["alpha_shadow_input_rows"] == 1
    positions = cfg.output_root / "polymarket_shadow" / "shadow_positions.csv"
    assert not positions.exists() or read_csv_rows(positions) == []


def test_live_loop_shadow_maintenance_reports_zero_rows_when_shadow_is_disabled(tmp_path):
    """The live loop carries the same predicate and the same defect."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "wo143b1_live_loop_disabled", ROOT / "scripts" / "run_polymarket_local_live_loop.py"
    )
    assert spec and spec.loader
    loop = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loop)

    config_path = _disabled_config(tmp_path)
    cfg = load_config(config_path)
    write_csv(
        cfg.output_root / "polymarket_predictions" / "predictions.csv",
        [
            {
                "market_id": "m-shadow-tick",
                "token_id": "t-shadow-tick",
                "shadow_trade_candidate": "True",
                "shadow_source": "test_shadow",
                "prediction_timestamp": "2026-06-27T04:00:00Z",
            }
        ],
    )

    result = loop._lightweight_shadow_maintenance(cfg, [])

    assert str(result["shadow"]["status"]) == "disabled"
    assert result["prediction_rows"] == 0, (
        "a disabled updater wrote nothing, so no row was forwarded this tick"
    )
