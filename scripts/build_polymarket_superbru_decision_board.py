from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

import pandas as pd


OUTPUT_COLUMNS = [
    "commence_time",
    "match_id",
    "home_team",
    "away_team",
    "recommended_scoreline",
    "recommended_scoreline_outcome",
    "expected_points",
    "p_exact",
    "private_chase_scoreline",
    "private_chase_ev_loss",
    "selection",
    "selection_label",
    "selection_outcome",
    "superbru_alignment",
    "yes_price",
    "market_implied_probability",
    "breakeven_probability_after_fee",
    "model_probability",
    "bookmaker_no_vig_probability_median",
    "model_edge_vs_polymarket_breakeven",
    "model_edge_vs_bookmaker_median",
    "expected_pnl_model_usdc",
    "model_to_market_ratio",
    "model_to_bookmaker_ratio",
    "bookmaker_cross_check_class",
    "sanity_flags",
    "manual_review_required",
    "review_reason",
    "decision_status",
    "decision_reason",
    "paper_stake_units",
    "paper_stake_usdc_at_10_usdc_unit",
    "price_source",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a manual Polymarket/Superbru decision board. This merges the existing Superbru scoreline "
            "model outputs with Polymarket candidate edges and bookmaker cross-checks. It does not place trades."
        )
    )
    parser.add_argument("--predictions-csv", default="outputs/latest/predictions_upcoming.csv")
    parser.add_argument("--cross-check-csv", default="outputs/polymarket_available_markets/bookmaker_cross_check.csv")
    parser.add_argument("--out-csv", default="outputs/polymarket_available_markets/polymarket_superbru_decision_board.csv")
    parser.add_argument("--tickets-csv", default="outputs/polymarket_available_markets/manual_trade_tickets.csv")
    parser.add_argument("--min-polymarket-edge", type=float, default=0.0)
    parser.add_argument("--min-bookmaker-edge", type=float, default=0.01)
    parser.add_argument("--max-model-to-market-ratio", type=float, default=1.5)
    parser.add_argument("--max-paper-stake-units", type=float, default=1.0)
    parser.add_argument("--paper-unit-usdc", type=float, default=10.0)
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
        if not text:
            return default
        return float(text)
    except Exception:
        return default


