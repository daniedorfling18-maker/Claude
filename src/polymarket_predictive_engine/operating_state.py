"""WO-68 generated operating state.

This module is reporting-only. It reads point-in-time configuration and
artifacts, renders unknown inputs as UNKNOWN, and never changes a gate, broker,
order path, or execution permission.
"""

from __future__ import annotations

from collections import Counter
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, Mapping

from .a1_controls import build_a1_sweep_advisory
from .config import EngineConfig, load_config
from .utils import now_utc, parse_timestamp, read_csv_rows, read_json, write_json


UNKNOWN = "UNKNOWN"
OUTPUT_JSON = "performance/operating_state.json"
OUTPUT_MARKDOWN = "performance/operating_state.md"
P3_OWNER_AMENDMENTS_HEADING_PATTERN = re.compile(r"^##\s+Owner amendments\s*$", re.IGNORECASE | re.MULTILINE)
P3_OWNER_AUTH_TRUE_PATTERN = re.compile(
    r"^AUTONOMOUS_MAKER_EXECUTION_AUTHORISED\s*=\s*true\s*$",
    re.IGNORECASE | re.MULTILINE,
)
P3_OWNER_AUTH_DATE_PATTERN = re.compile(
    r"^AUTONOMOUS_MAKER_EXECUTION_AUTHORISED_AT\s*=\s*\d{4}-\d{2}-\d{2}\s*$",
    re.IGNORECASE | re.MULTILINE,
)
P5_APPROVED_STATUS_PATTERN = re.compile(
    r"^\*\*Status:\s*APPROVED\s*[-\u2013\u2014]\s*[^,\n]+,\s*\d{4}-\d{2}-\d{2}\.\*\*\s*$",
    re.IGNORECASE | re.MULTILINE,
)
P5_A1_HEADING_PATTERN = re.compile(
    r"^##\s+Amendment A1\s+[-\u2013\u2014]\s+single project account\s*$",
    re.IGNORECASE | re.MULTILINE,
)
GOVERNED_PAPER_MAX_AGE_SECONDS = 24 * 3600

# 2026-07-12 WO-68b registered reporting targets. Config may tighten these
# maxima but cannot widen them. They alert a human only and are never read by a
# gate, broker, sizing rule, or order path.
REGISTERED_SLO_TARGETS: dict[str, float] = {
    "quote_sheet_max_age_seconds": 26 * 3600,
    "governance_refresh_max_duration_seconds": 2400,
    "max_consecutive_scheduler_overruns": 0,
    "websocket_max_gap_seconds": 300,
    "dashboard_max_age_seconds": 300,
    "reconciliation_max_age_seconds": 26 * 3600,
    "ledger_anchor_max_age_seconds": 36 * 3600,
    "anchor_push_max_age_seconds": 2 * 3600,
    "telemetry_push_max_age_seconds": 2 * 3600,
}

_DRIFT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("dated_project_state", re.compile(r"last project state update\s*:", re.IGNORECASE)),
    ("hard_coded_paper_prohibition", re.compile(r"not approved for paper trading", re.IGNORECASE)),
    ("hard_coded_current_audit", re.compile(r"current audit state is\s*:", re.IGNORECASE)),
    ("hard_coded_expected_audit", re.compile(r"current expected audit result\s*:", re.IGNORECASE)),
    ("hard_coded_paper_allowed", re.compile(r"paper_allowed\s*=\s*(?:true|false)", re.IGNORECASE)),
    ("hard_coded_wallet_state", re.compile(r"live_wallet_monitoring\s*=", re.IGNORECASE)),
    ("hard_coded_human_test_state", re.compile(r"human_live_test\s*=", re.IGNORECASE)),
    ("hard_coded_autonomous_state", re.compile(r"autonomous_execution\s*=", re.IGNORECASE)),
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _artifact(path: Path) -> dict[str, Any]:
    return _mapping(read_json(path, default={}) or {})


def _explicit_bool(mapping: Mapping[str, Any], key: str) -> bool | None:
    if key not in mapping:
        return None
    value = mapping.get(key)
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


