"""Per-family model calibration versus the Polymarket baseline.

This scorecard answers a narrow quant question:

```
In which market families does the model beat the market probability on clean
settled rows?
```

It deliberately reuses the market-relative validation join and metrics so the
answer is point-in-time, leakage-safe, and directly comparable with the existing
promotion gate. The output is diagnostic only: a family that beats the market
here still needs forward shadow / paper evidence before any sizing or promotion
decision.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Mapping, Sequence

from .config import EngineConfig, load_config
from .market_relative_validation import (
    _bootstrap_brier_gain_ci,
    brier_decomposition,
    brier_score,
    join_clean_settled_predictions,
    log_loss,
)
from .utils import git_commit_hash, now_utc, read_csv_rows, write_csv, write_json
from .worldcup_validation import classify_market_family

CLASS_MODEL_BEATS = "model_beats_market"
CLASS_MARKET_BEATS = "market_beats_model"
CLASS_INSUFFICIENT = "insufficient_calibration_evidence"

ROW_FIELDS = [
    "family",
    "evidence_class",
    "rows",
    "markets",
    "minimum_rows",
    "model_brier_score",
    "market_brier_score",
    "brier_gain_vs_market",
    "brier_gain_ci_low",
    "brier_gain_ci_high",
    "model_log_loss",
    "market_log_loss",
    "log_loss_gain_vs_market",
    "model_brier_skill_vs_market",
    "model_reliability",
    "market_reliability",
    "model_resolution",
    "market_resolution",
    "base_rate",
    "sample_status",
]


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    return cfg.raw.get("family_calibration", {}) or {}


def _clean_number(value: Any, digits: int = 8) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)


def _validation_units(rows: Sequence[Mapping[str, Any]]) -> set[str]:
    return {
        str(row.get("validation_unit_id") or row.get("market_id") or row.get("condition_id") or "")
        for row in rows
        if str(row.get("validation_unit_id") or row.get("market_id") or row.get("condition_id") or "").strip()
    }


def _family_name(row: Mapping[str, Any]) -> str:
    family = classify_market_family(dict(row))
    return family or "unknown"


def _evidence_class(rows: Sequence[Mapping[str, Any]], minimum_rows: int, ci_low: float | None, ci_high: float | None) -> str:
    if len(rows) < minimum_rows:
        return CLASS_INSUFFICIENT
    if ci_low is not None and ci_low > 0:
        return CLASS_MODEL_BEATS
    if ci_high is not None and ci_high < 0:
        return CLASS_MARKET_BEATS
    return CLASS_INSUFFICIENT


def _score_family(
    family: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    minimum_rows: int,
    bucket_count: int,
    bootstrap_iterations: int,
    bootstrap_seed: int,
) -> dict[str, Any]:
    model_brier = brier_score(rows, "model_probability")
    market_brier = brier_score(rows, "market_probability")
    model_log = log_loss(rows, "model_probability")
    market_log = log_loss(rows, "market_probability")
    point_gain, ci_low, ci_high = _bootstrap_brier_gain_ci(rows, n_boot=bootstrap_iterations, seed=bootstrap_seed)
    model_decomp, _ = brier_decomposition(rows, "model_probability", "model", bucket_count=bucket_count)
    market_decomp, _ = brier_decomposition(rows, "market_probability", "market", bucket_count=bucket_count)
    evidence_class = _evidence_class(rows, minimum_rows, _clean_number(ci_low), _clean_number(ci_high))
    brier_skill = 1 - model_brier / market_brier if market_brier and math.isfinite(market_brier) else float("nan")

    return {
        "family": family,
        "evidence_class": evidence_class,
        "rows": len(rows),
        "markets": len(_validation_units(rows)),
        "minimum_rows": minimum_rows,
        "model_brier_score": _clean_number(model_brier),
        "market_brier_score": _clean_number(market_brier),
        "brier_gain_vs_market": _clean_number(point_gain),
        "brier_gain_ci_low": _clean_number(ci_low),
        "brier_gain_ci_high": _clean_number(ci_high),
        "model_log_loss": _clean_number(model_log),
        "market_log_loss": _clean_number(market_log),
        "log_loss_gain_vs_market": _clean_number(market_log - model_log),
        "model_brier_skill_vs_market": _clean_number(brier_skill),
        "model_reliability": _clean_number(model_decomp.get("reliability")),
        "market_reliability": _clean_number(market_decomp.get("reliability")),
        "model_resolution": _clean_number(model_decomp.get("resolution")),
        "market_resolution": _clean_number(market_decomp.get("resolution")),
        "base_rate": _clean_number(model_decomp.get("base_rate")),
        "sample_status": "minimum_met" if len(rows) >= minimum_rows else "below_minimum",
    }


def build_family_calibration_scorecard(cfg: EngineConfig) -> dict[str, Any]:
    settings = _settings(cfg)
    minimum_rows = int(settings.get("minimum_rows", 25))
    bucket_count = int(settings.get("bucket_count", cfg.raw.get("calibration", {}).get("buckets", 10)))
    bootstrap_iterations = int(settings.get("bootstrap_iterations", cfg.raw.get("market_relative_validation", {}).get("bootstrap_iterations", 1000)))
    bootstrap_seed = int(settings.get("bootstrap_seed", 20260703))

    prediction_rows = read_csv_rows(cfg.output_root / "polymarket_predictions" / "predictions.csv")
    label_rows = read_csv_rows(cfg.output_root / "polymarket_training" / "labels.csv")
    joined, rejected = join_clean_settled_predictions(prediction_rows, label_rows)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in joined:
        grouped[_family_name(row)].append(row)

    families = [
        _score_family(
            family,
            rows,
            minimum_rows=minimum_rows,
            bucket_count=bucket_count,
            bootstrap_iterations=bootstrap_iterations,
            bootstrap_seed=bootstrap_seed,
        )
        for family, rows in sorted(grouped.items())
    ]
    families.sort(
        key=lambda row: (
            row["evidence_class"] != CLASS_MODEL_BEATS,
            -(row.get("brier_gain_vs_market") or -999.0),
            str(row.get("family") or ""),
        )
    )
    counts = Counter(str(row["evidence_class"]) for row in families)
    usable = [row for row in families if row["sample_status"] == "minimum_met"]
    positive = [row for row in families if row["evidence_class"] == CLASS_MODEL_BEATS]
    negative = [row for row in families if row["evidence_class"] == CLASS_MARKET_BEATS]

    payload = {
        "status": "ok" if joined else "no_clean_settled_rows",
        "generated_at_utc": now_utc(),
        "git_commit": git_commit_hash(),
        "minimum_rows": minimum_rows,
        "bucket_count": bucket_count,
        "bootstrap_iterations": bootstrap_iterations,
        "bootstrap_seed": bootstrap_seed,
        "clean_settled_joined_rows": len(joined),
        "rejected_join_rows": len(rejected),
        # join_clean_settled_predictions already computes a reason for every
        # rejection; publishing only the total made a 100% rejection rate
        # undiagnosable. Measured 2026-08-15: 16,910 rejected, 0 joined, with no
        # way to tell "predictions and labels cover disjoint markets" apart from
        # "the model probability IS the market midpoint" or an exact-timestamp
        # key that never matches. Diagnostic only - no gate reads this.
        "rejected_join_reasons": dict(Counter(str(row.get("reason") or "unknown") for row in rejected)),
        "rejected_join_examples": [
            {key: row.get(key, "") for key in ("market_id", "token_id", "prediction_timestamp", "reason")}
            for row in rejected[:5]
        ],
        "families_scored": len(families),
        "families_with_minimum_rows": len(usable),
        "evidence_counts": dict(counts),
        "model_beats_market_families": [row["family"] for row in positive],
        "market_beats_model_families": [row["family"] for row in negative],
        "families": families,
        "governance_note": "Family calibration is diagnostic; promotion still requires forward shadow evidence, paper fills, and unchanged governance gates.",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    write_json(cfg.governance_root / "family_calibration_scorecard.json", payload)
    write_csv(cfg.governance_root / "family_calibration_scorecard.csv", families, fieldnames=ROW_FIELDS)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return build_family_calibration_scorecard(load_config(config_path))
