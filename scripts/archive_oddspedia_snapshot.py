from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Archive current Oddspedia outputs before they are overwritten.")
    parser.add_argument("--history-dir", default="outputs/oddspedia_probability_extract/history")
    parser.add_argument("--grid-csv", default="inputs/smartbet_grids/oddspedia_probability_grids_auto.csv")
    parser.add_argument("--summary-csv", default="inputs/smartbet_grids/oddspedia_probability_summary_auto.csv")
    parser.add_argument("--shape-csv", default="inputs/smartbet_grids/oddspedia_score_shape_features.csv")
    parser.add_argument("--comparison-csv", default="outputs/oddspedia_pick_validation/oddspedia_pick_comparison.csv")
    parser.add_argument("--ev-recommendations-csv", default="outputs/oddspedia_pick_validation/oddspedia_ev_recommendations.csv")
    parser.add_argument("--snapshot-id", default="")
    return parser


def copy_if_exists(src: str, dest_dir: Path) -> dict[str, Any]:
    p = Path(src)
    if not p.exists():
        return {"source": src, "archived": False, "reason": "missing"}
    if not p.is_file():
        return {"source": src, "archived": False, "reason": "not_file"}
    dest = dest_dir / p.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, dest)
    return {"source": src, "archived": True, "archive_path": str(dest)}


def main() -> int:
    args = build_parser().parse_args()
    snapshot_id = args.snapshot_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot_dir = Path(args.history_dir) / snapshot_id
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    files = [
        args.grid_csv,
        args.summary_csv,
        args.shape_csv,
        args.comparison_csv,
        args.ev_recommendations_csv,
    ]
    archived = [copy_if_exists(path, snapshot_dir) for path in files]
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "snapshot_id": snapshot_id,
        "snapshot_dir": str(snapshot_dir),
        "archived_count": sum(1 for item in archived if item.get("archived")),
        "files": archived,
    }
    manifest = snapshot_dir / "snapshot_manifest.json"
    manifest.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    latest = Path(args.history_dir) / "latest_snapshot_manifest.json"
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    if payload["archived_count"] == 0:
        print("WARNING: No existing Oddspedia files were available to archive.")
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
