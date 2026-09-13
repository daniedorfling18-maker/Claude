"""The edge-strategy-search promotion gate must not promote overfit longshot rules.

A rule whose backtest ROI is carried by one lucky deep-longshot market looks great on the
point estimate but is statistically worthless. These tests cover the market-clustered ROI
bootstrap that the hardened gate relies on to tell the two apart.
"""
import math

from polymarket_predictive_engine.strategy_search import (
    _evaluate_rule,
    _market_clustered_roi_ci,
)
from polymarket_predictive_engine.utils import parse_timestamp


def _row(market: str, price: float, target: int, profit: float, *, when: str = "2026-07-01T00:00:00Z", fee: float = 0.0, token: str = "t") -> dict:
    return {
        "_market_key": market,
        "_price": price,
        "_target": target,
        "_profit_per_usdc": profit,
        "_gross_profit_per_usdc": profit + fee,
        "_fee_per_dollar": fee,
        "_timestamp": parse_timestamp(when),
        "token_id": token,
        "_family": "worldcup",
        "_outcome": "yes",
    }


def _longshot_mirage_rows() -> list[dict]:
    # Six markets; ROI is carried entirely by one 1-cent winner, the rest lose.
    rows = [_row("m0", 0.01, 1, (1 - 0.01) / 0.01)]              # +99 on a single market
    rows += [_row(f"m{i}", 0.01, 0, -1.0) for i in range(1, 6)]  # five losing markets
    return rows


def _broad_consistent_rows() -> list[dict]:
    # Eight independent markets, each a modest, consistent winner at a tradeable price.
    return [_row(f"k{i}", 0.5, 1, 0.4) for i in range(8)]


def test_concentrated_longshot_roi_is_not_significant():
    point, lo, hi = _market_clustered_roi_ci(_longshot_mirage_rows())
    assert point > 1.0                # point ROI looks enormous
    assert math.isfinite(lo)
    assert lo < 0.0                   # ...but the market-clustered CI lower bound is negative


def test_broad_consistent_edge_is_significant():
    point, lo, hi = _market_clustered_roi_ci(_broad_consistent_rows())
    assert point > 0.0
    assert lo > 0.0                   # consistent across markets -> lower bound stays positive


def test_single_market_has_no_confidence_interval():
    point, lo, hi = _market_clustered_roi_ci([_row("only", 0.5, 1, 0.4)])
    assert point == 0.4
    assert math.isnan(lo) and math.isnan(hi)   # one market -> cannot form a CI


def test_evaluate_rule_surfaces_holdout_ci_and_flags_the_mirage():
    rows = _longshot_mirage_rows()
    holdout = {str(r["_market_key"]) for r in rows}
    result = _evaluate_rule(
        rule_family="family_price",
        rule_value="worldcup|price=0.00-0.02",
        rule_rows=rows,
        train_markets=set(),
        validation_markets=holdout,
        final_markets=set(),
        stake_usdc=1.0,
    )
    assert result["holdout_markets"] == 6
    assert result["holdout_roi"] > 1.0                  # point ROI is huge
    assert result["holdout_roi_ci_low"] < 0.0           # the gate would reject this (CI low < 0)
    assert result["avg_entry_price"] < 0.05             # ...and it is below the entry-price floor too


# --- WO-171: chronological three-way split, purging, family registry, correction ---------

import json
from pathlib import Path

import pytest
import yaml

from polymarket_predictive_engine import strategy_search as ss
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.utils import read_csv_rows, write_csv

DAY = "2026-07-{:02d}T12:00:00Z"


def _config(tmp_path: Path):
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def _feature_row(market: str, token: str, when: str, price: float, target: int, *, category: str = "sports") -> dict:
    return {
        "market_id": market,
        "token_id": token,
        "prediction_timestamp": when,
        "executable_buy_price": price,
        "implied_probability": price,
        "target": target,
        "outcome": "yes",
        "market_slug": f"slug-{market}",
        "category": category,
        "fees_enabled": "true",
        "time_to_close_hours": 24,
    }


