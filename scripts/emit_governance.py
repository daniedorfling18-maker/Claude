#!/usr/bin/env python3
"""Emit the model run manifest and assumption register for audit/reproducibility.

Writes outputs/governance/{run_manifest,assumption_register}.json plus a readable
assumption_register.md, so any prediction run can be reproduced (config fingerprint,
code version, environment, seed) and its assumptions reviewed without reading source.
"""
from __future__ import annotations

import argparse
import json
import os

from superbru_score_engine.config import load_config
from superbru_score_engine.governance import assumption_register, run_manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--inputs", nargs="*", default=[], help="input files to fingerprint (e.g. odds snapshot)")
    ap.add_argument("--out-dir", default="outputs/governance")
    args = ap.parse_args()

    config = load_config(args.config)
    manifest = run_manifest(config, inputs=args.inputs)
    register = assumption_register(config)

    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, "run_manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
    with open(os.path.join(args.out_dir, "assumption_register.json"), "w", encoding="utf-8") as handle:
        json.dump(register, handle, indent=2)

    lines = ["# Assumption register", "", f"Config fingerprint: `{manifest['config_fingerprint_sha256']}`",
             f"Generated: {manifest['generated_at_utc']}", "",
             "| Assumption | Value | Rationale |", "|---|---|---|"]
    for entry in register:
        value = json.dumps(entry["value"]) if isinstance(entry["value"], (dict, list)) else entry["value"]
        rationale = entry["rationale"].replace("\n", " ")
        lines.append(f"| {entry['assumption']} | {value} | {rationale} |")
    with open(os.path.join(args.out_dir, "assumption_register.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")

    print(f"Wrote run manifest + assumption register to {args.out_dir}")
    print(f"  config fingerprint: {manifest['config_fingerprint_sha256']}")
    print(f"  git: {manifest['git'].get('commit')} dirty={manifest['git'].get('dirty')}")
    print(f"  assumptions documented: {len(register)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
