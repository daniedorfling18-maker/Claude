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
from .ledger_anchor import (
    ARCHIVE_EXCLUDED_PREFIXES,
    RESTORE_PROVENANCE_FILE,
    anchored_relative_paths,
    canonical_date,
    ledger_paths_for_archive,
    verify_ledger_chain,
)
from .runtime_lock import runtime_lock
from .utils import now_utc, parse_timestamp, read_csv_rows, read_json, write_json


class DisasterRecoveryError(RuntimeError):
    """Raised after a recovery failure has been stamped to disk."""


# WO-123 (2026-07-26 owner decision, authorized by the owner's merge of this
# change): the WO-61 enrolled set includes derived collection/training corpora
# that dominate the archive and are regenerable by re-harvest - measured on the
# VPS at 476.6MB of a 505.6MB set, one file at 467.1MB, 94% of the bytes. They
# stay ANCHORED, so tamper evidence over them is unchanged; they are excluded
# from the recovery ARCHIVE only, which brings the archive back under even the
# old 50MB ceiling. Prefixes are paths.output_root relative and must end in "/"
# so a prefix can never match a sibling file by accident.
# WO-127: ARCHIVE_EXCLUDED_PREFIXES moved to ledger_anchor so verification can
# intersect against it (this module already imports ledger_anchor; the reverse
# would be circular). Imported above and re-exported here for existing readers.
ARCHIVE_EXCLUSION_REASON = (
    "excluded_by_registration: derived collection/training corpus, regenerable by "
    "re-harvest, still covered by the WO-61 anchor chain"
)


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
        # WO-123: unset keeps the registered exclusion set. An override may only
        # SHRINK it (see _excluded_prefixes).
        "excluded_path_prefixes": None,
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


def _excluded_prefixes(settings: dict[str, Any]) -> tuple[str, ...]:
    """Resolve the archive exclusion set, which config may only SHRINK.

    WO-123 tighten-only direction: dropping a prefix from the override puts that
    corpus BACK into the archive (more recovery coverage), which is always
    allowed. Adding a prefix the registration does not carry would silently
    remove a ledger from disaster recovery, so unregistered entries are ignored
    rather than honoured. Omitting the key entirely keeps the registered set.
    """

    raw = settings.get("excluded_path_prefixes")
    if raw is None:
        return ARCHIVE_EXCLUDED_PREFIXES
    if isinstance(raw, str):
        candidates: list[str] = [raw]
    elif isinstance(raw, (list, tuple)):
        candidates = [str(item) for item in raw]
    else:
        raise ValueError("disaster_recovery.excluded_path_prefixes must be a list of path prefixes")
    normalised = set()
    for item in candidates:
        text = str(item).strip().replace("\\", "/").lstrip("/")
        if not text:
            continue
        normalised.add(text if text.endswith("/") else f"{text}/")
    return tuple(prefix for prefix in ARCHIVE_EXCLUDED_PREFIXES if prefix in normalised)


def _output_path(cfg: EngineConfig, value: Any) -> Path:
    text = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(text)
    if not text or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"disaster-recovery path must stay below paths.output_root: {value!r}")
    return cfg.output_root.joinpath(*path.parts)


def _excluded_prefixes_error(settings: dict[str, Any]) -> str:
    """Return the exclusion-config parse error, or "" when the config is valid."""

    try:
        _excluded_prefixes(settings)
    except ValueError as exc:
        return str(exc)
    return ""


def _excluded_prefixes_or_default(settings: dict[str, Any]) -> tuple[str, ...]:
    """Non-raising view for reporting paths; the builder still fails on error."""

    try:
        return _excluded_prefixes(settings)
    except ValueError:
        return ARCHIVE_EXCLUDED_PREFIXES


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
        # WO-123: the registered archive scope is visible in every state, not
        # only on a successful build, so an operator reading the status file
        # always knows what recovery does and does not cover.
        # WO-127: this runs BEFORE create_ledger_archive's try block, so it must
        # not raise — a malformed exclusion config used to escape with no status
        # stamped at all, reintroducing exactly the blind-failure class WO-122a
        # removed. The parse error is recorded here and re-raised inside the try,
        # where it becomes a stamped `status: error`.
        "archive_excluded_paths": list(_excluded_prefixes_or_default(settings)),
        "archive_exclusion_reason": (
            ARCHIVE_EXCLUSION_REASON if _excluded_prefixes_or_default(settings) else ""
        ),
        "archive_exclusion_config_error": _excluded_prefixes_error(settings),
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


