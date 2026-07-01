from __future__ import annotations

import math
from typing import Any

import numpy as np

from .config import EngineConfig, load_config
from .price_action_microstructure import build_latest_microstructure_features
from .utils import now_utc, read_csv_rows, safe_float, write_csv, write_json

OUTPUT_DIRNAME = "polymarket_price_action"
TRADE_EVENTS_FILE = "microstructure_trade_events.csv"
SUMMARY_JSON = "price_action_model_summary.json"
VALIDATION_FILE = "price_action_model_validation_predictions.csv"
CURRENT_FILE = "price_action_model_current_candidates.csv"
MODEL_ARTIFACT = "price_action_model_v1.json"
MODEL_VERSION = "pm-price-action-bid-reprice-logit-v1"

FEATURE_NAMES = [
    "entry_bid",
    "entry_ask",
    "entry_midpoint",
    "entry_spread",
    "relative_spread",
    "distance_to_half",
    "bid_move_abs",
    "mid_move_abs",
    "ask_move_abs",
    "spread_change",
    "net_buy_events",
    "net_buy_size",
    "current_price_change_size",
    "is_crypto",
    "is_sports",
    "is_esports",
    "is_macro",
    "is_tennis",
]

VALIDATION_FIELDS = [
    "split",
    "token_id",
    "market_slug",
    "family",
    "outcome",
    "entry_time_utc",
    "entry_ask",
    "exit_bid",
    "future_bid_edge",
    "future_bid_return",
    "pnl_usdc",
    "roi",
    "target",
    "predicted_reprice_probability",
    "selected_by_model",
]

CURRENT_FIELDS = [
    "token_id",
    "market_slug",
    "family",
    "outcome",
    "latest_time_utc",
    "latest_bid",
    "latest_ask",
    "latest_spread",
    "bid_move_abs",
    "mid_move_abs",
    "net_buy_events",
    "predicted_reprice_probability",
    "predicted_expected_roi",
    "minimum_probability_to_trade",
    "minimum_expected_roi_to_trade",
    "shadow_only",
]

THRESHOLD_GRID = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    value = cfg.raw.get("price_action_model", {})
    return value if isinstance(value, dict) else {}


def _float_setting(settings: dict[str, Any], key: str, default: float) -> float:
    value = safe_float(settings.get(key))
    return default if value is None else float(value)


def _int_setting(settings: dict[str, Any], key: str, default: int) -> int:
    value = safe_float(settings.get(key))
    return default if value is None else int(value)


def _family_flags(row: dict[str, Any]) -> dict[str, float]:
    family = str(row.get("family") or row.get("category") or row.get("signal_cohort") or "").lower()
    text = " ".join([family, str(row.get("market_slug") or ""), str(row.get("question") or "")]).lower()
    return {
        "is_crypto": 1.0 if "crypto" in text or "btc" in text or "sol" in text or "eth" in text else 0.0,
        "is_sports": 1.0 if "sports" in text or "world" in text or "soccer" in text else 0.0,
        "is_esports": 1.0 if "esports" in text or "lol" in text or "dota" in text else 0.0,
        "is_macro": 1.0 if "macro" in text or "fed" in text or "inflation" in text else 0.0,
        "is_tennis": 1.0 if "tennis" in text else 0.0,
    }


def _event_price(row: dict[str, Any], event_key: str, current_key: str | None = None) -> float:
    value = safe_float(row.get(event_key))
    if value is None and current_key is not None:
        value = safe_float(row.get(current_key))
    return 0.0 if value is None else float(value)


def _feature_vector(row: dict[str, Any], *, current: bool = False) -> list[float]:
    entry_bid = _event_price(row, "entry_bid", "latest_bid" if current else None)
    entry_ask = _event_price(row, "entry_ask", "latest_ask" if current else None)
    entry_midpoint = _event_price(row, "entry_midpoint", "latest_midpoint" if current else None)
    entry_spread = _event_price(row, "entry_spread", "latest_spread" if current else None)
    relative_spread = _event_price(row, "relative_spread")
    if relative_spread == 0.0 and entry_spread > 0 and entry_ask > 0:
        relative_spread = entry_spread / entry_ask
    flags = _family_flags(row)
    values = {
        "entry_bid": entry_bid,
        "entry_ask": entry_ask,
        "entry_midpoint": entry_midpoint,
        "entry_spread": entry_spread,
        "relative_spread": relative_spread,
        "distance_to_half": abs(entry_midpoint - 0.5) if entry_midpoint > 0 else 0.0,
        "bid_move_abs": _event_price(row, "bid_move_abs"),
        "mid_move_abs": _event_price(row, "mid_move_abs"),
        "ask_move_abs": _event_price(row, "ask_move_abs"),
        "spread_change": _event_price(row, "spread_change"),
        "net_buy_events": _event_price(row, "net_buy_events"),
        "net_buy_size": _event_price(row, "net_buy_size"),
        "current_price_change_size": _event_price(row, "current_price_change_size"),
        **flags,
    }
    return [float(values[name]) for name in FEATURE_NAMES]


