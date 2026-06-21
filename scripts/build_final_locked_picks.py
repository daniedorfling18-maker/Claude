from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


RESCORE_COLUMNS = [
    "home_team",
    "away_team",
    "current_pick",
    "candidate_pick",
    "recommendation",
    "current_component_score",
    "candidate_component_score",
    "component_improvement",
    "ev_loss",
    "delta_p_finish_first",
    "stress_pass_rate",
    "passes_hard_rule",
    "decision_reasons",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Apply approved market/component switch candidates and write the final locked Superbru picks."
    )
    parser.add_argument("--final-picks-csv", default="outputs/final_leader_decision_round_summary_profiles/final_picks.csv")
    parser.add_argument("--rescore-csv", default="outputs/component_validation/review_rescore_report.csv")
    parser.add_argument("--out-dir", default="outputs/final_locked_picks")
    parser.add_argument("--max-ev-loss", type=float, default=0.01)
    parser.add_argument("--min-delta-p-finish-first", type=float, default=-0.001)
    parser.add_argument("--min-candidate-component-score", type=float, default=0.65)
    parser.add_argument("--min-component-improvement", type=float, default=0.05)
    parser.add_argument(
        "--require-hard-rule",
        action="store_true",
        help="Only apply candidates where passes_hard_rule is True. Default is False because this script applies the market/component overlay rule.",
    )
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def fnum(value: Any, default: float = float("nan")) -> float:
    try:
        text = txt(value).replace(",", ".")
        if text == "":
            return default
        return float(text)
    except Exception:
        return default


def bval(value: Any) -> bool:
    return txt(value).lower() in {"true", "1", "yes", "y"}


def norm_team(value: Any) -> str:
    return "".join(ch.lower() for ch in txt(value) if ch.isalnum())


def match_key(home: Any, away: Any) -> tuple[str, str]:
    return norm_team(home), norm_team(away)


def load_csv(path: str | Path, label: str, *, allow_empty: bool = False) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        if allow_empty:
            return pd.DataFrame(columns=RESCORE_COLUMNS)
        raise FileNotFoundError(f"Missing {label}: {p}")
    try:
        return pd.read_csv(p).fillna("")
    except pd.errors.EmptyDataError:
        if allow_empty:
            return pd.DataFrame(columns=RESCORE_COLUMNS)
        raise


def pick_column(frame: pd.DataFrame) -> str:
    for column in ["final_pick", "locked_pick", "recommended_scoreline", "raw_pick", "pick"]:
        if column in frame.columns:
            return column
    raise ValueError(
        "Could not find a pick column in final picks CSV. Expected one of: "
        "final_pick, locked_pick, recommended_scoreline, raw_pick, pick."
    )


def approved_switches(args: argparse.Namespace, rescore: pd.DataFrame) -> pd.DataFrame:
    required = {"home_team", "away_team", "current_pick", "candidate_pick", "recommendation"}
    if rescore.empty:
        return pd.DataFrame(columns=list(required))
    missing = required - set(rescore.columns)
    if missing:
        raise ValueError(f"Rescore CSV is missing required columns: {sorted(missing)}")

    approved = rescore[rescore["recommendation"].astype(str).str.lower().eq("switch_candidate")].copy()
    if approved.empty:
        return approved

    approved["_ev_loss"] = approved["ev_loss"].map(lambda x: fnum(x, 999.0)) if "ev_loss" in approved.columns else 999.0
    approved["_delta"] = approved["delta_p_finish_first"].map(lambda x: fnum(x, -999.0)) if "delta_p_finish_first" in approved.columns else -999.0
    approved["_component"] = approved["candidate_component_score"].map(lambda x: fnum(x, 0.0)) if "candidate_component_score" in approved.columns else 0.0
    approved["_improvement"] = approved["component_improvement"].map(lambda x: fnum(x, 0.0)) if "component_improvement" in approved.columns else 0.0

    mask = (
        approved["_ev_loss"].le(args.max_ev_loss)
        & approved["_delta"].ge(args.min_delta_p_finish_first)
        & approved["_component"].ge(args.min_candidate_component_score)
        & approved["_improvement"].ge(args.min_component_improvement)
    )
    if args.require_hard_rule:
        mask = mask & approved.get("passes_hard_rule", pd.Series([False] * len(approved))).map(bval)

    approved = approved[mask].copy()
    approved["_key"] = approved.apply(lambda row: match_key(row.get("home_team"), row.get("away_team")), axis=1)
    approved = approved.sort_values(["_improvement", "_ev_loss"], ascending=[False, True])
    approved = approved.drop_duplicates("_key", keep="first")
    return approved


