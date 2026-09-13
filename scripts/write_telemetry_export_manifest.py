#!/usr/bin/env python3
"""WO-172: a per-file manifest for the telemetry mirror, so no extract is unlabelled.

`push_vps_telemetry.sh` caps every mirrored file: a CSV over `MAX_FILE_KB` is copied as its
header plus its last `CSV_TAIL_LINES` rows, and an oversized non-CSV is skipped entirely. Nothing
in the mirror said so. A reader downstream therefore saw "-$218.01 across 200 closed positions"
and had no way to learn that 200 is the truncation depth, not the population.

This writes `telemetry/export_manifest.json` beside the snapshot: for every mirrored file, the
source and exported line counts, whether it was truncated and by which rule, both content hashes,
a schema hash, and the source's modification time. Files the copier skipped are listed with the
reason. The filters block records the argument values actually in effect, not the script defaults,
so a mirror published under an environment override still says what it did.

Fail-closed: any failure exits 1, the caller stamps `manifest_failed`, and nothing is pushed. An
unlabelled extract is worse than no extract.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from polymarket_predictive_engine.credential_guard import (  # noqa: E402
    PAYLOAD_EXCLUDED_FRAGMENTS,
    _scan_csv,
    _scan_json,
)

MANIFEST_NAME = "export_manifest.json"
COPIED_SUFFIXES = (".json", ".md", ".csv", ".log")
# The copier walks each whitelisted directory with `find -maxdepth 2`; anything deeper is out of
# its scope and therefore out of this manifest's.
COPIER_MAXDEPTH = 2
MODE_MIRROR = "mirror"
MODE_FULL = "full"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _lines_after_header(path: Path) -> int:
    """Newline-delimited lines after the first — the unit `tail -n` truncates by."""
    with path.open("rb") as handle:
        total = sum(1 for _ in handle)
    return max(total - 1, 0)


def _schema_sha256(path: Path) -> str | None:
    """CSV: the header line. JSON: the sorted top-level key list. Anything else: None.

    The key name carries the fragment `sha` so `credential_guard._inspect_field` exempts it;
    a 64-hex value under an unexempted key would block every telemetry push.
    """
    if path.suffix == ".csv":
        with path.open("rb") as handle:
            header = handle.readline()
        return hashlib.sha256(header.rstrip(b"\r\n")).hexdigest()
    if path.suffix == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            return None
        keys = sorted(payload) if isinstance(payload, dict) else []
        return hashlib.sha256(json.dumps(keys, sort_keys=True).encode("utf-8")).hexdigest()
    return None


def _mtime_utc(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _excluded(relative: str) -> bool:
    return any(fragment in relative for fragment in PAYLOAD_EXCLUDED_FRAGMENTS)


def _source_candidates(repo_root: Path, whitelist_dirs: list[str]) -> list[str]:
    """Mirror the copier's walk: each whitelisted directory, depth 2, the four copied suffixes."""
    found: list[str] = []
    for relative_dir in whitelist_dirs:
        base = repo_root / relative_dir
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in COPIED_SUFFIXES:
                continue
            if len(path.relative_to(base).parts) > COPIER_MAXDEPTH:
                continue
            relative = path.relative_to(repo_root).as_posix()
            if _excluded(relative):
                continue
            found.append(relative)
    return sorted(found)


