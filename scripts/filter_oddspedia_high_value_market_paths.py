from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


KEYWORD_WEIGHTS = [
    ("correct_score", 45),
    ("correct score", 45),
    ("exact score", 45),
    ("actual_score", 45),
    ("modal_score", 42),
    ("home_goals", 36),
    ("away_goals", 36),
    ("smartbet_correct_score", 45),
    ("score", 18),
    ("1x2", 40),
    ("winner", 35),
    ("match winner", 35),
    ("full time result", 35),
    ("home draw away", 32),
    ("btts", 34),
    ("both teams", 34),
    ("over under", 32),
    ("over/under", 32),
    ("total", 30),
    ("goals", 22),
    ("handicap", 28),
    ("asian", 24),
    ("draw no bet", 22),
    ("double chance", 20),
    ("team total", 20),
    ("winning margin", 18),
    ("margin", 16),
    ("probabilities", 12),
    ("probability", 12),
    ("odds", 8),
    ("bookmaker", 6),
]

NOISE_KEYWORDS = [
    "breadcrumb", "modal dialog", "tooltip", "alert", "cookie", "image", "comment", "auth",
    "geo", "experiment", "device", "banner", "navigation", "translation", "seo",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reduce noisy Oddspedia data inventory into ranked, stable high-value market paths for Superbru EV."
    )
    parser.add_argument("--markets-csv", default="outputs/data_inventory/oddspedia_available_markets.csv")
    parser.add_argument("--network-csv", default="outputs/data_inventory/oddspedia_network_inventory.csv")
    parser.add_argument("--out-csv", default="outputs/data_inventory/oddspedia_high_value_market_paths.csv")
    parser.add_argument("--out-json", default="outputs/data_inventory/oddspedia_high_value_market_paths_summary.json")
    parser.add_argument("--min-coverage-rate", type=float, default=0.25)
    parser.add_argument("--top-n", type=int, default=250)
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def keyword_score(text: str) -> tuple[int, list[str], list[str]]:
    low = text.lower()
    hits: list[str] = []
    score = 0
    for keyword, weight in KEYWORD_WEIGHTS:
        if keyword in low:
            score += weight
            hits.append(keyword)
    noise_hits = [keyword for keyword in NOISE_KEYWORDS if keyword in low]
    score -= 15 * len(noise_hits)
    return score, hits, noise_hits


def classify_feature_family(text: str) -> str:
    low = text.lower()
    if any(token in low for token in [
        "correct_score", "correct score", "exact score", "actual_score", "modal_score",
        "home_goals", "away_goals", "smartbet_correct_score", "score probability grid",
    ]):
        return "correct_score"
    if any(token in low for token in ["1x2", "winner", "match winner", "full time result", "home draw away"]):
        return "result_probability"
    if any(token in low for token in ["btts", "both teams"]):
        return "btts"
    if any(token in low for token in ["over under", "over/under", "total", "goals"]):
        return "totals_goals"
    if any(token in low for token in ["handicap", "asian"]):
        return "handicap"
    if "double chance" in low:
        return "double_chance"
    if "draw no bet" in low:
        return "draw_no_bet"
    if any(token in low for token in ["margin", "winning margin"]):
        return "margin"
    if "bookmaker" in low or "odds" in low:
        return "bookmaker_odds"
    return "other_market_like"


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path).fillna("")
    except Exception:
        return pd.DataFrame()


