from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from premium_research import report, runner
from premium_research.manifest import ManifestEntry, build_manifest, sha256_bytes, write_manifest

HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
START = 1704067200000  # 2024-01-01T00:00Z (Monday)
DAYS = 100


def _write(path: Path, frame: pd.DataFrame) -> ManifestEntry:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = frame.to_csv(index=False, lineterminator="\n").encode("utf-8")
    path.write_bytes(payload)
    ts_col = frame.columns[0]
    return ManifestEntry(
        path=str(path.relative_to(path.parents[2])).replace("\\", "/"),
        source_urls=["synthetic"],
        fetched_at="2026-09-12T00:00:00Z",
        rows=int(len(frame)),
        first_timestamp_ms=int(frame[ts_col].iloc[0]),
        last_timestamp_ms=int(frame[ts_col].iloc[-1]),
        sha256=sha256_bytes(payload),
        upstream_checksums={"synthetic": "00" * 32} if "/binance/" in str(path) else {},
        checksum_verified="/binance/" in str(path),
    )


def build_synthetic_root(root: Path, *, seed: int = 11, funding_level: float = 0.0002, drop_perp_hour: int | None = None, drop_spot_hour: int | None = None) -> None:
    """A small but complete research tree: two symbols, 100 days, hourly prices, 8h funding, DVOL, Deribit funding."""
    rng = np.random.default_rng(seed)
    hours = DAYS * 24
    entries: list[ManifestEntry] = []
    for symbol, currency, level in (("BTCUSDT", "BTC", 40_000.0), ("ETHUSDT", "ETH", 2_000.0)):
        steps = rng.normal(0.0, 0.004, size=hours)
        spot = level * np.exp(np.cumsum(steps))
        basis = 1.0 + rng.normal(0.0, 0.0003, size=hours)
        perp = spot * basis
        open_time = np.array([START + i * HOUR_MS for i in range(hours)], dtype=np.int64)
        spot_frame = pd.DataFrame({"open_time": open_time, "open": spot, "high": spot * 1.002, "low": spot * 0.998, "close": spot})
        perp_frame = pd.DataFrame({"open_time": open_time, "open": perp, "high": perp * 1.002, "low": perp * 0.998, "close": perp})
        if drop_perp_hour is not None and symbol == "BTCUSDT":
            perp_frame = perp_frame.drop(index=drop_perp_hour).reset_index(drop=True)
        if drop_spot_hour is not None and symbol == "BTCUSDT":
            spot_frame = spot_frame.drop(index=drop_spot_hour).reset_index(drop=True)
        boundaries = np.array([START + i * 8 * HOUR_MS for i in range(1, DAYS * 3)], dtype=np.int64)
        funding = pd.DataFrame({"calc_time": boundaries, "calc_time_raw": boundaries, "funding_interval_hours": 8, "last_funding_rate": funding_level + rng.normal(0.0, 0.00005, size=len(boundaries))})
        entries.append(_write(root / "data" / "binance" / f"{symbol}_funding_8h.csv", funding))
        entries.append(_write(root / "data" / "binance" / f"{symbol}_perp_1h.csv", perp_frame))
        entries.append(_write(root / "data" / "binance" / f"{symbol}_spot_1h.csv", spot_frame))
        days = np.array([START + i * DAY_MS for i in range(DAYS)], dtype=np.int64)
        dvol = pd.DataFrame({"timestamp": days, "open": 60.0, "high": 61.0, "low": 59.0, "close": 60.0})
        entries.append(_write(root / "data" / "deribit" / f"{currency}_dvol_daily.csv", dvol))
        stamps = np.array([START + (i + 1) * HOUR_MS for i in range(hours)], dtype=np.int64)
        deribit = pd.DataFrame({"timestamp": stamps, "index_price": spot, "interest_8h": funding_level, "interest_1h": funding_level / 8.0})
        entries.append(_write(root / "data" / "deribit" / f"{currency}_funding_1h.csv", deribit))
    manifest = build_manifest(entries, generated_at="2026-09-12T00:00:00Z", span={"synthetic": "100 days"}, code_revision="synthetic")
    write_manifest(manifest, root / "manifest.json")


