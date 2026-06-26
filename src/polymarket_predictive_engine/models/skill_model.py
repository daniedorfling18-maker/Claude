"""Multivariate skill model: does adding point-in-time features to the market
price improve resolution forecasts *out of sample, versus the market itself*?

The market (the order-book midpoint / implied probability) is a strong forecaster;
the only honest test of model value is whether a model that *starts from* the market
price and adds features (price momentum, volatility, time-to-close, and order-book
microstructure when present) beats the raw market price on held-out markets.

Design choices that keep the test honest:
  - Baseline = the market price itself (``implied_probability``/``midpoint``), not uniform.
  - L2-regularised logistic regression on leakage-screened numeric features
    (``numeric_model_feature_columns``), always including the market logit.
  - Temporal split **by market**: the earliest markets train, the latest test, and no
    market's snapshots straddle the split (prevents within-market leakage).
  - Skill = 1 - model_brier / market_brier, with a paired, market-clustered bootstrap
    CI so "beats the market" means statistically, not by luck.
  - Skill is also reported on the uncertain region (market prob in [0.15, 0.85]) where
    forecasting is hard and edge, if any, must show up.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..config import EngineConfig, load_config
from ..utils import now_utc, read_csv_rows, safe_float, write_csv, write_json
from .calibration_v2 import joined_feature_label_rows, numeric_model_feature_columns

MODEL_VERSION = "pm-skill-logit-v1"
MARKET_PROB_FIELDS = ("implied_probability", "midpoint")


def _market_probability(row: dict[str, Any]) -> float | None:
    for field in MARKET_PROB_FIELDS:
        value = safe_float(row.get(field))
        if value is not None:
            return min(1.0, max(0.0, value))
    return None


def _logit(p: float) -> float:
    p = min(1 - 1e-6, max(1e-6, p))
    return math.log(p / (1 - p))


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def select_feature_columns(rows: list[dict[str, Any]]) -> list[str]:
    """Numeric, leakage-screened features, excluding raw market-price duplicates
    (the market logit is added separately as the anchor)."""
    # logit_midpoint is the market anchor added separately; the raw price columns are
    # the same signal in other units. Drop them so features mean "beyond the market".
    drop = {
        "implied_probability",
        "midpoint",
        "logit_midpoint",
        "predicted_probability",
        "target",
        "best_bid",
        "best_ask",
        "executable_buy_price",
        "executable_price",
        "last_trade_price",
        "price_change_price",
    }
    return [c for c in numeric_model_feature_columns(rows) if c.lower() not in drop]


def build_design(rows: list[dict[str, Any]], feature_columns: list[str]) -> list[dict[str, Any]]:
    """Attach the model design vector to each joined feature/label row.

    Vector = [1, market_logit, *features]; rows without a usable market price are dropped.
    """
    design: list[dict[str, Any]] = []
    for row in rows:
        market_p = _market_probability(row)
        if market_p is None:
            continue
        feats = [safe_float(row.get(col)) for col in feature_columns]
        feats = [0.0 if v is None else v for v in feats]
        design.append({
            **row,
            "_market_p": market_p,
            "_x": [1.0, _logit(market_p), *feats],
            "_y": int(row["target"]),
        })
    return design


def _thin_by_token(design: list[dict[str, Any]], max_per_token: int) -> list[dict[str, Any]]:
    """Cap snapshots per (market, token) to ``max_per_token``, evenly spaced in time.

    Dense intraday snapshots are highly autocorrelated; thinning keeps the design
    tractable and the effective sample honest without dropping any market."""
    if max_per_token <= 0:
        return design
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in design:
        by_key.setdefault((str(row.get("market_id")), str(row.get("token_id"))), []).append(row)
    out: list[dict[str, Any]] = []
    for rows in by_key.values():
        rows.sort(key=lambda r: str(r.get("prediction_timestamp", "")))
        if len(rows) <= max_per_token:
            out.extend(rows)
        else:
            idx = sorted(set(np.linspace(0, len(rows) - 1, max_per_token).round().astype(int).tolist()))
            out.extend(rows[i] for i in idx)
    return out


def _temporal_market_split(rows: list[dict[str, Any]], test_fraction: float) -> tuple[list[int], list[int]]:
    """Split row indices so the latest ``test_fraction`` of markets (by earliest
    snapshot time) are the test set and no market spans both sides."""
    first_seen: dict[str, str] = {}
    for row in rows:
        key = str(row.get("market_id") or row.get("market_slug") or "")
        ts = str(row.get("prediction_timestamp") or "")
        if key not in first_seen or ts < first_seen[key]:
            first_seen[key] = ts
    ordered = [m for m, _ in sorted(first_seen.items(), key=lambda kv: (kv[1], kv[0]))]
    if len(ordered) < 2:
        return list(range(len(rows))), []
    cut = max(1, int(round(len(ordered) * (1 - test_fraction))))
    train_markets = set(ordered[:cut])
    train_idx, test_idx = [], []
    for i, row in enumerate(rows):
        key = str(row.get("market_id") or row.get("market_slug") or "")
        (train_idx if key in train_markets else test_idx).append(i)
    return train_idx, test_idx


def _standardize(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Z-score every column except the intercept (column 0)."""
    mean = matrix.mean(axis=0)
    std = matrix.std(axis=0, ddof=1)
    mean[0], std[0] = 0.0, 1.0
    std = np.where(std < 1e-12, 1.0, std)
    out = (matrix - mean) / std
    out[:, 0] = 1.0
    return out, mean, std


