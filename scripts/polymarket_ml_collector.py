#!/usr/bin/env python3
"""Continuous Polymarket data collector and lightweight ML scorer.

This sidecar is intentionally read-only with respect to trading. It watches the
existing Docker outputs, appends raw data for today's run, builds quote-level
features, and writes model/scoring diagnostics until the container is stopped.

Outputs:
- outputs/polymarket/ml/raw_market_snapshots.csv
- outputs/polymarket/ml/raw_long_short_intents.csv
- outputs/polymarket/ml/raw_mm_evaluations.csv
- outputs/polymarket/ml/latest_features.csv
- outputs/polymarket/ml/latest_quote_candidates.csv
- outputs/polymarket/ml/model_diagnostics.csv
"""
from __future__ import annotations

import csv
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def f(value, default=np.nan):
    try:
        if value in {None, ""}:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


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


def write_df(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def load_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def append_if_changed(src: Path, dest: Path, state: dict, key: str) -> int:
    if not src.exists() or src.stat().st_size == 0:
        return 0
    sig = f"{src.stat().st_mtime_ns}:{src.stat().st_size}"
    if state.get(key) == sig:
        return 0
    df = read_csv(src)
    if df.empty:
        state[key] = sig
        return 0
    observed = utc_now()
    df = df.copy()
    df.insert(0, "ml_observed_at_utc", observed)
    df.insert(1, "ml_source_file", src.name)
    df.insert(2, "ml_source_mtime_ns", src.stat().st_mtime_ns)
    append_rows(dest, df.astype(object).where(pd.notnull(df), "").to_dict("records"))
    state[key] = sig
    return len(df)


def sigmoid(x: np.ndarray | float) -> np.ndarray | float:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30, 30)))


def train_logistic_sgd(x: np.ndarray, y: np.ndarray, *, epochs: int = 800, lr: float = 0.05, l2: float = 0.01) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mu = np.nanmean(x, axis=0)
    sd = np.nanstd(x, axis=0)
    sd[sd == 0] = 1.0
    xs = np.nan_to_num((x - mu) / sd, nan=0.0, posinf=0.0, neginf=0.0)
    xb = np.c_[np.ones(len(xs)), xs]
    w = np.zeros(xb.shape[1])
    for _ in range(epochs):
        p = sigmoid(xb @ w)
        grad = (xb.T @ (p - y)) / max(1, len(y))
        grad[1:] += l2 * w[1:]
        w -= lr * grad
    return w, mu, sd


def predict_logistic(x: np.ndarray, w: np.ndarray, mu: np.ndarray, sd: np.ndarray) -> np.ndarray:
    xs = np.nan_to_num((x - mu) / sd, nan=0.0, posinf=0.0, neginf=0.0)
    xb = np.c_[np.ones(len(xs)), xs]
    return sigmoid(xb @ w)


def latest_by_token(snapshot: pd.DataFrame) -> pd.DataFrame:
    if snapshot.empty or "token_id" not in snapshot.columns:
        return pd.DataFrame()
    return snapshot.dropna(subset=["token_id"]).drop_duplicates("token_id", keep="last")


