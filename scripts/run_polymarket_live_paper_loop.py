#!/usr/bin/env python3
"""Run a strictly paper Polymarket loop on live market snapshots.

Each iteration:
1. forces scanner-only/no-live-execution environment flags;
2. scans live Polymarket order books into ``market_snapshot.csv``;
3. ingests that snapshot into the canonical raw snapshot stream;
4. optionally refreshes the optimized ML artifact;
5. runs the canonical forward paper cycle: features -> predictions -> signals ->
   persistent paper broker;
6. writes a heartbeat JSON for monitoring.

No private key, wallet, CLOB client, or live executor is used here.
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import polymarket_mispricing_bot as scanner  # noqa: E402
from polymarket_predictive_engine.cohort_validation import write_signal_cohort_pnl  # noqa: E402
from polymarket_predictive_engine.config import load_config  # noqa: E402
from polymarket_predictive_engine.dashboard import render_dashboard  # noqa: E402
from polymarket_predictive_engine.mispricing_alpha import train_mispricing_alpha_model  # noqa: E402
from polymarket_predictive_engine.models.optimized import train_optimized_model  # noqa: E402
from polymarket_predictive_engine.paper_cycle import run_paper_cycle  # noqa: E402
from polymarket_predictive_engine.shadow_cohort import update_shadow_cohort_evidence  # noqa: E402
from polymarket_predictive_engine.snapshot_ingest import ingest_scanner_snapshot  # noqa: E402
from polymarket_predictive_engine.storage import connect_db  # noqa: E402
from polymarket_predictive_engine.strategy_search import run_edge_strategy_search  # noqa: E402
from polymarket_predictive_engine.utils import now_utc, write_json  # noqa: E402
from run_polymarket_liquidity_discovery import run_liquidity_discovery  # noqa: E402
from run_promoted_rule_shadow_scan import run_promoted_rule_shadow_scan  # noqa: E402


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _current_memory_percent() -> float | None:
    """Return host/container memory load using only stdlib, or None if unavailable."""
    if os.name == "nt":
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return float(status.dwMemoryLoad)
        except Exception:
            return None
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        try:
            values: dict[str, float] = {}
            for line in meminfo.read_text(encoding="utf-8", errors="ignore").splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    values[parts[0].rstrip(":")] = float(parts[1])
            total = values.get("MemTotal")
            available = values.get("MemAvailable")
            if total and available is not None:
                return max(0.0, min(100.0, 100.0 * (1.0 - available / total)))
        except Exception:
            return None
    return None


def _resource_guard(cfg) -> dict[str, Any]:
    settings = cfg.raw.get("runtime_resource_guard", {}) or {}
    memory_percent = _current_memory_percent()
    max_memory = float(settings.get("max_memory_percent", 92.0))
    enabled = _truthy(settings.get("enabled", True), default=True)
    skip = bool(enabled and memory_percent is not None and memory_percent >= max_memory)
    return {
        "enabled": enabled,
        "skip_cycle": skip,
        "memory_percent": memory_percent,
        "max_memory_percent": max_memory,
        "reason": "memory_above_limit" if skip else "ok",
    }


def _scheduled(settings: dict[str, Any], key: str, iteration: int, *, default: int = 1) -> bool:
    every = int(settings.get(key, default) or default)
    if every <= 0:
        return False
    return iteration == 1 or iteration % every == 0


def _run_settlement_only_cycle(cfg) -> dict[str, Any]:
    """Cheap maintenance path: settle expired shadow positions without a full market scan."""
    shadow = update_shadow_cohort_evidence(cfg, [])
    cohort: dict[str, Any]
    try:
        con = connect_db(cfg.database_path)
        try:
            cohort = write_signal_cohort_pnl(con, cfg)
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001 - settlement-only must not kill the loop
        cohort = {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "status": "settlement_only",
        "shadow": {
            "status": shadow.get("status"),
            "open_positions": shadow.get("open_positions"),
            "closed_this_cycle": shadow.get("closed_this_cycle"),
            "settlement_checks": shadow.get("settlement_checks"),
            "settled_positions": shadow.get("settled_positions"),
            "settlement_errors": shadow.get("settlement_errors"),
            "promoted_cohorts": shadow.get("promoted_cohorts", []),
        },
        "cohort_pnl": {
            "status": cohort.get("status"),
            "promoted_cohorts": cohort.get("promoted_cohorts", []),
        },
    }


def _abs(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def force_paper_environment() -> None:
    os.environ["PM_MODE"] = "scan"
    os.environ["POLYMARKET_MODE"] = "scan"
    os.environ["POLYMARKET_EXECUTE_LIVE"] = "false"
    os.environ["POLYMARKET_LIVE_TRADING"] = "0"
    os.environ.setdefault("POLYMARKET_QUERY", "world cup")
    os.environ.setdefault("POLYMARKET_MODEL_PROBABILITIES_CSV", "inputs/polymarket/model_probabilities.csv")
    os.environ.setdefault("POLYMARKET_POSITIONS_CSV", "inputs/polymarket/positions.csv")
    os.environ.setdefault("POLYMARKET_OUTPUT_DIR", "outputs/polymarket")
    os.environ.setdefault("POLYMARKET_MIN_EDGE", "0.04")


def ensure_scanner_runtime_files() -> None:
    model_csv = _abs(Path(os.environ["POLYMARKET_MODEL_PROBABILITIES_CSV"]))
    positions_csv = _abs(Path(os.environ["POLYMARKET_POSITIONS_CSV"]))
    output_dir = _abs(Path(os.environ["POLYMARKET_OUTPUT_DIR"]))
    model_csv.parent.mkdir(parents=True, exist_ok=True)
    positions_csv.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    if not model_csv.exists():
        model_csv.write_text("token_id,probability\n", encoding="utf-8")
    if not positions_csv.exists():
        positions_csv.write_text("token_id,shares\n", encoding="utf-8")


def scan_once() -> dict[str, Any]:
    config = scanner.BotConfig.from_env()
    config = dataclasses.replace(
        config,
        mode="scan",
        execute_live=False,
        model_csv=_abs(config.model_csv),
        positions_csv=_abs(config.positions_csv),
        output_dir=_abs(config.output_dir),
    )
    token_count, opportunity_count = scanner.run_once(config, live_executor=None)
    return {
        "tokens": token_count,
        "scanner_opportunities": opportunity_count,
        "snapshot_path": str(config.output_dir / "market_snapshot.csv"),
    }


def refresh_repo_worldcup_fundamentals(cfg) -> dict[str, Any]:
    settings = cfg.raw.get("mispricing_alpha", {})
    if not settings.get("use_fundamental_probabilities", True):
        return {"status": "disabled"}
    script = ROOT / "scripts" / "build_repo_worldcup_winner_probabilities.py"
    if not script.exists():
        return {"status": "missing_script", "script": str(script)}
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(ROOT),
            check=False,
            capture_output=True,
            text=True,
            timeout=float(settings.get("fundamental_refresh_timeout_seconds", 90)),
        )
    except Exception as exc:  # noqa: BLE001 - fundamentals are additive, not a reason to stop paper collection
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    return {
        "status": "ran" if result.returncode == 0 else "error",
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-2000:],
        "stderr_tail": result.stderr[-2000:],
    }


def run_iteration(*, config_path: Path, optimize_model: bool, iteration: int, paper_source: str = "raw_snapshot") -> dict[str, Any]:
    force_paper_environment()
    ensure_scanner_runtime_files()
    cfg = load_config(config_path)
    if cfg.trading_mode not in {"paper", "backtest"}:
        raise RuntimeError(f"trading.mode is {cfg.trading_mode}; expected paper or backtest")

    guard = _resource_guard(cfg)
    write_json(cfg.governance_root / "runtime_resource_guard.json", {**guard, "generated_at_utc": now_utc()})
    if guard.get("skip_cycle"):
        heartbeat = {
            "status": "skipped_resource_guard",
            "generated_at_utc": now_utc(),
            "iteration": iteration,
            "live_data": False,
            "live_trading": False,
            "resource_guard": guard,
            "message": "Heavy paper cycle skipped to protect local machine resources.",
        }
        write_json(cfg.governance_root / "live_paper_loop_heartbeat.json", heartbeat)
        if _truthy((cfg.raw.get("runtime_resource_guard", {}) or {}).get("render_dashboard_on_skip", False)):
            heartbeat["dashboard"] = render_dashboard(cfg)
        return heartbeat

    schedule = cfg.raw.get("runtime_schedule", {}) or {}
    if not _scheduled(schedule, "full_scan_every_iterations", iteration, default=1):
        settlement = _run_settlement_only_cycle(cfg)
        heartbeat = {
            "status": "settlement_only",
            "generated_at_utc": now_utc(),
            "iteration": iteration,
            "live_data": False,
            "live_trading": False,
            "resource_guard": guard,
            "runtime_schedule": {
                "full_scan_ran": False,
                "settlement_only_ran": True,
            },
            "settlement": settlement,
        }
        write_json(cfg.governance_root / "live_paper_loop_heartbeat.json", heartbeat)
        return heartbeat

    scan = scan_once()
    fundamentals = refresh_repo_worldcup_fundamentals(cfg)
    ingest = ingest_scanner_snapshot(cfg, snapshot_path=scan["snapshot_path"])
    optimization: dict[str, Any] = {"status": "skipped"}
    if (
        optimize_model
        and bool(cfg.raw.get("ml_optimization", {}).get("enabled", True))
        and _scheduled(schedule, "optimize_model_every_iterations", iteration, default=12)
    ):
        optimization = train_optimized_model(cfg)
    alpha: dict[str, Any] = {"status": "skipped"}
    if bool(cfg.raw.get("mispricing_alpha", {}).get("enabled", True)) and _scheduled(
        schedule,
        "mispricing_alpha_every_iterations",
        iteration,
        default=12,
    ):
        alpha = train_mispricing_alpha_model(cfg)
    strategy_search = (
        run_edge_strategy_search(cfg)
        if bool(cfg.raw.get("edge_strategy_search", {}).get("enabled", True))
        and _scheduled(schedule, "edge_strategy_search_every_iterations", iteration, default=12)
        else {"status": "skipped"}
    )
    paper = run_paper_cycle(cfg, source=paper_source)
    promoted_rule_shadow = (
        run_promoted_rule_shadow_scan(cfg)
        if bool(cfg.raw.get("promoted_rule_shadow", {}).get("enabled", True))
        and _scheduled(schedule, "promoted_rule_shadow_every_iterations", iteration, default=3)
        else {"status": "skipped"}
    )
    liquidity_discovery = (
        run_liquidity_discovery(cfg)
        if bool(cfg.raw.get("liquidity_discovery", {}).get("enabled", True))
        and _scheduled(schedule, "liquidity_discovery_every_iterations", iteration, default=12)
        else {"status": "skipped"}
    )
    heartbeat = {
        "status": "ran",
        "generated_at_utc": now_utc(),
        "iteration": iteration,
        "live_data": True,
        "live_trading": False,
        "scan": scan,
        "fundamentals": fundamentals,
        "ingest": ingest,
        "optimization": {
            "status": optimization.get("status"),
            "deployment_mode": optimization.get("deployment_mode"),
            "model_version": optimization.get("model_version"),
            "holdout": optimization.get("holdout", {}),
        },
        "mispricing_alpha": {
            "status": alpha.get("status"),
            "deployment_mode": alpha.get("deployment_mode"),
            "model_version": alpha.get("model_version"),
            "training_rows": alpha.get("training_rows"),
            "training_markets": alpha.get("training_markets"),
        },
        "edge_strategy_search": {
            "status": strategy_search.get("status"),
            "promotable_rules": strategy_search.get("promotable_rules"),
            "top_rules": strategy_search.get("top_rules", [])[:3],
        },
        "promoted_rule_shadow": {
            "status": promoted_rule_shadow.get("status"),
            "promoted_rules": promoted_rule_shadow.get("promoted_rules"),
            "exploratory_rules": promoted_rule_shadow.get("exploratory_rules"),
            "rules_scanned": promoted_rule_shadow.get("rules_scanned"),
            "candidates": promoted_rule_shadow.get("candidates"),
            "rejected": promoted_rule_shadow.get("rejected"),
            "scans": promoted_rule_shadow.get("scans", [])[:3],
        },
        "liquidity_discovery": {
            "status": liquidity_discovery.get("status"),
            "tokens_scanned": liquidity_discovery.get("tokens_scanned"),
            "tradable_tokens": liquidity_discovery.get("tradable_tokens"),
            "top_tradable": liquidity_discovery.get("top_tradable", [])[:5],
            "family_summary": liquidity_discovery.get("family_summary", [])[:10],
        },
        "resource_guard": guard,
        "runtime_schedule": {
            "full_scan_ran": True,
            "settlement_only_ran": False,
            "optimize_model_ran": optimization.get("status") != "skipped",
            "mispricing_alpha_ran": alpha.get("status") != "skipped",
            "edge_strategy_search_ran": strategy_search.get("status") != "skipped",
            "promoted_rule_shadow_ran": promoted_rule_shadow.get("status") != "skipped",
            "liquidity_discovery_ran": liquidity_discovery.get("status") != "skipped",
        },
        "paper": paper,
    }
    write_json(cfg.governance_root / "live_paper_loop_heartbeat.json", heartbeat)
    if _scheduled(schedule, "dashboard_render_every_iterations", iteration, default=2):
        heartbeat["dashboard"] = render_dashboard(cfg)
    else:
        heartbeat["dashboard"] = {"status": "skipped_by_runtime_schedule"}
    return heartbeat


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_polymarket_live_paper_loop.py")
    parser.add_argument("--config", default="polymarket_predictive_config.example.yaml")
    parser.add_argument("--iterations", type=int, default=1, help="Number of cycles to run; 0 means run forever.")
    parser.add_argument("--sleep-seconds", type=float, default=60.0)
    parser.add_argument("--optimize-model", action="store_true")
    parser.add_argument("--paper-source", choices=["raw_snapshot", "websocket"], default="raw_snapshot")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = _abs(Path(args.config))
    failures = 0
    iteration = 0
    while True:
        iteration += 1
        try:
            heartbeat = run_iteration(
                config_path=config_path,
                optimize_model=args.optimize_model,
                iteration=iteration,
                paper_source=args.paper_source,
            )
            print(f"live-paper-loop iteration={iteration} status={heartbeat['status']}", flush=True)
        except Exception as exc:
            failures += 1
            traceback.print_exc()
            try:
                cfg = load_config(config_path)
                write_json(
                    cfg.governance_root / "live_paper_loop_heartbeat.json",
                    {
                        "status": "error",
                        "generated_at_utc": now_utc(),
                        "iteration": iteration,
                        "live_trading": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
            except Exception:
                pass
        if args.iterations > 0 and iteration >= args.iterations:
            break
        if args.iterations == 0 or iteration < args.iterations:
            time.sleep(max(0.0, args.sleep_seconds))
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