def small_config() -> runner.Config:
    return runner.Config(
        lane_a_start_ms=START,
        lane_a_end_ms=START + DAYS * DAY_MS,
        lane_b_start_ms=START,
        lane_b_end_ms=START + DAYS * DAY_MS,
        complete_years_a=(2024,),
        complete_years_b=(2024,),
        min_eligible_weeks_per_year=5,
        min_windows_per_year=2,
        g4_min_positive_years=1,
        g5_min_positive_years=1,
        n_draws=500,
    )


def test_verdict_line_is_generated_from_booleans() -> None:
    gates = {key: True for key in report.GATE_KEYS_A}
    assert report.verdict_line(gates, report.GATE_KEYS_A, "Lane A").startswith("**Lane A: GO**")
    gates[report.GATE_KEYS_A[1]] = False
    line = report.verdict_line(gates, report.GATE_KEYS_A, "Lane A")
    assert line.startswith("**Lane A: NO-GO**") and "G2=FAIL" in line
    with pytest.raises(ValueError, match="missing"):
        report.verdict_line({report.GATE_KEYS_A[0]: True}, report.GATE_KEYS_A, "Lane A")
    with pytest.raises(ValueError, match="booleans"):
        report.verdict_line({**{key: True for key in report.GATE_KEYS_A}, report.GATE_KEYS_A[0]: "yes"}, report.GATE_KEYS_A, "Lane A")


def test_run_writes_results_and_verify_passes_then_fails_on_any_byte_difference(tmp_path: Path) -> None:
    root = tmp_path / "premium_poc"
    build_synthetic_root(root)
    config = small_config()
    summary = runner.run_all(root, code_revision="deadbeef", generated_at="2026-09-12T00:00:00Z", config=config)
    assert "lane A (V0) GO=" in summary
    results = root / "results"
    assert sorted(p.name for p in results.iterdir()) == sorted(runner.RESULT_FILES)
    v0 = json.loads((results / "carry_v0.json").read_text(encoding="utf-8"))
    assert v0["work_order"] == "WO-166" and v0["evidence_class"] == "historical"
    assert v0["paper_trading_invoked"] is False and v0["live_trading_invoked"] is False
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["paper_trading_invoked"] is False and manifest["live_trading_invoked"] is False
    v1 = json.loads((results / "carry_v1.json").read_text(encoding="utf-8"))
    assert v1["gated"] is False and "gates" not in v1
    assert set(v0["gates"]) == {"G1_lower_bound_after_haircut_positive", "G2_point_after_haircut_at_least_hurdle", "G3_drawdown_bounded_and_no_forced_liquidation", "G4_positive_in_enough_qualifying_years", "lane_a_go"}
    assert v0["pooled"]["forced_liquidations"] == 0
    assert v0["pooled"]["unverifiable_open_periods"] == 0
    assert v0["pooled"]["lower_bound_gate_level"]["block_length"] == runner.block_length_for(v0["pooled"]["eligible_weeks"])
    assert v0["manifest_sha256"] == runner.sha256_path(root / "manifest.json")
    text = (results / "report.md").read_text(encoding="utf-8")
    assert "declared assumption, not a measurement" in text
    assert "margin of the G2 quantity over the 6.0% hurdle" in text and "margin of the G1 quantity over zero" in text
    assert text.count("| G1 quantity: that lower bound") == 1  # the V1 table carries no gate labels
    assert "descriptive; V1 is never gated" in text
    v1_section = text.split("### V1 (conditional entry")[1].split("### Deribit cross-check")[0]
    assert "G1" not in v1_section and "G2" not in v1_section and "G3" not in v1_section
    assert ("**Lane A — funding carry (V0, always on): GO**" in text) == bool(v0["gates"]["lane_a_go"])
    assert runner.verify_results(root, config=config) == []

    # one flipped byte
    target = results / "vrp.json"
    payload = bytearray(target.read_bytes())
    payload[10] ^= 0x01
    target.write_bytes(bytes(payload))
    assert runner.verify_results(root, config=config) == ["byte difference: vrp.json"]
    payload[10] ^= 0x01
    target.write_bytes(bytes(payload))
    assert runner.verify_results(root, config=config) == []

    # an extra file
    (results / "notes.txt").write_text("x", encoding="utf-8")
    assert runner.verify_results(root, config=config) == ["extra file: notes.txt"]
    (results / "notes.txt").unlink()

    # a missing file
    (results / "carry_v1.json").unlink()
    assert runner.verify_results(root, config=config) == ["missing: carry_v1.json"]

    # a second run is refused without --force: one analysis pass is registered
    with pytest.raises(RuntimeError, match="one analysis pass"):
        runner.run_all(root, code_revision="deadbeef", generated_at="2026-09-12T00:00:00Z", config=config)