def _write_corpus(cfg, rows: list[dict], *, resolution_days: dict[str, int] | None = None, horizons: str = "all_valid") -> None:
    """Write the two joined inputs the search reads, plus the label availability times."""
    write_csv(
        cfg.output_root / "polymarket_training" / "features_v2.csv",
        rows,
        fieldnames=["market_id", "token_id", "prediction_timestamp", "executable_buy_price", "implied_probability", "market_slug", "category", "fees_enabled", "time_to_close_hours"],
    )
    labels = []
    for row in rows:
        day = (resolution_days or {}).get(row["market_id"])
        labels.append(
            {
                "market_id": row["market_id"],
                "token_id": row["token_id"],
                "prediction_timestamp": row["prediction_timestamp"],
                "horizon": horizons,
                "target": row["target"],
                "outcome": row["outcome"],
                "resolution_time": DAY.format(day) if day is not None else "",
                "close_time": row["prediction_timestamp"],
            }
        )
    write_csv(
        cfg.output_root / "polymarket_training" / "labels.csv",
        labels,
        fieldnames=["market_id", "token_id", "prediction_timestamp", "horizon", "target", "outcome", "resolution_time", "close_time"],
    )


def _corpus(markets: int, rows_per_market: int = 4, price: float = 0.5, target: int = 1) -> list[dict]:
    rows = []
    for index in range(markets):
        for k in range(rows_per_market):
            rows.append(_feature_row(f"m{index:02d}", f"t{index:02d}_{k}", DAY.format(index + 1), price, target))
    return rows


def test_three_way_split_is_chronological_by_market(tmp_path: Path):
    """WO-171 item 1: the earliest half trains, the next quarter selects, the last quarter
    confirms. Below four markets no segment can be formed and nothing is evaluated."""
    availability = {f"m{i:02d}": ss.parse_timestamp(DAY.format(i + 1)) for i in range(12)}
    rows, dropped = [], 0
    for index in range(12):
        rows.append({"_market_key": f"m{index:02d}", "_timestamp": ss.parse_timestamp(DAY.format(index + 1))})
    train, validation, final, accounting = ss._split_markets_three_way(rows, availability)
    assert sorted(train) == [f"m{i:02d}" for i in range(6)]
    assert sorted(validation) == [f"m{i:02d}" for i in range(6, 9)]
    assert sorted(final) == [f"m{i:02d}" for i in range(9, 12)]
    assert accounting["split_counts"] == {"train": 6, "validation": 3, "final": 3}
    assert dropped == 0

    cfg = _config(tmp_path)
    _write_corpus(cfg, _corpus(3))
    payload = ss.run_edge_strategy_search(cfg)
    assert payload["status"] == "insufficient_markets"
    assert read_csv_rows(cfg.governance_root / "edge_strategy_search.csv") == []
    assert read_csv_rows(cfg.governance_root / "edge_strategy_search_family.csv") == []


def test_train_market_whose_label_arrives_after_validation_starts_is_purged(tmp_path: Path):
    """WO-171 item 2: the embargo is the availability time itself. A market whose label
    became knowable only at or after the next segment's first prediction is purged; a market
    with no availability time at all is dropped from every segment, fail-closed."""
    stamps = {f"m{i:02d}": ss.parse_timestamp(DAY.format(i + 1)) for i in range(12)}
    rows = [{"_market_key": market, "_timestamp": stamp} for market, stamp in stamps.items()]
    validation_start = stamps["m06"]

    late = dict(stamps)
    late["m00"] = validation_start  # exactly at the boundary: purged
    train, _, _, accounting = ss._split_markets_three_way(rows, late)
    assert "m00" not in train
    assert accounting["purged_train_overlaps_validation"] == 1

    early = dict(stamps)
    early["m00"] = validation_start - ss.datetime.resolution  # one tick before: kept
    train, _, _, accounting = ss._split_markets_three_way(rows, early)
    assert "m00" in train
    assert accounting["purged_train_overlaps_validation"] == 0

    missing = {market: stamp for market, stamp in stamps.items() if market != "m00"}
    train, validation, final, accounting = ss._split_markets_three_way(rows, missing)
    assert "m00" not in train | validation | final
    assert accounting["purged_no_availability_time"] == 1
    assert accounting["markets_ordered"] == 11
    assert accounting["availability_basis"] == "venue_resolution_time_else_close_time"


