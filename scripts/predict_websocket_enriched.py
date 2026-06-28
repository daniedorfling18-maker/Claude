#!/usr/bin/env python3
"""Build websocket predictions after enriching CLOB-only rows with live scanner metadata.

This is a read-only/paper-safe bridge for the pivot workflow. It does not open
positions, generate orders, run the broker, or loosen strategy gates. It writes
standard predictions.csv so the existing alpha/signals diagnostics can run.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from polymarket_predictive_engine.config import EngineConfig, load_config  # noqa: E402
from polymarket_predictive_engine.features_v2 import _text_features, build_features_v2  # noqa: E402
from polymarket_predictive_engine.models.calibrated import load_prediction_models, write_predictions  # noqa: E402
from polymarket_predictive_engine.utils import now_utc, parse_timestamp, read_csv_rows, write_csv, write_json  # noqa: E402

_HEX_SLUG_RE = re.compile(r"^0x[0-9a-fA-F]{24,}$")


def _metadata_paths(cfg: EngineConfig) -> list[Path]:
    return [
        cfg.output_root / "polymarket_liquidity_discovery" / "liquidity_watchlist.csv",
        cfg.output_root / "polymarket" / "market_snapshot.csv",
        cfg.output_root / "polymarket_fast_updown" / "fast_updown_market_snapshot.csv",
    ]


def _metadata_index(cfg: EngineConfig) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    for path in _metadata_paths(cfg):
        if not path.exists():
            continue
        for row in read_csv_rows(path):
            meta = {
                "market_slug": str(row.get("market_slug") or row.get("slug") or "").strip(),
                "market_id": str(row.get("market_id") or row.get("condition_id") or row.get("market") or "").strip(),
                "question": str(row.get("question") or row.get("title") or row.get("market_question") or "").strip(),
                "category": str(row.get("family") or row.get("category") or "").strip(),
                "selection_name": str(row.get("outcome") or row.get("selection") or row.get("runner") or "").strip(),
                "close_time": str(row.get("close_time") or row.get("end_time") or row.get("end_date") or "").strip(),
            }
            token_id = str(row.get("token_id") or row.get("asset_id") or row.get("outcome_token_id") or "").strip()
            for key in (token_id, meta["market_id"], meta["market_slug"]):
                if key and key not in index:
                    index[key] = meta
    return index


def _needs_metadata(row: dict[str, Any]) -> bool:
    category = str(row.get("category") or "").strip().lower()
    question = str(row.get("question") or "").strip()
    slug = str(row.get("market_slug") or "").strip()
    return category in {"", "unknown"} or not question or bool(_HEX_SLUG_RE.match(slug))


def _lookup(index: dict[str, dict[str, str]], row: dict[str, Any]) -> dict[str, str]:
    for key in (str(row.get("token_id") or "").strip(), str(row.get("market_id") or "").strip(), str(row.get("market_slug") or "").strip()):
        if key and key in index:
            return index[key]
    return {}


def _refresh_close_features(row: dict[str, Any]) -> None:
    prediction_ts = parse_timestamp(row.get("prediction_timestamp"))
    close_ts = parse_timestamp(row.get("close_time"))
    if prediction_ts is None or close_ts is None:
        return
    hours_to_close = (close_ts.astimezone(timezone.utc) - prediction_ts.astimezone(timezone.utc)).total_seconds() / 3600.0
    row["hours_to_close"] = hours_to_close
    row["days_to_close"] = hours_to_close / 24.0
    row["is_last_24h"] = int(0 <= hours_to_close <= 24)
    row["is_last_6h"] = int(0 <= hours_to_close <= 6)
    row["is_last_1h"] = int(0 <= hours_to_close <= 1)


def _apply_metadata(row: dict[str, Any], meta: dict[str, str]) -> bool:
    if not meta:
        return False
    changed = False
    for field in ("market_id", "market_slug", "question", "category", "selection_name", "close_time"):
        value = meta.get(field, "")
        if value and (not str(row.get(field) or "").strip() or field in {"market_slug", "category"} and _needs_metadata(row)):
            row[field] = value
            changed = True
    if changed:
        row.update(_text_features(str(row.get("question") or ""), str(row.get("market_slug") or "")))
        _refresh_close_features(row)
    return changed


def build_enriched_predictions(config_path: str = "polymarket_predictive_config.example.yaml") -> dict[str, Any]:
    cfg = load_config(config_path)
    features = build_features_v2(cfg, source="websocket", allow_unlabelled_research=True, require_clean_labels=False)
    index = _metadata_index(cfg)
    enriched = 0
    still_unknown = 0
    family_counts: Counter[str] = Counter()
    for row in features:
        if _needs_metadata(row):
            enriched += int(_apply_metadata(row, _lookup(index, row)))
        if _needs_metadata(row):
            still_unknown += 1
        family_counts[str(row.get("category") or "unknown")] += 1

    out_root = cfg.output_root / "polymarket_training"
    enriched_features_path = out_root / "features_v2_websocket_enriched.csv"
    write_csv(enriched_features_path, features)

    global_model, category_models = load_prediction_models(cfg)
    predictions_root = cfg.output_root / "polymarket_predictions"
    predictions_root.mkdir(parents=True, exist_ok=True)
    predictions = write_predictions(
        features,
        str(predictions_root / "predictions.csv"),
        model=global_model,
        category_models=category_models,
        training_cutoff=str(global_model.get("trained_at", "")),
    )
    prediction_family_counts = Counter(str(row.get("category") or "unknown") for row in predictions)
    payload = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "feature_rows": len(features),
        "metadata_index_keys": len(index),
        "features_enriched": enriched,
        "features_still_unknown_or_unresolved": still_unknown,
        "feature_family_counts": dict(family_counts.most_common(20)),
        "predictions": len(predictions),
        "prediction_family_counts": dict(prediction_family_counts.most_common(20)),
        "enriched_features_file": str(enriched_features_path),
        "predictions_file": str(predictions_root / "predictions.csv"),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    write_json(cfg.governance_root / "websocket_enriched_prediction_summary.json", payload)
    return payload


def main() -> int:
    config_path = sys.argv[1] if len(sys.argv) > 1 else "polymarket_predictive_config.example.yaml"
    print(json.dumps(build_enriched_predictions(config_path), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