def _fit_logistic(matrix: np.ndarray, y: np.ndarray, l2: float, iters: int = 100) -> np.ndarray:
    """L2-regularised logistic regression via Newton/IRLS (vectorised, ridge off the intercept)."""
    n, d = matrix.shape
    w = np.zeros(d)
    reg = np.eye(d) * l2
    reg[0, 0] = 0.0
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-np.clip(matrix @ w, -30, 30)))
        weights = np.clip(p * (1 - p), 1e-6, None)
        grad = matrix.T @ (p - y) + reg @ w
        hess = (matrix.T * weights) @ matrix + reg
        try:
            step = np.linalg.solve(hess, grad)
        except np.linalg.LinAlgError:
            step = np.linalg.lstsq(hess, grad, rcond=None)[0]
        w = w - step
        if np.max(np.abs(step)) < 1e-8:
            break
    return w


def _brier(pred: list[float], y: list[int]) -> float:
    return sum((p - t) ** 2 for p, t in zip(pred, y)) / len(y) if y else float("nan")


def _log_loss(pred: list[float], y: list[int]) -> float:
    if not y:
        return float("nan")
    tot = 0.0
    for p, t in zip(pred, y):
        p = min(1 - 1e-12, max(1e-12, p))
        tot += -(t * math.log(p) + (1 - t) * math.log(1 - p))
    return tot / len(y)


def _reliability(probs: list[float], y: list[int], n_buckets: int = 10) -> list[dict[str, Any]]:
    """Market calibration: mean predicted vs realized rate per probability decile."""
    rows: list[dict[str, Any]] = []
    for b in range(n_buckets):
        lo, hi = b / n_buckets, (b + 1) / n_buckets
        idx = [i for i, p in enumerate(probs) if (lo <= p < hi) or (b == n_buckets - 1 and p >= hi)]
        if not idx:
            continue
        rows.append({
            "bucket": f"[{lo:.1f},{hi:.1f})",
            "n": len(idx),
            "mean_predicted": round(sum(probs[i] for i in idx) / len(idx), 4),
            "realized_rate": round(sum(y[i] for i in idx) / len(idx), 4),
        })
    return rows


def _subgroup_brier(keys: list[str], model_p: list[float], market_p: list[float], y: list[int]) -> list[dict[str, Any]]:
    groups: dict[str, list[int]] = {}
    for i, k in enumerate(keys):
        groups.setdefault(k, []).append(i)
    out: list[dict[str, Any]] = []
    for k, idx in sorted(groups.items()):
        mb = _brier([market_p[i] for i in idx], [y[i] for i in idx])
        modb = _brier([model_p[i] for i in idx], [y[i] for i in idx])
        out.append({"group": k, "n": len(idx), "market_brier": round(mb, 4), "model_brier": round(modb, 4),
                    "brier_skill_vs_market": round(1 - modb / mb, 4) if mb else None})
    return out


def _htc_bucket(hours: float | None) -> str:
    if hours is None:
        return "unknown"
    if hours < 6:
        return "0-6h"
    if hours < 24:
        return "6-24h"
    if hours < 72:
        return "24-72h"
    return ">72h"