def build_manifest(
    *,
    snapshot_dir: Path,
    repo_root: Path,
    as_of: str,
    max_file_kb: int,
    csv_tail_lines: int,
    whitelist_dirs: list[str],
    mode: str = MODE_MIRROR,
) -> dict[str, object]:
    telemetry = snapshot_dir / "telemetry"
    files: list[dict[str, object]] = []
    exported_relatives: set[str] = set()
    for exported in sorted(p for p in telemetry.rglob("*") if p.is_file()):
        relative = exported.relative_to(telemetry).as_posix()
        if relative == MANIFEST_NAME:
            continue  # the manifest never describes itself
        exported_relatives.add(relative)
        # The one renamed file: the host manifest is copied to telemetry/manifest.json.
        source_relative = "outputs/performance/vps_telemetry_manifest.json" if relative == "manifest.json" else relative
        source = repo_root / source_relative
        exported_sha = _sha256(exported)
        entry: dict[str, object] = {
            "path": relative,
            "source_path": source_relative,
            "exported_bytes": exported.stat().st_size,
            "exported_sha256": exported_sha,
            "schema_sha256": _schema_sha256(exported),
            "snapshot_utc": as_of,
        }
        is_csv = exported.suffix == ".csv"
        entry["exported_lines_after_header"] = _lines_after_header(exported) if is_csv else None
        if source.is_file():
            source_sha = _sha256(source)
            entry["source_bytes"] = source.stat().st_size
            entry["source_sha256"] = source_sha
            entry["source_mtime_utc"] = _mtime_utc(source)
            entry["source_lines_after_header"] = _lines_after_header(source) if is_csv else None
            source_lines = entry["source_lines_after_header"]
            exported_lines = entry["exported_lines_after_header"]
            entry["truncated"] = bool(
                isinstance(source_lines, int) and isinstance(exported_lines, int) and source_lines > exported_lines
            )
            # The rule is derived from the snapshot itself, never from a size boundary: a file
            # that happens to sit under the cap and a file the copier passed through whole are
            # both "whole" because their bytes match.
            entry["truncation_rule"] = "whole" if exported_sha == source_sha else f"header_plus_tail:{csv_tail_lines}"
        else:
            entry["source_bytes"] = None
            entry["source_sha256"] = None
            entry["source_mtime_utc"] = None
            entry["source_lines_after_header"] = None
            entry["truncated"] = False
            entry["truncation_rule"] = "source_absent"
        files.append(entry)

    skipped: list[dict[str, object]] = []
    for source_relative in _source_candidates(repo_root, whitelist_dirs):
        if source_relative in exported_relatives:
            continue
        source = repo_root / source_relative
        try:
            size_kb = source.stat().st_size // 1024
        except OSError:
            skipped.append({"source_path": source_relative, "reason": "size_unreadable"})
            continue
        if source.suffix != ".csv" and size_kb > max_file_kb:
            skipped.append({"source_path": source_relative, "reason": f"oversized_non_csv:{size_kb}"})
        else:
            skipped.append({"source_path": source_relative, "reason": "size_unreadable"})

    if mode == MODE_FULL:
        truncated = [entry["path"] for entry in files if entry["truncated"]]
        if truncated:
            raise RuntimeError(f"--mode full requires whole files; truncated: {truncated}")

    return {
        "work_order": "WO-172",
        "mode": mode,
        "generated_at_utc": as_of,
        "snapshot_utc": as_of,
        "file_count": len(files),
        "truncated_file_count": sum(1 for entry in files if entry["truncated"]),
        "filters": {
            "snapshot_dir": str(snapshot_dir),
            "repo_root": str(repo_root),
            "as_of": as_of,
            "max_file_kb": max_file_kb,
            "csv_tail_lines": csv_tail_lines,
            "whitelist_dirs": list(whitelist_dirs),
            "name_exclusions": list(PAYLOAD_EXCLUDED_FRAGMENTS),
        },
        "files": files,
        "skipped": skipped,
        "note": (
            "Every truncated entry is an EXTRACT: source_lines_after_header is the population and "
            "exported_lines_after_header is what the mirror carries. A figure computed from a "
            "truncated file describes the extract, not the population."
        ),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }


def scan_credentials(directory: Path, repo_root: Path) -> list[dict[str, str]]:
    """Every CSV and JSON under `directory`, with the key-aware scanner, whole files."""
    findings: list[dict[str, str]] = []
    for path in sorted(p for p in Path(directory).rglob("*") if p.is_file()):
        if path.suffix == ".csv":
            findings.extend(_scan_csv(path, repo_root, tail_rows=None))
        elif path.suffix == ".json":
            findings.extend(_scan_json(path, repo_root))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WO-172 telemetry export manifest")
    parser.add_argument("--snapshot-dir")
    parser.add_argument("--repo-root")
    parser.add_argument("--as-of")
    parser.add_argument("--max-file-kb", type=int, default=300)
    parser.add_argument("--csv-tail-lines", type=int, default=200)
    parser.add_argument("--whitelist-dir", action="append", default=[])
    parser.add_argument("--mode", choices=(MODE_MIRROR, MODE_FULL), default=MODE_MIRROR)
    parser.add_argument("--scan-credentials", default=None, help="scan this directory and exit non-zero on any finding")
    args = parser.parse_args(argv)

    if args.scan_credentials:
        findings = scan_credentials(Path(args.scan_credentials), Path(args.repo_root or REPO_ROOT))
        if findings:
            print(f"credential scan findings: {findings}", file=sys.stderr)
            return 1
        print(json.dumps({"scanned": args.scan_credentials, "findings": 0}))
        return 0

    if not args.snapshot_dir or not args.repo_root or not args.as_of:
        print("write_telemetry_export_manifest needs --snapshot-dir, --repo-root and --as-of", file=sys.stderr)
        return 1
    snapshot_dir = Path(args.snapshot_dir)
    manifest = build_manifest(
        snapshot_dir=snapshot_dir,
        repo_root=Path(args.repo_root),
        as_of=args.as_of,
        max_file_kb=args.max_file_kb,
        csv_tail_lines=args.csv_tail_lines,
        whitelist_dirs=[d for d in args.whitelist_dir if d],
        mode=args.mode,
    )
    target = snapshot_dir / "telemetry" / MANIFEST_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{MANIFEST_NAME}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, target)
    print(json.dumps({"files": manifest["file_count"], "truncated": manifest["truncated_file_count"]}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # fail closed: the caller stamps manifest_failed and pushes nothing
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
