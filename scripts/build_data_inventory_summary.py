from __future__ import annotations

# Action trigger stamp: 2026-06-20T23:04:00+02:00

import argparse
import importlib.util
import json
import re
import shutil
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


def read_csv(path: Path, nrows: int | None = None) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, nrows=nrows).fillna("")
    except Exception:
        return pd.DataFrame()


def csv_count(path: Path) -> int:
    frame = read_csv(path)
    return int(len(frame)) if not frame.empty else 0


def csv_nunique(path: Path, column: str) -> int:
    frame = read_csv(path)
    if frame.empty or column not in frame.columns:
        return 0
    return int(frame[column].astype(str).replace("", pd.NA).dropna().nunique())


def bool_count(frame: pd.DataFrame, column: str) -> int:
    if column not in frame.columns:
        return 0
    return int(frame[column].astype(str).str.lower().isin(["true", "1", "yes"]).sum())


def coverage_metrics(path: Path) -> dict[str, Any]:
    frame = read_csv(path)
    row_count = int(len(frame))
    fixture_visible = bool_count(frame, "fixture_visible")
    likely_pick = bool_count(frame, "likely_pick_data_visible")
    return {
        "row_count": row_count,
        "fixture_visible_count": fixture_visible,
        "likely_pick_data_visible_count": likely_pick,
        "fixture_visible_rate": fixture_visible / row_count if row_count else 0,
        "likely_pick_data_visible_rate": likely_pick / row_count if row_count else 0,
    }


def round_metrics(path: Path) -> dict[str, Any]:
    frame = read_csv(path)
    if frame.empty:
        return {"row_count": 0}
    return {
        "row_count": int(len(frame)),
        "rounds_with_scorelines": int((pd.to_numeric(frame.get("body_scoreline_count", 0), errors="coerce").fillna(0) > 0).sum()),
        "rounds_with_fixture_visibility": int((pd.to_numeric(frame.get("fixture_visible_count", 0), errors="coerce").fillna(0) > 0).sum()),
        "total_body_scorelines": int(pd.to_numeric(frame.get("body_scoreline_count", 0), errors="coerce").fillna(0).sum()),
        "total_table_scorelines": int(pd.to_numeric(frame.get("table_scoreline_count", 0), errors="coerce").fillna(0).sum()),
        "total_xhr_fetch_urls": int(pd.to_numeric(frame.get("xhr_fetch_url_count", 0), errors="coerce").fillna(0).sum()),
    }


def oddspedia_access_status(page_csv: Path, odd_fields: dict[str, Any]) -> dict[str, Any]:
    pages = read_csv(page_csv)
    if pages.empty:
        return {"status": "no_or_empty_page_inventory"}
    titles = pages.get("title", pd.Series(dtype=str)).astype(str).str.lower()
    cloudflare_pages = int(titles.str.contains("just a moment", na=False).sum())
    candidate_count = int(pd.to_numeric(pages.get("app_state_candidate_count", 0), errors="coerce").fillna(0).sum())
    blocked = cloudflare_pages > 0 and candidate_count == 0 and int(odd_fields.get("field_row_count", 0) or 0) == 0
    return {
        "status": "cloudflare_blocked" if blocked else "accessible_or_partial",
        "cloudflare_page_count": cloudflare_pages,
        "page_row_count": int(len(pages)),
        "app_state_candidate_total": candidate_count,
        "note": "GitHub-hosted browser reached Cloudflare challenge pages; cached Oddspedia/smartbet_grids fallback will be used if available." if blocked else "Oddspedia page inventory is not fully blocked.",
    }


def load_module_from_scripts(filename: str, function_name: str) -> Any:
    module_path = Path(__file__).resolve().with_name(filename)
    spec = importlib.util.spec_from_file_location(filename.replace(".py", ""), module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not import {filename} from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, function_name)


