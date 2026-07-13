#!/usr/bin/env python3
"""Run WO-73 telemetry/archive credential guard without printing values."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from polymarket_predictive_engine.credential_guard import scan_telemetry_credentials


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    result = scan_telemetry_credentials(args.repo_root)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("status", "files_scanned", "finding_count")}, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