def _finite(value: Any) -> float | None:
    """Return the value as a float only when it is a real, finite number.

    A2/S5: missing, empty, unparseable, NaN and infinity all return None so
    that every caller's comparison falls to the fail-closed branch rather than
    silently comparing against a non-finite value.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _row(key: str, question: str, state: str, evidence: str, source: str, verified_at: str = "") -> dict[str, str]:
    return {
        "key": key,
        "question": question,
        "state": state or UNKNOWN,
        "evidence": evidence or UNKNOWN,
        "source": source or UNKNOWN,
        "verified_at_utc": verified_at or UNKNOWN,
    }


def _timestamp(payload: Mapping[str, Any]) -> str:
    return str(
        payload.get("generated_at_utc")
        or payload.get("collected_at_utc")
        or payload.get("pushed_at_utc")
        or payload.get("updated_at_utc")
        or ""
    ).strip()


def _slo_settings(cfg: EngineConfig) -> dict[str, float]:
    configured = _mapping(cfg.raw.get("operating_state_slos"))
    targets: dict[str, float] = {}
    for key, registered in REGISTERED_SLO_TARGETS.items():
        try:
            candidate = float(configured.get(key, registered))
        except (TypeError, ValueError):
            candidate = registered
        if candidate < 0:
            candidate = registered
        targets[key] = min(float(registered), candidate)
    return targets


def _path_timestamp(path: Path, *, fields: tuple[str, ...] = ()) -> tuple[datetime | None, str]:
    if not path.exists():
        return None, ""
    payload = read_json(path, default={})
    if isinstance(payload, Mapping):
        for field in fields:
            raw = str(payload.get(field) or "").strip()
            parsed = parse_timestamp(raw)
            if parsed is not None:
                return parsed, raw
        if fields:
            # A malformed/error JSON stub is not a successful observation;
            # its filesystem mtime must never launder it as fresh.
            return None, ""
    stamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    return stamp, stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def _slo_row(
    identifier: str,
    metric: str,
    *,
    target: float,
    measured: float | None,
    unit: str,
    source: str,
    observed_at_utc: str = "",
) -> dict[str, Any]:
    breach = None if measured is None else bool(measured > target)
    return {
        "id": identifier,
        "metric": metric,
        "target": target,
        "measured": None if measured is None else round(float(measured), 3),
        "unit": unit,
        "breach": breach,
        "state": UNKNOWN if breach is None else ("BREACH" if breach else "OK"),
        "source": source,
        "observed_at_utc": observed_at_utc or UNKNOWN,
    }


def _age_slo_row(
    identifier: str,
    metric: str,
    *,
    target: float,
    observed: datetime | None,
    observed_raw: str,
    as_of: datetime,
    source: str,
) -> dict[str, Any]:
    age = max(0.0, (as_of - observed).total_seconds()) if observed is not None else None
    return _slo_row(
        identifier,
        metric,
        target=target,
        measured=age,
        unit="seconds",
        source=source,
        observed_at_utc=observed_raw,
    )


def _latest_websocket_timestamp(cfg: EngineConfig) -> tuple[datetime | None, str, str]:
    latest_path = cfg.output_root / "polymarket_websocket" / "websocket_messages_latest.json"
    retained_path = cfg.output_root / "polymarket_websocket" / "websocket_messages.json"
    messages = read_json(latest_path, default=[])
    source_path = latest_path
    if not isinstance(messages, list) or not messages:
        messages = read_json(retained_path, default=[])
        source_path = retained_path
    candidates: list[tuple[datetime, str]] = []
    if isinstance(messages, list):
        for row in messages:
            if not isinstance(row, Mapping):
                continue
            raw = str(row.get("collected_at_utc") or "").strip()
            parsed = parse_timestamp(raw)
            if parsed is not None:
                candidates.append((parsed, raw))
    if candidates:
        candidates.sort(key=lambda item: item[0])
        return candidates[-1][0], candidates[-1][1], str(source_path)
    # A refreshed error/summary file is not a market observation. With no
    # captured message in either window, freshness is unknown and fails closed.
    return None, "", str(source_path)


def _build_slo_block(cfg: EngineConfig, *, as_of: datetime) -> dict[str, Any]:
    targets = _slo_settings(cfg)
    output_root = cfg.output_root

    quote_path = output_root / "maker_carry" / "maker_quote_sheet.md"
    quote_at, quote_raw = _path_timestamp(quote_path)

    ops_path = output_root / "ops_scheduler" / "status.json"
    ops = _artifact(ops_path)
    jobs = _mapping(ops.get("jobs"))
    governance_job = _mapping(jobs.get("governance_refresh"))
    governance_duration: float | None = None
    governance_observed = str(governance_job.get("last_run_utc") or "")
    try:
        if governance_job.get("duration_seconds") is not None:
            governance_duration = max(0.0, float(governance_job["duration_seconds"]))
    except (TypeError, ValueError):
        governance_duration = None
    governance_source = str(ops_path)
    if governance_duration is None:
        heartbeat_path = cfg.governance_root / "governance_refresh_heartbeat.json"
        heartbeat = _artifact(heartbeat_path)
        started = parse_timestamp(heartbeat.get("started_at_utc"))
        ended = as_of if str(heartbeat.get("status") or "").lower() == "running" else parse_timestamp(heartbeat.get("generated_at_utc"))
        if started is not None and ended is not None:
            governance_duration = max(0.0, (ended - started).total_seconds())
            governance_observed = str(heartbeat.get("generated_at_utc") or heartbeat.get("started_at_utc") or "")
        governance_source = str(heartbeat_path)

    overrun_counts: list[int] = []
    intentional_counts: list[int] = []
    overrun_totals: list[int] = []
    intentional_totals: list[int] = []
    for name, value in jobs.items():
        if name == "scheduler" or not isinstance(value, Mapping):
            continue
        for key, target in (
            ("consecutive_skipped_overrun", overrun_counts),
            ("consecutive_skipped_intentional", intentional_counts),
            ("skipped_overrun_total", overrun_totals),
            ("skipped_intentional_total", intentional_totals),
        ):
            if key not in value:
                continue
            try:
                target.append(max(0, int(value.get(key) or 0)))
            except (TypeError, ValueError):
                continue
    consecutive_overruns = float(sum(overrun_counts)) if overrun_counts else None

    websocket_at, websocket_raw, websocket_source = _latest_websocket_timestamp(cfg)
    dashboard_path = output_root / "polymarket_dashboard" / "dashboard_data.json"
    dashboard_at, dashboard_raw = _path_timestamp(dashboard_path, fields=("generated_at_utc",))
    reconciliation_path = output_root / "performance" / "wallet_reconciliation.json"
    reconciliation_at, reconciliation_raw = _path_timestamp(reconciliation_path, fields=("generated_at_utc",))
    anchor_path = output_root / "performance" / "ledger_anchor_head.json"
    anchor_at, anchor_raw = _path_timestamp(anchor_path, fields=("anchored_at_utc", "generated_at_utc"))
    anchor_push_path = output_root / "performance" / "anchor_push_status.json"
    anchor_push_at, anchor_push_raw = _path_timestamp(anchor_push_path, fields=("generated_at_utc",))
    telemetry_push_path = output_root / "performance" / "telemetry_push_status.json"
    telemetry_push_at, telemetry_push_raw = _path_timestamp(telemetry_push_path, fields=("generated_at_utc",))

    rows = [
        _age_slo_row(
            "quote_sheet_age",
            "Quote-sheet age",
            target=targets["quote_sheet_max_age_seconds"],
            observed=quote_at,
            observed_raw=quote_raw,
            as_of=as_of,
            source=str(quote_path),
        ),
        _slo_row(
            "governance_refresh_duration",
            "Governance-refresh duration",
            target=targets["governance_refresh_max_duration_seconds"],
            measured=governance_duration,
            unit="seconds",
            source=governance_source,
            observed_at_utc=governance_observed,
        ),
        _slo_row(
            "scheduler_overrun_cycles",
            "Consecutive scheduler overrun/missed cycles",
            target=targets["max_consecutive_scheduler_overruns"],
            measured=consecutive_overruns,
            unit="cycles",
            source=str(ops_path),
            observed_at_utc=_timestamp(ops),
        ),
        _age_slo_row(
            "websocket_gap",
            "Websocket observation gap",
            target=targets["websocket_max_gap_seconds"],
            observed=websocket_at,
            observed_raw=websocket_raw,
            as_of=as_of,
            source=websocket_source,
        ),
        _age_slo_row(
            "dashboard_staleness",
            "Dashboard staleness",
            target=targets["dashboard_max_age_seconds"],
            observed=dashboard_at,
            observed_raw=dashboard_raw,
            as_of=as_of,
            source=str(dashboard_path),
        ),
        _age_slo_row(
            "reconciliation_age",
            "Wallet-reconciliation age",
            target=targets["reconciliation_max_age_seconds"],
            observed=reconciliation_at,
            observed_raw=reconciliation_raw,
            as_of=as_of,
            source=str(reconciliation_path),
        ),
        _age_slo_row(
            "ledger_anchor_age",
            "Ledger-anchor age",
            target=targets["ledger_anchor_max_age_seconds"],
            observed=anchor_at,
            observed_raw=anchor_raw,
            as_of=as_of,
            source=str(anchor_path),
        ),
        _age_slo_row(
            "anchor_push_age", "Ledger-anchor publication age",
            target=targets["anchor_push_max_age_seconds"], observed=anchor_push_at,
            observed_raw=anchor_push_raw, as_of=as_of, source=str(anchor_push_path),
        ),
        _age_slo_row(
            "telemetry_push_age", "Telemetry publication age",
            target=targets["telemetry_push_max_age_seconds"], observed=telemetry_push_at,
            observed_raw=telemetry_push_raw, as_of=as_of, source=str(telemetry_push_path),
        ),
    ]
    breach_count = sum(row["breach"] is True for row in rows)
    unknown_count = sum(row["breach"] is None for row in rows)
    return {
        "status": "breach" if breach_count else ("incomplete_unknown_inputs" if unknown_count else "ok"),
        "targets_tighten_only": True,
        "breach_count": breach_count,
        "unknown_count": unknown_count,
        "scheduler_skip_taxonomy": {
            "intentional_quota_or_preflight_skips_total": sum(intentional_totals),
            "overrun_or_missed_cycles_total": sum(overrun_totals),
            "consecutive_intentional_skips": sum(intentional_counts),
            "consecutive_overrun_or_missed_cycles": sum(overrun_counts),
            "slo_uses_overrun_only": True,
        },
        "rows": rows,
        "reporting_only": True,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }


def _latest_timestamp(payloads: Mapping[str, Mapping[str, Any]]) -> tuple[str, list[str]]:
    candidates: list[tuple[Any, str, str]] = []
    for name, payload in payloads.items():
        raw = _timestamp(payload)
        parsed = parse_timestamp(raw)
        if parsed is not None:
            candidates.append((parsed, raw, name))
    if not candidates:
        return UNKNOWN, []
    candidates.sort(key=lambda item: item[0])
    latest = candidates[-1]
    sources = sorted(name for parsed, _, name in candidates if parsed == latest[0])
    return latest[1], sources


def _gate_state(gates: Mapping[str, Any], name: str) -> str:
    row = _mapping(gates.get(name))
    return str(row.get("state") or UNKNOWN)


def _sha_matches(left: str, right: str) -> bool:
    left = left.strip().lower()
    right = right.strip().lower()
    if not left or not right or UNKNOWN.lower() in {left, right}:
        return False
    return left.startswith(right) or right.startswith(left)


def _telemetry_manifest(cfg: EngineConfig) -> tuple[dict[str, Any], Path]:
    configured = str(os.getenv("PM_VPS_TELEMETRY_MANIFEST_PATH") or "").strip()
    candidates = [
        Path(configured) if configured else None,
        cfg.output_root / "performance" / "vps_telemetry_manifest.json",
        cfg.path.resolve().parent / "telemetry" / "manifest.json",
    ]
    for candidate in candidates:
        if candidate is not None and candidate.exists():
            return _artifact(candidate), candidate
    return {}, cfg.output_root / "performance" / "vps_telemetry_manifest.json"


def _precondition(identifier: str, title: str, state: str, evidence: str, source: str) -> dict[str, str]:
    return {
        "id": identifier,
        "title": title,
        "state": state,
        "evidence": evidence,
        "source": source,
    }


def _heading_section(text: str, heading_pattern: re.Pattern[str]) -> str | None:
    match = heading_pattern.search(text)
    if match is None:
        return None
    tail = text[match.end() :]
    next_heading = re.search(r"^##\s+", tail, re.MULTILINE)
    return tail[: next_heading.start()] if next_heading is not None else tail


def _owner_authorisation(repo_root: Path) -> tuple[str, str]:
    path = repo_root / "AGENTS.md"
    if not path.is_file():
        return UNKNOWN, "AGENTS.md is unavailable; P3 cannot be determined"
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    section = _heading_section(text, P3_OWNER_AMENDMENTS_HEADING_PATTERN)
    if section is None:
        return "not_met", "AGENTS.md has no signed Owner amendments section; the WO-67 draft authorises nothing"
    authorised = P3_OWNER_AUTH_TRUE_PATTERN.search(section) is not None
    dated = P3_OWNER_AUTH_DATE_PATTERN.search(section) is not None
    if authorised and dated:
        return "met", "exact dated P3 authorisation markers are present in AGENTS.md Owner amendments"
    return "not_met", "exact dated P3 authorisation markers are absent from AGENTS.md Owner amendments"


def _key_custody_approval(repo_root: Path) -> tuple[str, str]:
    path = repo_root / "docs" / "KEY_CUSTODY_DESIGN_WO67_P5.md"
    if not path.is_file():
        return UNKNOWN, "signed custody document is unavailable; P5 cannot be determined"
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    approved_lines = P5_APPROVED_STATUS_PATTERN.findall(text)
    a1_section = _heading_section(text, P5_A1_HEADING_PATTERN)
    a1_approved = bool(a1_section and P5_APPROVED_STATUS_PATTERN.search(a1_section))
    if len(approved_lines) >= 2 and a1_approved:
        return "met", "custody design and Amendment A1 both carry exact dated APPROVED status lines"
    return "not_met", "custody design and/or Amendment A1 lacks the exact dated APPROVED status line"


def _premium_support_state(payload: Mapping[str, Any]) -> tuple[str, str]:
    """P1' — at least one registered premium has cleared its support gate.

    Registered 2026-09-04, superseding the original P1 ("Maker gates
    M-A/M-B/M-C pass"), which became unreachable when the hypothesis those
    gates serve returned a terminal no_for_tested_edge_classes.

    Fail-closed (S5/A2): a missing artifact, a missing field, a non-finite
    number, or a sample that is projected rather than reached all read as NOT
    met. Only an explicit true, a positive finite capacity, and an observed
    sample that has actually reached its required floor can satisfy this.
    """
    if not payload:
        return UNKNOWN, (
            "premium_support.json has not been produced in this checkout; "
            "it is written by the registered H5/H6 support evaluator"
        )
    premia = _mapping(payload.get("premia"))
    if not premia:
        return "not_met", "premium_support.json carries no premia block"
    qualifying: list[str] = []
    details: list[str] = []
    for name in sorted(premia):
        row = _mapping(premia.get(name))
        gate = _explicit_bool(row, "support_gate_passed")
        fdr = _explicit_bool(row, "fdr_applied")
        capacity = _finite(row.get("capacity_usd"))
        observed = _finite(row.get("n_observed"))
        required = _finite(row.get("n_required"))
        tail_cap = _explicit_bool(row, "tail_position_cap_registered")
        reached = observed is not None and required is not None and observed >= required
        if (
            gate is True
            and fdr is True
            and capacity is not None
            and capacity > 0
            and reached
            and tail_cap is True
        ):
            qualifying.append(name)
        details.append(
            f"{name}: support_gate_passed={gate}; fdr_applied={fdr}; "
            f"capacity_usd={capacity}; n_observed={observed}; n_required={required}; "
            f"tail_position_cap_registered={tail_cap}"
        )
    state = "met" if qualifying else "not_met"
    prefix = f"qualifying={','.join(qualifying) if qualifying else 'none'}; "
    return state, prefix + "; ".join(details)


def _wo67_preconditions(
    cfg: EngineConfig,
    maker_study: Mapping[str, Any],
    decision_policy: Mapping[str, Any],
    merge_gate: Mapping[str, Any],
    premium_support: Mapping[str, Any],
) -> list[dict[str, str]]:
    p1_state, p1_evidence = _premium_support_state(premium_support)

    ladder = _mapping(decision_policy.get("ladder"))
    stage = decision_policy.get("ladder_stage_permitted", ladder.get("stage"))
    if not decision_policy or stage in {None, ""}:
        p2_state = UNKNOWN
    else:
        try:
            p2_state = "met" if int(stage) >= 1 else "not_met"
        except (TypeError, ValueError):
            p2_state = UNKNOWN

    p3_state, p3_evidence = _owner_authorisation(cfg.path.resolve().parent)

    if not merge_gate:
        p4_state = UNKNOWN
        p4_evidence = (
            "independent_merge_gate.json has not been produced in this checkout; "
            "run scripts/audit_github_merge_gate.py to evaluate P4"
        )
    else:
        p4_state = (
            "met"
            if _explicit_bool(merge_gate, "enforced") is True
            and _explicit_bool(merge_gate, "branch_protection_enabled") is True
            and _explicit_bool(merge_gate, "independent_review_required") is True
            else "not_met"
        )
        p4_evidence = (
            f"status={merge_gate.get('status', UNKNOWN)}; "
            f"enforced={merge_gate.get('enforced', UNKNOWN)}; "
            f"branch_protection_enabled={merge_gate.get('branch_protection_enabled', UNKNOWN)}; "
            f"independent_review_required={merge_gate.get('independent_review_required', UNKNOWN)}"
        )

    p5_state, p5_evidence = _key_custody_approval(cfg.path.resolve().parent)

    consecutive_days = ladder.get("consecutive_live_ok_days", UNKNOWN)
    p2_evidence = (
        f"ladder_stage_permitted={stage if stage not in {None, ''} else UNKNOWN}; "
        f"consecutive_live_ok_days={consecutive_days}; Stage 1 requires >=7 positive real days "
        "with fills inside the registered 2x-model bound"
    )
    return [
        _precondition(
            "P1'",
            "At least one registered premium has cleared its support gate",
            p1_state,
            p1_evidence,
            "premium_evidence/premium_support.json",
        ),
        _precondition("P2", "Human live-test Stage 1 complete", p2_state, p2_evidence, "decision_policy.json"),
        _precondition("P3", "Dated owner amendment authorises scoped live path", p3_state, p3_evidence, "AGENTS.md Owner amendments"),
        _precondition("P4", "Independent merge control enforced", p4_state, p4_evidence, "independent_merge_gate.json"),
        _precondition("P5", "Scoped key-custody design approved", p5_state, p5_evidence, "docs/KEY_CUSTODY_DESIGN_WO67_P5.md"),
    ]


def _front_door_region(text: str, *, stop_heading: str) -> str:
    marker = f"\n{stop_heading}"
    return text.split(marker, 1)[0]


def front_door_drift_violations(*, readme_text: str, agents_text: str) -> list[str]:
    regions = {
        # README is short, operator-facing, and entirely front-door prose.
        # Scan the whole file so stale dynamic claims cannot hide below a
        # historical heading while the generated-state pointer remains clean.
        "README.md": readme_text,
        "AGENTS.md": _front_door_region(agents_text, stop_heading="## Run model"),
    }
    violations: list[str] = []
    required_pointer = OUTPUT_MARKDOWN.lower()
    for name, text in regions.items():
        lowered = text.lower().replace("`", "")
        if required_pointer not in lowered:
            violations.append(f"{name}: missing pointer to {OUTPUT_MARKDOWN}")
        for label, pattern in _DRIFT_PATTERNS:
            if pattern.search(text):
                violations.append(f"{name}: {label}")
    return violations


def assert_front_door_docs_state_pointer_only(repo_root: Path) -> None:
    readme = (repo_root / "README.md").read_text(encoding="utf-8-sig", errors="replace")
    agents = (repo_root / "AGENTS.md").read_text(encoding="utf-8-sig", errors="replace")
    violations = front_door_drift_violations(readme_text=readme, agents_text=agents)
    if violations:
        raise AssertionError("front-door operating-state drift: " + "; ".join(violations))


def _paper_fill_lanes(fill_rows: list[dict[str, str]], order_rows: list[dict[str, str]]) -> dict[str, int]:
    orders = {str(row.get("order_id") or ""): row for row in order_rows if str(row.get("order_id") or "")}
    counts: Counter[str] = Counter()
    for fill in fill_rows:
        order = orders.get(str(fill.get("order_id") or ""), {})
        source: dict[str, Any] = {}
        try:
            parsed = json.loads(str(order.get("source_signal_json") or "{}"))
            source = _mapping(parsed)
        except (TypeError, ValueError, json.JSONDecodeError):
            source = {}
        strategy = str(order.get("strategy_name") or source.get("strategy_name") or "").strip()
        entry_source = str(source.get("price_action_entry_source") or source.get("source") or "").strip()
        if strategy and entry_source:
            lane = f"{strategy}:{entry_source}"
        elif strategy:
            lane = strategy
        elif entry_source:
            lane = entry_source
        else:
            lane = "unattributed_legacy_paper_fill"
        counts[lane] += 1
    return dict(sorted(counts.items()))


def _md(value: Any) -> str:
    rendered = UNKNOWN if value is None or value == "" else str(value)
    return rendered.replace("|", "\\|").replace("\n", " ")


def _write_markdown(path: Path, payload: Mapping[str, Any]) -> None:
    lines = [
        "# Generated operating state",
        "",
        f"Generated at: `{_md(payload.get('generated_at_utc'))}`  ",
        "Source: point-in-time config and artifacts. Missing evidence is `UNKNOWN`; it is never guessed.",
        "",
        "| Question | State | Evidence | Source | Verified at |",
        "|---|---|---|---|---|",
    ]
    for row in payload.get("rows", []):
        lines.append(
            f"| {_md(row.get('question'))} | **{_md(row.get('state'))}** | {_md(row.get('evidence'))} | "
            f"`{_md(row.get('source'))}` | {_md(row.get('verified_at_utc'))} |"
        )
    lines.extend(
        [
            "",
            "## Operating SLOs (reporting only)",
            "",
            "A breach alerts the operator; it never changes a gate, size, broker, or order path.",
            "",
            "| Metric | State | Target | Measured | Unit | Source | Observed at |",
            "|---|---|---:|---:|---|---|---|",
        ]
    )
    for row in _mapping(payload.get("slo")).get("rows", []):
        lines.append(
            f"| {_md(row.get('metric'))} | **{_md(row.get('state'))}** | {_md(row.get('target'))} | "
            f"{_md(row.get('measured'))} | {_md(row.get('unit'))} | `{_md(row.get('source'))}` | "
            f"{_md(row.get('observed_at_utc'))} |"
        )
    watchdog = _mapping(payload.get("degraded_state_watchdog"))
    lines.extend(
        [
            "",
            "## Degraded-state watchdog (reporting only)",
            "",
            "Persistent semantic-health incidents alert the owner; they never override a fail-closed or risk state.",
            "",
            "| Registration | Entity | State | Reason | Source |",
            "|---|---|---|---|---|",
        ]
    )
    for row in watchdog.get("active_incidents", []):
        lines.append(
            f"| {_md(row.get('registration_id'))} | {_md(row.get('entity'))} | **INCIDENT** | "
            f"{_md(row.get('reason'))} | `{_md(row.get('source_artifact'))}` |"
        )
    if not watchdog.get("active_incidents"):
        lines.append("| - | - | **None active** | - | - |")
    lines.extend(
        [
            "",
            "## WO-67 autonomous-execution preconditions",
            "",
            "WO-67 remains blocked unless every precondition is independently `met`. This report never authorises execution.",
            "",
            "| ID | Precondition | State | Evidence | Source |",
            "|---|---|---|---|---|",
        ]
    )
    for row in payload.get("wo67_preconditions", []):
        lines.append(
            f"| {_md(row.get('id'))} | {_md(row.get('title'))} | **{_md(row.get('state'))}** | "
            f"{_md(row.get('evidence'))} | `{_md(row.get('source'))}` |"
        )
    missing = payload.get("missing_inputs", [])
    lines.extend(["", "## Missing inputs", ""])
    lines.extend([f"- `{_md(item)}`" for item in missing] or ["- None"])
    lines.extend(["", "`paper_trading_invoked=false`; `live_trading_invoked=false`.", ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def build_operating_state(cfg: EngineConfig) -> dict[str, Any]:
    generated_at = now_utc()
    as_of = parse_timestamp(generated_at) or datetime.now(timezone.utc)
    output_root = cfg.output_root
    governance = cfg.governance_root
    performance = output_root / "performance"
    maker_root = output_root / "maker_carry"

    audit = _artifact(governance / "local_history_audit_summary.json")
    readiness = _artifact(governance / "paper_trade_readiness.json")
    profit_verdict = _artifact(governance / "profit_verdict.json")
    maker_study = _artifact(maker_root / "maker_carry_study.json")
    maker_live_test = _artifact(maker_root / "maker_live_test.json")
    decision_policy = _artifact(maker_root / "decision_policy.json")
    executor_status_path = output_root / "execution" / "executor_status.json"
    executor_ledger_path = output_root / "execution" / "execution_ledger.csv"
    executor_status_artifact = _artifact(executor_status_path)
    future_executor_rows = read_csv_rows(executor_ledger_path)
    if executor_status_artifact:
        executor_status = executor_status_artifact
    elif future_executor_rows:
        executor_status = {
            "status": UNKNOWN,
            "mode": UNKNOWN,
            "executor_present": True,
            "execution_ledger_rows": len(future_executor_rows),
        }
    else:
        executor_status = {
            "status": "ABSENT",
            "mode": "ABSENT",
            "executor_present": False,
            "execution_ledger_rows": 0,
        }
    degraded_watchdog = _artifact(output_root / "ops_scheduler" / "degraded_state_watchdog.json")
    deploy_acceptance = _artifact(output_root / "ops_scheduler" / "deploy_acceptance.json")
    merge_gate = _artifact(performance / "independent_merge_gate.json")
    premium_support = _artifact(output_root / "premium_evidence" / "premium_support.json")
    a1_sweep_advisory = build_a1_sweep_advisory(cfg, as_of=as_of)
    telemetry_manifest, telemetry_manifest_path = _telemetry_manifest(cfg)
    artifacts = {
        "local_history_audit": audit,
        "paper_trade_readiness": readiness,
        "profit_verdict": profit_verdict,
        "maker_carry_study": maker_study,
        "maker_live_test": maker_live_test,
        "decision_policy": decision_policy,
        "independent_merge_gate": merge_gate,
        "vps_telemetry_manifest": telemetry_manifest,
        "degraded_state_watchdog": degraded_watchdog,
    }
    if executor_status_artifact:
        artifacts["executor_status"] = executor_status_artifact

    missing_inputs = [name for name, payload in artifacts.items() if not payload]
    if future_executor_rows and not executor_status_artifact:
        missing_inputs.append("executor_status")
    raw = cfg.raw
    paper_cfg = _mapping(raw.get("paper_trading"))
    live_cfg = _mapping(raw.get("live_trading"))
    maker_cfg = _mapping(raw.get("maker_live_test"))
    trading_cfg = _mapping(raw.get("trading"))
    paper_enabled = _explicit_bool(paper_cfg, "enabled")
    live_enabled = _explicit_bool(live_cfg, "enabled")
    maker_enabled = _explicit_bool(maker_cfg, "enabled")
    trading_mode = str(trading_cfg.get("mode") or "").strip().lower() or UNKNOWN
    operator_wallet_configured = bool(str(maker_cfg.get("wallet_address") or "").strip()) if maker_cfg else None
    executor_wallet_configured = bool(str(maker_cfg.get("executor_wallet_address") or "").strip()) if maker_cfg else None
    wallet_configured = (
        None
        if operator_wallet_configured is None or executor_wallet_configured is None
        else bool(operator_wallet_configured or executor_wallet_configured)
    )

    if trading_mode == UNKNOWN or paper_enabled is None or live_enabled is None:
        research_mode = UNKNOWN
    elif trading_mode in {"paper", "backtest"} and paper_enabled and not live_enabled:
        research_mode = "ACTIVE_SHADOW_PAPER_GATED"
    elif live_enabled or trading_mode == "live":
        research_mode = "LIVE_CONFIGURATION_PRESENT"
    else:
        research_mode = "INACTIVE_OR_NON_PAPER"

    if paper_enabled is None or trading_mode == UNKNOWN:
        paper_capability = UNKNOWN
        mechanical_evidence = "mechanical_readiness=unknown; configuration could not be evaluated"
    else:
        mechanically_ready = bool(paper_enabled and trading_mode in {"paper", "backtest"})
        paper_capability = "READY" if mechanically_ready else "DISABLED"
        mechanical_evidence = (
            f"mechanical_readiness={'ready' if mechanically_ready else 'disabled'}; "
            "capability_only=true; governed_authorisation_is_separate=true"
        )

    paper_decision = _mapping(audit.get("paper_decision"))
    paper_allowed = paper_decision.get("paper_allowed")
    audit_at = parse_timestamp(_timestamp(audit))
    audit_age = max(0.0, (as_of - audit_at).total_seconds()) if audit_at is not None else None
    if isinstance(paper_allowed, bool) and audit_age is not None and audit_age <= GOVERNED_PAPER_MAX_AGE_SECONDS:
        governed_paper = "GRANTED" if paper_allowed else "NOT_GRANTED"
        governed_evidence = (
            f"{paper_decision.get('reason') or 'paper_decision artifact'}; "
            f"verification_age_seconds={round(audit_age, 3)}; maximum={GOVERNED_PAPER_MAX_AGE_SECONDS}"
        )
    elif isinstance(paper_allowed, bool) and audit_age is not None:
        governed_paper = "NOT_GRANTED_STALE_EVIDENCE"
        governed_evidence = (
            f"paper decision failed closed because verification_age_seconds={round(audit_age, 3)} "
            f"exceeds maximum={GOVERNED_PAPER_MAX_AGE_SECONDS}"
        )
    else:
        governed_paper = UNKNOWN
        governed_evidence = "paper decision or its verification timestamp is unavailable"

    fills_path = output_root / "polymarket_portfolio" / "paper_fills.csv"
    if fills_path.exists():
        fill_rows = read_csv_rows(fills_path)
        fill_count = len(fill_rows)
        lane_counts = _paper_fill_lanes(
            fill_rows,
            read_csv_rows(output_root / "polymarket_portfolio" / "paper_orders.csv"),
        )
        paper_activity = f"RECORDED_FILLS={fill_count}"
        paper_activity_evidence = (
            f"paper_simulation_only=true; registered_experiment_lanes={json.dumps(lane_counts, sort_keys=True)}; "
            "does_not_imply_live_or_governed_authorisation=true"
        )
    else:
        lane_counts = {}
        paper_activity = UNKNOWN
        paper_activity_evidence = "paper fill ledger is missing"
        missing_inputs.append("paper_fills_ledger")

    if maker_enabled is None or wallet_configured is None:
        human_live_test = UNKNOWN
    elif maker_enabled and wallet_configured:
        human_live_test = "OPERATOR_DOMAIN_MONITORED_READ_ONLY"
    else:
        human_live_test = "NOT_CONFIGURED"

    monitored_wallets = _mapping(maker_live_test.get("wallets"))

    def monitored_state(role: str, configured: bool | None) -> str:
        if maker_enabled is None or configured is None:
            return UNKNOWN
        if not maker_enabled or not configured:
            return "NOT_CONFIGURED"
        role_payload = _mapping(monitored_wallets.get(role))
        if role_payload:
            return f"ACTIVE_READ_ONLY:{role_payload.get('status', UNKNOWN)}"
        if role == "operator" and maker_live_test:
            return f"ACTIVE_READ_ONLY:{maker_live_test.get('status', UNKNOWN)}"
        return UNKNOWN

    operator_wallet_state = monitored_state("operator", operator_wallet_configured)
    executor_present = bool(executor_status.get("executor_present"))
    if executor_wallet_configured:
        executor_wallet_state = "A1_CONFIG_DRIFT_SEPARATE_EXECUTOR_WALLET"
    elif operator_wallet_configured:
        operator_payload = _mapping(monitored_wallets.get("operator"))
        monitored_status = operator_payload.get("status", maker_live_test.get("status", UNKNOWN))
        window = "EXECUTOR_WINDOW_ACTIVE" if executor_present else "EXECUTOR_WINDOW_INACTIVE"
        executor_wallet_state = f"A1_SINGLE_PROJECT_WALLET:{window}:{monitored_status}"
    elif operator_wallet_configured is False:
        executor_wallet_state = "NOT_CONFIGURED"
    else:
        executor_wallet_state = UNKNOWN
    wallet_state = operator_wallet_state

    live_ledger_candidates = [
        executor_ledger_path,
        output_root / "maker_carry" / "maker_execution_ledger.csv",
        output_root / "polymarket_execution" / "live_execution_ledger.csv",
    ]
    live_ledger = next((path for path in live_ledger_candidates if path.exists()), None)
    if live_ledger is None:
        live_orders = UNKNOWN
        live_order_evidence = f"no execution ledger; live_config_enabled={live_enabled if live_enabled is not None else UNKNOWN}"
        missing_inputs.append("live_execution_ledger")
    else:
        live_order_rows = read_csv_rows(live_ledger)
        if live_ledger == executor_ledger_path:
            live_order_rows = [
                row
                for row in live_order_rows
                if str(row.get("mode") or "").strip().lower() in {"canary", "portfolio"}
                and str(row.get("action_type") or "").strip().lower() == "place"
            ]
        live_order_count = len(live_order_rows)
        live_orders = f"RECORDED_ORDERS={live_order_count}"
        live_order_evidence = (
            f"{live_ledger}; executor live place rows only"
            if live_ledger == executor_ledger_path
            else str(live_ledger)
        )

    # The monitor's top-level status may be A1_ADVISORY for a human custody
    # sweep while the executor itself is deliberately absent.  Presence is
    # established by the explicit executor flag and mode, never by unrelated
    # advisory state.
    executor_absent = not executor_present and str(executor_status.get("mode") or "").upper() == "ABSENT"
    executor_mode = "ABSENT" if executor_absent else str(executor_status.get("mode") or UNKNOWN).upper()
    executor_open_orders = executor_status.get("open_orders")
    executor_open_orders_state = (
        "ABSENT" if executor_absent else (UNKNOWN if executor_open_orders is None else str(executor_open_orders))
    )
    executor_exposure = executor_status.get("exposure_usd")
    executor_stage_cap = executor_status.get("stage_cap_usd")
    executor_exposure_state = str(executor_status.get("exposure_vs_stage_cap_state") or UNKNOWN).upper()
    executor_exposure_view = (
        "ABSENT"
        if executor_absent
        else (
            UNKNOWN
            if executor_exposure is None or executor_stage_cap is None
            else f"{executor_exposure_state}: ${executor_exposure} / ${executor_stage_cap}"
        )
    )
    executor_last_action_age = executor_status.get("last_action_age_seconds")
    executor_last_action_state = (
        "ABSENT"
        if executor_absent
        else (UNKNOWN if executor_last_action_age is None else f"{executor_last_action_age}s")
    )
    executor_dead_man = _mapping(executor_status.get("dead_man"))
    executor_freshness = _mapping(executor_status.get("freshness_slo"))
    executor_kill = _mapping(executor_status.get("kill_criteria_scoreboard"))
    executor_dead_man_state = (
        "ABSENT" if executor_absent else str(executor_dead_man.get("state") or UNKNOWN).upper()
    )
    executor_freshness_state = (
        "ABSENT" if executor_absent else str(executor_freshness.get("state") or UNKNOWN).upper()
    )
    executor_kill_state = "ABSENT" if executor_absent else str(executor_kill.get("status") or UNKNOWN).upper()

    preconditions = _wo67_preconditions(cfg, maker_study, decision_policy, merge_gate, premium_support)
    states = [row["state"] for row in preconditions]
    if all(state == "met" for state in states):
        autonomous_state = "PRECONDITIONS_MET_EXECUTOR_NOT_IMPLEMENTED"
    elif any(state == "not_met" for state in states):
        autonomous_state = "BLOCKED"
    else:
        autonomous_state = "BLOCKED_UNKNOWN"

    telemetry_sha = str(telemetry_manifest.get("deployed_git_rev") or "").strip()
    source_sha = str(telemetry_manifest.get("source_git_rev") or "").strip()
    checkout_sha = str(telemetry_manifest.get("checkout_git_rev") or "").strip()
    source_observed_at = str(telemetry_manifest.get("source_observed_at_utc") or "").strip()
    divergence_started_at = str(telemetry_manifest.get("divergence_started_at_utc") or "").strip()
    expected_sha = str(os.getenv("PM_VPS_DEPLOYED_SHA") or "").strip()
    image_sha = str(os.getenv("PM_IMAGE_BUILD_SHA") or "").strip()
    deployed_sha = telemetry_sha or expected_sha or image_sha or UNKNOWN
    comparable_markers = [value for value in (telemetry_sha, expected_sha, image_sha) if value]
    deployment_markers_aligned = (
        all(_sha_matches(comparable_markers[0], value) for value in comparable_markers[1:])
        if len(comparable_markers) > 1
        else None
    )
    deployment_evidence = (
        f"telemetry={telemetry_sha or UNKNOWN}; expected={expected_sha or UNKNOWN}; "
        f"image={image_sha or UNKNOWN}; aligned="
        f"{deployment_markers_aligned if deployment_markers_aligned is not None else UNKNOWN}"
    )
    if not source_sha or not telemetry_sha or "unknown" in {source_sha.lower(), telemetry_sha.lower()}:
        source_deployed_state = UNKNOWN
        divergence_age_seconds: float | None = None
    elif _sha_matches(source_sha, telemetry_sha):
        source_deployed_state = "ALIGNED"
        divergence_age_seconds = 0.0
    else:
        source_deployed_state = "DIVERGED"
        divergence_started = parse_timestamp(divergence_started_at)
        divergence_age_seconds = max(0.0, (as_of - divergence_started).total_seconds()) if divergence_started else None
    source_deployed_evidence = (
        f"source={source_sha or UNKNOWN}; checkout={checkout_sha or UNKNOWN}; deployed={telemetry_sha or UNKNOWN}; "
        f"divergence_started_at_utc={divergence_started_at or UNKNOWN}; "
        f"divergence_age_seconds={round(divergence_age_seconds, 3) if divergence_age_seconds is not None else UNKNOWN}"
    )
    latest_evidence, latest_sources = _latest_timestamp(artifacts)
    maker_gates = _mapping(maker_study.get("maker_gates"))
    if degraded_watchdog:
        watchdog_count = int(degraded_watchdog.get("active_incident_count") or 0)
        watchdog_state = "INCIDENT" if watchdog_count else str(degraded_watchdog.get("status") or "OK").upper()
        watchdog_evidence = (
            f"active_incidents={watchdog_count}; new_incidents="
            f"{int(degraded_watchdog.get('new_incident_count') or 0)}"
        )
    else:
        watchdog_count = 0
        watchdog_state = UNKNOWN
        watchdog_evidence = "degraded-state watchdog artifact is missing"
    if deploy_acceptance:
        deploy_acceptance_state = str(deploy_acceptance.get("status") or UNKNOWN).upper()
        failed_checks = [
            str(item.get("id") or "unknown")
            for item in deploy_acceptance.get("checks", [])
            if isinstance(item, Mapping) and str(item.get("status") or "").upper() == "FAIL"
        ]
        deploy_acceptance_evidence = (
            f"target={deploy_acceptance.get('target_deploy_sha') or UNKNOWN}; "
            f"failed_checks={','.join(failed_checks) if failed_checks else 'none'}; "
            f"rollback_ref={deploy_acceptance.get('rollback_ref') or UNKNOWN}"
        )
    else:
        # A checkout can legitimately predate its first WO-79 deploy. This is
        # explicit rather than UNKNOWN so the acceptance check is not
        # self-referential on its first run.
        deploy_acceptance_state = "NOT_RUN"
        deploy_acceptance_evidence = "no post-deploy acceptance artifact has been produced yet"
    rows = [
        _row("research_mode", "Research mode", research_mode, f"trading.mode={trading_mode}; paper.enabled={paper_enabled}; live.enabled={live_enabled}", "effective config"),
        _row("mechanical_paper_capability", "Mechanical paper readiness (not authorisation)", paper_capability, mechanical_evidence, "effective config + paper_trade_readiness.json", _timestamp(readiness)),
        _row("governed_paper_authorisation", "Governed paper authorisation", governed_paper, governed_evidence, "local_history_audit_summary.json", _timestamp(audit)),
        _row("paper_activity", "Paper-simulation activity by registered lane", paper_activity, paper_activity_evidence, "paper_fills.csv + paper_orders.csv"),
        _row("human_live_test", "Human live-test authorisation", human_live_test, "human execution is outside system control; system monitoring is read-only", "maker_live_test config"),
        _row(
            "live_wallet_monitoring",
            "Live-wallet monitoring (legacy primary view)",
            wallet_state,
            "primary=single_project_account; custody=A1; attribution=non_overlapping_mode_time_windows",
            "config + maker_live_test.json",
            _timestamp(maker_live_test),
        ),
        _row(
            "operator_wallet_monitoring",
            "Operator wallet monitoring",
            operator_wallet_state,
            f"configured={operator_wallet_configured if operator_wallet_configured is not None else UNKNOWN}; role=human_operator_window; custody=A1",
            "maker_live_test.wallet_address + maker_live_test.json",
            _timestamp(maker_live_test),
        ),
        _row(
            "executor_wallet_monitoring",
            "Executor-era monitoring on the A1 single project wallet",
            executor_wallet_state,
            (
                "configured_from=maker_live_test.wallet_address; legacy_executor_wallet_field_must_remain_empty="
                f"{not bool(executor_wallet_configured)}; role=executor_mode_time_window; concurrent_human_window_prohibited=true"
            ),
            "custody Amendment A1 + maker_live_test.wallet_address + execution ledger mode/time",
            _timestamp(maker_live_test),
        ),
        _row(
            "a1_sweep_advisory",
            "A1 excess-balance sweep advisory",
            str(a1_sweep_advisory.get("status") or UNKNOWN).upper(),
            str(a1_sweep_advisory.get("advice") or a1_sweep_advisory.get("reason") or UNKNOWN),
            "execution/a1_sweep_advisory.json",
            _timestamp(a1_sweep_advisory),
        ),
        _row("live_orders_submitted", "Live orders submitted by the system", live_orders, live_order_evidence, str(live_ledger or UNKNOWN)),
        _row(
            "executor_mode",
            "Future executor mode",
            executor_mode,
            f"status={executor_status.get('status', UNKNOWN)}; ledger_rows={executor_status.get('execution_ledger_rows', 0)}",
            "execution/executor_status.json",
            _timestamp(executor_status),
        ),
        _row(
            "executor_open_orders",
            "Future executor open orders",
            executor_open_orders_state,
            f"ledger_rows={executor_status.get('execution_ledger_rows', 0)}",
            "execution/executor_status.json",
            _timestamp(executor_status),
        ),
        _row(
            "executor_exposure_vs_stage_cap",
            "Future executor exposure vs stage cap",
            executor_exposure_view,
            f"cap_source={executor_status.get('stage_cap_source', UNKNOWN)}",
            "execution/executor_status.json + maker_carry/decision_policy.json",
            _timestamp(executor_status),
        ),
        _row(
            "executor_last_action_age",
            "Future executor last action age",
            executor_last_action_state,
            f"last_action_at_utc={executor_status.get('last_action_at_utc') or UNKNOWN}",
            "execution/executor_status.json",
            _timestamp(executor_status),
        ),
        _row(
            "executor_dead_man",
            "Independent executor dead-man monitor",
            executor_dead_man_state,
            (
                f"heartbeat_age_seconds={executor_dead_man.get('heartbeat_age_seconds', UNKNOWN)}; "
                f"countdown_seconds={executor_dead_man.get('countdown_seconds', UNKNOWN)}; "
                f"threshold_seconds={executor_dead_man.get('gap_threshold_seconds', 1800)}"
            ),
            "execution/executor_heartbeat.json read by vps_ops_scheduler",
            _timestamp(executor_status),
        ),
        _row(
            "executor_freshness_slo",
            "Future executor freshness SLO",
            executor_freshness_state,
            (
                f"heartbeat_age_seconds={executor_freshness.get('heartbeat_age_seconds', UNKNOWN)}; "
                f"slo_seconds={executor_freshness.get('slo_seconds', 600)}"
            ),
            "execution/executor_status.json",
            _timestamp(executor_status),
        ),
        _row(
            "executor_kill_criteria",
            "Future executor kill-criteria scoreboard",
            executor_kill_state,
            f"triggered={','.join(str(value) for value in executor_kill.get('triggered', [])) or 'none'}",
            "maker_carry/decision_policy.json via executor monitor",
            _timestamp(executor_status),
        ),
        _row("autonomous_execution", "Autonomous execution authorisation", autonomous_state, ", ".join(f"{row['id']}={row['state']}" for row in preconditions), "WO-67 preconditions"),
        _row(
            "degraded_state_watchdog",
            "Persistent degraded-state incidents",
            watchdog_state,
            watchdog_evidence,
            "ops_scheduler/degraded_state_watchdog.json",
            _timestamp(degraded_watchdog),
        ),
        _row(
            "deploy_acceptance",
            "Latest post-deploy acceptance",
            deploy_acceptance_state,
            deploy_acceptance_evidence,
            "ops_scheduler/deploy_acceptance.json",
            _timestamp(deploy_acceptance),
        ),
        _row(
            "latest_deployed_sha",
            "Latest deployed SHA",
            deployed_sha,
            deployment_evidence,
            f"{telemetry_manifest_path} + PM_VPS_DEPLOYED_SHA/PM_IMAGE_BUILD_SHA",
            _timestamp(telemetry_manifest),
        ),
        _row(
            "source_vs_deployed_sha",
            "Source vs deployed SHA",
            source_deployed_state,
            source_deployed_evidence,
            str(telemetry_manifest_path),
            source_observed_at or _timestamp(telemetry_manifest),
        ),
        _row("latest_verified_evidence", "Latest verified evidence timestamp", latest_evidence, ", ".join(latest_sources) or UNKNOWN, "artifact generated_at_utc", latest_evidence if latest_evidence != UNKNOWN else ""),
    ]

    slo = _build_slo_block(cfg, as_of=as_of)

    payload = {
        "status": "ok" if not missing_inputs and all(row["state"] != UNKNOWN for row in rows) else "incomplete_unknown_inputs",
        "generated_at_utc": generated_at,
        "source": "point_in_time_config_and_artifacts",
        "rows": rows,
        "wo67_preconditions": preconditions,
        "slo": slo,
        "degraded_state_watchdog": degraded_watchdog,
        "deploy_acceptance": deploy_acceptance,
        "executor_status": executor_status,
        "a1_sweep_advisory": a1_sweep_advisory,
        "paper_activity_lanes": lane_counts,
        "deployment": {
            "status": source_deployed_state,
            "source_git_rev": source_sha or UNKNOWN,
            "checkout_git_rev": checkout_sha or UNKNOWN,
            "deployed_git_rev": telemetry_sha or UNKNOWN,
            "source_observed_at_utc": source_observed_at or UNKNOWN,
            "divergence_started_at_utc": divergence_started_at or UNKNOWN,
            "divergence_age_seconds": divergence_age_seconds,
        },
        "missing_inputs": sorted(set(missing_inputs)),
        "taker_verdict": {
            "verdict": profit_verdict.get("verdict", UNKNOWN),
            "generated_at_utc": _timestamp(profit_verdict) or UNKNOWN,
            "gate_a_state": _mapping(_mapping(profit_verdict.get("gates")).get("A_edge_exists")).get("state", UNKNOWN),
        },
        "maker_verdict": {
            "verdict": maker_gates.get("maker_verdict", UNKNOWN),
            "generated_at_utc": _timestamp(maker_study) or UNKNOWN,
            "m_a_state": _gate_state(maker_gates, "M_A_carry_evidence"),
            "m_b_state": _gate_state(maker_gates, "M_B_adverse_realism"),
            "m_c_state": _gate_state(maker_gates, "M_C_payout_floor"),
        },
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    json_path = output_root / OUTPUT_JSON
    markdown_path = output_root / OUTPUT_MARKDOWN
    write_json(json_path, payload)
    _write_markdown(markdown_path, payload)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return build_operating_state(load_config(config_path))