def cached_capture_kind(path: Path, frame: pd.DataFrame) -> str:
    name = path.name.lower().replace("-", "_")
    cols = " ".join(str(c).lower() for c in frame.columns)
    haystack = f"{name} {cols}"
    if "smartbet_grids" in haystack or "smartbet" in haystack or "smart_bet" in haystack:
        return "smartbet_grids"
    if "probability_grid" in haystack or "probability grid" in haystack or "prob_grid" in haystack:
        return "probability_grid"
    if re.search(r"\bp_home\b|\bp_draw\b|\bp_away\b|home_probability|draw_probability|away_probability", haystack):
        return "probability_grid"
    if re.search(r"prob.*score|score.*prob|exact.*score|correct.*score", haystack):
        return "score_probability_grid"
    if "market_path" in frame.columns or "market_label" in frame.columns:
        return "market_inventory"
    if any(token in haystack for token in ["odds", "bookmaker", "probabilit", "market"]):
        return "generic_market_like_csv"
    return "unknown"


def has_match_identity(frame: pd.DataFrame) -> bool:
    cols = {str(c).lower() for c in frame.columns}
    return bool(
        "match_id" in cols
        or "fixture_id" in cols
        or "event_id" in cols
        or {"home_team", "away_team"}.issubset(cols)
        or {"home", "away"}.issubset(cols)
        or {"team_home", "team_away"}.issubset(cols)
    )


def normalise_cached_oddspedia_capture(source: Path, target: Path, kind: str) -> dict[str, Any]:
    frame = read_csv(source)
    lookup = {str(c).lower(): str(c) for c in frame.columns}
    for out_col, alternatives in {
        "home_team": ["home", "team_home", "home_name", "hometeam", "home_team_name"],
        "away_team": ["away", "team_away", "away_name", "awayteam", "away_team_name"],
    }.items():
        if out_col not in frame.columns:
            for alt in alternatives:
                if alt in lookup:
                    frame[out_col] = frame[lookup[alt]]
                    break
    if "match_id" not in frame.columns:
        if "fixture_id" in lookup:
            frame["match_id"] = frame[lookup["fixture_id"]].astype(str)
        elif "event_id" in lookup:
            frame["match_id"] = frame[lookup["event_id"]].astype(str)
        elif {"home_team", "away_team"}.issubset(frame.columns):
            frame["match_id"] = (
                frame["home_team"].astype(str).str.lower().str.replace(r"[^a-z0-9]+", "-", regex=True).str.strip("-")
                + "-"
                + frame["away_team"].astype(str).str.lower().str.replace(r"[^a-z0-9]+", "-", regex=True).str.strip("-")
            )
        else:
            frame["match_id"] = source.stem
    if "home_team" not in frame.columns:
        frame["home_team"] = ""
    if "away_team" not in frame.columns:
        frame["away_team"] = ""
    if "market_path" not in frame.columns:
        path_by_kind = {
            "smartbet_grids": "cached/smartbet_grids/probability_grid",
            "score_probability_grid": "cached/probability_grid/correct_score",
            "probability_grid": "cached/probability_grid/result_probabilities",
            "market_inventory": "cached/market_inventory",
            "generic_market_like_csv": "cached/generic_market_like",
        }
        frame["market_path"] = path_by_kind.get(kind, "cached/oddspedia_like_capture")
    if "market_label" not in frame.columns:
        label_by_kind = {
            "smartbet_grids": "Smartbet grids probability capture",
            "score_probability_grid": "Score probability grid",
            "probability_grid": "Result probability grid",
            "market_inventory": "Cached market inventory",
            "generic_market_like_csv": "Cached market-like capture",
        }
        frame["market_label"] = label_by_kind.get(kind, "Cached Oddspedia capture")
    if "source_name" not in frame.columns:
        frame["source_name"] = f"cached_{kind}"
    probability_cols = [c for c in frame.columns if re.search(r"prob|^p_|_p$|chance", str(c), re.IGNORECASE)]
    odds_cols = [c for c in frame.columns if re.search(r"odds|price|decimal", str(c), re.IGNORECASE)]
    bookmaker_cols = [c for c in frame.columns if re.search(r"book|provider|sportsbook", str(c), re.IGNORECASE)]
    outcome_cols = [c for c in frame.columns if re.search(r"outcome|selection|score|home|draw|away|fixture|match|event", str(c), re.IGNORECASE)]
    if "object_keys" not in frame.columns:
        frame["object_keys"] = ";".join(sorted(set(probability_cols + odds_cols + bookmaker_cols + outcome_cols))[:100])
    for col, value in {
        "probabilities_count": len(probability_cols),
        "odds_count": len(odds_cols),
        "bookmakers_count": max(1, len(bookmaker_cols)) if odds_cols or probability_cols else len(bookmaker_cols),
        "outcomes_count": max(0, len(outcome_cols)),
        "markets_count": 1,
    }.items():
        if col not in frame.columns:
            frame[col] = value
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(target, index=False)
    return {
        "normalised": True,
        "kind": kind,
        "row_count": int(len(frame)),
        "probability_column_count": len(probability_cols),
        "odds_column_count": len(odds_cols),
        "market_path_count": int(frame["market_path"].astype(str).nunique()),
    }