def test_gates_read_false_on_non_finite_operands_and_positive_funding_can_pass(tmp_path: Path) -> None:
    root = tmp_path / "premium_poc"
    build_synthetic_root(root, funding_level=0.0006)  # 0.06% per period = 65.7% annualised on notional
    config = small_config()
    runner.run_all(root, code_revision="x", generated_at="2026-09-12T00:00:00Z", config=config)
    v0 = json.loads((root / "results" / "carry_v0.json").read_text(encoding="utf-8"))
    assert v0["gates"]["G1_lower_bound_after_haircut_positive"] is True
    assert v0["gates"]["G2_point_after_haircut_at_least_hurdle"] is True
    lb = v0["pooled"]["lower_bound_gate_level"]
    assert lb["minimum"] == min(lb["cluster"], lb["stationary_block"])
    assert math.isfinite(v0["pooled"]["annualised_lower_bound_after_haircut"])
    # the year check requires enough eligible weeks: 100 days gives ~13, so with the floor at 45 the year does not qualify
    strict = runner.Config(**{**small_config().__dict__, "min_eligible_weeks_per_year": 45})
    files = runner.compute_all(root, config=strict, code_revision="x", generated_at="2026-09-12T00:00:00Z")
    strict_v0 = json.loads(files["carry_v0.json"])
    assert strict_v0["pooled"]["year_check"]["detail"]["2024"]["qualifies"] is False
    assert strict_v0["gates"]["G4_positive_in_enough_qualifying_years"] is False
    assert strict_v0["gates"]["lane_a_go"] is False


def test_results_directory_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "premium_poc"
    build_synthetic_root(root)

    def boom(*args, **kwargs):
        raise RuntimeError("render failed")

    monkeypatch.setattr(runner, "render_report", boom)
    with pytest.raises(RuntimeError, match="render failed"):
        runner.run_all(root, code_revision="x", generated_at="2026-09-12T00:00:00Z", config=small_config())
    assert not (root / "results").exists()
    assert not list(root.glob(".results-tmp-*"))


def test_manifest_mismatch_refuses_to_run(tmp_path: Path) -> None:
    root = tmp_path / "premium_poc"
    build_synthetic_root(root)
    target = root / "data" / "binance" / "BTCUSDT_funding_8h.csv"
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(RuntimeError, match="manifest verification failed"):
        runner.run_all(root, code_revision="x", generated_at="2026-09-12T00:00:00Z", config=small_config())
    assert not (root / "results").exists()


def test_missing_bar_during_an_open_position_fails_g3_and_is_counted(tmp_path: Path) -> None:
    root = tmp_path / "premium_poc"
    build_synthetic_root(root, funding_level=0.0006, drop_perp_hour=24 * 10 + 2)  # an hour that is not a boundary hour
    config = small_config()
    runner.run_all(root, code_revision="x", generated_at="2026-09-12T00:00:00Z", config=config)
    v0 = json.loads((root / "results" / "carry_v0.json").read_text(encoding="utf-8"))
    assert v0["pooled"]["unverifiable_open_periods"] == 1
    assert v0["pooled"]["forced_liquidations"] == 0
    assert v0["gates"]["G3_drawdown_bounded_and_no_forced_liquidation"] is False
    assert v0["gates"]["lane_a_go"] is False
    text = (root / "results" / "report.md").read_text(encoding="utf-8")
    assert "liquidation unverifiable; G3 requires 0) | 1 |" in text


