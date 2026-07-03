"""Append-only evidence history for the Polymarket research loop.

Latest-snapshot dashboards are useful, but they hide whether evidence is
actually accumulating. This module turns the key governance artifacts into a
small time series, one row per source artifact generation.

The writer is deliberately idempotent: if a source artifact's
``generated_at_utc`` is already the latest recorded timestamp for that source,
it is not appended again.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Callable

from .config import EngineConfig, load_config
from .utils import git_commit_hash, read_csv_rows, read_json, safe_float, write_csv, write_json

HISTORY_FIELDS = [
    "recorded_at_utc",
    "source",
    "positions_scored_attributed",
    "final_line_positions",
    "mean_final_clv",
    "positive_cohorts",
    "total_pnl_usdc",
    "decision_or_class_summary",
]


def _pipe(values: Any) -> str:
    if not isinstance(values, list):
        return ""
    return "|".join(str(value) for value in values if str(value).strip())


def _class_summary(rows: Any, field: str) -> str:
    if not isinstance(rows, list):
        return ""
    counts = Counter(str(row.get(field) or "unknown") for row in rows if isinstance(row, dict))
    return "|".join(f"{key}={value}" for key, value in sorted(counts.items()))


def _generated_at(payload: dict[str, Any]) -> str:
    return str(payload.get("generated_at_utc") or "").strip()


def _number(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return ""
    return str(round(number, 6))


def _clv_row(payload: dict[str, Any]) -> dict[str, Any] | None:
    recorded = _generated_at(payload)
    if not recorded:
        return None
    return {
        "recorded_at_utc": recorded,
        "source": "closing_line_value",
        "positions_scored_attributed": payload.get("positions_scored", ""),
        "final_line_positions": payload.get("final_line_positions", ""),
        "mean_final_clv": _number(payload.get("mean_final_clv")),
        "positive_cohorts": _pipe(payload.get("positive_clv_cohorts")),
        "total_pnl_usdc": "",
        "decision_or_class_summary": _class_summary(payload.get("cohorts"), "clv_evidence") or str(payload.get("status") or ""),
    }


def _edge_row(payload: dict[str, Any]) -> dict[str, Any] | None:
    recorded = _generated_at(payload)
    if not recorded:
        return None
    cohorts = payload.get("cohorts", []) if isinstance(payload.get("cohorts", []), list) else []
    total_pnl = sum(safe_float(row.get("total_pnl_usdc")) or 0.0 for row in cohorts if isinstance(row, dict))
    positive = [
        str(row.get("signal_cohort") or "")
        for row in cohorts
        if isinstance(row, dict) and str(row.get("attribution_class") or "") == "positive_edge_confirmed"
    ]
    return {
        "recorded_at_utc": recorded,
        "source": "edge_attribution",
        "positions_scored_attributed": payload.get("attributed_positions", ""),
        "final_line_positions": "",
        "mean_final_clv": "",
        "positive_cohorts": _pipe(positive),
        "total_pnl_usdc": _number(total_pnl),
        "decision_or_class_summary": _class_summary(cohorts, "attribution_class") or str(payload.get("status") or ""),
    }


def _algo_row(payload: dict[str, Any]) -> dict[str, Any] | None:
    recorded = _generated_at(payload)
    if not recorded:
        return None
    selected = payload.get("selected") if isinstance(payload.get("selected"), dict) else {}
    decision = str(payload.get("decision") or payload.get("status") or "")
    selected_bits = []
    if selected:
        selected_bits.append(f"strategy={selected.get('strategy', '')}")
        selected_bits.append(f"params={selected.get('params', '')}")
    return {
        "recorded_at_utc": recorded,
        "source": "algo_sweep",
        "positions_scored_attributed": payload.get("combos_tested", ""),
        "final_line_positions": "",
        "mean_final_clv": "",
        "positive_cohorts": "",
        "total_pnl_usdc": _number(selected.get("validation_pnl_usdc") if selected else ""),
        "decision_or_class_summary": "|".join([decision, *selected_bits]) if selected_bits else decision,
    }


def _source_specs(cfg: EngineConfig) -> list[tuple[str, Path, Callable[[dict[str, Any]], dict[str, Any] | None]]]:
    return [
        ("closing_line_value", cfg.governance_root / "closing_line_value.json", _clv_row),
        ("edge_attribution", cfg.governance_root / "edge_attribution.json", _edge_row),
        ("algo_sweep", cfg.output_root / "polymarket_algo" / "algo_sweep_summary.json", _algo_row),
    ]


def _latest_recorded_by_source(rows: list[dict[str, str]]) -> dict[str, str]:
    latest: dict[str, str] = {}
    for row in rows:
        source = str(row.get("source") or "")
        recorded = str(row.get("recorded_at_utc") or "")
        if source and recorded:
            latest[source] = recorded
    return latest


def append_evidence_history(cfg: EngineConfig) -> dict[str, Any]:
    history_path = cfg.governance_root / "evidence_history.csv"
    existing = read_csv_rows(history_path)
    latest = _latest_recorded_by_source(existing)

    appended: list[dict[str, Any]] = []
    skipped_unchanged: list[str] = []
    missing_sources: list[str] = []
    for source, path, builder in _source_specs(cfg):
        payload = read_json(path, default=None)
        if not isinstance(payload, dict):
            missing_sources.append(source)
            continue
        row = builder(payload)
        if row is None:
            missing_sources.append(source)
            continue
        recorded = str(row.get("recorded_at_utc") or "")
        if latest.get(source) == recorded:
            skipped_unchanged.append(source)
            continue
        appended.append(row)

    if appended:
        write_csv(history_path, [*existing, *appended], fieldnames=HISTORY_FIELDS)
    elif not history_path.exists():
        write_csv(history_path, [], fieldnames=HISTORY_FIELDS)

    summary = {
        "status": "ok",
        "history_path": str(history_path),
        "existing_rows": len(existing),
        "appended_rows": len(appended),
        "total_rows": len(existing) + len(appended),
        "appended_sources": [row["source"] for row in appended],
        "skipped_unchanged_sources": skipped_unchanged,
        "missing_sources": missing_sources,
        "git_commit": git_commit_hash(),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    write_json(cfg.governance_root / "evidence_history_status.json", summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return append_evidence_history(load_config(config_path))
