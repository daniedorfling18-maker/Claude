from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .config import EngineConfig, load_config
from .utils import discover_files, load_yaml, write_csv, write_json

SERVICE_FIELDS = ["service_name", "image_name", "command", "script_entry_point", "category_covered", "input_files", "output_files", "polling_frequency", "environment_variables_used", "writes_raw_data", "writes_transformed_data", "writes_ml_predictions", "writes_opportunities", "writes_execution_logs", "writes_state_files", "can_place_orders", "monitor_only", "paper_only", "has_live_trading_path", "secrets_required", "data_timestamped_point_in_time", "suitable_for_model_training", "suitable_only_for_diagnostics", "known_failure_modes", "duplicate_writer_risk", "conflicting_signal_risk"]


def _infer_category(text: str) -> str:
    text = text.lower()
    for cat in ["worldcup", "sports", "soccer", "bitcoin", "crypto", "election", "finance", "trump", "fed", "all"]:
        if cat in text:
            return cat
    return "unknown"


def _script_from_command(command: Any) -> str:
    text = " ".join(command) if isinstance(command, list) else str(command or "")
    match = re.search(r"([\w./-]*polymarket[\w./-]*\.py|scripts/[\w./-]+\.py)", text)
    return match.group(1) if match else ""


def _row_from_service(compose_path: Path, name: str, service: dict[str, Any]) -> dict[str, Any]:
    text = " ".join(str(v) for v in service.values()) + " " + name
    env = service.get("environment", {})
    if isinstance(env, list):
        env_vars = ";".join(str(e).split("=")[0] for e in env)
    elif isinstance(env, dict):
        env_vars = ";".join(env.keys())
    else:
        env_vars = ""
    command = service.get("command", "")
    image = service.get("image") or service.get("build") or ""
    can_place = any(x in text.lower() for x in ["place_order", "execute", "private_key", "clob", "live"])
    has_live = any(x in text.lower() for x in ["live", "execute_live", "pm_mode"])
    outputs = ";".join(re.findall(r"outputs/[\w./-]+", text))
    return {
        "service_name": name,
        "image_name": str(image),
        "command": str(command),
        "script_entry_point": _script_from_command(command),
        "category_covered": _infer_category(text),
        "input_files": ";".join(re.findall(r"(inputs/[\w./-]+|data/[\w./-]+|work/[\w./-]+)", text)),
        "output_files": outputs,
        "polling_frequency": ";".join(re.findall(r"(?:POLL|INTERVAL|FREQUENCY)[\w_]*[=: ]+([\w.-]+)", text, flags=re.I)),
        "environment_variables_used": env_vars,
        "writes_raw_data": "raw_market_snapshots" in text,
        "writes_transformed_data": "latest_joined_snapshot" in text or "market_snapshot" in text,
        "writes_ml_predictions": "prediction" in text.lower() or "ml" in name.lower(),
        "writes_opportunities": "opportunities" in text,
        "writes_execution_logs": "execution_log" in text,
        "writes_state_files": "collector_state" in text or "heartbeat" in text,
        "can_place_orders": can_place,
        "monitor_only": not can_place,
        "paper_only": "dry_run" in text.lower() or "paper" in text.lower() or not has_live,
        "has_live_trading_path": has_live,
        "secrets_required": any(x in env_vars.upper() + text.upper() for x in ["KEY", "SECRET", "PRIVATE", "WALLET", "API"]),
        "data_timestamped_point_in_time": "timestamp" in text.lower() or "snapshot" in text.lower(),
        "suitable_for_model_training": "raw_market_snapshots" in text,
        "suitable_only_for_diagnostics": "latest_joined_snapshot" in text and "raw_market_snapshots" not in text,
        "known_failure_modes": "static inspection only, validate Docker runtime separately",
        "duplicate_writer_risk": "unknown until inventory is compared",
        "conflicting_signal_risk": "high" if can_place and "opportunities" in text else "unknown",
    }


