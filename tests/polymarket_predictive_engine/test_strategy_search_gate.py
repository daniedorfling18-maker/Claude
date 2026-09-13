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


def _config(tmp_path: Path, *, settings: dict | None = None):
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    if settings:
        raw.setdefault("edge_strategy_search", {}).update(settings)
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


def test_selection_reads_validation_only_and_final_never_feeds_promotable(tmp_path: Path):
    """WO-171 item 3, end to end: `promotable` and the ranking are invariant under any change to
    the final segment, and the final segment is READ only for a rule already selected.

    Rewritten after the build line audit found the earlier version asserted neither `promotable`
    nor a ranking position, because it called the evaluator directly and the evaluator computes
    neither. It also found the final segment was being read for every variant, which publishes its
    ROI for every rule and makes the read ledger wrong."""
    def corpus(final_targets: dict[str, int]) -> list[dict]:
        rows = []
        # 32 markets: 16 train, 8 validation, 8 final, so each of the two categories reaches the
        # registered 4-validation-market floor and can be promotable on its own.
        for index in range(32):
            for k in range(6):
                if index < 24:
                    # Rule A (sports) wins train and validation; rule B (crypto) loses.
                    target = 1 if index % 2 == 0 else 0
                else:
                    target = final_targets["A" if index % 2 == 0 else "B"]
                category = "sports" if index % 2 == 0 else "crypto"
                rows.append({**_feature_row(f"m{index:02d}", f"t{index:02d}_{k}", DAY.format(index + 1), 0.5, target, category=category)})
        return rows

    baseline_cfg = _config(tmp_path)
    _write_corpus(baseline_cfg, corpus({"A": 0, "B": 1}))
    baseline = ss.run_edge_strategy_search(baseline_cfg)

    swapped_root = tmp_path / "swapped"
    swapped_root.mkdir()
    swapped_cfg = _config(swapped_root)
    _write_corpus(swapped_cfg, corpus({"A": 1, "B": 0}))
    swapped = ss.run_edge_strategy_search(swapped_cfg)

    def by_rule(payload, cfg):
        return {(row["rule_family"], row["rule_value"]): row for row in read_csv_rows(cfg.governance_root / "edge_strategy_search_family.csv")}

    base_rows, swap_rows = by_rule(baseline, baseline_cfg), by_rule(swapped, swapped_cfg)
    assert base_rows.keys() == swap_rows.keys()
    assert baseline["promotable_rules"] > 0
    for key, row in base_rows.items():
        other = swap_rows[key]
        # Selection is byte-identical under a total inversion of the final segment.
        for field in ("promotable", "promotion_reason", "train_roi", "validation_roi", "validation_roi_ci_low", "validation_p_value", "bh_significant", "family_status"):
            assert row[field] == other[field], (key, field)
    # The ranking order is identical too.
    order = [r["rule_value"] for r in baseline["top_rules"]]
    assert order == [r["rule_value"] for r in swapped["top_rules"]]
    # ...and the final readings themselves DID change, so the invariance above is not vacuous.
    promoted_key = (baseline["promoted_rules"][0]["rule_family"], baseline["promoted_rules"][0]["rule_value"])
    assert base_rows[promoted_key]["final_roi"] != swap_rows[promoted_key]["final_roi"]

    # A rule that is not promotable is never read against the final segment at all.
    unread = [row for row in base_rows.values() if row["promotable"] in ("False", "false")]
    assert unread, "the fixture must contain a non-promotable variant"
    for row in unread:
        assert row["final_rows"] == "" and row["final_roi"] == "" and row["final_evidence_class"] == ""
    promoted_rows = [row for row in base_rows.values() if row["promotable"] in ("True", "true")]
    for row in promoted_rows:
        assert row["final_evidence_class"] == "retrospective"
        assert float(row["final_roi"]) < 0
    # Every read is a ledger row: the count is the number that happened, not the number ranked.
    assert baseline["final_period_reads"] == len(promoted_rows) == baseline["promotable_rules"]


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
    # geopolitics carries a registered rate of 0.0, so it cannot discriminate `fees_enabled`;
    # sports at 0.05 can, and does.
    assert ss._fee_per_dollar({"fees_enabled": "true", "category": "sports"}, 0.5) == pytest.approx(0.025)
    assert ss._fee_per_dollar({"fees_enabled": "false", "category": "sports"}, 0.5) == 0.0
    assert ss._fee_per_dollar({"fees_enabled": "true", "category": "geopolitics"}, 0.5) == 0.0


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
    """WO-171 item 4: the registry is written before truncation, so `max_ranked_rules` cannot hide
    a variant that was evaluated.

    Rewritten after the build line audit found the earlier fixture produced 26 variants against a
    cap of 100, so the truncation never fired and the guard could be moved after it with the test
    still green. The cap here is BELOW the variant count, so truncation is real."""
    cfg = _config(tmp_path, settings={"max_ranked_rules": 4})
    rows = []
    prices = [0.03, 0.07, 0.15, 0.3, 0.5, 0.9]
    hours = [0.5, 3, 12, 48, 100, 300]
    for index in range(12):
        for k in range(6):
            rows.append({**_feature_row(f"m{index:02d}", f"t{index:02d}_{k}", DAY.format(index + 1), prices[k], 1 if index < 9 else 0), "time_to_close_hours": hours[k]})
    _write_corpus(cfg, rows)
    payload = ss.run_edge_strategy_search(cfg)
    assert payload["status"] == "computed"

    family = read_csv_rows(cfg.governance_root / "edge_strategy_search_family.csv")
    ranked = read_csv_rows(cfg.governance_root / "edge_strategy_search.csv")
    # Truncation is real on this fixture, and only the ranked file feels it.
    assert payload["family_size"] > 4
    assert len(family) == payload["family_size"]
    assert len(ranked) == payload["ranked_rules"] == 4
    assert len(payload["top_rules"]) <= 4
    # Every ranked variant is in the family; the family carries variants the ranked file drops.
    ranked_keys = {(row["rule_family"], row["rule_value"]) for row in ranked}
    family_keys = {(row["rule_family"], row["rule_value"]) for row in family}
    assert ranked_keys < family_keys
    assert payload["family_tested_size"] <= payload["family_size"]
    assert {row["family_status"] for row in family} <= {"tested", "untested_insufficient_sample"}