def build_features(out_dir: Path, edge_buffer: float) -> pd.DataFrame:
    snapshot = latest_by_token(read_csv(out_dir / "market_snapshot.csv"))
    intents = read_csv(out_dir / "long_short_intents.csv")
    if snapshot.empty or intents.empty:
        return pd.DataFrame()

    intents = intents[intents.get("mode", "") == "MARKET_MAKE"].copy()
    if intents.empty:
        return pd.DataFrame()

    for df in (snapshot, intents):
        if "token_id" in df.columns:
            df["token_id"] = df["token_id"].astype(str)

    merged = intents.merge(snapshot, on="token_id", how="left", suffixes=("_intent", "_book"))

    def col(name: str, fallback: str | None = None):
        if name in merged.columns:
            return merged[name]
        if fallback and fallback in merged.columns:
            return merged[fallback]
        return np.nan

    bid = pd.to_numeric(col("best_bid", "bid_book"), errors="coerce")
    ask = pd.to_numeric(col("best_ask", "ask_book"), errors="coerce")
    fair = pd.to_numeric(col("fair_probability", "model_prob"), errors="coerce")
    tick = pd.to_numeric(col("tick_size_book", "tick_size_intent"), errors="coerce").fillna(0.01)
    old_order = pd.to_numeric(col("order_price"), errors="coerce")
    spread = ask - bid
    mid = (bid + ask) / 2.0

    candidate_bid = np.minimum(ask - tick, fair - edge_buffer)
    candidate_bid = np.maximum(candidate_bid, bid + tick)
    feasible = (candidate_bid > bid) & (candidate_bid < ask) & (candidate_bid <= fair - edge_buffer) & (spread > tick)
    candidate_bid = np.where(feasible, candidate_bid, np.nan)

    features = pd.DataFrame({
        "scored_at_utc": utc_now(),
        "token_id": merged["token_id"],
        "question": col("question", "match"),
        "outcome": col("outcome_intent", "outcome"),
        "fair": fair,
        "best_bid": bid,
        "best_ask": ask,
        "mid": mid,
        "spread": spread,
        "tick_size": tick,
        "old_order_price": old_order,
        "old_distance_from_bid": old_order - bid,
        "old_distance_to_ask": ask - old_order,
        "old_edge_to_fair": fair - old_order,
        "candidate_bid": candidate_bid,
        "candidate_distance_from_bid": candidate_bid - bid,
        "candidate_distance_to_ask": ask - candidate_bid,
        "candidate_edge_to_fair": fair - candidate_bid,
        "candidate_feasible": feasible.fillna(False),
    })
    return features


def labelled_training_data(raw_eval_path: Path) -> tuple[pd.DataFrame, pd.Series]:
    ev = read_csv(raw_eval_path)
    if ev.empty:
        return pd.DataFrame(), pd.Series(dtype=float)
    required = ["order_price", "current_bid", "current_ask", "current_mid", "quote_model_prob", "quote_bid", "quote_ask", "touch_fill_proxy"]
    for c in required:
        if c not in ev.columns:
            return pd.DataFrame(), pd.Series(dtype=float)
    x = pd.DataFrame({
        "order_price": pd.to_numeric(ev["order_price"], errors="coerce"),
        "current_bid": pd.to_numeric(ev["current_bid"], errors="coerce"),
        "current_ask": pd.to_numeric(ev["current_ask"], errors="coerce"),
        "current_mid": pd.to_numeric(ev["current_mid"], errors="coerce"),
        "quote_model_prob": pd.to_numeric(ev["quote_model_prob"], errors="coerce"),
        "quote_bid": pd.to_numeric(ev["quote_bid"], errors="coerce"),
        "quote_ask": pd.to_numeric(ev["quote_ask"], errors="coerce"),
    })
    x["spread"] = x["current_ask"] - x["current_bid"]
    x["distance_to_ask"] = x["current_ask"] - x["order_price"]
    x["edge_to_fair"] = x["quote_model_prob"] - x["order_price"]
    y = ev["touch_fill_proxy"].astype(str).str.lower().isin(["true", "1", "yes"]).astype(float)
    keep = x.notna().all(axis=1)
    return x[keep], y[keep]


