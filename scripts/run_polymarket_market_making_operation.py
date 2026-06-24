#!/usr/bin/env python3
"""Standalone Polymarket market-making evidence operation.

This is deliberately separate from the scheduled mispricing scanner. It runs the
full dry-run mechanics repeatedly so market-making can be evaluated as its own
operation:

cycle 1: build current maker quotes and store state
cycle 2+: evaluate previous quotes against a fresh book, then store new quotes

No live orders are placed. This is evidence collection for quote touch-rate and
adverse-selection markout, not trading execution.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import run_polymarket_pipeline as pipeline

ROOT = Path(__file__).resolve().parents[1]


def run_evidence_cycle(cycle: int) -> None:
    print("=" * 80, flush=True)
    print(f"market-making evidence operation: cycle {cycle}", flush=True)
    print("=" * 80, flush=True)
    pipeline.safe_dry_run_env()
    pipeline.ensure_runtime_files()
    pipeline.run_scanner_once(f"mm-evidence-{cycle}-bootstrap")
    pipeline.run_converter()
    pipeline.run_scanner_once(f"mm-evidence-{cycle}-final")
    pipeline.run_long_short()
    pipeline.run_market_making_eval()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run standalone Polymarket market-making evidence collection")
    parser.add_argument("--cycles", type=int, default=3, help="Number of evidence cycles to run")
    parser.add_argument("--sleep-seconds", type=int, default=90, help="Delay between cycles")
    args = parser.parse_args()

    cycles = max(1, args.cycles)
    sleep_seconds = max(0, args.sleep_seconds)

    for cycle in range(1, cycles + 1):
        run_evidence_cycle(cycle)
        if cycle < cycles and sleep_seconds:
            print(f"market-making evidence operation: sleeping {sleep_seconds}s before next cycle", flush=True)
            time.sleep(sleep_seconds)

    print("market-making evidence operation: complete", flush=True)
    print("outputs:", flush=True)
    print("  outputs/polymarket/mm_quote_state.csv", flush=True)
    print("  outputs/polymarket/mm_quote_evaluations.csv", flush=True)
    print("  outputs/polymarket/mm_quote_summary.csv", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
