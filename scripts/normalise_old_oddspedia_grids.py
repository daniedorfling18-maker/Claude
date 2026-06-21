from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

import pandas as pd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Normalise historical Oddspedia score_grids.csv files into the SmartBet grid schema."
    )
    parser.add_argument("--in-dir", default="inputs/smartbet_grids")
    parser.add_argument("--glob", default="old_*score_grids.csv")
    parser.add_argument("--results-csv", default="inputs/results_to_date.csv")
    parser.add_argument("--out-csv", default="inputs/smartbet_grids/converted_old_oddspedia_score_grids.csv")
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def norm_team(value: Any) -> str:
    return "".join(ch.lower() for ch in txt(value) if ch.isalnum())


def parse_score(value: Any) -> tuple[int, int] | None:
    match = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", txt(value))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def num(value: Any) -> float | None:
    text = txt(value).replace("%", "").replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        return None


def probability_pct(value: Any) -> float | str:
    parsed = num(value)
    if parsed is None:
        return ""
    return round(parsed * 100.0, 6) if parsed <= 1.0 else round(parsed, 6)


def load_results_lookup(results_csv: Path) -> dict[tuple[str, str], str]:
    lookup: dict[tuple[str, str], str] = {}
    if not results_csv.exists():
        return lookup

    results = pd.read_csv(results_csv).fillna("")
    required = {"home_team", "away_team", "home_goals", "away_goals"}
    if not required.issubset(results.columns):
        return lookup

    for _, row in results.iterrows():
        home_goals = txt(row.get("home_goals"))
        away_goals = txt(row.get("away_goals"))
        if not home_goals.isdigit() or not away_goals.isdigit():
            continue
        key = (norm_team(row.get("home_team")), norm_team(row.get("away_team")))
        lookup[key] = f"{int(home_goals)}-{int(away_goals)}"
    return lookup


def normalise_file(path: Path, results_lookup: dict[tuple[str, str], str]) -> list[dict[str, Any]]:
    frame = pd.read_csv(path).fillna("")
    required = {"home_team", "away_team", "scoreline", "probability"}
    if not required.issubset(frame.columns):
        print(f"Skipping incompatible old file: {path}")
        return []

    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        parsed = parse_score(row.get("scoreline"))
        if parsed is None:
            continue

        home = txt(row.get("home_team"))
        away = txt(row.get("away_team"))
        key = (norm_team(home), norm_team(away))
        selected = txt(row.get("selected_correct_score"))
        modal = selected if parse_score(selected) is not None else ""

        rows.append(
            {
                "source_date": txt(row.get("scraped_at"))[:10] or "2026-06-16",
                "source": txt(row.get("source")) or "Oddspedia historical score grid",
                "screenshot_file": path.name,
                "home": "",
                "away": "",
                "home_name": home,
                "away_name": away,
                "actual_score": results_lookup.get(key, ""),
                "winner_home_pct": probability_pct(row.get("home_win_probability")),
                "winner_draw_pct": probability_pct(row.get("draw_probability")),
                "winner_away_pct": probability_pct(row.get("away_win_probability")),
                "modal_score": modal,
                "home_goals": parsed[0],
                "away_goals": parsed[1],
                "probability_pct": probability_pct(row.get("probability")),
                "probability_raw": txt(row.get("probability")),
                "cell_type": txt(row.get("bucket")) or "old_score_grid",
                "match_id": txt(row.get("match_id")),
                "url": txt(row.get("url")),
                "old_source_file": str(path),
            }
        )
    return rows


def main() -> int:
    args = build_parser().parse_args()
    in_dir = Path(args.in_dir)
    out_csv = Path(args.out_csv)
    results_lookup = load_results_lookup(Path(args.results_csv))

    files = sorted(in_dir.glob(args.glob))
    rows: list[dict[str, Any]] = []
    for path in files:
        try:
            rows.extend(normalise_file(path, results_lookup))
        except Exception as exc:
            print(f"Skipping unreadable file: {path} :: {exc}")

    if not rows:
        raise SystemExit("No historical Oddspedia grid rows could be normalised.")

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    result = pd.DataFrame(rows)
    result.to_csv(out_csv, index=False)

    unique_matches = result[["home_name", "away_name"]].drop_duplicates()
    completed = result[result["actual_score"].astype(str).str.len() > 0][["home_name", "away_name"]].drop_duplicates()
    print(f"Wrote {out_csv} with {len(result)} score-cell rows from {len(files)} old files.")
    print(f"Unique old matches: {len(unique_matches)}")
    print(f"Completed old matches matched from {args.results_csv}: {len(completed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