def _bootstrap_skill_ci(market_loss: list[float], model_loss: list[float], markets: list[str],
                        n_boot: int = 5000, seed: int = 20260624) -> tuple[float, float, float]:
    """Paired, market-clustered bootstrap of mean(market_loss - model_loss).

    Returns (mean_gain, ci_low, ci_high); positive means the model beats the market.
    """
    import random

    rng = random.Random(seed)
    by_market: dict[str, list[int]] = {}
    for i, m in enumerate(markets):
        by_market.setdefault(m, []).append(i)
    keys = list(by_market.keys())
    diffs = [market_loss[i] - model_loss[i] for i in range(len(market_loss))]
    point = sum(diffs) / len(diffs)
    means = []
    for _ in range(n_boot):
        s = 0.0
        c = 0
        for _ in range(len(keys)):
            for i in by_market[keys[rng.randrange(len(keys))]]:
                s += diffs[i]
                c += 1
        means.append(s / c if c else 0.0)
    means.sort()
    lo = means[int(0.025 * n_boot)]
    hi = means[int(0.975 * n_boot)]
    return point, lo, hi


def _edge_roi(model_p: list[float], market_p: list[float], y: list[int], markets: list[str],
              threshold: float, fee: float, n_boot: int = 5000, seed: int = 20260624) -> dict[str, Any]:
    """Capital-weighted ROI of trading the model's disagreements with the obtainable price.

    The model fair is the "sharp anchor"; the market midpoint is the obtainable price. We
    buy the side the model thinks is underpriced by more than ``threshold`` (paying
    price + ``fee``), and settle at resolution. Betting at the line's own price is ~0-EV
    by construction, so positive ROI here means the model finds genuinely soft prices.
    """
    import random

    profit: list[float] = []
    staked: list[float] = []
    bet_markets: list[str] = []
    for mp, qp, t, mk in zip(model_p, market_p, y, markets):
        edge = mp - qp
        if edge > threshold:                       # model: YES underpriced -> buy YES at qp
            cost, payoff = qp + fee, float(t)
        elif -edge > threshold:                    # model: NO underpriced -> buy NO at 1-qp
            cost, payoff = (1.0 - qp) + fee, float(1 - t)
        else:
            continue
        if cost <= 0:
            continue
        profit.append(payoff - cost)
        staked.append(cost)
        bet_markets.append(mk)

    if not staked:
        return {"threshold": threshold, "fee": fee, "n_bets": 0, "roi": None, "roi_ci95": [None, None]}

    roi = sum(profit) / sum(staked)
    by_market: dict[str, list[int]] = {}
    for i, mk in enumerate(bet_markets):
        by_market.setdefault(mk, []).append(i)
    keys = list(by_market.keys())
    rng = random.Random(seed)
    rois: list[float] = []
    for _ in range(n_boot):
        pr = st = 0.0
        for _ in range(len(keys)):
            for i in by_market[keys[rng.randrange(len(keys))]]:
                pr += profit[i]
                st += staked[i]
        if st > 0:
            rois.append(pr / st)
    rois.sort()
    lo = rois[int(0.025 * len(rois))] if rois else None
    hi = rois[int(0.975 * len(rois))] if rois else None
    return {"threshold": threshold, "fee": fee, "n_bets": len(staked), "n_markets": len(keys),
            "roi": roi, "roi_ci95": [lo, hi], "profitable_significantly": bool(lo is not None and lo > 0)}