def find_cached_oddspedia_market_capture(live_markets: Path) -> dict[str, Any]:
    repo_root = Path.cwd()
    excluded_parts = {".git", "raw_diagnostics", "node_modules", ".venv", "venv"}
    patterns = [
        "**/*smartbet_grids*.csv", "**/*smartbet*.csv", "**/*smart_bet*.csv",
        "**/*probability*grid*.csv", "**/*prob*grid*.csv",
        "**/*score*prob*.csv", "**/*prob*score*.csv",
        "**/*oddspedia*available*markets*.csv", "**/*oddspedia*markets*.csv", "**/*oddspedia*market*.csv",
        "**/*market*inventory*.csv", "**/*.csv",
    ]
    candidates: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for pattern in patterns:
        for path in repo_root.glob(pattern):
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            if any(part in excluded_parts for part in path.parts):
                continue
            try:
                if path.resolve() == live_markets.resolve() or path.stat().st_size > 125_000_000:
                    continue
            except Exception:
                continue
            frame = read_csv(path, nrows=3000)
            if frame.empty:
                continue
            kind = cached_capture_kind(path, frame)
            if kind == "unknown":
                continue
            if not has_match_identity(frame) and kind not in {"smartbet_grids", "probability_grid", "score_probability_grid"}:
                continue
            row_count = csv_count(path)
            if row_count <= 0:
                continue
            market_paths = int(frame.get("market_path", pd.Series(dtype=str)).astype(str).replace("", pd.NA).dropna().nunique()) if "market_path" in frame.columns else 1
            priority = {"smartbet_grids": 110, "score_probability_grid": 100, "probability_grid": 95, "market_inventory": 80, "generic_market_like_csv": 50}.get(kind, 0)
            candidates.append({
                "path": str(path),
                "row_count": row_count,
                "sampled_unique_market_paths": market_paths,
                "columns": sorted(str(c) for c in frame.columns),
                "matched_by": kind,
                "kind_priority": priority,
                "has_match_identity": has_match_identity(frame),
            })
    candidates.sort(key=lambda r: (int(r["kind_priority"]), int(r["row_count"]), int(r["sampled_unique_market_paths"])), reverse=True)
    return candidates[0] if candidates else {"path": "", "row_count": 0, "sampled_unique_market_paths": 0, "columns": []}


def apply_oddspedia_cached_fallback(inv: Path, odd_status: dict[str, Any]) -> dict[str, Any]:
    markets_path = inv / "oddspedia_available_markets.csv"
    ranked_path = inv / "oddspedia_high_value_market_paths.csv"
    ranked_json_path = inv / "oddspedia_high_value_market_paths_summary.json"
    network_path = inv / "oddspedia_network_inventory.csv"
    if odd_status.get("status") != "cloudflare_blocked":
        return {"status": "not_needed", "reason": odd_status.get("status")}
    if csv_count(markets_path) > 0 and csv_nunique(markets_path, "market_path") > 0:
        return {"status": "not_needed", "reason": "live_markets_present"}
    best = find_cached_oddspedia_market_capture(markets_path)
    if not best.get("path"):
        return {"status": "missing", "reason": "no_valid_smartbet_grids_or_probability_capture_found"}
    source = Path(str(best["path"]))
    try:
        normalised = normalise_cached_oddspedia_capture(source, markets_path, str(best.get("matched_by") or "generic_market_like_csv"))
    except Exception:
        shutil.copyfile(source, markets_path)
        normalised = {"normalised": False}
    try:
        summarise_markets = load_module_from_scripts("filter_oddspedia_high_value_market_paths.py", "summarise_markets")
        summarise_network = load_module_from_scripts("filter_oddspedia_high_value_market_paths.py", "summarise_network")
        markets = read_csv(markets_path)
        network = read_csv(network_path)
        ranked = summarise_markets(markets, min_coverage_rate=0.25, top_n=250)
        ranked.to_csv(ranked_path, index=False)
        family_counts = ranked["feature_family"].value_counts().to_dict() if not ranked.empty else {}
        by_family = {family: group.head(10).to_dict(orient="records") for family, group in ranked.groupby("feature_family")} if not ranked.empty else {}
        payload = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "input_market_row_count": int(len(markets)),
            "ranked_market_path_count": int(len(ranked)),
            "min_coverage_rate": 0.25,
            "top_n": 250,
            "feature_family_counts": family_counts,
            "top_ranked_market_paths": ranked.head(30).to_dict(orient="records") if not ranked.empty else [],
            "top_ranked_by_family": by_family,
            "high_value_network_candidates": summarise_network(network),
            "outputs": {"out_csv": str(ranked_path), "out_json": str(ranked_json_path)},
            "fallback_source": best,
            "normalisation": normalised,
            "note": "Ranked using cached smartbet_grids/Oddspedia probability capture because GitHub-hosted live scrape was Cloudflare-blocked.",
        }
        ranked_json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return {"status": "applied", "source": best, "normalisation": normalised, "ranked_market_path_count": int(len(ranked))}
    except Exception as exc:
        return {"status": "applied_copy_only", "source": best, "normalisation": normalised, "error": str(exc)}


