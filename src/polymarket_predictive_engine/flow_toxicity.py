"""WO-49 flow-toxicity conditioning.

This is a measurement-only lane for maker-carry risk review:

* VPIN-lite from signed trade-print volume buckets;
* wallet-tier markout split for leaderboard top-100 wallets versus crowd.

The output is a quote-sheet conditioning artifact. It does not modify adverse
selection charges, gates, net carry, sizing, or any order path.
"""
from __future__ import annotations

import csv
import gzip
import math
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from statistics import mean
from tempfile import TemporaryDirectory
from typing import Any

from .config import EngineConfig, load_config
from .utils import (
    normalize_external_timestamp,
    now_utc,
    read_csv_rows,
    read_json,
    safe_float,
    write_csv,
    write_json,
)

TOXICITY_FIELDS = [
    "generated_at_utc",
    "market",
    "asset_id",
    "toxicity_score",
    "vpin_raw",
    "volume_buckets",
    "trades_seen",
    "smart_fill_count",
    "crowd_fill_count",
    "smart_fill_markout",
    "crowd_fill_markout",
    "missing_price_points",
    "raw_imbalance_block",
    "percentile_block",
    "markout_coverage_ratio",
    "toxic_blocked",
    "toxicity_block_reasons",
]

# Wallet-axis markouts.
#
# The market-axis table above answers "is this market's flow toxic". It cannot
# answer "which wallets actually predict", because it discards the wallet the
# moment it classifies a fill as smart or crowd. That classification is itself
# the constraint: _top_wallets resolves "smart" to the LATEST snapshot of
# leaderboard_history.csv capped at 100 wallets, and the mirror holds 200 rows
# across 2 snapshots naming the same 100 wallets. So of 475 markets scored from
# 200,000 fills, only 16 ever produce a smart-fill markout - not because prices
# are missing (176 markets have coverage) but because a fill can only be smart
# if it belongs to one of a hundred wallets on a public PnL leaderboard.
#
# A PnL/volume ranking is not a measure of prediction. This table publishes the
# empirical alternative the data already supports: forward markout per wallet,
# split into an earlier ranking window and a later evaluation window so a wallet
# can be ranked on one and judged on the other. Diagnostic only - no gate,
# sizing or order surface reads it.
WALLET_MARKOUT_FIELDS = [
    "generated_at_utc",
    # AGENTS.md artifact-level provenance: a header-only file names the columns
    # but asserts nothing, so a disabled run must still emit ONE row that states
    # its own evidence class (Codex P1 wave-4 on #451). artifact_status is "ok"
    # on a scored row and "disabled" on that sentinel.
    "artifact_status",
    "wallet",
    "on_current_leaderboard",
    "fills_total",
    "markout_mean_total",
    "fills_ranking_window",
    "markout_mean_ranking_window",
    "fills_evaluation_window",
    "markout_mean_evaluation_window",
    "fills_split_spanning",
    "fills_label_embargoed",
    "fills_stale_price_excluded",
    "fills_missing_price",
    "markets_touched",
    # AGENTS.md artifact-level provenance invariant: every NEW artifact states
    # both flags itself; the summary's copy does not satisfy it.
    "paper_trading_invoked",
    "live_trading_invoked",
]

# WO-102 (2026-07-17): the historical toxicity_score is a UNIVERSE-RELATIVE
# percentile (index / (n-1)). A genuinely one-sided market can silently fall
# BELOW the standing-rule-8 percentile threshold simply because more calm
# markets were measured alongside it that day -- de-vetoing a toxic market
# with no change in its own flow. This adds an ABSOLUTE, universe-independent
# raw-imbalance floor so the veto cannot drift. The composite screen is
# strictly TIGHTEN-ONLY: a market is blocked if the percentile rule OR the
# absolute floor fires. It never clears a market the old rule blocked.
REGISTERED_RAW_IMBALANCE_FLOOR = 0.90
REGISTERED_PERCENTILE_BLOCK = 0.90


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = cfg.raw.get("flow_toxicity", {}) if isinstance(cfg.raw.get("flow_toxicity"), dict) else {}
    merged = {
        "enabled": True,
        "volume_bucket_usd": 500,
        "buckets": 50,
        "markout_horizon_minutes": 5,
    }
    merged.update({k: v for k, v in raw.items() if v is not None})
    return merged


def _stamp(value: Any) -> float | None:
    return normalize_external_timestamp(value)


def _wallet(row: dict[str, Any]) -> str:
    for key in ("counterparty_wallet", "wallet", "proxyWallet", "proxy_wallet", "trader", "user"):
        value = str(row.get(key) or "").strip()
        if value:
            return value.lower()
    return ""