def train_skill_model(cfg: EngineConfig, test_fraction: float = 0.3, l2: float = 5.0,
                      uncertain_band: tuple[float, float] = (0.15, 0.85),
                      max_rows_per_token: int = 12) -> dict[str, Any]:
    train_root = cfg.output_root / "polymarket_training"
    rows = joined_feature_label_rows(str(train_root / "features_v2.csv"), str(train_root / "labels.csv"))
    feature_columns = select_feature_columns(rows) if rows else []
    design = _thin_by_token(build_design(rows, feature_columns), max_rows_per_token)

    out_dir = cfg.output_root / "polymarket_models"
    out_dir.mkdir(parents=True, exist_ok=True)

    train_idx, test_idx = _temporal_market_split(design, test_fraction)
    n_markets = len({str(r.get("market_id") or r.get("market_slug")) for r in design})
    if len(design) < 50 or not test_idx or len({design[i]["_y"] for i in train_idx}) < 2:
        summary = {
            "status": "insufficient_data",
            "model_version": MODEL_VERSION,
            "joined_rows": len(design),
            "markets": n_markets,
            "reason": "need >=50 joined rows, both classes in train, and a non-empty market-held-out test set",
            "generated_at_utc": now_utc(),
        }
        write_json(cfg.governance_root / "skill_model_summary.json", summary)
        return summary

    x_train = np.array([design[i]["_x"] for i in train_idx], dtype=float)
    y_train = np.array([design[i]["_y"] for i in train_idx], dtype=float)
    x_train_s, mean, std = _standardize(x_train)
    weights = _fit_logistic(x_train_s, y_train, l2=l2)

    test = [design[i] for i in test_idx]
    x_test_s = (np.array([r["_x"] for r in test], dtype=float) - mean) / std
    x_test_s[:, 0] = 1.0
    model_p = (1.0 / (1.0 + np.exp(-np.clip(x_test_s @ weights, -30, 30)))).tolist()
    market_p = [r["_market_p"] for r in test]
    y_test = [r["_y"] for r in test]
    markets = [str(r.get("market_id") or r.get("market_slug")) for r in test]

    model_brier, market_brier = _brier(model_p, y_test), _brier(market_p, y_test)
    model_ll, market_ll = _log_loss(model_p, y_test), _log_loss(market_p, y_test)
    brier_skill = 1 - model_brier / market_brier if market_brier else float("nan")
    gain, lo, hi = _bootstrap_skill_ci(
        [(mp - t) ** 2 for mp, t in zip(market_p, y_test)],
        [(pp - t) ** 2 for pp, t in zip(model_p, y_test)],
        markets,
    )

    unc = [i for i, mp in enumerate(market_p) if uncertain_band[0] <= mp <= uncertain_band[1]]
    unc_block = None
    if unc:
        unc_block = {
            "n": len(unc),
            "model_brier": _brier([model_p[i] for i in unc], [y_test[i] for i in unc]),
            "market_brier": _brier([market_p[i] for i in unc], [y_test[i] for i in unc]),
        }
        unc_block["brier_skill_vs_market"] = (
            1 - unc_block["model_brier"] / unc_block["market_brier"] if unc_block["market_brier"] else float("nan")
        )

    write_csv(out_dir / "skill_model_oos_predictions.csv", [
        {"market_id": markets[i], "token_id": test[i].get("token_id", ""),
         "prediction_timestamp": test[i].get("prediction_timestamp", ""),
         "market_probability": round(market_p[i], 6), "model_probability": round(model_p[i], 6),
         "target": y_test[i]} for i in range(len(test))
    ])

    weight_names = ["intercept", "market_logit"] + feature_columns
    feature_weights = sorted(
        ({"name": n, "std_weight": round(float(w), 4)} for n, w in zip(weight_names, weights.tolist())),
        key=lambda d: abs(d["std_weight"]), reverse=True,
    )
    htc_keys = [_htc_bucket(safe_float(r.get("hours_to_close"))) for r in test]
    cat_keys = ["worldcup" if str(r.get("category")) == "worldcup" else "other" for r in test]
    diagnostics = {
        "market_reliability_oos": _reliability(market_p, y_test),
        "skill_by_time_to_close": _subgroup_brier(htc_keys, model_p, market_p, y_test),
        "skill_by_category": _subgroup_brier(cat_keys, model_p, market_p, y_test),
        "standardized_feature_weights": feature_weights,
        "min_detectable_brier_gain": round((hi - lo) / 2, 5),
    }

    summary = {
        "status": "ok",
        "model_version": MODEL_VERSION,
        "generated_at_utc": now_utc(),
        "joined_rows": len(design),
        "markets": n_markets,
        "feature_columns": feature_columns,
        "l2": l2,
        "test_fraction": test_fraction,
        "split": {"train_rows": len(train_idx), "test_rows": len(test_idx),
                  "train_markets": n_markets - len(set(markets)), "test_markets": len(set(markets))},
        "oos_vs_market": {
            "n": len(y_test),
            "base_rate": sum(y_test) / len(y_test),
            "model_brier": model_brier, "market_brier": market_brier,
            "brier_skill_vs_market": brier_skill,
            "model_log_loss": model_ll, "market_log_loss": market_ll,
            "log_loss_skill_vs_market": 1 - model_ll / market_ll if market_ll else float("nan"),
            "mean_brier_gain_vs_market": gain,
            "mean_brier_gain_ci95": [lo, hi],
            "beats_market_significantly": lo > 0,
        },
        "oos_uncertain_region": unc_block,
        "diagnostics": diagnostics,
        "oos_edge_roi": {
            "note": "Trade model-vs-obtainable-price disagreements, settle at resolution. "
                    "fee approximates taker cost/half-spread; betting at the line itself is ~0-EV.",
            "by_threshold": [_edge_roi(model_p, market_p, y_test, markets, thr, fee=0.01)
                             for thr in (0.03, 0.05, 0.08)],
        },
        "verdict": (
            "model beats market OOS (95% CI excludes 0)" if lo > 0
            else "no statistically significant edge over the market OOS"
        ),
    }
    write_json(cfg.governance_root / "skill_model_summary.json", summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return train_skill_model(load_config(config_path))