def run_superbru_abbreviation_enhancement(inv: Path) -> dict[str, Any]:
    try:
        enhance_coverage = load_module_from_scripts("enhance_superbru_fixture_coverage.py", "enhance_coverage")
    except Exception as exc:
        return {"status": "enhancer_import_failed", "error": str(exc)}
    fixtures_path = Path("outputs/final_locked_picks/superbru_final_card.csv")
    coverage_path = inv / "superbru_visible_pick_coverage.csv"
    controls_path = inv / "superbru_control_inventory.csv"
    detected_path = inv / "superbru_detected_match_controls.csv"
    fields_path = inv / "superbru_available_fields.json"
    try:
        fixtures = read_csv(fixtures_path)
        coverage = read_csv(coverage_path)
        controls = read_csv(controls_path)
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
    odd_status = oddspedia_access_status(inv / "oddspedia_page_inventory.csv", odd_fields)
    odd_fallback = apply_oddspedia_cached_fallback(inv, odd_status)
    odd_high_value = load_json(inv / "oddspedia_high_value_market_paths_summary.json")
    superbru_fields = load_json(inv / "superbru_available_fields.json")
    odd_markets = inv / "oddspedia_available_markets.csv"
    odd_high_value_csv = inv / "oddspedia_high_value_market_paths.csv"
    odd_network = inv / "oddspedia_network_inventory.csv"
    superbru_network = inv / "superbru_network_inventory.csv"
    superbru_tables = inv / "superbru_table_inventory.csv"
    superbru_rounds = inv / "superbru_round_inventory.csv"
    superbru_coverage = inv / "superbru_visible_pick_coverage.csv"
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "inventory_dir": str(inv),
        "oddspedia": {
            "access_status": odd_status,
            "cached_fallback": odd_fallback,
            "input_match_count": odd_fields.get("input_match_count", 0),
            "field_row_count": odd_fields.get("field_row_count", 0),
            "market_like_row_count": odd_fields.get("market_like_row_count", csv_count(odd_markets)),
            "unique_market_path_count": csv_nunique(odd_markets, "market_path"),
            "high_value_market_path_count": odd_high_value.get("ranked_market_path_count", csv_count(odd_high_value_csv)),
            "feature_family_counts": odd_high_value.get("feature_family_counts", {}),
            "network_row_count": odd_fields.get("network_row_count", csv_count(odd_network)),
            "outputs": {**odd_fields.get("outputs", {}), **odd_high_value.get("outputs", {})},
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
            "note": "This summary reports browser-exposed data, Superbru abbreviation-mapped fixture coverage, Oddspedia access status, and cached smartbet_grids/probability-grid fallback usage when Actions are Cloudflare-blocked.",
            "next_step": "Use Superbru detected match controls and cached smartbet_grids probability-grid paths as pool-intelligence/model feature candidates.",
        },
    }
    out_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
