"""WO-64 generated investment policy statement (code is policy).

The document is rendered from the same structured constants and effective
configuration consumed by the maker and verdict modules. It is reporting
infrastructure only: no gate, sizing rule, broker, or order path reads it.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable

from .config import EngineConfig, load_config
from .live_test_decision_policy import (
    DEFAULT_SETTINGS as DECISION_POLICY_DEFAULTS,
    KILL_CRITERIA_REGISTRATION,
    REGISTERED_ACTION_POLICY,
    REGISTERED_AT_UTC as DECISION_POLICY_REGISTERED_AT_UTC,
    effective_policy_settings,
)
from .maker_carry_study import (
    MAKER_GATE_REGISTRATION,
    MAKER_GATES_REGISTERED_AT_UTC,
    MAKER_POLICY_DEFAULTS,
    QUOTE_SHEET_STANDING_RULES,
    maker_policy_settings,
    quote_sheet_standing_rules,
)
from .profit_verdict import (
    DEFAULT_SETTINGS as VERDICT_DEFAULTS,
    REGISTERED_AMENDMENTS,
    REGISTERED_AT_UTC as VERDICT_REGISTERED_AT_UTC,
    REGISTERED_EXTENSION_PROTOCOL,
    VERDICT_GATE_DEFINITIONS,
    effective_verdict_settings,
)
from .utils import now_utc, read_csv_rows, read_json, safe_float, write_json, write_text_atomic


RISK_POLICY_KEYS = [
    "bankroll",
    "minimum_edge",
    "minimum_confidence",
    "maximum_spread",
    "minimum_liquidity",
    "minimum_time_to_close_minutes",
    "maximum_resolution_risk",
    "maximum_single_market_exposure",
    "maximum_category_exposure",
    "maximum_correlated_exposure",
    "maximum_daily_loss",
    "maximum_drawdown",
    "maximum_open_orders",
    "maximum_order_rate_per_minute",
    "maximum_slippage",
    "liquidity_cap_fraction",
    "kelly_cap",
    "kelly_shrinkage",
    "minimum_entry_price",
    "maximum_entry_price",
]


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = cfg.raw.get("ips_render", {}) if isinstance(cfg.raw.get("ips_render"), dict) else {}
    merged = {
        "enabled": True,
        "output_file": "performance/investment_policy_statement.md",
        "summary_file": "performance/investment_policy_statement_summary.json",
    }
    merged.update({key: value for key, value in raw.items() if value is not None})
    return merged


def _enabled(value: Any) -> bool:
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _safe_output_path(cfg: EngineConfig, value: Any) -> Path:
    text = str(value or "").strip().replace("\\", "/")
    path = Path(text)
    if not text or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"IPS output path must stay below paths.output_root: {value!r}")
    return cfg.output_root / path


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def source_defaults_bundle() -> dict[str, Any]:
    """The frozen, hashable source-policy surface rendered by the IPS."""

    return {
        "schema": "wo64-source-policy-v1",
        "decision_policy": {
            "registered_at_utc": DECISION_POLICY_REGISTERED_AT_UTC,
            "settings": DECISION_POLICY_DEFAULTS,
            "action_policy": REGISTERED_ACTION_POLICY,
            "kill_criteria": KILL_CRITERIA_REGISTRATION,
        },
        "maker_policy": {
            "registered_at_utc": MAKER_GATES_REGISTERED_AT_UTC,
            "settings": MAKER_POLICY_DEFAULTS,
            "gates": MAKER_GATE_REGISTRATION,
            "quote_sheet_standing_rules": QUOTE_SHEET_STANDING_RULES,
        },
        "profit_verdict": {
            "registered_at_utc": VERDICT_REGISTERED_AT_UTC,
            "settings": VERDICT_DEFAULTS,
            "gates": VERDICT_GATE_DEFINITIONS,
            "amendments": REGISTERED_AMENDMENTS,
            "extension_protocol": REGISTERED_EXTENSION_PROTOCOL,
        },
    }


def effective_policy_bundle(cfg: EngineConfig) -> dict[str, Any]:
    decision = effective_policy_settings(cfg)
    verdict = effective_verdict_settings(cfg)
    maker = maker_policy_settings(cfg)
    return {
        "decision_policy": {key: decision.get(key) for key in DECISION_POLICY_DEFAULTS},
        "maker_policy": maker,
        "profit_verdict": {key: verdict.get(key) for key in VERDICT_DEFAULTS},
        "risk": {key: (cfg.raw.get("risk", {}) or {}).get(key) for key in RISK_POLICY_KEYS},
        "trading_mode": cfg.trading_mode,
        "paper_trading_enabled": bool((cfg.raw.get("paper_trading", {}) or {}).get("enabled", False)),
        "live_trading_enabled": bool((cfg.raw.get("live_trading", {}) or {}).get("enabled", False)),
        "quote_sheet_standing_rules": quote_sheet_standing_rules(maker),
    }


def _risk_annex(cfg: EngineConfig) -> dict[str, Any]:
    path = cfg.output_root / "polymarket_portfolio" / "risk_state.json"
    raw = read_json(path, default={}) or {}
    risk = raw.get("portfolio_risk") if isinstance(raw, dict) else {}
    if not isinstance(risk, dict) or not risk:
        snapshots = read_csv_rows(cfg.output_root / "polymarket_portfolio" / "portfolio_snapshots.csv")
        latest = snapshots[-1] if snapshots else {}
        fallback_exposure = safe_float(latest.get("total_exposure_usdc") or latest.get("total_exposure"))
        return {
            "status": "unavailable" if fallback_exposure is None else "partial",
            "source_path": str(path),
            "reason": "WO-12 risk_state.json is missing"
            if fallback_exposure is None
            else "WO-12 VaR state missing; exposure is from the latest portfolio snapshot",
            "open_positions": None,
            "total_cost_usdc": fallback_exposure,
            "var_95_usdc": None,
            "cvar_95_usdc": None,
            "worst_position_return_pct": None,
            "exposure_by_correlation_key": [],
            "exposure_by_category": [],
        }
    return {
        "status": "ok",
        "source_path": str(path),
        "reason": "",
        "open_positions": risk.get("open_positions"),
        "marked_positions": risk.get("marked_positions"),
        "unmarked_positions": risk.get("unmarked_positions"),
        "total_cost_usdc": risk.get("total_cost_usdc"),
        "var_95_usdc": risk.get("var_95_usdc"),
        "cvar_95_usdc": risk.get("cvar_95_usdc"),
        "var_95_return": risk.get("var_95_return"),
        "cvar_95_return": risk.get("cvar_95_return"),
        "worst_position_return_pct": risk.get("worst_position_return_pct"),
        "mark_source": risk.get("mark_source"),
        "exposure_by_correlation_key": risk.get("exposure_by_correlation_key") or [],
        "exposure_by_category": risk.get("exposure_by_category") or [],
    }


def _capacity_annex(cfg: EngineConfig) -> dict[str, Any]:
    path = cfg.output_root / "maker_carry" / "maker_carry_study.json"
    study = read_json(path, default={}) or {}
    if not isinstance(study, dict) or not study:
        return {
            "status": "unavailable",
            "source_path": str(path),
            "reason": "maker-carry capital curve is missing",
            "capital_curve": [],
            "capital_for_100_per_month": None,
        }
    curve = study.get("capital_curve") if isinstance(study.get("capital_curve"), list) else []
    return {
        "status": "ok" if curve else "partial",
        "source_path": str(path),
        "reason": "" if curve else "maker-carry study has no capital curve yet",
        "study_status": study.get("status"),
        "generated_at_utc": study.get("generated_at_utc"),
        "registered_capital_usd": study.get("portfolio_capital_usd"),
        "registered_net_usd_per_day": study.get("portfolio_net_carry_usd_per_day"),
        "registered_net_usd_per_month": study.get("portfolio_net_carry_usd_per_month"),
        "capital_curve": [row for row in curve if isinstance(row, dict)],
        "capital_for_100_per_month": study.get("capital_for_100_per_month"),
        "evidence_class": "modeled_upper_bound_not_gate_input",
    }


def _cell(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        text = f"{value:.8f}".rstrip("0").rstrip(".")
    elif isinstance(value, (dict, list, tuple)):
        text = _canonical_json(value)
    else:
        text = str(value)
    return text.replace("|", "\\|").replace("\n", "<br>")


def _table(headers: list[str], rows: Iterable[Iterable[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines.extend("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)
    return lines


def _thresholds(registration: dict[str, Any], effective: dict[str, Any]) -> str:
    keys = registration.get("setting_keys") or []
    if registration.get("threshold_config_key"):
        keys = [registration["threshold_config_key"]]
    return ", ".join(f"{key}={_cell(effective.get(key))}" for key in keys) or "structural"


def _render_markdown(payload: dict[str, Any]) -> str:
    effective = payload["effective_policy"]
    risk = payload["risk_annex"]
    capacity = payload["capacity_annex"]
    lines = [
        "# Investment Policy Statement — Polymarket research system",
        "",
        f"Generated: {payload['generated_at_utc']}",
        "",
        "> Generated from enforced code constants and the loaded effective configuration. "
        "This document is reporting-only and cannot authorise paper or live orders.",
        "",
        "## Policy provenance",
        "",
        f"- Source defaults SHA-256: `{payload['source_defaults_sha256']}`",
        f"- Effective VPS configuration SHA-256: `{payload['effective_vps_config_sha256']}`",
        f"- Configuration: `{payload['config_path']}`",
        f"- Deployed revision marker: `{payload['deployed_git_sha']}`",
        "",
        "## Mandate and execution posture",
        "",
        f"- Trading mode: **{effective['trading_mode']}**",
        f"- Paper configuration enabled: **{_cell(effective['paper_trading_enabled'])}**",
        f"- Live configuration enabled: **{_cell(effective['live_trading_enabled'])}**",
        "- Repository mandate: research, dry-run, and governed paper evidence only; human action remains outside the system.",
        "",
        "## Frozen WO-50 decision policy — effective values",
        "",
    ]
    lines += _table(
        ["Setting", "Source default", "Effective deployed value"],
        ((key, DECISION_POLICY_DEFAULTS[key], effective["decision_policy"].get(key)) for key in sorted(DECISION_POLICY_DEFAULTS)),
    )
    lines += ["", "### Registered action table", ""]
    lines += _table(
        ["Priority", "Policy row", "Condition", "Indicated human action"],
        ((row["priority"], row["id"], row["condition"], row["indicated_action"]) for row in REGISTERED_ACTION_POLICY),
    )
    lines += ["", "### Registered kill criteria", ""]
    lines += _table(
        ["Criterion", "Rule", "Effective threshold"],
        ((row["id"], row["rule"], _thresholds(row, effective["decision_policy"])) for row in KILL_CRITERIA_REGISTRATION),
    )
    lines += ["", "## Registered maker gates", ""]
    lines += _table(
        ["Gate", "Rule", "Effective threshold"],
        ((row["id"], row["rule"], _thresholds(row, effective["maker_policy"])) for row in MAKER_GATE_REGISTRATION),
    )
    lines += ["", "### Maker policy constants", ""]
    lines += _table(
        ["Setting", "Source default", "Effective deployed value"],
        ((key, MAKER_POLICY_DEFAULTS[key], effective["maker_policy"].get(key)) for key in sorted(MAKER_POLICY_DEFAULTS)),
    )
    lines += ["", "## Registered taker verdict gates", ""]
    lines += _table(
        ["Gate", "Rule", "Effective settings"],
        ((row["id"], row["rule"], _thresholds(row, effective["profit_verdict"])) for row in VERDICT_GATE_DEFINITIONS),
    )
    lines += ["", "### Registered amendments 1–7", ""]
    lines += _table(
        ["Amendment", "Registered", "Direction", "Effect"],
        ((row["number"], row["registered_at_utc"], row["direction"], row["effect"]) for row in REGISTERED_AMENDMENTS),
    )
    lines += ["", "## Quote-sheet standing rules", ""]
    lines += [f"{row['number']}. **{row['title']}** — {row['text']}" for row in effective["quote_sheet_standing_rules"]]
    lines += ["", "## Effective risk controls", ""]
    lines += _table(
        ["Risk setting", "Effective configured value"],
        ((key, effective["risk"].get(key)) for key in RISK_POLICY_KEYS),
    )
    lines += ["", "## Current risk annex", ""]
    lines += _table(
        ["Measure", "Current value"],
        [
            ("Status", risk.get("status")),
            ("Source", risk.get("source_path")),
            ("Open positions", risk.get("open_positions")),
            ("Total cost exposure (USDC)", risk.get("total_cost_usdc")),
            ("VaR 95 (USDC)", risk.get("var_95_usdc")),
            ("CVaR 95 (USDC)", risk.get("cvar_95_usdc")),
            ("Worst marked position return (%)", risk.get("worst_position_return_pct")),
            ("Missing-input note", risk.get("reason")),
        ],
    )
    lines += ["", "### Exposure concentration — correlation groups", ""]
    correlation_rows = risk.get("exposure_by_correlation_key") or []
    lines += (
        _table(
            ["Correlation key", "Cost basis (USDC)"],
            ((row.get("key"), row.get("cost_basis_usdc")) for row in correlation_rows),
        )
        if correlation_rows
        else ["No correlation concentration rows available."]
    )
    lines += ["", "### Exposure concentration — categories", ""]
    category_rows = risk.get("exposure_by_category") or []
    lines += (
        _table(
            ["Category", "Cost basis (USDC)"],
            ((row.get("key"), row.get("cost_basis_usdc")) for row in category_rows),
        )
        if category_rows
        else ["No category concentration rows available."]
    )
    lines += ["", "## Capacity statement", ""]
    lines += _table(
        ["Measure", "Value"],
        [
            ("Status", capacity.get("status")),
            ("Evidence class", capacity.get("evidence_class")),
            ("Registered net/day", capacity.get("registered_net_usd_per_day")),
            ("Registered net/month", capacity.get("registered_net_usd_per_month")),
            ("First measured cap reaching $100/month", capacity.get("capital_for_100_per_month")),
            ("Missing-input note", capacity.get("reason")),
        ],
    )
    curve = capacity.get("capital_curve") or []
    lines += ["", "### Supplementary capital curve — not a gate input", ""]
    lines += (
        _table(
            ["Capital cap (USD)", "Capital used (USD)", "Modelled net/day (USD)"],
            ((row.get("capital_cap_usd"), row.get("capital_used_usd"), row.get("net_usd_per_day")) for row in curve),
        )
        if curve
        else ["No capital-curve rows available."]
    )
    lines += [
        "",
        "## Control declaration",
        "",
        "- `paper_trading_invoked = false`",
        "- `live_trading_invoked = false`",
        "- No gate, threshold, stake, broker, or order path reads this IPS.",
    ]
    return "\n".join(lines) + "\n"


def render_ips(cfg: EngineConfig) -> dict[str, Any]:
    settings = _settings(cfg)
    output_path = _safe_output_path(cfg, settings["output_file"])
    summary_path = _safe_output_path(cfg, settings["summary_file"])
    payload: dict[str, Any] = {
        "status": "disabled",
        "generated_at_utc": now_utc(),
        "config_path": str(cfg.path),
        "output_path": str(output_path),
        "source_defaults_sha256": _sha256(source_defaults_bundle()),
        "effective_vps_config_sha256": _sha256(cfg.raw),
        "deployed_git_sha": os.getenv("PM_VPS_DEPLOYED_SHA") or os.getenv("PM_IMAGE_BUILD_SHA") or "unknown",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    if not _enabled(settings.get("enabled", True)):
        write_json(summary_path, payload)
        return payload

    payload.update(
        {
            "status": "ok",
            "source_defaults": source_defaults_bundle(),
            "effective_policy": effective_policy_bundle(cfg),
            "risk_annex": _risk_annex(cfg),
            "capacity_annex": _capacity_annex(cfg),
        }
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(output_path, _render_markdown(payload))
    write_json(summary_path, payload)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return render_ips(load_config(config_path))