def test_benchmark_and_excess(tmp_path: Path):
    """WO-171 item 7: a rule that merely rides its segment shows no excess.

    Rewritten after the build line audit found the earlier version asserted the identity that
    computes the field, which cannot fail, and used a fixture where the rule spanned the whole
    segment so the excess was always 0. Here the two rules differ and the margin is known."""
    cfg = _config(tmp_path)
    rows = []
    for index in range(16):
        for k in range(6):
            category = "sports" if index % 2 == 0 else "crypto"
            # In the validation quarter the sports rule wins every row and crypto loses every row,
            # so buying everything sits exactly between them.
            if index < 8:
                target = 1
            else:
                target = 1 if category == "sports" else 0
            rows.append(_feature_row(f"m{index:02d}", f"t{index:02d}_{k}", DAY.format(index + 1), 0.5, target, category=category))
    _write_corpus(cfg, rows)
    payload = ss.run_edge_strategy_search(cfg)
    benchmark = payload["benchmark_buy_all"]
    assert benchmark["rows"] > 0 and benchmark["markets"] == 4

    family = {row["rule_value"]: row for row in read_csv_rows(cfg.governance_root / "edge_strategy_search_family.csv")}
    winner = family["sports"]
    loser = family["crypto_special"]
    # Fee-net, at each category's own registered rate: sports 0.05 gives a fee of 0.025 per dollar
    # at an entry of 0.5, so a win returns 0.975; crypto 0.07 gives 0.035, so a loss returns
    # -1.035. Buying every validation row is their mean, and the excess is measured against it.
    assert float(winner["validation_roi"]) == pytest.approx(0.975)
    assert float(loser["validation_roi"]) == pytest.approx(-1.035)
    assert benchmark["validation_roi"] == pytest.approx((0.975 - 1.035) / 2)
    assert float(winner["excess_over_buy_all"]) == pytest.approx(0.975 - (0.975 - 1.035) / 2)
    assert float(loser["excess_over_buy_all"]) == pytest.approx(-1.035 - (0.975 - 1.035) / 2)
    assert float(winner["excess_over_buy_all"]) == pytest.approx(1.005)
    assert float(loser["excess_over_buy_all"]) == pytest.approx(-1.005)
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


def test_availability_index_prefers_resolution_time_per_market(tmp_path: Path):
    """WO-171 item 2, added after the build line audit found `_availability_index` had no test at
    all and two defects inside it.

    The fallback is per MARKET, not per label row: taking `resolution_time or close_time` row by
    row let one row's close time outvote another row's resolution time on the same market. And a
    BLANK horizon was read as `all_valid`, so a market whose labels carry no horizon gained an
    availability time it had not earned and survived the fail-closed drop."""
    cfg = _config(tmp_path)
    labels = [
        # One market, two labels: the resolution time must win even though a later close time exists.
        {"market_id": "mA", "token_id": "t1", "prediction_timestamp": DAY.format(1), "horizon": "all_valid", "resolution_time": DAY.format(5), "close_time": DAY.format(2)},
        {"market_id": "mA", "token_id": "t2", "prediction_timestamp": DAY.format(1), "horizon": "all_valid", "resolution_time": "", "close_time": DAY.format(20)},
        # A market with no resolution time at all falls back to its latest close time.
        {"market_id": "mB", "token_id": "t3", "prediction_timestamp": DAY.format(1), "horizon": "all_valid", "resolution_time": "", "close_time": DAY.format(7)},
        {"market_id": "mB", "token_id": "t4", "prediction_timestamp": DAY.format(1), "horizon": "all_valid", "resolution_time": "", "close_time": DAY.format(9)},
        # A blank horizon is not `all_valid`: this market gets no availability time and is dropped.
        {"market_id": "mC", "token_id": "t5", "prediction_timestamp": DAY.format(1), "horizon": "", "resolution_time": DAY.format(3), "close_time": DAY.format(3)},
        # A different horizon is excluded, as it always was.
        {"market_id": "mD", "token_id": "t6", "prediction_timestamp": DAY.format(1), "horizon": "24h", "resolution_time": DAY.format(3), "close_time": DAY.format(3)},
    ]
    write_csv(
        cfg.output_root / "polymarket_training" / "labels.csv",
        labels,
        fieldnames=["market_id", "token_id", "prediction_timestamp", "horizon", "resolution_time", "close_time"],
    )
    index = ss._availability_index(cfg)
    assert index["mA"] == ss.parse_timestamp(DAY.format(5)), "the max resolution_time must win over any close_time"
    assert index["mB"] == ss.parse_timestamp(DAY.format(9)), "no resolution_time on this market: the max close_time"
    assert "mC" not in index, "a blank horizon is not all_valid"
    assert "mD" not in index

    # A missing labels.csv is None, which the caller turns into `no_labels`.
    (cfg.output_root / "polymarket_training" / "labels.csv").unlink()
    assert ss._availability_index(cfg) is None