def summarise_markets(markets: pd.DataFrame, min_coverage_rate: float, top_n: int) -> pd.DataFrame:
    expected_cols = [
        "match_id", "home_team", "away_team", "market_path", "market_label",
        "object_keys", "probabilities_count", "odds_count", "markets_count",
        "bookmakers_count", "outcomes_count", "source_name",
    ]
    for col in expected_cols:
        if col not in markets.columns:
            markets[col] = ""

    total_matches = max(1, markets["match_id"].replace("", pd.NA).dropna().nunique())
    grouped_rows: list[dict[str, Any]] = []

    for market_path, group in markets.groupby("market_path", dropna=False):
        market_path = txt(market_path)
        labels = sorted({txt(v) for v in group["market_label"] if txt(v)})
        object_key_values = sorted({txt(v) for v in group["object_keys"] if txt(v)})
        source_names = sorted({txt(v) for v in group["source_name"] if txt(v)})
        combined = " ".join([market_path, " ".join(labels), " ".join(object_key_values), " ".join(source_names)])
        k_score, hits, noise_hits = keyword_score(combined)
        feature_family = classify_feature_family(combined)

        match_count = int(group["match_id"].replace("", pd.NA).dropna().nunique())
        coverage_rate = match_count / total_matches if total_matches else 0.0

        numeric_cols = ["probabilities_count", "odds_count", "markets_count", "bookmakers_count", "outcomes_count"]
        counts: dict[str, float] = {}
        for col in numeric_cols:
            counts[f"max_{col}"] = pd.to_numeric(group[col], errors="coerce").fillna(0).max() if col in group.columns else 0

        content_score = 0
        if counts["max_probabilities_count"] > 0:
            content_score += 20
        if counts["max_odds_count"] > 0:
            content_score += 14
        if counts["max_bookmakers_count"] > 0:
            content_score += 10
        if counts["max_outcomes_count"] > 0:
            content_score += 10
        if counts["max_markets_count"] > 0:
            content_score += 8

        stability_score = min(35.0, coverage_rate * 35.0)
        ev_priority_boost = {
            "correct_score": 35,
            "result_probability": 32,
            "totals_goals": 28,
            "btts": 24,
            "handicap": 20,
            "margin": 16,
            "draw_no_bet": 12,
            "double_chance": 10,
            "bookmaker_odds": 8,
            "other_market_like": 0,
        }.get(feature_family, 0)

        usefulness_score = k_score + content_score + stability_score + ev_priority_boost

        grouped_rows.append({
            "market_path": market_path,
            "feature_family": feature_family,
            "usefulness_score": round(float(usefulness_score), 4),
            "keyword_score": k_score,
            "content_score": round(float(content_score), 4),
            "stability_score": round(float(stability_score), 4),
            "ev_priority_boost": ev_priority_boost,
            "match_coverage_count": match_count,
            "match_coverage_rate": round(float(coverage_rate), 4),
            "total_match_count": total_matches,
            "sample_market_labels": " | ".join(labels[:8]),
            "sample_object_keys": " | ".join(object_key_values[:6]),
            "source_names": ";".join(source_names),
            "keyword_hits": ";".join(hits),
            "noise_hits": ";".join(noise_hits),
            **{k: int(v) for k, v in counts.items()},
        })

    out = pd.DataFrame(grouped_rows)
    if out.empty:
        return out
    filtered = out[
        (out["match_coverage_rate"] >= min_coverage_rate)
        & (out["usefulness_score"] > 0)
        & (out["noise_hits"].astype(str).str.len() < 40)
    ].copy()
    if filtered.empty:
        filtered = out[(out["usefulness_score"] > 0)].copy()
    filtered = filtered.sort_values(
        ["usefulness_score", "match_coverage_rate", "match_coverage_count"],
        ascending=[False, False, False],
    ).head(top_n)
    filtered["rank"] = range(1, len(filtered) + 1)
    cols = ["rank"] + [c for c in filtered.columns if c != "rank"]
    return filtered[cols]


def summarise_network(network: pd.DataFrame) -> list[dict[str, Any]]:
    if network.empty or "url" not in network.columns:
        return []
    rows: list[dict[str, Any]] = []
    candidates = network.copy()
    candidates["url_text"] = candidates["url"].astype(str)
    candidates["score"] = candidates["url_text"].str.lower().apply(lambda u: keyword_score(u)[0])
    candidates = candidates[candidates["score"] > 0].sort_values("score", ascending=False).head(40)
    for _, row in candidates.iterrows():
        rows.append({
            "score": int(row.get("score", 0)),
            "resource_type": txt(row.get("resource_type")),
            "status": txt(row.get("status")),
            "content_type": txt(row.get("content_type")),
            "url": txt(row.get("url")),
        })
    return rows


def main() -> int:
    args = build_parser().parse_args()
    markets_path = Path(args.markets_csv)
    network_path = Path(args.network_csv)
    out_csv = Path(args.out_csv)
    out_json = Path(args.out_json)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)

    markets = read_csv(markets_path)
    network = read_csv(network_path)
    ranked = summarise_markets(markets, args.min_coverage_rate, args.top_n)
    ranked.to_csv(out_csv, index=False)

    family_counts = {}
    top_by_family: dict[str, list[dict[str, Any]]] = {}
    if not ranked.empty:
        family_counts = ranked["feature_family"].value_counts().to_dict()
        for family, group in ranked.groupby("feature_family"):
            top_by_family[family] = group.head(10).to_dict(orient="records")

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_market_row_count": int(len(markets)),
        "ranked_market_path_count": int(len(ranked)),
        "min_coverage_rate": args.min_coverage_rate,
        "top_n": args.top_n,
        "feature_family_counts": family_counts,
        "top_ranked_market_paths": ranked.head(30).to_dict(orient="records") if not ranked.empty else [],
        "top_ranked_by_family": top_by_family,
        "high_value_network_candidates": summarise_network(network),
        "outputs": {
            "out_csv": str(out_csv),
            "out_json": str(out_json),
        },
        "note": "This ranks candidate Oddspedia/smartbet grid paths only. It does not yet promote these paths into production model features.",
    }
    out_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
