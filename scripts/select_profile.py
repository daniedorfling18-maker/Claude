#!/usr/bin/env python3
"""Stamp the active calibration profile into config.yaml.

calibration_profiles.yaml is the source of truth for the named calibrations.
The engine only reads the flat model.* keys in config.yaml, so this copies the
active profile's values into those keys IN PLACE (comments and every other
setting are preserved). Use this instead of hand-editing the model calibration
or copying config.example.yaml over config.yaml.

Usage (with your Python):
    & $python scripts\\select_profile.py                  # use active_calibration_profile
    & $python scripts\\select_profile.py --profile worldcup_1x2
    & $python scripts\\select_profile.py --list
"""
from __future__ import annotations

import argparse
import pathlib
import re

import yaml

MANAGED_KEYS = ("devig_method", "dixon_coles_rho", "odds_weight", "ratings_weight")


def fmt(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)  # 1.0 -> "1.0", -0.04 -> "-0.04"
    return str(value)


def set_key(text: str, key: str, value) -> str:
    pat = re.compile(rf"^(?P<indent>[ \t]*){re.escape(key)}:[ \t]*\S.*$", re.M)
    hits = pat.findall(text)
    if len(hits) != 1:
        raise SystemExit(f"Expected exactly one '{key}:' line in config.yaml, found {len(hits)}.")
    return pat.sub(lambda m: f"{m.group('indent')}{key}: {fmt(value)}", text, count=1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profiles", default="calibration_profiles.yaml")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--profile", default=None, help="override active_calibration_profile")
    ap.add_argument("--list", action="store_true", help="list profiles and exit")
    args = ap.parse_args()

    doc = yaml.safe_load(pathlib.Path(args.profiles).read_text(encoding="utf-8"))
    profiles = doc.get("calibration_profiles", {})
    active = doc.get("active_calibration_profile")

    if args.list:
        for name, vals in profiles.items():
            mark = "*" if name == active else " "
            print(f"{mark} {name}: {vals}")
        return 0

    name = args.profile or active
    if name not in profiles:
        raise SystemExit(f"Profile '{name}' not found. Available: {', '.join(profiles)}")
    profile = profiles[name]
    missing = [k for k in MANAGED_KEYS if k not in profile]
    if missing:
        raise SystemExit(f"Profile '{name}' is missing keys: {missing}")

    cfg_path = pathlib.Path(args.config)
    text = cfg_path.read_text(encoding="utf-8")
    for key in MANAGED_KEYS:
        text = set_key(text, key, profile[key])
    cfg_path.write_text(text, encoding="utf-8")
    yaml.safe_load(cfg_path.read_text(encoding="utf-8"))  # sanity re-parse

    print(f"Applied profile '{name}' to {args.config}:")
    for key in MANAGED_KEYS:
        print(f"  {key}: {profile[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
