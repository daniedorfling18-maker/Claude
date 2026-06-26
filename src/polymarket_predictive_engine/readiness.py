from __future__ import annotations

import os
from typing import Any

from .config import EngineConfig, kill_switch_active, load_config
from .data_quality import data_quality
from .pipeline_health import pipeline_health
from .utils import csv_columns, discover_files, find_first_column, read_csv_rows, read_json, write_json

DECISIONS = {
    "APPROVED_FOR_TRAINING",
    "APPROVED_FOR_BACKTEST_ONLY",
    "APPROVED_FOR_PAPER_TRADING_ONLY",
    "NOT_APPROVED_INSUFFICIENT_LABELS",
    "NOT_APPROVED_DATA_QUALITY_BLOCKERS",
    "NOT_APPROVED_PIPELINE_STALE",
    "NOT_APPROVED_SCHEMA_UNKNOWN",
}


def _clean_resolved_markets_from_resolution_file(cfg: EngineConfig) -> set[str]:
    path = cfg.output_root / "polymarket_training" / "market_resolutions.csv"
    resolved: set[str] = set()
    for row in read_csv_rows(path):
        if row.get("resolution_quality") == "clean_settlement" and str(row.get("target", "")).lower() in {"0", "1", "true", "false"}:
            market = row.get("market_slug") or row.get("condition_id") or row.get("gamma_market_id")
            if market:
                resolved.add(market)
    return resolved


def count_clean_resolved_labels(cfg: EngineConfig) -> int:
    """Unique (market, token) clean settlements across all resolution files."""
    seen: set[tuple[str, str]] = set()
    for name in ("market_resolutions.csv", "historical_resolutions.csv", "websocket_resolutions.csv"):
        for row in read_csv_rows(cfg.output_root / "polymarket_training" / name):
            if row.get("resolution_quality") != "clean_settlement":
                continue
            token = row.get("token_id", "")
            if not token or str(row.get("target", "")).lower() not in {"0", "1", "true", "false"}:
                continue
            market = row.get("condition_id") or row.get("market_slug") or row.get("gamma_market_id") or ""
            seen.add((market, token))
    return len(seen)


def training_artifact_blockers(cfg: EngineConfig) -> list[str]:
    """Detect empty, pseudo-labelled, or quarantined artefacts before model use."""
    training = cfg.output_root / "polymarket_training"
    labels_path = training / "labels.csv"
    labels = read_csv_rows(labels_path, limit=500000)
    blockers: list[str] = []
    if labels_path.exists() and not labels:
        blockers.append("labels.csv exists but is empty")
    for row in labels:
        if (
            str(row.get("label_type", "")).lower() == "pseudo"
            or str(row.get("pseudo_label", "")).lower() in {"1", "true", "yes"}
            or "pseudo" in str(row.get("label_source", "")).lower()
        ):
            blockers.append("labels.csv contains pseudo labels")
            break
    features_path = training / "features_v2.csv"
    if features_path.exists() and not labels:
        blockers.append("features_v2.csv exists while clean labels are empty")
    for row in read_csv_rows(features_path, limit=500000):
        source = " ".join(
            [
                str(row.get("source_file", "")),
                str(row.get("feature_source", "")),
            ]
        ).lower()
        if "quarantine" in source or "pseudo" in source:
            blockers.append("features_v2.csv references quarantined or pseudo-labelled data")
            break
    return list(dict.fromkeys(blockers))


def _market_relative_validation_summary(cfg: EngineConfig) -> dict[str, Any]:
    summary = read_json(cfg.governance_root / "market_relative_validation_summary.json", default={}) or {}
    if not summary:
        summary = read_json(cfg.output_root / "polymarket_model_validation" / "market_relative_validation_summary.json", default={}) or {}
    return summary if isinstance(summary, dict) else {}


def _market_relative_skill_is_credible(summary: dict[str, Any]) -> bool:
    if not summary:
        return False
    if summary.get("approved_for_paper_trading") is True and summary.get("status") == "approved":
        return True
    ci = summary.get("brier_gain_vs_market_ci95") or [None, None]
    try:
        ci_low = float(ci[0])
    except Exception:
        return False
    return bool(
        summary.get("beats_market_oos")
        and summary.get("statistically_credible_market_relative_skill")
        and not summary.get("model_copies_market_midpoint")
        and ci_low > 0
    )


