from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a predictions CSV for leader simulation from a locked Superbru card."
    )
    parser.add_argument("--base-predictions-csv", default="outputs/latest/predictions.csv")
    parser.add_argument("--locked-card-csv", default="outputs/daily_robust_card/daily_robust_superbru_card.csv")
    parser.add_argument("--out-csv", default="outputs/daily_robust_card/predictions_for_final_simulation.csv")
    parser.add_argument(
        "--allow-card-only-fallback",
        action="store_true",
        help=(
            "If the base predictions CSV is missing, synthesise the minimal leader-simulation "
            "predictions CSV directly from the locked card instead of failing."
        ),
    )
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def norm_team(value: Any) -> str:
    return "".join(ch.lower() for ch in txt(value) if ch.isalnum())


def match_key(home: Any, away: Any) -> tuple[str, str]:
    return norm_team(home), norm_team(away)


def find_pick_column(frame: pd.DataFrame) -> str:
    for col in ["robust_locked_pick", "locked_pick", "final_pick", "recommended_scoreline", "pick"]:
        if col in frame.columns:
            return col
    raise ValueError("Could not find a locked pick column in locked-card CSV.")


def synthesize_predictions_from_card(card: pd.DataFrame, pick_col: str, card_path: Path) -> pd.DataFrame:
    required_card = {"commence_time", "home_team", "away_team", pick_col}
    missing = required_card - set(card.columns)
    if missing:
        raise ValueError(f"Locked card missing columns for card-only fallback: {sorted(missing)}")

    predictions = pd.DataFrame(
        {
            "commence_time": card["commence_time"].map(txt),
            "home_team": card["home_team"].map(txt),
            "away_team": card["away_team"].map(txt),
            "recommended_scoreline": card[pick_col].map(txt),
        }
    )

    if "match_id" in card.columns:
        predictions.insert(0, "match_id", card["match_id"].map(txt))

    for column in [
        "risk_tier",
        "confidence_tier",
        "raw_pick",
        "top1_scoreline",
        "top2_scoreline",
        "top3_scoreline",
        "modal_scoreline",
        "private_chase_scoreline",
        "conservative_scoreline",
        "exact_chase_scoreline",
    ]:
        if column in card.columns and column not in predictions.columns:
            predictions[column] = card[column].map(txt)

    if "raw_ev_scoreline" not in predictions.columns:
        source_col = "raw_pick" if "raw_pick" in predictions.columns else "recommended_scoreline"
        predictions["raw_ev_scoreline"] = predictions[source_col].map(txt)

    predictions["locked_card_source"] = str(card_path)
    predictions["prediction_source"] = "locked_card_fallback"
    return predictions


def main() -> int:
    args = build_parser().parse_args()
    base_path = Path(args.base_predictions_csv)
    card_path = Path(args.locked_card_csv)
    out_path = Path(args.out_csv)

    if not card_path.exists():
        raise FileNotFoundError(f"Missing locked card CSV: {card_path}")

    card = pd.read_csv(card_path).fillna("")
    pick_col = find_pick_column(card)

    if base_path.exists():
        predictions = pd.read_csv(base_path).fillna("")
        source = "base_predictions"
    elif args.allow_card_only_fallback:
        predictions = synthesize_predictions_from_card(card, pick_col, card_path)
        source = "locked_card_fallback"
    else:
        raise FileNotFoundError(
            f"Missing base predictions CSV: {base_path}. Pass --allow-card-only-fallback to build a minimal "
            "leader-simulation predictions file from the locked card."
        )

    required_predictions = {"commence_time", "home_team", "away_team", "recommended_scoreline"}
    if not required_predictions.issubset(predictions.columns):
        raise ValueError(f"Base predictions missing columns: {sorted(required_predictions - set(predictions.columns))}")

    locked_lookup = {
        match_key(row.get("home_team"), row.get("away_team")): txt(row.get(pick_col))
        for _, row in card.iterrows()
    }

    changed = 0
    unmatched: list[dict[str, str]] = []
    for idx, row in predictions.iterrows():
        key = match_key(row.get("home_team"), row.get("away_team"))
        if key not in locked_lookup:
            unmatched.append({"home_team": txt(row.get("home_team")), "away_team": txt(row.get("away_team"))})
            continue
        new_pick = locked_lookup[key]
        if new_pick and txt(row.get("recommended_scoreline")) != new_pick:
            predictions.at[idx, "recommended_scoreline"] = new_pick
            changed += 1

    predictions["locked_card_source"] = str(card_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    predictions.to_csv(out_path, index=False)

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "base_predictions_csv": str(base_path),
        "locked_card_csv": str(card_path),
        "out_csv": str(out_path),
        "source": source,
        "prediction_rows": int(len(predictions)),
        "locked_card_rows": int(len(card)),
        "changed_prediction_count": int(changed),
        "unmatched_prediction_count": int(len(unmatched)),
        "unmatched_predictions": unmatched[:20],
    }
    print(json.dumps(summary, indent=2))
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
