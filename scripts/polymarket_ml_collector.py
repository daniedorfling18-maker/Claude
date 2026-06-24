#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except Exception:
        return pd.DataFrame()


def append_rows(path: Path, rows: list[dict]) -> None:
    if not rows:
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0

    fieldnames = sorted({k for row in rows for k in row.keys()})

    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerows(rows)


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def source_sig(src: Path) -> str:
    return f"{src.stat().st_mtime_ns}:{src.stat().st_size}"


def append_full_snapshot_if_changed(src: Path, dest: Path, state: dict, key: str) -> int:
    """
    For files that represent a fresh current-state snapshot each cycle:
    - market_snapshot.csv
    - long_short_intents.csv

    We intentionally append the full file when changed because the full table is the observation.
    """
    if not src.exists() or src.stat().st_size == 0:
        return 0

    sig = source_sig(src)
    if state.get(f"{key}_sig") == sig:
        return 0

    df = read_csv(src)
    state[f"{key}_sig"] = sig

    if df.empty:
        return 0

    df = df.copy()
    df.insert(0, "collector_observed_at_utc", utc_now())
    df.insert(1, "collector_source_file", src.name)
    df.insert(2, "collector_source_mtime_ns", src.stat().st_mtime_ns)

    append_rows(dest, df.astype(object).where(pd.notnull(df), "").to_dict("records"))
    return len(df)


def append_delta_rows_if_grown(src: Path, dest: Path, state: dict, key: str) -> int:
    """
    For cumulative files:
    - mm_quote_evaluations.csv
    - mm_quote_summary.csv

    On first run, prime the row counter and append nothing. After that, append only new rows.
    This prevents repeatedly copying the entire cumulative file into raw_ml outputs.
    """
    if not src.exists() or src.stat().st_size == 0:
        return 0

    df = read_csv(src)
    current_rows = len(df)

    if df.empty:
        state[f"{key}_rows_seen"] = 0
        state[f"{key}_sig"] = source_sig(src)
        return 0

    previous_rows = state.get(f"{key}_rows_seen")

    # First run after archive/restart: start collecting from this point forward.
    if previous_rows is None:
        state[f"{key}_rows_seen"] = current_rows
        state[f"{key}_sig"] = source_sig(src)
        return 0

    # If source file was reset/truncated, treat current rows as the new baseline and append them once.
    if current_rows < int(previous_rows):
        new_df = df.copy()
    elif current_rows > int(previous_rows):
        new_df = df.iloc[int(previous_rows):].copy()
    else:
        state[f"{key}_sig"] = source_sig(src)
        return 0

    state[f"{key}_rows_seen"] = current_rows
    state[f"{key}_sig"] = source_sig(src)

    if new_df.empty:
        return 0

    new_df.insert(0, "collector_observed_at_utc", utc_now())
    new_df.insert(1, "collector_source_file", src.name)
    new_df.insert(2, "collector_source_mtime_ns", src.stat().st_mtime_ns)
    new_df.insert(3, "collector_source_row_count_at_read", current_rows)

    append_rows(dest, new_df.astype(object).where(pd.notnull(new_df), "").to_dict("records"))
    return len(new_df)


def build_latest_joined_snapshot(out_dir: Path, ml_dir: Path) -> int:
    snapshot = read_csv(out_dir / "market_snapshot.csv")
    intents = read_csv(out_dir / "long_short_intents.csv")
    evals = read_csv(out_dir / "mm_quote_evaluations.csv")

    if snapshot.empty:
        return 0

    joined = snapshot.copy()
    joined.insert(0, "joined_built_at_utc", utc_now())

    if "token_id" in joined.columns:
        joined["token_id"] = joined["token_id"].astype(str)

    if not intents.empty and "token_id" in intents.columns:
        intents = intents.copy()
        intents["token_id"] = intents["token_id"].astype(str)

        if "mode" in intents.columns:
            intents = intents[intents["mode"].astype(str) == "MARKET_MAKE"].copy()

        joined = joined.merge(
            intents.drop_duplicates("token_id", keep="last"),
            on="token_id",
            how="left",
            suffixes=("_snapshot", "_intent"),
        )

    if not evals.empty and "token_id" in evals.columns:
        evals = evals.copy()
        evals["token_id"] = evals["token_id"].astype(str)

        joined = joined.merge(
            evals.drop_duplicates("token_id", keep="last"),
            on="token_id",
            how="left",
            suffixes=("", "_eval"),
        )

    ml_dir.mkdir(parents=True, exist_ok=True)
    joined.to_csv(ml_dir / "latest_joined_snapshot.csv", index=False)
    return len(joined)


def main() -> int:
    out_dir = Path(os.getenv("POLYMARKET_OUTPUT_DIR", "outputs/polymarket"))
    ml_dir = out_dir / "ml"
    poll = int(os.getenv("POLYMARKET_ML_POLL_SECONDS", "30"))

    state_path = ml_dir / "collector_state.json"
    state = load_state(state_path)

    print(f"polymarket data collector: watching {out_dir} (poll {poll}s)", flush=True)
    print("polymarket data collector: DATA COLLECTION ONLY - no training, no scoring, no orders", flush=True)
    print("polymarket data collector: cumulative evaluator files are collected as row deltas only", flush=True)

    while True:
        try:
            n_snap = append_full_snapshot_if_changed(
                out_dir / "market_snapshot.csv",
                ml_dir / "raw_market_snapshots.csv",
                state,
                "market_snapshot",
            )

            n_int = append_full_snapshot_if_changed(
                out_dir / "long_short_intents.csv",
                ml_dir / "raw_long_short_intents.csv",
                state,
                "long_short_intents",
            )

            n_eval = append_delta_rows_if_grown(
                out_dir / "mm_quote_evaluations.csv",
                ml_dir / "raw_mm_quote_evaluations.csv",
                state,
                "mm_quote_evaluations",
            )

            n_summary = append_delta_rows_if_grown(
                out_dir / "mm_quote_summary.csv",
                ml_dir / "raw_mm_quote_summary.csv",
                state,
                "mm_quote_summary",
            )

            joined_rows = build_latest_joined_snapshot(out_dir, ml_dir)

            append_rows(
                ml_dir / "collector_heartbeat.csv",
                [
                    {
                        "heartbeat_at_utc": utc_now(),
                        "snapshot_rows_appended": n_snap,
                        "intent_rows_appended": n_int,
                        "evaluation_rows_appended": n_eval,
                        "summary_rows_appended": n_summary,
                        "latest_joined_snapshot_rows": joined_rows,
                    }
                ],
            )

            save_state(state_path, state)

            print(
                f"polymarket data collector: appended snapshot={n_snap} intents={n_int} evals_delta={n_eval} summaries_delta={n_summary}; joined={joined_rows}",
                flush=True,
            )

        except Exception as exc:
            print(f"polymarket data collector error continuing: {exc}", flush=True)

        time.sleep(poll)


if __name__ == "__main__":
    raise SystemExit(main())