def test_selection_reads_validation_only_and_final_never_feeds_promotable():
    """WO-171 item 3: a rule that wins on validation and loses every final row is still
    promotable, and swapping two rules' final rows changes neither `promotable` nor the rank.
    The final segment confirms; it never selects."""
    train_rows = [_row(f"d{i}", 0.5, 1, 0.4, when=DAY.format(1)) for i in range(30)]
    for i, row in enumerate(train_rows):
        row["_market_key"] = f"d{i % 6}"
    validation_rows = [_row(f"v{i % 4}", 0.5, 1, 0.975, when=DAY.format(7), fee=0.025) for i in range(6)]
    final_rows = [_row(f"f{i % 3}", 0.5, 0, -1.025, when=DAY.format(10), fee=0.025) for i in range(6)]
    result = ss._evaluate_rule(
        rule_family="family",
        rule_value="worldcup",
        rule_rows=train_rows + validation_rows + final_rows,
        train_markets={f"d{i}" for i in range(6)},
        validation_markets={f"v{i}" for i in range(4)},
        final_markets={f"f{i}" for i in range(3)},
        stake_usdc=1.0,
        roi_bootstrap_samples=200,
    )
    assert result["validation_rows"] == 6 and result["validation_markets"] == 4
    assert result["validation_roi"] == pytest.approx(0.975)
    assert result["validation_roi_ci_low"] > 0.02
    assert result["final_roi"] < 0
    assert result["final_evidence_class"] == "retrospective"
    assert "feeds no selection and no gate" in result["final_segment_note"]
    # Every selection quantity is computed without reading a final row.
    swapped = ss._evaluate_rule(
        rule_family="family",
        rule_value="worldcup",
        rule_rows=train_rows + validation_rows + [_row(f"f{i % 3}", 0.5, 1, 0.975, when=DAY.format(10), fee=0.025) for i in range(6)],
        train_markets={f"d{i}" for i in range(6)},
        validation_markets={f"v{i}" for i in range(4)},
        final_markets={f"f{i}" for i in range(3)},
        stake_usdc=1.0,
        roi_bootstrap_samples=200,
    )
    for key in ("train_roi", "validation_roi", "validation_roi_ci_low", "validation_p_value", "turnover_rows_per_market_day"):
        assert swapped[key] == result[key]
    assert swapped["final_roi"] != result["final_roi"]


def test_bh_fdr_hand_example():
    """WO-171 item 5: Benjamini-Hochberg at q = 0.10, by hand."""
    assert ss._bh_significant([0.001, 0.02, 0.04, 0.5], q=0.10) == [True, True, True, False]
    assert ss._bh_significant([0.001, 0.02, 0.08, 0.5], q=0.10) == [True, True, False, False]
    # A non-finite p-value is 1.0 and still counts in m, so it makes the correction stricter.
    assert ss._bh_significant([0.001, float("nan")], q=0.10) == [True, False]
    assert ss._bh_significant([]) == []


def test_bootstrap_p_value_is_one_sided_against_the_hurdle_and_floored():
    """WO-171 item 5: the share of resamples at or below the hurdle, floored at one draw."""
    assert ss._bootstrap_p_value([0.5] * 2000, 0.02) == pytest.approx(1.0 / 2000)
    assert ss._bootstrap_p_value([0.01] * 2000, 0.02) == 1.0
    assert ss._bootstrap_p_value([0.02] * 2000, 0.02) == 1.0  # at the hurdle is not above it
    assert ss._bootstrap_p_value([], 0.02) == 1.0