def _optimized_model_summary(cfg: EngineConfig) -> dict[str, Any]:
    summary = read_json(cfg.output_root / "polymarket_models" / "optimized_model_v1.json", default={}) or {}
    return summary if isinstance(summary, dict) else {}


def _optimized_model_is_champion(summary: dict[str, Any]) -> bool:
    return bool(
        summary.get("status") == "trained"
        and summary.get("deployment_mode") == "champion"
        and summary.get("model_type") == "market_anchored_logistic"
    )


def _forward_paper_evidence(cfg: EngineConfig) -> dict[str, Any]:
    """Summarise observed forward paper trading evidence for live-promotion governance."""
    fills = read_csv_rows(cfg.output_root / "polymarket_portfolio" / "paper_fills.csv", limit=500000)
    snapshots = read_csv_rows(cfg.output_root / "polymarket_portfolio" / "portfolio_snapshots.csv", limit=500000)
    latest_cycle = read_json(cfg.governance_root / "forward_paper_cycle.json", default={}) or {}
    sources: list[dict[str, Any]] = [
        {
            "path": str(cfg.output_root / "polymarket_portfolio" / "paper_fills.csv"),
            "status": "present" if fills else "missing_or_empty",
            "trades": len(fills),
            "roi": None,
        }
    ]
    legacy_candidates = [
        cfg.governance_root / "forward_paper_evidence.json",
        cfg.governance_root / "paper_forward_summary.json",
        cfg.output_root / "polymarket_paper" / "paper_session_summary.json",
        cfg.output_root / "polymarket_paper_edge" / "forward_paper_summary.json",
    ]
    legacy_trades = 0
    legacy_positive_roi = False
    legacy_roi = None
    for path in legacy_candidates:
        summary = read_json(path, default={}) or {}
        if not isinstance(summary, dict) or not summary:
            continue
        raw_trades = (
            summary.get("trades")
            or summary.get("paper_trades")
            or summary.get("filled_orders")
            or summary.get("orders")
            or 0
        )
        try:
            trades_from_summary = int(raw_trades)
        except Exception:
            trades_from_summary = 0
        raw_roi = (
            summary.get("roi_on_staked")
            or summary.get("paper_roi")
            or summary.get("realized_roi")
            or summary.get("roi")
        )
        roi_from_summary = None
        if raw_roi not in {None, ""}:
            try:
                roi_from_summary = float(raw_roi)
            except Exception:
                roi_from_summary = None
        legacy_trades = max(legacy_trades, trades_from_summary)
        legacy_positive_roi = legacy_positive_roi or bool(roi_from_summary is not None and roi_from_summary > 0)
        if legacy_roi is None and roi_from_summary is not None:
            legacy_roi = roi_from_summary
        sources.append(
            {
                "path": str(path),
                "status": summary.get("status", "present"),
                "trades": trades_from_summary,
                "roi": roi_from_summary,
            }
        )

    trades = max(len(fills), legacy_trades)
    roi = None
    positive_roi = False
    if snapshots:
        latest = snapshots[-1]
        equity = None
        for key in ("equity_usdc", "equity"):
            try:
                if latest.get(key, "") != "":
                    equity = float(latest[key])
                    break
            except Exception:
                pass
        starting_cash = float(cfg.raw.get("paper_trading", {}).get("starting_cash", cfg.raw.get("risk", {}).get("bankroll", 1000)))
        if equity is not None and starting_cash > 0:
            roi = (equity - starting_cash) / starting_cash
            positive_roi = roi > 0
    if roi is None:
        roi = legacy_roi
        positive_roi = legacy_positive_roi
    return {
        "present": bool(fills or snapshots or any(source.get("status") == "present" for source in sources)),
        "trades": trades,
        "latest_cycle_status": latest_cycle.get("status", "missing") if isinstance(latest_cycle, dict) else "missing",
        "latest_cycle_predictions": latest_cycle.get("predictions", 0) if isinstance(latest_cycle, dict) else 0,
        "paper_roi": roi,
        "positive_roi": positive_roi,
        "sources": sources,
    }


