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


def _row(market: str, price: float, target: int, profit: float) -> dict:
    return {
        "_market_key": market,
        "_price": price,
        "_target": target,
        "_profit_per_usdc": profit,
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
        development_markets=set(),
        holdout_markets=holdout,
        stake_usdc=1.0,
    )
    assert result["holdout_markets"] == 6
    assert result["holdout_roi"] > 1.0                  # point ROI is huge
    assert result["holdout_roi_ci_low"] < 0.0           # the gate would reject this (CI low < 0)
    assert result["avg_entry_price"] < 0.05             # ...and it is below the entry-price floor too
