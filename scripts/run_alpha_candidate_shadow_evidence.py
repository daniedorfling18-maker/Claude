#!/usr/bin/env python3
"""Open/update shadow-only evidence for validated alpha candidates.

This is deliberately not paper trading and not live execution. It takes rows that
already passed the alpha validation layer but are still blocked by later paper
trading governance gates, marks them as shadow candidates, and feeds them through
the existing shadow cohort ledger.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from polymarket_predictive_engine.config import load_config  # noqa: E402
from polymarket_predictive_engine.shadow_cohort import update_shadow_cohort_evidence  # noqa: E402
from polymarket_predictive_engine.utils import boolish, now_utc, read_csv_rows, safe_float, write_csv, write_json  # noqa: E402

EXCLUDED_SHADOW_FAMILIES = {
    "unknown",
    "crypto_btc_updown_5m",
    "crypto_sol_updown_5m",
    "crypto_xrp_updown_5m",
}


def _valid_alpha_shadow_candidate(row: dict[str, Any]) -> bool:
    category = str(row.get("category") or "unknown").strip().lower()
    if category in EXCLUDED_SHADOW_FAMILIES:
        return False
    if not boolish(row.get("alpha_trade_candidate")):
        return False
    if not boolish(row.get("validation_layer_pass")):
        return False
    if not boolish(row.get("microstructure_filter_pass")):
        return False
    if not boolish(row.get("crypto_model_gate_pass", True)):
        return False
    edge = safe_float(row.get("edge_lower_bound"))
    return edge is not None and edge > 0


def _shadow_row_from_alpha(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["shadow_trade_candidate"] = True
    out["shadow_source"] = "alpha_trade_candidate_governance_holdout"
    out["shadow_candidate_reason"] = "alpha_candidate_shadow_evidence_only_no_paper_trade"
    if not str(out.get("shadow_priority_score") or "").strip():
        out["shadow_priority_score"] = out.get("edge_lower_bound", "")
    return out


def _reserve_shadow_capacity(cfg: Any, alpha_shadow_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Raise in-memory shadow capacity for this evidence-only run.

    The normal shadow ledger intentionally caps long-horizon positions. That is
    useful for unattended operation, but it can silently prevent validated alpha
    candidates from being recorded when older stale research positions already
    fill the long-horizon bucket. This override affects only this script's
    in-memory config and only the shadow ledger; it does not change paper or live
    trading gates and does not write to the YAML config.
    """
    settings = cfg.raw.setdefault("shadow_cohort_validation", {})
    before = {
        "maximum_open_positions": int(settings.get("maximum_open_positions", 60) or 60),
        "maximum_long_horizon_open_positions": int(settings.get("maximum_long_horizon_open_positions", 10) or 10),
        "candidate_limit_per_cycle": int(settings.get("candidate_limit_per_cycle", 16) or 16),
    }
    desired_extra = max(1, len(alpha_shadow_rows)) if alpha_shadow_rows else 0
    settings["maximum_open_positions"] = max(before["maximum_open_positions"], 60, before["maximum_open_positions"] + desired_extra)
    settings["maximum_long_horizon_open_positions"] = max(
        before["maximum_long_horizon_open_positions"],
        25,
        before["maximum_long_horizon_open_positions"] + desired_extra,
    )
    settings["candidate_limit_per_cycle"] = max(before["candidate_limit_per_cycle"], len(alpha_shadow_rows) + 8)
    return {
        "before": before,
        "after": {
            "maximum_open_positions": settings["maximum_open_positions"],
            "maximum_long_horizon_open_positions": settings["maximum_long_horizon_open_positions"],
            "candidate_limit_per_cycle": settings["candidate_limit_per_cycle"],
        },
        "scope": "in_memory_shadow_only_no_yaml_change",
    }


def run(config_path: str = "polymarket_predictive_config.example.yaml") -> dict[str, Any]:
    cfg = load_config(config_path)
    alpha_path = cfg.output_root / "polymarket_predictions" / "mispricing_alpha_scores.csv"
    rows = read_csv_rows(alpha_path)
    prepared: list[dict[str, Any]] = []
    alpha_shadow_rows: list[dict[str, Any]] = []
    existing_shadow_rows = 0
    existing_near_miss_rows = 0
    for row in rows:
        if boolish(row.get("shadow_trade_candidate")):
            prepared.append(row)
            existing_shadow_rows += 1
            continue
        if boolish(row.get("near_miss_learning_candidate")):
            prepared.append(row)
            existing_near_miss_rows += 1
            continue
        if _valid_alpha_shadow_candidate(row):
            shadow_row = _shadow_row_from_alpha(row)
            prepared.append(shadow_row)
            alpha_shadow_rows.append(shadow_row)

    capacity_override = _reserve_shadow_capacity(cfg, alpha_shadow_rows)
    candidate_file = cfg.governance_root / "alpha_candidate_shadow_evidence_inputs.csv"
    write_csv(candidate_file, alpha_shadow_rows)
    shadow_summary = update_shadow_cohort_evidence(cfg, prepared)
    # WO-143b.1 F4 (caller-honesty contract): when the update does not WRITE --
    # `skipped_shadow_lock_held` from WO-143b's internal lock, any other
    # `skipped_*`, or `disabled` -- nothing reached the shadow ledger, so every
    # count that claims rows were FORWARDED/SENT must be 0. `alpha_score_rows`
    # and `alpha_shadow_input_rows` describe this script's own input and CSV
    # output, which are written regardless, so they are not forwarded-count
    # claims and do not change. The status is surfaced verbatim via
    # `shadow_summary` below.
    #
    # ALLOW-LISTED ON THE WRITING SIDE, not deny-listed on the non-writing one
    # (Codex P2 wave-42). The predicate was `startswith("skipped_")`, which does
    # not match `disabled` -- so with `shadow_cohort_validation.enabled` false
    # the updater returned without writing either ledger while this receipt
    # reported every prepared row as forwarded and sent. A disabled deployment
    # produced a receipt claiming delivery of evidence that was never recorded.
    # Deny-listing is the wrong shape here: a status added later defaults to
    # "claim delivery", and the failure is silent. `computed` is the only
    # outcome that writes, so it is the only outcome that may claim to have.
    shadow_status = shadow_summary.get("status") if isinstance(shadow_summary, dict) else None
    shadow_did_not_write = shadow_status != "computed"
    family_counts = Counter(str(row.get("category") or "unknown") for row in alpha_shadow_rows)
    cohort_counts = Counter(str(row.get("signal_cohort") or "unknown") for row in alpha_shadow_rows)
    payload = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "source_file": str(alpha_path),
        "alpha_score_rows": len(rows),
        "alpha_shadow_input_rows": len(alpha_shadow_rows),
        "existing_shadow_rows_forwarded": 0 if shadow_did_not_write else existing_shadow_rows,
        "existing_near_miss_rows_forwarded": 0 if shadow_did_not_write else existing_near_miss_rows,
        "prepared_rows_sent_to_shadow_ledger": 0 if shadow_did_not_write else len(prepared),
        "shadow_update_status": shadow_status or "not_run",
        "alpha_shadow_family_counts": dict(sorted(family_counts.items())),
        "alpha_shadow_cohort_counts": dict(sorted(cohort_counts.items())),
        "candidate_file": str(candidate_file),
        "capacity_override": capacity_override,
        "shadow_summary": shadow_summary,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    write_json(cfg.governance_root / "alpha_candidate_shadow_evidence_summary.json", payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return payload


def main() -> int:
    config_path = sys.argv[1] if len(sys.argv) > 1 else "polymarket_predictive_config.example.yaml"
    run(config_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