def pipeline_inventory(cfg: EngineConfig) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    root = cfg.data_root
    compose_files = discover_files(root, ["docker-compose*.yml", "docker-compose*.yaml"])
    for path in compose_files:
        try:
            data = load_yaml(path)
        except Exception:
            continue
        services = data.get("services", {}) if isinstance(data, dict) else {}
        for name, service in services.items():
            if isinstance(service, dict):
                row = _row_from_service(path, name, service)
                row["source_file"] = str(path)
                rows.append(row)
    # Add script-only assets to inventory so static repos without compose still map entry points.
    script_files = discover_files(root, ["scripts/*polymarket*", "scripts/run_*polymarket*", "scripts/run_*pm*"])
    for script in script_files:
        text = script.read_text(encoding="utf-8", errors="replace")[:20000]
        rows.append({
            "service_name": script.name,
            "image_name": "",
            "command": f"python {script}",
            "script_entry_point": str(script),
            "category_covered": _infer_category(str(script) + text),
            "input_files": ";".join(re.findall(r"(inputs/[\w./-]+|data/[\w./-]+|work/[\w./-]+)", text)),
            "output_files": ";".join(re.findall(r"outputs/[\w./-]+", text)),
            "polling_frequency": "",
            "environment_variables_used": ";".join(sorted(set(re.findall(r"os\.getenv\(['\"]([A-Z0-9_]+)['\"]", text)))),
            "writes_raw_data": "raw_market_snapshots" in text,
            "writes_transformed_data": "latest_joined_snapshot" in text or "market_snapshot" in text,
            "writes_ml_predictions": "prediction" in text.lower(),
            "writes_opportunities": "opportunities" in text,
            "writes_execution_logs": "execution_log" in text,
            "writes_state_files": "collector_state" in text or "heartbeat" in text,
            "can_place_orders": any(x in text.lower() for x in ["place_order", "execute_live", "clob"]),
            "monitor_only": not any(x in text.lower() for x in ["place_order", "execute_live", "clob"]),
            "paper_only": "dry_run" in text.lower() or "paper" in text.lower(),
            "has_live_trading_path": "live" in text.lower() or "execute_live" in text.lower(),
            "secrets_required": any(x in text.upper() for x in ["PRIVATE_KEY", "API_KEY", "SECRET", "WALLET"]),
            "data_timestamped_point_in_time": "timestamp" in text.lower() or "snapshot" in text.lower(),
            "suitable_for_model_training": "raw_market_snapshots" in text,
            "suitable_only_for_diagnostics": "latest_joined_snapshot" in text and "raw_market_snapshots" not in text,
            "known_failure_modes": "static script inspection only",
            "duplicate_writer_risk": "unknown until compared with other scripts",
            "conflicting_signal_risk": "high" if "opportunities" in text and "execute" in text.lower() else "unknown",
            "source_file": str(script),
        })
    outputs: dict[str, int] = {}
    for row in rows:
        for out in str(row.get("output_files", "")).split(";"):
            if out:
                outputs[out] = outputs.get(out, 0) + 1
    for row in rows:
        duplicate = [out for out in str(row.get("output_files", "")).split(";") if outputs.get(out, 0) > 1]
        row["duplicate_writer_risk"] = "high: " + ";".join(duplicate) if duplicate else "low"
    out = cfg.governance_root
    write_csv(out / "docker_service_inventory.csv", rows, fieldnames=[*SERVICE_FIELDS, "source_file"])
    write_json(out / "docker_service_inventory.json", rows)
    write_pipeline_map(out / "../../docs/POLYMARKET_PIPELINE_MAP.md", rows)
    write_pipeline_map(Path("docs/POLYMARKET_PIPELINE_MAP.md"), rows)
    return rows


def write_pipeline_map(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = ["# Polymarket Pipeline Map", "", "Generated from static repository inspection.", "", "| Service or Script | Category | Can place orders | Training suitable | Outputs |", "|---|---:|---:|---:|---|"]
    for row in rows:
        lines.append(f"| {row.get('service_name','')} | {row.get('category_covered','')} | {row.get('can_place_orders','')} | {row.get('suitable_for_model_training','')} | {row.get('output_files','')} |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(config_path: str) -> list[dict[str, Any]]:
    return pipeline_inventory(load_config(config_path))