def score_candidates(features: pd.DataFrame, raw_eval_path: Path, edge_buffer: float) -> tuple[pd.DataFrame, dict]:
    diagnostics = {"scored_at_utc": utc_now(), "rows_scored": len(features), "model_trained": False, "reason": ""}
    if features.empty:
        diagnostics["reason"] = "no features"
        return features, diagnostics

    x_train, y_train = labelled_training_data(raw_eval_path)
    positives = int(y_train.sum()) if len(y_train) else 0
    negatives = int(len(y_train) - positives) if len(y_train) else 0
    diagnostics.update({"training_rows": int(len(y_train)), "positive_touches": positives, "negative_not_touched": negatives})

    score = None
    feature_cols = ["candidate_bid", "best_bid", "best_ask", "mid", "fair", "spread", "candidate_distance_to_ask", "candidate_edge_to_fair"]

    if len(y_train) >= 50 and positives >= 5 and negatives >= 5:
        train_cols = ["order_price", "current_bid", "current_ask", "current_mid", "quote_model_prob", "spread", "distance_to_ask", "edge_to_fair"]
        w, mu, sd = train_logistic_sgd(x_train[train_cols].to_numpy(dtype=float), y_train.to_numpy(dtype=float))
        x_now = pd.DataFrame({
            "order_price": features["candidate_bid"],
            "current_bid": features["best_bid"],
            "current_ask": features["best_ask"],
            "current_mid": features["mid"],
            "quote_model_prob": features["fair"],
            "spread": features["spread"],
            "distance_to_ask": features["candidate_distance_to_ask"],
            "edge_to_fair": features["candidate_edge_to_fair"],
        })
        score = predict_logistic(x_now[train_cols].to_numpy(dtype=float), w, mu, sd)
        diagnostics["model_trained"] = True
        diagnostics["reason"] = "logistic_sgd"
    else:
        # Heuristic fallback until we observe actual touches. It favours quotes close to ask
        # while retaining positive edge to model fair.
        dist = pd.to_numeric(features["candidate_distance_to_ask"], errors="coerce").fillna(1.0)
        edge = pd.to_numeric(features["candidate_edge_to_fair"], errors="coerce").fillna(-1.0)
        spread = pd.to_numeric(features["spread"], errors="coerce").fillna(0.0)
        score = sigmoid((-dist / 0.01) + (edge / max(edge_buffer, 0.005)) + (spread / 0.02))
        diagnostics["reason"] = "heuristic_until_positive_touches"

    out = features.copy()
    out["ml_touch_score"] = score
    out["ml_edge_score"] = pd.to_numeric(out["candidate_edge_to_fair"], errors="coerce").fillna(-1.0)
    out["ml_rank_score"] = out["ml_touch_score"] * np.maximum(out["ml_edge_score"], 0.0)
    out = out[out["candidate_feasible"] == True].sort_values("ml_rank_score", ascending=False)
    return out, diagnostics


def main() -> int:
    out_dir = Path(os.getenv("POLYMARKET_OUTPUT_DIR", "outputs/polymarket"))
    ml_dir = out_dir / "ml"
    poll = int(os.getenv("POLYMARKET_ML_POLL_SECONDS", "30"))
    edge_buffer = float(os.getenv("POLYMARKET_ML_MM_EDGE_BUFFER", "0.01"))
    state_path = ml_dir / "collector_state.json"
    state = load_state(state_path)

    print(f"polymarket ML collector: watching {out_dir} (poll {poll}s, edge_buffer={edge_buffer})", flush=True)
    print("polymarket ML collector: data collection/scoring only; no orders are placed", flush=True)

    while True:
        try:
            n_snap = append_if_changed(out_dir / "market_snapshot.csv", ml_dir / "raw_market_snapshots.csv", state, "snapshot")
            n_int = append_if_changed(out_dir / "long_short_intents.csv", ml_dir / "raw_long_short_intents.csv", state, "intents")
            n_eval = append_if_changed(out_dir / "mm_quote_evaluations.csv", ml_dir / "raw_mm_evaluations.csv", state, "evals")

            features = build_features(out_dir, edge_buffer)
            if not features.empty:
                write_df(ml_dir / "latest_features.csv", features)
                candidates, diagnostics = score_candidates(features, ml_dir / "raw_mm_evaluations.csv", edge_buffer)
                write_df(ml_dir / "latest_quote_candidates.csv", candidates)
                append_rows(ml_dir / "model_diagnostics.csv", [diagnostics])
                print(
                    f"polymarket ML collector: appended snapshot={n_snap} intents={n_int} evals={n_eval}; "
                    f"features={len(features)} candidates={len(candidates)} trained={diagnostics.get('model_trained')} reason={diagnostics.get('reason')}",
                    flush=True,
                )
            else:
                print(f"polymarket ML collector: waiting for snapshot/intents; appended snapshot={n_snap} intents={n_int} evals={n_eval}", flush=True)

            save_state(state_path, state)
        except Exception as exc:
            print(f"polymarket ML collector error continuing: {exc}", flush=True)
        time.sleep(poll)


if __name__ == "__main__":
    raise SystemExit(main())
