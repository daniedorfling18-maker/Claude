"""
Backward-compatible shim for the smart-odds auto-pick runner.

The smart-odds quota guard (skip the Odds API pull and submit when SuperBru already
shows the committed card pick) is now built into the base runner
``auto_pick_match_scoped.py`` and is on by default (``--smart-odds``). The base runner
additionally adds live pre-kickoff recompute, leaderboard pool-position intelligence,
and Oddspedia correct-score grid blending, which this thin wrapper never had.

This file is kept only so any external caller referencing it keeps working: it delegates
entirely to the base runner. Prefer calling ``scripts/auto_pick_match_scoped.py`` directly.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_base():
    base_path = Path(__file__).with_name("auto_pick_match_scoped.py")
    spec = importlib.util.spec_from_file_location("auto_pick_match_scoped", base_path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Could not load base auto-pick module: {base_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = _load_base()
# Re-export for callers that imported helpers from this module historically.
run = base.run
build_parser = base.build_parser
main = base.main


if __name__ == "__main__":
    raise SystemExit(main())