def test_g3_reads_the_drawdown_magnitude_and_fails_above_twenty_percent(tmp_path: Path) -> None:
    root = tmp_path / "premium_poc"
    build_synthetic_root(root, funding_level=-0.004)  # -0.4% per period bleeds ~40% of capital over 100 days
    config = small_config()
    runner.run_all(root, code_revision="x", generated_at="2026-09-12T00:00:00Z", config=config)
    v0 = json.loads((root / "results" / "carry_v0.json").read_text(encoding="utf-8"))
    assert v0["pooled"]["max_drawdown_all_weeks"] < -0.20  # the helper reports a negative peak-to-trough ratio
    assert v0["pooled"]["forced_liquidations"] == 0 and v0["pooled"]["unverifiable_open_periods"] == 0
    assert v0["gates"]["G3_drawdown_bounded_and_no_forced_liquidation"] is False
    assert "G3=FAIL" in (root / "results" / "report.md").read_text(encoding="utf-8")


def test_yearly_sums_use_eligible_weeks_only() -> None:
    weekly = pd.DataFrame(
        {
            "iso_year": [2024, 2024, 2024],
            "iso_week": [2, 3, 4],
            "return_on_capital": [0.01, 0.50, -0.02],
            "eligible": [True, False, True],
        }
    )
    assert runner._yearly_sums(weekly, "return_on_capital") == {"2024": pytest.approx(-0.01)}


def test_run_re_validates_committed_inputs(tmp_path: Path) -> None:
    root = tmp_path / "premium_poc"
    build_synthetic_root(root)
    target = root / "data" / "binance" / "BTCUSDT_perp_1h.csv"
    frame = pd.read_csv(target)
    dup = pd.concat([frame, frame.iloc[[100]].assign(high=frame["high"].iloc[100] * 3)], ignore_index=True).sort_values("open_time", kind="stable")
    payload = dup.to_csv(index=False, lineterminator="\n").encode("utf-8")
    target.write_bytes(payload)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["entries"]:
        if entry["path"].endswith("BTCUSDT_perp_1h.csv"):
            entry["sha256"] = sha256_bytes(payload)
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="duplicated open_time"):
        runner.run_all(root, code_revision="x", generated_at="2026-09-12T00:00:00Z", config=small_config())
    assert not (root / "results").exists()


