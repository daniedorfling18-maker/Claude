"""WO-106 append-only reward-epoch time-series collection.

The VPS scheduler invokes this collector sequentially immediately after the
maker-carry study. Concurrent invocation is explicitly out of scope, so the
read-then-append idempotency guard does not require a second lock.

Collection only: missing or empty candidates, or a missing/inactive study,
append nothing and report status no_candidates/no_study; malformed numeric
fields are written through unchanged; no gate, sizing, or order surface reads
this artifact.
"""

from __future__ import annotations

from typing import Any

from .config import EngineConfig
from .utils import append_csv_rows, now_utc, read_csv_rows, read_json, write_json


WORK_ORDER = "WO-106"
CANDIDATE_COPY_FIELDS = [
    "condition_id",
    "token_id",
    "question",
    "band_eligible",
    "pot_usd_per_day",
    "estimated_reward_share",
    "gross_reward_usd_per_day",
    "competitor_score_bid",
    "competitor_score_ask",
    "mid_price",
]
SAMPLE_FIELDS = [
    "sampled_at_utc",
    "study_generated_at_utc",
    *CANDIDATE_COPY_FIELDS,
]
FAIL_SAFE_NOTE = (
    "Collection only: missing or empty candidates, or a missing/inactive study, "
    "append nothing and report status no_candidates/no_study; malformed numeric "
    "fields are written through unchanged; no gate, sizing, or order surface "
    "reads this artifact."
)


def run_reward_epoch_sample(cfg: EngineConfig) -> dict[str, Any]:
    """Append one idempotent sample per candidate for the current study run."""

    sampled_at = now_utc()
    out_root = cfg.output_root / "maker_carry"
    candidates_path = out_root / "maker_carry_candidates.csv"
    study_path = out_root / "maker_carry_study.json"
    samples_path = out_root / "reward_epoch_samples.csv"
    summary_path = out_root / "reward_epoch_sampler.json"

    candidates = read_csv_rows(candidates_path)
    study = read_json(study_path, default={}) or {}
    study_generated_at = (
        str(study.get("generated_at_utc") or "").strip()
        if isinstance(study, dict)
        else ""
    )
    study_status = (
        str(study.get("status") or "").strip().lower()
        if isinstance(study, dict)
        else ""
    )
    existing = read_csv_rows(samples_path)

    status = "ok"
    rows_to_append: list[dict[str, Any]] = []
    if not candidates:
        status = "no_candidates"
    elif not study_generated_at or study_status != "ok":
        status = "no_study"
    else:
        existing_keys = {
            (
                str(row.get("study_generated_at_utc") or "").strip(),
                str(row.get("condition_id") or "").strip(),
            )
            for row in existing
        }
        seen_conditions: set[str] = set()
        for candidate in candidates:
            condition_id = str(candidate.get("condition_id") or "").strip()
            if not condition_id or condition_id in seen_conditions:
                continue
            seen_conditions.add(condition_id)
            if (study_generated_at, condition_id) in existing_keys:
                continue
            rows_to_append.append(
                {
                    "sampled_at_utc": sampled_at,
                    "study_generated_at_utc": study_generated_at,
                    **{field: candidate.get(field, "") for field in CANDIDATE_COPY_FIELDS},
                }
            )

    if rows_to_append:
        append_csv_rows(samples_path, rows_to_append, fieldnames=SAMPLE_FIELDS)

    payload = {
        "work_order": WORK_ORDER,
        "status": status,
        "generated_at_utc": sampled_at,
        "study_generated_at_utc": study_generated_at,
        "study_status": study_status or None,
        "rows_sampled": len(rows_to_append),
        "total_rows": len(existing) + len(rows_to_append),
        "note": FAIL_SAFE_NOTE,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    write_json(summary_path, payload)
    return payload
