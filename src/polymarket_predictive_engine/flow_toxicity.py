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
from statistics import mean, median
from tempfile import TemporaryDirectory
from typing import Any

from .config import EngineConfig, load_config
from .degraded_state_watchdog import REGISTERED_JOB_FRESHNESS_MAX_SECONDS
from .runtime_lock import runtime_lock
from .utils import (
    normalize_external_timestamp,
    now_utc,
    parse_timestamp,
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
# READ from WO-85's registered ceiling, never redefined here (Codex P2
# wave-29). collect_wallet_intel is a step of training_harvest
# (training_harvest.py:88), so the job's own completion-freshness SLO is the
# only non-invented bound on how old its summary may be. Imported rather than
# copied so a change to WO-85 cannot leave a stale duplicate behind -- the same
# READ-never-change discipline degraded_state_watchdog.ORPHAN_BOUND_SECONDS
# applies to these values.
REGISTERED_HARVEST_FRESHNESS_MAX_SECONDS = float(
    REGISTERED_JOB_FRESHNESS_MAX_SECONDS["training_harvest"]
)

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


def _median_stamp(stamps: list[float]) -> float:
    """The statistical median of sorted fill timestamps, or 0.0 when empty.

    `stamps[len(stamps) // 2]` is the UPPER MIDDLE observation, not the median,
    and the two differ on every even-sized corpus (Codex P1 wave-41). When the
    two middle fills straddle a quiet interval the difference is not a rounding
    detail: the cutoff moves by the whole gap, admitting pre-split markets whose
    label windows would still cross the true median. The artifact's registered
    contract says MEDIAN fill time splits the sample, and this cutoff is frozen
    permanently on first computation, so an off-by-one-observation split would
    outlive every later run.

    One helper for all three call sites -- the freeze, the non-persisting
    in-memory split, and the invalid-state fallback -- because they must agree;
    three inline copies of the same expression is how they would stop agreeing.
    """
    if not stamps:
        return 0.0
    return float(median(stamps))


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


def _top_wallets(cfg: EngineConfig, limit: int = 100) -> tuple[set[str], set[str], bool]:
    # The leaderboard has a producer too, and its retained rows survive a failed
    # refresh (wallet_intelligence_collector.py:366-372 keeps the old history
    # and records the error in its summary). Treating any nonempty file as
    # authoritative published definitive on_current_leaderboard values after a
    # failed refresh, when membership may have moved (Codex P2 wave-17). Same
    # producer-status rule already applied to the trade ledger, now applied to
    # this dependency.
    producer = read_json(cfg.output_root / "wallet_intelligence" / "wallet_intelligence_summary.json")
    producer_status = (
        str(producer.get("status") or "missing").strip().lower()
        if isinstance(producer, dict)
        else "missing"
    )
    path = cfg.output_root / "wallet_intelligence" / "leaderboard_history.csv"
    rows = read_csv_rows(path)
    if not rows:
        return set(), set(), True
    # A failed refresh makes membership UNKNOWN FOR REPORTING, but it does not
    # make the retained top-100 disappear (Codex P1 wave-18). The wave-17 fix
    # returned an empty set in that case, and this same set feeds the LEGACY
    # market-axis tier split at :779 -- so every formerly smart fill was
    # reclassified as crowd, silently rewriting smart_fill_count,
    # crowd_fill_count and both tier markouts in the parent's registered
    # artifact. The parent used the retained top-100 here and still does. The
    # unknown-membership signal is carried separately and affects only the NEW
    # wallet artifact's reporting column.
    # LEADERBOARD-SPECIFIC evidence, not the collector's aggregate (Codex P2
    # wave-19). wallet_intelligence_collector.py:403-408 sets "partial" when ANY
    # request failed -- including an unrelated HOLDER request -- so keying off
    # the aggregate discarded a leaderboard that had just refreshed
    # successfully and rendered every membership unknown. leaderboard_rows_added
    # is the per-dependency evidence, so a positive count means the leaderboard
    # itself refreshed whatever else went wrong.
    leaderboard_rows_added = _finite_number(
        producer.get("leaderboard_rows_added") if isinstance(producer, dict) else None
    )
    # A positive row count is not enough: the collector records
    # leaderboard_probe_params.complete=false when the endpoint returned a
    # NONEMPTY but INCOMPLETE top-100 (wallet_intelligence_collector.py:235,
    # :264, published at :423). Trusting rows_added alone published ordinary
    # True/False membership off a partial prefix, so wallets outside the fetched
    # range read as definitively off the current leaderboard (Codex P2 wave-20).
    # ONLY a real JSON boolean settles completeness (Codex P2 wave-29).
    # bool("false") is True, so a malformed summary carrying the STRING "false"
    # was read as a complete refresh and published definitive membership off a
    # partial prefix -- the same truthiness hazard _finite_number exists to stop
    # on the numeric side, on the one field here that is not a number.
    # Absent stays None, because older collector summaries genuinely do not
    # publish the field; PRESENT-but-not-a-boolean fails closed to False.
    # KEY PRESENCE, not value-is-None (Codex P2 wave-43). JSON `null`
    # deserialises to Python None, so `raw_complete is None` could not tell an
    # ABSENT legacy field from one that is present and explicitly null -- and
    # the second is precisely the malformed case the adjacent contract says
    # must fail closed. A summary carrying `"complete": null` alongside
    # status="ok", positive leaderboard_rows_added and no legacy
    # requested_limit therefore took the legacy path, left membership_unknown
    # false, and published wallets outside a short fetched prefix as
    # definitively absent. `requested_limit` below already drew this
    # distinction; this is the same defect on the field that one was modelled
    # on.
    probe = producer.get("leaderboard_probe_params") if isinstance(producer, dict) else None
    complete_present = isinstance(probe, dict) and "complete" in probe
    raw_complete = probe.get("complete") if isinstance(probe, dict) else None
    if not complete_present:
        leaderboard_complete = None
    elif isinstance(raw_complete, bool):
        leaderboard_complete = raw_complete
    else:
        leaderboard_complete = False
    # `complete` IS RELATIVE TO A CONFIGURABLE LIMIT, so it alone does not mean
    # a full top-100 (Codex P2 wave-31). wallet_intelligence_collector.py:188
    # takes `limit = min(100, max(1, leaderboard_limit))` and :235/:264 set
    # complete as `len(parsed) >= limit` -- so `leaderboard_limit: 50` yields 50
    # rows and a summary that truthfully reports complete=true for the request
    # it made. This module then asked a DIFFERENT question, "is this wallet in
    # the top 100", and answered a definitive False for every wallet in ranks
    # 51-100 that the producer never fetched.
    #
    # The requested limit must therefore cover what THIS consumer needs. It is
    # published in the same probe params, so no new field is required.
    # ABSENT is the legacy path, exactly as for `complete` above: older
    # collector summaries do not publish requested_limit, and treating that as
    # disqualifying would render membership permanently unknown on every
    # pre-existing summary. Present-but-too-small is the fault this catches.
    # KEY PRESENCE, not value-is-not-None: JSON `null` deserialises to Python
    # None, so `probe.get(...) is not None` cannot tell an absent legacy key
    # from a key that is present and explicitly null -- and the latter is
    # exactly the malformed case this distinguishes.
    requested_limit_present = isinstance(probe, dict) and "requested_limit" in probe
    raw_requested_limit = probe.get("requested_limit") if isinstance(probe, dict) else None
    requested_limit = _finite_number(raw_requested_limit)
    # ABSENT vs PRESENT-BUT-INVALID, the same distinction rule 8e already draws
    # for `complete` (Codex P2 wave-39). Collapsing them let a summary carrying
    # `requested_limit: null` be read as a legacy summary: BOTH gates were then
    # skipped -- the coverage check because it defaults to permissive, and the
    # short-snapshot check because it needs a number -- so a 50-row snapshot
    # could still publish wallets ranked 51-100 as definitively absent. Absent
    # stays the legacy path; present-but-unreadable fails closed.
    requested_limit_invalid = requested_limit_present and requested_limit is None
    limit_covers_consumer = (
        not requested_limit_invalid
        and (requested_limit is None or requested_limit >= limit)
    )
    refreshed = (
        leaderboard_rows_added is not None
        and leaderboard_rows_added > 0
        and leaderboard_complete is not False
        and limit_covers_consumer
    )
    # "disabled" is NOT a licence to publish definitive membership (Codex P2
    # wave-21). A disabled producer refreshes nothing, so the retained snapshot
    # is of unknown currency -- the same reason any other non-refresh state
    # renders membership unknown. It remains a legitimate resting state for the
    # TRADE-ledger rule, where the question is "did the ledger change"; here the
    # question is "is this membership current", and a disabled producer cannot
    # answer it.
    # THE SUMMARY MUST DESCRIBE THE ROWS IT IS VOUCHING FOR (Codex P2 wave-25).
    # The collector writes leaderboard_history.csv at
    # wallet_intelligence_collector.py:372, then issues one holder request per
    # tracked market, and only writes its summary at :434. A flow-toxicity run
    # landing in that gap -- which can be minutes wide -- reads the NEW rows
    # beside the PREVIOUS cycle's summary, and the previous summary's
    # status="ok" and positive leaderboard_rows_added then vouch for rows it
    # never saw. Definitive True/False membership gets published off a snapshot
    # whose completeness is unverified.
    #
    # The two artifacts already share a revision id, so no new field and no
    # change to the collector is needed: every leaderboard row carries
    # snapshot_at_utc, set from the same `snapshot_at` the summary publishes as
    # generated_at_utc (:344, :99-100). If the newest row in the ledger is
    # NEWER than the summary claims to be, the summary is describing an earlier
    # cycle and cannot speak for those rows.
    #
    # Only that direction is a tear. The reverse -- a summary newer than the
    # newest row -- is the ordinary result of a cycle whose leaderboard fetch
    # added nothing, and `refreshed` already handles it. Rows with no parseable
    # stamp cannot be bound at all, so they are unknown too, fail-closed.
    summary_stamp = parse_timestamp(
        producer.get("generated_at_utc") if isinstance(producer, dict) else None
    )
    # EVERY row must carry a parseable stamp, not just one of them (Codex P2
    # wave-29). The wave-25 comprehension silently dropped unparseable stamps,
    # so a ledger holding one good row and one bad one still computed a newest
    # stamp from the good one and read as untorn -- while `_instant_key` below
    # selects the latest snapshot LEXICOGRAPHICALLY, where "not-a-time" sorts
    # above "2026-08-22T00:00:00Z" and becomes the selected instant. The row
    # driving membership was then one that could not be bound to the summary at
    # all. The same hazard exists for a row with a BLANK snapshot_at_utc, since
    # `_instant_key` falls back to snapshot_date and a bare date can outsort a
    # full timestamp. Both are unbindable, so both make membership unknown.
    parsed_stamps = [parse_timestamp(row.get("snapshot_at_utc")) for row in rows]
    unbindable_rows = any(parsed is None for parsed in parsed_stamps)
    row_stamps = [parsed for parsed in parsed_stamps if parsed is not None]
    newest_row_stamp = max(row_stamps) if row_stamps else None
    revision_torn = (
        newest_row_stamp is None
        or summary_stamp is None
        or unbindable_rows
        or newest_row_stamp > summary_stamp
    )
    # THE PRODUCER MUST HAVE RUN RECENTLY ENOUGH TO SPEAK FOR ITS OWN LEDGER
    # (Codex P2 wave-29). If collect_wallet_intel times out or aborts before
    # replacing either artifact, the retained ledger and retained summary stay
    # mutually CONSISTENT with status "ok" -- so every check above passes even
    # when both files are days old, and the artifact publishes definitive
    # membership from a stale snapshot.
    #
    # The bound is READ, never invented, which is what the earlier attempt at
    # this got wrong (see the SECOND KNOWN LIMITATION: comparing summaries to
    # trade_prints.csv broke the normal case and any age threshold looked like
    # an A1 violation). collect_wallet_intel is a STEP of training_harvest
    # (training_harvest.py:88), and WO-85 registers that job's completion
    # freshness ceiling as an exact 25 hours in
    # degraded_state_watchdog.REGISTERED_JOB_FRESHNESS_MAX_SECONDS, a fixed
    # maximum with no config path that can widen it. A summary older than its
    # own job's registered ceiling means the harvest has already missed that SLO
    # and raised its own incident; it cannot also certify current membership.
    # This is an ABSOLUTE freshness read against a registered SLO, not a
    # data-relative window -- rule 12's S1 contract governs `_markout_stats` and
    # is untouched by it.
    #
    # BOTH ENDS of the window are closed (Codex P2 wave-30). A future-dated
    # summary produces a NEGATIVE age, which the one-sided check read as
    # freshly written -- so a collector running on a skewed clock, or a
    # retained pair corrupted to the same future stamp, published definitive
    # membership from evidence that stays stale until wall time catches up.
    # `revision_torn` does not catch it either, since rows and summary moved
    # together. Zero skew is allowed, and that is a literal with a basis rather
    # than an invented tolerance: collect_wallet_intel and flow_toxicity are
    # both steps of the SAME harvest process on the SAME host
    # (training_harvest.py:88), so there is no cross-host clock to reconcile and
    # a summary stamped in the future is malformed, not merely early.
    now = parse_timestamp(now_utc())
    age_seconds = (now - summary_stamp).total_seconds() if (now and summary_stamp) else None
    producer_stale = (
        age_seconds is None
        or age_seconds < 0
        or age_seconds > REGISTERED_HARVEST_FRESHNESS_MAX_SECONDS
    )
    membership_unknown = (
        (producer_status != "ok" and not refreshed)
        or leaderboard_complete is False
        or revision_torn
        or producer_stale
        # DIRECTLY, not only through `refreshed` (Codex P2 wave-37). The first
        # clause ignores `refreshed` entirely when the producer reports "ok", so
        # with leaderboard_limit=50 the producer could truthfully report ok,
        # complete and 50 rows, and membership stayed KNOWN -- publishing every
        # wallet ranked 51-100 as definitively absent from a top-100 the
        # producer never fetched. The short-snapshot check below did not catch
        # it either, because it compares against the producer's own request.
        # This is reachable by supported CONFIGURATION, not just corruption.
        or not limit_covers_consumer
    )
    # TWO sets, because this function has TWO consumers with DIFFERENT
    # contracts, and returning one made every change to it silently rewrite the
    # parent's registered artifact (Codex P1 wave-22 -- the SECOND time, after
    # wave-18; both times a change made for the wallet axis moved
    # smart_fill_count, crowd_fill_count and the tier markouts in
    # flow_toxicity.csv):
    #
    #   tier_wallets   -> the LEGACY market-axis smart/crowd split. Must keep
    #                     the parent's selection exactly: group by
    #                     snapshot_date, rank-sort, take `limit`.
    #   current_wallets -> the NEW on_current_leaderboard column only. Selects
    #                     the latest INSTANT, because two collector runs on one
    #                     UTC date share a snapshot_date and _dedupe_latest
    #                     retains same-day wallets that dropped out of the newer
    #                     top 100, so date-grouping could report a stale wallet
    #                     as currently ranked.
    def _rank_take(subset: list[dict[str, Any]]) -> set[str]:
        ordered = sorted(
            subset,
            key=lambda row: safe_float(row.get("rank")) if safe_float(row.get("rank")) is not None else 1e9,
        )
        return {
            str(row.get("wallet") or "").strip().lower()
            for row in ordered[:limit]
            if str(row.get("wallet") or "").strip()
        }

    latest_date = max(str(row.get("snapshot_date") or row.get("snapshot_at_utc") or "") for row in rows)
    tier_wallets = _rank_take(
        [row for row in rows if str(row.get("snapshot_date") or row.get("snapshot_at_utc") or "") == latest_date]
    )

    def _instant_key(row: dict[str, Any]) -> str:
        return str(row.get("snapshot_at_utc") or row.get("snapshot_date") or "")

    latest_instant = max(_instant_key(row) for row in rows)
    instant_rows = [row for row in rows if _instant_key(row) == latest_instant]
    current_wallets = _rank_take(instant_rows)
    # A COMPLETE REQUEST CAN STILL YIELD A SHORT SNAPSHOT (Codex P2 wave-31).
    # `_dedupe_latest` keys on (snapshot_date, wallet), so a response repeating
    # a wallet collapses to fewer unique rows than the producer fetched, while
    # the summary -- describing the FETCH -- still reports it complete.
    #
    # Measured against the producer's OWN reported request, not against `limit`
    # unconditionally: a venue that genuinely lists fewer than `limit` wallets
    # is not a fault, and a bare `len(current_wallets) < limit` would render
    # every such snapshot unknown forever. When requested_limit is absent we are
    # on the legacy path and cannot make the comparison at all.
    if requested_limit is not None and len(current_wallets) < min(requested_limit, limit):
        membership_unknown = True

    # A nonempty file whose selected snapshot has no usable wallet cells is
    # UNAVAILABLE, not "nobody is on the leaderboard" (Codex P2 wave-15).
    # Returning an empty set with missing_wallet_data False published every
    # measured wallet as definitively off-leaderboard when membership could not
    # be established at all -- the same conflation the absent-file case fixed.
    return tier_wallets, current_wallets, membership_unknown or not current_wallets


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
) -> tuple[int, int, int]:
    """Stream feature corpora into a bounded, disk-backed lookup index.

    Only points inside a token's required markout interval are retained. One
    earliest tail point is also kept so the final trade target can still be
    marked when its next observation falls after the interval. This preserves
    the original first-midpoint-at-or-after lookup without holding every
    decompressed archive row in RAM.

    Returns (scanned, indexed, MALFORMED). The third count is only the rows
    rejected as INVALID -- blank or unparseable venue stamp, unparseable or
    non-finite midpoint, out-of-domain midpoint, unparseable collection time --
    and never the rows skipped legitimately for being outside a token's markout
    interval or for belonging to a token nothing targets (Codex P1 wave-28).
    `scanned - indexed` conflates the two and is not a malformed count.
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
            available_stamp REAL NOT NULL,
            availability_known INTEGER NOT NULL,
            source_order INTEGER NOT NULL
        );
        """
    )
    batch: list[tuple[str, float, float, float, int, int]] = []
    tail_candidates: dict[str, tuple[float, int, float, int, float]] = {}
    scanned_rows = 0
    indexed_rows = 0
    malformed_rows = 0

    def flush() -> None:
        nonlocal indexed_rows
        if not batch:
            return
        connection.executemany(
            "INSERT INTO feature_prices"
            "(asset_id, stamp, midpoint, available_stamp, availability_known, source_order)"
            " VALUES (?, ?, ?, ?, ?, ?)",
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
            # A ROW OF A DIFFERENT CORPUS IS NOT A MALFORMED FEATURE (Codex P2
            # wave-41). `_feature_paths` scans every gzip in
            # polymarket_training_archive, and trade_print_collector.py:243-246
            # deliberately archives EXPIRING TRADE PRINTS into that same
            # directory. Those rows carry `asset_id` -- so they reach here
            # whenever they share a target token -- but no `source_timestamp`
            # and no `midpoint` at all, and the wave-28 counter scored every one
            # of them as a malformed feature. On a healthy production archive
            # that reports widespread feature corruption, which is worse than no
            # counter: an alarm that always fires is one operators learn to
            # ignore.
            #
            # Schema, not content: a row missing BOTH feature columns is a
            # different corpus and is skipped silently. A row that HAS them and
            # cannot be parsed is a genuine malformed feature and still counts.
            if "source_timestamp" not in row and "midpoint" not in row:
                continue
            # VENUE timestamp only -- no fallback to collection time (Codex
            # P1 wave-23). websocket_normaliser.py:165-170 persists a BLANK
            # source_timestamp when an event omits `timestamp`, so the fallback
            # made a delayed event with an UNKNOWN venue time count as the
            # market state at its collection instant: a fill targeting 300 would
            # accept it as a t=301 price and publish a markout for both axes.
            # This is the THIRD instance of this `or` idiom -- trade timestamps
            # (wave-17) and now feature timestamps -- and it is the last one in
            # this module; `grep -n 'collected_at_utc'` finds only the
            # deliberate availability read below.
            source_stamp = _stamp(row.get("source_timestamp"))
            midpoint = safe_float(row.get("midpoint"))
            if source_stamp is None or midpoint is None:
                malformed_rows += 1
                continue
            # ONE effective stamp, not two interacting ones.
            #
            # A price is usable no earlier than BOTH the venue stamping it and
            # us observing it, so the effective time is the later of the two.
            # Carrying venue and collection times separately meant four
            # independent decisions - cursor advance, ranking embargo, staleness
            # ceiling, tie-break - each of which had to remember which clock it
            # cared about, and waves 8 through 12 produced a defect in one of
            # them per round. Collapsing to a single value makes those four
            # decisions consistent by construction.
            #
            # It also fixes the cursor defect directly (Codex P2 wave-12): the
            # cursor advances in effective-stamp order, so a delayed row
            # (sourced 310, collected 1000 -> effective 1000) now sorts AFTER a
            # timely one (sourced and collected 320 -> effective 320) and can no
            # longer be selected and stale-excluded while the usable row is
            # skipped.
            #
            # A collection field that is PRESENT but unparseable or non-finite
            # is rejected outright rather than defaulted to the venue stamp
            # (Codex P1 wave-12): defaulting manufactured an availability time
            # we do not know, and an unverifiable label could then appear
            # available before the split and enter the ranking mean. Absent is
            # different from invalid - an absent field means the corpus predates
            # the column, and the venue stamp is the only evidence there is.
            # TWO stamps, because they answer two different questions -- and
            # collapsing them into one (wave-12) was WRONG (Codex P1 wave-15):
            #
            #   stamp      = the VENUE time. Which market state this price
            #                represents. Gates target eligibility and the
            #                staleness ceiling -- both "is this the market at
            #                the requested horizon".
            #   available  = the later of venue and collection. When we could
            #                first have SEEN it. Gates ranking eligibility only
            #                -- purely a look-ahead question.
            #
            # Under the collapse, a fill targeting 300 would score a quote
            # sourced at 299 and collected at 301: max() made it "reach" the
            # target while its midpoint represents the market BEFORE the
            # horizon, corrupting both axes. Carrying two values is not the
            # defect; conflating what they mean was.
            raw_collected = row.get("collected_at_utc")
            if str(raw_collected or "").strip():
                collected = _stamp(raw_collected)
                if collected is None or not math.isfinite(collected):
                    malformed_rows += 1
                    continue
                stamp = source_stamp
                available = max(source_stamp, collected)
                availability_known = 1
            else:
                # ABSENT collection time: the corpus predates the column, or the
                # row was replayed. The venue stamp is the only evidence there
                # is, so it is used for ordering and markout -- but availability
                # is UNPROVEN, and an unproven availability must not earn a
                # place in the RANKING window, whose whole purpose is that its
                # labels were observable before the split (Codex P1 wave-13).
                # Rejecting such rows outright would zero the artifact on any
                # legacy archive; blocking ranking only is the narrow fix. This
                # is one boolean feeding exactly one decision, not a second
                # clock -- the wave-12 collapse stands.
                stamp = source_stamp
                available = source_stamp
                availability_known = 0
            if stamp < bounds[0]:
                continue
            # safe_float parses "inf"/"nan" here too (Codex P1 wave-6 on #451:
            # the wave-5 fix validated only TRADE fields). An inf midpoint
            # yields an infinite markout that is emitted as a successfully
            # scored wallet mean; a nan midpoint reaches an INSERT into a
            # `midpoint REAL NOT NULL` column, where SQLite stores NaN as NULL
            # and the constraint aborts the whole build. Rejected before either.
            if not math.isfinite(stamp) or not math.isfinite(midpoint):
                malformed_rows += 1
                continue
            # Finite is not enough on this side either (Codex P2 wave-16). A
            # binary-market midpoint is a probability in [0, 1]; the upstream
            # normaliser does not enforce bounds, so a malformed venue row can
            # reach here and publish a healthy-looking markout outside the
            # possible payoff range. Same rule the trade side already applies --
            # the asymmetry was mine, added last wave on one boundary only.
            if not (0.0 <= midpoint <= 1.0):
                malformed_rows += 1
                continue
            source_order = scanned_rows
            if stamp <= bounds[1]:
                batch.append((token, stamp, midpoint, available, availability_known, source_order))
                if len(batch) >= 10_000:
                    flush()
                continue
            # Ordered (stamp, source_order, midpoint) so the tuple comparison
            # below breaks ties on ARRIVAL ORDER, never on price. This is the
            # second place ordering mattered: the SQL ORDER BY decides among
            # indexed rows, but for a token whose only candidates fall past the
            # target bound it is THIS comparison that picks the single retained
            # row, and with midpoint second it picked whichever price was
            # numerically smaller (Codex P1 wave-9).
            # Same key order as the SQL ORDER BY: venue stamp, then AVAILABLE
            # stamp, then arrival order. This is the second tie-break site and
            # it must agree with the first, or which row is retained depends on
            # which code path saw it (the wave-9 defect, in its third form).
            # PROVEN availability sorts ahead of unknown at the same venue
            # stamp (Codex P2 wave-16): otherwise a legacy row with no
            # collection time -- whose available_stamp defaults to the venue
            # stamp, so it ties -- is selected first and then embargoed for
            # being unproven, while a proven row at the same instant that was
            # collected before the split and could validly rank is never
            # considered. Negated so 1 (proven) sorts before 0 (unknown), and
            # kept identical to the SQL ORDER BY above.
            candidate = (stamp, -availability_known, available, source_order, midpoint)
            current = tail_candidates.get(token)
            if current is None or candidate < current:
                tail_candidates[token] = candidate

    for token, (stamp, neg_known, available, source_order, midpoint) in tail_candidates.items():
        availability_known = -neg_known
        batch.append((token, stamp, midpoint, available, availability_known, source_order))
    flush()
    connection.commit()
    connection.execute(
        "CREATE INDEX feature_prices_token_stamp_idx "
        "ON feature_prices(asset_id, stamp, source_order, midpoint, available_stamp, availability_known)"
    )
    connection.commit()
    return scanned_rows, indexed_rows, malformed_rows


def _finite_number(value: Any) -> float | None:
    """A finite float, or None if the value is not one.

    Booleans are rejected BEFORE conversion: safe_float(True) is 1.0 and
    safe_float(False) is 0.0, so a JSON/YAML boolean sails through any plain
    finite check. That has now been a separate finding three waves running --
    split_stamp (wave-10), markout_horizon_minutes (wave-13), markets_polled
    (wave-14) -- so the guard lives in one place rather than being rediscovered
    per field.
    """
    if isinstance(value, bool):
        return None
    number = safe_float(value)
    if number is None or not math.isfinite(number):
        return None
    return number


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
    value = _finite_number(persisted.get("split_stamp"))
    if value is None or value < 0:
        return None
    # The artifact must also assert its own evidence class (Codex P1 wave-18).
    # A persisted split with the flags absent, or with either set true, was
    # reused indefinitely and reported as frozen while the file itself claimed
    # an unknown or forbidden provenance. Both must be PRESENT and exactly
    # False, or the state takes the invalid-frozen-state path.
    if persisted.get("paper_trading_invoked") is not False:
        return None
    if persisted.get("live_trading_invoked") is not False:
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
    value = _finite_number(persisted.get("horizon_seconds"))
    if value is None:
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
        return (_median_stamp(stamps)), False
    stamps = sorted(float(trade["stamp"]) for trade in trades)
    split_stamp = _median_stamp(stamps)
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
# Mapped to the poll evidence EACH producer actually publishes (Codex P1
# wave-13). collect_trade_prints and the explicit-market collector both always
# initialise and publish markets_polled (trade_print_collector.py:373, :487), so
# for those two an ABSENT count means a malformed or legacy summary and is not
# refresh evidence. backfill_trade_prints never writes markets_polled at all --
# it reports candidate_markets/markets_attempted -- and its no-op outcomes have
# their own distinct statuses, so for it an "ok" status IS the evidence. The
# earlier blanket "absent does not disqualify" rule was right for backfill and
# wrong for the other two.
_TRADE_LEDGER_PRODUCER_SUMMARIES = (
    ("trade_prints_summary.json", True),
    ("maker_portfolio_trade_prints_summary.json", True),
    ("trade_print_backfill_summary.json", False),
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
    # Producers that both reported ok AND show evidence of an actual poll.
    refreshing: list[str] = []
    for filename, polled_required in _TRADE_LEDGER_PRODUCER_SUMMARIES:
        payload = read_json(root / filename)
        if not isinstance(payload, dict):
            statuses.append("missing")
            continue
        status = str(payload.get("status") or "missing").strip().lower()
        statuses.append(status)
        # "ok" alone is not evidence that the venue was actually polled (Codex
        # P1 wave-11 on #451): collect_maker_replay_data reaches the
        # explicit-market collector, which reports market_source_status="empty"
        # and still writes status="ok" with markets_polled=0
        # (trade_print_collector.py:406-410, 484-487). A producer counts as
        # having REFRESHED the ledger only if it also shows poll evidence.
        # Narrow deliberately: only EXPLICIT evidence of no poll disqualifies.
        # An absent field is not evidence of absence -- backfill_trade_prints
        # reports candidate_markets/markets_attempted and never writes
        # markets_polled at all, so treating a missing field as "did not poll"
        # would disqualify a producer that legitimately refreshed the ledger.
        # (I wrote that broader version first; the registered tests rejected it.)
        polled = _finite_number(payload.get("markets_polled"))
        source_state = str(payload.get("market_source_status") or "").strip().lower()
        # A count must be finite and positive: safe_float returns None for
        # garbage and NaN makes `polled <= 0` false, so both would otherwise
        # sail through as valid poll evidence (Codex P1 wave-12). For the two
        # producers that always publish the field, ABSENT is also disqualifying
        # (Codex P1 wave-13).
        bad_count = polled is None or polled <= 0
        if polled_required:
            no_poll = source_state == "empty" or bad_count
        else:
            no_poll = source_state == "empty" or ("markets_polled" in payload and bad_count)
        if status == "ok" and not no_poll:
            refreshing.append(status)
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
    if not any(status == "ok" for status in refreshing):
        # Nothing actually refreshed the ledger this run while flow-toxicity
        # itself stayed enabled (Codex P1 wave-8). Returning "ok" would stamp
        # retained rows as current and let them permanently seed the frozen
        # split. Disclosed, not fatal: rows are still measured and stamped.
        return "no_producer_refreshed"
    return "ok"


def _markout_stats(
    connection: sqlite3.Connection,
    trades: list[dict[str, Any]],
    tier_wallets: set[str],
    horizon_seconds: float,
    split_stamp: float,
) -> dict[str, dict[str, float | int]]:
    """Replay markouts over RECORDED HISTORY on a data-relative clock.

    S1 CONTRACT, stated here because Engineering Standards S1 requires the
    DOCSTRING to say it and not merely a comment in the body (Codex P2
    wave-16): every window below -- the median split, the ranking and
    evaluation windows, the label embargo, the staleness ceiling -- is derived
    from the corpus's own fill and feature timestamps, NEVER from the run
    clock. S1 permits a data-relative window only for replaying recorded
    history, which is exactly what this is: a backward-looking replay over an
    immutable trade ledger.

    It is NOT a freshness window and must never be read as one. Nothing here
    says whether the corpus is current; that is a separate question answered by
    the producer-status rule, and a caller that mistakes this for a freshness
    check will read a stale archive as live evidence.
    """
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
                # Ties on the effective stamp break by ARRIVAL ORDER, never by
                # midpoint (Codex P1 wave-9 on #451): ordering by price made
                # both which row is selected and whether it is embargoed depend
                # on which number happened to be smaller.
                # Ties on the VENUE stamp break by AVAILABLE stamp, never by
                # midpoint (Codex P1 wave-9): among quotes representing the same
                # venue instant, the one we could see EARLIEST is the one a
                # trader could have acted on; preferring a later correction is
                # hindsight, and ordering by price makes the choice depend on
                # which number happens to be smaller.
                "SELECT stamp, midpoint, available_stamp, availability_known FROM feature_prices "
                "WHERE asset_id = ? "
                "ORDER BY stamp, availability_known DESC, available_stamp, source_order",
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
            tier = "smart" if trade["wallet"] and trade["wallet"] in tier_wallets else "crowd"
            market_stats[f"{tier}_count"] += 1
            market_stats[f"{tier}_sum"] += markout
            if entry is not None:
                # STALENESS IS MEASURED ON THE VENUE STAMP, and this comment
                # says so because the code says so: wave-11 changed this to the
                # later of venue and collection time, and wave-15 REVERTED that
                # as a correctness error -- the comment outlived the revert.
                # Staleness asks how far past the markout target the quote's
                # market state sits, and only the venue stamp answers that. A
                # quote that IS the t+horizon state is not stale merely because
                # our collector was slow; late collection makes a price unusable
                # for RANKING, which is the look-ahead question handled below on
                # available_stamp, and it is not a second clock here.
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
                # Ranking eligibility is the LOOK-AHEAD question, so it uses
                # the AVAILABLE stamp, not the venue stamp.
                if window == "ranking" and (
                    float(current_feature[2]) >= split_stamp or not int(current_feature[3])
                ):
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


def _trade_rows(
    cfg: EngineConfig,
) -> tuple[list[dict[str, Any]], int, int, set[str], set[str], int, bool]:
    """Parsed fills, plus WHICH markets and tokens lost rows to rejection.

    The counts alone were not enough (Codex P1 wave-26). A market with both
    valid and rejected rows still produces a fresh toxicity row, computed from
    an incomplete sample, and that row can read CLEAN and replace a prior
    blocking one -- so knowing only that "some rows were rejected somewhere"
    cannot protect the market that actually lost coverage.

    Three attribution outcomes, in decreasing order of what they let the veto
    rules do (the token tier added at wave-33):

      rejected_markets  -- the row named a market. That market's sample is
                           incomplete and only it is affected.
      rejected_tokens   -- the row named no market but DID name a token.
                           requote_alerts keys its toxicity map by asset_id too,
                           so this is still addressable; treating it as wholly
                           unattributable over-tainted the table and left a
                           token with no other coverage with no veto at all.
      unattributable    -- neither identifier readable. Nothing can be pinned to
                           it, so every market's absence becomes unverifiable.
    """
    parsed: list[dict[str, Any]] = []
    malformed_rows = 0
    source_rows = 0
    rejected_markets: set[str] = set()
    rejected_tokens: set[str] = set()
    unattributable_rejections = 0
    path = cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv"
    # PRESENCE is tracked separately from emptiness (Codex P2 wave-39).
    # `_iter_csv_any` yields nothing for an ABSENT file exactly as it does for a
    # header-only one, so a deleted or never-published ledger was
    # indistinguishable from the deliberately supported quiet-day corpus: with
    # retained producer summaries still healthy the run emitted the benign
    # `no_wallets_scored` sentinel and reported status "ok", presenting MISSING
    # INPUT as healthy evidence.
    ledger_present = path.exists()
    for row in _iter_csv_any(path):
        source_rows += 1
        market = str(row.get("market") or "").strip()
        token = str(row.get("asset_id") or row.get("token_id") or "").strip()
        price = safe_float(row.get("price"))
        size = safe_float(row.get("size"))
        # The VENUE timestamp only -- never a fallback to collection time
        # (Codex P1 wave-17). trade_print_collector.py:161 persists a BLANK
        # timestamp when /trades omits both `timestamp` and `matchTime`, beside
        # a collected_at_utc of now(). Falling back made a delayed or backfilled
        # historical fill look as though it occurred when it was FETCHED, moving
        # both its markout target and its ranking/evaluation window by hours or
        # days. This is the same venue-vs-collection conflation fixed across
        # three waves on the FEATURE side; it was sitting on the trade side the
        # whole time, in the identical `or` idiom.
        stamp = _stamp(row.get("timestamp"))
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
            # Counted too (Codex P2 wave-20): these basic-shape rejections were
            # silent, so a wholly malformed ledger looked like an empty one.
            malformed_rows += 1
            if market:
                rejected_markets.add(market)
            elif token:
                # A blank market is not necessarily unattributable (Codex P2
                # wave-33). requote_alerts.py:141 keys its toxicity map by
                # market, asset_id AND condition_id, and :503 looks a quote up
                # by condition_id OR token_id -- so a token alone is enough to
                # address a veto. Treating this as wholly unattributable both
                # over-tainted the rest of the table and, for a token with no
                # other coverage, left NO addressable row at all.
                rejected_tokens.add(token)
            else:
                # Neither identifier readable: the loss cannot be attributed to
                # anything, so no single market's veto can be protected by it
                # and the whole table is unverifiable instead.
                unattributable_rejections += 1
            continue
        if not math.isfinite(price) or not math.isfinite(size) or not math.isfinite(stamp):
            malformed_rows += 1
            rejected_markets.add(market)
            continue
        # Finite is not enough (Codex P2 wave-15). A binary-market share price
        # is a probability in [0, 1] by construction, so price=1.5 is not a
        # print; and size <= 0 is not a fill -- it would increment the wallet's
        # counters while contributing no VPIN volume, publishing a successful
        # markout for something that never traded. Same domain reasoning as the
        # edge-attribution price rule.
        if not (0.0 <= price <= 1.0) or size <= 0:
            malformed_rows += 1
            rejected_markets.add(market)
            continue
        # NO negative-timestamp guard here, deliberately (Codex P1 wave-31).
        # wave-21 added one and wave-30 registered it as a parent-artifact
        # difference; both were wrong, and the branch was UNREACHABLE the whole
        # time. `_stamp` is `utils.normalize_external_timestamp`, which returns
        # None for a negative epoch on BOTH its numeric and its parsed branch
        # (utils.py:57-72), so `stamp is None` above has already rejected the
        # row. The parent used the same normaliser, so this was never a
        # behaviour change either. Dead code shaped like a live guard is worse
        # than no guard: it misled this spec into registering a difference that
        # does not exist. The contract is pinned by
        # `test_stamp_never_returns_a_negative_epoch` instead, so a future
        # change to the normaliser breaks a test rather than silently reopening
        # the hole this branch pretended to close.
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
    return (
        parsed,
        malformed_rows,
        source_rows,
        rejected_markets,
        rejected_tokens,
        unattributable_rejections,
        ledger_present,
    )


def build_flow_toxicity(cfg: EngineConfig) -> dict[str, Any]:
    """Score the market and wallet toxicity axes.

    Serialised under the `flow_toxicity` runtime lock (Codex P2 wave-17). The
    check/create/build sequence around the frozen split is NOT a transaction: a
    manual CLI invocation overlapping the harvest could have both processes see
    the split file absent, compute DIFFERENT medians from different atomic
    ledger revisions, and interleave their writes -- leaving one process's
    wallet rows paired with the other's persisted cutoff, so the published
    windows could not be reproduced from the state on disk. A contended run
    writes nothing and returns skipped_locked, matching the idiom in
    cost_ledger.py:310.
    """
    # NO age-based stealing at all (Codex P2 wave-23). A literal ceiling cannot
    # work here: the harvest step timeout is configurable with no upper bound
    # (training_harvest.py:145-159) and manual invocations are unbounded, so any
    # number I pick can be exceeded by a legitimate build -- and
    # runtime_lock.py:144-164 unlinks a still-live lock on age ALONE, with no
    # liveness check. Passing 0 disables the age and corrupt-payload steal paths
    # while leaving the same-pid orphan check intact.
    #
    # The trade-off is stated rather than hidden: a process that dies HARD
    # leaves this lock behind and every later run returns skipped_locked until
    # an operator removes outputs/polymarket_runtime/flow_toxicity.lock. That is
    # the correct direction for this artifact -- a stuck lock leaves the market
    # veto intact and merely stale, whereas a stolen lock produces interleaved
    # writes and wallet rows paired with another process's cutoff, which is
    # unreproducible and silent. PREREQUISITE, registered: promoting the wallet
    # axis to a standing input wants a liveness-checking lease in runtime_lock
    # itself, which is a shared module with many callers and its own work order.
    # The handler wraps the `with` ITSELF, not just its body (Codex P2 wave-39).
    # Entering runtime_lock can raise on its own -- a permissions or filesystem
    # error on outputs/polymarket_runtime alone is enough -- and that happens
    # BEFORE the inner try is active. The advertised generic failure path then
    # wrote neither the build_failed summary nor its wallet sentinel, so
    # yesterday's rows stayed readable as artifact_status="ok" while the harvest
    # recorded a failed step: the exact fail-open rule 9's build_failed sentinel
    # exists to close, reached through the one statement outside its reach.
    try:
        return _build_flow_toxicity_under_lock(cfg)
    except Exception as exc:
        if not getattr(exc, "_flow_toxicity_handled", False):
            _invalidate_wallet_evidence_on_abort(cfg, exc)
        raise


def _invalidate_wallet_evidence_on_abort(cfg: EngineConfig, exc: BaseException) -> None:
    """Replace the wallet artifact and summary with build_failed sentinels."""
    out_root = cfg.output_root / "maker_carry"
    generated_at = now_utc()
    write_json(
        out_root / "flow_toxicity_summary.json",
        {
            "status": "build_failed",
            "generated_at_utc": generated_at,
            "work_order": "WO-49",
            "error": f"{type(exc).__name__}: {exc}",
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )
    write_csv(
        out_root / "flow_toxicity_wallets.csv",
        [_wallet_sentinel_row(generated_at, "build_failed")],
        fieldnames=WALLET_MARKOUT_FIELDS,
    )


def _build_flow_toxicity_under_lock(cfg: EngineConfig) -> dict[str, Any]:
    with runtime_lock(cfg, "flow_toxicity", stale_after_seconds=0.0) as lock:
        if not lock.acquired:
            return {
                "status": "skipped_locked",
                "generated_at_utc": now_utc(),
                "work_order": "WO-49",
                "runtime_lock": lock.as_dict(),
                "paper_trading_invoked": False,
                "live_trading_invoked": False,
            }
        try:
            return _build_flow_toxicity_locked(cfg)
        except Exception as exc:
            # Any unexpected abort -- a truncated .csv.gz, a disk error -- would
            # otherwise escape before the wallet artifact or summary were
            # rewritten, leaving YESTERDAY's rankings on disk still saying
            # artifact_status="ok" while the resilient harvest merely records a
            # failed step (Codex P2 wave-19). The deliberate failure paths write
            # their own, more specific sentinels and set _HANDLED_FAILURE, so
            # this does not overwrite them.
            if not getattr(exc, "_flow_toxicity_handled", False):
                _invalidate_wallet_evidence_on_abort(cfg, exc)
            raise


def _build_flow_toxicity_locked(cfg: EngineConfig) -> dict[str, Any]:
    settings = _settings(cfg)
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
    # Hoisted above the frozen-split gate below, which validates the persisted
    # state against the horizon it was frozen under -- and validated here,
    # before any artifact is read or written (Codex P1 wave-12). A nonnumeric
    # value raised from float() while the previous artifact_status="ok" rows sat
    # readable on disk; NaN or a negative value made every target and staleness
    # comparison fail OPEN, publishing meaningless markouts and an unusable
    # frozen split. Configuration corruption must not be able to preserve or
    # manufacture apparently healthy evidence.
    horizon_setting = settings.get("markout_horizon_minutes")
    # safe_float(True) is 1.0, so YAML `markout_horizon_minutes: true` would
    # silently score both axes at a ONE-MINUTE horizon and permanently freeze
    # that unintended horizon while publishing healthy-looking artifacts (Codex
    # P2 wave-13). Rejected before conversion, as the split-state validators do.
    horizon_raw = _finite_number(horizon_setting)
    invalid_horizon = horizon_raw is None or horizon_raw <= 0
    # The CONVERTED value needs its own check, and it has to happen HERE rather
    # than at the point of use (Codex P2 wave-29). A finite but enormous
    # markout_horizon_minutes -- 1e308 -- passes the guard above and overflows
    # to inf when multiplied by 60. The run then persisted
    # horizon_seconds: Infinity into the split state, which every LATER run
    # rejected because _finite_number cannot read it back: a writer creating
    # state its own reader refuses, the third instance of that shape here after
    # rule 0b's negative stamps and wave-10's boolean split. Deciding it later
    # would set the flag AFTER the invalidation branch below has already read
    # it, so no sentinel would be written and the fault would be silent.
    horizon_seconds = (horizon_raw or 5.0) * 60.0
    if not math.isfinite(horizon_seconds) or horizon_seconds <= 0:
        invalid_horizon = True
        horizon_seconds = 5.0 * 60.0
    if invalid_horizon:
        # DETECTED here, RAISED after the market axis is rebuilt (Codex P1
        # wave-14). Raising here left the previous flow_toxicity.csv intact,
        # so requote_alerts.py and stage_ticket_eligibility.py kept reading a
        # formerly-clean toxicity row after the flow turned toxic -- the same
        # defect wave-11 fixed on the split path, which I then recreated on this
        # one. Invalidating the market artifact instead would be WORSE: absent
        # rows make requote_alerts' `tox_record` None and the veto fails OPEN
        # for every market, where a stale row at least still blocks the markets
        # it already flagged. So the veto fields, which are computed from trade
        # volume buckets and do NOT depend on the horizon, are rebuilt; only the
        # markout columns are left empty.
        write_csv(
            out_root / "flow_toxicity_wallets.csv",
            [_wallet_sentinel_row(generated_at, "invalid_markout_horizon")],
            fieldnames=WALLET_MARKOUT_FIELDS,
        )
    horizon = horizon_seconds

    # DETECT here, but do NOT raise here (Codex P1 wave-11 on #451). Raising
    # before the market-axis rebuild was a safety regression I introduced in
    # wave 8: flow_toxicity.csv feeds the toxicity VETO that requote_alerts.py
    # (:135, :508) and stage_ticket_eligibility.py read, so a corrupt WALLET
    # file would freeze the market-axis table and let a market that has since
    # turned toxic keep reading clean - on every harvest, until an operator
    # repaired an unrelated wallet artifact. A wallet-side fault must degrade
    # the wallet axis only. The wallet artifact is invalidated immediately, the
    # market axis is rebuilt normally, and the raise happens at the END so the
    # harvest still records a loud failure.
    split_path = out_root / "flow_toxicity_wallet_split.json"
    invalid_split_state = False
    if split_path.exists():
        persisted = read_json(split_path)
        stored = _split_stamp_value(persisted)
        # The horizon-mismatch check is SKIPPED when the configured horizon is
        # itself invalid (Codex P2 wave-19). An invalid horizon substitutes an
        # arbitrary 5-minute fallback purely so the horizon-independent veto can
        # rebuild; comparing a sound persisted split against that fallback
        # falsely condemned it -- a valid 2-minute split plus a temporarily
        # malformed setting was labelled invalid_frozen_split_state, overwriting
        # the invalid-horizon sentinel, even though restoring the setting makes
        # the split valid again. One fault must not manufacture a second.
        if stored is None or (not invalid_horizon and _frozen_horizon_mismatch(persisted, horizon)):
            invalid_split_state = True
            write_csv(
                out_root / "flow_toxicity_wallets.csv",
                [_wallet_sentinel_row(generated_at, "invalid_frozen_split_state")],
                fieldnames=WALLET_MARKOUT_FIELDS,
            )
    (
        trades,
        malformed_trade_rows,
        trade_source_rows,
        rejected_markets,
        rejected_tokens,
        unattributable_rejections,
        trade_ledger_present,
    ) = _trade_rows(cfg)
    trade_corpus_status = _trade_corpus_status(cfg)
    tier_wallets, current_wallets, missing_wallet_data = _top_wallets(cfg)
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
    malformed_feature_rows = 0
    price_index_disk_bytes = 0
    # Skipped entirely on an invalid horizon: every markout is defined relative
    # to it, so none can be computed. The veto fields below come from trade
    # volume buckets and are horizon-independent, so they still rebuild.
    if trades and not invalid_horizon:
        with TemporaryDirectory(prefix="polymarket-flow-toxicity-") as temp_dir:
            database_path = Path(temp_dir) / "price_index.sqlite3"
            connection = sqlite3.connect(database_path)
            try:
                feature_rows_scanned, feature_rows_indexed, malformed_feature_rows = _build_price_index(
                    cfg,
                    connection,
                    _price_target_bounds(trades, horizon),
                )
                if invalid_split_state:
                    # Already detected and the wallet artifact already
                    # invalidated; the run will raise once the market axis is
                    # written. Do not re-enter the freeze path, whose defensive
                    # raise would fire here and block that rebuild.
                    stamps = sorted(float(trade["stamp"]) for trade in trades)
                    split_stamp = _median_stamp(stamps)
                    split_was_frozen = False
                else:
                    split_stamp, split_was_frozen = _frozen_split_stamp(
                        cfg,
                        trades,
                        # A LOCALLY rejected row makes the ledger partial in
                        # exactly the sense rule 10 forbids freezing against
                        # (Codex P1 wave-26). Checking only the producer status
                        # let a run persist a median computed from the surviving
                        # subset; once the malformed rows were corrected, every
                        # later run reused that contaminated cutoff FOREVER,
                        # permanently changing which markets rank and which are
                        # evaluated. The rejected run must not outlive itself.
                        stamps_ok=trade_corpus_status == "ok" and not malformed_trade_rows,
                        horizon_seconds=horizon,
                    )
                markout_by_market, markout_by_wallet = _markout_stats(
                    connection, trades, tier_wallets, horizon, split_stamp
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
    # COUNTED BEFORE any carry, retention or synthetic block is added (Codex P2
    # wave-32). `len(rows_out)` at the end includes rows the current corpus did
    # NOT produce -- a carried prior row, a retained prior blocking row, a
    # synthetic row for a market nothing could be measured on -- so coverage
    # telemetry reported them as markets this run scored. One freshly measured
    # market beside one carried veto read markets_scored 2. The separate
    # carry/retention/unmeasurable counters stay; this one now means what its
    # name says.
    markets_freshly_scored = len(rows_out)
    if trade_source_rows and not trades:
        # PRESERVE the previous market rows (Codex P1 wave-21). A nonempty
        # ledger whose rows all fail parsing leaves rows_out empty, and writing
        # a header-only flow_toxicity.csv would leave requote_alerts.py:502-535
        # with no tox_record at all -- clearing the ACTIVE veto for every market
        # on a corrupt refresh. Same reasoning as rule 8a: an absent toxicity
        # record fails OPEN, so stale-blocking is strictly safer than absent.
        # The failure is disclosed on the wallet artifact and in the summary,
        # which are the honest places for it.
        summary["market_axis_preserved"] = True
        # PRESERVING IS NOT ENOUGH ON THIS PATH EITHER (Codex P1 wave-34).
        # Rules 13f-13j were all built in the `else` branch, so when EVERY row
        # is rejected none of them ran: a previously clean market kept
        # toxic_blocked=false, and an attributable market seen for the first
        # time got no row at all. requote_alerts.py:502-535 raises nothing in
        # either case, even though the current sample is WHOLLY unmeasurable --
        # which is the strongest case for blocking there is, and the one path
        # that skipped it.
        #
        # The all-rejected corpus taints everything by construction: nothing
        # survived, so no market's coverage is verified. Every retained row is
        # converted, and every attributable market with no row at all gets a
        # synthetic one, on exactly the rule 13g terms.
        preserved_rows = read_csv_rows(path)
        preserved_markets = {
            str(row.get("market") or "") for row in preserved_rows if str(row.get("market") or "")
        }
        # SCOPED, exactly as the surviving-rows path is (Codex P2 wave-35).
        # Converting every preserved row here bypassed rejected_markets and
        # rejected_tokens entirely, so a market that merely AGED OUT of the
        # rolling ledger -- with nothing to do with the corruption -- was
        # blocked as unmeasurable. Worse than a stale veto: requote_alerts does
        # not age these rows, so that market would stay blocked indefinitely,
        # and the all-rejected branch would contradict the scoped behaviour
        # rules 13d and 13f apply whenever any row survives.
        #
        # "Every row was rejected" does not mean "every market is implicated".
        # It means the rows the ledger HELD were all bad, and those rows name
        # which markets and tokens lost coverage. Only a rejection naming
        # NEITHER identifier leaves every absence unverifiable.
        taint_everything = bool(unattributable_rejections)

        def _implicated(row: dict[str, Any]) -> bool:
            if taint_everything:
                return True
            return (
                str(row.get("market") or "") in rejected_markets
                or str(row.get("asset_id") or "") in rejected_tokens
            )

        converted = 0
        for row in preserved_rows:
            if str(row.get("toxic_blocked") or "").strip().lower() == "true":
                continue
            if not _implicated(row):
                continue
            row["toxic_blocked"] = True
            reasons = [r for r in str(row.get("toxicity_block_reasons") or "").split(";") if r]
            reasons.append("unmeasurable_sample")
            row["toxicity_block_reasons"] = ";".join(reasons)
            converted += 1
        synthetic = [
            *((market, "") for market in sorted(rejected_markets - preserved_markets)),
            *(
                ("", token)
                for token in sorted(
                    rejected_tokens
                    - {str(row.get("asset_id") or "") for row in preserved_rows}
                )
                if token
            ),
        ]
        for market, asset_id in synthetic:
            preserved_rows.append(
                {
                    "generated_at_utc": generated_at,
                    "market": market,
                    "asset_id": asset_id,
                    "toxicity_score": 0.0,
                    "vpin_raw": 0.0,
                    "volume_buckets": 0,
                    "trades_seen": 0,
                    "smart_fill_count": 0,
                    "crowd_fill_count": 0,
                    "smart_fill_markout": None,
                    "crowd_fill_markout": None,
                    "missing_price_points": 0,
                    "raw_imbalance_block": False,
                    "percentile_block": False,
                    "markout_coverage_ratio": 0.0,
                    "toxic_blocked": True,
                    "toxicity_block_reasons": "wholly_rejected_sample",
                }
            )
        if converted:
            summary["market_blocks_on_carried_clean_rows"] = converted
        if synthetic:
            summary["market_blocks_on_unmeasurable_sample"] = len(synthetic)
        if converted or synthetic:
            write_csv(path, preserved_rows, fieldnames=TOXICITY_FIELDS)
    else:
        # PARTIAL exclusions clear vetoes too (Codex P1 wave-25). The branch
        # above catches only the all-rejected corpus. When SOME rows survive,
        # a market whose every row was rejected this run simply vanishes from
        # rows_out, and an absent tox_record is NO BLOCK at requote_alerts.py
        # :502-535 -- so one bad market silently loses its veto while the run
        # reports status "ok".
        #
        # The remedy is a UNION, not a wholesale preserve. Preserving the whole
        # table would be worse than writing it: a market that just turned toxic
        # would keep its old clean record and fail OPEN in the other direction.
        # Fresh rows always win; a prior row is carried forward ONLY for a
        # market the fresh table does not mention.
        #
        # Gated on rows actually having been excluded, because a market leaving
        # the table is NORMAL: the ledger is a rolling window and a market with
        # no recent fills legitimately ages out. Carrying forward unconditionally
        # would pin every veto that ever fired, forever. Exclusions are the only
        # condition under which absence is unverifiable rather than meaningful.
        #
        # A carried row keeps its ORIGINAL generated_at_utc, so its age is
        # visible in the artifact itself -- it is a stale veto, disclosed as
        # stale, not a fresh measurement.
        if malformed_trade_rows:
            fresh_by_market = {str(row["market"]): row for row in rows_out}
            fresh_rows_by_market = dict(fresh_by_market)
            # A market can lose its veto TWO ways, and wave-25 closed only one.
            #
            #   (a) every row rejected -> the market vanishes from rows_out, and
            #       an absent tox_record is NO BLOCK at requote_alerts.py:502-535.
            #   (b) SOME rows rejected -> a fresh row is still produced, but from
            #       an INCOMPLETE sample, and that row can read clean and
            #       overwrite a prior blocking one (Codex P1 wave-26).
            #
            # (b) is the more dangerous of the two, because the artifact looks
            # healthy: a fresh row with a fresh timestamp, computed off a sample
            # that is quietly missing the very prints that made the market toxic.
            #
            # The rule for (b) is one-directional, so it can never HIDE toxicity:
            # a fresh row that BLOCKS always wins, because newly-measured
            # toxicity is real information. Only a fresh row that does NOT block,
            # for a market that lost rows this run, defers to a prior blocking
            # row. Unblocking requires a complete sample; blocking does not.
            #
            # Rejections whose `market` field was itself unreadable cannot be
            # attributed, so they taint EVERY market: with those present, any
            # market could be the one that lost coverage.
            #
            # THE TWO BLOCK KINDS DO NOT HAVE THE SAME BLAST RADIUS, and wave-26
            # treated them as if they did (Codex P1 wave-27):
            #
            #   raw_imbalance_block is MARKET-LOCAL -- it reads this market's own
            #     VPIN, so a market that lost no rows still measures it correctly
            #     no matter what happened elsewhere in the corpus.
            #   percentile_block is UNIVERSE-RELATIVE -- `toxicity_score` comes
            #     from _percentiles() over every surviving market, so rejecting
            #     rows from market A changes A's VPIN, changes the ORDERING, and
            #     can push an untouched market B from just above 0.90 to exactly
            #     0.90. B loses its veto having lost nothing at all.
            #
            # So the protected set differs by kind. A market that lost rows has
            # BOTH readings unverifiable. Every other fresh market has a
            # trustworthy raw reading and an untrustworthy percentile one, so it
            # is protected wherever its prior block had a PERCENTILE COMPONENT --
            # whether or not a raw one coincided (Codex P1 wave-41; the earlier
            # "percentile-ONLY" reading is answered at the predicate below).
            # A FLAG, not a set: "taint everything" has to cover markets ABSENT
            # from the fresh table too, and `set(fresh_by_market)` by
            # construction cannot contain one -- so the set form silently failed
            # to protect exactly the departed markets case (a) exists for.
            taint_all = bool(unattributable_rejections)

            def _lost_rows(market: str) -> bool:
                return taint_all or market in rejected_markets
            carried: list[dict[str, Any]] = []
            carried_clean_blocked: list[str] = []
            retained_blocks = 0
            prior_rows = read_csv_rows(path)
            prior_by_market = {
                str(row.get("market") or ""): row
                for row in prior_rows
                if str(row.get("market") or "")
            }
            # TOKEN-ONLY rows are addressable too, and indexing solely by market
            # dropped them (Codex P1 wave-37). Rule 13j's orphan-token synthetic
            # rows carry a BLANK market and only an asset_id, so the
            # market-keyed map above excludes them entirely: under a global
            # taint they were neither carried nor recreated, and the rewrite
            # below then DELETED a veto this module had itself created --
            # leaving requote_alerts.py:503, which looks up by token id, with no
            # record for that token at all. A fail-open produced by the
            # preservation machinery rather than despite it.
            prior_by_token = {
                str(row.get("asset_id") or ""): row
                for row in prior_rows
                if str(row.get("asset_id") or "") and not str(row.get("market") or "")
            }
            # A token-only rejection is resolved to its market where the table
            # already knows the pairing (Codex P2 wave-33), so the ordinary
            # market rules apply to it. Only a token with NO coverage anywhere
            # needs a token-addressable row of its own.
            token_to_market: dict[str, str] = {}
            for row in (*rows_out, *prior_rows):
                token = str(row.get("asset_id") or "").strip()
                market_name = str(row.get("market") or "").strip()
                if token and market_name:
                    token_to_market.setdefault(token, market_name)
            orphan_tokens = set()
            for token in rejected_tokens:
                resolved = token_to_market.get(token)
                if resolved:
                    rejected_markets.add(resolved)
                else:
                    orphan_tokens.add(token)
            # Token-only prior rows first: they have no market to match against
            # the fresh table, so the only questions are whether this run
            # re-created them and whether their absence is verifiable.
            for token, prior in prior_by_token.items():
                if token in orphan_tokens:
                    continue  # recreated below as a fresh synthetic row
                if taint_all or token in rejected_tokens:
                    carried.append(prior)
            for prior in prior_by_market.values():
                market = str(prior.get("market") or "")
                if not market:
                    continue
                fresh = fresh_by_market.get(market)
                if fresh is None:
                    # Case (a). Restricted to markets that actually lost rows
                    # (Codex P2 wave-27): a market with no recent fills ages out
                    # of the rolling ledger legitimately, and carrying every
                    # absent market forward whenever ANY row was rejected pinned
                    # unrelated departed markets indefinitely -- contradicting
                    # this rule's own gate rationale. Unattributable rejections
                    # still taint everything, because then any absence could be
                    # the corruption.
                    if _lost_rows(market):
                        # A CARRIED CLEAN ROW MUST NOT CARRY ITS CLEARANCE
                        # (Codex P1 wave-33). This branch fires only when the
                        # market lost rows, so its CURRENT sample is entirely
                        # unmeasurable -- and rule 13f already says unverified
                        # is not clean. Carrying a previously clean row verbatim
                        # let requote_alerts.py:502-535 and
                        # stage_ticket_eligibility.py:195-218 read an
                        # unmeasurable market as measured and safe, which is the
                        # rule's own text contradicted by the one path that
                        # bypassed it. The measurement columns and the original
                        # generated_at_utc are kept, so the stale numbers stay
                        # visible as stale; only the verdict changes.
                        prior_blocked_row = (
                            str(prior.get("toxic_blocked") or "").strip().lower() == "true"
                        )
                        if not prior_blocked_row:
                            prior = dict(prior)
                            prior["toxic_blocked"] = True
                            reasons = [
                                r for r in str(prior.get("toxicity_block_reasons") or "").split(";") if r
                            ]
                            reasons.append("unmeasurable_sample")
                            prior["toxicity_block_reasons"] = ";".join(reasons)
                            carried_clean_blocked.append(market)
                        carried.append(prior)
                    continue
                prior_blocked = str(prior.get("toxic_blocked") or "").strip().lower() == "true"
                if not prior_blocked or fresh["toxic_blocked"]:
                    continue
                if not _lost_rows(market):
                    # KEYED ON THE PERCENTILE BLOCK, not the raw one (Codex P1
                    # wave-41). The wave-31 form skipped retention whenever the
                    # prior row carried a raw block -- but a row can carry BOTH,
                    # and then the raw half being re-measurable says nothing
                    # about the percentile half, which is universe-relative and
                    # still unverifiable. So a market with both blocks was
                    # treated exactly like a market with only a raw block, and
                    # its fresh clean row cleared a live veto during a malformed
                    # refresh. The question was never "was raw set" but "is any
                    # component of this veto unverifiable", and only the
                    # percentile component can be.
                    prior_percentile_block = (
                        str(prior.get("percentile_block") or "").strip().lower() == "true"
                    )
                    if not prior_percentile_block:
                        # Purely market-local veto, and this market lost
                        # nothing: the fresh measurement is trustworthy and wins.
                        continue
                # Case (b): keep the prior BLOCKING row verbatim, original
                # generated_at_utc included, so the retained veto is visibly
                # stale rather than passing as a fresh clean measurement.
                fresh_by_market[market] = prior
                retained_blocks += 1
            # RETAINING A VETO IS NOT ENOUGH -- an incomplete sample cannot
            # certify CLEAN either (Codex P1 wave-31). The rules above protect a
            # veto that already existed; a market that lost rows but was clean
            # before, or that has no prior row at all, still published
            # toxic_blocked=false computed from the surviving subset. A balanced
            # remainder beside a rejected one-sided burst reads clean, and both
            # requote_alerts.py:502-535 and stage_ticket_eligibility.py:195-218
            # then treat that market as MEASURED and safe to quote. Malformed
            # coverage could therefore manufacture clearance outright, not just
            # fail to preserve it.
            #
            # A market whose sample is incomplete is UNVERIFIED, and unverified
            # is not clean. It blocks until a complete sample is observed, with
            # the reason named so the row is not mistaken for a measured
            # toxicity finding. A freshly BLOCKING row is untouched: it already
            # says the right thing, and its own reason stays.
            unverified_blocks = 0
            for market, row in fresh_by_market.items():
                if not _lost_rows(market) or row["toxic_blocked"]:
                    continue
                if row is not fresh_rows_by_market.get(market):
                    continue  # a retained prior row already carries the veto
                row["toxic_blocked"] = True
                reasons = [r for r in str(row["toxicity_block_reasons"] or "").split(";") if r]
                reasons.append("incomplete_sample")
                row["toxicity_block_reasons"] = ";".join(reasons)
                unverified_blocks += 1
            # THE PERCENTILE IS UNVERIFIABLE IN BOTH DIRECTIONS (Codex P1
            # wave-43). Rules 13c and 13m protect a market whose PRIOR row
            # already carried a percentile veto. The reordering cuts the other
            # way too: a market that lost nothing and was clean before can be
            # pushed ABOVE 0.90 by the corrected sample, and its fresh
            # `toxic_blocked=false` row is then accepted as a measured
            # clearance by requote_alerts.py:502-535 and
            # stage_ticket_eligibility.py:195-218. Protecting only markets that
            # already held a veto covers the half where a veto is LOST and is
            # silent on the half where one is never GAINED.
            #
            # NOT "block every fresh row during a partial corpus", which is what
            # closing this the obvious way would mean: a single malformed trade
            # print would then veto the entire universe, and an over-block that
            # large is the kind that gets quietly fixed OPEN later. The
            # reordering is BOUNDED, so the block is bounded to match. Adding
            # back the rows of one market can move that market past any other,
            # changing an untouched market's rank by at most one position each
            # -- so with L markets holding rejections, a market at rank r can
            # reach at worst rank r+L, i.e. percentile (r+L)/(N-1). Only a
            # market whose WORST CASE clears 0.90 is unverifiable; everything
            # further down is provably still clean whatever the missing rows
            # say.
            #
            # The bound is deliberately loose in two directions, both
            # fail-closed: L counts every market with a rejection rather than
            # only those currently sorting above the market under test, and a
            # wholly rejected market absent from the fresh table still counts
            # toward L even though restoring it would also raise N.
            unverifiable_percentile_blocks = 0
            lost_market_count = (
                len(fresh_rows_by_market) if taint_all else len(rejected_markets)
            )
            if lost_market_count:
                ordered_fresh = sorted(
                    fresh_rows_by_market.items(),
                    key=lambda item: float(item[1].get("vpin_raw") or 0.0),
                )
                denominator = max(1, len(ordered_fresh) - 1)
                for rank, (market, fresh_row) in enumerate(ordered_fresh):
                    row = fresh_by_market.get(market)
                    if row is not fresh_row:
                        continue  # a retained prior row already carries the veto
                    if row["toxic_blocked"]:
                        continue
                    worst_case_percentile = min(1.0, (rank + lost_market_count) / denominator)
                    if worst_case_percentile <= REGISTERED_PERCENTILE_BLOCK:
                        continue
                    row["toxic_blocked"] = True
                    reasons = [
                        r for r in str(row["toxicity_block_reasons"] or "").split(";") if r
                    ]
                    reasons.append("percentile_unverifiable")
                    row["toxicity_block_reasons"] = ";".join(reasons)
                    unverifiable_percentile_blocks += 1
            # A WHOLLY REJECTED NEW MARKET HAS NO ROW TO CORRECT (Codex P1
            # wave-32). Both loops above iterate over rows that already exist --
            # the prior CSV, or the fresh table. A market seen for the FIRST
            # time whose every row is rejected is in neither, so it simply never
            # reaches flow_toxicity.csv, and an absent tox_record is NO BLOCK at
            # requote_alerts.py:502-535. That is the same fail-open the whole
            # carry-forward arc exists to close, reached by the one path where
            # there is nothing to carry and nothing to correct.
            #
            # A synthetic blocking row is emitted instead. Its measurement
            # columns are zero because nothing was measurable -- the point is
            # `toxic_blocked` and the named reason, which is what the consumers
            # read; a zero score cannot itself trip the score or imbalance
            # thresholds, so the composite block is doing the work and says so.
            unmeasurable = [
                (market, "")
                for market in sorted(rejected_markets)
                if market not in fresh_by_market and market not in prior_by_market
            ]
            # Token-addressable rows for rejections that named no market and
            # whose token the table cannot pair with one (Codex P2 wave-33).
            # requote_alerts keys on asset_id as well, so a blank market still
            # reaches the quote that needs blocking.
            unmeasurable.extend(("", token) for token in sorted(orphan_tokens))
            for market, asset_id in unmeasurable:
                rows_out.append(
                    {
                        "generated_at_utc": generated_at,
                        "market": market,
                        "asset_id": asset_id,
                        "toxicity_score": 0.0,
                        "vpin_raw": 0.0,
                        "volume_buckets": 0,
                        "trades_seen": 0,
                        "smart_fill_count": 0,
                        "crowd_fill_count": 0,
                        "smart_fill_markout": None,
                        "crowd_fill_markout": None,
                        "missing_price_points": 0,
                        "raw_imbalance_block": False,
                        "percentile_block": False,
                        "markout_coverage_ratio": 0.0,
                        "toxic_blocked": True,
                        "toxicity_block_reasons": "wholly_rejected_sample",
                    }
                )
            if unmeasurable:
                summary["market_blocks_on_unmeasurable_sample"] = len(unmeasurable)
            if carried_clean_blocked:
                summary["market_blocks_on_carried_clean_rows"] = len(carried_clean_blocked)
            if unverified_blocks:
                summary["market_blocks_on_unverified_sample"] = unverified_blocks
            if unverifiable_percentile_blocks:
                summary["market_blocks_on_unverifiable_percentile"] = unverifiable_percentile_blocks
            if retained_blocks or unverified_blocks or unverifiable_percentile_blocks:
                rows_out = [
                    fresh_by_market.get(str(row["market"]), row) for row in rows_out
                ]
            if retained_blocks:
                summary["market_blocks_retained_on_partial_sample"] = retained_blocks
            if carried:
                rows_out = [*rows_out, *carried]
                summary["market_rows_carried_forward"] = len(carried)
        write_csv(path, rows_out, fieldnames=TOXICITY_FIELDS)
    if invalid_horizon:
        summary["status"] = "invalid_markout_horizon"
        summary["markets_scored"] = markets_freshly_scored
        write_json(summary_path, summary)
        error = ValueError(
            "flow_toxicity.markout_horizon_minutes must be a finite positive number; got "
            f"{settings.get('markout_horizon_minutes')!r}. The wallet artifact has been "
            "invalidated so no stale ranking is readable; the market-axis veto fields WERE "
            "rebuilt, with the markout columns left empty, so the toxicity veto stays current."
        )
        error._flow_toxicity_handled = True  # type: ignore[attr-defined]
        raise error
    if invalid_split_state:
        # The market axis is now current on disk, so the toxicity veto is not
        # frozen by a wallet-side fault. Only now does the run fail loudly.
        summary["status"] = "invalid_frozen_split_state"
        summary["markets_scored"] = markets_freshly_scored
        write_json(summary_path, summary)
        # The recovery instruction used to read "or delete it deliberately to
        # re-freeze" (Codex P1 wave-27). Following it causes the exact leakage
        # the error exists to refuse: with the file gone the next run is a FIRST
        # run, and it computes a new median from a corpus whose evaluation
        # markouts have already been published and inspected -- permanently
        # recycling observed evaluation markets into the ranking population.
        # Deleting the cutoff ALONE is never a valid recovery, so it is no
        # longer offered.
        error = ValueError(
            f"frozen wallet split state at {split_path} is present but invalid; refusing to "
            "manufacture a replacement cutoff from already-observed evaluation data. "
            "RECOVERY: restore the original file. Do NOT delete it to re-freeze -- the next "
            "run would then compute a fresh median from a corpus whose evaluation markouts "
            "are already published, recycling observed evaluation markets into the ranking "
            "population. If the original is unrecoverable, the experiment must be RESET as a "
            "whole: discard the published wallet evidence with the cutoff, and record that the "
            "prior read is void. "
            "The wallet artifact has been invalidated so no stale ranking is readable; "
            "the market-axis table WAS rebuilt so the toxicity veto stays current."
        )
        error._flow_toxicity_handled = True  # type: ignore[attr-defined]
        raise error

    def _mean(total: float, count: int) -> float | None:
        return round(total / count, 6) if count else None

    wallet_rows = [
        {
            "generated_at_utc": generated_at,
            "artifact_status": "ok",
            "wallet": wallet,
            # "unknown" when the leaderboard itself was unavailable (Codex
            # P2 wave-14). Rendering it as False made "confirmed absent from the
            # current leaderboard" indistinguishable from "membership could not
            # be determined", so a potentially ranked wallet was reported as
            # definitively unranked. The summary's missing_wallet_data flag does
            # not reach a reader of this CSV.
            "on_current_leaderboard": "unknown" if missing_wallet_data else (wallet in current_wallets),
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
    # A wallet retained purely for coverage -- every forward price missing or
    # stale -- is NOT a scored wallet (Codex P2 wave-21). Counting it made
    # wallets_scored claim a healthy artifact in which no markout exists at all.
    # The coverage row stays; only the count and the status change.
    scored_wallet_rows = [row for row in wallet_rows if row["fills_total"]]
    # ONE pass with an explicit precedence ladder, rather than four blanket
    # overwrites (Codex P2 wave-28). The sequential form could only express
    # statuses that apply to EVERY row, which is why `no_usable_labels` fired
    # only when NO wallet was scored: in a healthy mixed corpus a coverage row
    # -- a wallet whose every forward price was missing or stale -- kept
    # artifact_status="ok" while representing an unmeasured wallet. The row that
    # exists precisely to disclose "not measurable" was claiming healthy
    # evidence. It is a PER-ROW fact and is now decided per row.
    #
    # The registered precedence is unchanged, only made explicit, weakest first:
    #   no_usable_labels  <  leaderboard_unavailable  <  partial_malformed  <  upstream_*
    # A broken dependency outranks an incomplete sample, which outranks one
    # unresolved column, which outranks having no markout. The all-unscored case
    # still stamps every row, because under a per-row rule every row qualifies.
    for row in wallet_rows:
        status = "ok"
        if not row["fills_total"]:
            status = "no_usable_labels"
        if missing_wallet_data:
            # Still measured -- markouts do not depend on the leaderboard -- but
            # the artifact must say its membership column is unresolved rather
            # than presenting it as an ordinary result.
            status = "leaderboard_unavailable"
        if malformed_trade_rows:
            # An incomplete sample is not a healthy one (Codex P2 wave-25):
            # trade rows rejected at ingestion leave no per-wallet trace, so a
            # consumer could otherwise rank on a sample missing another wallet's
            # every fill with the exclusion visible only in a summary counter.
            status = "partial_malformed_trade_corpus"
        if trade_corpus_status != "ok":
            # The dependency is stale, partial, or unreported. Rows are STAMPED
            # rather than the artifact being blanked (Codex P1 wave-5): ranking
            # on an undisclosed stale sample is the harm, and stamping lets a
            # reader reject the artifact without a missing producer summary
            # silently destroying a working one.
            status = f"upstream_{trade_corpus_status}"
        row["artifact_status"] = status
    if not wallet_rows:
        # An enabled run with no trades, or with trades carrying no wallet
        # attribution, must still state its evidence class rather than emit a
        # bare header (Codex P1 wave-5 on #451).
        if not trade_ledger_present:
            # A ledger that does not EXIST is not a quiet day (Codex P2
            # wave-39). Reported distinctly so deletion or a failure to publish
            # cannot read as the supported header-only corpus.
            sentinel_status = "missing_trade_ledger"
        elif trade_source_rows and not trades:
            sentinel_status = "malformed_trade_corpus"
        elif trade_corpus_status == "ok":
            sentinel_status = "no_wallets_scored"
        else:
            sentinel_status = f"upstream_{trade_corpus_status}"
        wallet_rows = [_wallet_sentinel_row(generated_at, sentinel_status)]
    write_csv(wallet_path, wallet_rows, fieldnames=WALLET_MARKOUT_FIELDS)
    summary.update(
        {
            # An all-rejected corpus is NOT an empty ledger (Codex P2
            # wave-20): source rows existed and none survived parsing, which is
            # a malformed producer output, not a quiet day. Reported distinctly
            # so a reader cannot mistake one for the other.
            "status": (
                "missing_trade_ledger"
                if not trade_ledger_present
                else "malformed_trade_corpus"
                if trade_source_rows and not trades
                else "ok"
                if rows_out or not trades
                else "no_trades"
            ),
            "trade_source_rows": trade_source_rows,
            "markets_scored": markets_freshly_scored,
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
            # Feature-side rejections get their own counter (Codex P1 wave-28).
            # They are NOT folded into malformed_trade_rows_excluded and do NOT
            # set partial_malformed_trade_corpus, because the two losses are
            # disclosed differently: a rejected TRADE row leaves no per-wallet
            # trace at all, while a rejected FEATURE row shows up per wallet as
            # fills_missing_price or fills_stale_price_excluded. The residual
            # the coverage columns do NOT catch is a fill that silently falls
            # through to a DIFFERENT valid feature, so the count is published
            # here rather than left to `scanned - indexed`, which conflates
            # invalid rows with rows legitimately outside a markout interval.
            "malformed_feature_rows_excluded": malformed_feature_rows,
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