def paper_live_promotion_gate(cfg: EngineConfig) -> dict[str, Any]:
    """Gate promotion on accumulated labels and proven out-of-sample market-relative skill."""
    thresholds = cfg.raw.get("governance_thresholds", {})
    min_labels = int(thresholds.get("min_resolved_labels", 100))
    target_labels = int(thresholds.get("target_resolved_labels", 300))

    labels = count_clean_resolved_labels(cfg)
    legacy = read_json(cfg.governance_root / "skill_model_summary.json", default={}) or {}
    legacy_oos = legacy.get("oos_vs_market", {}) if isinstance(legacy, dict) else {}
    legacy_skill = bool(legacy_oos.get("beats_market_significantly"))

    market_validation = _market_relative_validation_summary(cfg)
    market_oos = market_validation.get("oos", {}) if isinstance(market_validation, dict) else {}
    market_skill = _market_relative_skill_is_credible(market_validation)
    skill_significant = market_skill or legacy_skill

    dq_issues, _ = data_quality(cfg, allow_warnings=True)
    blockers = sum(1 for i in dq_issues if i.get("severity") == "blocker" and i.get("issue_type") != "no_raw_snapshots")

    paper_reasons: list[str] = []
    if labels < min_labels:
        paper_reasons.append(f"insufficient resolved labels: {labels} < {min_labels}")
    if not skill_significant:
        paper_reasons.append("no statistically credible out-of-sample skill over the market midpoint")
    if market_validation.get("model_copies_market_midpoint"):
        paper_reasons.append("model probabilities merely copy the market midpoint")
    if blockers:
        paper_reasons.append(f"{blockers} data-quality blocker(s)")
    if kill_switch_active():
        paper_reasons.append("kill switch active")

    forward_paper_evidence = _forward_paper_evidence(cfg)
    min_forward_paper_trades = int(thresholds.get("min_forward_paper_trades", 1))

    live_reasons = list(paper_reasons)
    if forward_paper_evidence["trades"] < min_forward_paper_trades:
        live_reasons.append(
            f"forward paper evidence missing or insufficient: "
            f"{forward_paper_evidence['trades']} < {min_forward_paper_trades}"
        )
    if thresholds.get("require_positive_forward_paper_roi") and not forward_paper_evidence["positive_roi"]:
        live_reasons.append("forward paper evidence does not show positive ROI")
    if labels < target_labels:
        live_reasons.append(f"labels below live target: {labels} < {target_labels}")
    if not cfg.live_approval_file.exists():
        live_reasons.append(f"human approval file missing: {cfg.live_approval_file}")
    optimized_summary = _optimized_model_summary(cfg)

    payload = {
        "resolved_labels": labels,
        "min_resolved_labels": min_labels,
        "target_resolved_labels": target_labels,
        "skill_model_status": legacy.get("status", "missing") if isinstance(legacy, dict) else "missing",
        "oos_brier_skill_vs_market": legacy_oos.get("brier_skill_vs_market"),
        "oos_beats_market_significantly": skill_significant,
        "market_relative_validation_status": market_validation.get("status", "missing"),
        "market_relative_validation_rows": market_oos.get("sample_size"),
        "market_relative_validation_markets": market_oos.get("markets"),
        "market_relative_brier_improvement": market_oos.get("brier_improvement_vs_market"),
        "market_relative_brier_gain_ci95": market_validation.get("brier_gain_vs_market_ci95"),
        "market_relative_skill_credible": market_skill,
        "model_copies_market_midpoint": market_validation.get("model_copies_market_midpoint"),
        "optimized_model_status": optimized_summary.get("status", "missing"),
        "optimized_model_deployment_mode": optimized_summary.get("deployment_mode", "missing"),
        "optimized_model_champion": _optimized_model_is_champion(optimized_summary),
        "data_quality_blockers": blockers,
        "forward_paper_evidence": forward_paper_evidence,
        "approved_for_paper_trading": not paper_reasons,
        "approved_for_live_trading": not live_reasons,
        "paper_blockers": paper_reasons,
        "live_blockers": live_reasons,
    }
    write_json(cfg.governance_root / "promotion_gate.json", payload)
    return payload


