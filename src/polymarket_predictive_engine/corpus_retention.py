"""WO-71 bounded retention for high-volume research corpora.

Expired raw rows are compacted into daily gzip files before the live source is
rewritten.  The compacted corpus archive has registered age and size bounds.
WO-61 decision ledgers and the WO-65 investor recovery archive are explicitly
outside the writable surface.
"""

from __future__ import annotations

from contextlib import ExitStack
import csv
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import tempfile
from typing import Any, Iterable, Mapping, Sequence

from .config import EngineConfig, load_config
from .ledger_anchor import DEFAULT_LEDGER_REGISTRY
from .runtime_lock import runtime_lock
from .utils import (
    ensure_dir,
    normalize_external_timestamp,
    parse_timestamp,
    read_csv_rows,
    safe_float,
    serialize_value,
    write_csv,
    write_json,
)


OUTPUT_FILE = "ops_scheduler/corpus_retention.json"
HISTORY_FILE = "ops_scheduler/corpus_retention_history.csv"
ARCHIVE_ROOT = "polymarket_training_archive"

REGISTERED_WINDOWS: dict[str, float] = {
    "websocket_feature_raw_days": 3.0,
    "trade_print_raw_days": 30.0,
    "official_book_raw_days": 14.0,
    "training_archive_days": 90.0,
    "orphan_temp_max_age_hours": 24.0,
    "training_archive_compaction_age_hours": 24.0,
}
REGISTERED_ARCHIVE_MAX_MB = 1024.0

HISTORY_FIELDS = [
    "generated_at_utc",
    "status",
    "bytes_before",
    "bytes_after",
    "bytes_archived",
    "bytes_pruned_raw",
    "bytes_pruned_archive",
    "orphan_temp_bytes_deleted",
    "projected_daily_growth_bytes",
    "projected_days_to_90_percent_disk",
    "disk_used_percent_before",
    "disk_used_percent_after",
    "paper_trading_invoked",
    "live_trading_invoked",
]