def test_a_row_with_an_unparseable_timestamp_is_dropped_and_counted(tmp_path: Path):
    """WO-171 item 1 and test 11's last clause, added after the build line audit found
    `dropped_unparseable_timestamp` had no assertion anywhere in the repository.

    A row with no usable clock cannot be placed in a chronological segment, so it is dropped and
    counted rather than sorted to the epoch, where it would silently join the training half."""
    cfg = _config(tmp_path)
    rows = _corpus(16)
    broken = dict(rows[0])
    broken["shadow_position_id"] = "broken"
    broken["token_id"] = "broken-token"
    broken["prediction_timestamp"] = "not-a-timestamp"
    _write_corpus(cfg, rows + [broken])
    payload = ss.run_edge_strategy_search(cfg)
    assert payload["split"]["dropped_unparseable_timestamp"] == 1
    assert payload["joined_rows"] == len(rows)

    # A corpus of only unparseable rows evaluates nothing rather than sorting them all together.
    empty_root = tmp_path / "allbroken"
    empty_root.mkdir()
    scoped = _config(empty_root)
    _write_corpus(scoped, [{**row, "prediction_timestamp": "not-a-timestamp"} for row in _corpus(16)])
    broken_payload = ss.run_edge_strategy_search(scoped)
    assert broken_payload["status"] == "insufficient_markets"
    # 16 markets x 4 rows: _corpus defaults to 4 rows per market.
    assert broken_payload["split"]["dropped_unparseable_timestamp"] == 64


def test_consumer_readers_select_the_same_rules_on_the_new_summary(tmp_path: Path):
    """WO-171 item 10 (A9), added after the build line audit found the registered consumer-reader
    clause was never built: the earlier test compared alias values but imported nothing from
    `scripts/` and constructed no old-shaped summary.

    The two scripts that consume this artifact read it through plain key lookups. Here the same
    reader logic runs over the new summary and over an equivalent old-shaped one and must select
    the same rules, so no downstream behaviour changes on this release."""
    cfg, _ = _winning_corpus(tmp_path)
    payload = ss.run_edge_strategy_search(cfg)
    assert payload["promotable_rules"] > 0

    def shadow_scan_selection(summary: dict) -> list[tuple]:
        """The shape `run_promoted_rule_shadow_scan.py` reads: promotable rules, by their keys."""
        return sorted(
            (row["rule_family"], row["rule_value"], round(float(row["dev_roi"]), 6), round(float(row["holdout_roi"]), 6), int(row["holdout_rows"]), int(row["dev_rows"]), int(row["dev_markets"]))
            for row in summary.get("top_rules", [])
            if row.get("promotable")
        )

    def liquidity_selection(summary: dict) -> list[tuple]:
        """The shape `run_polymarket_liquidity_discovery.py` reads."""
        return sorted(
            (row["rule_value"], row["family"], int(row["holdout_rows"]), bool(row["promotable"]))
            for row in summary.get("top_rules", [])
        )

    old_shaped = {
        "status": payload["status"],
        "promotable_rules": payload["promotable_rules"],
        "development_markets": payload["development_markets"],
        "holdout_markets": payload["holdout_markets"],
        "top_rules": [
            {key: row[key] for key in ("rule_family", "rule_value", "family", "outcome", "promotable", "promotion_reason", "dev_rows", "dev_markets", "dev_roi", "dev_win_rate", "holdout_rows", "holdout_markets", "holdout_roi", "holdout_win_rate", "holdout_roi_ci_low", "holdout_roi_ci_high", "rows", "markets", "roi", "win_rate", "avg_entry_price")}
            for row in payload["top_rules"]
        ],
    }
    assert shadow_scan_selection(payload) == shadow_scan_selection(old_shaped)
    assert liquidity_selection(payload) == liquidity_selection(old_shaped)
    assert shadow_scan_selection(payload), "the fixture must promote something for this to bite"
    # The paper loop reads three keys off the top level; all three still resolve.
    assert old_shaped["status"] == "computed"
    assert isinstance(payload["promotable_rules"], int)
    assert isinstance(payload["top_rules"], list)
