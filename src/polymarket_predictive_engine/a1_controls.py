"""WO-81 custody Amendment A1 balance-discipline controls.

The signed amendment uses one project account and requires excess capital to
be swept manually.  This module calculates that advice from the latest clean
three-way reconciliation and the tightest active stage cap.  It has no venue
client, credential reader, signer, transfer, withdrawal, broker, or order path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .config import EngineConfig
from .utils import parse_timestamp, read_json, safe_float, write_json, write_text_atomic


WORK_ORDER = "WO-81"
OUTPUT_FILE = "execution/a1_sweep_advisory.json"
OUTPUT_MARKDOWN = "execution/a1_sweep_advisory.md"
REGISTERED_SWEEP_THRESHOLD_USD = 5.0
REGISTERED_RECONCILIATION_MAX_AGE_SECONDS = 26 * 3600
REGISTERED_STAGE0_CAPITAL_USD = 100.0


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _enabled(value: Any) -> bool:
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _positive(value: Any, default: float) -> float:
    parsed = safe_float(value)
    return parsed if parsed is not None and parsed > 0 else default


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = _mapping(cfg.raw.get("a1_balance_discipline"))
    return {
        "enabled": _enabled(raw.get("enabled", True)),
        # Both values are maxima. Overrides may trigger earlier, never later.
        "sweep_threshold_usd": min(
            REGISTERED_SWEEP_THRESHOLD_USD,
            _positive(raw.get("sweep_threshold_usd"), REGISTERED_SWEEP_THRESHOLD_USD),
        ),
        "reconciliation_max_age_seconds": min(
            REGISTERED_RECONCILIATION_MAX_AGE_SECONDS,
            _positive(
                raw.get("reconciliation_max_age_seconds"),
                REGISTERED_RECONCILIATION_MAX_AGE_SECONDS,
            ),
        ),
    }


def _as_of(value: datetime | str | None) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    parsed = parse_timestamp(value) if value else None
    return parsed.astimezone(timezone.utc) if parsed is not None else datetime.now(timezone.utc)


def _stage_cap(cfg: EngineConfig) -> tuple[float, str]:
    policy = _mapping(read_json(cfg.output_root / "maker_carry" / "decision_policy.json", default={}) or {})
    configured = _mapping(cfg.raw.get("decision_policy"))
    candidates: list[tuple[float, str]] = []
    for value, source in (
        (_mapping(policy.get("sizing")).get("binding_capital_usd"), "decision_policy.sizing.binding_capital_usd"),
        (_mapping(policy.get("ladder")).get("ladder_capital_usd"), "decision_policy.ladder.ladder_capital_usd"),
    ):
        parsed = safe_float(value)
        if parsed is not None and parsed > 0:
            candidates.append((float(parsed), source))
    if candidates:
        value, source = min(candidates, key=lambda item: item[0])
        return round(value, 8), source
    configured_stage0 = safe_float(configured.get("stage0_capital_usd"))
    if configured_stage0 is not None and configured_stage0 > 0:
        return (
            round(min(REGISTERED_STAGE0_CAPITAL_USD, configured_stage0), 8),
            "effective_config.decision_policy.stage0_capital_usd",
        )
    return REGISTERED_STAGE0_CAPITAL_USD, "registered_stage0_default"


def _reconciled_nav(payload: Mapping[str, Any]) -> tuple[float | None, list[float]]:
    values = [
        value
        for value in (
            safe_float(payload.get("internal_nav_usd")),
            safe_float(payload.get("data_api_nav_usd")),
            safe_float(payload.get("onchain_nav_usd")),
        )
        if value is not None and value >= 0
    ]
    # A sweep decision needs all three WO-62 views. The minimum avoids advising
    # a withdrawal from capital that only one view believes exists.
    return (min(values), values) if len(values) == 3 else (None, values)


def _withdrawable_cash(payload: Mapping[str, Any]) -> tuple[float | None, list[float]]:
    internal = _mapping(payload.get("internal"))
    data_api = _mapping(payload.get("data_api"))
    onchain = _mapping(payload.get("onchain"))
    values = [
        value
        for value in (
            safe_float(internal.get("cash_usd")),
            safe_float(data_api.get("reconstructed_cash_usd")),
            safe_float(onchain.get("pusd_balance_usd")),
        )
        if value is not None and value >= 0
    ]
    return (min(values), values) if values else (None, [])


def _write_markdown(path: Path, payload: Mapping[str, Any]) -> None:
    def money(value: Any) -> str:
        parsed = safe_float(value)
        return "UNKNOWN" if parsed is None else f"${parsed:,.2f}"

    lines = [
        "# Amendment A1 sweep advisory",
        "",
        f"Generated: `{payload.get('generated_at_utc')}`  ",
        f"State: **{payload.get('status')}**",
        "",
        f"- Reconciled project-account NAV: {money(payload.get('reconciled_nav_usd'))}",
        f"- Active stage cap: {money(payload.get('active_stage_cap_usd'))}",
        f"- Registered trigger buffer: {money(payload.get('sweep_threshold_usd'))}",
        f"- Withdrawable cash observed: {money(payload.get('withdrawable_cash_usd'))}",
        f"- Advice: **{payload.get('advice')}**",
        "",
        "Human action only. This artifact cannot transfer, withdraw, place, amend, cancel, sign, or authenticate anything.",
        "Use `docs/A1_WITHDRAWAL_AND_EXIT_RAIL_RUNBOOK.md` for the registered exit-rail test and WO-63 cost capture.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, "\n".join(lines))


def build_a1_sweep_advisory(
    cfg: EngineConfig,
    *,
    as_of: datetime | str | None = None,
) -> dict[str, Any]:
    """Render tighten-only manual sweep advice from current reconciled NAV."""

    observed_at = _as_of(as_of)
    generated_at = observed_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    settings = _settings(cfg)
    reconciliation_path = cfg.output_root / "performance" / "wallet_reconciliation.json"
    reconciliation = _mapping(read_json(reconciliation_path, default={}) or {})
    stage_cap, cap_source = _stage_cap(cfg)
    threshold = float(settings["sweep_threshold_usd"])
    status = "disabled"
    reason = "a1_balance_discipline is disabled"
    nav: float | None = None
    withdrawable: float | None = None
    sweep_amount = 0.0
    reconciliation_age: float | None = None

    if settings["enabled"]:
        status = "unknown"
        reason = "wallet reconciliation is missing"
        generated = parse_timestamp(reconciliation.get("generated_at_utc"))
        if generated is not None:
            reconciliation_age = max(0.0, (observed_at - generated).total_seconds())
        reconciliation_state = str(
            reconciliation.get("reconciliation_status") or reconciliation.get("status") or ""
        ).strip().lower()
        if reconciliation and generated is None:
            reason = "wallet reconciliation timestamp is missing"
        elif reconciliation_age is not None and reconciliation_age > float(settings["reconciliation_max_age_seconds"]):
            reason = "wallet reconciliation is too old for withdrawal advice"
        elif not reconciliation_state:
            reason = "wallet reconciliation status is missing"
        elif reconciliation_state not in {"clean", "explained"}:
            status = "withheld_reconciliation_not_clean"
            reason = f"wallet reconciliation is not clean/explained: {reconciliation_state or 'unknown'}"
        else:
            nav, _ = _reconciled_nav(reconciliation)
            withdrawable, _ = _withdrawable_cash(reconciliation)
            if nav is None:
                reason = "all three reconciled NAV views are required"
            elif nav <= stage_cap + threshold:
                status = "not_required"
                reason = "project-account NAV is within the registered stage-cap buffer"
            elif withdrawable is None:
                status = "manual_review_cash_unavailable"
                reason = "NAV exceeds the trigger but no withdrawable-cash field is available"
            else:
                sweep_amount = min(max(0.0, nav - stage_cap), withdrawable)
                if sweep_amount > 0:
                    status = "sweep_advised"
                    reason = "project-account NAV exceeds the active stage cap by more than the registered buffer"
                else:
                    status = "manual_review_cash_unavailable"
                    reason = "NAV exceeds the trigger but observed withdrawable cash is zero"

    advice = (
        f"sweep ${sweep_amount:,.2f} to VALR manually"
        if status == "sweep_advised"
        else "do not initiate an A1 sweep from this artifact"
    )
    payload = {
        "work_order": WORK_ORDER,
        "generated_at_utc": generated_at,
        "status": status,
        "reason": reason,
        "advice": advice,
        "notification_eligible": status == "sweep_advised",
        "account_structure": "single_project_account_under_custody_amendment_A1",
        "active_stage_cap_usd": stage_cap,
        "stage_cap_source": cap_source,
        "sweep_threshold_usd": threshold,
        "threshold_tighten_only": True,
        "reconciled_nav_usd": None if nav is None else round(nav, 8),
        "withdrawable_cash_usd": None if withdrawable is None else round(withdrawable, 8),
        "suggested_sweep_usd": round(sweep_amount, 8),
        "reconciliation_status": reconciliation.get("reconciliation_status") or reconciliation.get("status"),
        "reconciliation_age_seconds": None if reconciliation_age is None else round(reconciliation_age, 3),
        "reconciliation_source": str(reconciliation_path),
        "withdrawal_runbook": "docs/A1_WITHDRAWAL_AND_EXIT_RAIL_RUNBOOK.md",
        "human_action_required": status == "sweep_advised",
        "reporting_only": True,
        "credential_loading_invoked": False,
        "transfer_invoked": False,
        "withdrawal_invoked": False,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        "order_placement_invoked": False,
        "order_amendment_invoked": False,
        "order_cancellation_invoked": False,
    }
    json_path = cfg.output_root / OUTPUT_FILE
    markdown_path = cfg.output_root / OUTPUT_MARKDOWN
    write_json(json_path, payload)
    _write_markdown(markdown_path, payload)
    return payload


def main(config_path: str) -> dict[str, Any]:
    from .config import load_config

    return build_a1_sweep_advisory(load_config(config_path))
