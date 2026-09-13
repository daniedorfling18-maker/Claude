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


# ---------------------------------------------------------------------------- WO-170

WO170_SYNTHETIC_KWARGS = {"funding_level": 0.0006, "drop_spot_hour": 24 * 10 + 7}
CLOCK = "2026-09-13T00:00:00Z"
# sha256 of every file a WO-167 run and a WO-166 run write on the synthetic tree above with clock CLOCK and revision "x",
# computed once at 70a8971 (before WO-170's switches existed) so the switches are proven to change nothing under their defaults.
WO167_SYNTHETIC_SHA256 = {
    "carry_v0.json": "7d17caa8316c36e7ed039bbf6a8d0df72672bc1736bd066e4bd490ddc7e4d8d0",
    "carry_v1.json": "7617fcbff4538b7cac8d6fcc46fdf5df16609d5d2f6d34d66ac212446c025dc6",
    "report.md": "8a197805335279b70c58df1ed7fa9b08c9a00dbe2c5475b55b29e3c2cac203c9",
    "vrp.json": "d4f56b7376184b5f2b3c991843a98df110aebbbcaa9dbfa6861393befd63351c",
}
WO166_SYNTHETIC_SHA256 = {
    "carry_v0.json": "60c0d0c20027f1eea4e59616ba598f59921447eb1229eafbfa350637bb800813",
    "carry_v1.json": "45c93125e445f189c6917c3f8cf8395ab64a67f586b83608633b36b4b0f4a24b",
    "report.md": "f7aa4948294c655f96a99bdd066ae52164e6f823f00e56bb66e9ba8cf1ce34ef",
    "vrp.json": "29fdfac11b12a31fadbcd342a3448dcafe283ed6609e01fbf4f9ac78db84b27c",
}
G124_LEAVES = (
    "pooled.mean_weekly_return_on_capital", "pooled.annualised_return_on_capital", "pooled.annualised_after_haircut", "pooled.annualised_lower_bound",
    "pooled.annualised_lower_bound_after_haircut", "pooled.eligible_weeks", "pooled.max_drawdown_all_weeks",
    "gates.G1_lower_bound_after_haircut_positive", "gates.G2_point_after_haircut_at_least_hurdle", "gates.G4_positive_in_enough_qualifying_years",
)


def _wo167_and_wo170(root: Path) -> tuple[runner.Config, runner.Config]:
    wo167 = _scope_config(small_config(), unverifiable_scope="perp", work_order="WO-167", results_dir="results_wo167")
    wo170 = _scope_config(small_config(), unverifiable_scope="perp", work_order="WO-170", results_dir="results_wo170", drawdown_basis="nav", rv_alignment="return_intervals", result_files=runner.WO170_RESULT_FILES)
    return wo167, wo170