def _iter_csv_any(path: Path) -> Iterator[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8-sig", errors="replace", newline="") as handle:
            for row in csv.DictReader(handle):
                yield {str(k): "" if v is None else str(v) for k, v in row.items()}
        return
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        for row in csv.DictReader(handle):
            yield {str(k): "" if v is None else str(v) for k, v in row.items()}


def _top_wallets(cfg: EngineConfig, limit: int = 100) -> tuple[set[str], bool]:
    path = cfg.output_root / "wallet_intelligence" / "leaderboard_history.csv"
    rows = read_csv_rows(path)
    if not rows:
        return set(), True
    latest = max(str(row.get("snapshot_date") or row.get("snapshot_at_utc") or "") for row in rows)
    latest_rows = [row for row in rows if str(row.get("snapshot_date") or row.get("snapshot_at_utc") or "") == latest]
    latest_rows.sort(key=lambda row: safe_float(row.get("rank")) if safe_float(row.get("rank")) is not None else 1e9)
    return {str(row.get("wallet") or "").lower() for row in latest_rows[:limit] if row.get("wallet")}, False


def _feature_paths(cfg: EngineConfig) -> Iterator[Path]:
    archive = cfg.output_root / "polymarket_training_archive"
    if archive.exists():
        for path in sorted(archive.glob("*.csv.gz")):
            yield path
    live = cfg.output_root / "polymarket_training" / "websocket_market_features.csv"
    if live.exists():
        yield live


def _price_target_bounds(
    trades: list[dict[str, Any]],
    horizon_seconds: float,
) -> dict[str, tuple[float, float]]:
    bounds: dict[str, tuple[float, float]] = {}
    for trade in trades:
        token = str(trade["asset_id"])
        target = float(trade["stamp"]) + horizon_seconds
        current = bounds.get(token)
        if current is None:
            bounds[token] = (target, target)
        else:
            bounds[token] = (min(current[0], target), max(current[1], target))
    return bounds


def _build_price_index(
    cfg: EngineConfig,
    connection: sqlite3.Connection,
    target_bounds: dict[str, tuple[float, float]],
) -> tuple[int, int]:
    """Stream feature corpora into a bounded, disk-backed lookup index.

    Only points inside a token's required markout interval are retained. One
    earliest tail point is also kept so the final trade target can still be
    marked when its next observation falls after the interval. This preserves
    the original first-midpoint-at-or-after lookup without holding every
    decompressed archive row in RAM.
    """

    connection.executescript(
        """
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous = OFF;
        PRAGMA temp_store = FILE;
        PRAGMA cache_size = -32768;
        PRAGMA mmap_size = 0;
        CREATE TABLE feature_prices (
            asset_id TEXT NOT NULL,
            stamp REAL NOT NULL,
            midpoint REAL NOT NULL,
            observed_stamp REAL NOT NULL,
            source_order INTEGER NOT NULL
        );
        """
    )
    batch: list[tuple[str, float, float, float, int]] = []
    tail_candidates: dict[str, tuple[float, float, int, float]] = {}
    scanned_rows = 0
    indexed_rows = 0

    def flush() -> None:
        nonlocal indexed_rows
        if not batch:
            return
        connection.executemany(
            "INSERT INTO feature_prices(asset_id, stamp, midpoint, observed_stamp, source_order)"
            " VALUES (?, ?, ?, ?, ?)",
            batch,
        )
        indexed_rows += len(batch)
        batch.clear()

    for path in _feature_paths(cfg):
        for row in _iter_csv_any(path):
            scanned_rows += 1
            token = str(row.get("asset_id") or row.get("token_id") or "").strip()
            bounds = target_bounds.get(token)
            if bounds is None:
                continue
            stamp = _stamp(row.get("source_timestamp") or row.get("collected_at_utc"))
            # The VENUE stamp orders the series; the COLLECTION stamp says when
            # the price actually became observable to us (Codex P1 wave-8 on
            # #451). Keeping only the venue stamp let a delayed or replayed
            # feature sourced at 900 but collected at 1100 count as available at
            # 900 - so it would pass a split at 1000 and enter the RANKING mean
            # despite only existing during evaluation. Both are retained and the
            # ranking embargo tests the later of the two.
            observed = _stamp(row.get("collected_at_utc")) or stamp
            midpoint = safe_float(row.get("midpoint"))
            if stamp is None or midpoint is None or stamp < bounds[0]:
                continue
            # safe_float parses "inf"/"nan" here too (Codex P1 wave-6 on #451:
            # the wave-5 fix validated only TRADE fields). An inf midpoint
            # yields an infinite markout that is emitted as a successfully
            # scored wallet mean; a nan midpoint reaches an INSERT into a
            # `midpoint REAL NOT NULL` column, where SQLite stores NaN as NULL
            # and the constraint aborts the whole build. Rejected before either.
            if not math.isfinite(stamp) or not math.isfinite(midpoint):
                continue
            if observed is None or not math.isfinite(observed):
                observed = stamp
            source_order = scanned_rows
            if stamp <= bounds[1]:
                batch.append((token, stamp, midpoint, observed, source_order))
                if len(batch) >= 10_000:
                    flush()
                continue
            # Ordered (stamp, observed, source_order, midpoint) so the tuple
            # comparison below breaks same-stamp ties on OBSERVATION time, never
            # on price. This is the second place that ordering mattered: the
            # SQL ORDER BY decides among indexed rows, but for a token whose
            # only candidates fall past the target bound it is THIS comparison
            # that picks the single retained row, and with midpoint second it
            # picked whichever price was numerically smaller (Codex P1 wave-9).
            candidate = (stamp, observed, source_order, midpoint)
            current = tail_candidates.get(token)
            if current is None or candidate < current:
                tail_candidates[token] = candidate

    for token, (stamp, observed, source_order, midpoint) in tail_candidates.items():
        batch.append((token, stamp, midpoint, observed, source_order))
    flush()
    connection.commit()
    connection.execute(
        "CREATE INDEX feature_prices_token_stamp_idx "
        "ON feature_prices(asset_id, stamp, observed_stamp, source_order, midpoint)"
    )
    connection.commit()
    return scanned_rows, indexed_rows


def _split_stamp_value(persisted: Any) -> float | None:
    """The frozen split_stamp, or None if the state is not usable.

    safe_float(True) is 1.0 and safe_float(False) is 0.0, so a corrupted or
    hand-restored file holding a JSON boolean would have passed a plain finite
    check and been reused as a cutoff (Codex P1 wave-10 on #451) - silently
    putting every real trade on one side of a nonsensical split while the
    summary reported a healthy frozen state. Booleans are rejected before
    conversion, along with anything non-numeric or negative: a split stamp is
    epoch seconds and cannot be negative.
    """
    if not isinstance(persisted, dict):
        return None
    raw = persisted.get("split_stamp")
    if isinstance(raw, bool):
        return None
    value = safe_float(raw)
    if value is None or not math.isfinite(value) or value < 0:
        return None
    return value


def _frozen_horizon_mismatch(persisted: Any, horizon_seconds: float) -> bool:
    """True when the split was frozen under a DIFFERENT markout horizon.

    The embargo in rule 3 is defined relative to the horizon, so reusing a
    cutoff frozen under another one re-opens the leak persistence exists to
    close (Codex P1 wave-10 on #451): shortening the horizon narrows the
    embargo interval and can move a previously-embargoed pre-split market into
    the ranking window AFTER its markout was published and inspectable. The
    horizon is therefore part of the frozen state, and a change to it makes the
    state invalid rather than silently reusable. A state written before this
    field existed has no recorded horizon and is also treated as invalid, so it
    fails closed rather than being assumed compatible.
    """
    if not isinstance(persisted, dict):
        return True
    recorded = persisted.get("horizon_seconds")
    if isinstance(recorded, bool):
        return True
    value = safe_float(recorded)
    if value is None or not math.isfinite(value):
        return True
    return abs(value - horizon_seconds) > 1e-9


def _frozen_split_stamp(
    cfg: EngineConfig, trades: list[dict[str, Any]], *, stamps_ok: bool, horizon_seconds: float
) -> tuple[float, bool]:
    """Return the ranking/evaluation split, frozen at first computation.

    Recomputing the median from the CURRENT corpus on every daily rebuild moves
    the split forward as fills arrive, so a market that was EVALUATION yesterday
    - and whose evaluation markout has already been published and inspected -
    can become RANKING today (Codex P1 wave-5 on #451). That recycles observed
    evaluation evidence back into the ranking sample, which is the same
    circularity the split exists to prevent, just spread across runs.

    The split is therefore computed once and persisted. As the corpus grows the
    ranking sample stays fixed and evaluation accumulates, which is exactly the
    behaviour an out-of-sample evaluation should have.
    """
    path = cfg.output_root / "maker_carry" / "flow_toxicity_wallet_split.json"
    if path.exists():
        persisted = read_json(path)
        stored = _split_stamp_value(persisted)
        if stored is not None:
            return stored, True
        # Unreachable in build_flow_toxicity: the early gate there validates
        # this file, invalidates the wallet artifact and raises before any of
        # this runs. Kept as a defensive guard for any other caller, so an
        # invalid frozen state can never silently become a fresh cutoff.
        raise ValueError(
            f"frozen wallet split state at {path} is present but invalid; refusing to "
            "manufacture a replacement cutoff from already-observed evaluation data."
        )
    if not stamps_ok:
        # Never freeze against a stale or partial ledger (Codex P1 wave-6 on
        # #451): the first such run would permanently fix the median of a
        # contaminated corpus, and every later healthy run would report
        # wallet_split_was_frozen=true while reusing that boundary forever. The
        # failure must be contained to the rejected run, so no state is created.
        stamps = sorted(float(trade["stamp"]) for trade in trades)
        return (stamps[len(stamps) // 2] if stamps else 0.0), False
    stamps = sorted(float(trade["stamp"]) for trade in trades)
    split_stamp = stamps[len(stamps) // 2] if stamps else 0.0
    if stamps:
        write_json(
            path,
            {
                "split_stamp": split_stamp,
                # Part of the frozen state: the embargo is defined relative to
                # the horizon, so a cutoff frozen under a different one is not
                # reusable (Codex P1 wave-10 on #451).
                "horizon_seconds": horizon_seconds,
                "frozen_at_utc": now_utc(),
                "corpus_rows_at_freeze": len(stamps),
                # AGENTS.md artifact-level provenance: this is an independently
                # persisted artifact, so the wallet CSV's flags do not establish
                # ITS evidence class (Codex P1 wave-6 on #451).
                "paper_trading_invoked": False,
                "live_trading_invoked": False,
                "note": (
                    "Frozen ranking/evaluation split. Never recompute from a later corpus: "
                    "a moving median recycles published evaluation evidence into ranking."
                ),
            },
        )
    return split_stamp, False


def _wallet_sentinel_row(generated_at: str, status: str) -> dict[str, Any]:
    """One row asserting the artifact's evidence class when no wallet is scored.

    A header-only CSV names the columns but asserts NOTHING, so a consumer
    cannot establish the artifact's evidence class (Codex P1 wave-4/wave-5 on
    #451). Every non-scored state - disabled, an enabled run with no wallets,
    and an upstream corpus that is not ok - emits this instead.
    """
    return {
        "generated_at_utc": generated_at,
        "artifact_status": status,
        "wallet": "",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }


# EVERY producer that writes trade_prints.csv, with the summary each reports to
# (Codex P1 wave-7 on #451). The canonical ledger has THREE writers, not one:
# collect_trade_prints (trade_prints_summary.json), collect_maker_replay_data
# (trade_print_collector.py:521, same ledger, maker_portfolio_... summary) and
# backfill_trade_prints (:563/:571, same ledger, trade_print_backfill_summary).
# All three run in training_harvest.py:98-106 immediately before flow_toxicity,
# and the harvest is deliberately resilient - it continues past a failed step.
# Consulting only the first summary therefore let a stale-but-"ok" primary vouch
# for rows another producer had just written partially, and worse, permitted the
# permanent split freeze on that contaminated corpus.
_TRADE_LEDGER_PRODUCER_SUMMARIES = (
    "trade_prints_summary.json",
    "maker_portfolio_trade_prints_summary.json",
    "trade_print_backfill_summary.json",
)


def _trade_corpus_status(cfg: EngineConfig) -> str:
    """The WORST status across every producer that writes the trade ledger.

    The previous run's capped ledger stays readable when a collection fails or
    is partial, so scoring it without consulting the producers would rank wallets
    on an undisclosed stale or incomplete sample (Codex P1 wave-5 on #451).
    Absent/unparseable summary is treated as NOT ok - fail closed, not open - and
    "ok" is returned only when EVERY producer says ok, since any one of them can
    have written the partial rows (Codex P1 wave-7).
    """
    # KNOWN LIMITATION, registered rather than half-fixed (Codex P1 wave-10 on
    # #451). The concern is real: the harvest is resilient, so a producer can be
    # skipped, time out, or die before rewriting its summary, and a leftover
    # "ok" then certifies a ledger it never saw. But the obvious remedy --
    # requiring each summary to be newer than trade_prints.csv -- is WRONG here,
    # and wrong in the direction that breaks the normal case: the three
    # producers run in sequence and all write the same ledger, so after backfill
    # rewrites it, the earlier producers' summaries are LEGITIMATELY older. That
    # check would fire on every healthy harvest. Deciding this correctly needs
    # the harvest's own per-step result, which this module is not given -- it is
    # invoked as a standalone CLI command. Binding the summaries to a harvest
    # cycle id is the real fix and is registered as a prerequisite in §49.1.
    root = cfg.output_root / "polymarket_trade_prints"
    statuses: list[str] = []
    for filename in _TRADE_LEDGER_PRODUCER_SUMMARIES:
        payload = read_json(root / filename)
        if not isinstance(payload, dict):
            statuses.append("missing")
            continue
        statuses.append(str(payload.get("status") or "missing").strip().lower())
    # RESTING states are successful no-ops, not failures, and must not veto
    # (Codex P1 wave-9 on #451 — the wave-7/8 tightening over-corrected here and
    # would have broken the NORMAL case): "disabled" is a producer switched off;
    # `skipped_all_completed` is what backfill_trade_prints returns once its
    # one-shot work is done, and `no_candidate_markets` when there is nothing to
    # backfill. Both are set in an elif chain AFTER the error check
    # (trade_print_collector.py:666-673), so both mean the producer ran and had
    # nothing to do. Treating them as vetoes would stamp every wallet row non-OK
    # on a healthy steady-state daily harvest and refuse to persist the split
    # even when another producer had just refreshed the ledger.
    resting = {"disabled", "skipped_all_completed", "no_candidate_markets"}
    for status in statuses:
        if status != "ok" and status not in resting:
            return status
    if not any(status == "ok" for status in statuses):
        # Nothing actually refreshed the ledger this run while flow-toxicity
        # itself stayed enabled (Codex P1 wave-8). Returning "ok" would stamp
        # retained rows as current and let them permanently seed the frozen
        # split. Disclosed, not fatal: rows are still measured and stamped.
        return "no_producer_refreshed"
    return "ok"


def _markout_stats(
    connection: sqlite3.Connection,
    trades: list[dict[str, Any]],
    top_wallets: set[str],
    horizon_seconds: float,
    split_stamp: float,
) -> dict[str, dict[str, float | int]]:
    trades_by_token: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        trades_by_token.setdefault(str(trade["asset_id"]), []).append(trade)

    stats: dict[str, dict[str, float | int]] = {}
    wallet_stats: dict[str, dict[str, Any]] = {}
    # Median fill time splits the sample chronologically, and the split is by
    # WHOLE MARKET, not by fill: a wallet trading one market on both sides of
    # the median would let market-specific effects observed during ranking
    # reappear in the evaluation window (Codex P1 on #451; AGENTS.md requires
    # validation chronological and out-of-sample BY MARKET). A market whose
    # fills span the split belongs to NEITHER window - excluded fail-closed and
    # disclosed per wallet as fills_split_spanning, never silently scored.
    market_bounds: dict[str, tuple[float, float]] = {}
    for trade in trades:
        market_key = str(trade["market"])
        stamp = float(trade["stamp"])
        low, high = market_bounds.get(market_key, (stamp, stamp))
        market_bounds[market_key] = (min(low, stamp), max(high, stamp))
    # EMBARGO (Codex P2 on #451). A fill's markout LABEL is a price read one
    # horizon after the fill, so a market whose last fill lands just before the
    # split has its label observed AFTER evaluation fills have already begun.
    # Ranking would then be scored partly on the evaluation period - the leak
    # the chronological split exists to prevent. A market qualifies as
    # "ranking" only when its label window also closes before the split
    # (high + horizon < split); one that would otherwise rank but whose label
    # crosses the split is EXCLUDED fail-closed and disclosed per wallet as
    # fills_label_embargoed, kept separate from fills_split_spanning so the two
    # exclusion reasons stay distinguishable.
    # The market-level test below uses the NOMINAL label time (high + horizon).
    # That is necessary but NOT sufficient: the staleness rule accepts an actual
    # observation up to one further horizon late, so a market ending at 600 with
    # a 300s horizon and a split at 1000 ranks on the nominal test (900 < 1000)
    # while its price could actually be read at 1100 - inside the evaluation
    # period (Codex P1 wave-4 on #451). The exact check is per fill, on the
    # timestamp actually selected, and is applied at accumulation below; this
    # market-level pass still excludes the bulk cheaply.
    market_window: dict[str, str] = {}
    for market_key, (low, high) in market_bounds.items():
        if high + horizon_seconds < split_stamp:
            market_window[market_key] = "ranking"
        elif low >= split_stamp:
            market_window[market_key] = "evaluation"
        elif high < split_stamp:
            market_window[market_key] = "label_embargoed"
        else:
            market_window[market_key] = "spanning"
    # A markout meant to measure the configured horizon, read from a price more
    # than one horizon late, measures a different horizon (Codex P1 on #451:
    # without a ceiling, the first observation hours later still scored). The
    # wallet axis accepts a price only inside [target, target + horizon] and
    # otherwise counts the fill as stale-excluded. The market-axis smart/crowd
    # columns keep their long-standing WO-49 lookup unchanged - tightening a
    # registered artifact is its own change, not a rider on this one.
    staleness_tolerance = horizon_seconds
    for token, token_trades in trades_by_token.items():
        feature_rows = iter(
            connection.execute(
                # Ties on the venue stamp break by OBSERVATION time, never by
                # midpoint (Codex P1 wave-9 on #451). websocket_normaliser.py
                # includes collection time in its dedup key, so two rows can
                # share a source_timestamp - e.g. a 0.70 quote collected before
                # the split and a corrected 0.60 quote collected after it.
                # Ordering by midpoint made BOTH which row is selected and
                # whether it is embargoed depend on the numerical price
                # ordering: swap the two prices and a different row wins. Which
                # observation was available first is the only defensible
                # tiebreak.
                "SELECT stamp, midpoint, observed_stamp FROM feature_prices "
                "WHERE asset_id = ? ORDER BY stamp, observed_stamp, source_order",
                (token,),
            )
        )
        current_feature = next(feature_rows, None)
        for trade in sorted(token_trades, key=lambda row: float(row["stamp"])):
            target = float(trade["stamp"]) + horizon_seconds
            while current_feature is not None and float(current_feature[0]) < target:
                current_feature = next(feature_rows, None)
            market = str(trade["market"])
            market_stats = stats.setdefault(
                market,
                {
                    "smart_count": 0,
                    "smart_sum": 0.0,
                    "crowd_count": 0,
                    "crowd_sum": 0.0,
                    "missing_prices": 0,
                },
            )
            # Wallet accounting is opened BEFORE the forward-price lookup can
            # `continue` (Codex P2 on #451). Creating it after the
            # missing-price branch meant a wallet whose every fill lacked a
            # forward price vanished from the artifact entirely - read as "did
            # not trade" when the truth is "was not measurable", the failure
            # mode the coverage columns exist to make visible.
            wallet = str(trade["wallet"] or "").strip().lower()
            entry: dict[str, Any] | None = None
            if wallet:
                entry = wallet_stats.setdefault(
                    wallet,
                    {
                        "fills_total": 0,
                        "markout_total": 0.0,
                        "fills_ranking": 0,
                        "markout_ranking": 0.0,
                        "fills_evaluation": 0,
                        "markout_evaluation": 0.0,
                        "fills_split_spanning": 0,
                        "fills_label_embargoed": 0,
                        "fills_stale_price_excluded": 0,
                        "fills_missing_price": 0,
                        "markets": set(),
                    },
                )
                entry["markets"].add(market)
            if current_feature is None:
                market_stats["missing_prices"] += 1
                if entry is not None:
                    entry["fills_missing_price"] += 1
                continue
            later = float(current_feature[1])
            markout = later - float(trade["price"])
            if trade["side"] == "SELL":
                markout = -markout
            tier = "smart" if trade["wallet"] and trade["wallet"] in top_wallets else "crowd"
            market_stats[f"{tier}_count"] += 1
            market_stats[f"{tier}_sum"] += markout
            if entry is not None:
                if float(current_feature[0]) - target > staleness_tolerance:
                    entry["fills_stale_price_excluded"] += 1
                    continue
                entry["fills_total"] += 1
                entry["markout_total"] += markout
                window = market_window.get(market, "spanning")
                # The label's OBSERVATION time decides, not the venue stamp
                # (Codex P1 wave-8 on #451): a feature sourced before the split
                # but collected after it only became available during
                # evaluation, so ranking on it is still look-ahead. Take the
                # later of the two - a venue stamp after the split embargoes on
                # its own, and so does a late collection of an early quote.
                label_time = max(float(current_feature[0]), float(current_feature[2]))
                if window == "ranking" and label_time >= split_stamp:
                    window = "label_embargoed"
                if window in {"spanning", "label_embargoed"}:
                    entry["fills_split_spanning" if window == "spanning" else "fills_label_embargoed"] += 1
                else:
                    entry[f"fills_{window}"] += 1
                    entry[f"markout_{window}"] += markout
    return stats, wallet_stats


def _vpin_raw(trades: list[dict[str, Any]], bucket_usd: float, bucket_count: int) -> tuple[float, int]:
    buckets: list[tuple[float, float]] = []
    signed = 0.0
    volume = 0.0
    for trade in sorted(trades, key=lambda row: row["stamp"]):
        remaining = trade["usd_volume"]
        sign = 1.0 if trade["side"] == "BUY" else -1.0
        while remaining > 0:
            take = min(remaining, bucket_usd - volume)
            signed += sign * take
            volume += take
            remaining -= take
            if volume >= bucket_usd - 1e-9:
                buckets.append((signed, volume))
                signed = 0.0
                volume = 0.0
    if volume > 0:
        buckets.append((signed, volume))
    recent = buckets[-bucket_count:] if bucket_count > 0 else buckets
    if not recent:
        return 0.0, 0
    return round(mean(abs(signed_volume) / max(total_volume, 1e-9) for signed_volume, total_volume in recent), 6), len(recent)


def _percentiles(raw_by_market: dict[str, float]) -> dict[str, float]:
    if not raw_by_market:
        return {}
    ordered = sorted(raw_by_market.items(), key=lambda item: item[1])
    if len(ordered) == 1:
        return {ordered[0][0]: round(ordered[0][1], 6)}
    return {market: round(index / (len(ordered) - 1), 6) for index, (market, _) in enumerate(ordered)}


def _trade_rows(cfg: EngineConfig) -> tuple[list[dict[str, Any]], int]:
    parsed: list[dict[str, Any]] = []
    malformed_rows = 0
    path = cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv"
    for row in _iter_csv_any(path):
        market = str(row.get("market") or "").strip()
        token = str(row.get("asset_id") or row.get("token_id") or "").strip()
        price = safe_float(row.get("price"))
        size = safe_float(row.get("size"))
        stamp = _stamp(row.get("timestamp") or row.get("collected_at_utc"))
        side = str(row.get("side") or "").upper()
        # safe_float PARSES "nan" and "inf" (Codex P1 wave-5 on #451): a nan
        # price yields a nan markout that increments fills_total and poisons
        # markout_total, so the wallet is emitted as SCORED with a nan mean
        # rather than excluded as malformed - and nan*size poisons usd_volume
        # and therefore VPIN too. Rejecting at the ingestion boundary is the
        # S8/A2 fail-closed input rule and is the only place that covers BOTH
        # the wallet axis and the long-standing market axis; a malformed venue
        # print is not a print, so this is an input-validity guard rather than
        # a change to what either metric MEANS.
        if not market or not token or price is None or size is None or stamp is None or side not in {"BUY", "SELL"}:
            continue
        if not math.isfinite(price) or not math.isfinite(size) or not math.isfinite(stamp):
            malformed_rows += 1
            continue
        parsed.append(
            {
                "market": market,
                "asset_id": token,
                "price": price,
                "size": size,
                "usd_volume": price * size,
                "stamp": stamp,
                "side": side,
                "wallet": _wallet(row),
            }
        )
    return parsed, malformed_rows


def build_flow_toxicity(cfg: EngineConfig) -> dict[str, Any]:
    settings = _settings(cfg)
    # Hoisted above the frozen-split gate below, which validates the persisted
    # state against the horizon it was frozen under.
    horizon = float(settings["markout_horizon_minutes"]) * 60.0
    out_root = cfg.output_root / "maker_carry"
    path = out_root / "flow_toxicity.csv"
    summary_path = out_root / "flow_toxicity_summary.json"
    generated_at = now_utc()
    summary: dict[str, Any] = {
        "status": "disabled",
        "generated_at_utc": generated_at,
        "work_order": "WO-49",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    if str(settings.get("enabled", True)).strip().lower() in {"0", "false", "no"}:
        write_json(summary_path, summary)
        # This early return precedes every artifact write, so the previous
        # enabled run's wallet rows would survive on disk with their own
        # generated_at_utc and be read as current rankings (Codex P2 on #451).
        # The wallet artifact is NEW here, so its disabled-path behaviour is
        # ours to define: replace it with a single sentinel row that states the
        # artifact is disabled AND carries both invocation flags, since a
        # header-only file names the columns but asserts nothing (Codex P1
        # wave-4). flow_toxicity.csv's long-standing stale-on-disabled
        # behaviour is deliberately NOT touched - changing a registered
        # artifact is its own change, not a rider.
        write_csv(
            out_root / "flow_toxicity_wallets.csv",
            [_wallet_sentinel_row(generated_at, "disabled")],
            fieldnames=WALLET_MARKOUT_FIELDS,
        )
        return summary
    # Validate the frozen split state BEFORE any heavy work and, critically,
    # before raising (Codex P2 wave-8 on #451). The raise is meant to fail
    # closed, but it happened before the wallet CSV or summary were rewritten,
    # so the harvest recorded the command failure, continued, and left the
    # PREVIOUS run's rows on disk still saying artifact_status="ok". A
    # fail-closed guard that leaves stale evidence readable fails OPEN at the
    # artifact level. Invalidate first, then raise.
    split_path = out_root / "flow_toxicity_wallet_split.json"
    if split_path.exists():
        persisted = read_json(split_path)
        stored = _split_stamp_value(persisted)
        if stored is None or _frozen_horizon_mismatch(persisted, horizon):
            summary["status"] = "invalid_frozen_split_state"
            write_json(summary_path, summary)
            write_csv(
                out_root / "flow_toxicity_wallets.csv",
                [_wallet_sentinel_row(generated_at, "invalid_frozen_split_state")],
                fieldnames=WALLET_MARKOUT_FIELDS,
            )
            raise ValueError(
                f"frozen wallet split state at {split_path} is present but invalid; refusing to "
                "manufacture a replacement cutoff from already-observed evaluation data. "
                "Restore the file from backup, or delete it deliberately to re-freeze. "
                "The wallet artifact has been invalidated so no stale ranking is readable."
            )
    trades, malformed_trade_rows = _trade_rows(cfg)
    trade_corpus_status = _trade_corpus_status(cfg)
    top_wallets, missing_wallet_data = _top_wallets(cfg)
    by_market: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        by_market.setdefault(trade["market"], []).append(trade)
    raw_vpin = {
        market: _vpin_raw(rows, float(settings["volume_bucket_usd"]), int(settings["buckets"]))[0]
        for market, rows in by_market.items()
    }
    toxicity = _percentiles(raw_vpin)
    markout_by_market: dict[str, dict[str, float | int]] = {}
    # Bound before the price-index block, which is skipped entirely when there
    # are no trades: an empty enabled run still publishes a summary.
    split_was_frozen = False
    markout_by_wallet: dict[str, dict[str, Any]] = {}
    feature_rows_scanned = 0
    feature_rows_indexed = 0
    price_index_disk_bytes = 0
    if trades:
        with TemporaryDirectory(prefix="polymarket-flow-toxicity-") as temp_dir:
            database_path = Path(temp_dir) / "price_index.sqlite3"
            connection = sqlite3.connect(database_path)
            try:
                feature_rows_scanned, feature_rows_indexed = _build_price_index(
                    cfg,
                    connection,
                    _price_target_bounds(trades, horizon),
                )
                split_stamp, split_was_frozen = _frozen_split_stamp(
                    cfg, trades, stamps_ok=trade_corpus_status == "ok", horizon_seconds=horizon
                )
                markout_by_market, markout_by_wallet = _markout_stats(
                    connection, trades, top_wallets, horizon, split_stamp
                )
                price_index_disk_bytes = database_path.stat().st_size
            finally:
                connection.close()
    rows_out: list[dict[str, Any]] = []
    for market, rows in sorted(by_market.items()):
        markouts = markout_by_market.get(market, {})
        smart_count = int(markouts.get("smart_count", 0))
        smart_sum = float(markouts.get("smart_sum", 0.0))
        crowd_count = int(markouts.get("crowd_count", 0))
        crowd_sum = float(markouts.get("crowd_sum", 0.0))
        missing_prices = int(markouts.get("missing_prices", len(rows)))
        asset_id = rows[0]["asset_id"] if rows else ""
        _, bucket_n = _vpin_raw(rows, float(settings["volume_bucket_usd"]), int(settings["buckets"]))
        market_vpin = raw_vpin.get(market, 0.0)
        market_percentile = toxicity.get(market, 0.0)
        measured_markouts = smart_count + crowd_count
        coverage_ratio = round(measured_markouts / len(rows), 6) if rows else 0.0
        raw_block = market_vpin >= REGISTERED_RAW_IMBALANCE_FLOOR
        pct_block = market_percentile > REGISTERED_PERCENTILE_BLOCK
        reasons = []
        if raw_block:
            reasons.append(f"raw_imbalance>={REGISTERED_RAW_IMBALANCE_FLOOR:g}")
        if pct_block:
            reasons.append(f"percentile>{REGISTERED_PERCENTILE_BLOCK:g}")
        rows_out.append(
            {
                "generated_at_utc": generated_at,
                "market": market,
                "asset_id": asset_id,
                "toxicity_score": market_percentile,
                "vpin_raw": market_vpin,
                "volume_buckets": bucket_n,
                "trades_seen": len(rows),
                "smart_fill_count": smart_count,
                "crowd_fill_count": crowd_count,
                "smart_fill_markout": round(smart_sum / smart_count, 6) if smart_count else None,
                "crowd_fill_markout": round(crowd_sum / crowd_count, 6) if crowd_count else None,
                "missing_price_points": missing_prices,
                "raw_imbalance_block": raw_block,
                "percentile_block": pct_block,
                "markout_coverage_ratio": coverage_ratio,
                "toxic_blocked": raw_block or pct_block,
                "toxicity_block_reasons": ";".join(reasons),
            }
        )
    write_csv(path, rows_out, fieldnames=TOXICITY_FIELDS)

    def _mean(total: float, count: int) -> float | None:
        return round(total / count, 6) if count else None

    wallet_rows = [
        {
            "generated_at_utc": generated_at,
            "artifact_status": "ok",
            "wallet": wallet,
            "on_current_leaderboard": wallet in top_wallets,
            "fills_total": entry["fills_total"],
            "markout_mean_total": _mean(entry["markout_total"], entry["fills_total"]),
            "fills_ranking_window": entry["fills_ranking"],
            "markout_mean_ranking_window": _mean(entry["markout_ranking"], entry["fills_ranking"]),
            "fills_evaluation_window": entry["fills_evaluation"],
            "markout_mean_evaluation_window": _mean(entry["markout_evaluation"], entry["fills_evaluation"]),
            "fills_split_spanning": entry["fills_split_spanning"],
            "fills_label_embargoed": entry["fills_label_embargoed"],
            "fills_stale_price_excluded": entry["fills_stale_price_excluded"],
            "fills_missing_price": entry["fills_missing_price"],
            "markets_touched": len(entry["markets"]),
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        }
        for wallet, entry in sorted(markout_by_wallet.items(), key=lambda item: -item[1]["fills_total"])
    ]
    wallet_path = cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv"
    # Summary aggregates are computed over the SCORED rows only; a sentinel
    # carries no window counters and must never be counted as a scored wallet.
    scored_wallet_rows = wallet_rows
    if trade_corpus_status != "ok":
        # The dependency is stale, partial, or unreported. Every row is STAMPED
        # with the upstream state rather than the artifact being blanked (Codex
        # P1 wave-5 on #451): ranking wallets on an undisclosed stale sample is
        # the harm, and stamping every row lets a reader reject the artifact
        # without a missing producer summary silently destroying a working one.
        for row in wallet_rows:
            row["artifact_status"] = f"upstream_{trade_corpus_status}"
    if not wallet_rows:
        # An enabled run with no trades, or with trades carrying no wallet
        # attribution, must still state its evidence class rather than emit a
        # bare header (Codex P1 wave-5 on #451).
        wallet_rows = [
            _wallet_sentinel_row(
                generated_at,
                "no_wallets_scored" if trade_corpus_status == "ok" else f"upstream_{trade_corpus_status}",
            )
        ]
    write_csv(wallet_path, wallet_rows, fieldnames=WALLET_MARKOUT_FIELDS)
    summary.update(
        {
            "status": "ok" if rows_out or not trades else "no_trades",
            "markets_scored": len(rows_out),
            "wallets_scored": len(scored_wallet_rows),
            # Published so the sample is visible before any ranking is trusted:
            # a wallet ranked on the earlier window must be judged on the later
            # one, and a wallet present in only one window cannot be judged at all.
            "wallets_in_both_windows": sum(
                1
                for row in scored_wallet_rows
                if row["fills_ranking_window"] and row["fills_evaluation_window"]
            ),
            "wallet_output_path": str(wallet_path),
            # States the embargo so a reader of the summary alone cannot assume
            # the chronological split is a bare median: a ranking market's label
            # window must also close before the split.
            "wallet_ranking_embargo_seconds": horizon,
            "wallet_split_was_frozen": split_was_frozen,
            "trade_corpus_status": trade_corpus_status,
            "malformed_trade_rows_excluded": malformed_trade_rows,
            "trades_seen": len(trades),
            "missing_wallet_data": missing_wallet_data,
            "price_index_strategy": "disk_backed_streaming_sqlite",
            "feature_rows_scanned": feature_rows_scanned,
            "feature_rows_indexed": feature_rows_indexed,
            "price_index_disk_bytes": price_index_disk_bytes,
            "max_toxicity_score": max([safe_float(row.get("toxicity_score")) or 0.0 for row in rows_out], default=0.0),
            "output_path": str(path),
            "note": (
                "Flow-toxicity is quote-sheet conditioning only. It does not modify maker adverse charges, "
                "net carry, gates, sizing, or any order path."
            ),
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        }
    )
    write_json(summary_path, summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return build_flow_toxicity(load_config(config_path))
