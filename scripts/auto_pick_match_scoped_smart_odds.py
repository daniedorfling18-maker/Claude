"""Alias-aware live entrypoint for match-scoped Auto Pick.

This file is the production entrypoint used by the scheduled workflow. It keeps
the full live-recompute behaviour from `auto_pick_match_scoped.py`, then patches
team matching and browser submission to be alias-aware.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any

from team_name_aliases import canonical_team_key


def load_base_module():
    base_path = Path(__file__).with_name("auto_pick_match_scoped.py")
    spec = importlib.util.spec_from_file_location("auto_pick_match_scoped", base_path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Could not load base auto-pick module: {base_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = load_base_module()
base.norm_team = canonical_team_key


async def submit_pick_alias_aware(args: Any, home_team: str, away_team: str, pick: str, out_dir: Path) -> dict[str, Any]:
    submit_path = Path(__file__).with_name("submit_superbru_pick_cdp_aliases.py")
    spec = importlib.util.spec_from_file_location("submit_superbru_pick_cdp_aliases", submit_path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Could not load alias-aware submit script: {submit_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    submit_args = types.SimpleNamespace(
        launch=True,
        headless=args.headless,
        email=args.email,
        password=args.password,
        login_url=args.login_url,
        pool_url=args.pool_url,
        home_team=home_team,
        away_team=away_team,
        new_pick=pick,
        settle_ms=8000,
        timeout_ms=60000,
        dry_run=args.dry_run,
        inspect_only=False,
        diagnostics_dir=str(out_dir / "submit_diagnostics"),
        cdp_url="http://127.0.0.1:9222",
    )
    return await mod.run(submit_args)


base.submit_pick = submit_pick_alias_aware
build_parser = base.build_parser
run = base.run


def main() -> int:
    args = base.build_parser().parse_args()
    if not args.email or not args.password:
        base.write_missing_credentials_summary(args)
        print("ERROR: SUPERBRU_EMAIL and SUPERBRU_PASSWORD must be set", file=sys.stderr)
        return 1
    result = asyncio.run(base.run(args))
    result["alias_aware_entrypoint"] = True
    print("\n" + json.dumps(result, indent=2, default=str))
    return base.exit_code_for_result(result, args)


if __name__ == "__main__":
    raise SystemExit(main())
