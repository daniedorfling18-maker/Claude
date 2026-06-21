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
    parser.add_argument("--allow-card-only-fallback", action="store_true")
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
    required = {"commence_time", "home_team", "away_team", pick_col}
    missing = required - set(card.columns)
    if missing:
        raise ValueError(f"Locked card missing columns: {sorted(missing)}")
    predictions = pd.DataFrame(
        {
            "commence_time": card["commence_time"].map(txt),
            "home_team": card["home_team"].map(txt),
            "away_team": card["away_team"].map(txt),
            "recommended_scoreline": card[pick_col].map(txt),
        }
    )
    if "raw_pick" in card.columns:
        predictions["raw_ev_scoreline"] = card["raw_pick"].map(txt)
    else:
        predictions["raw_ev_scoreline"] = predictions["recommended_scoreline"].map(txt)
    predictions["locked_card_source"] = str(card_path)
    predictions["prediction_source"] = "locked_card_fallback"
    return predictions


def load_base_predictions(base_path: Path) -> tuple[pd.DataFrame | None, str]:
    required = {"home_team", "away_team", "recommended_scoreline"}
    if not base_path.exists():
        return None, "missing"
    try:
        predictions = pd.read_csv(base_path).fillna("")
    except Exception as exc:
        return None, f"unreadable:{exc}"
    missing = required - set(predictions.columns)
    if missing:
        return None, f"missing_columns:{','.join(sorted(missing))}"
    if predictions.empty:
        return None, "empty"
    return predictions, "base_predictions"


def main() -> int:
    args = build_parser().parse_args()
    base_path = Path(args.base_predictions_csv)
    card_path = Path(args.locked_card_csv)
    out_path = Path(args.out_csv)

    if not card_path.exists():
        raise FileNotFoundError(f"Missing locked card CSV: {card_path}")

    card = pd.read_csv(card_path).fillna("")
    pick_col = find_pick_column(card)
    predictions, source = load_base_predictions(base_path)

    if predictions is None:
        if not args.allow_card_only_fallback:
            raise FileNotFoundError(f"Missing usable base predictions CSV: {base_path} ({source})")
        print(f"Using locked-card fallback because base predictions are {source}.")
        predictions = synthesize_predictions_from_card(card, pick_col, card_path)
        source = "locked_card_fallback"

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