def test_profit_is_net_of_the_taker_fee():
    """WO-171 item 6: the entry fee is charged whether the position wins or loses."""
    row = {"category": "sports", "fees_enabled": "true", "fee_schedule_rate": 0.05}
    fee = ss._fee_per_dollar(row, 0.5)
    assert fee == pytest.approx(0.025)
    win = {**row, "_gross_profit_per_usdc": ss._gross_profit_per_usdc(1, 0.5), "_fee_per_dollar": fee}
    loss = {**row, "_gross_profit_per_usdc": ss._gross_profit_per_usdc(0, 0.5), "_fee_per_dollar": fee}
    assert ss._profit_at_fee_multiple(win) == pytest.approx(0.975)
    assert ss._profit_at_fee_multiple(loss) == pytest.approx(-1.025)
    assert ss._profit_at_fee_multiple(win, 2.0) == pytest.approx(0.95)
    assert ss._profit_at_fee_multiple(loss, 2.0) == pytest.approx(-1.05)
    assert ss._fee_per_dollar({"fees_enabled": "false", "category": "geopolitics"}, 0.5) == 0.0


def test_turnover_and_drawdown_use_the_prediction_timestamp_clock():
    """WO-171 item 8: turnover is validation rows per distinct (market, UTC date); the
    drawdown is peak-to-trough of the cumulative fee-net profit in stake units."""
    rows = []
    for market, day in (("a", 1), ("a", 2), ("a", 3), ("b", 1), ("b", 2)):
        rows += [_row(market, 0.5, 1, 0.975, when=DAY.format(day), fee=0.025, token=f"{market}{day}x"),
                 _row(market, 0.5, 1, 0.975, when=DAY.format(day), fee=0.025, token=f"{market}{day}y")]
    assert ss._turnover_rows_per_market_day(rows) == pytest.approx(2.0)

    ordered = [
        _row("a", 0.5, 1, 1.0, when=DAY.format(1), token="p1"),
        _row("a", 0.5, 0, -1.025, when=DAY.format(2), token="p2"),
        _row("a", 0.5, 0, -1.025, when=DAY.format(3), token="p3"),
        _row("a", 0.5, 1, 1.0, when=DAY.format(4), token="p4"),
    ]
    assert ss._max_drawdown_net_per_stake(ordered) == pytest.approx(-2.05)
    assert ss._max_drawdown_net_per_stake([]) == 0.0


def _winning_corpus(tmp_path: Path):
    """16 markets: 8 train, 4 validation, 4 final. Train and validation win at a tradeable
    price so a rule clears every floor and the correction; the final segment loses every row,
    so a rule that reaches the final read demonstrably was not selected on it."""
    cfg = _config(tmp_path)
    rows = []
    for index in range(16):
        for k in range(6):
            target = 0 if index >= 12 else 1
            rows.append(_feature_row(f"m{index:02d}", f"t{index:02d}_{k}", DAY.format(index + 1), 0.5, target))
    _write_corpus(cfg, rows)
    return cfg, rows


