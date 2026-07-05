#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.dashboard import render_dashboard


def main() -> int:
    parser = argparse.ArgumentParser(prog="render_polymarket_dashboard.py")
    parser.add_argument("--config", default="polymarket_predictive_config.example.yaml")
    args = parser.parse_args()
    result = render_dashboard(load_config(Path(args.config)))
    print(result["dashboard_file"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
