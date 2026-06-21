#!/usr/bin/env python3
"""Validate config.yaml against calibration_profiles.yaml.

This script keeps model-governance checks available without changing the main
prediction CLI. It exits non-zero when the active config does not match the
named calibration profile.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    import pandas as pd
except ImportError:  # pragma: no cover - convenience fallback for minimal runtimes.
    pd = None

from superbru_score_engine.config import calibration_check_rows, load_config, validate_calibration_profile


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate active model config against calibration_profiles.yaml")
    parser.add_argument("--config", default="config.yaml", help="Path to YAML/JSON config")
    parser.add_argument("--profiles", default="calibration_profiles.yaml", help="Path to calibration profile registry")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    check = validate_calibration_profile(config, args.profiles)
    rows = calibration_check_rows(check)
    if pd is not None:
        print(pd.DataFrame(rows).to_string(index=False))
    else:
        for row in rows:
            print(f"{row['field']}: {row['value']}")
    return 0 if check.profile_found and check.matches_config else 1


if __name__ == "__main__":
    raise SystemExit(main())
