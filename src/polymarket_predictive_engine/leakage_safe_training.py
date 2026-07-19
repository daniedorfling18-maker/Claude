"""WO-101 leakage-safe resolved-corpus training assembly.

This is diagnostic substrate for H3 structural-bias research, not a fourth
hypothesis and not the registered H3 verdict.  It joins only exact historical
bid/ask observations to append-only terminal labels, then assigns whole markets
to a chronological validation tail.  Training labels must finish before the
validation feature window plus a fixed embargo; overlapping markets are purged.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import math
from pathlib import Path
from typing import Any, Mapping

from .config import EngineConfig, load_config
from .historical_bid_ask import QUOTE_LEDGER_RELATIVE_PATH, iter_csv_rows
from .resolution_corpus import RESOLUTION_CORPUS_RELATIVE_PATH, canonical_utc
from .utils import parse_timestamp, safe_float, write_csv, write_json


WORK_ORDER = "WO-101"
FEATURES_RELATIVE_PATH = Path("polymarket_training") / "resolved_corpus_features_v1.csv"
LABELS_RELATIVE_PATH = Path("polymarket_training") / "resolved_corpus_labels_v1.csv"
SPLIT_RELATIVE_PATH = Path("polymarket_training") / "resolved_corpus_split_v1.csv"
SUMMARY_RELATIVE_PATH = Path("polymarket_model_governance") / "leakage_safe_training_summary.json"

REGISTERED_MAX_LOOKBACK_HOURS = 7 * 24
REGISTERED_MIN_EMBARGO_HOURS = 24
REGISTERED_MIN_VALIDATION_FRACTION = 0.30
REGISTERED_MAX_VALIDATION_FRACTION = 0.50
REGISTERED_MIN_VALIDATION_MARKETS = 10
REGISTERED_MIN_THIN_BUCKET_SECONDS = 3600

FEATURE_FIELDS = [
    "training_row_id",
    "market_id",
    "market_slug",
    "token_id",
    "prediction_timestamp",
    "category",
    "hypothesis",
    "split",
    "feature_source",
    "midpoint",
    "implied_probability",
    "best_bid",
    "best_ask",
    "spread",
    "executable_buy_price",
    "executable_sell_price",
    "top_bid_size",
    "top_ask_size",
    "bid_depth_1pct",
    "ask_depth_1pct",
    "bid_depth_5pct",
    "ask_depth_5pct",
    "book_imbalance",
    "hours_to_close",
    "fees_enabled",
    "fee_schedule_rate",
    "fee_schedule_exponent",
    "fee_schedule_taker_only",
]

LABEL_FIELDS = [
    "training_row_id",
    "market_id",
    "token_id",
    "prediction_timestamp",
    "split",
    "target",
    "horizon",
    "label_available_at_utc",
    "resolution_observed_at_utc",
    "resolution_observation_id",
    "resolution_quality",
]

SPLIT_FIELDS = [
    "market_id",
    "market_slug",
    "category",
    "split",
    "reason",
    "first_quote_at_utc",
    "last_quote_at_utc",
    "label_available_at_utc",
    "validation_feature_start_utc",
    "embargo_cutoff_utc",
    "row_count",
]


def _positive(value: Any, default: float) -> float:
    parsed = safe_float(value)
    return float(parsed) if parsed is not None and parsed > 0 else float(default)


def training_settings(cfg: EngineConfig) -> dict[str, float | int]:
    """Apply tighten-only overrides to the filed split and observation window."""

    raw = cfg.raw.get("leakage_safe_training", {})
    raw = raw if isinstance(raw, dict) else {}
    return {
        "max_lookback_hours": min(
            float(REGISTERED_MAX_LOOKBACK_HOURS),
            _positive(raw.get("max_lookback_hours"), REGISTERED_MAX_LOOKBACK_HOURS),
        ),
        "embargo_hours": max(
            float(REGISTERED_MIN_EMBARGO_HOURS),
            _positive(raw.get("embargo_hours"), REGISTERED_MIN_EMBARGO_HOURS),
        ),
        "validation_fraction": min(
            float(REGISTERED_MAX_VALIDATION_FRACTION),
            max(
                float(REGISTERED_MIN_VALIDATION_FRACTION),
                _positive(raw.get("validation_fraction"), REGISTERED_MIN_VALIDATION_FRACTION),
            ),
        ),
        "minimum_validation_markets": max(
            REGISTERED_MIN_VALIDATION_MARKETS,
            int(
                math.ceil(
                    _positive(
                        raw.get("minimum_validation_markets"),
                        REGISTERED_MIN_VALIDATION_MARKETS,
                    )
                )
            ),
        ),
        "thin_bucket_seconds": max(
            float(REGISTERED_MIN_THIN_BUCKET_SECONDS),
            _positive(raw.get("thin_bucket_seconds"), REGISTERED_MIN_THIN_BUCKET_SECONDS),
        ),
    }


def _iso(value: datetime | None) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if value else ""


def _bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _binary(value: Any) -> int | None:
    text = str(value).strip().lower()
    if text in {"1", "1.0", "true", "yes"}:
        return 1
    if text in {"0", "0.0", "false", "no"}:
        return 0
    return None


def _market_key(row: Mapping[str, Any]) -> str:
    return str(
        row.get("condition_id")
        or row.get("gamma_market_id")
        or row.get("market_slug")
        or ""
    ).strip()


def _resolution_index(
    path: Path,
    *,
    as_of: datetime,
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    counts: Counter[str] = Counter()
    for row in iter_csv_rows(path):
        counts["resolution_rows_seen"] += 1
        observed = parse_timestamp(row.get("resolution_observed_at_utc"))
        if observed is None:
            counts["resolution_missing_observation_time"] += 1
            continue
        if observed.astimezone(timezone.utc) > as_of:
            counts["resolution_future_observations"] += 1
            continue
        if str(row.get("resolution_quality") or "") != "clean_settlement" or not _bool(row.get("closed")):
            counts["resolution_not_clean_closed"] += 1
            continue
        target = _binary(row.get("target"))
        token_id = str(row.get("token_id") or "").strip()
        market_id = _market_key(row)
        close_at = parse_timestamp(row.get("close_time") or row.get("end_time"))
        resolution_at = parse_timestamp(row.get("resolution_time"))
        if target is None or not token_id or not market_id:
            counts["resolution_missing_identity_or_target"] += 1
            continue
        if close_at is None or resolution_at is None:
            counts["resolution_missing_close_or_resolution_time"] += 1
            continue
        # A terminal state cannot be used merely because the venue later
        # reports an earlier close/resolution timestamp.  The label first
        # exists for this corpus when a producer actually observed it.
        label_available = max(
            close_at.astimezone(timezone.utc),
            resolution_at.astimezone(timezone.utc),
            observed.astimezone(timezone.utc),
        )
        candidates[token_id].append(
            {
                **row,
                "target_int": target,
                "market_id_canonical": market_id,
                "close_at": close_at.astimezone(timezone.utc),
                "label_available_at": label_available,
                "resolution_observed_at": observed.astimezone(timezone.utc),
            }
        )

    index: dict[str, dict[str, Any]] = {}
    for token_id, rows in candidates.items():
        targets = {int(row["target_int"]) for row in rows}
        markets = {str(row["market_id_canonical"]) for row in rows}
        if len(targets) != 1 or len(markets) != 1:
            counts["conflicting_clean_resolution_tokens"] += 1
            continue
        rows.sort(
            key=lambda row: (
                row["resolution_observed_at"],
                str(row.get("resolution_observation_id") or ""),
            )
        )
        index[token_id] = rows[0]
    counts["clean_resolution_tokens"] = len(index)
    return index, dict(counts)


def _resolution_identity_set(row: Mapping[str, Any]) -> set[str]:
    return {
        str(row.get(key) or "").strip()
        for key in ("condition_id", "gamma_market_id", "market_slug")
        if str(row.get(key) or "").strip()
    }


def _eligible_quotes(
    path: Path,
    resolution_index: Mapping[str, Mapping[str, Any]],
    *,
    as_of: datetime,
    settings: Mapping[str, float | int],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    selected: dict[tuple[str, int], dict[str, Any]] = {}
    counts: Counter[str] = Counter()
    bucket_seconds = int(settings["thin_bucket_seconds"])
    for quote in iter_csv_rows(path):
        counts["quote_rows_seen"] += 1
        token_id = str(quote.get("token_id") or "").strip()
        resolution = resolution_index.get(token_id)
        if resolution is None:
            counts["quote_without_clean_resolution"] += 1
            continue
        observed = parse_timestamp(quote.get("observed_at_utc"))
        if observed is None:
            counts["quote_missing_observation_time"] += 1
            continue
        observed = observed.astimezone(timezone.utc)
        if observed > as_of:
            counts["quote_future_observations"] += 1
            continue
        bid = safe_float(quote.get("best_bid"))
        ask = safe_float(quote.get("best_ask"))
        if bid is None or ask is None:
            counts["midpoint_or_one_sided_quotes_excluded"] += 1
            continue
        if not 0.0 < bid <= ask < 1.0:
            counts["invalid_or_crossed_quotes"] += 1
            continue
        quote_market = str(quote.get("market_id") or "").strip()
        resolution_ids = _resolution_identity_set(resolution)
        if quote_market and resolution_ids and quote_market not in resolution_ids:
            counts["market_identity_mismatch"] += 1
            continue
        close_at = resolution["close_at"]
        label_available = resolution["label_available_at"]
        if observed >= close_at or observed >= label_available:
            counts["quote_at_or_after_label_time"] += 1
            continue
        hours_to_close = (close_at - observed).total_seconds() / 3600.0
        if hours_to_close < 0 or hours_to_close > settings["max_lookback_hours"]:
            counts["quote_outside_registered_lookback"] += 1
            continue
        bucket = int(observed.timestamp()) // bucket_seconds
        row = {
            "quote": dict(quote),
            "resolution": dict(resolution),
            "observed_at": observed,
            "hours_to_close": hours_to_close,
            "best_bid": bid,
            "best_ask": ask,
        }
        key = (token_id, bucket)
        previous = selected.get(key)
        if previous is None or (
            observed,
            str(quote.get("quote_observation_id") or ""),
        ) < (
            previous["observed_at"],
            str(previous["quote"].get("quote_observation_id") or ""),
        ):
            selected[key] = row
    counts["eligible_thinned_quote_rows"] = len(selected)
    return sorted(
        selected.values(),
        key=lambda row: (
            row["observed_at"],
            str(row["resolution"]["market_id_canonical"]),
            str(row["quote"].get("token_id") or ""),
        ),
    ), dict(counts)


def _split_markets(
    rows: list[dict[str, Any]],
    *,
    settings: Mapping[str, float | int],
) -> tuple[dict[str, str], list[dict[str, Any]], dict[str, Any]]:
    by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_market[str(row["resolution"]["market_id_canonical"])].append(row)
    ordered = sorted(
        by_market,
        key=lambda market_id: (
            max(row["resolution"]["label_available_at"] for row in by_market[market_id]),
            market_id,
        ),
    )
    assignments: dict[str, str] = {}
    split_rows: list[dict[str, Any]] = []
    minimum_validation_markets = int(settings["minimum_validation_markets"])
    minimum_total_markets = minimum_validation_markets + 1
    if len(ordered) < minimum_total_markets:
        for market_id in ordered:
            assignments[market_id] = "purged"
        validation_start = None
        cutoff = None
    else:
        validation_count = min(
            len(ordered) - 1,
            max(
                minimum_validation_markets,
                int(math.ceil(len(ordered) * float(settings["validation_fraction"]))),
            ),
        )
        validation_markets = set(ordered[-validation_count:])
        validation_start = min(
            row["observed_at"]
            for market_id in validation_markets
            for row in by_market[market_id]
        )
        cutoff = validation_start - timedelta(hours=settings["embargo_hours"])
        for market_id in ordered:
            if market_id in validation_markets:
                assignments[market_id] = "validation"
                continue
            label_available = max(
                row["resolution"]["label_available_at"] for row in by_market[market_id]
            )
            assignments[market_id] = "train" if label_available < cutoff else "purged"

    for market_id in ordered:
        market_rows = by_market[market_id]
        split = assignments[market_id]
        resolution = market_rows[0]["resolution"]
        if len(ordered) < minimum_total_markets:
            reason = (
                "need_at_least_"
                f"{minimum_total_markets}_independent_markets_for_one_train_and_"
                f"{minimum_validation_markets}_validation"
            )
        elif split == "validation":
            reason = "latest_chronological_market_tail"
        elif split == "train":
            reason = "label_available_strictly_before_validation_window_and_embargo"
        else:
            reason = "purged_label_interval_overlaps_validation_window_or_embargo"
        split_rows.append(
            {
                "market_id": market_id,
                "market_slug": resolution.get("market_slug", ""),
                "category": resolution.get("category", ""),
                "split": split,
                "reason": reason,
                "first_quote_at_utc": _iso(min(row["observed_at"] for row in market_rows)),
                "last_quote_at_utc": _iso(max(row["observed_at"] for row in market_rows)),
                "label_available_at_utc": _iso(
                    max(row["resolution"]["label_available_at"] for row in market_rows)
                ),
                "validation_feature_start_utc": _iso(validation_start),
                "embargo_cutoff_utc": _iso(cutoff),
                "row_count": len(market_rows),
            }
        )
    return assignments, split_rows, {
        "validation_feature_start_utc": _iso(validation_start),
        "embargo_cutoff_utc": _iso(cutoff),
        "markets_total": len(ordered),
        "independent_market_unit": "canonical_market_id_whole_market",
        "minimum_validation_markets_required": minimum_validation_markets,
        "train_markets": sum(value == "train" for value in assignments.values()),
        "validation_markets": sum(value == "validation" for value in assignments.values()),
        "purged_markets": sum(value == "purged" for value in assignments.values()),
    }


def _file_evidence(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "present": False, "byte_length": 0, "sha256": ""}
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return {
        "path": str(path),
        "present": True,
        "byte_length": path.stat().st_size,
        "sha256": digest.hexdigest(),
    }


def build_leakage_safe_training(
    cfg: EngineConfig,
    *,
    as_of: datetime | str | None = None,
) -> dict[str, Any]:
    """Build atomic feature/label files and a registered purged split."""

    generated_at = canonical_utc(as_of)
    current = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    settings = training_settings(cfg)
    resolution_path = cfg.output_root / RESOLUTION_CORPUS_RELATIVE_PATH
    quote_path = cfg.output_root / QUOTE_LEDGER_RELATIVE_PATH
    feature_path = cfg.output_root / FEATURES_RELATIVE_PATH
    label_path = cfg.output_root / LABELS_RELATIVE_PATH
    split_path = cfg.output_root / SPLIT_RELATIVE_PATH
    summary_path = cfg.output_root / SUMMARY_RELATIVE_PATH

    resolution_index, resolution_coverage = _resolution_index(resolution_path, as_of=current)
    rows, quote_coverage = _eligible_quotes(
        quote_path,
        resolution_index,
        as_of=current,
        settings=settings,
    )
    assignments, split_rows, split_summary = _split_markets(rows, settings=settings)

    features: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []
    for item in rows:
        quote = item["quote"]
        resolution = item["resolution"]
        market_id = str(resolution["market_id_canonical"])
        split = assignments.get(market_id, "purged")
        if split not in {"train", "validation"}:
            continue
        row_id = str(quote.get("quote_observation_id") or "")
        midpoint = (item["best_bid"] + item["best_ask"]) / 2.0
        features.append(
            {
                "training_row_id": row_id,
                "market_id": market_id,
                "market_slug": resolution.get("market_slug", ""),
                "token_id": quote.get("token_id", ""),
                "prediction_timestamp": _iso(item["observed_at"]),
                "category": resolution.get("category") or quote.get("category") or "unknown",
                "hypothesis": "H3_structural_bias_diagnostic_substrate",
                "split": split,
                "feature_source": "wo101_historical_bid_ask_v1",
                "midpoint": midpoint,
                "implied_probability": midpoint,
                "best_bid": item["best_bid"],
                "best_ask": item["best_ask"],
                "spread": item["best_ask"] - item["best_bid"],
                "executable_buy_price": item["best_ask"],
                "executable_sell_price": item["best_bid"],
                "top_bid_size": quote.get("top_bid_size", ""),
                "top_ask_size": quote.get("top_ask_size", ""),
                "bid_depth_1pct": quote.get("bid_depth_1pct", ""),
                "ask_depth_1pct": quote.get("ask_depth_1pct", ""),
                "bid_depth_5pct": quote.get("bid_depth_5pct", ""),
                "ask_depth_5pct": quote.get("ask_depth_5pct", ""),
                "book_imbalance": quote.get("book_imbalance", ""),
                "hours_to_close": item["hours_to_close"],
                "fees_enabled": quote.get("fees_enabled", ""),
                "fee_schedule_rate": quote.get("fee_schedule_rate", ""),
                "fee_schedule_exponent": quote.get("fee_schedule_exponent", ""),
                "fee_schedule_taker_only": quote.get("fee_schedule_taker_only", ""),
            }
        )
        labels.append(
            {
                "training_row_id": row_id,
                "market_id": market_id,
                "token_id": quote.get("token_id", ""),
                "prediction_timestamp": _iso(item["observed_at"]),
                "split": split,
                "target": resolution["target_int"],
                "horizon": "terminal_resolution",
                "label_available_at_utc": _iso(resolution["label_available_at"]),
                "resolution_observed_at_utc": _iso(resolution["resolution_observed_at"]),
                "resolution_observation_id": resolution.get("resolution_observation_id", ""),
                "resolution_quality": "clean_settlement",
            }
        )

    write_csv(feature_path, features, fieldnames=FEATURE_FIELDS)
    write_csv(label_path, labels, fieldnames=LABEL_FIELDS)
    write_csv(split_path, split_rows, fieldnames=SPLIT_FIELDS)
    train_targets = {
        int(label["target"]) for label in labels if label["split"] == "train"
    }
    overlap = {
        feature["market_id"] for feature in features if feature["split"] == "train"
    } & {
        feature["market_id"] for feature in features if feature["split"] == "validation"
    }
    status = (
        "ready"
        if split_summary["train_markets"] > 0
        and split_summary["validation_markets"]
        >= split_summary["minimum_validation_markets_required"]
        and train_targets == {0, 1}
        and not overlap
        else "collecting"
    )
    payload = {
        "work_order": WORK_ORDER,
        "generated_at_utc": generated_at,
        "status": status,
        "hypothesis_mapping": "H3 structural-bias diagnostic substrate only",
        "registered_h3_verdict_authority": False,
        "promotion_authority": False,
        "split_policy": (
            "whole-market chronological validation tail with at least 10 distinct "
            "validation markets; purge training labels overlapping validation feature "
            "start plus embargo"
        ),
        "settings": settings,
        "input_artifacts": {
            "resolution_corpus": _file_evidence(resolution_path),
            "historical_bid_ask": _file_evidence(quote_path),
        },
        "coverage": {**resolution_coverage, **quote_coverage},
        "split": {
            **split_summary,
            "train_rows": sum(feature["split"] == "train" for feature in features),
            "validation_rows": sum(feature["split"] == "validation" for feature in features),
            "train_target_classes": sorted(train_targets),
            "market_overlap_count": len(overlap),
            "market_overlap": sorted(overlap),
        },
        "outputs": {
            "features": str(feature_path),
            "labels": str(label_path),
            "split_audit": str(split_path),
        },
        "feature_file_contains_resolution_fields": False,
        "midpoint_only_rows_accepted": 0,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        "next_unmet_condition": (
            "none; diagnostic trainer may run"
            if status == "ready"
            else (
                "collect exact bid/ask rows that later join to non-overlapping clean "
                "resolutions, at least 10 distinct validation markets, and both train "
                "target classes"
            )
        ),
    }
    write_json(summary_path, payload)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return build_leakage_safe_training(load_config(config_path))
