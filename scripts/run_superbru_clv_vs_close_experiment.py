from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


TEAM_ALIASES = {
    "usa": "unitedstates",
    "us": "unitedstates",
    "u s a": "unitedstates",
    "unitedstatesofamerica": "unitedstates",
    "cotedivoire": "ivorycoast",
    "côtedivoire": "ivorycoast",
    "drc": "drcongo",
    "democraticrepublicofcongo": "drcongo",
    "bosniaandherzegovina": "bosniaherzegovina",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a CLV-vs-close audit for SuperBru picks. If the repo does not yet "
            "contain two usable odds snapshots, the script emits a decisive data-readiness "
            "verdict and a pick-time market-edge proxy instead."
        )
    )
    parser.add_argument("--locked-picks-csv", default="outputs/final_locked_picks/final_picks_locked.csv")
    parser.add_argument("--upload-card-csv", default="outputs/final_locked_picks/superbru_final_card.csv")
    parser.add_argument("--predictions-csv", default="outputs/latest/predictions.csv")
    parser.add_argument("--market-odds-flat-csv", default="outputs/market_odds/worldcup_market_odds_flat.csv")
    parser.add_argument("--market-history-csv", default="outputs/market_odds_history/market_odds_history.csv")
    parser.add_argument("--smartbet-grid-dir", default="inputs/smartbet_grids")
    parser.add_argument("--out-dir", default="outputs/superbru_clv_experiment")
    parser.add_argument(
        "--close-window-hours",
        type=float,
        default=6.0,
        help="Latest market snapshot must be within this many hours before kickoff to count as close.",
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
        if not text:
            return default
        return float(text)
    except Exception:
        return default


def parse_dt(value: Any) -> pd.Timestamp | None:
    text = txt(value)
    if not text:
        return None
    try:
        stamp = pd.to_datetime(text, utc=True, errors="coerce")
    except Exception:
        return None
    if pd.isna(stamp):
        return None
    return stamp


def norm_team(value: Any) -> str:
    text = txt(value).lower().replace("&", "and")
    text = re.sub(r"[^a-z0-9]+", "", text)
    return TEAM_ALIASES.get(text, text)


def match_key(home: Any, away: Any, commence_time: Any = "") -> str:
    kick = txt(commence_time)
    return f"{norm_team(home)}::{norm_team(away)}::{kick}"


def team_pair_key(home: Any, away: Any) -> str:
    return f"{norm_team(home)}::{norm_team(away)}"


def parse_score(value: Any) -> tuple[int, int] | None:
    match = re.search(r"^\s*(\d+)\s*-\s*(\d+)\s*$", txt(value))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def score_outcome(value: Any) -> str:
    parsed = parse_score(value)
    if parsed is None:
        return ""
    home_goals, away_goals = parsed
    if home_goals > away_goals:
        return "home"
    if away_goals > home_goals:
        return "away"
    return "draw"


def implied_probability(price: Any) -> float:
    decimal_price = fnum(price)
    if math.isnan(decimal_price) or decimal_price <= 1.0:
        return float("nan")
    return 1.0 / decimal_price


def round_or_none(value: Any, digits: int = 6) -> float | None:
    number = fnum(value)
    if math.isnan(number):
        return None
    return round(number, digits)


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_csv(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    return pd.read_csv(p).fillna("")


def pick_column(frame: pd.DataFrame) -> str:
    for column in ("locked_pick", "final_pick", "recommended_scoreline", "pick", "scoreline"):
        if column in frame.columns:
            return column
    raise ValueError("Could not find a scoreline pick column.")


def load_pick_sources(locked_path: Path, upload_path: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for source_name, path in (
        ("locked_engine_card", locked_path),
        ("superbru_upload_card", upload_path),
    ):
        frame = read_csv(path)
        if frame.empty:
            continue
        column = pick_column(frame)
        out = pd.DataFrame(
            {
                "pick_source": source_name,
                "pick_file": str(path),
                "commence_time": frame.get("commence_time", ""),
                "home_team": frame.get("home_team", ""),
                "away_team": frame.get("away_team", ""),
                "locked_pick": frame[column],
                "pick_column": column,
            }
        )
        frames.append(out)
    if not frames:
        return pd.DataFrame()
    picks = pd.concat(frames, ignore_index=True).fillna("")
    picks["_match_key"] = picks.apply(
        lambda row: match_key(row.get("home_team"), row.get("away_team"), row.get("commence_time")),
        axis=1,
    )
    picks["_team_pair_key"] = picks.apply(lambda row: team_pair_key(row.get("home_team"), row.get("away_team")), axis=1)
    picks["selected_outcome"] = picks["locked_pick"].map(score_outcome)
    return picks


def build_prediction_lookup(predictions: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if predictions.empty:
        return {}
    predictions = predictions.copy()
    predictions["_match_key"] = predictions.apply(
        lambda row: match_key(row.get("home_team"), row.get("away_team"), row.get("commence_time")),
        axis=1,
    )
    return {txt(row.get("_match_key")): row.to_dict() for _, row in predictions.iterrows()}


def selected_prediction_probability(prediction: dict[str, Any] | None, outcome: str, prefix: str) -> float:
    if not prediction or outcome not in {"home", "draw", "away"}:
        return float("nan")
    if outcome == "draw":
        return fnum(prediction.get(f"{prefix}_draw"))
    return fnum(prediction.get(f"{prefix}_{outcome}_win"))


def h2h_consensus_for_event(event_odds: pd.DataFrame, home_team: str, away_team: str) -> dict[str, Any]:
    h2h = event_odds[event_odds["market"].astype(str).str.lower().eq("h2h")].copy()
    if h2h.empty:
        return {"market_available": False}

    home_key = norm_team(home_team)
    away_key = norm_team(away_team)
    book_probs: list[dict[str, float]] = []

    for _, group in h2h.groupby("bookmaker", dropna=False):
        probs: dict[str, float | None] = {"home": None, "draw": None, "away": None}
        for _, row in group.iterrows():
            outcome_name_raw = txt(row.get("outcome_name"))
            outcome_name = norm_team(outcome_name_raw)
            probability = implied_probability(row.get("price"))
            if math.isnan(probability):
                continue
            if outcome_name == home_key:
                probs["home"] = probability
            elif outcome_name == away_key:
                probs["away"] = probability
            elif "draw" in outcome_name_raw.lower():
                probs["draw"] = probability

        if all(probs[key] is not None for key in ("home", "draw", "away")):
            total = float(probs["home"] or 0.0) + float(probs["draw"] or 0.0) + float(probs["away"] or 0.0)
            if total > 0:
                book_probs.append({key: float(probs[key] or 0.0) / total for key in ("home", "draw", "away")})

    if not book_probs:
        return {"market_available": False}

    avg = {key: sum(book[key] for book in book_probs) / len(book_probs) for key in ("home", "draw", "away")}
    favourite = max(avg, key=avg.get)
    updates = [parse_dt(value) for value in h2h.get("last_update", pd.Series(dtype=str)).tolist()]
    updates = [value for value in updates if value is not None]
    return {
        "market_available": True,
        "market_event_id": txt(h2h.iloc[0].get("event_id")),
        "market_home_team": txt(h2h.iloc[0].get("home_team")),
        "market_away_team": txt(h2h.iloc[0].get("away_team")),
        "h2h_bookmaker_count": len(book_probs),
        "market_p_home": avg["home"],
        "market_p_draw": avg["draw"],
        "market_p_away": avg["away"],
        "market_favourite": favourite,
        "market_favourite_prob": avg[favourite],
        "market_last_update_min": min(updates).isoformat() if updates else "",
        "market_last_update_max": max(updates).isoformat() if updates else "",
    }


def build_market_lookup(market_odds: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if market_odds.empty:
        return {}
    market_odds = market_odds.copy()
    market_odds["_match_key"] = market_odds.apply(
        lambda row: match_key(row.get("home_team"), row.get("away_team"), row.get("commence_time")),
        axis=1,
    )
    lookup: dict[str, dict[str, Any]] = {}
    for key, group in market_odds.groupby("_match_key"):
        first = group.iloc[0]
        lookup[txt(key)] = h2h_consensus_for_event(group, txt(first.get("home_team")), txt(first.get("away_team")))
    return lookup


def snapshot_gap_hours(commence_time: Any, snapshot_time: Any) -> float:
    kick = parse_dt(commence_time)
    snap = parse_dt(snapshot_time)
    if kick is None or snap is None:
        return float("nan")
    return (kick - snap).total_seconds() / 3600.0


def build_history_clv_lookup(history: pd.DataFrame, close_window_hours: float) -> dict[str, dict[str, Any]]:
    if history.empty or "snapshot_id" not in history.columns:
        return {}
    history = history.copy()
    history["_match_key"] = history.apply(
        lambda row: match_key(row.get("home_team"), row.get("away_team"), row.get("commence_time")),
        axis=1,
    )
    if "snapshot_created_at_utc" not in history.columns:
        history["snapshot_created_at_utc"] = ""
    history["_snapshot_time"] = history["snapshot_created_at_utc"].map(parse_dt)

    out: dict[str, dict[str, Any]] = {}
    for key, group in history.groupby("_match_key"):
        group = group.drop_duplicates(subset=["snapshot_id"]).copy()
        if len(group) < 2:
            continue
        group = group.sort_values(["_snapshot_time", "snapshot_id"], na_position="last")
        first = group.iloc[0]
        close = group.iloc[-1]
        outcome = txt(close.get("pred_outcome")) or score_outcome(close.get("final_pick"))
        if outcome not in {"home", "draw", "away"}:
            continue
        open_prob = fnum(first.get(f"market_p_{outcome}"))
        close_prob = fnum(close.get(f"market_p_{outcome}"))
        close_gap = snapshot_gap_hours(close.get("commence_time"), close.get("snapshot_created_at_utc"))
        if math.isnan(open_prob) or math.isnan(close_prob):
            continue
        out[txt(key)] = {
            "clv_open_snapshot_id": txt(first.get("snapshot_id")),
            "clv_close_snapshot_id": txt(close.get("snapshot_id")),
            "clv_open_selected_market_prob": open_prob,
            "clv_close_selected_market_prob": close_prob,
            "clv_selected_probability_move": close_prob - open_prob,
            "clv_close_hours_to_kickoff": close_gap,
            "clv_close_snapshot_usable": bool(0 <= close_gap <= close_window_hours),
        }
    return out


def smartbet_grid_overlap(picks: pd.DataFrame, grid_dir: Path) -> dict[str, Any]:
    if picks.empty or not grid_dir.exists():
        return {"files_checked": 0, "overlap_rows": 0, "overlap_pick_keys": []}

    pick_pairs = set(picks["_team_pair_key"].astype(str))
    files = [
        grid_dir / "oddspedia_probability_grids_auto.csv",
        grid_dir / "oddspedia_probability_grids_alt.csv",
        grid_dir / "converted_old_oddspedia_score_grids.csv",
        grid_dir / "old_master-20260616T1025_score_grids.csv",
    ]

    overlaps: set[str] = set()
    rows = 0
    checked = 0
    for path in files:
        if not path.exists():
            continue
        checked += 1
        frame = read_csv(path)
        if frame.empty:
            continue
        home_col = "home_team" if "home_team" in frame.columns else "home_name" if "home_name" in frame.columns else "home"
        away_col = "away_team" if "away_team" in frame.columns else "away_name" if "away_name" in frame.columns else "away"
        if home_col not in frame.columns or away_col not in frame.columns:
            continue
        frame["_team_pair_key"] = frame.apply(lambda row: team_pair_key(row.get(home_col), row.get(away_col)), axis=1)
        hit = frame[frame["_team_pair_key"].astype(str).isin(pick_pairs)]
        if not hit.empty:
            overlaps.update(hit["_team_pair_key"].astype(str).tolist())
            rows += len(hit)

    return {"files_checked": checked, "overlap_rows": rows, "overlap_pick_keys": sorted(overlaps)}


def build_rows(
    picks: pd.DataFrame,
    predictions: pd.DataFrame,
    market_odds: pd.DataFrame,
    history: pd.DataFrame,
    close_window_hours: float,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    prediction_lookup = build_prediction_lookup(predictions)
    market_lookup = build_market_lookup(market_odds)
    history_clv = build_history_clv_lookup(history, close_window_hours)

    rows: list[dict[str, Any]] = []
    for _, pick in picks.iterrows():
        key = txt(pick.get("_match_key"))
        prediction = prediction_lookup.get(key)
        market = market_lookup.get(key, {"market_available": False})
        outcome = txt(pick.get("selected_outcome"))
        selected_market_prob = fnum(market.get(f"market_p_{outcome}")) if market.get("market_available") else float("nan")
        model_prob = selected_prediction_probability(prediction, outcome, "model")
        fair_prob = selected_prediction_probability(prediction, outcome, "fair")
        market_update = txt(market.get("market_last_update_max"))
        hours_to_kickoff = snapshot_gap_hours(pick.get("commence_time"), market_update)
        clv = history_clv.get(key, {})

        row = {
            "analysis_match_key": key,
            "pick_source": txt(pick.get("pick_source")),
            "commence_time": txt(pick.get("commence_time")),
            "home_team": txt(pick.get("home_team")),
            "away_team": txt(pick.get("away_team")),
            "locked_pick": txt(pick.get("locked_pick")),
            "selected_outcome": outcome,
            "market_available": bool(market.get("market_available")),
            "market_event_id": txt(market.get("market_event_id")),
            "market_home_team": txt(market.get("market_home_team")),
            "market_away_team": txt(market.get("market_away_team")),
            "h2h_bookmaker_count": int(fnum(market.get("h2h_bookmaker_count"), 0.0)),
            "market_last_update_min_utc": txt(market.get("market_last_update_min")),
            "market_last_update_max_utc": market_update,
            "hours_market_snapshot_to_kickoff": round_or_none(hours_to_kickoff, 3),
            "selected_market_prob": round_or_none(selected_market_prob),
            "selected_market_fair_decimal": round_or_none(1.0 / selected_market_prob, 4)
            if not math.isnan(selected_market_prob) and selected_market_prob > 0
            else None,
            "selected_model_prob": round_or_none(model_prob),
            "selected_fair_prob": round_or_none(fair_prob),
            "model_edge_vs_market": round_or_none(model_prob - selected_market_prob)
            if not math.isnan(model_prob) and not math.isnan(selected_market_prob)
            else None,
            "fair_edge_vs_market": round_or_none(fair_prob - selected_market_prob)
            if not math.isnan(fair_prob) and not math.isnan(selected_market_prob)
            else None,
            "recommended_scoreline": txt(prediction.get("recommended_scoreline")) if prediction else "",
            "p_exact_for_recommended": round_or_none(prediction.get("p_exact")) if prediction else None,
            "p_close_for_recommended": round_or_none(prediction.get("p_close")) if prediction else None,
            "p_outcome_for_recommended": round_or_none(prediction.get("p_outcome")) if prediction else None,
            "prediction_available": bool(prediction),
            "clv_available": bool(clv and clv.get("clv_close_snapshot_usable")),
            "clv_open_snapshot_id": txt(clv.get("clv_open_snapshot_id")),
            "clv_close_snapshot_id": txt(clv.get("clv_close_snapshot_id")),
            "clv_open_selected_market_prob": round_or_none(clv.get("clv_open_selected_market_prob")),
            "clv_close_selected_market_prob": round_or_none(clv.get("clv_close_selected_market_prob")),
            "clv_selected_probability_move": round_or_none(clv.get("clv_selected_probability_move")),
            "clv_close_hours_to_kickoff": round_or_none(clv.get("clv_close_hours_to_kickoff"), 3),
        }
        if not row["market_available"]:
            row["clv_reason"] = "no bookmaker h2h market match in repo odds file"
        elif not clv:
            row["clv_reason"] = "fewer than two market snapshots for this pick"
        elif not clv.get("clv_close_snapshot_usable"):
            row["clv_reason"] = f"latest snapshot is not within {close_window_hours:g}h pre-kickoff close window"
        else:
            row["clv_reason"] = "usable"
        rows.append(row)

    rows_frame = pd.DataFrame(rows)
    return rows_frame, {
        "prediction_keys": len(prediction_lookup),
        "market_keys": len(market_lookup),
        "history_clv_keys": len(history_clv),
    }


def summarise(
    rows: pd.DataFrame,
    picks: pd.DataFrame,
    market_history: pd.DataFrame,
    smartbet_overlap: dict[str, Any],
    aux_counts: dict[str, Any],
    close_window_hours: float,
    out_dir: Path,
) -> dict[str, Any]:
    snapshot_ids = (
        sorted({txt(value) for value in market_history.get("snapshot_id", pd.Series(dtype=str)).tolist() if txt(value)})
        if not market_history.empty
        else []
    )
    snapshot_times = (
        sorted({txt(value) for value in market_history.get("snapshot_created_at_utc", pd.Series(dtype=str)).tolist() if txt(value)})
        if not market_history.empty
        else []
    )

    market_rows = rows[rows["market_available"].astype(bool)].copy() if not rows.empty else pd.DataFrame()
    proxy_rows = market_rows[
        market_rows["selected_market_prob"].notna() & market_rows["selected_model_prob"].notna()
    ].copy() if not market_rows.empty else pd.DataFrame()
    if not proxy_rows.empty and "analysis_match_key" in proxy_rows.columns:
        proxy_rows["_source_rank"] = proxy_rows["pick_source"].map({"locked_engine_card": 0, "superbru_upload_card": 1}).fillna(9)
        unique_proxy_rows = (
            proxy_rows.sort_values(["analysis_match_key", "_source_rank"])
            .drop_duplicates(subset=["analysis_match_key"], keep="first")
            .drop(columns=["_source_rank"])
        )
    else:
        unique_proxy_rows = proxy_rows.copy()
    clv_rows = rows[rows["clv_available"].astype(bool)].copy() if not rows.empty else pd.DataFrame()

    unmatched = rows[~rows["market_available"].astype(bool)][
        ["pick_source", "commence_time", "home_team", "away_team", "locked_pick", "clv_reason"]
    ].to_dict("records") if not rows.empty else []

    mean_model_edge = float(unique_proxy_rows["model_edge_vs_market"].mean()) if not unique_proxy_rows.empty else float("nan")
    median_model_edge = float(unique_proxy_rows["model_edge_vs_market"].median()) if not unique_proxy_rows.empty else float("nan")
    positive_model_edge = int((unique_proxy_rows["model_edge_vs_market"] > 0).sum()) if not unique_proxy_rows.empty else 0
    negative_model_edge = int((unique_proxy_rows["model_edge_vs_market"] < 0).sum()) if not unique_proxy_rows.empty else 0
    top_positive = (
        unique_proxy_rows.sort_values("model_edge_vs_market", ascending=False)
        .head(5)[
            [
                "pick_source",
                "home_team",
                "away_team",
                "locked_pick",
                "selected_outcome",
                "selected_market_prob",
                "selected_model_prob",
                "model_edge_vs_market",
                "hours_market_snapshot_to_kickoff",
            ]
        ]
        .to_dict("records")
        if not unique_proxy_rows.empty
        else []
    )
    top_negative = (
        unique_proxy_rows.sort_values("model_edge_vs_market", ascending=True)
        .head(5)[
            [
                "pick_source",
                "home_team",
                "away_team",
                "locked_pick",
                "selected_outcome",
                "selected_market_prob",
                "selected_model_prob",
                "model_edge_vs_market",
                "hours_market_snapshot_to_kickoff",
            ]
        ]
        .to_dict("records")
        if not unique_proxy_rows.empty
        else []
    )

    decisive_clv_available = bool(len(clv_rows) > 0)
    if decisive_clv_available:
        status = "clv_ready"
        verdict = "Usable CLV rows exist; inspect clv_selected_probability_move by pick and source."
    else:
        status = "insufficient_close_history"
        verdict = (
            "True CLV-vs-close is not computable from the current repo snapshot set: "
            "each usable SuperBru pick needs an earlier pick-time quote and a later near-close quote."
        )

    return {
        "generated_at_utc": iso_now(),
        "status": status,
        "decisive_clv_available": decisive_clv_available,
        "verdict": verdict,
        "close_window_hours": close_window_hours,
        "pick_rows_total": int(len(picks)),
        "pick_sources": {str(k): int(v) for k, v in picks["pick_source"].value_counts().to_dict().items()} if not picks.empty else {},
        "market_matched_rows": int(len(market_rows)),
        "market_matched_unique_rows": int(market_rows["analysis_match_key"].nunique()) if not market_rows.empty else 0,
        "market_unmatched_rows": int(len(unmatched)),
        "prediction_proxy_rows": int(len(proxy_rows)),
        "prediction_proxy_unique_rows": int(len(unique_proxy_rows)),
        "usable_clv_rows": int(len(clv_rows)),
        "market_history_rows": int(len(market_history)),
        "market_history_snapshot_count": int(len(snapshot_ids)),
        "market_history_snapshot_ids": snapshot_ids,
        "market_history_snapshot_times": snapshot_times,
        "smartbet_grid_overlap": smartbet_overlap,
        "aux_counts": aux_counts,
        "model_edge_proxy": {
            "description": "Selected outcome model probability minus de-vigged bookmaker h2h consensus probability at the available market snapshot.",
            "mean": round_or_none(mean_model_edge),
            "median": round_or_none(median_model_edge),
            "positive_count": positive_model_edge,
            "negative_count": negative_model_edge,
            "positive_rate": round_or_none(positive_model_edge / len(unique_proxy_rows)) if len(unique_proxy_rows) else None,
            "top_positive": top_positive,
            "top_negative": top_negative,
        },
        "unmatched_market_rows": unmatched,
        "outputs": {
            "rows_csv": str(out_dir / "clv_vs_close_rows.csv"),
            "summary_json": str(out_dir / "clv_vs_close_summary.json"),
            "report_md": str(out_dir / "clv_vs_close_report.md"),
        },
    }


def write_report(summary: dict[str, Any], rows: pd.DataFrame, path: Path) -> None:
    edge = summary.get("model_edge_proxy", {})
    lines = [
        "# SuperBru CLV-vs-close experiment",
        "",
        f"Generated: `{summary.get('generated_at_utc')}`",
        "",
        f"Verdict: **{summary.get('status')}**",
        "",
        txt(summary.get("verdict")),
        "",
        "## Data coverage",
        "",
        "| Check | Value |",
        "| --- | ---: |",
        f"| Pick rows | {summary.get('pick_rows_total')} |",
        f"| Market-matched rows | {summary.get('market_matched_rows')} |",
        f"| Market-matched unique matches | {summary.get('market_matched_unique_rows')} |",
        f"| Prediction proxy rows | {summary.get('prediction_proxy_rows')} |",
        f"| Prediction proxy unique matches | {summary.get('prediction_proxy_unique_rows')} |",
        f"| Market-history snapshots | {summary.get('market_history_snapshot_count')} |",
        f"| Usable CLV rows | {summary.get('usable_clv_rows')} |",
        f"| Smartbet/Oddspedia grid overlap rows | {summary.get('smartbet_grid_overlap', {}).get('overlap_rows', 0)} |",
        "",
        "## Pick-time market-edge proxy",
        "",
        "This is not CLV. It compares the selected outcome probability from the engine to the de-vigged bookmaker consensus available in the repo.",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Mean model edge vs market | {edge.get('mean')} |",
        f"| Median model edge vs market | {edge.get('median')} |",
        f"| Positive model-edge rows | {edge.get('positive_count')} |",
        f"| Negative model-edge rows | {edge.get('negative_count')} |",
        f"| Positive rate | {edge.get('positive_rate')} |",
        "",
        "## Strongest positive proxy rows",
        "",
        "| Pick source | Match | Pick | Outcome | Market p | Model p | Edge | Hours to kickoff |",
        "| --- | --- | ---: | --- | ---: | ---: | ---: | ---: |",
    ]

    for row in edge.get("top_positive", []):
        lines.append(
            "| {pick_source} | {home_team} vs {away_team} | {locked_pick} | {selected_outcome} | "
            "{selected_market_prob} | {selected_model_prob} | {model_edge_vs_market} | {hours_market_snapshot_to_kickoff} |".format(
                **row
            )
        )

    lines.extend(
        [
            "",
            "## Strongest negative proxy rows",
            "",
            "| Pick source | Match | Pick | Outcome | Market p | Model p | Edge | Hours to kickoff |",
            "| --- | --- | ---: | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in edge.get("top_negative", []):
        lines.append(
            "| {pick_source} | {home_team} vs {away_team} | {locked_pick} | {selected_outcome} | "
            "{selected_market_prob} | {selected_model_prob} | {model_edge_vs_market} | {hours_market_snapshot_to_kickoff} |".format(
                **row
            )
        )

    if not rows.empty:
        reason_counts = rows["clv_reason"].value_counts().to_dict()
        lines.extend(["", "## CLV blockers", "", "| Reason | Rows |", "| --- | ---: |"])
        for reason, count in reason_counts.items():
            lines.append(f"| {reason} | {int(count)} |")

    lines.extend(
        [
            "",
            "## Next instrumentation required",
            "",
            "- Save every SuperBru pick with a pick timestamp.",
            "- Save bookmaker h2h/totals/spread snapshots repeatedly until kickoff.",
            "- Mark the final pre-kickoff snapshot as close.",
            "- Promote a SuperBru signal only when forward CLV is positive for the same family, not just when the model likes the pick.",
        ]
    )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    picks = load_pick_sources(Path(args.locked_picks_csv), Path(args.upload_card_csv))
    predictions = read_csv(args.predictions_csv)
    market_odds = read_csv(args.market_odds_flat_csv)
    history = read_csv(args.market_history_csv)
    smartbet_overlap = smartbet_grid_overlap(picks, Path(args.smartbet_grid_dir))

    rows, aux_counts = build_rows(picks, predictions, market_odds, history, args.close_window_hours)
    summary = summarise(rows, picks, history, smartbet_overlap, aux_counts, args.close_window_hours, out_dir)

    rows_path = out_dir / "clv_vs_close_rows.csv"
    summary_path = out_dir / "clv_vs_close_summary.json"
    report_path = out_dir / "clv_vs_close_report.md"

    rows.to_csv(rows_path, index=False)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    write_report(summary, rows, report_path)

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
