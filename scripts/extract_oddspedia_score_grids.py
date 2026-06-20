from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

SCORE_RE = re.compile(r"^\s*(\d{1,2})\s*-\s*(\d{1,2})\s*$")
PROB_KEYS = ("probability", "prob", "chance", "percent", "percentage", "value")
ODDS_KEYS = ("odds", "price", "decimal", "decimalOdds")
SCORE_KEYS = ("score", "scoreline", "name", "label", "selection", "outcome", "marketName")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract correct-score grid rows from discovered Oddspedia JSON payloads into SmartBet grid schema."
    )
    parser.add_argument("--discovery-json", default="outputs/oddspedia_api_discovery/oddspedia_discovered_payloads.json")
    parser.add_argument("--out-csv", default="inputs/smartbet_grids/oddspedia_probability_grids_auto.csv")
    parser.add_argument("--source-date", default="", help="Optional YYYY-MM-DD source date. Defaults to today UTC.")
    parser.add_argument("--min-probability-pct", type=float, default=0.0)
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def fnum(value: Any, default: float = float("nan")) -> float:
    try:
        text = txt(value).replace("%", "").replace(",", ".")
        if not text:
            return default
        return float(text)
    except Exception:
        return default


def parse_score(value: Any) -> tuple[int, int] | None:
    m = SCORE_RE.match(txt(value))
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def implied_probability_pct_from_odds(value: Any) -> float:
    odds = fnum(value)
    if math.isnan(odds) or odds <= 1.0:
        return float("nan")
    return round(100.0 / odds, 6)


def probability_pct(value: Any) -> float:
    parsed = fnum(value)
    if math.isnan(parsed):
        return float("nan")
    if parsed <= 1.0:
        return round(parsed * 100.0, 6)
    return round(parsed, 6)


def walk(value: Any, path: str = "$") -> Iterable[tuple[str, Any]]:
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, f"{path}[{index}]")


def find_score_in_dict(item: dict[str, Any]) -> tuple[int, int] | None:
    for key in SCORE_KEYS:
        if key in item:
            parsed = parse_score(item.get(key))
            if parsed is not None:
                return parsed
    for value in item.values():
        parsed = parse_score(value)
        if parsed is not None:
            return parsed
    return None


def find_probability_in_dict(item: dict[str, Any]) -> tuple[float, str]:
    for key in PROB_KEYS:
        if key in item:
            pct = probability_pct(item.get(key))
            if not math.isnan(pct):
                return pct, key
    for key in ODDS_KEYS:
        if key in item:
            pct = implied_probability_pct_from_odds(item.get(key))
            if not math.isnan(pct):
                return pct, f"implied_from_{key}"
    return float("nan"), ""


def extract_rows_from_payload(payload: Any, payload_file: str, source_date: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path, node in walk(payload):
        if not isinstance(node, dict):
            continue
        score = find_score_in_dict(node)
        if score is None:
            continue
        pct, prob_key = find_probability_in_dict(node)
        if math.isnan(pct):
            continue
        rows.append(
            {
                "source_date": source_date,
                "source": "Oddspedia auto-discovered JSON",
                "screenshot_file": Path(payload_file).name,
                "home": "",
                "away": "",
                "home_name": "",
                "away_name": "",
                "actual_score": "",
                "winner_home_pct": "",
                "winner_draw_pct": "",
                "winner_away_pct": "",
                "modal_score": "",
                "home_goals": score[0],
                "away_goals": score[1],
                "probability_pct": pct,
                "probability_raw": txt(node.get(prob_key.replace("implied_from_", ""))) if prob_key else "",
                "cell_type": "oddspedia_auto_correct_score",
                "json_path": path,
                "probability_key": prob_key,
                "payload_file": payload_file,
            }
        )
    return rows


def read_discovery(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Missing discovery JSON: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    files: list[str] = []
    for item in data:
        payload_file = txt(item.get("payload_file")) if isinstance(item, dict) else ""
        if payload_file and Path(payload_file).exists():
            files.append(payload_file)
    return files


def main() -> int:
    args = build_parser().parse_args()
    source_date = args.source_date or datetime.now(timezone.utc).date().isoformat()
    files = read_discovery(Path(args.discovery_json))
    rows: list[dict[str, Any]] = []
    for file_name in files:
        try:
            payload = json.loads(Path(file_name).read_text(encoding="utf-8"))
            rows.extend(extract_rows_from_payload(payload, file_name, source_date))
        except Exception as exc:
            print(f"Skipping {file_name}: {exc}")

    result = pd.DataFrame(rows)
    if not result.empty:
        result = result[result["probability_pct"].astype(float).ge(args.min_probability_pct)]
        result = result.drop_duplicates(["home_goals", "away_goals", "probability_pct", "payload_file"])
        result = result.sort_values(["payload_file", "probability_pct"], ascending=[True, False])

    out_csv = Path(args.out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(out_csv, index=False)

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "payload_file_count": len(files),
        "extracted_rows": int(len(result)),
        "out_csv": str(out_csv),
        "note": "Home/away names may need to be mapped after API discovery if payloads do not contain match metadata.",
    }
    print(json.dumps(summary, indent=2))
    print(f"Wrote {out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
