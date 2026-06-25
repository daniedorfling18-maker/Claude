from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import EngineConfig, load_config
from .utils import csv_columns, discover_files, find_first_column, infer_category, parse_timestamp, read_csv_rows, safe_float, write_csv, write_json

PRICE_HINTS = ["price", "midpoint", "probability", "prob", "bid", "ask", "last"]
LEAKAGE_HINTS = ["resolved", "winner", "winning", "payout", "settled", "final", "resolution"]


def issue(path: Path, severity: str, issue_type: str, message: str, category: str = "", market_id: str = "") -> dict[str, Any]:
    return {"file_path": str(path), "category": category or infer_category(path), "market_id": market_id, "severity": severity, "issue_type": issue_type, "message": message}


def quality_for_file(path: Path, cfg: EngineConfig) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    columns = csv_columns(path)
    rows = read_csv_rows(path)
    category = infer_category(path)
    schema = cfg.raw.get("schema", {})
    timestamp_col = find_first_column(columns, schema.get("timestamp_fields", ["snapshot_timestamp", "timestamp", "collected_at", "updated_at"]))
    market_col = find_first_column(columns, schema.get("market_id_fields", ["market_id", "condition_id", "id", "slug"]))
    token_col = find_first_column(columns, schema.get("token_id_fields", ["token_id", "asset_id", "outcome_token_id"]))
    if not columns:
        return [issue(path, "blocker", "empty_or_unreadable_csv", "CSV has no readable header", category)]
    if not timestamp_col:
        issues.append(issue(path, "blocker", "missing_timestamp", "No point-in-time timestamp column found", category))
    if not market_col:
        issues.append(issue(path, "blocker", "missing_market_identifier", "No market identifier column found", category))
    if not token_col:
        issues.append(issue(path, "high", "missing_token_identifier", "No token identifier column found", category))
    seen_rows: set[tuple[tuple[str, str], ...]] = set()
    seen_snapshot_keys: set[tuple[str, str, str]] = set()
    market_timestamps: dict[str, set[str]] = {}
    for idx, row in enumerate(rows):
        market_id = row.get(market_col or "", "")
        if timestamp_col:
            ts_value = row.get(timestamp_col, "")
            ts = parse_timestamp(ts_value)
            if not ts:
                issues.append(issue(path, "high", "unparseable_timestamp", f"Row {idx + 2} has unparseable timestamp {ts_value!r}", category, market_id))
            else:
                market_timestamps.setdefault(market_id, set()).add(ts.isoformat())
        row_key = tuple(sorted(row.items()))
        if row_key in seen_rows:
            issues.append(issue(path, "medium", "duplicate_row", f"Duplicate row at CSV row {idx + 2}", category, market_id))
        seen_rows.add(row_key)
        if timestamp_col and market_col and token_col:
            snap_key = (row.get(timestamp_col, ""), row.get(market_col, ""), row.get(token_col, ""))
            if snap_key in seen_snapshot_keys:
                issues.append(issue(path, "medium", "duplicate_snapshot", f"Duplicate market/token/timestamp at row {idx + 2}", category, market_id))
            seen_snapshot_keys.add(snap_key)
        for col, value in row.items():
            col_l = col.lower()
            number = safe_float(value)
            if number is None:
                continue
            if any(h in col_l for h in PRICE_HINTS) and not (0 <= number <= 1):
                if "size" not in col_l and "volume" not in col_l and "liquidity" not in col_l:
                    issues.append(issue(path, "high", "price_out_of_bounds", f"{col}={value} outside [0,1] at row {idx + 2}", category, market_id))
            if any(h in col_l for h in ["liquidity", "volume", "depth", "minimum_order", "min_order"]):
                if number < 0:
                    issues.append(issue(path, "high", "negative_microstructure_value", f"{col}={value} is negative at row {idx + 2}", category, market_id))
        bid_col = find_first_column(columns, ["best_bid", "bid"])
        ask_col = find_first_column(columns, ["best_ask", "ask"])
        bid = safe_float(row.get(bid_col or ""))
        ask = safe_float(row.get(ask_col or ""))
        if bid is not None and ask is not None and bid > ask:
            issues.append(issue(path, "high", "bid_greater_than_ask", f"Bid {bid} exceeds ask {ask} at row {idx + 2}", category, market_id))
    for market_id, times in market_timestamps.items():
        if market_id and len(times) <= 1:
            issues.append(issue(path, "medium", "single_timestamp_market", "Market has only one snapshot timestamp", category, market_id))
    for col in columns:
        col_l = col.lower()
        if any(h in col_l for h in LEAKAGE_HINTS):
            issues.append(issue(path, "informational", "potential_leakage_column", f"Column {col} may reveal final outcome or resolution state", category))
    if not any("outcome" in c.lower() or "winner" in c.lower() or "resolved" in c.lower() for c in columns):
        issues.append(issue(path, "medium", "missing_resolution_fields", "No obvious resolution or outcome columns present", category))
    return issues


def data_quality(cfg: EngineConfig, allow_warnings: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    files = discover_files(cfg.data_root, ["outputs/polymarket_wide/**/raw_market_snapshots.csv", "outputs/polymarket_fixed/**/raw_market_snapshots.csv"])
    issues: list[dict[str, Any]] = []
    if not files:
        issues.append({"file_path": "", "category": "", "market_id": "", "severity": "blocker", "issue_type": "no_raw_snapshots", "message": "No raw_market_snapshots.csv files discovered"})
    for path in files:
        issues.extend(quality_for_file(path, cfg))
    summary = {sev + "_count": sum(1 for i in issues if i["severity"] == sev) for sev in ["blocker", "high", "medium", "low", "informational"]}
    summary.update({"file_count": len(files), "issue_count": len(issues), "training_allowed": summary.get("blocker_count", 0) == 0})
    write_csv(cfg.governance_root / "data_quality_report.csv", issues)
    write_json(cfg.governance_root / "data_quality_summary.json", summary)
    if summary.get("blocker_count", 0) and not allow_warnings:
        raise RuntimeError(
            f"Data-quality blockers present: {summary['blocker_count']} blocker(s). "
            "Reports were written; rerun with allow_warnings=True only for inspection."
        )
    return issues, summary


def main(config_path: str, allow_warnings: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return data_quality(load_config(config_path), allow_warnings=allow_warnings)
