from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

SCORE_RE = re.compile(r"\b([A-Z]{2,4})\s+(\d{1,2})\s*[-:]\s*(\d{1,2})\s+([A-Z]{2,4})\b")
VERSUS_RE = re.compile(r"\b([A-Z]{2,4})\s+v(?:s\.)?\s+([A-Z]{2,4})\b", re.IGNORECASE)

FIFA_CODES = {
    "argentina": "ARG", "australia": "AUS", "austria": "AUT", "belgium": "BEL",
    "brazil": "BRA", "cape verde": "CPV", "colombia": "COL", "curacao": "CUR",
    "dr congo": "COD", "congo dr": "COD", "ecuador": "ECU", "egypt": "EGY",
    "france": "FRA", "germany": "GER", "ghana": "GHA", "iran": "IRN",
    "iraq": "IRQ", "ivory coast": "CIV", "cote d ivoire": "CIV", "japan": "JPN",
    "netherlands": "NED", "new zealand": "NZL", "paraguay": "PAR", "portugal": "POR",
    "saudi arabia": "KSA", "south africa": "RSA", "south korea": "KOR", "spain": "ESP",
    "sweden": "SWE", "tunisia": "TUN", "turkiye": "TUR", "türkiye": "TUR",
    "turkey": "TUR", "uruguay": "URU", "usa": "USA", "united states": "USA",
    "uzbekistan": "UZB",
}

ALIASES = {
    "KSA": {"saudi arabia"},
    "CIV": {"ivory coast", "cote d ivoire", "côte d'ivoire"},
    "CPV": {"cape verde"},
    "COD": {"dr congo", "congo dr"},
    "DRC": {"dr congo", "congo dr"},
    "NZL": {"new zealand"},
    "RSA": {"south africa"},
    "KOR": {"south korea"},
    "USA": {"usa", "united states"},
}