def paper_trade_readiness(cfg: EngineConfig) -> dict[str, Any]:
    """Go/no-go for *paper* trading. Paper is risk-free, so it is permitted on mechanical
    grounds (clean data + a trained calibration model + paper mode + kill switch off) and
    does NOT require proven edge - paper is how forward edge is gathered. The honest
    out-of-sample paper P&L is surfaced as an advisory so the user knows what to expect.
    Live promotion still goes through ``paper_live_promotion_gate``.
    """
    thresholds = cfg.raw.get("governance_thresholds", {})
    paper_min = int(thresholds.get("min_paper_labels", 50))
    labels = count_clean_resolved_labels(cfg)
    calibration_present = (cfg.output_root / "polymarket_models" / "calibration_v2.json").exists()
    optimized_summary = _optimized_model_summary(cfg)
    optimized_champion = _optimized_model_is_champion(optimized_summary)
    model_present = calibration_present or optimized_champion
    dq_issues, _ = data_quality(cfg, allow_warnings=True)
    blockers = [i for i in dq_issues if i.get("severity") == "blocker" and i.get("issue_type") != "no_raw_snapshots"]
    artifact_blockers = training_artifact_blockers(cfg)

    paper_summary = read_json(cfg.output_root / "polymarket_paper_edge" / "paper_edge_summary.json", default={}) or {}
    min_edge = paper_summary.get("minimum_edge", float(cfg.raw.get("risk", {}).get("minimum_edge", 0.03)))
    oos_roi = None
    for curve in paper_summary.get("paper_pnl_by_edge_threshold", []):
        if abs(float(curve.get("edge_threshold", -1)) - float(min_edge)) < 1e-9:
            oos_roi = curve.get("roi_on_staked")

    reasons: list[str] = []
    if cfg.trading_mode not in {"paper", "backtest"}:
        reasons.append(f"trading.mode is {cfg.trading_mode}, expected paper or backtest")
    if kill_switch_active():
        reasons.append("kill switch active")
    if not model_present:
        reasons.append("no trained calibration model or optimized champion (run train-calibration or optimize-model)")
    if labels < paper_min:
        reasons.append(f"insufficient resolved labels: {labels} < {paper_min}")
    if blockers:
        reasons.append(f"{len(blockers)} data-quality blocker(s)")
    reasons.extend(artifact_blockers)
    clean_snapshot_path = cfg.output_root / "polymarket_training" / "clean_resolved_snapshot_labels.csv"
    if (
        bool(thresholds.get("require_clean_snapshot_labels_for_paper", False))
        and clean_snapshot_path.exists()
        and not read_csv_rows(clean_snapshot_path, limit=1)
    ):
        reasons.append("clean_resolved_snapshot_labels.csv has zero rows")
    if os.getenv("POLYMARKET_EXECUTE_LIVE") == "true" or os.getenv("POLYMARKET_LIVE_TRADING") == "1":
        reasons.append("live execution env flag is set; refusing to run paper under live flags")

    payload = {
        "decision": "APPROVED_FOR_PAPER_TRADING" if not reasons else "NOT_APPROVED_FOR_PAPER_TRADING",
        "approved_for_paper_trading": not reasons,
        "resolved_labels": labels,
        "min_paper_labels": paper_min,
        "calibration_model_present": calibration_present,
        "optimized_model_champion": optimized_champion,
        "optimized_model_deployment_mode": optimized_summary.get("deployment_mode", "missing"),
        "model_source": (
            "optimized_market_anchored_logistic"
            if optimized_champion
            else ("calibration_v2" if calibration_present else "missing")
        ),
        "trading_mode": cfg.trading_mode,
        "kill_switch_active": kill_switch_active(),
        "data_quality_blockers": len(blockers),
        "training_artifact_blockers": artifact_blockers,
        "blockers": reasons,
        "oos_paper_roi_at_minimum_edge": oos_roi,
        "expected_profitable_oos": bool(oos_roi is not None and oos_roi > 0),
        "advisory": (
            "paper trading mechanically ready, but the model's out-of-sample paper P&L is not "
            "positive - expect losses; run forward only to gather a live track record"
            if (oos_roi is not None and oos_roi <= 0)
            else "paper trading mechanically ready"
        ),
    }
    write_json(cfg.governance_root / "paper_trade_readiness.json", payload)
    return payload


