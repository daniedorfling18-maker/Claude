"""WO-73 credential guard for telemetry and investor archive manifests.

The scanner reports only finding type and location.  It deliberately never
copies a matched value into its output, so the guard itself cannot become a
credential exfiltration path.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


WORK_ORDER = "WO-73"
ALLOWED_SUFFIXES = frozenset({".json", ".md", ".csv", ".log"})
ARCHIVE_MANIFESTS = (
    "outputs/performance/ledger_state_archive_manifest.json",
    "outputs/performance/disaster_recovery_status.json",
)

SENSITIVE_KEY_RE = re.compile(
    r"(?:^|[_\-\s])("
    r"private[_\-\s]*key|"
    r"api[_\-\s]*(?:key|secret|token)|"
    r"(?:access|auth|bearer|refresh|session|id)[_\-\s]*token|"
    r"secret|passphrase|password|credential"
    r")(?:$|[_\-\s])",
    re.IGNORECASE,
)
LABELLED_VALUE_RE = re.compile(
    r"(?i)(?:private[_\-\s]*key|api[_\-\s]*(?:key|secret|token)|"
    r"(?:access|auth|bearer|refresh|session|id)[_\-\s]*token|"
    r"secret|passphrase|password|credential)"
    r"\s*[=:]\s*[\"']?([^\s\"',;}]{8,})"
)
HEX_32_BYTE_RE = re.compile(r"(?<![0-9A-Fa-f])(?:0x)?[0-9A-Fa-f]{64}(?![0-9A-Fa-f])")
PEM_RE = re.compile(r"-----BEGIN (?:EC |RSA |OPENSSH )?PRIVATE KEY-----")

PUBLIC_IDENTIFIER_KEYS = (
    "condition",
    "transaction",
    "tx_hash",
    "hash",
    "sha",
    "digest",
    "token",
    "market",
    "asset",
    "chain_head",
    "chain head",
    "condition id",
    "transaction hash",
    "commit",
    "git_rev",
    "incident_id",
    "manifest",
    "correlation",
    "official_books",
)
SAFE_SENTINELS = frozenset(
    {
        "",
        "0",
        "false",
        "true",
        "none",
        "null",
        "missing",
        "unset",
        "unknown",
        "present",
        "redacted",
        "masked",
        "disabled",
        "not_configured",
    }
)


def telemetry_whitelist(repo_root: Path) -> tuple[str, ...]:
    script = repo_root / "scripts" / "push_vps_telemetry.sh"
    text = script.read_text(encoding="utf-8")
    match = re.search(r'TELEMETRY_DIRS="\s*(.*?)\s*"', text, re.DOTALL)
    if match is None:
        raise ValueError("push_vps_telemetry.sh has no parseable TELEMETRY_DIRS declaration")
    rows = tuple(line.strip() for line in match.group(1).splitlines() if line.strip())
    if not rows or any(not row.startswith("outputs/") for row in rows):
        raise ValueError("telemetry whitelist is empty or contains a non-output path")
    return rows


def _candidate_files(repo_root: Path) -> list[Path]:
    candidates: set[Path] = set()
    for relative in telemetry_whitelist(repo_root):
        root = repo_root / relative
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.is_symlink() or path.suffix.lower() not in ALLOWED_SUFFIXES:
                continue
            if len(path.relative_to(root).parts) <= 2 and "official_books" not in path.as_posix():
                candidates.add(path)
    for relative in ARCHIVE_MANIFESTS:
        path = repo_root / relative
        if path.is_file() and not path.is_symlink():
            candidates.add(path)
    return sorted(candidates)


def _safe_value(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, float)):
        return True
    text = str(value).strip().strip('"\'').lower()
    return text in SAFE_SENTINELS or text.startswith("${") or "***" in text


def _public_identifier_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in PUBLIC_IDENTIFIER_KEYS)


def _finding(path: Path, repo_root: Path, location: str, kind: str) -> dict[str, str]:
    relative = path.relative_to(repo_root).as_posix()
    fingerprint = hashlib.sha256(f"{relative}|{location}|{kind}".encode("utf-8")).hexdigest()[:16]
    return {
        "path": relative,
        "location": location,
        "kind": kind,
        "finding_id": f"credential_guard_{fingerprint}",
    }


def _inspect_field(
    path: Path,
    repo_root: Path,
    *,
    key: str,
    value: Any,
    location: str,
) -> list[dict[str, str]]:
    if _safe_value(value):
        return []
    text = str(value)
    findings: list[dict[str, str]] = []
    sensitive_named = bool(SENSITIVE_KEY_RE.search(key))
    if sensitive_named:
        findings.append(_finding(path, repo_root, location, "nonempty_sensitive_named_field"))
    if PEM_RE.search(text):
        findings.append(_finding(path, repo_root, location, "private_key_pem"))
    if HEX_32_BYTE_RE.fullmatch(text.strip()) and not sensitive_named and not _public_identifier_key(key):
        findings.append(_finding(path, repo_root, location, "unclassified_32_byte_hex_value"))
    return findings


def _walk_json(value: Any, *, prefix: str = "$") -> Iterable[tuple[str, str, Any]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            location = f"{prefix}.{key}"
            if isinstance(child, (Mapping, list)):
                yield from _walk_json(child, prefix=location)
            else:
                yield location, str(key), child
    elif isinstance(value, list):
        for index, child in enumerate(value):
            location = f"{prefix}[{index}]"
            if isinstance(child, (Mapping, list)):
                yield from _walk_json(child, prefix=location)
            else:
                # Preserve the parent field name for scalar list members. A
                # bare list index loses the distinction between public venue
                # identifiers (for example recent_top_markets) and an
                # unlabeled credential value.
                yield location, prefix, child


def _scan_json(path: Path, repo_root: Path) -> list[dict[str, str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _scan_text(path, repo_root)
    findings: list[dict[str, str]] = []
    for location, key, value in _walk_json(payload):
        findings.extend(_inspect_field(path, repo_root, key=key, value=value, location=location))
    return findings


def _scan_csv(path: Path, repo_root: Path) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            for row_number, row in enumerate(csv.DictReader(handle), start=2):
                for key, value in row.items():
                    findings.extend(
                        _inspect_field(
                            path,
                            repo_root,
                            key=str(key or ""),
                            value=value,
                            location=f"row:{row_number}:{key}",
                        )
                    )
    except (OSError, csv.Error):
        return _scan_text(path, repo_root)
    return findings


def _scan_text(path: Path, repo_root: Path) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return findings
    for number, line in enumerate(lines, start=1):
        location = f"line:{number}"
        if PEM_RE.search(line):
            findings.append(_finding(path, repo_root, location, "private_key_pem"))
        labelled = LABELLED_VALUE_RE.search(line)
        if labelled and not _safe_value(labelled.group(1)):
            findings.append(_finding(path, repo_root, location, "labelled_credential_value"))
        context_key = ""
        for previous in reversed(lines[max(0, number - 9) : number]):
            key_match = re.search(r'["\']?([A-Za-z0-9_. -]+)["\']?\s*:', previous)
            if key_match:
                context_key = key_match.group(1)
                break
        public_context = _public_identifier_key(f"{context_key} {line}")
        sensitive_context = bool(SENSITIVE_KEY_RE.search(context_key))
        if HEX_32_BYTE_RE.search(line) and (not public_context or sensitive_context):
            findings.append(_finding(path, repo_root, location, "unclassified_32_byte_hex_value"))
    return findings


def scan_telemetry_credentials(repo_root: str | Path) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    files = _candidate_files(root)
    findings: list[dict[str, str]] = []
    for path in files:
        if path.suffix.lower() == ".json":
            findings.extend(_scan_json(path, root))
        elif path.suffix.lower() == ".csv":
            findings.extend(_scan_csv(path, root))
        else:
            findings.extend(_scan_text(path, root))
    unique = sorted(
        {row["finding_id"]: row for row in findings}.values(),
        key=lambda row: (row["path"], row["location"], row["kind"]),
    )
    return {
        "work_order": WORK_ORDER,
        "status": "PASS" if not unique else "FAIL",
        "telemetry_directories": list(telemetry_whitelist(root)),
        "archive_manifests": list(ARCHIVE_MANIFESTS),
        "files_scanned": len(files),
        "finding_count": len(unique),
        "findings": unique,
        "matched_values_redacted": True,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
