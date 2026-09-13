"""WO-172: why each starved input path is empty, said in words the reader can act on.

Four research paths report nothing in every snapshot: wallet fills, sharp-anchor joins,
calibration joins, and implication legs. "Zero" is not one fact but several, and they have
opposite meanings. A lane whose collector never ran is *untested*; a lane whose complete inputs
were scanned and found nothing is a *measured negative*. Reading the first as the second turns a
broken pipe into evidence.

This classifies each path into one closed set, `unknown` first, and states the evidence rule that
follows from it. It reads every artifact and writes only its own; it changes no gate and no
producer. `stale` is a separate flag, read from the watchdog's own registered ceilings rather than
re-typed here, because a looser ceiling would push it toward `false` — the favourable direction.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import EngineConfig, load_config
from .degraded_state_watchdog import REGISTERED_JOB_FRESHNESS_MAX_SECONDS
from .utils import now_utc, parse_timestamp, read_csv_rows, safe_float, write_json

UNKNOWN = "unknown"
INGESTION_FAILURE = "ingestion_failure"
COVERAGE_LIMITATION = "coverage_limitation"
JOIN_FAILURE = "join_failure"
MEASURED_NEGATIVE = "measured_negative"
# `unknown` is first and wins: a missing artifact can never read as a measured negative.
CLASSIFICATIONS = (UNKNOWN, INGESTION_FAILURE, COVERAGE_LIMITATION, JOIN_FAILURE, MEASURED_NEGATIVE)

ABSENCE_RULE = "absence of evidence: untested"
MEASURED_RULE = "measured negative within stated scope"
EVIDENCE_RULES = {
    INGESTION_FAILURE: ABSENCE_RULE,
    COVERAGE_LIMITATION: ABSENCE_RULE,
    JOIN_FAILURE: ABSENCE_RULE,
    MEASURED_NEGATIVE: MEASURED_RULE,
    UNKNOWN: "unknown: the artifact could not be read as a measurement",
}

SCHEDULED = "scheduled"
MANUAL_ONLY = "manual_only"
NO_PRODUCER = "no_producer_registered"

GOVERNANCE_LANE = "governance_refresh"
TRADE_PRINTS_LANE = "trade_prints"

LEDGER_BASIS_MANIFEST = "manifest"
LEDGER_BASIS_VPS = "vps"
LEDGER_BASIS_UNKNOWN = "unknown"

OUTPUT_RELATIVE_PATH = "data_coverage_report.json"


def _finite(value: Any) -> bool:
    number = safe_float(value)
    return number is not None and math.isfinite(number)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _age_seconds(generated_at: Any, run_clock: str) -> float | None:
    produced = parse_timestamp(generated_at)
    now = parse_timestamp(run_clock)
    if produced is None or now is None:
        return None
    return (now - produced).total_seconds()


def _staleness(payload: dict[str, Any] | None, lane: str | None, run_clock: str) -> dict[str, Any]:
    """`stale` is a second flag, never a classification. An unreadable clock is stale with a null age."""
    ceiling = REGISTERED_JOB_FRESHNESS_MAX_SECONDS.get(lane) if lane else None
    generated_at = (payload or {}).get("generated_at_utc")
    age = _age_seconds(generated_at, run_clock) if payload is not None else None
    if age is None or ceiling is None:
        return {
            "generated_at_utc": generated_at,
            "age_seconds": None,
            "freshness_lane": lane,
            "freshness_ceiling_seconds": ceiling,
            "stale": True,
        }
    return {
        "generated_at_utc": generated_at,
        "age_seconds": round(age, 3),
        "freshness_lane": lane,
        "freshness_ceiling_seconds": ceiling,
        "stale": bool(age > ceiling),
    }


def _entry(
    *,
    artifact: str,
    fields_read: list[str],
    producer: str | None,
    producer_state: str,
    cadence: int | None,
    payload: dict[str, Any] | None,
    lane: str | None,
    run_clock: str,
    classification: str,
    population: Any = None,
    eligible: Any = None,
    excluded: Any = None,
    missing_outcomes: Any = None,
    scope: str | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    return {
        "artifact": artifact,
        "fields_read": fields_read,
        "producer": producer,
        "producer_state": producer_state,
        "producer_cadence_seconds": cadence,
        "population": population,
        "eligible": eligible,
        "excluded": excluded,
        "missing_outcomes": missing_outcomes,
        "classification": classification,
        "evidence_rule": EVIDENCE_RULES[classification],
        "scope": scope,
        "detail": detail,
        **_staleness(payload, lane, run_clock),
    }


def _smart_flow(cfg: EngineConfig, run_clock: str) -> list[dict[str, Any]]:
    path = cfg.governance_root / "smart_flow_clv.json"
    payload = _read_json(path)
    fills = cfg.data_root / "inputs" / "polymarket" / "public_wallet_fills.csv"
    fills_rows = len(read_csv_rows(fills)) if Path(fills).exists() else 0
    seen = (payload or {}).get("fills_seen")
    if payload is None or not _finite(seen):
        classification = UNKNOWN
    elif safe_float(seen) == 0 and fills_rows == 0:
        classification = INGESTION_FAILURE
    elif safe_float(seen) == 0:
        classification = COVERAGE_LIMITATION
    else:
        classification = MEASURED_NEGATIVE if safe_float((payload or {}).get("fills_scored")) == 0 else UNKNOWN
    return [
        _entry(
            artifact="polymarket_model_governance/smart_flow_clv.json",
            fields_read=["fills_seen", "fills_scored", "wallets"],
            producer="smart-flow-clv (manual CLI)",
            # No scheduled producer: refresh_governance registers none for this lane.
            producer_state=MANUAL_ONLY,
            cadence=None,
            payload=payload,
            lane=GOVERNANCE_LANE,
            run_clock=run_clock,
            classification=classification,
            population=fills_rows,
            eligible=safe_float(seen) if _finite(seen) else None,
            detail="H3's input has no registered producer anywhere in the repository; its only reference is the reader.",
        ),
        _entry(
            artifact="inputs/polymarket/public_wallet_fills.csv",
            fields_read=["row count"],
            producer=None,
            producer_state=NO_PRODUCER,
            cadence=None,
            payload=None,
            lane=None,
            run_clock=run_clock,
            classification=INGESTION_FAILURE if fills_rows == 0 else UNKNOWN,
            population=fills_rows,
            eligible=fills_rows,
            detail="No collector writes this path; building one is a separate work order.",
        ),
    ]


def _sharp_anchor(cfg: EngineConfig, run_clock: str) -> dict[str, Any]:
    payload = _read_json(cfg.governance_root / "sharp_anchor_coverage.json")
    fetched = (payload or {}).get("total_rows_fetched")
    joined = (payload or {}).get("total_rows_joined")
    if payload is None or not (_finite(fetched) and _finite(joined)):
        classification = UNKNOWN
    elif safe_float(fetched) == 0:
        classification = INGESTION_FAILURE
    elif safe_float(joined) == 0:
        classification = COVERAGE_LIMITATION
    else:
        classification = UNKNOWN
    return _entry(
        artifact="polymarket_model_governance/sharp_anchor_coverage.json",
        fields_read=["total_rows_fetched", "total_rows_joined", "total_stale_rows"],
        producer="refresh-governance",
        producer_state=SCHEDULED,
        cadence=21600,
        payload=payload,
        lane=GOVERNANCE_LANE,
        run_clock=run_clock,
        classification=classification,
        population=int(safe_float(fetched)) if _finite(fetched) else None,
        eligible=int(safe_float(joined)) if _finite(joined) else None,
        excluded={"stale_rows": int(safe_float((payload or {}).get("total_stale_rows")))} if _finite((payload or {}).get("total_stale_rows")) else None,
        detail="Rows were fetched and none could be joined to a market; the anchor was never tested against a price.",
    )


def _calibration(cfg: EngineConfig, run_clock: str) -> dict[str, Any]:
    payload = _read_json(cfg.governance_root / "family_calibration_scorecard.json")
    rejected = (payload or {}).get("rejected_join_rows")
    joined = (payload or {}).get("clean_settled_joined_rows")
    if payload is None or not (_finite(rejected) and _finite(joined)):
        classification = UNKNOWN
    elif safe_float(joined) == 0 and safe_float(rejected) > 0:
        classification = JOIN_FAILURE
    elif safe_float(joined) == 0:
        classification = INGESTION_FAILURE
    else:
        classification = UNKNOWN
    return _entry(
        artifact="polymarket_model_governance/family_calibration_scorecard.json",
        fields_read=["rejected_join_rows", "clean_settled_joined_rows"],
        producer="refresh-governance",
        producer_state=SCHEDULED,
        cadence=21600,
        payload=payload,
        lane=GOVERNANCE_LANE,
        run_clock=run_clock,
        classification=classification,
        population=int(safe_float(rejected)) if _finite(rejected) else None,
        eligible=int(safe_float(joined)) if _finite(joined) else None,
        missing_outcomes=int(safe_float(rejected)) if _finite(rejected) else None,
        detail="Joins were attempted and every one was rejected; no calibration was measured.",
    )


def _implication(cfg: EngineConfig, run_clock: str) -> dict[str, Any]:
    payload = _read_json(cfg.output_root / "implication_consistency" / "implication_scan.json")
    scanned = (payload or {}).get("events_scanned")
    legs = (payload or {}).get("classified_legs")
    if payload is None or not (_finite(scanned) and _finite(legs)):
        classification = UNKNOWN
    elif safe_float(scanned) == 0:
        classification = INGESTION_FAILURE
    elif safe_float(legs) == 0:
        classification = COVERAGE_LIMITATION
    else:
        classification = MEASURED_NEGATIVE if safe_float((payload or {}).get("flagged_deviations")) == 0 else UNKNOWN
    return _entry(
        artifact="implication_consistency/implication_scan.json",
        fields_read=["events_scanned", "classified_legs", "flagged_deviations"],
        producer="trade_prints",
        producer_state=SCHEDULED,
        cadence=900,
        payload=payload,
        lane=TRADE_PRINTS_LANE,
        run_clock=run_clock,
        classification=classification,
        population=int(safe_float(scanned)) if _finite(scanned) else None,
        eligible=int(safe_float(legs)) if _finite(legs) else None,
        detail="Events were scanned and no leg could be classified; no implication was ever tested.",
    )


def _event_group(cfg: EngineConfig, run_clock: str) -> dict[str, Any]:
    payload = _read_json(cfg.output_root / "event_group_consistency" / "event_group_scan.json")
    complete = (payload or {}).get("groups_with_complete_ask_side")
    flagged = (payload or {}).get("flagged_deviations")
    if payload is None or not (_finite(complete) and _finite(flagged)):
        classification = UNKNOWN
    elif safe_float(complete) == 0:
        classification = COVERAGE_LIMITATION
    elif safe_float(flagged) == 0:
        classification = MEASURED_NEGATIVE
    else:
        classification = UNKNOWN
    scope = f"within {int(safe_float(complete))} complete-ask groups" if _finite(complete) else None
    return _entry(
        artifact="event_group_consistency/event_group_scan.json",
        fields_read=["groups_with_complete_ask_side", "flagged_deviations", "max_executable_basket_usd"],
        producer="trade_prints",
        producer_state=SCHEDULED,
        cadence=900,
        payload=payload,
        lane=TRADE_PRINTS_LANE,
        run_clock=run_clock,
        classification=classification,
        population=int(safe_float(complete)) if _finite(complete) else None,
        eligible=int(safe_float(complete)) if _finite(complete) else None,
        scope=scope,
        detail="Complete-ask groups were scanned and no deviation was found; this is a measured negative inside that scope only.",
    )


def _ledger_completeness(export_manifest: dict[str, Any] | None, ledger_source: str | None) -> tuple[str, dict[str, Any] | None]:
    """A figure is read only from a ledger known to be whole."""
    if ledger_source == LEDGER_BASIS_VPS:
        return LEDGER_BASIS_VPS, None
    if export_manifest is None:
        return LEDGER_BASIS_UNKNOWN, None
    for entry in export_manifest.get("files", []) or []:
        if str(entry.get("path", "")).endswith("shadow_positions.csv"):
            return LEDGER_BASIS_MANIFEST, entry
    return LEDGER_BASIS_UNKNOWN, None


def _pnl_attribution_check(
    cfg: EngineConfig,
    export_manifest: dict[str, Any] | None,
    ledger_source: str | None,
) -> dict[str, Any]:
    """Missing attribution must not remove losses from the total.

    Every closed row counts toward the total; rows whose cohort is blank are reported as
    unattributed rather than dropped. The cohort file's own figure is realised plus unrealised at
    mark, so it is reported under its own name and marked not comparable."""
    basis, entry = _ledger_completeness(export_manifest, ledger_source)
    block: dict[str, Any] = {"ledger_completeness_basis": basis}
    if basis == LEDGER_BASIS_UNKNOWN:
        block["state"] = "unknown_no_manifest"
        block["detail"] = "no export manifest and no --ledger-source; a truncated ledger would understate every figure"
        return block
    if basis == LEDGER_BASIS_MANIFEST and bool((entry or {}).get("truncated")):
        block["state"] = "unknown_truncated_input"
        block["source_lines_after_header"] = (entry or {}).get("source_lines_after_header")
        block["exported_lines_after_header"] = (entry or {}).get("exported_lines_after_header")
        block["detail"] = "the manifest marks shadow_positions.csv an extract; no figure is stated from it"
        return block

    rows = read_csv_rows(cfg.output_root / "polymarket_shadow" / "shadow_positions.csv")
    closed = [row for row in rows if str(row.get("status") or "").strip().lower() == "closed"]
    malformed = [row for row in closed if not _finite(row.get("realised_pnl_usdc"))]
    if malformed:
        block["state"] = "unknown_malformed_row"
        block["malformed_closed_rows"] = len(malformed)
        block["closed_rows"] = len(closed)
        block["detail"] = "a closed row carries a blank or non-finite realised P&L; no figure is stated"
        return block

    attributed: dict[str, float] = {}
    unattributed = 0.0
    total = 0.0
    for row in closed:
        value = float(safe_float(row.get("realised_pnl_usdc")))
        total += value
        cohort = str(row.get("signal_cohort") or "").strip()
        if cohort:
            attributed[cohort] = round(attributed.get(cohort, 0.0) + value, 6)
        else:
            unattributed += value
    block.update(
        {
            "state": "ok",
            "closed_rows": len(closed),
            "closed_total_pnl_usdc": round(total, 6),
            "attributed_by_cohort": dict(sorted(attributed.items())),
            "unattributed_pnl_usdc": round(unattributed, 6),
            "identity": "closed_total_pnl_usdc == sum(attributed_by_cohort) + unattributed_pnl_usdc",
        }
    )
    cohort_payload = _read_json(cfg.governance_root / "shadow_signal_cohort_pnl.json")
    cohorts = (cohort_payload or {}).get("cohorts") or []
    shadow_sum = sum(float(safe_float(c.get("shadow_total_pnl_usdc")) or 0.0) for c in cohorts if isinstance(c, dict))
    block["cohort_file_shadow_total_pnl_usdc"] = round(shadow_sum, 6) if cohorts else None
    block["comparable"] = False
    block["comparability_note"] = (
        "the cohort file's figure is realised PLUS unrealised at mark over all positions; the "
        "ledger figure above is realised P&L on closed rows only. They are different quantities."
    )
    return block


def build_data_coverage_report(
    cfg: EngineConfig,
    *,
    export_manifest_path: str | Path | None = None,
    ledger_source: str | None = None,
) -> dict[str, Any]:
    run_clock = now_utc()
    export_manifest = _read_json(Path(export_manifest_path)) if export_manifest_path else None
    paths = [
        *_smart_flow(cfg, run_clock),
        _sharp_anchor(cfg, run_clock),
        _calibration(cfg, run_clock),
        _implication(cfg, run_clock),
        _event_group(cfg, run_clock),
    ]
    payload = {
        "work_order": "WO-172",
        "status": "ok",
        "generated_at_utc": run_clock,
        "export_manifest_path": str(export_manifest_path) if export_manifest_path else None,
        "classification_set": list(CLASSIFICATIONS),
        "classification_note": (
            "first match wins, and `unknown` is first: a missing, unparseable or non-finite "
            "artifact can never be read as a measured negative"
        ),
        "paths": paths,
        "counts_by_classification": {
            name: sum(1 for entry in paths if entry["classification"] == name) for name in CLASSIFICATIONS
        },
        "stale_paths": [entry["artifact"] for entry in paths if entry["stale"]],
        "pnl_attribution_check": _pnl_attribution_check(cfg, export_manifest, ledger_source),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    write_json(cfg.governance_root / OUTPUT_RELATIVE_PATH, payload)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return build_data_coverage_report(load_config(config_path))
