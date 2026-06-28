#!/usr/bin/env python3
"""Normalise the local Polymarket example config after emergency overrides.

The quick PowerShell workaround for a rejected Gamma public-search query appended a
second top-level ``liquidity_discovery:`` block. PyYAML keeps the last duplicate
key, which means important settings such as ``event_limit`` and ``token_limit``
fall back to code defaults. This utility keeps the first real block, removes later
duplicate blocks, and sets ``public_search_enabled: false`` inside the retained
block.

This script is intentionally text-based so it does not require any extra YAML
formatting dependency. It does not touch trading/risk thresholds.
"""
from __future__ import annotations

import argparse
from pathlib import Path


TOP_LEVEL_PREFIXES_TO_KEEP = (" ", "\t", "-", "#", "\n", "\r\n")


def _is_top_level_key(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped and not line.startswith((" ", "\t", "-")) and stripped.endswith(":"))


def _normalise_lines(lines: list[str]) -> tuple[list[str], dict[str, int | bool]]:
    output: list[str] = []
    seen_liquidity = False
    removed_duplicate_blocks = 0
    in_liquidity = False
    inserted_public_search = False
    replaced_public_search = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("liquidity_discovery:"):
            if seen_liquidity:
                removed_duplicate_blocks += 1
                i += 1
                while i < len(lines) and not _is_top_level_key(lines[i]):
                    i += 1
                continue
            seen_liquidity = True
            in_liquidity = True
            output.append(line)
            i += 1
            continue

        if in_liquidity and _is_top_level_key(line):
            if not inserted_public_search and not replaced_public_search:
                output.append("  public_search_enabled: false\n")
                inserted_public_search = True
            in_liquidity = False

        if in_liquidity and line.strip().startswith("public_search_enabled:"):
            output.append("  public_search_enabled: false\n")
            replaced_public_search = True
            i += 1
            continue

        output.append(line)
        i += 1

    if in_liquidity and not inserted_public_search and not replaced_public_search:
        output.append("  public_search_enabled: false\n")
        inserted_public_search = True

    return output, {
        "liquidity_block_seen": seen_liquidity,
        "removed_duplicate_liquidity_blocks": removed_duplicate_blocks,
        "inserted_public_search_enabled": inserted_public_search,
        "replaced_public_search_enabled": replaced_public_search,
    }


def normalise_config(path: Path, *, dry_run: bool = False) -> dict[str, object]:
    original = path.read_text(encoding="utf-8").splitlines(keepends=True)
    normalised, stats = _normalise_lines(original)
    changed = normalised != original
    if changed and not dry_run:
        path.write_text("".join(normalised), encoding="utf-8")
    return {
        "status": "ok",
        "path": str(path),
        "changed": changed,
        "dry_run": dry_run,
        **stats,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="normalise_polymarket_config.py")
    parser.add_argument("path", nargs="?", default="polymarket_predictive_config.example.yaml")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(normalise_config(Path(args.path), dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
