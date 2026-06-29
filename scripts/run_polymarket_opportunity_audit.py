#!/usr/bin/env python3
"""Run the Polymarket collected-data opportunity audit.

Read-only: this script analyses existing local outputs and writes Strategy V2
research reports only. It does not generate trade signals, paper orders, or live
orders.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from polymarket_predictive_engine.opportunity_audit import run  # noqa: E402


def main() -> int:
    config_path = sys.argv[1] if len(sys.argv) > 1 else "polymarket_predictive_config.example.yaml"
    print(json.dumps(run(config_path), indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