def _clamp_probability(value: float) -> float:
    return max(1e-6, min(1 - 1e-6, float(value)))


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(values, -40, 40)))


def _standardize(matrix: np.ndarray) -> tuple[np.ndarray, list[float], list[float]]:
    mean = matrix.mean(axis=0)
    std = matrix.std(axis=0)
    mean[0] = 0.0
    std[0] = 1.0
    std = np.where((~np.isfinite(std)) | (std < 1e-12), 1.0, std)
    out = (matrix - mean) / std
    out[:, 0] = 1.0
    return out, mean.astype(float).tolist(), std.astype(float).tolist()


def _fit_logistic(matrix: np.ndarray, target: np.ndarray, *, l2: float, iters: int = 100) -> np.ndarray:
    weights = np.zeros(matrix.shape[1])
    reg = np.eye(matrix.shape[1]) * float(l2)
    reg[0, 0] = 0.0
    for _ in range(iters):
        probabilities = _sigmoid(matrix @ weights)
        diag = np.clip(probabilities * (1 - probabilities), 1e-7, None)
        gradient = matrix.T @ (probabilities - target) + reg @ weights
        hessian = (matrix.T * diag) @ matrix + reg
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            step = np.linalg.lstsq(hessian, gradient, rcond=None)[0]
        weights = weights - step
        if np.max(np.abs(step)) < 1e-8:
            break
    return weights


def _predict_probabilities(rows: list[dict[str, Any]], artifact: dict[str, Any], *, current: bool = False) -> list[float]:
    if not rows:
        return []
    matrix = np.array([[1.0, *_feature_vector(row, current=current)] for row in rows], dtype=float)
    mean = np.array(artifact["standardization_mean"], dtype=float)
    std = np.array(artifact["standardization_std"], dtype=float)
    weights = np.array(artifact["weights"], dtype=float)
    standardised = (matrix - mean) / std
    standardised[:, 0] = 1.0
    return [float(_clamp_probability(value)) for value in _sigmoid(standardised @ weights).tolist()]


def _prepared_event(row: dict[str, str], settings: dict[str, Any]) -> dict[str, Any] | None:
    entry_ask = safe_float(row.get("entry_ask"))
    exit_bid = safe_float(row.get("exit_bid"))
    stake = safe_float(row.get("stake_usdc")) or 0.0
    pnl = safe_float(row.get("pnl_usdc"))
    if entry_ask is None or exit_bid is None or pnl is None or entry_ask <= 0 or not 0 < exit_bid < 1:
        return None
    future_bid_edge = exit_bid - entry_ask
    future_bid_return = future_bid_edge / entry_ask
    min_label_return = _float_setting(settings, "minimum_profitable_return_label", 0.0)
    min_label_edge = _float_setting(settings, "minimum_bid_edge_abs_label", 0.0)
    target = int(pnl > 0 and future_bid_edge > min_label_edge and future_bid_return >= min_label_return)
    return {
        **row,
        "_x": [1.0, *_feature_vector(row)],
        "_y": target,
        "_future_bid_edge": future_bid_edge,
        "_future_bid_return": future_bid_return,
        "_stake_usdc": stake,
        "_pnl_usdc": pnl,
        "_roi": safe_float(row.get("roi")) or (pnl / stake if stake > 0 else 0.0),
    }


