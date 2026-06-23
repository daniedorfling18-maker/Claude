from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import EngineConfig, load_config
from .utils import parse_timestamp, read_csv_rows, safe_float, write_csv

REQUIRED = ["timestamp", "market_slug", "outcome", "fair_probability", "confidence", "source", "notes"]


def normalize_external_signals(cfg: EngineConfig) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    quality: list[dict[str, Any]] = []
    paths = cfg.raw.get("external_signals", {}).get("manual_csv_paths", [])
    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists():
            quality.append({"file_path": str(path), "status": "missing", "message": "manual signal file not found"})
            continue
        for idx, row in enumerate(read_csv_rows(path)):
            missing = [c for c in REQUIRED if c not in row]
            if missing:
                quality.append({"file_path": str(path), "row": idx + 2, "status": "rejected", "message": "missing columns: " + ", ".join(missing)})
                continue
            ts = parse_timestamp(row.get("timestamp"))
            fair = safe_float(row.get("fair_probability"))
            conf = safe_float(row.get("confidence"))
            if not ts or fair is None or not 0 <= fair <= 1 or conf is None or not 0 <= conf <= 1:
                quality.append({"file_path": str(path), "row": idx + 2, "status": "rejected", "message": "invalid timestamp, fair_probability, or confidence"})
                continue
            rows.append({"timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "market_slug": row["market_slug"], "outcome": row["outcome"], "fair_probability": fair, "confidence": conf, "source": row["source"], "notes": row.get("notes", "")})
            quality.append({"file_path": str(path), "row": idx + 2, "status": "accepted", "message": "manual signal accepted"})
    out = cfg.output_root / "polymarket_training"
    write_csv(out / "external_signals_normalized.csv", rows)
    write_csv(cfg.governance_root / "external_signal_quality.csv", quality)
    return rows, quality


def main(config_path: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return normalize_external_signals(load_config(config_path))
