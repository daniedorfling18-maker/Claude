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

    candidate_file = cfg.governance_root / "alpha_candidate_shadow_evidence_inputs.csv"
    write_csv(candidate_file, alpha_shadow_rows)
    shadow_summary = update_shadow_cohort_evidence(cfg, prepared)
    family_counts = Counter(str(row.get("category") or "unknown") for row in alpha_shadow_rows)
    cohort_counts = Counter(str(row.get("signal_cohort") or "unknown") for row in alpha_shadow_rows)
    payload = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "source_file": str(alpha_path),
        "alpha_score_rows": len(rows),
        "alpha_shadow_input_rows": len(alpha_shadow_rows),
        "existing_shadow_rows_forwarded": existing_shadow_rows,
        "existing_near_miss_rows_forwarded": existing_near_miss_rows,
        "prepared_rows_sent_to_shadow_ledger": len(prepared),
        "alpha_shadow_family_counts": dict(sorted(family_counts.items())),
        "alpha_shadow_cohort_counts": dict(sorted(cohort_counts.items())),
        "candidate_file": str(candidate_file),
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
