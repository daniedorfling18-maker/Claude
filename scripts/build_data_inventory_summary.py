from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a combined Oddspedia and Superbru data coverage summary.")
    parser.add_argument("--inventory-dir", default="outputs/data_inventory")
    parser.add_argument("--out-json", default="outputs/data_inventory/data_coverage_summary.json")
    return parser


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"missing": True, "path": str(path)}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"error": str(exc), "path": str(path)}


def csv_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return len(pd.read_csv(path))
    except Exception:
        return 0


def csv_nunique(path: Path, column: str) -> int:
    if not path.exists():
        return 0
    try:
        frame = pd.read_csv(path).fillna("")
        if column not in frame.columns:
            return 0
        return int(frame[column].nunique())
    except Exception:
        return 0


def coverage_metrics(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"row_count": 0, "fixture_visible_count": 0, "likely_pick_data_visible_count": 0}
    try:
        frame = pd.read_csv(path).fillna("")
    except Exception as exc:
        return {"error": str(exc), "row_count": 0}
    row_count = int(len(frame))
    fixture_visible = 0
    likely_pick = 0
    if "fixture_visible" in frame.columns:
        fixture_visible = int(frame["fixture_visible"].astype(str).str.lower().isin(["true", "1", "yes"]).sum())
    if "likely_pick_data_visible" in frame.columns:
        likely_pick = int(frame["likely_pick_data_visible"].astype(str).str.lower().isin(["true", "1", "yes"]).sum())
    return {
        "row_count": row_count,
        "fixture_visible_count": fixture_visible,
        "likely_pick_data_visible_count": likely_pick,
        "fixture_visible_rate": fixture_visible / row_count if row_count else 0,
        "likely_pick_data_visible_rate": likely_pick / row_count if row_count else 0,
    }


def main() -> int:
    args = build_parser().parse_args()
    inv = Path(args.inventory_dir)
    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)

    odd_fields = load_json(inv / "oddspedia_available_fields.json")
    superbru_fields = load_json(inv / "superbru_available_fields.json")
    odd_markets = inv / "oddspedia_available_markets.csv"
    odd_network = inv / "oddspedia_network_inventory.csv"
    superbru_network = inv / "superbru_network_inventory.csv"
    superbru_tables = inv / "superbru_table_inventory.csv"
    superbru_coverage = inv / "superbru_visible_pick_coverage.csv"

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "inventory_dir": str(inv),
        "oddspedia": {
            "input_match_count": odd_fields.get("input_match_count", 0),
            "field_row_count": odd_fields.get("field_row_count", 0),
            "market_like_row_count": odd_fields.get("market_like_row_count", csv_count(odd_markets)),
            "unique_market_path_count": csv_nunique(odd_markets, "market_path"),
            "network_row_count": odd_fields.get("network_row_count", csv_count(odd_network)),
            "outputs": odd_fields.get("outputs", {}),
        },
        "superbru": {
            "table_count": superbru_fields.get("table_count", 0),
            "control_count": superbru_fields.get("control_count", 0),
            "form_count": superbru_fields.get("form_count", 0),
            "fixture_input_count": superbru_fields.get("fixture_input_count", 0),
            "fixture_coverage": coverage_metrics(superbru_coverage),
            "network_row_count": superbru_fields.get("network_row_count", csv_count(superbru_network)),
            "table_inventory_row_count": csv_count(superbru_tables),
            "outputs": superbru_fields.get("outputs", {}),
        },
        "interpretation": {
            "status": "inventory_only",
            "note": "This summary reports what the browser exposes. It does not yet promote newly discovered fields into the prediction model.",
            "next_step": "Review high-value Oddspedia markets and Superbru coverage, then map stable fields into EV and pool-intelligence features.",
        },
    }
    out_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