def _archive_source_payloads(
    cfg: EngineConfig,
    *,
    snapshot_date: str,
    source_cap_bytes: int,
    excluded_prefixes: tuple[str, ...] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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

    WO-123: paths under ``excluded_prefixes`` are reported instead of archived.
    They are neither hashed nor read, which is the point - the excluded corpus is
    94% of the bytes. They remain enrolled in the WO-61 chain, so the tamper
    lane still covers them.
    """
    root = cfg.output_root.resolve()
    payloads: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    total = 0
    for path in ledger_paths_for_archive(cfg, as_of_date=snapshot_date):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"archive source must be a regular file: {path}")
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError(f"archive source escaped paths.output_root: {path}") from exc
        if excluded_prefixes and relative.startswith(excluded_prefixes):
            excluded.append({"path": f"outputs/{relative}", "size_bytes": resolved.stat().st_size})
            continue
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
    return payloads, excluded


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
            # WO-127: inside the try, so a malformed config stamps status:error
            # instead of raising past _base_payload with nothing recorded.
            excluded_prefixes = _excluded_prefixes(settings)
            sources, excluded = _archive_source_payloads(
                cfg,
                snapshot_date=day,
                source_cap_bytes=source_cap_bytes,
                excluded_prefixes=excluded_prefixes,
            )
            generated = now_utc()
            manifest = {
                "schema_version": 2,
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
                # WO-123: the archive states its own scope. Restore verification
                # reads these prefixes back (intersected with the registered set,
                # never trusting the archive to widen them) so excluded corpora
                # read as excluded-by-design instead of a broken chain.
                "excluded_path_prefixes": list(excluded_prefixes),
                "excluded_reason": ARCHIVE_EXCLUSION_REASON if excluded_prefixes else "",
                "excluded_file_count": len(excluded),
                "excluded_bytes": sum(int(row["size_bytes"]) for row in excluded),
                "excluded_files": excluded,
                "paper_trading_invoked": False,
                "live_trading_invoked": False,
            }
            # Codex review of #364 (P2, two findings). An operator who NARROWS the
            # exclusion set on a restored host before re-harvest recreates the corpus
            # produces an archive that omits it, because an absent file is invisible
            # to the builder. Reporting that was not enough: push_vps_archive.sh gates
            # only on `status`, so an incomplete archive would still force-replace the
            # SOLE remote snapshot and stamp the RPO as met - a diagnostic no consumer
            # reads is the same fail-silent class this batch exists to remove. So the
            # build REFUSES, before the archive file is written, and the refusal is
            # stamped where the recovery watchdog reads it.
            archived_relatives = {
                str(row["path"])[len("outputs/") :]
                for row in sources
                if str(row["path"]).startswith("outputs/")
            }
            # Codex review of #364 (P1) - a regression introduced by the previous
            # commit. Whether a missing path is excluded BY SCOPE must be read from
            # the effective exclusion set, never from `excluded`: that list is built
            # by walking files that exist, so an absent path can never appear in it.
            # Immediately after a restore every anchored corpus path is absent by
            # design while the configured prefix still declares it excluded, so
            # deriving the answer from `excluded` classified them as coverage gaps and
            # refused the next archive - wedging the recurring recovery lane after
            # every restore until collection recreated the corpus. That is the exact
            # class of failure WO-127 exists to remove, reintroduced one layer over.
            pending_reharvest = sorted(
                relative
                for relative in anchored_relative_paths(cfg, as_of_date=day)
                if relative.startswith(ARCHIVE_EXCLUDED_PREFIXES)
                and not relative.startswith(tuple(excluded_prefixes))
                and relative not in archived_relatives
            )
            # Codex review of #364 (P1). The absent-corpus case above is not the only
            # way a narrowed archive can lie. If the restore and the re-harvest happen
            # on the archive's own snapshot day, `anchor_ledgers` returns
            # already_anchored and writes NO new row - so the corpus is present, passes
            # the coverage check, and yet every chain row naming it predates the
            # boundary and is waived. Those bytes would enter the recovery archive
            # having never resumed tamper coverage: attacker-chosen content with a
            # self-consistent archive manifest would be accepted. Including a corpus
            # therefore requires it to be anchored `present` AFTER the boundary.
            inherited_boundary = str(verification.get("restore_boundary_date") or "")
            unauthenticated: list[str] = []
            if inherited_boundary:
                covered_since_restore = anchored_relative_paths(
                    cfg, as_of_date=day, after_date=inherited_boundary
                )
                unauthenticated = sorted(
                    relative
                    for relative in archived_relatives
                    if relative.startswith(ARCHIVE_EXCLUDED_PREFIXES)
                    and relative not in covered_since_restore
                )
            payload["archive_pending_reharvest_paths"] = pending_reharvest
            payload["archive_unanchored_since_restore_paths"] = unauthenticated
            payload["archive_coverage_complete"] = not pending_reharvest and not unauthenticated
            if unauthenticated:
                raise ValueError(
                    "archive would carry bytes no anchor attests: "
                    f"{len(unauthenticated)} path(s) under a registered prefix are present but "
                    f"have no `present` anchor after the restore boundary {inherited_boundary} "
                    f"(first: {unauthenticated[0]}). The daily anchor lane must record them on a "
                    "later UTC day before they can enter the recovery archive; until then keep the "
                    "prefix in disaster_recovery.excluded_path_prefixes"
                )
            if pending_reharvest:
                raise ValueError(
                    "archive would claim coverage it does not have: "
                    f"{len(pending_reharvest)} anchored path(s) under a registered prefix are "
                    f"absent and not declared excluded (first: {pending_reharvest[0]}). Either "
                    "wait for re-harvest to recreate them, or restore the prefix to "
                    "disaster_recovery.excluded_path_prefixes so the archive states its real scope"
                )
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
                    "archive_excluded_paths": list(excluded_prefixes),
                    "archive_pending_reharvest_paths": pending_reharvest,
                    "archive_coverage_complete": not pending_reharvest,
                    "archive_excluded_file_count": len(excluded),
                    "archive_excluded_bytes": manifest["excluded_bytes"],
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


def _normalised_prefixes(value: Any) -> set[str]:
    """Normalise an untrusted exclusion declaration to comparable prefixes.

    Empty and whitespace-only entries are dropped - `startswith("")` is true for
    every path, so honouring one would turn a declaration into a wildcard.
    """

    if not isinstance(value, (list, tuple)):
        return set()
    normalised: set[str] = set()
    for item in value:
        text = str(item).strip().replace("\\", "/").lstrip("/")
        if not text:
            continue
        normalised.add(text if text.endswith("/") else f"{text}/")
    return normalised


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
    _reject_contradictory_exclusions(manifest, actual_paths)
    return manifest, files


def _reject_contradictory_exclusions(manifest: dict[str, Any], member_paths: set[str]) -> None:
    """Refuse an archive that both declares a prefix excluded and ships it.

    Codex review of #364 (P1). WO-127 grants boundary-scoped verification
    tolerance from the manifest's exclusion declaration, and the marker is
    installed BEFORE chain verification runs. So an archive that declares
    ``polymarket_training/`` excluded while also carrying an
    ``outputs/polymarket_training/...`` member gets that member's anchored digest
    SKIPPED: arbitrary bytes with a self-consistent archive manifest would restore
    with ``status: ok``. The declaration would be granting tolerance for a file
    the archive itself supplies.

    No archive this code builds can take that shape - ``_archive_source_payloads``
    reports excluded paths instead of adding them - so the shape only arises from a
    forged or corrupted archive, and it is rejected on the untrusted-input boundary
    rather than reconciled later. Rejection is keyed on the DECLARED set, not the
    registered/config intersection, so the archive is refused even when the
    contradiction happens not to be exploitable under the current configuration.
    """

    # Codex review of #364 (P1): this matched RAW declarations while
    # verify_and_restore_archive matched normalised ones, and a mismatch between two
    # readings of the same untrusted field is a bypass by construction. A declaration
    # of "/polymarket_training/" missed here, then normalised into the registered
    # exclusion there - so the marker waived the included member's pre-boundary digest
    # and arbitrary self-consistent bytes restored. One normalisation, used by both.
    prefixes = tuple(sorted(_normalised_prefixes(manifest.get("excluded_path_prefixes"))))
    if not prefixes:
        return
    contradictory = sorted(
        name
        for name in member_paths
        if name.startswith("outputs/") and name[len("outputs/") :].startswith(prefixes)
    )
    if contradictory:
        raise ValueError(
            "archive declares "
            + ", ".join(prefixes)
            + " excluded while also including "
            + f"{len(contradictory)} member(s) under those prefixes "
            + f"(first: {contradictory[0]}); a declaration cannot excuse verification of a file "
            "the archive supplies"
        )


def _write_restore_provenance(
    output_root: Path,
    *,
    excluded_prefixes: tuple[str, ...],
    boundary_date: str,
    chain_head: str,
    dry_run: bool,
) -> Path | None:
    """Record why a restored tree is missing registered, excluded paths.

    WO-127. Without this, a restore verified clean and the next production
    ``anchor_ledgers`` run froze the head on ``blocked_broken_chain``, because
    verification there has no caller-supplied tolerance. The marker makes the
    tolerance a property of the tree, so both callers agree.

    ``restore_boundary_date`` is the archive's snapshot date: entries anchored at
    or before it recorded digests for bytes that cannot be reproduced (a
    re-harvest yields different content), so they are unverifiable by design.
    Anything anchored after it is verified normally.

    Codex review of #364 (P1): a boundary is a fact about the CHAIN's history, not
    about one archive's exclusion set, so an INHERITED marker carried in the
    archive is never discarded. Without this, an archive built on a restored host
    with ``excluded_path_prefixes: []`` - the documented tighten-only option of
    putting the corpus back into recovery - wrote no marker into the extracted
    tree, so the pre-restore rows were compared against re-harvested bytes and
    every such archive was rejected. Archive creation on a restored host would
    have failed permanently. Boundaries therefore MERGE to the later date and the
    prefix sets union: rows between the two boundaries are excused only when this
    archive also drops the corpus, which is exactly when they are unverifiable.
    """

    path = output_root / PurePosixPath(RESTORE_PROVENANCE_FILE)
    inherited = read_json(path, default={}) or {}
    if not isinstance(inherited, dict):
        inherited = {}
    inherited_prefixes = inherited.get("excluded_path_prefixes")
    prefixes = {str(item) for item in excluded_prefixes}
    if isinstance(inherited_prefixes, (list, tuple)):
        prefixes |= {str(item) for item in inherited_prefixes}
    # The reader intersects against the REGISTERED set regardless, so an
    # inherited prefix cannot widen the waiver beyond it.
    prefixes &= set(ARCHIVE_EXCLUDED_PREFIXES)
    inherited_boundary = str(inherited.get("restore_boundary_date") or "").strip()
    # This archive's own snapshot date extends the waiver ONLY when this archive
    # actually drops the corpus. When it carries the regenerated corpus instead,
    # rows anchored since the inherited boundary recorded bytes the archive
    # supplies, so they must verify normally - extending the boundary over them
    # would excuse a tampered restored corpus.
    new_boundary = str(boundary_date or "").strip() if excluded_prefixes else ""
    # Compare as calendar dates, never as strings: both sides are untrusted input
    # and a non-canonical spelling sorts wrongly (#364 P1). A side that does not
    # parse loses to one that does, and if neither parses no marker is written -
    # so verification refuses rather than honouring an unreadable boundary.
    # The boundary and the chain head it names travel as a PAIR. Verification binds
    # them (a boundary must be the anchor date of the row carrying that head), so
    # keeping the inherited boundary while recording this archive's head would
    # produce a marker that refuses itself.
    inherited_head = str(inherited.get("restored_from_chain_head") or "").strip()
    new_head = str(chain_head or "").strip()
    # Codex review of #364 (P2), twice. Ranking by date was wrong in both directions:
    # first an inherited pair sharing the date displaced the valid current pair, then
    # ANY later-but-invalid inherited pair did - a future date or a head that names no
    # row - so post-build restore refused the marker, found the deliberately excluded
    # corpus missing, and failed archive creation. That is the wedge class this work
    # order exists to remove, arriving through the ranking function.
    #
    # Ranking is not needed at all. When this archive excludes the corpus it supplies
    # its own boundary, which is the snapshot date and therefore never earlier than an
    # inherited boundary (a boundary must name a row at or before it). So: use the
    # current pair whenever it exists, and fall back to the inherited pair only when
    # this archive supplies none - which is exactly the case the inheritance exists
    # for. An inherited pair is eligible only if it could actually be honoured: a
    # canonical, non-future boundary and a non-empty head.
    effective_boundary, effective_head = "", ""
    if new_boundary and new_head:
        effective_boundary, effective_head = new_boundary, new_head
    elif inherited_boundary and inherited_head:
        inherited_day = canonical_date(inherited_boundary)
        today = canonical_date(now_utc()[:10])
        if inherited_day is not None and today is not None and inherited_day <= today:
            effective_boundary, effective_head = inherited_boundary, inherited_head
    if not prefixes or not effective_boundary:
        return None
    write_json(
        path,
        {
            "work_order": "WO-127",
            "generated_at_utc": now_utc(),
            "restore_boundary_date": effective_boundary,
            "excluded_path_prefixes": sorted(prefixes),
            "restored_from_chain_head": effective_head,
            "inherited_restore_boundary_date": inherited_boundary or None,
            "archive_snapshot_date": new_boundary or None,
            "dry_run": bool(dry_run),
            "reason": (
                "these registered prefixes are excluded from the recovery archive and are "
                "regenerable by re-harvest; entries anchored at or before the boundary cannot "
                "be byte-verified and are excused, entries after it are verified normally"
            ),
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )
    return path


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
        # WO-123: tolerate missing files ONLY for prefixes this archive declares
        # excluded AND that the registration carries. The manifest is untrusted
        # input, so the intersection is the ceiling: an archive cannot widen its
        # own tolerance, and an archive declaring nothing (every archive built
        # before WO-123) gets no tolerance at all. Tolerance covers absence only
        # - a present file whose anchored digest changed still fails below.
        declared = manifest.get("excluded_path_prefixes")
        # Codex review of #364 (P2): NORMALISE before matching. A forged manifest can
        # declare [""], and every path satisfies startswith("") - so an archive could
        # claim to have "declared" a prefix it never named and slip past the
        # undeclared-omission guard below while the inherited marker still supplied
        # the registered waiver. Empty and whitespace entries are dropped, separators
        # normalised, and a trailing slash enforced so a prefix cannot match a sibling
        # file by accident.
        declared_prefixes = _normalised_prefixes(declared)
        # Codex review of #364 (P2) corrected my reasoning here, and it was the better
        # argument. I based the missing-path exception on "did the archive validly
        # declare this prefix", on the grounds that validity is a property of the
        # archive rather than of the host. That conflated two questions. Whether the
        # DECLARATION is well formed is indeed the archive's property - and it still
        # gates the contradiction check. But whether a restore delivers the coverage
        # REQUIRED is the restoring host's property: if this host's config says to
        # archive the corpus, a restore lacking it does not satisfy this host, however
        # the archive labelled itself. The exception is therefore based on `tolerated`,
        # the manifest-declared / registered / config-effective intersection.
        # WO-127: also intersect the CONFIG-EFFECTIVE set. A config that NARROWS
        # exclusions means more was archived, so tolerating the full registered
        # set would excuse the absence of something that should be present.
        tolerated = tuple(
            prefix
            for prefix in ARCHIVE_EXCLUDED_PREFIXES
            if prefix in declared_prefixes and prefix in set(_excluded_prefixes(settings))
        )
        with tempfile.TemporaryDirectory(prefix="polymarket-ledger-restore-") as temp_dir:
            extracted_output = _materialise_files(files, Path(temp_dir))
            # WO-127: the restore check now reads the same provenance marker the
            # production anchor lane reads, so the two cannot disagree. Writing it
            # into the extracted tree BEFORE verification is what makes the
            # verified tree and the handed-over tree the same tree.
            _write_restore_provenance(
                extracted_output,
                excluded_prefixes=tolerated,
                boundary_date=snapshot_date,
                chain_head=str(manifest.get("chain_head") or ""),
                dry_run=dry_run,
            )
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
            # Codex review of #364 (P2). I removed this check once as redundant; it is
            # redundant ONLY for an archive built after a re-harvest, where the corpus
            # carries a post-boundary `present` row that chain verification already
            # enforces. The still-excluded shape is not covered: an archive built while
            # the prefix was excluded carries the inherited marker and no corpus, and
            # relabelling its manifest to declare no exclusions is self-consistent -
            # the file set already matches. The inherited prefix is then imported by
            # the union, historical rows are waived, later missing_at_anchor rows are
            # tolerated by design, and it restores ok while claiming full coverage.
            # An archive that does not DECLARE a prefix excluded is asserting coverage
            # of it, so every historically anchored path under it must be present -
            # independent of any inherited provenance.
            undeclared_missing = sorted(
                relative
                for relative in anchored_relative_paths(extracted_cfg, as_of_date=snapshot_date)
                if relative.startswith(ARCHIVE_EXCLUDED_PREFIXES)
                and not relative.startswith(tolerated)
                and not (extracted_output / PurePosixPath(relative)).is_file()
            )
            # Codex review of #364 (P1): the guard above checks only that a path
            # EXISTS, so adding bytes satisfied it. An archive built on a restored
            # host while the prefix was excluded carries the inherited marker and
            # only `missing_at_anchor` rows after that boundary; relabel it to
            # declare no exclusions and add arbitrary self-consistent corpus bytes,
            # and nothing ever digest-checks them - pre-boundary rows are waived by
            # the inherited marker and post-boundary rows are missing_at_anchor,
            # which performs no byte check. The forged corpus restored as recovered
            # evidence. Presence is not attestation: bytes under a waived prefix are
            # accepted only where an anchor recorded them `present` AFTER the
            # boundary, mirroring the builder-side rule.
            honoured_boundary = str(chain.get("restore_boundary_date") or "")
            unattested: list[str] = []
            if honoured_boundary:
                attested_since_restore = anchored_relative_paths(
                    extracted_cfg, as_of_date=snapshot_date, after_date=honoured_boundary
                )
                unattested = sorted(
                    name[len("outputs/") :]
                    for name in files
                    if name.startswith("outputs/")
                    and name[len("outputs/") :].startswith(ARCHIVE_EXCLUDED_PREFIXES)
                    and name[len("outputs/") :] not in attested_since_restore
                )
            if unattested:
                raise ValueError(
                    "archive carries bytes under a waived prefix that no anchor attests: "
                    f"{len(unattested)} path(s) have no `present` anchor after the inherited "
                    f"restore boundary {honoured_boundary} (first: {unattested[0]}); a restore "
                    "waiver excuses absence, it does not authenticate replacement bytes"
                )
            if undeclared_missing:
                raise ValueError(
                    "archive omits "
                    f"{len(undeclared_missing)} anchored path(s) under a registered prefix it does "
                    f"not declare excluded (first: {undeclared_missing[0]}); an inherited restore "
                    "boundary excuses only what a declared exclusion accounts for, never an "
                    "undeclared omission"
                )
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
                "excluded_path_prefixes_tolerated": list(tolerated),
                "restored_unverifiable_tolerated": int(
                    chain.get("restored_unverifiable_tolerated") or 0
                ),
                # WO-127: make the narrowed recovery state explicit in the report.
                # A restore that cannot bring these paths back is a successful
                # restore of the evidence ledgers, not a complete tree.
                "restored_without_prefixes": list(tolerated),
                # Codex review of #364 (P2): report the boundary actually HONOURED,
                # not this archive's snapshot date. On a restored host whose
                # exclusions were narrowed, the installed marker deliberately keeps
                # the older inherited boundary, and reporting the snapshot date here
                # contradicted both the marker and the chain verification - handing
                # the operator the wrong scope for the waiver.
                "restore_boundary_date": chain.get("restore_boundary_date"),
                "restore_provenance_rejected": chain.get("restore_provenance_rejected"),
                "file_count": len(manifest.get("files") or []),
                "archive_sha256": hashlib.sha256(Path(archive_path).read_bytes()).hexdigest(),
                "restore_applied": not dry_run,
            }
        )
    except Exception as exc:
        payload.update({"status": "error", "error": f"{type(exc).__name__}: {exc}", "failure_stamped": True})
        # Codex review of #364 (P2): the failure path used to discard the chain
        # diagnostics, so a restore that failed BECAUSE its inherited marker was
        # malformed reported neither the rejection reason nor the verification
        # result - the registered fail-safe reason existed and was thrown away at
        # exactly the moment an operator needs it.
        if isinstance(locals().get("chain"), dict):
            diagnostics: dict[str, Any] = locals()["chain"]
            payload.setdefault("ledger_chain_verification", diagnostics)
            payload.setdefault(
                "restore_provenance_rejected", diagnostics.get("restore_provenance_rejected")
            )
            payload.setdefault("restore_boundary_date", diagnostics.get("restore_boundary_date"))
        write_json(report_path, payload)
        raise DisasterRecoveryError(payload["error"]) from exc
    write_json(report_path, payload)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return create_ledger_archive(load_config(config_path))