def test_family_file_records_every_variant_beyond_max_ranked(tmp_path: Path):
    """WO-171 item 4: the registry of tested variants is written before any truncation, so
    `max_ranked_rules` cannot hide a variant that was evaluated."""
    cfg = _config(tmp_path)
    rows = []
    for index in range(12):
        for k in range(6):
            # Distinct price buckets and time buckets multiply the rule values.
            price = [0.03, 0.07, 0.15, 0.3, 0.5, 0.9][k]
            rows.append({**_feature_row(f"m{index:02d}", f"t{index:02d}_{k}", DAY.format(index + 1), price, 1 if index < 9 else 0), "time_to_close_hours": [0.5, 3, 12, 48, 100, 300][k]})
    _write_corpus(cfg, rows)
    payload = ss.run_edge_strategy_search(cfg)
    assert payload["status"] == "computed"
    family = read_csv_rows(cfg.governance_root / "edge_strategy_search_family.csv")
    assert len(family) == payload["family_size"] > 10
    assert payload["family_size"] >= payload["ranked_rules"]
    assert len(read_csv_rows(cfg.governance_root / "edge_strategy_search.csv")) == payload["ranked_rules"]
    assert len(payload["top_rules"]) <= 10
    assert payload["family_tested_size"] <= payload["family_size"]
    # Every evaluated variant carries its family status; none is silently absent.
    assert {row["family_status"] for row in family} <= {"tested", "untested_insufficient_sample"}


def test_benchmark_and_excess(tmp_path: Path):
    """WO-171 item 7: buying every validation row is the benchmark, and a rule's excess over
    it is reported, so a rule that merely rides the segment cannot look special."""
    cfg, _ = _winning_corpus(tmp_path)
    payload = ss.run_edge_strategy_search(cfg)
    benchmark = payload["benchmark_buy_all"]
    assert benchmark["rows"] > 0 and benchmark["markets"] > 0
    family = read_csv_rows(cfg.governance_root / "edge_strategy_search_family.csv")
    for row in family:
        assert float(row["benchmark_buy_all_roi"]) == pytest.approx(benchmark["validation_roi"])
        assert float(row["excess_over_buy_all"]) == pytest.approx(float(row["validation_roi"]) - benchmark["validation_roi"])
    assert payload["hurdle"] == 0.02
    assert "already netted" in payload["hurdle_basis"]


def test_final_period_ledger_appends_one_row_per_promotable_rule_per_run(tmp_path: Path):
    """WO-171 item 9: the final segment's untouched status decays with every read, so every
    read is recorded. A header mismatch appends nothing and says so."""
    cfg, _ = _winning_corpus(tmp_path)
    first = ss.run_edge_strategy_search(cfg)
    assert first["ledger_status"] == "ok"
    ledger = cfg.governance_root / "edge_strategy_search_final_period_ledger.csv"
    promotable = first["promotable_rules"]
    if promotable:
        assert first["final_period_reads"] == promotable
        second = ss.run_edge_strategy_search(cfg)
        assert second["final_period_reads"] == 2 * promotable
        assert len(read_csv_rows(ledger)) == 2 * promotable
        assert {row["run_utc"] for row in read_csv_rows(ledger)} == {first["generated_at_utc"], second["generated_at_utc"]}
    else:
        assert first["final_period_reads"] == 0

    # A pre-existing ledger with a different header: nothing is appended, nothing is rewritten.
    ledger.write_text("run_utc,rule_family\n2026-01-01T00:00:00Z,family\n", encoding="utf-8")
    before = ledger.read_bytes()
    third = ss.run_edge_strategy_search(cfg)
    assert third["ledger_status"] == "schema_mismatch"
    assert third["final_period_reads"] is None
    assert ledger.read_bytes() == before