def readiness_decision(cfg: EngineConfig) -> dict[str, Any]:
    _, dq_summary = data_quality(cfg, allow_warnings=True)
    health_rows, health_summary = pipeline_health(cfg)
    raw_files = discover_files(cfg.data_root, ["outputs/polymarket_wide/**/raw_market_snapshots.csv", "outputs/polymarket_fixed/**/raw_market_snapshots.csv"])
    unique_markets: set[str] = set()
    resolved_markets: set[str] = _clean_resolved_markets_from_resolution_file(cfg)
    snapshot_counts: dict[str, int] = {}
    schema_unknown = False
    artifact_blockers = training_artifact_blockers(cfg)
    for path in raw_files:
        cols = csv_columns(path)
        market_col = find_first_column(cols, cfg.raw.get("schema", {}).get("market_id_fields", ["market_id", "condition_id", "id", "market_slug", "slug"]))
        if not market_col:
            schema_unknown = True
            continue
        rows = read_csv_rows(path, limit=200000)
        for row in rows:
            m = row.get(market_col, "")
            if m:
                unique_markets.add(m)
                snapshot_counts[m] = snapshot_counts.get(m, 0) + 1
                keys = " ".join(row.keys()).lower()
                if any(k in keys for k in ["winning", "resolved", "resolution"]) and any(str(v).lower() in {"1", "true", "yes", "won", "winner"} for v in row.values()):
                    resolved_markets.add(m)
    joined_resolved_markets = unique_markets.intersection(resolved_markets)
    if artifact_blockers:
        decision = "NOT_APPROVED_DATA_QUALITY_BLOCKERS"
    elif dq_summary.get("blocker_count", 0):
        decision = "NOT_APPROVED_DATA_QUALITY_BLOCKERS"
    elif schema_unknown:
        decision = "NOT_APPROVED_SCHEMA_UNKNOWN"
    elif health_summary.get("stalled_categories"):
        decision = "NOT_APPROVED_PIPELINE_STALE"
    elif len(joined_resolved_markets) < cfg.min_resolved_markets:
        decision = "NOT_APPROVED_INSUFFICIENT_LABELS"
    elif len(unique_markets) >= cfg.min_resolved_markets and min(snapshot_counts.values() or [0]) >= cfg.min_snapshots_per_market:
        decision = "APPROVED_FOR_TRAINING"
    else:
        decision = "APPROVED_FOR_BACKTEST_ONLY"

    market_validation = _market_relative_validation_summary(cfg)
    market_oos = market_validation.get("oos", {}) if isinstance(market_validation, dict) else {}
    market_skill_credible = _market_relative_skill_is_credible(market_validation)
    paper_blockers: list[str] = []
    if decision not in {"APPROVED_FOR_TRAINING", "APPROVED_FOR_BACKTEST_ONLY", "APPROVED_FOR_PAPER_TRADING_ONLY"}:
        paper_blockers.append(f"data readiness decision is {decision}")
    if not market_skill_credible:
        paper_blockers.append("market-relative validation has not proven statistically credible skill")
    if market_validation.get("model_copies_market_midpoint"):
        paper_blockers.append("model probabilities merely copy the market midpoint")
    if kill_switch_active():
        paper_blockers.append("kill switch active")

    payload = {
        "decision": decision,
        "data_quality_blockers": dq_summary.get("blocker_count", 0),
        "training_artifact_blockers": artifact_blockers,
        "unique_markets": len(unique_markets),
        "unique_resolved_markets": len(joined_resolved_markets),
        "clean_resolution_markets_available": len(resolved_markets),
        "minimum_snapshots_per_market_observed": min(snapshot_counts.values() or [0]),
        "category_coverage": [r["category"] for r in health_rows if r.get("raw_snapshots_growing")],
        "stale_collectors": health_summary.get("stalled_categories", []),
        "duplicate_writer_risks": [],
        "unsuitable_latest_only_data": any(r.get("latest_joined_updating") and not r.get("raw_snapshots_growing") for r in health_rows),
        "missing_resolution_outcomes": len(joined_resolved_markets) == 0,
        "market_relative_validation_status": market_validation.get("status", "missing"),
        "market_relative_validation_rows": market_oos.get("sample_size"),
        "market_relative_validation_markets": market_oos.get("markets"),
        "market_relative_brier_improvement": market_oos.get("brier_improvement_vs_market"),
        "market_relative_brier_gain_ci95": market_validation.get("brier_gain_vs_market_ci95"),
        "market_relative_skill_credible": market_skill_credible,
        "approved_for_paper_trading": not paper_blockers,
        "paper_trading_blockers": paper_blockers,
    }
    write_json(cfg.governance_root / "data_readiness_decision.json", payload)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return readiness_decision(load_config(config_path))