def apply_switches(final_picks: pd.DataFrame, switches: pd.DataFrame, pick_col: str) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    locked = final_picks.copy()
    locked["original_pick"] = locked[pick_col].map(txt)
    locked["locked_pick"] = locked[pick_col].map(txt)
    locked["switch_applied"] = False
    locked["switch_reason"] = ""

    warnings: list[str] = []
    applied_rows: list[dict[str, Any]] = []

    switch_lookup = {
        match_key(row.get("home_team"), row.get("away_team")): row
        for _, row in switches.iterrows()
    }

    for idx, row in locked.iterrows():
        key = match_key(row.get("home_team"), row.get("away_team"))
        if key not in switch_lookup:
            continue

        switch = switch_lookup[key]
        current_pick = txt(switch.get("current_pick"))
        current_final = txt(row.get(pick_col))
        candidate_pick = txt(switch.get("candidate_pick"))

        if current_pick and current_final and current_pick != current_final:
            warnings.append(
                f"Skipped {row.get('home_team')} vs {row.get('away_team')}: "
                f"rescore current_pick={current_pick}, final_picks={current_final}."
            )
            continue

        reason = txt(switch.get("decision_reasons")) or "market/component overlay switch"
        locked.at[idx, "locked_pick"] = candidate_pick
        locked.at[idx, "switch_applied"] = True
        locked.at[idx, "switch_reason"] = reason

        applied_rows.append(
            {
                "home_team": txt(row.get("home_team")),
                "away_team": txt(row.get("away_team")),
                "original_pick": current_final,
                "locked_pick": candidate_pick,
                "ev_loss": switch.get("ev_loss", ""),
                "delta_p_finish_first": switch.get("delta_p_finish_first", ""),
                "current_component_score": switch.get("current_component_score", ""),
                "candidate_component_score": switch.get("candidate_component_score", ""),
                "component_improvement": switch.get("component_improvement", ""),
                "decision_reasons": reason,
            }
        )

    return locked, pd.DataFrame(applied_rows), warnings


def write_outputs(out_dir: Path, locked: pd.DataFrame, applied: pd.DataFrame, warnings: list[str], switches: pd.DataFrame) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    locked_path = out_dir / "final_picks_locked.csv"
    superbru_path = out_dir / "superbru_final_card.csv"
    switches_path = out_dir / "applied_switches.csv"
    summary_path = out_dir / "final_picks_locked_summary.json"

    locked.to_csv(locked_path, index=False)

    card_cols = [col for col in ["commence_time", "home_team", "away_team", "locked_pick"] if col in locked.columns]
    card = locked[card_cols].copy()
    if "commence_time" in card.columns:
        card = card.sort_values("commence_time")
    card.to_csv(superbru_path, index=False)

    applied.to_csv(switches_path, index=False)

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "final_pick_count": int(len(locked)),
        "approved_switch_candidates": int(len(switches)),
        "applied_switch_count": int(len(applied)),
        "applied_switches": applied.to_dict(orient="records"),
        "warnings": warnings,
        "outputs": {
            "locked_picks": str(locked_path),
            "superbru_final_card": str(superbru_path),
            "applied_switches": str(switches_path),
        },
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))
    print(f"Wrote {locked_path}")
    print(f"Wrote {superbru_path}")
    print(f"Wrote {switches_path}")
    print(f"Wrote {summary_path}")


def main() -> int:
    args = build_parser().parse_args()
    final_picks = load_csv(args.final_picks_csv, "final picks CSV")
    rescore = load_csv(args.rescore_csv, "rescore CSV", allow_empty=True)
    pick_col = pick_column(final_picks)
    switches = approved_switches(args, rescore)
    locked, applied, warnings = apply_switches(final_picks, switches, pick_col)
    write_outputs(Path(args.out_dir), locked, applied, warnings, switches)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