def test_binance_manifest_entries_must_carry_verified_upstream_checksums(tmp_path: Path) -> None:
    from premium_research.manifest import verify_manifest

    root = tmp_path / "premium_poc"
    build_synthetic_root(root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert verify_manifest(root / "manifest.json", root) == []
    for entry in manifest["entries"]:
        if entry["path"].startswith("data/binance/"):
            entry["upstream_checksums"] = {}
            entry["checksum_verified"] = False
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    failures = verify_manifest(root / "manifest.json", root)
    assert len(failures) == 6 and all("binance entry without verified upstream checksums" in f for f in failures)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_wo166_results_still_verify_under_the_default_scope() -> None:
    root = REPO_ROOT / "research" / "premium_poc"
    assert (root / "results" / "carry_v0.json").is_file()
    assert runner.verify_results(root) == []


def _scope_config(base: runner.Config, **overrides) -> runner.Config:
    return runner.Config(**{**base.__dict__, **overrides})


def test_wo167_config_writes_its_own_results_directory_and_records_the_scope(tmp_path: Path) -> None:
    root = tmp_path / "premium_poc"
    build_synthetic_root(root, funding_level=0.0006, drop_spot_hour=24 * 10 + 7)  # the hour ending at a boundary, spot only
    wo166 = _scope_config(small_config())
    wo167 = _scope_config(small_config(), unverifiable_scope="perp", work_order="WO-167", results_dir="results_wo167")
    runner.run_all(root, code_revision="x", generated_at="2026-09-13T00:00:00Z", config=wo166)
    runner.run_all(root, code_revision="x", generated_at="2026-09-13T00:00:00Z", config=wo167)
    a = json.loads((root / "results" / "carry_v0.json").read_text(encoding="utf-8"))
    b = json.loads((root / "results_wo167" / "carry_v0.json").read_text(encoding="utf-8"))
    assert a["work_order"] == "WO-166" and "unverifiable_scope" not in a  # WO-166's JSON gains no key
    assert b["work_order"] == "WO-167" and b["unverifiable_scope"] == "perp"
    assert a["pooled"]["unverifiable_open_periods"] == 2 and a["gates"]["G3_drawdown_bounded_and_no_forced_liquidation"] is False
    assert b["pooled"]["unverifiable_open_periods"] == 0 and b["gates"]["G3_drawdown_bounded_and_no_forced_liquidation"] is True
    for key in ("mean_weekly_return_on_capital", "annualised_return_on_capital", "annualised_after_haircut", "annualised_lower_bound", "annualised_lower_bound_after_haircut", "yearly_return_on_capital", "yearly_eligible_weeks", "eligible_weeks", "max_drawdown_all_weeks", "sharpe_weekly_annualised", "forced_liquidations"):
        assert a["pooled"][key] == b["pooled"][key], key
    for gate in ("G1_lower_bound_after_haircut_positive", "G2_point_after_haircut_at_least_hurdle", "G4_positive_in_enough_qualifying_years"):
        assert a["gates"][gate] == b["gates"][gate], gate
    assert "rejected_open_periods" not in a["pooled"] and b["pooled"]["rejected_open_periods"] == 2  # the either-scope count travels with the perp-scope result
    assert "rejected for an absent bar on either leg" in (root / "results_wo167" / "report.md").read_text(encoding="utf-8")
    assert "perpetual-side data absent" in (root / "results_wo167" / "report.md").read_text(encoding="utf-8")
    assert sorted(p.name for p in (root / "results").iterdir()) == sorted(runner.RESULT_FILES)
    text = (root / "results_wo167" / "report.md").read_text(encoding="utf-8")
    assert text.startswith("# WO-167 proof-of-concept results") and "**perp**" in text
    assert "Completeness scope" not in (root / "results" / "report.md").read_text(encoding="utf-8")
    assert runner.verify_results(root, config=wo166) == []
    assert runner.verify_results(root, config=wo167) == []


def test_run_refuses_a_second_wo167_pass(tmp_path: Path) -> None:
    root = tmp_path / "premium_poc"
    build_synthetic_root(root)
    wo167 = _scope_config(small_config(), unverifiable_scope="perp", work_order="WO-167", results_dir="results_wo167")
    runner.run_all(root, code_revision="x", generated_at="2026-09-13T00:00:00Z", config=wo167)
    with pytest.raises(RuntimeError, match="one analysis pass"):
        runner.run_all(root, code_revision="x", generated_at="2026-09-13T00:00:00Z", config=wo167)


def test_verify_results_selector_recomputes_under_the_right_scope(tmp_path: Path) -> None:
    root = tmp_path / "premium_poc"
    build_synthetic_root(root, funding_level=0.0006, drop_spot_hour=24 * 10 + 7)
    wo167 = _scope_config(small_config(), unverifiable_scope="perp", work_order="WO-167", results_dir="results_wo167")
    runner.run_all(root, code_revision="x", generated_at="2026-09-13T00:00:00Z", config=wo167)
    assert runner.verify_results(root, config=wo167) == []
    wrong_scope = _scope_config(small_config(), unverifiable_scope="either", work_order="WO-167", results_dir="results_wo167")
    failures = runner.verify_results(root, config=wrong_scope)
    assert failures and all(f.startswith("byte difference") for f in failures)
    with pytest.raises(ValueError, match="unknown unverifiable_scope"):
        runner.Config(unverifiable_scope="spot")


# Differences the WO-167 pass is registered to produce against WO-166's committed results; anything else is a defect (WO-167 A11).
WO167_PERMITTED_DIFFERENCES = {
    "code_revision", "generated_at", "work_order", "unverifiable_scope",
    "gates.G3_drawdown_bounded_and_no_forced_liquidation", "gates.lane_a_go",
    "pooled.unverifiable_open_periods", "pooled.rejected_open_periods",
    "per_asset.BTCUSDT.unverifiable_open_periods", "per_asset.ETHUSDT.unverifiable_open_periods",
    "per_asset.BTCUSDT.rejected_open_periods", "per_asset.ETHUSDT.rejected_open_periods",
}


def _flatten(payload, prefix=""):
    out = {}
    for key, value in payload.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flatten(value, name + "."))
        else:
            out[name] = value
    return out


