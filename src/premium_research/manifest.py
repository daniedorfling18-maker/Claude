"""Manifest of committed inputs (WO-166).

The manifest is the proof that the research tree is what the fetch produced:
one entry per committed data file with its source URLs, fetch time, row
count, first and last timestamp, sha256 of the stored bytes, and for Binance
files the upstream ``.CHECKSUM`` digests that were verified.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

MANIFEST_VERSION = 1


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class ManifestEntry:
    path: str
    source_urls: list[str]
    fetched_at: str
    rows: int
    first_timestamp_ms: int
    last_timestamp_ms: int
    sha256: str
    upstream_checksums: dict[str, str] = field(default_factory=dict)
    checksum_verified: bool = False
    notes: dict[str, Any] = field(default_factory=dict)


def build_manifest(entries: list[ManifestEntry], *, generated_at: str, span: dict[str, str], code_revision: str) -> dict[str, Any]:
    return {
        "manifest_version": MANIFEST_VERSION,
        "generated_at": generated_at,
        "code_revision": code_revision,
        "span": dict(span),
        "entries": [asdict(entry) for entry in sorted(entries, key=lambda item: item.path)],
    }


def write_manifest(manifest: dict[str, Any], path: Path) -> None:
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"manifest unreadable at {path}: {exc}") from exc


def verify_manifest(manifest_path: Path, root: Path) -> list[str]:
    """Return a list of failures; empty means every entry matches on disk."""
    failures: list[str] = []
    try:
        manifest = load_manifest(manifest_path)
    except ValueError as exc:
        return [str(exc)]
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        return ["manifest has no entries"]
    listed: set[str] = set()
    for entry in entries:
        rel = str(entry.get("path", ""))
        listed.add(rel)
        target = root / rel
        if not target.is_file():
            failures.append(f"missing: {rel}")
            continue
        expected = str(entry.get("sha256", "")).lower()
        actual = sha256_path(target)
        if actual != expected:
            failures.append(f"sha256 mismatch: {rel} expected {expected} got {actual}")
        if entry.get("upstream_checksums") and not entry.get("checksum_verified"):
            failures.append(f"upstream checksums recorded but not verified: {rel}")
    data_root = root / "data"
    if data_root.is_dir():
        on_disk = {str(path.relative_to(root)).replace("\\", "/") for path in data_root.rglob("*") if path.is_file()}
        for extra in sorted(on_disk - listed):
            failures.append(f"unlisted file under data/: {extra}")
    return failures
