from __future__ import annotations

import argparse
import html
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

PERCENT_RE = re.compile(r"<?\d+(?:\.\d+)?%")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scrape visible SmartBet prematch probability text from Oddspedia match pages."
    )
    parser.add_argument("--urls-csv", default="inputs/oddspedia_match_urls.csv")
    parser.add_argument("--out-csv", default="inputs/smartbet_grids/oddspedia_smartbet_text_blocks.csv")
    parser.add_argument("--out-json", default="outputs/oddspedia_smartbet_text/oddspedia_smartbet_text_blocks.json")
    parser.add_argument("--timeout", type=int, default=30)
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def clean_text(raw_html: str) -> str:
    # Keep headings/blocks separated enough for regexes to work.
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", raw_html, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_between(text: str, start: str, end_options: list[str]) -> str:
    lower = text.lower()
    start_index = lower.find(start.lower())
    if start_index < 0:
        return ""
    after_start = start_index + len(start)
    end_index = len(text)
    for end in end_options:
        idx = lower.find(end.lower(), after_start)
        if idx >= 0:
            end_index = min(end_index, idx)
    return text[after_start:end_index].strip()


def extract_percent_before_label(block: str, label: str) -> str:
    # Handles both "40% Home" and occasional spacing variants.
    match = re.search(rf"(<?\d+(?:\.\d+)?%)\s+{re.escape(label)}\b", block, flags=re.I)
    return match.group(1) if match else ""


def extract_correct_score_modal(text: str) -> str:
    match = re.search(r"Correct Score:\s*(\d+\s*-\s*\d+)", text, flags=re.I)
    return re.sub(r"\s+", "", match.group(1)) if match else ""


def fetch_url(url: str, timeout: int) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.text


def scrape_row(row: dict[str, Any], timeout: int) -> dict[str, Any]:
    url = txt(row.get("url"))
    home_team = txt(row.get("home_team"))
    away_team = txt(row.get("away_team"))
    out: dict[str, Any] = {
        "scraped_at_utc": datetime.now(timezone.utc).isoformat(),
        "match_id": txt(row.get("match_id")),
        "home_team": home_team,
        "away_team": away_team,
        "url": url,
        "winner_home_pct": "",
        "winner_draw_pct": "",
        "winner_away_pct": "",
        "smartbet_forecast_correct": "",
        "correct_score_modal": "",
        "correct_score_probability_tokens": "",
        "correct_score_probability_count": 0,
        "correct_score_block": "",
        "error": "",
    }
    try:
        page_html = fetch_url(url, timeout)
        text = clean_text(page_html)
        prob_block = extract_between(text, "Prematch Probabilities", ["Outright odds", "Player markets", "Bonus Offers", "About the Match"])
        winner_block = extract_between(prob_block or text, "Winner probabilities", ["SmartBet forecast", "Correct Score probabilities"])
        correct_block = extract_between(prob_block or text, "Correct Score probabilities", ["Correct Score:", "Outright odds", "Player markets"])
        forecast_match = re.search(r"SmartBet forecast correct:\s*([^\n]*?)(?:Correct Score probabilities|Correct Score:|Outright odds|$)", prob_block or text, flags=re.I)

        out["winner_home_pct"] = extract_percent_before_label(winner_block, "Home")
        out["winner_draw_pct"] = extract_percent_before_label(winner_block, "Draw")
        out["winner_away_pct"] = extract_percent_before_label(winner_block, "Away")
        out["smartbet_forecast_correct"] = forecast_match.group(1).strip() if forecast_match else ""
        out["correct_score_modal"] = extract_correct_score_modal(prob_block or text)
        tokens = PERCENT_RE.findall(correct_block)
        out["correct_score_probability_tokens"] = json.dumps(tokens)
        out["correct_score_probability_count"] = len(tokens)
        out["correct_score_block"] = correct_block[:2000]
        if not prob_block:
            out["error"] = "Prematch Probabilities block not found"
    except Exception as exc:
        out["error"] = str(exc)
    return out


def main() -> int:
    args = build_parser().parse_args()
    urls_path = Path(args.urls_csv)
    if not urls_path.exists():
        raise FileNotFoundError(f"Missing URLs CSV: {urls_path}")
    urls = pd.read_csv(urls_path).fillna("").to_dict(orient="records")
    rows = [scrape_row(row, args.timeout) for row in urls if txt(row.get("url"))]
    result = pd.DataFrame(rows)

    out_csv = Path(args.out_csv)
    out_json = Path(args.out_json)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(out_csv, index=False)
    out_json.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_url_count": len(urls),
        "scraped_rows": len(rows),
        "rows_with_probability_block": int((result.get("error", pd.Series(dtype=str)) == "").sum()) if not result.empty else 0,
        "out_csv": str(out_csv),
        "out_json": str(out_json),
    }
    print(json.dumps(summary, indent=2))
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