def test_committed_wo167_results_differ_from_wo166_only_where_registered() -> None:
    root = REPO_ROOT / "research" / "premium_poc"
    for name in ("carry_v0.json", "carry_v1.json", "vrp.json"):
        a = _flatten(json.loads((root / "results" / name).read_text(encoding="utf-8")))
        b = _flatten(json.loads((root / "results_wo167" / name).read_text(encoding="utf-8")))
        differing = {k for k in set(a) | set(b) if a.get(k, "<absent>") != b.get(k, "<absent>")}
        assert differing <= WO167_PERMITTED_DIFFERENCES, sorted(differing - WO167_PERMITTED_DIFFERENCES)
        assert a["manifest_sha256"] == b["manifest_sha256"]
    v0 = json.loads((root / "results_wo167" / "carry_v0.json").read_text(encoding="utf-8"))
    assert v0["work_order"] == "WO-167" and v0["unverifiable_scope"] == "perp"
    assert v0["pooled"]["unverifiable_open_periods"] == 0 and v0["pooled"]["rejected_open_periods"] == 16


def test_scope_disclosure_is_keyed_on_the_scope_not_only_the_work_order(tmp_path: Path) -> None:
    # A Python-API configuration that narrows the scope but keeps WO-166's label must still disclose the scope.
    root = tmp_path / "premium_poc"
    build_synthetic_root(root, funding_level=0.0006, drop_spot_hour=24 * 10 + 7)
    odd = _scope_config(small_config(), unverifiable_scope="perp", work_order="WO-166", results_dir="results_odd")
    assert odd.discloses_scope is True and small_config().discloses_scope is False
    runner.run_all(root, code_revision="x", generated_at="2026-09-13T00:00:00Z", config=odd)
    payload = json.loads((root / "results_odd" / "carry_v0.json").read_text(encoding="utf-8"))
    assert payload["unverifiable_scope"] == "perp" and payload["pooled"]["rejected_open_periods"] == 2


def test_run_refuses_a_dirty_estimator_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import subprocess

    from premium_research import cli

    repo = tmp_path / "repo"
    (repo / "src" / "premium_research").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "base"], check=True)
    assert cli.uncommitted_paths(repo, "src/premium_research") == []
    (repo / "src" / "premium_research" / "carry.py").write_text("x = 1\n", encoding="utf-8")
    assert cli.uncommitted_paths(repo, "src/premium_research") == ["src/premium_research/carry.py"]
    monkeypatch.setattr(cli, "REPO_ROOT", repo)
    calls: list[str] = []
    monkeypatch.setattr("premium_research.runner.run_all", lambda *a, **k: calls.append("ran"))
    assert cli.main(["--root", str(tmp_path / "nowhere"), "run"]) == 2
    assert "uncommitted changes" in capsys.readouterr().err and calls == []
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "clean"], check=True)
    assert cli.uncommitted_paths(repo, "src/premium_research") == []