def norm(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.lower().strip().replace("&", " and ").replace("’", "'")
    text = re.sub(r"[^a-z0-9']+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def team_codes(team: str) -> set[str]:
    n = norm(team)
    codes: set[str] = set()
    if not n:
        return codes
    if n in FIFA_CODES:
        codes.add(FIFA_CODES[n])
    compact = n.replace(" ", "")
    if compact:
        codes.add(compact[:3].upper())
    parts = n.split()
    if len(parts) > 1:
        codes.add("".join(part[:1] for part in parts).upper())
        codes.add(parts[-1][:3].upper())
    else:
        codes.add(n[:3].upper())
    for code, aliases in ALIASES.items():
        if n in {norm(a) for a in aliases}:
            codes.add(code)
    return {code for code in codes if code}


def code_to_teams(fixtures: pd.DataFrame) -> dict[str, set[str]]:
    mapping: dict[str, set[str]] = {}
    for _, row in fixtures.iterrows():
        for col in ["home_team", "away_team"]:
            team = str(row.get(col, "")).strip()
            for code in team_codes(team):
                mapping.setdefault(code, set()).add(norm(team))
    for code, aliases in ALIASES.items():
        mapping.setdefault(code, set()).update(norm(alias) for alias in aliases)
    return mapping


def parse_control_matches(controls: pd.DataFrame) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    if controls.empty or "text" not in controls.columns:
        return matches
    for _, row in controls.fillna("").iterrows():
        text = str(row.get("text", "")).strip()
        if not text:
            continue
        for m in SCORE_RE.finditer(text):
            matches.append({
                "round_label": row.get("round_label", ""),
                "round_scan_index": row.get("round_scan_index", ""),
                "control_index": row.get("control_index", ""),
                "home_code": m.group(1).upper(),
                "away_code": m.group(4).upper(),
                "home_score": int(m.group(2)),
                "away_score": int(m.group(3)),
                "detected_scoreline": f"{m.group(2)}-{m.group(3)}",
                "detected_control_text": text,
                "detection_type": "scoreline_control",
            })
        for m in VERSUS_RE.finditer(text):
            matches.append({
                "round_label": row.get("round_label", ""),
                "round_scan_index": row.get("round_scan_index", ""),
                "control_index": row.get("control_index", ""),
                "home_code": m.group(1).upper(),
                "away_code": m.group(2).upper(),
                "home_score": "",
                "away_score": "",
                "detected_scoreline": "",
                "detected_control_text": text,
                "detection_type": "fixture_control",
            })
    dedup: dict[tuple[Any, ...], dict[str, Any]] = {}
    for item in matches:
        key = (item.get("round_label"), item.get("home_code"), item.get("away_code"), item.get("detected_scoreline"), item.get("detected_control_text"))
        dedup[key] = item
    return list(dedup.values())


def match_fixture(control: dict[str, Any], fixtures: pd.DataFrame, code_map: dict[str, set[str]]) -> dict[str, Any] | None:
    home_code = str(control.get("home_code", "")).upper()
    away_code = str(control.get("away_code", "")).upper()
    possible_home = code_map.get(home_code, set())
    possible_away = code_map.get(away_code, set())
    for _, row in fixtures.iterrows():
        home = norm(row.get("home_team", ""))
        away = norm(row.get("away_team", ""))
        if home in possible_home and away in possible_away:
            return row.to_dict()
        if home in possible_away and away in possible_home:
            flipped = row.to_dict()
            flipped["_flipped"] = True
            return flipped
    return None


def enhance_coverage(coverage: pd.DataFrame, controls: pd.DataFrame, fixtures: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    if fixtures.empty:
        return coverage, pd.DataFrame(), {"enhanced_match_count": 0, "reason": "no fixtures"}
    code_map = code_to_teams(fixtures)
    parsed = parse_control_matches(controls)
    enhanced_rows: list[dict[str, Any]] = []
    for item in parsed:
        fixture = match_fixture(item, fixtures, code_map)
        if not fixture:
            continue
        row = {**item, **fixture}
        row["match_id"] = fixture.get("match_id") or f"{norm(fixture.get('home_team')).replace(' ', '-')}-{norm(fixture.get('away_team')).replace(' ', '-')}"
        row["fixture_visible"] = True
        row["home_visible"] = True
        row["away_visible"] = True
        row["nearby_scoreline_count"] = 1 if item.get("detected_scoreline") else 0
        row["likely_pick_data_visible"] = bool(item.get("detected_scoreline"))
        row["nearby_text_sample"] = item.get("detected_control_text", "")
        enhanced_rows.append(row)
    enhanced = pd.DataFrame(enhanced_rows)
    if coverage.empty or enhanced.empty:
        return coverage, enhanced, {"enhanced_match_count": int(len(enhanced)), "parsed_control_match_count": int(len(parsed))}

    out = coverage.copy()
    for col in ["detected_control_text", "home_code", "away_code", "detected_scoreline", "detection_type"]:
        if col not in out.columns:
            out[col] = ""

    for _, e in enhanced.iterrows():
        match_id = str(e.get("match_id", ""))
        if not match_id:
            continue
        mask = out["match_id"].astype(str).eq(match_id)
        if "round_scan_index" in out.columns and str(e.get("round_scan_index", "")) != "":
            same_round = out["round_scan_index"].astype(str).eq(str(e.get("round_scan_index", "")))
            if same_round.any():
                mask = mask & same_round
        if not mask.any():
            mask = out["match_id"].astype(str).eq(match_id)
        out.loc[mask, "home_visible"] = True
        out.loc[mask, "away_visible"] = True
        out.loc[mask, "fixture_visible"] = True
        out.loc[mask, "nearby_scoreline_count"] = int(e.get("nearby_scoreline_count") or 0)
        out.loc[mask, "likely_pick_data_visible"] = bool(e.get("likely_pick_data_visible"))
        out.loc[mask, "nearby_text_sample"] = str(e.get("nearby_text_sample", ""))
        out.loc[mask, "detected_control_text"] = str(e.get("detected_control_text", ""))
        out.loc[mask, "home_code"] = str(e.get("home_code", ""))
        out.loc[mask, "away_code"] = str(e.get("away_code", ""))
        out.loc[mask, "detected_scoreline"] = str(e.get("detected_scoreline", ""))
        out.loc[mask, "detection_type"] = str(e.get("detection_type", ""))

    summary = {
        "parsed_control_match_count": int(len(parsed)),
        "enhanced_match_count": int(enhanced["match_id"].nunique()) if "match_id" in enhanced.columns else int(len(enhanced)),
        "fixture_visible_count_after_enhancement": int(out["fixture_visible"].astype(str).str.lower().isin(["true", "1", "yes"]).sum()) if "fixture_visible" in out.columns else 0,
        "likely_pick_data_visible_count_after_enhancement": int(out["likely_pick_data_visible"].astype(str).str.lower().isin(["true", "1", "yes"]).sum()) if "likely_pick_data_visible" in out.columns else 0,
    }
    return out, enhanced, summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Enhance Superbru fixture coverage by mapping displayed team abbreviations back to fixture teams.")
    parser.add_argument("--fixtures-csv", default="outputs/final_locked_picks/superbru_final_card.csv")
    parser.add_argument("--coverage-csv", default="outputs/data_inventory/superbru_visible_pick_coverage.csv")
    parser.add_argument("--controls-csv", default="outputs/data_inventory/superbru_control_inventory.csv")
    parser.add_argument("--fields-json", default="outputs/data_inventory/superbru_available_fields.json")
    parser.add_argument("--out-detected-csv", default="outputs/data_inventory/superbru_detected_match_controls.csv")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    fixtures = pd.read_csv(args.fixtures_csv).fillna("") if Path(args.fixtures_csv).exists() else pd.DataFrame()
    coverage = pd.read_csv(args.coverage_csv).fillna("") if Path(args.coverage_csv).exists() else pd.DataFrame()
    controls = pd.read_csv(args.controls_csv).fillna("") if Path(args.controls_csv).exists() else pd.DataFrame()
    enhanced_coverage, detected, summary = enhance_coverage(coverage, controls, fixtures)
    Path(args.coverage_csv).parent.mkdir(parents=True, exist_ok=True)
    enhanced_coverage.to_csv(args.coverage_csv, index=False)
    detected.to_csv(args.out_detected_csv, index=False)
    fields_path = Path(args.fields_json)
    fields = {}
    if fields_path.exists():
        fields = json.loads(fields_path.read_text(encoding="utf-8"))
    fields["abbreviation_enhancement"] = summary
    fields["fixture_visible_count"] = summary.get("fixture_visible_count_after_enhancement", fields.get("fixture_visible_count", 0))
    fields["likely_pick_data_visible_count"] = summary.get("likely_pick_data_visible_count_after_enhancement", fields.get("likely_pick_data_visible_count", 0))
    fields.setdefault("outputs", {})["detected_match_controls_csv"] = args.out_detected_csv
    fields_path.write_text(json.dumps(fields, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