def test_consumers_read_the_same_keys(tmp_path: Path):
    """WO-171 item 10 (A9): every key a consumer reads today still resolves, with the same
    value as its new twin, so no downstream reader changes behaviour on this release."""
    cfg, _ = _winning_corpus(tmp_path)
    payload = ss.run_edge_strategy_search(cfg)
    assert payload["development_markets"] == payload["train_markets"]
    assert payload["holdout_markets"] == payload["validation_markets"]
    family = read_csv_rows(cfg.governance_root / "edge_strategy_search_family.csv")
    assert family
    for row in family:
        for alias, new in (
            ("dev_rows", "train_rows"), ("dev_markets", "train_markets"), ("dev_roi", "train_roi"),
            ("dev_profit_usdc_per_1_stake", "train_profit_usdc_per_1_stake"), ("dev_win_rate", "train_win_rate"),
            ("holdout_rows", "validation_rows"), ("holdout_markets", "validation_markets"),
            ("holdout_roi", "validation_roi"), ("holdout_profit_usdc_per_1_stake", "validation_profit_usdc_per_1_stake"),
            ("holdout_win_rate", "validation_win_rate"), ("holdout_roi_ci_low", "validation_roi_ci_low"),
            ("holdout_roi_ci_high", "validation_roi_ci_high"),
        ):
            assert row[alias] == row[new], f"{alias} != {new}"
    # The settings block carries both vocabularies, and the ignored key is named.
    settings = payload["settings"]
    assert settings["split_fractions"] == [0.5, 0.25, 0.25]
    assert settings["min_holdout_rows"] == settings["min_validation_rows"]
    assert settings["min_dev_roi"] == settings["min_train_roi"]
    assert settings["ignored_keys"] == ["holdout_fraction"]
    assert payload["availability_basis"] == "venue_resolution_time_else_close_time"
    assert "each run is a further look" in payload["inference_note"]


def test_a_new_key_wins_over_its_old_name(tmp_path: Path):
    """WO-171 item 3: where a new key and its old name are both present the new key binds,
    and a runtime config carrying only the old names keeps working."""
    settings = {"min_validation_rows": 9, "min_holdout_rows": 6}
    assert ss._setting(settings, "min_validation_rows", "min_holdout_rows", 6) == 9
    assert ss._setting({"min_holdout_rows": 3}, "min_validation_rows", "min_holdout_rows", 6) == 3
    assert ss._setting({}, "min_validation_rows", "min_holdout_rows", 6) == 6


def test_missing_labels_file_yields_no_labels_and_empty_csvs(tmp_path: Path):
    """WO-171 S5: a missing `labels.csv` evaluates no rule and leaves no stale artifact."""
    cfg = _config(tmp_path)
    write_csv(
        cfg.output_root / "polymarket_training" / "features_v2.csv",
        _corpus(12),
        fieldnames=["market_id", "token_id", "prediction_timestamp", "executable_buy_price", "implied_probability", "market_slug", "category", "fees_enabled", "time_to_close_hours"],
    )
    cfg.governance_root.mkdir(parents=True, exist_ok=True)
    (cfg.governance_root / "edge_strategy_search.csv").write_text("stale\n1\n", encoding="utf-8")
    payload = ss.run_edge_strategy_search(cfg)
    assert payload["status"] == "no_labels"
    assert read_csv_rows(cfg.governance_root / "edge_strategy_search.csv") == []
    assert json.loads((cfg.governance_root / "edge_strategy_search_summary.json").read_text(encoding="utf-8"))["status"] == "no_labels"


def test_bh_significance_is_required_for_promotion(tmp_path: Path, monkeypatch):
    """WO-171 item 5: the correction is a conjunct of `promotable`, not a reported number.

    A rule that clears every floor and every gate but is not rejected by
    Benjamini-Hochberg over the whole tested family is not promotable, and the reason
    string says so."""
    cfg, _ = _winning_corpus(tmp_path)
    promoting = ss.run_edge_strategy_search(cfg)
    assert promoting["promotable_rules"] > 0
    assert promoting["bh_rejections"] > 0

    monkeypatch.setattr(ss, "_bh_significant", lambda p_values, q=ss.BH_Q: [False] * len(p_values))
    corrected = ss.run_edge_strategy_search(cfg)
    assert corrected["promotable_rules"] == 0
    assert corrected["bh_rejections"] == 0
    # Everything else is unchanged: only the correction removed them.
    assert corrected["family_tested_size"] == promoting["family_tested_size"]
    family = read_csv_rows(cfg.governance_root / "edge_strategy_search_family.csv")
    assert all(row["promotable"] in ("False", "false") for row in family)
    assert all("BH significance at q=0.10" in row["promotion_reason"] for row in family)