TIMESTAMP_FIELDS: dict[str, tuple[str, ...]] = {
    "websocket_features": ("source_timestamp", "collected_at_utc", "prediction_timestamp"),
    "trade_prints": ("timestamp", "collected_at_utc"),
    "official_books": ("source_timestamp", "collected_at_utc"),
    "training_archive": (
        "source_timestamp",
        "timestamp",
        "collected_at_utc",
        "snapshot_timestamp",
        "prediction_timestamp",
    ),
}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _enabled(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _positive(value: Any, default: float) -> float:
    parsed = safe_float(value)
    return float(parsed) if parsed is not None and parsed > 0 else float(default)


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = _mapping(cfg.raw.get("collection_hygiene"))
    settings: dict[str, Any] = {
        "enabled": _enabled(raw.get("corpus_retention_enabled"), True),
        "archive_root": ARCHIVE_ROOT,
        "archive_max_mb": min(
            REGISTERED_ARCHIVE_MAX_MB,
            _positive(raw.get("corpus_archive_max_mb"), REGISTERED_ARCHIVE_MAX_MB),
        ),
        "lock_stale_seconds": max(60.0, _positive(raw.get("retention_lock_stale_seconds"), 1800.0)),
    }
    for key, registered in REGISTERED_WINDOWS.items():
        # Shorter host retention is tighter. A config cannot retain raw data
        # longer than the filed window without a dated work-order amendment.
        settings[key] = min(registered, _positive(raw.get(key), registered))
    return settings


def _utc(as_of: datetime | None) -> datetime:
    value = as_of or datetime.now(timezone.utc)
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iter_csv_any(path: Path) -> Iterable[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return
    if path.suffix == ".gz":
        handle_context = gzip.open(path, "rt", encoding="utf-8-sig", errors="replace", newline="")
    else:
        handle_context = path.open("rt", encoding="utf-8-sig", errors="replace", newline="")
    with handle_context as handle:
        for row in csv.DictReader(handle):
            yield {str(key): "" if value is None else str(value) for key, value in row.items()}


def _read_csv_any(path: Path) -> list[dict[str, str]]:
    """Materialize only bounded corpora whose rewrite requires complete rows."""

    return list(_iter_csv_any(path))


def _count_csv_rows(path: Path) -> int:
    return sum(1 for _ in _iter_csv_any(path))


def _csv_fieldnames(path: Path) -> list[str]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    if path.suffix == ".gz":
        handle_context = gzip.open(path, "rt", encoding="utf-8-sig", errors="replace", newline="")
    else:
        handle_context = path.open("rt", encoding="utf-8-sig", errors="replace", newline="")
    with handle_context as handle:
        return [str(field) for field in (csv.DictReader(handle).fieldnames or [])]


def _row_digest(row: Mapping[str, Any], fields: Sequence[str]) -> bytes:
    digest = hashlib.blake2b(digest_size=16)
    for field in fields:
        value = str(row.get(field) or "").encode("utf-8", errors="replace")
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return digest.digest()


def _merge_gzip_archive(
    path: Path,
    incoming: Path,
    fields: Sequence[str],
    scratch_root: Path,
) -> int:
    """Merge one spool into a daily archive with disk-backed exact dedup."""

    ensure_dir(path.parent)
    fieldnames = [str(field) for field in fields]
    existing_fields = _csv_fieldnames(path)
    if existing_fields and set(existing_fields) != set(fieldnames):
        raise ValueError(f"archive schema mismatch for {path}")
    before = path.stat().st_size if path.exists() else 0
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    seen_path = scratch_root / f"seen_{hashlib.sha256(path.name.encode()).hexdigest()[:16]}.sqlite"
    connection = sqlite3.connect(seen_path)
    try:
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        connection.execute("PRAGMA temp_store=FILE")
        connection.execute("CREATE TABLE seen (digest BLOB PRIMARY KEY) WITHOUT ROWID")
        cursor = connection.cursor()
        accepted = 0
        with gzip.open(temporary, "wt", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            sources = [path, incoming] if path.exists() else [incoming]
            for source in sources:
                for row in _iter_csv_any(source):
                    cursor.execute("INSERT OR IGNORE INTO seen(digest) VALUES (?)", (_row_digest(row, fieldnames),))
                    if cursor.rowcount != 1:
                        continue
                    writer.writerow({field: serialize_value(row.get(field, "")) for field in fieldnames})
                    accepted += 1
                    if accepted % 10_000 == 0:
                        connection.commit()
        connection.commit()
        os.replace(temporary, path)
    finally:
        connection.close()
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return max(0, path.stat().st_size - before)


def _fieldnames(rows: Iterable[Mapping[str, Any]], existing: Sequence[str] = ()) -> list[str]:
    fields = [str(field) for field in existing]
    for row in rows:
        for key in row:
            if str(key) not in fields:
                fields.append(str(key))
    return fields


def _write_gzip(path: Path, rows: list[Mapping[str, Any]], fields: Sequence[str]) -> None:
    ensure_dir(path.parent)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with gzip.open(temp, "wt", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({field: serialize_value(row.get(field, "")) for field in fields})
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _row_time(row: Mapping[str, Any], fields: Sequence[str]) -> datetime | None:
    for field in fields:
        seconds = normalize_external_timestamp(row.get(field))
        if seconds is not None:
            return datetime.fromtimestamp(seconds, timezone.utc)
    return None


def _ledger_patterns(cfg: EngineConfig) -> list[str]:
    raw = _mapping(cfg.raw.get("ledger_anchor"))
    registrations = raw.get("ledger_globs") if isinstance(raw.get("ledger_globs"), list) else DEFAULT_LEDGER_REGISTRY
    patterns: list[str] = []
    for item in registrations:
        value = item.get("glob") if isinstance(item, Mapping) else item
        text = str(value or "").strip().replace("\\", "/")
        if text:
            patterns.append(text)
    return patterns


def _protected(cfg: EngineConfig, path: Path) -> bool:
    try:
        relative = path.resolve().relative_to(cfg.output_root.resolve()).as_posix()
    except ValueError:
        return True
    candidate = PurePosixPath(relative)
    if any(candidate.match(pattern) for pattern in _ledger_patterns(cfg)):
        return True
    # Hard namespace exclusion: the retention writer has no authority over
    # investor evidence, paper positions/fills, or the WO-65 recovery files.
    return relative.startswith(("performance/", "polymarket_portfolio/", "polymarket_shadow/"))


def _row_key(row: Mapping[str, Any], fields: Sequence[str]) -> tuple[str, ...]:
    return tuple(str(row.get(field) or "") for field in fields)


def _archive_rows(
    cfg: EngineConfig,
    *,
    corpus: str,
    rows: list[dict[str, Any]],
    timestamp_fields: Sequence[str],
    fallback_day: datetime,
) -> tuple[int, list[str]]:
    if not rows:
        return 0, []
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        observed = _row_time(row, timestamp_fields) or fallback_day
        day = observed.astimezone(timezone.utc).strftime("%Y%m%d")
        fields = sorted(str(key) for key in row)
        schema = hashlib.sha256("|".join(fields).encode("utf-8")).hexdigest()[:12]
        groups.setdefault((day, schema), []).append(row)
    bytes_added = 0
    paths: list[str] = []
    root = cfg.output_root / ARCHIVE_ROOT
    for (day, schema), group in sorted(groups.items()):
        path = root / f"daily_{corpus}_{day}_{schema}.csv.gz"
        if _protected(cfg, path):
            raise PermissionError(f"refusing to archive into protected path: {path}")
        existing = _read_csv_any(path)
        fields = _fieldnames([*existing, *group])
        dedup: dict[tuple[str, ...], dict[str, Any]] = {}
        for row in [*existing, *group]:
            dedup[_row_key(row, fields)] = dict(row)
        before = path.stat().st_size if path.exists() else 0
        _write_gzip(path, list(dedup.values()), fields)
        bytes_added += max(0, path.stat().st_size - before)
        paths.append(str(path))
    return bytes_added, paths


def _retire_rows(
    cfg: EngineConfig,
    *,
    corpus: str,
    path: Path,
    raw_days: float,
    timestamp_fields: Sequence[str],
    current: datetime,
    dry_run: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "corpus": corpus,
        "source": str(path),
        "rows_seen": 0,
        "rows_archived": 0,
        "rows_retained": 0,
        "bytes_archived": 0,
        "bytes_pruned_raw": 0,
        "archive_files": [],
    }
    if not path.exists() or _protected(cfg, path):
        result["status"] = "protected" if path.exists() else "missing"
        return result
    rows = _read_csv_any(path)
    result["rows_seen"] = len(rows)
    cutoff = current - timedelta(days=raw_days)
    expired: list[dict[str, Any]] = []
    retained: list[dict[str, Any]] = []
    for row in rows:
        observed = _row_time(row, timestamp_fields)
        if observed is not None and observed < cutoff:
            expired.append(dict(row))
        else:
            # Unknown timestamps stay fail-safe on the host; they are never
            # silently destroyed merely because the file itself is old.
            retained.append(dict(row))
    result.update(
        {
            "status": "would_compact" if dry_run and expired else "ok",
            "cutoff_utc": _iso(cutoff),
            "rows_archived": len(expired),
            "rows_retained": len(retained),
        }
    )
    if dry_run and expired:
        result["bytes_pruned_raw"] = int(path.stat().st_size * len(expired) / max(len(rows), 1))
    if not expired or dry_run:
        return result
    before = path.stat().st_size
    archived_bytes, archive_paths = _archive_rows(
        cfg,
        corpus=corpus,
        rows=expired,
        timestamp_fields=timestamp_fields,
        fallback_day=current,
    )
    fields = _fieldnames(rows)
    if path.suffix == ".gz":
        _write_gzip(path, retained, fields)
    else:
        write_csv(path, retained, fieldnames=fields)
    result["bytes_archived"] = archived_bytes
    result["bytes_pruned_raw"] = max(0, before - path.stat().st_size)
    result["archive_files"] = archive_paths
    return result


def _compact_training_archive(
    cfg: EngineConfig,
    *,
    current: datetime,
    min_age_hours: float,
    dry_run: bool,
) -> dict[str, Any]:
    root = cfg.output_root / ARCHIVE_ROOT
    cutoff = current - timedelta(hours=min_age_hours)
    sources = [
        path
        for path in sorted(root.glob("features_*.csv.gz"))
        if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < cutoff
    ] if root.exists() else []
    rows_archived = 0
    bytes_archived = 0
    bytes_pruned = 0
    archive_files: set[str] = set()
    eligible_sources = [path for path in sources if not _protected(cfg, path)]
    source_bytes = sum(path.stat().st_size for path in eligible_sources)
    if dry_run:
        rows_archived = sum(_count_csv_rows(path) for path in eligible_sources)
        bytes_pruned = source_bytes
    elif eligible_sources:
        # Spool by day/schema while streaming producer chunks. The previous
        # implementation accumulated every decompressed row in one list; the
        # production corpus can expand far beyond the scheduler's cgroup cap.
        ensure_dir(root)
        with tempfile.TemporaryDirectory(prefix=".corpus_retention.", dir=root) as scratch_name:
            scratch_root = Path(scratch_name)
            groups: dict[tuple[str, str], dict[str, Any]] = {}
            with ExitStack() as stack:
                for source in eligible_sources:
                    for row in _iter_csv_any(source):
                        fields = tuple(sorted(str(key) for key in row))
                        observed = _row_time(row, TIMESTAMP_FIELDS["training_archive"]) or current
                        day = observed.astimezone(timezone.utc).strftime("%Y%m%d")
                        schema = hashlib.sha256("|".join(fields).encode("utf-8")).hexdigest()[:12]
                        key = (day, schema)
                        group = groups.get(key)
                        if group is None:
                            spool_path = scratch_root / f"incoming_{day}_{schema}.csv.gz"
                            handle = stack.enter_context(gzip.open(spool_path, "wt", encoding="utf-8", newline=""))
                            writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
                            writer.writeheader()
                            group = {
                                "fields": fields,
                                "path": spool_path,
                                "rows": 0,
                                "writer": writer,
                            }
                            groups[key] = group
                        group["writer"].writerow(
                            {field: serialize_value(row.get(field, "")) for field in group["fields"]}
                        )
                        group["rows"] = int(group["rows"]) + 1
                        rows_archived += 1

            for (day, schema), group in sorted(groups.items()):
                target = root / f"daily_training_archive_{day}_{schema}.csv.gz"
                if _protected(cfg, target):
                    raise PermissionError(f"refusing to archive into protected path: {target}")
                bytes_archived += _merge_gzip_archive(
                    target,
                    Path(group["path"]),
                    list(group["fields"]),
                    scratch_root,
                )
                archive_files.add(str(target))

            if groups:
                # Sources are deleted only after every spool has merged and
                # atomically replaced its bounded daily archive.
                for source in eligible_sources:
                    size = source.stat().st_size
                    source.unlink()
                    bytes_pruned += size
    return {
        "corpus": "training_archive",
        "status": "would_compact" if dry_run and sources else "ok",
        "source_files": len(eligible_sources),
        "rows_archived": rows_archived,
        "bytes_archived": bytes_archived,
        "bytes_pruned_raw": bytes_pruned,
        "archive_files": sorted(archive_files),
        "compaction_cutoff_utc": _iso(cutoff),
    }


def _archive_day(path: Path) -> datetime | None:
    match = re.search(r"_(\d{8})_[0-9a-f]{12}\.csv\.gz$", path.name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _bound_archive(
    cfg: EngineConfig,
    *,
    current: datetime,
    max_days: float,
    max_bytes: int,
    dry_run: bool,
) -> dict[str, Any]:
    root = cfg.output_root / ARCHIVE_ROOT
    files = sorted(root.glob("daily_*.csv.gz")) if root.exists() else []
    expired = [path for path in files if (_archive_day(path) or current) < current - timedelta(days=max_days)]
    remaining = [path for path in files if path not in expired]
    total = sum(path.stat().st_size for path in remaining)
    for path in sorted(remaining, key=lambda item: (_archive_day(item) or current, item.name)):
        if total <= max_bytes:
            break
        expired.append(path)
        total -= path.stat().st_size
    deleted_bytes = sum(path.stat().st_size for path in set(expired))
    if not dry_run:
        for path in set(expired):
            if not _protected(cfg, path):
                path.unlink()
    actual_files = [path for path in files if path not in set(expired)]
    return {
        "status": "would_prune" if dry_run and expired else "ok",
        "archive_root": str(root),
        "max_days": max_days,
        "max_bytes": max_bytes,
        "files_before": len(files),
        "files_pruned": len(set(expired)),
        "bytes_pruned_archive": deleted_bytes,
        "files_after": len(actual_files),
        "bytes_after": sum(path.stat().st_size for path in actual_files),
    }


def _orphan_temps(
    cfg: EngineConfig,
    *,
    current: datetime,
    max_age_hours: float,
    dry_run: bool,
) -> dict[str, Any]:
    roots = [
        cfg.output_root / "polymarket_training",
        cfg.output_root / "polymarket_websocket",
        cfg.output_root / "polymarket_trade_prints",
        cfg.output_root / "maker_carry" / "official_books",
    ]
    cutoff = current - timedelta(hours=max_age_hours)
    candidates: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.glob("*.tmp"):
            if path.is_file() and datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < cutoff:
                candidates.append(path)
    deleted_bytes = sum(path.stat().st_size for path in candidates if not _protected(cfg, path))
    if not dry_run:
        for path in candidates:
            if not _protected(cfg, path):
                path.unlink()
    return {
        "status": "would_delete" if dry_run and candidates else "ok",
        "cutoff_utc": _iso(cutoff),
        "files_deleted": len(candidates),
        "bytes_deleted": deleted_bytes,
        "paths": [str(path) for path in candidates[:50]],
        "note": "Only stale atomic-write temp files are deleted; incomplete temp files are not valid corpus rows.",
    }


def _managed_bytes(cfg: EngineConfig) -> int:
    paths = [
        cfg.output_root / "polymarket_training",
        cfg.output_root / "polymarket_websocket",
        cfg.output_root / "polymarket_trade_prints",
        cfg.output_root / "maker_carry" / "official_books",
        cfg.output_root / ARCHIVE_ROOT,
    ]
    total = 0
    seen: set[Path] = set()
    for root in paths:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path not in seen:
                seen.add(path)
                total += path.stat().st_size
    return total


def _recent_managed_write_bytes(cfg: EngineConfig, current: datetime) -> int:
    """Conservative first-run daily-growth proxy from recently written files."""
    cutoff = current - timedelta(days=1)
    roots = [
        cfg.output_root / "polymarket_training",
        cfg.output_root / "polymarket_websocket",
        cfg.output_root / "polymarket_trade_prints",
        cfg.output_root / "maker_carry" / "official_books",
        cfg.output_root / ARCHIVE_ROOT,
    ]
    total = 0
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            if modified >= cutoff and not path.name.endswith(".tmp"):
                total += path.stat().st_size
    return total


def _disk_projection(
    cfg: EngineConfig,
    *,
    generated_at: str,
    bytes_before: int,
    bytes_after: int,
    disk_before: Any,
    disk_after: Any,
) -> dict[str, Any]:
    history = read_csv_rows(cfg.output_root / HISTORY_FILE)
    previous = history[-1] if history else {}
    previous_time = parse_timestamp(previous.get("generated_at_utc"))
    current_time = parse_timestamp(generated_at)
    previous_after = safe_float(previous.get("bytes_after"))
    if previous_time is not None and current_time is not None and previous_after is not None:
        elapsed_days = max((current_time - previous_time).total_seconds() / 86400.0, 1 / 1440)
        daily_growth = max(0.0, (bytes_before - previous_after) / elapsed_days)
        basis = "prior_retention_run"
    else:
        current_time = current_time or datetime.now(timezone.utc)
        daily_growth = float(_recent_managed_write_bytes(cfg, current_time))
        basis = "first_run_recent_file_write_footprint"
    threshold_bytes = disk_after.total * 0.90
    headroom = max(0.0, threshold_bytes - disk_after.used)
    days_to_90 = headroom / daily_growth if daily_growth > 0 else None
    return {
        "basis": basis,
        "managed_bytes_before": bytes_before,
        "managed_bytes_after": bytes_after,
        "projected_daily_growth_bytes": round(daily_growth, 3),
        "projected_days_to_90_percent_disk": None if days_to_90 is None else round(days_to_90, 3),
        "projected_disk_used_bytes_in_30_days_without_further_retention": round(
            disk_after.used + daily_growth * 30,
            3,
        ),
        "disk_total_bytes": disk_after.total,
        "disk_used_bytes_before": disk_before.used,
        "disk_used_bytes_after": disk_after.used,
        "disk_used_percent_before": round(disk_before.used / max(disk_before.total, 1) * 100, 3),
        "disk_used_percent_after": round(disk_after.used / max(disk_after.total, 1) * 100, 3),
    }


def _append_history(cfg: EngineConfig, payload: Mapping[str, Any]) -> None:
    path = cfg.output_root / HISTORY_FILE
    rows = read_csv_rows(path)
    projection = _mapping(payload.get("disk_projection"))
    row = {
        "generated_at_utc": payload.get("generated_at_utc"),
        "status": payload.get("status"),
        "bytes_before": projection.get("managed_bytes_before"),
        "bytes_after": projection.get("managed_bytes_after"),
        "bytes_archived": payload.get("bytes_archived"),
        "bytes_pruned_raw": payload.get("bytes_pruned_raw"),
        "bytes_pruned_archive": payload.get("bytes_pruned_archive"),
        "orphan_temp_bytes_deleted": _mapping(payload.get("orphan_temp_cleanup")).get("bytes_deleted"),
        "projected_daily_growth_bytes": projection.get("projected_daily_growth_bytes"),
        "projected_days_to_90_percent_disk": projection.get("projected_days_to_90_percent_disk"),
        "disk_used_percent_before": projection.get("disk_used_percent_before"),
        "disk_used_percent_after": projection.get("disk_used_percent_after"),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    rows.append(row)
    write_csv(path, rows[-365:], fieldnames=HISTORY_FIELDS)


def run_corpus_retention(
    cfg: EngineConfig,
    *,
    as_of: datetime | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    settings = _settings(cfg)
    current = _utc(as_of)
    generated_at = _iso(current)
    base = {
        "work_order": "WO-71",
        "generated_at_utc": generated_at,
        "dry_run": bool(dry_run),
        "settings": settings,
        "protected_ledger_patterns": _ledger_patterns(cfg),
        "archive_contract": "bounded daily gzip corpus archive; separate from WO-61/WO-65 investor ledgers",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    output_path = cfg.output_root / OUTPUT_FILE
    if not settings["enabled"]:
        payload = {**base, "status": "disabled", "corpora": []}
        write_json(output_path, payload)
        return payload
    ensure_dir(cfg.output_root)
    with runtime_lock(
        cfg,
        "corpus_retention",
        stale_after_seconds=float(settings["lock_stale_seconds"]),
    ) as lock:
        if not lock.acquired:
            payload = {**base, "status": "skipped_lock_held", "corpora": [], "runtime_lock": lock.as_dict()}
            write_json(output_path, payload)
            return payload
        bytes_before = _managed_bytes(cfg)
        disk_before = shutil.disk_usage(cfg.output_root)
        corpora: list[dict[str, Any]] = []
        websocket_path = cfg.output_root / "polymarket_training" / "websocket_market_features.csv"
        websocket_cfg = _mapping(cfg.raw.get("websocket_market_data"))
        websocket_row_count = _count_csv_rows(websocket_path)
        writer_hours = safe_float(websocket_cfg.get("feature_retention_hours"))
        writer_compliant = (
            not websocket_path.exists()
            or (writer_hours is not None and writer_hours <= float(settings["websocket_feature_raw_days"]) * 24)
        )
        corpora.append(
            {
                "corpus": "websocket_features",
                "source": str(websocket_path),
                "status": "producer_managed",
                "registered_raw_days": float(settings["websocket_feature_raw_days"]),
                "effective_writer_retention_hours": writer_hours,
                "writer_retention_compliant": writer_compliant,
                "rows_seen": websocket_row_count,
                "rows_archived": 0,
                "rows_retained": websocket_row_count,
                "bytes_archived": 0,
                "bytes_pruned_raw": 0,
                "note": "The live normaliser atomically compacts expired rows; the daily job bounds its immutable archive without racing the producer.",
            }
        )
        corpora.append(
            _retire_rows(
                cfg,
                corpus="trade_prints",
                path=cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
                raw_days=float(settings["trade_print_raw_days"]),
                timestamp_fields=TIMESTAMP_FIELDS["trade_prints"],
                current=current,
                dry_run=dry_run,
            )
        )
        official_root = cfg.output_root / "maker_carry" / "official_books"
        for path in sorted(official_root.glob("*.csv.gz")) if official_root.exists() else []:
            corpora.append(
                _retire_rows(
                    cfg,
                    corpus="official_books",
                    path=path,
                    raw_days=float(settings["official_book_raw_days"]),
                    timestamp_fields=TIMESTAMP_FIELDS["official_books"],
                    current=current,
                    dry_run=dry_run,
                )
            )
        corpora.append(
            _compact_training_archive(
                cfg,
                current=current,
                min_age_hours=float(settings["training_archive_compaction_age_hours"]),
                dry_run=dry_run,
            )
        )
        archive = _bound_archive(
            cfg,
            current=current,
            max_days=float(settings["training_archive_days"]),
            max_bytes=int(float(settings["archive_max_mb"]) * 1024 * 1024),
            dry_run=dry_run,
        )
        temps = _orphan_temps(
            cfg,
            current=current,
            max_age_hours=float(settings["orphan_temp_max_age_hours"]),
            dry_run=dry_run,
        )
        bytes_after = _managed_bytes(cfg)
        disk_after = shutil.disk_usage(cfg.output_root)
        projection = _disk_projection(
            cfg,
            generated_at=generated_at,
            bytes_before=bytes_before,
            bytes_after=bytes_after,
            disk_before=disk_before,
            disk_after=disk_after,
        )
        payload = {
            **base,
            "status": (
                "configuration_breach"
                if not writer_compliant
                else ("dry_run" if dry_run else "ok")
            ),
            "corpora": corpora,
            "archive": archive,
            "orphan_temp_cleanup": temps,
            "bytes_archived": sum(int(row.get("bytes_archived") or 0) for row in corpora),
            "bytes_pruned_raw": sum(int(row.get("bytes_pruned_raw") or 0) for row in corpora),
            "bytes_pruned_archive": int(archive.get("bytes_pruned_archive") or 0),
            "disk_projection": projection,
            "decision_ledgers_touched": False,
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        }
        write_json(output_path, payload)
        _append_history(cfg, payload)
        return payload


def main(config_path: str) -> dict[str, Any]:
    return run_corpus_retention(load_config(config_path))
