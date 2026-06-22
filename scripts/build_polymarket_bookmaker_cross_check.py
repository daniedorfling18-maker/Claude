from __future__ import annotations

import argparse
import importlib.util
import math
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if SRC.exists() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from superbru_score_engine.ingest.offline import OfflineJsonProvider
from superbru_score_engine.model.team_names import canonical_team_key


OUTPUT_COLUMNS = [
    "commence_time",
    "match_id",
    "home_team",
    "away_team",
    "selection",
    "selection_label",
    "yes_price",
    "market_implied_probability",
    "breakeven_probability_after_fee",
    "model_probability",
    "bookmaker_no_vig_probability_median",
    "bookmaker_no_vig_probability_mean",
    "bookmaker_no_vig_probability_min",
    "bookmaker_no_vig_probability_max",
    "bookmaker_count",
    "best_bookmaker_decimal_odds",
    "best_bookmaker",
    "model_edge_vs_polymarket_breakeven",
    "model_edge_vs_bookmaker_median",
    "polymarket_edge_vs_bookmaker_median",
    "model_to_market_ratio",
    "model_to_bookmaker_ratio",
    "expected_pnl_model_usdc",
    "sanity_flags",
    "bookmaker_cross_check_class",
    "manual_review_required",
    "review_reason",
    "price_source",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Cross-check Polymarket candidate edges against independent bookmaker 1X2/H2H probabilities. "
            "This is a manual review report, not a trade execution tool."
        )
    )
    parser.add_argument(
        "--candidates-csv",
        default="outputs/polymarket_available_markets/strict_clean_candidate_edges.csv",
        help="Candidate Polymarket edge CSV from the available-market simulator workflow.",
    )
    parser.add_argument("--odds-json", default="data/odds_snapshot_real.json")
    parser.add_argument("--fixtures", default="data/fixtures_real.csv")
    parser.add_argument("--out-csv", default="outputs/polymarket_available_markets/bookmaker_cross_check.csv")
    parser.add_argument("--min-bookmaker-edge", type=float, default=0.01)
    parser.add_argument("--min-polymarket-edge", type=float, default=0.0)
    parser.add_argument("--max-model-to-market-ratio", type=float, default=2.0)
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def fnum(value: Any, default: float = float("nan")) -> float:
    try:
        text = txt(value)
        if text == "":
            return default
        return float(text)
    except Exception:
        return default