def bool_value(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def scoreline_outcome(scoreline: Any) -> str:
    text = txt(scoreline)
    if "-" not in text:
        return ""
    left, right = text.split("-", 1)
    if not left.strip().isdigit() or not right.strip().isdigit():
        return ""
    home = int(left.strip())
    away = int(right.strip())
    if home > away:
        return "home_win"
    if home < away:
        return "away_win"
    return "draw"


def selection_outcome(selection: Any) -> str:
    text = txt(selection)
    if text in {"home_win", "draw", "away_win"}:
        return text
    label = txt(selection).lower()
    if "draw" in label:
        return "draw"
    return ""


def merge_inputs(predictions: pd.DataFrame, cross_check: pd.DataFrame) -> pd.DataFrame:
    for frame in (predictions, cross_check):
        if "match_id" not in frame.columns:
            frame["match_id"] = ""

    merged = cross_check.merge(
        predictions[
            [
                col
                for col in [
                    "match_id",
                    "home_team",
                    "away_team",
                    "recommended_scoreline",
                    "expected_points",
                    "p_exact",
                    "private_chase_scoreline",
                    "private_chase_ev_loss",
                ]
                if col in predictions.columns
            ]
        ],
        on=["match_id", "home_team", "away_team"],
        how="left",
        suffixes=("", "_prediction"),
    )
    return merged.fillna("")


def paper_stake_units(row: pd.Series, args: argparse.Namespace) -> float:
    """Conservative paper stake sizing for review tickets only.

    This is not an order size and should not be wired to execution. It intentionally caps exposure.
    """

    edge = fnum(row.get("model_edge_vs_polymarket_breakeven"), 0.0)
    ratio = fnum(row.get("model_to_market_ratio"), 0.0)
    confirmed = txt(row.get("bookmaker_cross_check_class")) == "model_vs_both"
    aligned = txt(row.get("superbru_alignment")) == "aligned_with_superbru_pick"

    if edge <= 0 or ratio <= 1.0:
        return 0.0

    units = min(float(args.max_paper_stake_units), max(0.0, edge * 10.0))
    if not confirmed:
        units *= 0.35
    if not aligned:
        units *= 0.50
    return round(units, 4)


def decide(row: pd.Series, args: argparse.Namespace) -> tuple[str, str]:
    reasons: list[str] = []

    if txt(row.get("price_source")) != "clob_best_ask":
        reasons.append("not_clob_best_ask")
    if bool_value(row.get("manual_review_required"), False):
        reasons.append("cross_check_manual_review")
    if txt(row.get("bookmaker_cross_check_class")) != "model_vs_both":
        reasons.append("not_confirmed_vs_both")
    if fnum(row.get("model_edge_vs_polymarket_breakeven"), 0.0) <= float(args.min_polymarket_edge):
        reasons.append("no_polymarket_edge_after_fee")
    if fnum(row.get("model_edge_vs_bookmaker_median"), -999.0) <= float(args.min_bookmaker_edge):
        reasons.append("insufficient_bookmaker_confirmation")
    if fnum(row.get("model_to_market_ratio"), 999.0) > float(args.max_model_to_market_ratio):
        reasons.append("model_to_market_ratio_too_high")
    if "model_probability_gt_2x_market_implied" in txt(row.get("sanity_flags")):
        reasons.append("extreme_market_mismatch")

    if reasons:
        return "REVIEW_ONLY", "|".join(reasons)
    return "MANUAL_TICKET_CANDIDATE", "model_vs_both|fee_adjusted_edge|manual_approval_required"


def build_board(args: argparse.Namespace) -> pd.DataFrame:
    predictions_path = Path(args.predictions_csv)
    cross_check_path = Path(args.cross_check_csv)
    if not predictions_path.exists():
        raise FileNotFoundError(f"Predictions CSV not found: {predictions_path}")
    if not cross_check_path.exists():
        raise FileNotFoundError(f"Cross-check CSV not found: {cross_check_path}")

    predictions = pd.read_csv(predictions_path).fillna("")
    cross_check = pd.read_csv(cross_check_path).fillna("")
    board = merge_inputs(predictions, cross_check)

    board["recommended_scoreline_outcome"] = board["recommended_scoreline"].map(scoreline_outcome)
    board["selection_outcome"] = board["selection"].map(selection_outcome)
    board["superbru_alignment"] = board.apply(
        lambda row: "aligned_with_superbru_pick"
        if txt(row.get("recommended_scoreline_outcome")) and txt(row.get("recommended_scoreline_outcome")) == txt(row.get("selection_outcome"))
        else "not_aligned_with_superbru_pick",
        axis=1,
    )

    decisions = board.apply(lambda row: decide(row, args), axis=1)
    board["decision_status"] = [item[0] for item in decisions]
    board["decision_reason"] = [item[1] for item in decisions]
    board["paper_stake_units"] = board.apply(lambda row: paper_stake_units(row, args), axis=1)
    board["paper_stake_usdc_at_10_usdc_unit"] = (board["paper_stake_units"].astype(float) * float(args.paper_unit_usdc)).round(2)

    sort_columns = ["decision_status", "expected_pnl_model_usdc", "model_edge_vs_bookmaker_median"]
    status_order = {"MANUAL_TICKET_CANDIDATE": 0, "REVIEW_ONLY": 1}
    board["_status_order"] = board["decision_status"].map(status_order).fillna(9)
    board = board.sort_values(["_status_order", "expected_pnl_model_usdc"], ascending=[True, False])
    return board[[col for col in OUTPUT_COLUMNS if col in board.columns]]


def build_tickets(board: pd.DataFrame) -> pd.DataFrame:
    tickets = board[board["decision_status"] == "MANUAL_TICKET_CANDIDATE"].copy()
    if tickets.empty:
        return pd.DataFrame(columns=["ticket_id", *[col for col in OUTPUT_COLUMNS if col in board.columns]])
    tickets.insert(0, "ticket_id", [f"PM-{idx + 1:03d}" for idx in range(len(tickets))])
    tickets["execution_status"] = "MANUAL_APPROVAL_REQUIRED"
    tickets["execution_instruction"] = "Review market, liquidity, legality, and account limits manually. No automated order execution is performed."
    return tickets


def main() -> int:
    args = build_parser().parse_args()
    board = build_board(args)

    out_path = Path(args.out_csv)
    tickets_path = Path(args.tickets_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tickets_path.parent.mkdir(parents=True, exist_ok=True)

    board.to_csv(out_path, index=False)
    tickets = build_tickets(board)
    tickets.to_csv(tickets_path, index=False)

    print(f"Wrote {out_path}")
    print(f"Wrote {tickets_path}")
    print(f"Rows: {len(board)}")
    print(f"Manual ticket candidates: {len(tickets)}")
    if not board.empty:
        preview_cols = [
            "home_team",
            "away_team",
            "recommended_scoreline",
            "selection_label",
            "superbru_alignment",
            "model_probability",
            "bookmaker_no_vig_probability_median",
            "expected_pnl_model_usdc",
            "bookmaker_cross_check_class",
            "decision_status",
            "paper_stake_units",
        ]
        print(board[[col for col in preview_cols if col in board.columns]].head(25).to_string(index=False))
    print("No trades are placed. This is a manual decision board and paper ticket file only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
