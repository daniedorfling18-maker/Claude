from __future__ import annotations

# Action trigger stamp: 2026-06-20T22:28:00+02:00

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


def round_metrics(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"row_count": 0}
    try:
        frame = pd.read_csv(path).fillna("")
    except Exception as exc:
        return {"error": str(exc), "row_count": 0}
    return {
        "row_count": int(len(frame)),
        "rounds_with_scorelines": int((pd.to_numeric(frame.get("body_scoreline_count", 0), errors="coerce").fillna(0) > 0).sum()) if not frame.empty else 0,
        "rounds_with_fixture_visibility": int((pd.to_numeric(frame.get("fixture_visible_count", 0), errors="coerce").fillna(0) > 0).sum()) if not frame.empty else 0,
        "total_body_scorelines": int(pd.to_numeric(frame.get("body_scoreline_count", 0), errors="coerce").fillna(0).sum()) if not frame.empty else 0,
        "total_table_scorelines": int(pd.to_numeric(frame.get("table_scoreline_count", 0), errors="coerce").fillna(0).sum()) if not frame.empty else 0,
        "total_xhr_fetch_urls": int(pd.to_numeric(frame.get("xhr_fetch_url_count", 0), errors="coerce").fillna(0).sum()) if not frame.empty else 0,
    }


def oddspedia_access_status(page_csv: Path, odd_fields: dict[str, Any]) -> dict[str, Any]:
    if not page_csv.exists():
        return {"status": "no_page_inventory"}
    try:
        pages = pd.read_csv(page_csv).fillna("")
    except Exception as exc:
        return {"status": "page_inventory_read_error", "error": str(exc)}
    if pages.empty:
        return {"status": "empty_page_inventory"}
    titles = pages.get("title", pd.Series(dtype=str)).astype(str).str.lower()
    cloudflare_pages = int(titles.str.contains("just a moment", na=False).sum())
    candidate_count = int(pd.to_numeric(pages.get("app_state_candidate_count", 0), errors="coerce").fillna(0).sum())
    blocked = cloudflare_pages > 0 and candidate_count == 0 and int(odd_fields.get("field_row_count", 0) or 0) == 0
    return {
        "status": "cloudflare_blocked" if blocked else "accessible_or_partial",
        "cloudflare_page_count": cloudflare_pages,
        "page_row_count": int(len(pages)),
        "app_state_candidate_total": candidate_count,
        "note": "GitHub-hosted browser reached Cloudflare challenge pages; use local/self-hosted capture or cached Oddspedia inventory for live market extraction." if blocked else "Oddspedia page inventory is not fully blocked.",
    }


def run_superbru_abbreviation_enhancement(inv: Path) -> dict[str, Any]:
    try:
        from scripts.enhance_superbru_fixture_coverage import enhance_coverage
    except Exception as exc:
        return {"status": "enhancer_import_failed", "error": str(exc)}

    fixtures_path = Path("outputs/final_locked_picks/superbru_final_card.csv")
    coverage_path = inv / "superbru_visible_pick_coverage.csv"
    controls_path = inv / "superbru_control_inventory.csv"
    detected_path = inv / "superbru_detected_match_controls.csv"
    fields_path = inv / "superbru_available_fields.json"
    try:
        fixtures = pd.read_csv(fixtures_path).fillna("") if fixtures_path.exists() else pd.DataFrame()
        coverage = pd.read_csv(coverage_path).fillna("") if coverage_path.exists() else pd.DataFrame()
        controls = pd.read_csv(controls_path).fillna("") if controls_path.exists() else pd.DataFrame()
        enhanced_coverage, detected, summary = enhance_coverage(coverage, controls, fixtures)
        enhanced_coverage.to_csv(coverage_path, index=False)
        detected.to_csv(detected_path, index=False)
        fields = load_json(fields_path)
        fields["abbreviation_enhancement"] = summary
        fields["fixture_visible_count"] = summary.get("fixture_visible_count_after_enhancement", fields.get("fixture_visible_count", 0))
        fields["likely_pick_data_visible_count"] = summary.get("likely_pick_data_visible_count_after_enhancement", fields.get("likely_pick_data_visible_count", 0))
        fields.setdefault("outputs", {})["detected_match_controls_csv"] = str(detected_path)
        fields_path.write_text(json.dumps(fields, indent=2, default=str), encoding="utf-8")
        return {"status": "enhanced", **summary}
    except Exception as exc:
        return {"status": "enhancer_failed", "error": str(exc)}


def main() -> int:
    args = build_parser().parse_args()
    inv = Path(args.inventory_dir)
    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)

    superbru_enhancement = run_superbru_abbreviation_enhancement(inv)

    odd_fields = load_json(inv / "oddspedia_available_fields.json")
    odd_high_value = load_json(inv / "oddspedia_high_value_market_paths_summary.json")
    superbru_fields = load_json(inv / "superbru_available_fields.json")
    odd_markets = inv / "oddspedia_available_markets.csv"
    odd_high_value_csv = inv / "oddspedia_high_value_market_paths.csv"
    odd_network = inv / "oddspedia_network_inventory.csv"
    odd_pages = inv / "oddspedia_page_inventory.csv"
    superbru_network = inv / "superbru_network_inventory.csv"
    superbru_tables = inv / "superbru_table_inventory.csv"
    superbru_rounds = inv / "superbru_round_inventory.csv"
    superbru_coverage = inv / "superbru_visible_pick_coverage.csv"
    odd_status = oddspedia_access_status(odd_pages, odd_fields)

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "inventory_dir": str(inv),
        "oddspedia": {
            "access_status": odd_status,
            "input_match_count": odd_fields.get("input_match_count", 0),
            "field_row_count": odd_fields.get("field_row_count", 0),
            "market_like_row_count": odd_fields.get("market_like_row_count", csv_count(odd_markets)),
            "unique_market_path_count": csv_nunique(odd_markets, "market_path"),
            "high_value_market_path_count": odd_high_value.get("ranked_market_path_count", csv_count(odd_high_value_csv)),
            "feature_family_counts": odd_high_value.get("feature_family_counts", {}),
            "network_row_count": odd_fields.get("network_row_count", csv_count(odd_network)),
            "outputs": {
                **odd_fields.get("outputs", {}),
                **odd_high_value.get("outputs", {}),
            },
        },
        "superbru": {
            "abbreviation_enhancement": superbru_enhancement,
            "round_state_count": superbru_fields.get("round_state_count", 0),
            "rounds_clicked": superbru_fields.get("rounds_clicked", 0),
            "round_inventory": round_metrics(superbru_rounds),
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
            "status": "inventory_plus_ranked_feature_candidates",
            "note": "This summary reports browser-exposed data, Superbru abbreviation-mapped fixture coverage, and Oddspedia access status. It still does not promote newly discovered fields into the prediction model.",
            "next_step": "Use Superbru detected match controls as pool-intelligence features. For Oddspedia, use local/self-hosted capture or cached inventory because GitHub-hosted Actions are Cloudflare-blocked.",
        },
    }
    out_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