def _sha(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_wo166_and_wo167_results_still_verify_under_their_defaults() -> None:
    root = REPO_ROOT / "research" / "premium_poc"
    assert runner.verify_results(root) == []
    assert runner.verify_results(root, config=runner.WO167_CONFIG) == []


def test_wo170_config_differs_from_wo167_only_where_the_tables_allow(tmp_path: Path) -> None:
    root = tmp_path / "premium_poc"
    build_synthetic_root(root, **WO170_SYNTHETIC_KWARGS)
    wo167, wo170 = _wo167_and_wo170(root)
    runner.run_all(root, code_revision="x", generated_at=CLOCK, config=wo167)
    runner.run_all(root, code_revision="x", generated_at=CLOCK, config=wo170)
    recon = json.loads((root / "results_wo170" / "reconciliation.json").read_text(encoding="utf-8"))
    tables = runner._reconciliation_tables(wo170)
    for name in ("carry_v0.json", "carry_v1.json", "vrp.json"):
        old = runner._flatten(json.loads((root / "results_wo167" / name).read_text(encoding="utf-8")))
        new = runner._flatten(json.loads((root / "results_wo170" / name).read_text(encoding="utf-8")))
        differing = sorted(k for k in set(old) | set(new) if not (k in old and k in new and runner._leaf_equal(old[k], new[k])))
        listed = sorted(d["path"] for d in recon["files"][name]["differences"])
        assert differing == listed, name
        for path in differing:
            assert runner._match_reason(path, tables[name]) == next(d["reason"] for d in recon["files"][name]["differences"] if d["path"] == path)
        if name != "vrp.json":
            for leaf in G124_LEAVES:
                if leaf.startswith("gates.") and name == "carry_v1.json":
                    continue  # V1 is descriptive and never gated
                assert runner._leaf_equal(old[leaf], new[leaf]), leaf
            for leaf in (k for k in old if k.startswith(("pooled.bootstrap.", "pooled.yearly_return_on_capital.", "pooled.year_check.", "fee_sensitivity_2x."))):
                assert runner._leaf_equal(old[leaf], new[leaf]), leaf
        else:
            for leaf in (k for k in old if k.startswith("gates.") or k in ("pooled.positive_complete_years", "pooled.windows_accepted", "pooled.windows_total")):
                assert runner._leaf_equal(old[leaf], new[leaf]), leaf
    v0 = json.loads((root / "results_wo170" / "carry_v0.json").read_text(encoding="utf-8"))
    assert v0["gates"]["G3_drawdown_bounded_and_no_forced_liquidation"] is True and v0["pooled"]["unverifiable_open_periods"] == 0


def test_reconciliation_aborts_on_an_unexplained_leaf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "premium_poc"
    build_synthetic_root(root, **WO170_SYNTHETIC_KWARGS)
    wo167, wo170 = _wo167_and_wo170(root)
    runner.run_all(root, code_revision="x", generated_at=CLOCK, config=wo167)
    original = runner._lane_a_full

    def extra_key(*args, **kwargs):
        result, frames = original(*args, **kwargs)
        result["pooled"]["surprise"] = 1.0
        return result, frames

    monkeypatch.setattr(runner, "_lane_a_full", extra_key)
    with pytest.raises(RuntimeError, match="pooled.surprise"):
        runner.run_all(root, code_revision="x", generated_at=CLOCK, config=wo170)
    assert not (root / "results_wo170").exists()

    def removed_key(*args, **kwargs):
        result, frames = original(*args, **kwargs)
        result["pooled"].pop("rebalances")
        return result, frames

    monkeypatch.setattr(runner, "_lane_a_full", removed_key)
    with pytest.raises(RuntimeError, match="absent from this pass"):
        runner.run_all(root, code_revision="x", generated_at=CLOCK, config=wo170)
    assert not (root / "results_wo170").exists()


def _weekly_and_nav(n_eligible: int, *, ineligible_at: tuple[int, ...] = (), value: float = 0.002) -> tuple[pd.DataFrame, pd.DataFrame]:
    """A pooled weekly frame of ``n_eligible`` eligible weeks (plus ineligible ones) and a flat pooled NAV frame on the same weeks."""
    weeks = n_eligible + len(ineligible_at)
    monday = 1704067200000
    rows = []
    nav_rows = []
    nav = 3.0
    for i in range(weeks):
        week_end = monday + (i + 1) * 7 * 24 * 3_600_000
        eligible = i not in ineligible_at
        rows.append({"iso_year": 2024 if week_end < 1735603200000 else 2025, "iso_week": i + 1, "week_end_ms": week_end, "return_on_capital": value if eligible else 0.0, "eligible": eligible})
        for k in range(21):
            boundary = week_end - (20 - k) * 8 * 3_600_000
            nav_rows.append({"boundary_ms": boundary, "nav_pooled": nav, "iso_year": rows[-1]["iso_year"], "iso_week": i + 1})
    weekly = pd.DataFrame(rows)
    nav_frame = pd.DataFrame(nav_rows)
    return weekly, nav_frame


def test_recent_period_cuts_use_the_last_eligible_weeks_only() -> None:
    weekly, nav_frame = _weekly_and_nav(60, ineligible_at=(10, 40))
    out = runner._recent_period(weekly, nav_frame, small_config())
    assert out["last_52"]["state"] == "ok" and out["last_52"]["weeks"] == 52
    assert abs(out["last_52"]["mean_weekly_return_on_capital"] - 0.002) < 1e-12 and abs(out["last_52"]["annualised_simple"] - 0.104) < 1e-12
    assert out["last_52"]["max_drawdown_nav"] == 0.0
    assert out["rolling_52"]["state"] == "ok" and out["rolling_52"]["windows"] == 9
    assert abs(out["rolling_52"]["min_annualised_simple"] - 0.104) < 1e-12 and abs(out["rolling_52"]["max_annualised_simple"] - 0.104) < 1e-12 and abs(out["rolling_52"]["last_annualised_simple"] - 0.104) < 1e-12
    assert out["last_104"]["state"] == "insufficient_weeks" and math.isnan(out["last_104"]["annualised_simple"])
    weekly, nav_frame = _weekly_and_nav(40)
    out = runner._recent_period(weekly, nav_frame, small_config())
    assert out["last_52"]["state"] == out["last_104"]["state"] == out["rolling_52"]["state"] == "insufficient_weeks"


def test_bases_block_and_return_basis_present_under_wo170_only(tmp_path: Path) -> None:
    root = tmp_path / "premium_poc"
    build_synthetic_root(root, **WO170_SYNTHETIC_KWARGS)
    wo167, wo170 = _wo167_and_wo170(root)
    runner.run_all(root, code_revision="x", generated_at=CLOCK, config=wo167)
    runner.run_all(root, code_revision="x", generated_at=CLOCK, config=wo170)
    runner.run_all(root, code_revision="x", generated_at=CLOCK, config=small_config())
    a = json.loads((root / "results" / "carry_v0.json").read_text(encoding="utf-8"))
    b = json.loads((root / "results_wo167" / "carry_v0.json").read_text(encoding="utf-8"))
    c = json.loads((root / "results_wo170" / "carry_v0.json").read_text(encoding="utf-8"))
    for key in ("bases", "return_basis", "drawdown_basis", "rv_alignment"):
        assert key not in a and key not in b and key in c, key
    assert c["bases"] == runner.BASES and c["return_basis"] == "simple_on_inception_capital" and c["drawdown_basis"] == "nav" and c["rv_alignment"] == "return_intervals"
    assert small_config().discloses_bases is False and wo167.discloses_bases is False and wo170.discloses_bases is True
    text170 = (root / "results_wo170" / "report.md").read_text(encoding="utf-8")
    assert "What the prices and cash flows are" in text170 and "NAV path over every boundary (peak-to-trough, negative; G3 reads its magnitude)" in text170
    for name in ("results", "results_wo167"):
        text = (root / name / "report.md").read_text(encoding="utf-8")
        assert "What the prices and cash flows are" not in text and "NAV path" not in text


def test_result_files_are_per_config_and_verified(tmp_path: Path) -> None:
    root = tmp_path / "premium_poc"
    build_synthetic_root(root, **WO170_SYNTHETIC_KWARGS)
    wo167, wo170 = _wo167_and_wo170(root)
    runner.run_all(root, code_revision="x", generated_at=CLOCK, config=wo167)
    runner.run_all(root, code_revision="x", generated_at=CLOCK, config=wo170)
    assert sorted(p.name for p in (root / "results_wo170").iterdir()) == sorted(runner.WO170_RESULT_FILES) and len(runner.WO170_RESULT_FILES) == 9
    v0 = json.loads((root / "results_wo170" / "carry_v0.json").read_text(encoding="utf-8"))
    assert set(v0["ledger_files"]) == {"ledger_BTCUSDT_V0.csv", "ledger_ETHUSDT_V0.csv"}
    for name, digest in v0["ledger_files"].items():
        assert _sha(root / "results_wo170" / name) == digest
    ledger = pd.read_csv(root / "results_wo170" / "ledger_BTCUSDT_V0.csv")
    assert list(ledger.columns) == list(runner.LEDGER_COLUMNS)
    assert (np.abs(ledger["nav"] - (ledger["cash"] + ledger["margin"] + ledger["spot_value"])) <= 1e-9).all()
    assert runner.verify_results(root, config=wo170) == []
    (root / "results_wo170" / "ledger_ETHUSDT_V1.csv").unlink()
    assert runner.verify_results(root, config=wo170) == ["missing: ledger_ETHUSDT_V1.csv"]
    runner.run_all(root, code_revision="x", generated_at=CLOCK, config=wo170, force=True)
    (root / "results_wo170" / "stray.txt").write_text("x", encoding="utf-8")
    assert runner.verify_results(root, config=wo170) == ["extra file: stray.txt"]
    runner.run_all(root, code_revision="x", generated_at=CLOCK, config=small_config())
    assert sorted(p.name for p in (root / "results").iterdir()) == sorted(runner.RESULT_FILES)


def test_reconciliation_aborts_when_wo167_results_are_absent(tmp_path: Path) -> None:
    root = tmp_path / "premium_poc"
    build_synthetic_root(root, **WO170_SYNTHETIC_KWARGS)
    _, wo170 = _wo167_and_wo170(root)
    with pytest.raises(RuntimeError, match="reconciliation input missing"):
        runner.run_all(root, code_revision="x", generated_at=CLOCK, config=wo170)
    assert not (root / "results_wo170").exists()


def test_reconciliation_nan_and_removed_leaves(tmp_path: Path) -> None:
    nan = float("nan")
    assert runner._leaf_equal(nan, nan) and not runner._leaf_equal(nan, 1.0) and not runner._leaf_equal(1, 1.0)
    tables = runner._reconciliation_tables(small_config())
    assert runner._match_reason("pooled.recent_period.last_52.annualised_simple", tables["carry_v0.json"]) == "recent_period_cut"
    assert runner._match_reason("generated_at", tables["carry_v0.json"]) == "clock_or_revision"
    with pytest.raises(RuntimeError, match="unexplained difference"):
        runner._match_reason("pooled.annualised_after_haircut", tables["carry_v0.json"])
    root = tmp_path / "premium_poc"
    (root / "results_wo167").mkdir(parents=True)
    base = {"pooled": {"x": nan, "annualised_after_haircut": 0.1, "recent_period": {"a": nan}}, "generated_at": "t"}
    for name in ("carry_v0.json", "carry_v1.json", "vrp.json"):
        (root / "results_wo167" / name).write_text(json.dumps(base), encoding="utf-8")
    same = {"carry_v0.json": {"pooled": {"x": nan, "annualised_after_haircut": 0.1, "recent_period": {"a": 0.5}}, "generated_at": "u"}}
    same["carry_v1.json"] = same["carry_v0.json"]
    same["vrp.json"] = {"pooled": {"x": nan, "annualised_after_haircut": 0.1, "recent_period": {"a": nan}}, "generated_at": "u"}
    out = runner.reconcile(root, small_config(), same)
    assert [d["path"] for d in out["files"]["carry_v0.json"]["differences"]] == ["generated_at", "pooled.recent_period.a"]
    assert out["files"]["vrp.json"]["differing_leaves"] == 1  # only the clock; two NaN leaves compare equal
    bad = {**same, "vrp.json": {"pooled": {"x": 1.0, "annualised_after_haircut": 0.1, "recent_period": {"a": nan}}, "generated_at": "u"}}
    with pytest.raises(RuntimeError, match="unexplained difference at pooled.x"):
        runner.reconcile(root, small_config(), bad)
    removed = {**same, "vrp.json": {"pooled": {"annualised_after_haircut": 0.1, "recent_period": {"a": nan}}, "generated_at": "u"}}
    with pytest.raises(RuntimeError, match="absent from this pass"):
        runner.reconcile(root, small_config(), removed)


def test_wo167_bytes_unchanged_by_the_new_switches_on_a_synthetic_tree(tmp_path: Path) -> None:
    root = tmp_path / "premium_poc"
    build_synthetic_root(root, **WO170_SYNTHETIC_KWARGS)
    wo167, _ = _wo167_and_wo170(root)
    runner.run_all(root, code_revision="x", generated_at=CLOCK, config=wo167)
    assert {p.name: _sha(p) for p in (root / "results_wo167").iterdir()} == WO167_SYNTHETIC_SHA256
    runner.run_all(root, code_revision="x", generated_at=CLOCK, config=small_config())
    assert {p.name: _sha(p) for p in (root / "results").iterdir()} == WO166_SYNTHETIC_SHA256