def _split_events(events: list[dict[str, Any]], settings: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    explicit_train = [row for row in events if str(row.get("split") or "").lower() == "train"]
    explicit_validation = [row for row in events if str(row.get("split") or "").lower() == "validation"]
    if explicit_train and explicit_validation:
        return explicit_train, explicit_validation
    ordered = sorted(events, key=lambda row: str(row.get("entry_time_utc") or ""))
    train_fraction = max(0.1, min(0.9, _float_setting(settings, "train_fraction", 0.6)))
    split_index = max(1, min(len(ordered) - 1, int(len(ordered) * train_fraction))) if len(ordered) > 1 else len(ordered)
    return ordered[:split_index], ordered[split_index:]


def _brier(probabilities: list[float], target: list[int]) -> float:
    return sum((p - y) ** 2 for p, y in zip(probabilities, target)) / len(target) if target else 0.0


def _log_loss(probabilities: list[float], target: list[int]) -> float:
    if not target:
        return 0.0
    total = 0.0
    for p, y in zip(probabilities, target):
        p = _clamp_probability(p)
        total += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    return total / len(target)


def _threshold_grid(settings: dict[str, Any]) -> list[float]:
    raw = settings.get("probability_threshold_grid")
    if isinstance(raw, list):
        values = [safe_float(item) for item in raw]
        grid = sorted({float(item) for item in values if item is not None and 0 < item < 1})
        if grid:
            return grid
    return THRESHOLD_GRID


def _trade_metrics(rows: list[dict[str, Any]], probabilities: list[float] | None = None, threshold: float | None = None) -> dict[str, Any]:
    selected = [
        (row, probabilities[idx] if probabilities is not None else 1.0)
        for idx, row in enumerate(rows)
        if threshold is None or (probabilities is not None and probabilities[idx] >= threshold)
    ]
    stake = sum(float(row.get("_stake_usdc") or 0.0) for row, _ in selected)
    pnl = sum(float(row.get("_pnl_usdc") or 0.0) for row, _ in selected)
    wins = sum(1 for row, _ in selected if float(row.get("_pnl_usdc") or 0.0) > 0)
    return {
        "threshold": threshold,
        "trades": len(selected),
        "stake_usdc": stake,
        "pnl_usdc": pnl,
        "roi": pnl / stake if stake > 0 else 0.0,
        "win_rate": wins / len(selected) if selected else 0.0,
        "mean_probability": sum(probability for _, probability in selected) / len(selected) if selected else 0.0,
    }


def _selected_metrics(rows: list[dict[str, Any]], probabilities: list[float], threshold: float) -> dict[str, Any]:
    metrics = _trade_metrics(rows, probabilities, threshold)
    return {
        "threshold": threshold,
        "selected_trades": metrics["trades"],
        "selected_stake_usdc": metrics["stake_usdc"],
        "selected_pnl_usdc": metrics["pnl_usdc"],
        "selected_roi": metrics["roi"],
        "selected_win_rate": metrics["win_rate"],
        "mean_selected_probability": metrics["mean_probability"],
    }


def _market_clustered_roi_ci(
    rows: list[dict[str, Any]],
    probabilities: list[float],
    threshold: float,
    *,
    iterations: int = 500,
    seed: int = 20260701,
) -> list[float | None]:
    selected = [
        row
        for row, probability in zip(rows, probabilities)
        if probability >= threshold and float(row.get("_stake_usdc") or 0.0) > 0
    ]
    if not selected:
        return [None, None]
    import random

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in selected:
        key = str(row.get("market_slug") or row.get("token_id") or "unknown")
        grouped.setdefault(key, []).append(row)
    keys = list(grouped)
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(iterations):
        pnl = 0.0
        stake = 0.0
        for _ in keys:
            picked = grouped[keys[rng.randrange(len(keys))]]
            pnl += sum(float(row.get("_pnl_usdc") or 0.0) for row in picked)
            stake += sum(float(row.get("_stake_usdc") or 0.0) for row in picked)
        samples.append(pnl / stake if stake > 0 else 0.0)
    samples.sort()
    return [samples[int(0.025 * iterations)], samples[int(0.975 * iterations)]]


def _choose_threshold_from_train(
    rows: list[dict[str, Any]],
    probabilities: list[float],
    settings: dict[str, Any],
) -> dict[str, Any]:
    min_trades = _int_setting(settings, "minimum_selected_train_trades", 8)
    min_roi = _float_setting(settings, "minimum_selected_train_roi", 0.03)
    min_win_rate = _float_setting(settings, "minimum_selected_train_win_rate", 0.55)
    candidates: list[dict[str, Any]] = []
    for threshold in _threshold_grid(settings):
        metrics = _selected_metrics(rows, probabilities, threshold)
        passed = (
            metrics["selected_trades"] >= min_trades
            and metrics["selected_pnl_usdc"] > 0
            and metrics["selected_roi"] >= min_roi
            and metrics["selected_win_rate"] >= min_win_rate
        )
        candidates.append({**metrics, "train_threshold_pass": passed})
    passing = [row for row in candidates if row["train_threshold_pass"]]
    if passing:
        chosen = sorted(
            passing,
            key=lambda row: (row["selected_roi"], row["selected_trades"], row["selected_pnl_usdc"]),
            reverse=True,
        )[0]
    else:
        fallback = _float_setting(settings, "minimum_probability_to_trade", 0.55)
        chosen = min(candidates, key=lambda row: abs(float(row["threshold"]) - fallback)) if candidates else _selected_metrics(rows, probabilities, fallback)
        chosen = {**chosen, "train_threshold_pass": False}
    return {
        "chosen_threshold": chosen["threshold"],
        "chosen_train_metrics": chosen,
        "threshold_grid": candidates,
        "minimum_selected_train_trades": min_trades,
        "minimum_selected_train_roi": min_roi,
        "minimum_selected_train_win_rate": min_win_rate,
    }


def _roi_expectancy_components(rows: list[dict[str, Any]], probabilities: list[float], threshold: float) -> dict[str, float]:
    selected = [row for row, probability in zip(rows, probabilities) if probability >= threshold]
    wins = [float(row.get("_roi") or 0.0) for row in selected if float(row.get("_pnl_usdc") or 0.0) > 0]
    losses = [float(row.get("_roi") or 0.0) for row in selected if float(row.get("_pnl_usdc") or 0.0) <= 0]
    all_wins = [float(row.get("_roi") or 0.0) for row in rows if float(row.get("_pnl_usdc") or 0.0) > 0]
    all_losses = [float(row.get("_roi") or 0.0) for row in rows if float(row.get("_pnl_usdc") or 0.0) <= 0]
    wins = wins or all_wins or [0.05]
    losses = losses or all_losses or [-0.03]
    return {
        "avg_win_roi": sum(wins) / len(wins),
        "avg_loss_roi": sum(losses) / len(losses),
    }


def _expected_roi(probability: float, expectancy: dict[str, float]) -> float:
    return probability * expectancy["avg_win_roi"] + (1.0 - probability) * expectancy["avg_loss_roi"]


def _validation_rows(rows: list[dict[str, Any]], probabilities: list[float], threshold: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row, probability in zip(rows, probabilities):
        out.append(
            {
                "split": row.get("split", "validation"),
                "token_id": row.get("token_id", ""),
                "market_slug": row.get("market_slug", ""),
                "family": row.get("family", ""),
                "outcome": row.get("outcome", ""),
                "entry_time_utc": row.get("entry_time_utc", ""),
                "entry_ask": row.get("entry_ask", ""),
                "exit_bid": row.get("exit_bid", ""),
                "future_bid_edge": row["_future_bid_edge"],
                "future_bid_return": row["_future_bid_return"],
                "pnl_usdc": row["_pnl_usdc"],
                "roi": row["_roi"],
                "target": row["_y"],
                "predicted_reprice_probability": probability,
                "selected_by_model": probability >= threshold,
            }
        )
    out.sort(key=lambda item: safe_float(item.get("predicted_reprice_probability")) or 0.0, reverse=True)
    return out


def _current_candidate_rows(
    rows: list[dict[str, Any]],
    probabilities: list[float],
    *,
    probability_threshold: float,
    minimum_expected_roi: float,
    expectancy: dict[str, float],
    validation_pass: bool,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row, probability in zip(rows, probabilities):
        predicted_expected_roi = _expected_roi(probability, expectancy)
        if not validation_pass or probability < probability_threshold or predicted_expected_roi < minimum_expected_roi:
            continue
        out.append(
            {
                "token_id": row.get("token_id", ""),
                "market_slug": row.get("market_slug", ""),
                "family": row.get("family", ""),
                "outcome": row.get("outcome", ""),
                "latest_time_utc": row.get("entry_time_utc", row.get("latest_time_utc", "")),
                "latest_bid": row.get("entry_bid", row.get("latest_bid", "")),
                "latest_ask": row.get("entry_ask", row.get("latest_ask", "")),
                "latest_spread": row.get("entry_spread", row.get("latest_spread", "")),
                "bid_move_abs": row.get("bid_move_abs", ""),
                "mid_move_abs": row.get("mid_move_abs", ""),
                "net_buy_events": row.get("net_buy_events", ""),
                "predicted_reprice_probability": probability,
                "predicted_expected_roi": predicted_expected_roi,
                "minimum_probability_to_trade": probability_threshold,
                "minimum_expected_roi_to_trade": minimum_expected_roi,
                "shadow_only": True,
            }
        )
    out.sort(key=lambda item: safe_float(item.get("predicted_reprice_probability")) or 0.0, reverse=True)
    return out


def train_price_action_model(cfg: EngineConfig) -> dict[str, Any]:
    """Train a shadow model for the actual trading problem: future bid repricing.

    Label: buy at the historical ask; count it as positive only if a later
    executable bid clears the entry ask/cost hurdle inside the configured
    horizon used by the microstructure event builder.
    """
    settings = _settings(cfg)
    out_dir = cfg.output_root / OUTPUT_DIRNAME
    model_dir = cfg.output_root / "polymarket_models"
    rows = read_csv_rows(out_dir / TRADE_EVENTS_FILE)
    prepared = [event for row in rows if (event := _prepared_event(row, settings)) is not None]
    train_rows, validation_rows = _split_events(prepared, settings) if prepared else ([], [])
    minimum_rows = _int_setting(settings, "minimum_rows", 100)
    minimum_validation_rows = _int_setting(settings, "minimum_validation_rows", 25)
    l2 = _float_setting(settings, "l2", 5.0)
    common = {
        "model_version": MODEL_VERSION,
        "generated_at_utc": now_utc(),
        "deployment_mode": "shadow",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        "trading_objective": "predict_future_executable_bid_reprices_above_entry_ask",
        "label_definition": "target=1 only when future exit_bid clears historical entry_ask after the configured bid/ask cost hurdle.",
        "feature_names": FEATURE_NAMES,
        "source_file": str(out_dir / TRADE_EVENTS_FILE),
    }
    blockers: list[str] = []
    if len(prepared) < minimum_rows:
        blockers.append(f"training events {len(prepared)} < {minimum_rows}")
    if len(validation_rows) < minimum_validation_rows:
        blockers.append(f"validation events {len(validation_rows)} < {minimum_validation_rows}")
    if len({int(row["_y"]) for row in train_rows}) < 2:
        blockers.append("training split does not contain both profitable and unprofitable repricing examples")
    if blockers:
        payload = {
            **common,
            "status": "insufficient_data",
            "decision": "collect_more_bid_ask_price_action_training_events",
            "blockers": blockers,
            "training_events": len(prepared),
            "train_rows": len(train_rows),
            "validation_rows": len(validation_rows),
            "promotion_ready": False,
            "current_model_candidates": 0,
            "warnings": {"shadow_only": True, "not_settlement_probability_model": True},
        }
        write_json(out_dir / SUMMARY_JSON, payload)
        write_json(model_dir / MODEL_ARTIFACT, payload)
        write_csv(out_dir / VALIDATION_FILE, [], fieldnames=VALIDATION_FIELDS)
        write_csv(out_dir / CURRENT_FILE, [], fieldnames=CURRENT_FIELDS)
        return payload

    train_matrix = np.array([row["_x"] for row in train_rows], dtype=float)
    train_target = np.array([row["_y"] for row in train_rows], dtype=float)
    train_standardised, mean, std = _standardize(train_matrix)
    weights = _fit_logistic(train_standardised, train_target, l2=l2)
    artifact = {
        **common,
        "status": "trained",
        "weights": weights.astype(float).tolist(),
        "standardization_mean": mean,
        "standardization_std": std,
        "l2": l2,
    }
    train_probabilities = _predict_probabilities(train_rows, artifact)
    threshold_selection = _choose_threshold_from_train(train_rows, train_probabilities, settings)
    threshold = float(threshold_selection["chosen_threshold"])
    probabilities = _predict_probabilities(validation_rows, artifact)
    targets = [int(row["_y"]) for row in validation_rows]
    selected = _selected_metrics(validation_rows, probabilities, threshold)
    buy_all = _trade_metrics(validation_rows)
    validation_roi_ci = _market_clustered_roi_ci(
        validation_rows,
        probabilities,
        threshold,
        iterations=_int_setting(settings, "bootstrap_iterations", 500),
    )
    min_selected_trades = _int_setting(settings, "minimum_selected_validation_trades", 5)
    min_selected_roi = _float_setting(settings, "minimum_selected_validation_roi", 0.03)
    min_selected_win_rate = _float_setting(settings, "minimum_selected_validation_win_rate", 0.55)
    min_roi_ci_low = _float_setting(settings, "minimum_selected_validation_roi_ci_low", 0.0)
    validation_blockers: list[str] = []
    if not threshold_selection["chosen_train_metrics"].get("train_threshold_pass"):
        validation_blockers.append("no probability threshold cleared the training split trade gates")
    if len(validation_rows) < minimum_validation_rows:
        validation_blockers.append(f"validation rows {len(validation_rows)} < {minimum_validation_rows}")
    if selected["selected_trades"] < min_selected_trades:
        validation_blockers.append(f"selected validation trades {selected['selected_trades']} < {min_selected_trades}")
    if selected["selected_pnl_usdc"] <= 0:
        validation_blockers.append("selected validation P&L is not positive")
    if selected["selected_roi"] < min_selected_roi:
        validation_blockers.append(f"selected validation ROI {selected['selected_roi']:.4f} < {min_selected_roi:.4f}")
    if selected["selected_win_rate"] < min_selected_win_rate:
        validation_blockers.append(f"selected validation win rate {selected['selected_win_rate']:.4f} < {min_selected_win_rate:.4f}")
    if validation_roi_ci[0] is None or float(validation_roi_ci[0]) < min_roi_ci_low:
        validation_blockers.append(f"selected validation ROI CI low {validation_roi_ci[0]} < {min_roi_ci_low:.4f}")
    if selected["selected_roi"] <= buy_all["roi"]:
        validation_blockers.append("model-selected validation ROI does not beat buy-all baseline ROI")
    validation_pass = bool(
        not validation_blockers
    )
    validation_predictions = _validation_rows(validation_rows, probabilities, threshold)
    expectancy = _roi_expectancy_components(train_rows, train_probabilities, threshold)
    minimum_expected_roi = _float_setting(settings, "minimum_expected_roi_to_trade", min_selected_roi)
    current_rows = build_latest_microstructure_features(cfg)
    current_probabilities = _predict_probabilities(current_rows, artifact, current=True)
    current_candidates = _current_candidate_rows(
        current_rows,
        current_probabilities,
        probability_threshold=threshold,
        minimum_expected_roi=minimum_expected_roi,
        expectancy=expectancy,
        validation_pass=validation_pass,
    )
    payload = {
        **artifact,
        "decision": "price_action_model_ready_for_forward_shadow"
        if validation_pass
        else "collect_more_bid_ask_price_action_model_evidence",
        "promotion_ready": validation_pass,
        "training_events": len(prepared),
        "train_rows": len(train_rows),
        "validation_rows": len(validation_rows),
        "validation_positive_rate": sum(targets) / len(targets) if targets else 0.0,
        "validation_brier": _brier(probabilities, targets),
        "validation_log_loss": _log_loss(probabilities, targets),
        "train_threshold_selection": threshold_selection,
        "chosen_probability_threshold": threshold,
        "validation_selected": selected,
        "validation_blockers": validation_blockers,
        "validation_buy_all_baseline": buy_all,
        "validation_selected_roi_ci95": validation_roi_ci,
        "validation_edge_over_buy_all_roi": selected["selected_roi"] - buy_all["roi"],
        "roi_expectancy_from_train": expectancy,
        "minimum_expected_roi_to_trade": minimum_expected_roi,
        "minimum_selected_validation_trades": min_selected_trades,
        "minimum_selected_validation_roi": min_selected_roi,
        "minimum_selected_validation_win_rate": min_selected_win_rate,
        "minimum_selected_validation_roi_ci_low": min_roi_ci_low,
        "current_rows_scored": len(current_rows),
        "current_model_candidates": len(current_candidates),
        "current_candidates_preview": current_candidates[:15],
        "validation_predictions_file": str(out_dir / VALIDATION_FILE),
        "current_candidates_file": str(out_dir / CURRENT_FILE),
        "warnings": {
            "shadow_only": True,
            "not_settlement_probability_model": True,
            "uses_entry_ask_and_future_exit_bid": True,
            "does_not_authorise_paper_without_governance": True,
        },
    }
    write_json(out_dir / SUMMARY_JSON, payload)
    write_json(model_dir / MODEL_ARTIFACT, payload)
    write_csv(out_dir / VALIDATION_FILE, validation_predictions, fieldnames=VALIDATION_FIELDS)
    write_csv(out_dir / CURRENT_FILE, current_candidates, fieldnames=CURRENT_FIELDS)
    return payload


def run(config_path: str = "polymarket_predictive_config.example.yaml") -> dict[str, Any]:
    cfg = load_config(config_path)
    return train_price_action_model(cfg)