def load_mc_module():
    path = ROOT / "scripts" / "run_leaderboard_monte_carlo.py"
    if not path.exists():
        raise FileNotFoundError("scripts/run_leaderboard_monte_carlo.py not found")
    spec = importlib.util.spec_from_file_location("leaderboard_mc", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_matches(args: argparse.Namespace) -> list[Any]:
    mc = load_mc_module()
    matches = OfflineJsonProvider(args.odds_json).fetch_odds()
    return mc.merge_fixture_metadata(matches, args.fixtures)


def row_key(row: pd.Series | dict[str, Any]) -> tuple[str, str, str]:
    return (
        txt(row.get("match_id")),
        canonical_team_key(txt(row.get("home_team"))),
        canonical_team_key(txt(row.get("away_team"))),
    )


def match_key(match: Any) -> tuple[str, str, str]:
    return (
        txt(match.match_id),
        canonical_team_key(txt(match.home_team)),
        canonical_team_key(txt(match.away_team)),
    )


def match_lookup(matches: list[Any]) -> tuple[dict[tuple[str, str, str], Any], dict[tuple[str, str], Any]]:
    by_full_key = {match_key(match): match for match in matches}
    by_pair = {(key[1], key[2]): match for key, match in by_full_key.items()}
    return by_full_key, by_pair


def find_match(row: pd.Series, by_full_key: dict[tuple[str, str, str], Any], by_pair: dict[tuple[str, str], Any]) -> Any | None:
    key = row_key(row)
    if key in by_full_key:
        return by_full_key[key]
    return by_pair.get((key[1], key[2]))


def selection_outcome_name(row: pd.Series, match: Any) -> str:
    selection = txt(row.get("selection"))
    if selection == "home_win":
        return txt(row.get("home_team")) or txt(match.home_team)
    if selection == "away_win":
        return txt(row.get("away_team")) or txt(match.away_team)
    if selection == "draw":
        return "Draw"
    # Fall back to selection label, useful for futures if later extended.
    return txt(row.get("selection_label")) or selection


def outcome_matches(outcome_name: str, target: str) -> bool:
    outcome_key = canonical_team_key(outcome_name)
    target_key = canonical_team_key(target)
    if target_key == "draw":
        return outcome_key in {"draw", "tie"}
    return outcome_key == target_key


def bookmaker_probabilities(match: Any, target_outcome: str) -> list[dict[str, Any]]:
    books: list[dict[str, Any]] = []
    for market in match.market("h2h"):
        outcomes = list(market.outcomes)
        if len(outcomes) < 2:
            continue
        implied = []
        for outcome in outcomes:
            if outcome.price <= 1.0:
                continue
            implied.append((outcome, 1.0 / float(outcome.price)))
        total_implied = sum(value for _, value in implied)
        if total_implied <= 0:
            continue

        for outcome, raw_probability in implied:
            if not outcome_matches(outcome.name, target_outcome):
                continue
            books.append(
                {
                    "bookmaker": market.bookmaker,
                    "decimal_odds": float(outcome.price),
                    "raw_implied_probability": raw_probability,
                    "no_vig_probability": raw_probability / total_implied,
                    "last_update": market.last_update or "",
                    "outcome_name": outcome.name,
                }
            )
    return books


def summarise_books(books: list[dict[str, Any]]) -> dict[str, Any]:
    if not books:
        return {
            "bookmaker_no_vig_probability_median": float("nan"),
            "bookmaker_no_vig_probability_mean": float("nan"),
            "bookmaker_no_vig_probability_min": float("nan"),
            "bookmaker_no_vig_probability_max": float("nan"),
            "bookmaker_count": 0,
            "best_bookmaker_decimal_odds": float("nan"),
            "best_bookmaker": "",
        }

    frame = pd.DataFrame(books)
    best = frame.sort_values("decimal_odds", ascending=False).iloc[0]
    return {
        "bookmaker_no_vig_probability_median": round(float(frame["no_vig_probability"].median()), 8),
        "bookmaker_no_vig_probability_mean": round(float(frame["no_vig_probability"].mean()), 8),
        "bookmaker_no_vig_probability_min": round(float(frame["no_vig_probability"].min()), 8),
        "bookmaker_no_vig_probability_max": round(float(frame["no_vig_probability"].max()), 8),
        "bookmaker_count": int(len(frame)),
        "best_bookmaker_decimal_odds": round(float(best["decimal_odds"]), 6),
        "best_bookmaker": txt(best["bookmaker"]),
    }


def cross_check_class(model_edge_vs_polymarket: float, model_edge_vs_bookmaker: float, bookmaker_count: int, args: argparse.Namespace) -> str:
    if bookmaker_count <= 0 or math.isnan(model_edge_vs_bookmaker):
        return "no_bookmaker_data"
    beats_polymarket = model_edge_vs_polymarket > float(args.min_polymarket_edge)
    beats_bookmakers = model_edge_vs_bookmaker > float(args.min_bookmaker_edge)
    if beats_polymarket and beats_bookmakers:
        return "model_vs_both"
    if beats_polymarket:
        return "model_vs_polymarket_only"
    if beats_bookmakers:
        return "model_vs_bookmakers_only"
    return "no_confirmed_edge"


def review_reason(row: pd.Series, result: dict[str, Any], args: argparse.Namespace) -> tuple[bool, str]:
    reasons: list[str] = []
    if int(result.get("bookmaker_count", 0)) <= 0:
        reasons.append("no_bookmaker_data")
    if fnum(row.get("model_to_market_ratio"), 0.0) > float(args.max_model_to_market_ratio):
        reasons.append("model_to_market_ratio_above_limit")
    if "model_probability_gt_2x_market_implied" in txt(row.get("sanity_flags")):
        reasons.append("sanity_flag_gt_2x_market")
    if result.get("bookmaker_cross_check_class") not in {"model_vs_both"}:
        reasons.append("not_confirmed_by_both_markets")
    return bool(reasons), "|".join(reasons)


def build_report(args: argparse.Namespace) -> pd.DataFrame:
    candidates_path = ROOT / args.candidates_csv
    if not candidates_path.exists():
        raise FileNotFoundError(f"Candidate CSV not found: {candidates_path}")
    candidates = pd.read_csv(candidates_path).fillna("")
    matches = load_matches(args)
    by_full_key, by_pair = match_lookup(matches)

    rows: list[dict[str, Any]] = []
    for _, candidate in candidates.iterrows():
        match = find_match(candidate, by_full_key, by_pair)
        target_outcome = selection_outcome_name(candidate, match) if match is not None else txt(candidate.get("selection_label"))
        books = bookmaker_probabilities(match, target_outcome) if match is not None else []
        book_summary = summarise_books(books)

        model_probability = fnum(candidate.get("model_probability"))
        polymarket_breakeven = fnum(candidate.get("breakeven_probability_after_fee"))
        polymarket_implied = fnum(candidate.get("market_implied_probability"), fnum(candidate.get("yes_price")))
        bookmaker_median = fnum(book_summary.get("bookmaker_no_vig_probability_median"))

        model_edge_vs_polymarket = model_probability - polymarket_breakeven
        model_edge_vs_bookmaker = model_probability - bookmaker_median if not math.isnan(bookmaker_median) else float("nan")
        polymarket_edge_vs_bookmaker = bookmaker_median - polymarket_implied if not math.isnan(bookmaker_median) else float("nan")
        model_to_bookmaker_ratio = model_probability / bookmaker_median if bookmaker_median and not math.isnan(bookmaker_median) else float("nan")

        result = {
            "commence_time": txt(candidate.get("commence_time")),
            "match_id": txt(candidate.get("match_id")),
            "home_team": txt(candidate.get("home_team")),
            "away_team": txt(candidate.get("away_team")),
            "selection": txt(candidate.get("selection")),
            "selection_label": txt(candidate.get("selection_label")),
            "yes_price": round(fnum(candidate.get("yes_price")), 6),
            "market_implied_probability": round(polymarket_implied, 8),
            "breakeven_probability_after_fee": round(polymarket_breakeven, 8),
            "model_probability": round(model_probability, 8),
            **book_summary,
            "model_edge_vs_polymarket_breakeven": round(model_edge_vs_polymarket, 8),
            "model_edge_vs_bookmaker_median": round(model_edge_vs_bookmaker, 8) if not math.isnan(model_edge_vs_bookmaker) else "",
            "polymarket_edge_vs_bookmaker_median": round(polymarket_edge_vs_bookmaker, 8) if not math.isnan(polymarket_edge_vs_bookmaker) else "",
            "model_to_market_ratio": round(fnum(candidate.get("model_to_market_ratio")), 6),
            "model_to_bookmaker_ratio": round(model_to_bookmaker_ratio, 6) if not math.isnan(model_to_bookmaker_ratio) else "",
            "expected_pnl_model_usdc": round(fnum(candidate.get("expected_pnl_model_usdc")), 6),
            "sanity_flags": txt(candidate.get("sanity_flags")),
            "price_source": txt(candidate.get("price_source")),
        }
        result["bookmaker_cross_check_class"] = cross_check_class(
            model_edge_vs_polymarket,
            model_edge_vs_bookmaker,
            int(book_summary["bookmaker_count"]),
            args,
        )
        manual_review_required, reason = review_reason(candidate, result, args)
        result["manual_review_required"] = manual_review_required
        result["review_reason"] = reason
        rows.append(result)

    report = pd.DataFrame(rows)
    if report.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    sort_cols = ["bookmaker_cross_check_class", "manual_review_required", "expected_pnl_model_usdc"]
    class_order = {
        "model_vs_both": 0,
        "model_vs_polymarket_only": 1,
        "model_vs_bookmakers_only": 2,
        "no_confirmed_edge": 3,
        "no_bookmaker_data": 4,
    }
    report["_class_order"] = report["bookmaker_cross_check_class"].map(class_order).fillna(9)
    report = report.sort_values(["_class_order", "manual_review_required", "expected_pnl_model_usdc"], ascending=[True, True, False])
    return report[[col for col in OUTPUT_COLUMNS if col in report.columns]]


def main() -> int:
    args = build_parser().parse_args()
    out_path = ROOT / args.out_csv
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report = build_report(args)
    report.to_csv(out_path, index=False)

    print(f"Wrote {out_path}")
    print(f"Rows: {len(report)}")
    if not report.empty:
        counts = report["bookmaker_cross_check_class"].value_counts().to_dict()
        print("Class counts:", counts)
        preview_cols = [
            "home_team",
            "away_team",
            "selection_label",
            "model_probability",
            "market_implied_probability",
            "bookmaker_no_vig_probability_median",
            "model_edge_vs_polymarket_breakeven",
            "model_edge_vs_bookmaker_median",
            "expected_pnl_model_usdc",
            "bookmaker_cross_check_class",
            "manual_review_required",
        ]
        print(report[preview_cols].head(25).to_string(index=False))
    print("This report is for manual review only. It does not place trades or manage account funding.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
