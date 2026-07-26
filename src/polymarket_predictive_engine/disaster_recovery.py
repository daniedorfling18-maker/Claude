"""WO-65 full ledger snapshots and tested restore.

Only the WO-61 enrolled ledger set and its chain material enter the archive.
The functions here are reporting/recovery infrastructure: they cannot place,
amend, or cancel orders and no trading or governance gate reads their output.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
from typing import Any

from .config import EngineConfig, load_config
from .ledger_anchor import ledger_paths_for_archive, verify_ledger_chain
from .runtime_lock import runtime_lock
from .utils import now_utc, parse_timestamp, read_csv_rows, read_json, write_json


class DisasterRecoveryError(RuntimeError):
    """Raised after a recovery failure has been stamped to disk."""


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = cfg.raw.get("disaster_recovery", {}) if isinstance(cfg.raw.get("disaster_recovery"), dict) else {}
    merged: dict[str, Any] = {
        "enabled": True,
        "archive_file": "performance/ledger_state_archive.tar.gz",
        "archive_manifest_file": "performance/ledger_state_archive_manifest.json",
        "status_file": "performance/disaster_recovery_status.json",
        "restore_status_file": "performance/restore_verification_status.json",
        "archive_branch": "vps-archive",
        "size_cap_mb": 240,
        # WO-122: runtime ceiling on the UNCOMPRESSED source set. Distinct from
        # the registered size_cap_mb, which still bounds the compressed archive
        # that leaves the host and remains tighten-only.
        "source_cap_mb": 2048,
        "active_rpo_hours": 168,
        "paper_stage_max_rpo_hours": 168,
        "pre_live_max_rpo_hours": 24,
        "lock_stale_seconds": 3600,
    }
    merged.update({key: value for key, value in raw.items() if value is not None})
    # 2026-07-11 WO-65 tighten-only registration: overrides may reduce the
    # archive size or RPO ceilings, never widen the filed caps.
    # 2026-07-26 owner amendment (authorized by the owner's merge of this
    # change): the archive ceiling is raised 50MB -> 240MB. The WO-61 enrolled
    # ledger set is append-only and outgrew 50MB on 2026-07-16, which killed
    # disaster recovery for ten days - the cap is enforced on the compressed
    # archive, the uncompressed source, the expanded restore (a
    # decompression-bomb guard), and the remote push. 240MB restores DR while
    # keeping every one of those guards binding. Still tighten-only: config may
    # reduce this, never widen it.
    merged["size_cap_mb"] = min(240.0, float(merged["size_cap_mb"]))
    merged["source_cap_mb"] = max(1.0, float(merged["source_cap_mb"]))
    merged["paper_stage_max_rpo_hours"] = min(168.0, float(merged["paper_stage_max_rpo_hours"]))
    merged["pre_live_max_rpo_hours"] = min(24.0, float(merged["pre_live_max_rpo_hours"]))
    return merged


def _enabled(value: Any) -> bool:
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _output_path(cfg: EngineConfig, value: Any) -> Path:
    text = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(text)
    if not text or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"disaster-recovery path must stay below paths.output_root: {value!r}")
    return cfg.output_root.joinpath(*path.parts)


def _base_payload(cfg: EngineConfig, settings: dict[str, Any], *, status: str) -> dict[str, Any]:
    return {
        "status": status,
        "generated_at_utc": now_utc(),
        "work_order": "WO-65",
        "reporting_and_recovery_only": True,
        "archive_branch": str(settings["archive_branch"]),
        "archive_path": str(_output_path(cfg, settings["archive_file"])),
        "archive_manifest_path": str(_output_path(cfg, settings["archive_manifest_file"])),
        "size_cap_mb": float(settings["size_cap_mb"]),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }


def _write_status(cfg: EngineConfig, settings: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    write_json(_output_path(cfg, settings["status_file"]), payload)
    return payload


def _live_capital_context(cfg: EngineConfig) -> bool:
    wallet = str((cfg.raw.get("maker_live_test", {}) or {}).get("wallet_address") or "").strip()
    return cfg.trading_mode == "live" or bool(wallet)


def _validated_rpo(
    cfg: EngineConfig,
    settings: dict[str, Any],
    *,
    observed_age_hours: float | None = None,
) -> dict[str, Any]:
    active = float(settings["active_rpo_hours"])
    paper_max = float(settings["paper_stage_max_rpo_hours"])
    pre_live_max = float(settings["pre_live_max_rpo_hours"])
    if active <= 0 or paper_max <= 0 or pre_live_max <= 0:
        raise ValueError("disaster-recovery RPO values must be positive")
    live_context = _live_capital_context(cfg)
    allowed = pre_live_max if live_context else paper_max
    if active > allowed:
        context = "configured live-capital context" if live_context else "paper stage"
        raise ValueError(
            f"active RPO {active:g}h exceeds the {allowed:g}h maximum for {context}; "
            "tighten disaster_recovery.active_rpo_hours with a dated config change before proceeding"
        )
    # WO-122: `compliant` used to be hardcoded True, asserting only that the
    # CONFIGURED ceiling was respected. Published beside a 233-hour archive age
    # against a 24-hour RPO, it read as "backups are fine" while the archive
    # builder had been failing for ten days. It now also requires the OBSERVED
    # archive age to be inside the active RPO, and fails closed when the age is
    # unknown (never-archived is not compliance).
    observed_within = observed_age_hours is not None and observed_age_hours <= active
    return {
        "active_rpo_hours": active,
        "maximum_rpo_hours_for_context": allowed,
        "paper_stage_max_rpo_hours": paper_max,
        "pre_live_max_rpo_hours": pre_live_max,
        # True when a monitored wallet is configured OR trading_mode is live.
        # This only ever TIGHTENS the ceiling (24h instead of 168h), so it is
        # deliberately left conservative rather than renamed to match the
        # paper-only posture.
        "live_capital_context": live_context,
        "configured_rpo_within_registered_ceiling": True,
        "observed_archive_age_hours": (
            None if observed_age_hours is None else round(observed_age_hours, 4)
        ),
        "observed_within_rpo": observed_within,
        "compliant": observed_within,
    }


def _preserved_remote_state(previous: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "remote_push_status",
        "last_remote_success_at_utc",
        "last_remote_snapshot_date",
        "remote_commit",
        "remote_error",
    )
    return {key: previous.get(key) for key in keys if key in previous}


def _snapshot_due(previous: dict[str, Any], *, rpo_hours: float) -> tuple[bool, str | None, float | None]:
    last = parse_timestamp(previous.get("last_remote_success_at_utc"))
    now = parse_timestamp(now_utc())
    if last is None or now is None:
        return True, None, None
    age_hours = max(0.0, (now - last).total_seconds() / 3600.0)
    next_due = (last + timedelta(hours=rpo_hours)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return age_hours >= rpo_hours, next_due, age_hours


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _snapshot_date(cfg: EngineConfig) -> tuple[str, str]:
    settings = cfg.raw.get("ledger_anchor", {}) if isinstance(cfg.raw.get("ledger_anchor"), dict) else {}
    chain_relative = settings.get("chain_file") or "performance/ledger_anchor_chain.csv"
    rows = read_csv_rows(_output_path(cfg, chain_relative))
    if not rows:
        raise ValueError("WO-61 ledger anchor chain is empty; anchor ledgers before archiving")
    day = str(rows[-1].get("anchor_date") or "").strip()
    head = str(rows[-1].get("chain_head") or "").strip()
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("latest WO-61 anchor date is invalid") from exc
    if len(head) != 64:
        raise ValueError("latest WO-61 chain head is invalid")
    return day, head


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _archive_source_payloads(cfg: EngineConfig, *, snapshot_date: str, source_cap_bytes: int) -> list[dict[str, Any]]:
    """Describe (never materialize) the ledger set for one archive.

    WO-122: this used to read every ledger fully into memory and reject the run
    when the UNCOMPRESSED total exceeded the registered archive cap. That
    proxy was guaranteed to fail eventually - the WO-61 ledgers are append-only
    and only grow - and it fired on 2026-07-16, leaving disaster recovery dead
    for ten days. The registered guarantee bounds the ARCHIVE artifact that
    leaves the host, which ``_write_archive`` still enforces on the COMPRESSED
    size, unchanged. The remaining source ceiling exists only to bound runtime
    against a runaway output tree, so it is separately configurable and much
    larger; digests are computed streaming.
    """
    root = cfg.output_root.resolve()
    payloads: list[dict[str, Any]] = []
    total = 0
    for path in ledger_paths_for_archive(cfg, as_of_date=snapshot_date):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"archive source must be a regular file: {path}")
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError(f"archive source escaped paths.output_root: {path}") from exc
        sha256, size = _sha256_file(resolved)
        total += size
        if total > source_cap_bytes:
            raise ValueError(
                f"WO-61 ledger source set exceeds the {source_cap_bytes / 1024 / 1024:g}MB "
                "runtime ceiling (disaster_recovery.source_cap_mb); the compressed archive "
                "cap is enforced separately"
            )
        payloads.append(
            {
                "path": f"outputs/{relative}",
                "size_bytes": size,
                "sha256": sha256,
                "source_path": resolved,
            }
        )
    if not payloads:
        raise ValueError("WO-61 ledger archive set is empty")
    return payloads


def _write_archive(
    path: Path,
    *,
    payloads: list[dict[str, Any]],
    manifest: dict[str, Any],
    size_cap_bytes: int,
) -> tuple[int, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tarfile.open(temp_path, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
            manifest_info = tarfile.TarInfo("archive_manifest.json")
            manifest_info.size = len(manifest_bytes)
            manifest_info.mode = 0o600
            archive.addfile(manifest_info, io.BytesIO(manifest_bytes))
            for row in payloads:
                info = tarfile.TarInfo(str(row["path"]))
                info.size = int(row["size_bytes"])
                info.mode = 0o600
                # WO-122: stream from disk; the payload rows no longer carry
                # file bytes, so a large ledger set never has to fit in RAM.
                with Path(row["source_path"]).open("rb") as handle:
                    archive.addfile(info, handle)
        compressed_size = temp_path.stat().st_size
        if compressed_size > size_cap_bytes:
            raise ValueError(
                f"compressed archive is {compressed_size} bytes, above the {size_cap_bytes} byte cap"
            )
        archive_sha = hashlib.sha256(temp_path.read_bytes()).hexdigest()
        os.replace(temp_path, path)
        return compressed_size, archive_sha
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def create_ledger_archive(cfg: EngineConfig, *, force: bool = False) -> dict[str, Any]:
    """Build one size-capped full WO-61 ledger archive when the RPO is due."""

    settings = _settings(cfg)
    status_path = _output_path(cfg, settings["status_file"])
    previous = read_json(status_path, default={}) or {}
    if not isinstance(previous, dict):
        previous = {}
    payload = _base_payload(cfg, settings, status="disabled")
    payload.update(_preserved_remote_state(previous))
    if not _enabled(settings.get("enabled", True)):
        return _write_status(cfg, settings, payload)

    try:
        rpo = _validated_rpo(cfg, settings)
        due, next_due, age_hours = _snapshot_due(previous, rpo_hours=float(rpo["active_rpo_hours"]))
        # Re-derive compliance now that the observed archive age is known.
        rpo = _validated_rpo(cfg, settings, observed_age_hours=age_hours)
        payload["rpo"] = rpo
        payload.update(
            {
                "last_remote_archive_age_hours": None if age_hours is None else round(age_hours, 4),
                "next_archive_due_at_utc": next_due,
            }
        )
        if not force and not due:
            payload["status"] = "not_due"
            return _write_status(cfg, settings, payload)

        with runtime_lock(
            cfg,
            "ledger_archive",
            stale_after_seconds=float(settings["lock_stale_seconds"]),
        ) as lock:
            if not lock.acquired:
                payload.update({"status": "skipped_locked", "runtime_lock": lock.as_dict()})
                return _write_status(cfg, settings, payload)

            day, chain_head = _snapshot_date(cfg)
            verification = verify_ledger_chain(cfg, as_of_date=day, write_summary=False)
            if verification.get("status") != "ok":
                raise ValueError(
                    f"WO-61 chain verification failed at {verification.get('first_broken_date')}: "
                    f"{verification.get('issues')}"
                )
            size_cap_bytes = int(float(settings["size_cap_mb"]) * 1024 * 1024)
            if size_cap_bytes <= 0:
                raise ValueError("disaster_recovery.size_cap_mb must be positive")
            source_cap_bytes = int(float(settings["source_cap_mb"]) * 1024 * 1024)
            sources = _archive_source_payloads(
                cfg, snapshot_date=day, source_cap_bytes=source_cap_bytes
            )
            generated = now_utc()
            manifest = {
                "schema_version": 1,
                "work_order": "WO-65",
                "snapshot_date": day,
                "generated_at_utc": generated,
                "chain_head": chain_head,
                "ledger_chain_verified_before_archive": True,
                "rpo": rpo,
                "size_cap_mb": float(settings["size_cap_mb"]),
                "source_cap_mb": float(settings["source_cap_mb"]),
                "file_count": len(sources),
                "uncompressed_bytes": sum(int(row["size_bytes"]) for row in sources),
                "files": [
                    {key: row[key] for key in ("path", "size_bytes", "sha256")}
                    for row in sources
                ],
                "paper_trading_invoked": False,
                "live_trading_invoked": False,
            }
            archive_path = _output_path(cfg, settings["archive_file"])
            compressed_size, archive_sha = _write_archive(
                archive_path,
                payloads=sources,
                manifest=manifest,
                size_cap_bytes=size_cap_bytes,
            )
            write_json(_output_path(cfg, settings["archive_manifest_file"]), manifest)
            post_build = verify_and_restore_archive(cfg, archive_path, dry_run=True)
            payload.update(
                {
                    "status": "ok",
                    "snapshot_date": day,
                    "chain_head": chain_head,
                    "file_count": len(sources),
                    "uncompressed_bytes": manifest["uncompressed_bytes"],
                    "archive_size_bytes": compressed_size,
                    "archive_sha256": archive_sha,
                    "post_build_restore_verification": {
                        "status": post_build.get("status"),
                        "verified_through_date": (post_build.get("ledger_chain_verification") or {}).get(
                            "verified_through_date"
                        ),
                        "restore_applied": post_build.get("restore_applied"),
                    },
                    "last_build_success_at_utc": generated,
                    "remote_push_status": "pending",
                    "remote_error": "",
                }
            )
    except Exception as exc:
        payload.update(
            {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "failure_stamped": True,
            }
        )
        _write_status(cfg, settings, payload)
        raise DisasterRecoveryError(payload["error"]) from exc
    return _write_status(cfg, settings, payload)


def _safe_member_path(name: str) -> PurePosixPath:
    path = PurePosixPath(str(name or ""))
    if (
        not name
        or "\\" in name
        or "\x00" in name
        or path.is_absolute()
        or ".." in path.parts
        or "" in path.parts
    ):
        raise ValueError(f"unsafe archive member path: {name!r}")
    return path


def _read_and_validate_archive(archive_path: Path, *, size_cap_bytes: int) -> tuple[dict[str, Any], dict[str, bytes]]:
    if not archive_path.is_file():
        raise ValueError(f"archive does not exist: {archive_path}")
    if archive_path.stat().st_size > size_cap_bytes:
        raise ValueError("archive exceeds the configured compressed-size cap")
    files: dict[str, bytes] = {}
    total = 0
    with tarfile.open(archive_path, mode="r:gz") as archive:
        for member in archive.getmembers():
            path = _safe_member_path(member.name)
            if not member.isfile() or member.issym() or member.islnk():
                raise ValueError(f"archive member is not a regular file: {member.name}")
            total += int(member.size)
            if total > size_cap_bytes:
                raise ValueError("archive expands above the configured size cap")
            handle = archive.extractfile(member)
            if handle is None:
                raise ValueError(f"archive member cannot be read: {member.name}")
            normalised = path.as_posix()
            if normalised in files:
                raise ValueError(f"duplicate archive member: {member.name}")
            files[normalised] = handle.read()
    raw_manifest = files.get("archive_manifest.json")
    if raw_manifest is None:
        raise ValueError("archive_manifest.json is missing")
    manifest = json.loads(raw_manifest.decode("utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("archive manifest must be a mapping")
    expected = manifest.get("files")
    if not isinstance(expected, list) or any(not isinstance(row, dict) for row in expected):
        raise ValueError("archive manifest files must be a list of mappings")
    expected_paths = {str(row.get("path") or "") for row in expected}
    if len(expected_paths) != len(expected):
        raise ValueError("archive manifest contains duplicate file paths")
    actual_paths = set(files) - {"archive_manifest.json"}
    if expected_paths != actual_paths:
        raise ValueError("archive file set does not match its manifest")
    for row in expected:
        name = str(row.get("path") or "")
        data = files[name]
        if len(data) != int(row.get("size_bytes") or -1):
            raise ValueError(f"archive size mismatch for {name}")
        if _sha256_bytes(data) != str(row.get("sha256") or ""):
            raise ValueError(f"archive digest mismatch for {name}")
    return manifest, files


def _config_for_output_root(cfg: EngineConfig, output_root: Path) -> EngineConfig:
    raw = deepcopy(cfg.raw)
    raw.setdefault("paths", {})["output_root"] = str(output_root)
    raw["paths"]["data_root"] = str(output_root.parent)
    return EngineConfig(raw=raw, path=cfg.path)


def _materialise_files(files: dict[str, bytes], destination: Path) -> Path:
    output_root = destination / "outputs"
    for name, data in files.items():
        if name == "archive_manifest.json":
            continue
        path = _safe_member_path(name)
        if not path.parts or path.parts[0] != "outputs":
            raise ValueError(f"archive data member is outside outputs/: {name}")
        target = destination.joinpath(*path.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return output_root


def verify_and_restore_archive(
    cfg: EngineConfig,
    archive_path: str | Path,
    *,
    dry_run: bool = True,
    destination_output_root: str | Path | None = None,
) -> dict[str, Any]:
    """Verify an archive and optionally restore it into an empty output root."""

    settings = _settings(cfg)
    report_path = _output_path(cfg, settings["restore_status_file"])
    payload: dict[str, Any] = {
        "status": "error",
        "generated_at_utc": now_utc(),
        "work_order": "WO-65",
        "archive_path": str(Path(archive_path)),
        "dry_run": bool(dry_run),
        "destination_output_root": None if destination_output_root is None else str(destination_output_root),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    try:
        size_cap_bytes = int(float(settings["size_cap_mb"]) * 1024 * 1024)
        manifest, files = _read_and_validate_archive(Path(archive_path), size_cap_bytes=size_cap_bytes)
        snapshot_date = str(manifest.get("snapshot_date") or "")
        with tempfile.TemporaryDirectory(prefix="polymarket-ledger-restore-") as temp_dir:
            extracted_output = _materialise_files(files, Path(temp_dir))
            extracted_cfg = _config_for_output_root(cfg, extracted_output)
            chain = verify_ledger_chain(
                extracted_cfg,
                as_of_date=snapshot_date,
                write_summary=False,
            )
            if chain.get("status") != "ok" or chain.get("verified_through_date") != snapshot_date:
                raise ValueError(
                    f"restored WO-61 chain did not verify through {snapshot_date}: "
                    f"status={chain.get('status')} issues={chain.get('issues')}"
                )
            if str(chain.get("verified_chain_head") or "") != str(manifest.get("chain_head") or ""):
                raise ValueError("restored WO-61 chain head differs from the archive manifest")
            if not dry_run:
                if destination_output_root is None:
                    raise ValueError("destination_output_root is required unless --dry-run is used")
                destination = Path(destination_output_root)
                if destination.is_symlink():
                    raise ValueError("restore destination cannot be a symbolic link")
                if destination.exists() and any(destination.iterdir()):
                    raise ValueError("restore destination must be empty; preserve/move the old outputs first")
                destination.mkdir(parents=True, exist_ok=True)
                for source in extracted_output.rglob("*"):
                    if not source.is_file():
                        continue
                    target = destination / source.relative_to(extracted_output)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
        payload.update(
            {
                "status": "ok",
                "snapshot_date": snapshot_date,
                "chain_head": manifest.get("chain_head"),
                "ledger_chain_verification": chain,
                "file_count": len(manifest.get("files") or []),
                "archive_sha256": hashlib.sha256(Path(archive_path).read_bytes()).hexdigest(),
                "restore_applied": not dry_run,
            }
        )
    except Exception as exc:
        payload.update({"status": "error", "error": f"{type(exc).__name__}: {exc}", "failure_stamped": True})
        write_json(report_path, payload)
        raise DisasterRecoveryError(payload["error"]) from exc
    write_json(report_path, payload)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return create_ledger_archive(load_config(config_path))
