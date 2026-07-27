"""WO-61 tamper-evident anchoring for investor-facing evidence ledgers.

The chain stores a byte length and SHA-256 prefix digest for every enrolled
ledger on each UTC day.  A later append therefore leaves every prior anchor
verifiable, while a change to any previously anchored byte identifies the
first affected date.  This module is reporting infrastructure only: no gate,
sizing rule, policy, broker, or order path reads its outputs.
"""

from __future__ import annotations

import csv
from datetime import date
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
from typing import Any, NamedTuple

from .config import EngineConfig, load_config
from .runtime_lock import runtime_lock
from .utils import ensure_dir, now_utc, read_csv_rows, read_json, write_json


GENESIS_HEAD = "0" * 64
CHAIN_FIELDS = [
    "anchor_date",
    "anchored_at_utc",
    "previous_chain_head",
    "chain_head",
    "manifest_sha256",
    "ledger_manifest_json",
    "ledger_count",
    "present_count",
    "missing_count",
    "paper_trading_invoked",
    "live_trading_invoked",
]

# Most sources are append-only.  ``shadow_positions.csv`` is a state table and
# ``decision_policy.json`` and WO-66 ``requote_alerts.json`` are regenerated,
# so immutable daily copies are anchored for state-table sources instead of falsely treating an authorised
# rewrite as retroactive tampering.
DEFAULT_LEDGER_REGISTRY: list[dict[str, str]] = [
    {"glob": "polymarket_portfolio/portfolio_snapshots.csv", "mode": "append_only"},
    {"glob": "polymarket_portfolio/cash_ledger.csv", "mode": "append_only"},
    {"glob": "polymarket_portfolio/paper_fills.csv", "mode": "append_only"},
    # WO-110: full taker-fee fills export; a regenerated dump is anchored as a
    # daily snapshot, never append_only (the paper_fills.csv lesson).
    {"glob": "polymarket_portfolio/paper_fills_v2.csv", "mode": "snapshot"},
    {"glob": "polymarket_portfolio/settlements.csv", "mode": "append_only"},
    {"glob": "polymarket_shadow/shadow_positions.csv", "mode": "snapshot"},
    {"glob": "polymarket_shadow/shadow_fills.csv", "mode": "append_only"},
    # WO-115 (2026-07-26): reclassified append_only -> snapshot. The study's
    # committer legitimately REWRITES this file (legacy-schema upgrade path in
    # maker_carry_study.py), so an authorized header widening re-serialised
    # historical rows and broke the 2026-07-12 anchored prefix, blocking every
    # subsequent anchor run. Snapshot mode anchors an immutable daily copy,
    # exactly like the other regenerated sources below.
    {"glob": "maker_carry/maker_carry_history.csv", "mode": "snapshot"},
    # WO-111: forward-only per-day portfolio membership + per-market markout sidecar.
    # Fixed two-column schema so it can never need a column-addition rewrite; a
    # brand-new (initially empty) append_only file is anchor-safe.
    {"glob": "maker_carry/maker_carry_portfolio_members.csv", "mode": "append_only"},
    {"glob": "maker_carry/reward_epoch_samples.csv", "mode": "append_only"},
    {"glob": "maker_carry/maker_live_test_history.csv", "mode": "append_only"},
    {"glob": "maker_carry/maker_live_test_wallet_history.csv", "mode": "append_only"},
    {"glob": "maker_carry/maker_live_test_attribution_history.csv", "mode": "append_only"},
    {"glob": "maker_carry/decision_policy.json", "mode": "snapshot"},
    {"glob": "maker_carry/sharp_linking_qualification.json", "mode": "snapshot"},
    {"glob": "maker_carry/requote_alerts.json", "mode": "snapshot"},
    {"glob": "execution/execution_ledger.csv", "mode": "append_only"},
    {"glob": "execution/stage_operator_log.csv", "mode": "append_only"},
    {"glob": "execution/a1_sweep_advisory.json", "mode": "snapshot"},
    {"glob": "execution/executor_status.json", "mode": "snapshot"},
    {"glob": "performance/cost_ledger.csv", "mode": "append_only"},
    {"glob": "h3_smart_flow/h3_final_fills.csv", "mode": "append_only"},
    {"glob": "h2_dutch/h2_scan_observations_v1.csv", "mode": "append_only"},
    {"glob": "h2_dutch/h2_final_episodes_v1.csv", "mode": "append_only"},
    {"glob": "h2_dutch/h2_final_sample_manifest.json", "mode": "snapshot"},
    {"glob": "polymarket_training/resolution_corpus_v1.csv", "mode": "append_only"},
    {"glob": "polymarket_training/historical_bid_ask_v1.csv", "mode": "append_only"},
    {"glob": "performance/background_timeout_incidents.csv", "mode": "append_only"},
    {"glob": "performance/degraded_state_incidents.csv", "mode": "append_only"},
    {"glob": "performance/wallet_reconciliation_history.csv", "mode": "append_only"},
    {"glob": "performance/wallet_reconciliation_wallet_history.csv", "mode": "append_only"},
    {"glob": "performance/cost_ledger_summary.json", "mode": "snapshot"},
]

# Registered by WO-123 (2026-07-26), relocated here by WO-127: these enrolled
# paths are deliberately EXCLUDED from the WO-65 recovery archive. They are
# derived collection corpora — regenerable by re-harvest, and 94% of the archive
# bytes (476.6MB of 505.6MB measured on the VPS). They stay ANCHORED, so tamper
# evidence over them is unchanged; only recovery scope narrows.
#
# The set lives in this module, not in disaster_recovery, because verification
# must intersect against it and disaster_recovery already imports this module —
# the reverse would be circular.
ARCHIVE_EXCLUDED_PREFIXES: tuple[str, ...] = ("polymarket_training/",)

# Written by an applied restore; read by every verification caller.
RESTORE_PROVENANCE_FILE = "performance/ledger_restore_provenance.json"


class _RestoreProvenance(NamedTuple):
    """What an applied restore recorded about the tree it handed over."""

    tolerated_prefixes: tuple[str, ...]
    boundary_date: date | None
    boundary_text: str
    rejected_reason: str


_NO_RESTORE_PROVENANCE = _RestoreProvenance((), None, "", "")


def canonical_date(text: str) -> date | None:
    """Parse a canonical ``YYYY-MM-DD`` day, or None for anything else.

    Shared with ``disaster_recovery`` so both modules agree on what a date is; a
    comparison that depends on spelling is the #364 P1 defect class.
    """

    candidate = str(text or "").strip()
    try:
        parsed = date.fromisoformat(candidate)
    except ValueError:
        return None
    # Python 3.11's fromisoformat also accepts basic and week formats; only the
    # canonical spelling counts, so a comparison can never be spelling-dependent.
    return parsed if candidate == parsed.isoformat() else None


def _restore_provenance(cfg: EngineConfig) -> _RestoreProvenance:
    """Read the restore marker: tolerated prefixes, boundary, rejection reason.

    WO-127. An applied restore records which registered prefixes it could not
    restore and the snapshot date it restored to. Verification honours that
    record so the production anchor lane and the DR restore check agree by
    construction, instead of the restore check opting in and the anchor lane —
    the one that actually freezes the head — not knowing.

    Trust bounds, all four necessary:

    1. Only prefixes in the REGISTERED set above can be excused, so a forged or
       edited marker cannot reach anything beyond the re-harvestable corpora in
       the registered exclusion set above.
    2. The boundary must be a STRICT calendar date, not merely ten characters.
       Codex review of #364 (P1): a length check accepted `9999-99-99`, and
       because the row comparison was lexical, that sorted every historical anchor
       as pre-boundary — turning a scoped excuse into a blanket one and voiding
       the post-boundary tamper check entirely.
    3. The boundary may not be in the future. A restore cannot come from an
       archive that does not exist yet, and a future date would excuse rows that
       have not been written, which is the same blanket excuse by another route.
    4. The boundary is returned as a `date`, and the caller compares PARSED anchor
       dates against it. Codex review of #364 (second P1): a valid but
       non-zero-padded `2026-7-1` parses, is not in the future, and yet sorts
       lexically ABOVE every canonical `2026-0M-DD` row — so string comparison
       excused post-boundary rows too. Comparing dates removes the entire class
       rather than rejecting one spelling of it, and a non-canonical spelling is
       additionally refused because the only writer of this marker emits
       `date.isoformat()`.

    A marker that fails any of these is REJECTED, not silently ignored: the
    reason is surfaced in the verification result so an operator sees why their
    marker did not apply instead of reading an unexplained broken chain. The same
    applies to a marker that is present but unreadable — Codex review of #364
    (P2) — because `read_json` maps unparseable JSON to its default, which would
    otherwise make a truncated or hand-edited marker indistinguishable from no
    marker at all: a broken chain with a null diagnosis.
    """

    path = _output_path_relative(cfg, RESTORE_PROVENANCE_FILE)
    if not path.is_file():
        return _NO_RESTORE_PROVENANCE
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _RestoreProvenance(
            (),
            None,
            "",
            (
                f"restore provenance at {RESTORE_PROVENANCE_FILE} exists but cannot be read "
                f"({type(exc).__name__}: {exc}); the marker is refused and every anchored path "
                "is verified"
            ),
        )
    if not isinstance(payload, dict):
        return _RestoreProvenance(
            (),
            None,
            "",
            (
                f"restore provenance at {RESTORE_PROVENANCE_FILE} is a "
                f"{type(payload).__name__}, not a JSON object; the marker is refused and every "
                "anchored path is verified"
            ),
        )
    declared = payload.get("excluded_path_prefixes")
    declared_set = {str(item) for item in declared} if isinstance(declared, (list, tuple)) else set()
    tolerated = tuple(prefix for prefix in ARCHIVE_EXCLUDED_PREFIXES if prefix in declared_set)
    raw_boundary = str(payload.get("restore_boundary_date") or "").strip()
    if not tolerated:
        # The marker EXISTS, so say why it excused nothing. Reaching this with an
        # empty or prefix-less payload is a malformed marker, not the absence of
        # one, and the two must not read alike.
        return _RestoreProvenance(
            (),
            None,
            "",
            (
                "restore provenance declares no registered excluded prefix; nothing is excused "
                "and every anchored path is verified"
            ),
        )
    boundary_date = canonical_date(raw_boundary)
    if boundary_date is None:
        return _RestoreProvenance(
            (),
            None,
            "",
            (
                f"restore_boundary_date {raw_boundary!r} is not a canonical YYYY-MM-DD calendar "
                "date; the marker is refused and every anchored path is verified"
            ),
        )
    today = canonical_date(now_utc()[:10])
    if today is None:  # pragma: no cover - now_utc() is canonical by construction
        return _RestoreProvenance(
            (),
            None,
            "",
            (
                "the run clock did not yield a canonical UTC date, so the restore boundary cannot "
                "be bounded; the marker is refused and every anchored path is verified"
            ),
        )
    if boundary_date > today:
        return _RestoreProvenance(
            (),
            None,
            "",
            (
                f"restore_boundary_date {raw_boundary} is in the future (today is {today}); "
                "the marker is refused and every anchored path is verified"
            ),
        )
    return _RestoreProvenance(tolerated, boundary_date, raw_boundary, "")


def _output_path_relative(cfg: EngineConfig, relative: str) -> Path:
    return cfg.output_root.joinpath(*PurePosixPath(relative).parts)


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = cfg.raw.get("ledger_anchor", {}) if isinstance(cfg.raw.get("ledger_anchor"), dict) else {}
    merged: dict[str, Any] = {
        "enabled": True,
        "ledger_globs": DEFAULT_LEDGER_REGISTRY,
        "chain_file": "performance/ledger_anchor_chain.csv",
        "head_file": "performance/ledger_anchor_head.json",
        "summary_file": "performance/ledger_anchor_summary.json",
        "verification_file": "performance/ledger_anchor_verification.json",
        "snapshot_root": "performance/ledger_anchor_snapshots",
        "external_anchor_branch": "vps-anchor",
        "lock_stale_seconds": 1800,
    }
    merged.update({key: value for key, value in raw.items() if value is not None})
    return merged


def _enabled(value: Any) -> bool:
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _safe_relative(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    path = Path(text)
    if not text or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"ledger anchor path must stay below paths.output_root: {value!r}")
    return path.as_posix()


def _registry(settings: dict[str, Any]) -> list[dict[str, str]]:
    raw_entries = settings.get("ledger_globs")
    if not isinstance(raw_entries, list):
        raise ValueError("ledger_anchor.ledger_globs must be a list")
    entries: list[dict[str, str]] = []
    for raw in raw_entries:
        if isinstance(raw, str):
            pattern = _safe_relative(raw)
            mode = "append_only"
        elif isinstance(raw, dict):
            pattern = _safe_relative(raw.get("glob") or raw.get("pattern") or raw.get("path"))
            mode = str(raw.get("mode") or "append_only").strip().lower()
        else:
            raise ValueError("each ledger_anchor.ledger_globs entry must be a string or mapping")
        if mode not in {"append_only", "snapshot"}:
            raise ValueError(f"unsupported ledger anchor mode {mode!r} for {pattern}")
        entries.append({"glob": pattern, "mode": mode})
    return entries


def _output_path(cfg: EngineConfig, value: Any) -> Path:
    return cfg.output_root / _safe_relative(value)


def _sha256_prefix(path: Path, byte_length: int) -> str:
    if byte_length < 0:
        raise ValueError("byte_length cannot be negative")
    digest = hashlib.sha256()
    remaining = byte_length
    with path.open("rb") as handle:
        while remaining:
            block = handle.read(min(1024 * 1024, remaining))
            if not block:
                raise EOFError(f"{path} is shorter than anchored length {byte_length}")
            digest.update(block)
            remaining -= len(block)
    return digest.hexdigest()


def _manifest_json(manifest: list[dict[str, Any]]) -> str:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _chain_head(previous_head: str, manifest_json: str, anchor_date: str) -> str:
    # H_today = sha256(H_yesterday || canonical tuples || UTC date).
    material = f"{previous_head}{manifest_json}{anchor_date}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _matches(output_root: Path, pattern: str) -> list[Path]:
    root = output_root.resolve()
    matches: list[Path] = []
    for candidate in output_root.glob(pattern):
        if not candidate.is_file():
            continue
        try:
            candidate.resolve().relative_to(root)
        except ValueError:
            continue
        matches.append(candidate)
    return sorted(set(matches), key=lambda path: path.as_posix())


def _snapshot_path(cfg: EngineConfig, settings: dict[str, Any], anchor_date: str, relative: str) -> Path:
    return _output_path(cfg, settings["snapshot_root"]) / anchor_date / relative


def _collect_manifest(cfg: EngineConfig, settings: dict[str, Any], anchor_date: str) -> list[dict[str, Any]]:
    output_root = cfg.output_root
    root_resolved = output_root.resolve()
    manifest: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for registration in _registry(settings):
        pattern = registration["glob"]
        mode = registration["mode"]
        matches = _matches(output_root, pattern)
        if not matches:
            missing_key = (pattern, mode)
            if missing_key not in seen:
                seen.add(missing_key)
                manifest.append(
                    {
                        "path": pattern,
                        "verification_path": pattern,
                        "mode": mode,
                        "status": "missing_at_anchor",
                        "byte_length": 0,
                        "prefix_sha256": "",
                    }
                )
            continue
        for source in matches:
            relative = source.resolve().relative_to(root_resolved).as_posix()
            key = (relative, mode)
            if key in seen:
                continue
            seen.add(key)
            verification = source
            verification_relative = relative
            if mode == "snapshot":
                verification = _snapshot_path(cfg, settings, anchor_date, relative)
                ensure_dir(verification.parent)
                if verification.exists():
                    source_length = source.stat().st_size
                    if verification.stat().st_size != source_length or _sha256_prefix(verification, source_length) != _sha256_prefix(source, source_length):
                        raise FileExistsError(f"immutable daily ledger snapshot already differs: {verification}")
                else:
                    shutil.copyfile(source, verification)
                verification_relative = verification.resolve().relative_to(root_resolved).as_posix()
            byte_length = verification.stat().st_size
            manifest.append(
                {
                    "path": relative,
                    "verification_path": verification_relative,
                    "mode": mode,
                    "status": "present",
                    "byte_length": byte_length,
                    "prefix_sha256": _sha256_prefix(verification, byte_length),
                }
            )
    return sorted(manifest, key=lambda row: (str(row["path"]), str(row["mode"])))


def _parse_manifest(row: dict[str, Any]) -> list[dict[str, Any]]:
    raw = row.get("ledger_manifest_json")
    parsed = json.loads(str(raw or "[]"))
    if not isinstance(parsed, list) or any(not isinstance(item, dict) for item in parsed):
        raise ValueError("ledger_manifest_json must contain a list of mappings")
    return parsed


def _append_chain_row(path: Path, row: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CHAIN_FIELDS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow(row)
        handle.flush()
        os.fsync(handle.fileno())


def _verification_base(*, chain_path: Path, as_of_date: str | None) -> dict[str, Any]:
    return {
        "status": "empty",
        "generated_at_utc": now_utc(),
        "chain_path": str(chain_path),
        "as_of_date": as_of_date,
        "links_checked": 0,
        "ledger_prefixes_checked": 0,
        "missing_at_anchor_tolerated": 0,
        "restored_unverifiable_tolerated": 0,
        "restore_boundary_date": None,
        "restore_tolerated_prefixes": [],
        "restore_provenance_rejected": None,
        "first_broken_date": None,
        "issues": [],
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }


def verify_ledger_chain(
    cfg: EngineConfig,
    *,
    as_of_date: str | None = None,
    write_summary: bool = True,
) -> dict[str, Any]:
    """Verify chain linkage and every historical byte prefix in row order.

    WO-127 replaces WO-123's caller-supplied ``tolerated_missing_prefixes``.
    That parameter was opt-in, so only the disaster-recovery restore check
    passed it: a restore verified clean and then the very next production
    ``anchor_ledgers`` run — which calls this with no opt-in — read the
    deliberately-excluded corpora as "anchored file is missing" and froze the
    head on ``blocked_broken_chain``. Recovery handed over a tree that wedged
    the tamper lane.

    Tolerance is therefore a property of the TREE's recorded provenance, not of
    the caller: an applied restore writes a marker naming the prefixes it could
    not restore and the snapshot date it restored to, and every caller here
    honours the same thing. See ``_restore_provenance``.
    """

    settings = _settings(cfg)
    provenance = _restore_provenance(cfg)
    tolerated = provenance.tolerated_prefixes
    boundary_day = provenance.boundary_date
    provenance_rejected = provenance.rejected_reason
    chain_path = _output_path(cfg, settings["chain_file"])
    verification_path = _output_path(cfg, settings["verification_file"])
    result = _verification_base(chain_path=chain_path, as_of_date=as_of_date)
    result["restore_boundary_date"] = provenance.boundary_text or None
    result["restore_tolerated_prefixes"] = list(tolerated)
    # A refused marker is reported, never silently dropped: otherwise the
    # operator sees a broken chain with no hint that their marker was ignored.
    result["restore_provenance_rejected"] = provenance_rejected or None
    rows = read_csv_rows(chain_path)
    expected_previous = GENESIS_HEAD
    previous_date = ""
    for row in rows:
        anchor_date = str(row.get("anchor_date") or "")
        if as_of_date and anchor_date > as_of_date:
            continue
        # WO-127 (Codex #364 P1): the restore-boundary comparison below is on
        # calendar DATES, never strings. A row whose anchor_date is not a
        # canonical date yields None and is therefore never excused - the
        # fail-safe direction is to verify its bytes.
        row_day = canonical_date(anchor_date)
        issues: list[str] = []
        try:
            manifest = _parse_manifest(row)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            manifest = []
            issues.append(f"invalid manifest: {exc}")
        manifest_json = _manifest_json(manifest)
        recorded_previous = str(row.get("previous_chain_head") or "")
        recorded_head = str(row.get("chain_head") or "")
        if not anchor_date:
            issues.append("missing anchor_date")
        if previous_date and anchor_date <= previous_date:
            issues.append(f"anchor_date is not strictly increasing after {previous_date}")
        if recorded_previous != expected_previous:
            issues.append("previous_chain_head does not match the prior verified link")
        expected_head = _chain_head(expected_previous, manifest_json, anchor_date)
        if recorded_head != expected_head:
            issues.append("chain_head does not match the canonical manifest")
        expected_manifest_hash = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
        if str(row.get("manifest_sha256") or "") != expected_manifest_hash:
            issues.append("manifest_sha256 does not match the canonical manifest")

        for item in manifest:
            status = str(item.get("status") or "")
            if status == "missing_at_anchor":
                result["missing_at_anchor_tolerated"] += 1
                continue
            if status != "present":
                issues.append(f"{item.get('path')}: unsupported manifest status {status!r}")
                continue
            try:
                relative = _safe_relative(item.get("verification_path") or item.get("path"))
                anchored_path = cfg.output_root / relative
                byte_length = int(item.get("byte_length"))
                expected_prefix = str(item.get("prefix_sha256") or "")
                # WO-127: rows anchored at or before a restore boundary recorded
                # digests for corpora the archive deliberately excluded. Those
                # bytes are gone and CANNOT come back: a re-harvest produces
                # different content, so the entry would flip from "missing" to
                # "digest changed" and wedge the chain permanently. Such an entry
                # is unverifiable BY DESIGN - neither absence nor divergence is
                # evidence of tampering - so it is excused and counted, never
                # silently skipped. Rows anchored AFTER the boundary are verified
                # normally: they record missing_at_anchor until a re-harvest, then
                # present with fresh digests that do verify.
                if (
                    tolerated
                    and boundary_day is not None
                    and row_day is not None
                    and row_day <= boundary_day
                    and relative.startswith(tolerated)
                ):
                    result["restored_unverifiable_tolerated"] += 1
                    continue
                if not anchored_path.is_file():
                    issues.append(f"{item.get('path')}: anchored file is missing")
                    continue
                actual_prefix = _sha256_prefix(anchored_path, byte_length)
                result["ledger_prefixes_checked"] += 1
                if actual_prefix != expected_prefix:
                    issues.append(f"{item.get('path')}: anchored prefix digest changed")
            except (EOFError, OSError, TypeError, ValueError) as exc:
                issues.append(f"{item.get('path')}: {type(exc).__name__}: {exc}")

        result["links_checked"] += 1
        if issues:
            result["status"] = "broken"
            result["first_broken_date"] = anchor_date or None
            result["issues"] = issues
            break
        expected_previous = recorded_head
        previous_date = anchor_date

    if result["links_checked"] and result["status"] != "broken":
        result["status"] = "ok"
        result["verified_chain_head"] = expected_previous
        result["verified_through_date"] = previous_date or None
    if write_summary:
        write_json(verification_path, result)
    return result


def _write_head_file(
    cfg: EngineConfig,
    settings: dict[str, Any],
    row: dict[str, Any],
    manifest: list[dict[str, Any]],
) -> Path:
    head_path = _output_path(cfg, settings["head_file"])
    write_json(
        head_path,
        {
            "anchor_date": row["anchor_date"],
            "anchored_at_utc": row["anchored_at_utc"],
            "previous_chain_head": row["previous_chain_head"],
            "chain_head": row["chain_head"],
            "manifest_sha256": row["manifest_sha256"],
            "ledger_manifest": manifest,
            "external_anchor_branch": str(settings["external_anchor_branch"]),
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )
    return head_path


def anchor_ledgers(cfg: EngineConfig, *, anchor_date: str | None = None) -> dict[str, Any]:
    settings = _settings(cfg)
    chain_path = _output_path(cfg, settings["chain_file"])
    summary_path = _output_path(cfg, settings["summary_file"])
    day = str(anchor_date or now_utc()[:10])
    summary: dict[str, Any] = {
        "status": "disabled",
        "generated_at_utc": now_utc(),
        "anchor_date": day,
        "chain_path": str(chain_path),
        "external_anchor_branch": str(settings["external_anchor_branch"]),
        "external_anchor_pending": False,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    if not _enabled(settings.get("enabled", True)):
        write_json(summary_path, summary)
        return summary

    with runtime_lock(
        cfg,
        "ledger_anchor",
        stale_after_seconds=float(settings.get("lock_stale_seconds") or 1800),
    ) as lock:
        if not lock.acquired:
            summary.update({"status": "skipped_locked", "runtime_lock": lock.as_dict()})
            write_json(summary_path, summary)
            return summary

        existing = read_csv_rows(chain_path)
        verification = verify_ledger_chain(cfg, write_summary=True)
        if existing and verification["status"] != "ok":
            summary.update(
                {
                    "status": "blocked_broken_chain",
                    "first_broken_date": verification.get("first_broken_date"),
                    "issues": verification.get("issues", []),
                }
            )
            write_json(summary_path, summary)
            return summary

        if existing:
            last = existing[-1]
            last_date = str(last.get("anchor_date") or "")
            if day < last_date:
                raise ValueError(f"anchor_date {day} predates latest chain date {last_date}")
            if day == last_date:
                manifest = _parse_manifest(last)
                head_path = _write_head_file(cfg, settings, last, manifest)
                summary.update(
                    {
                        "status": "already_anchored",
                        "chain_head": last.get("chain_head"),
                        "head_path": str(head_path),
                        "ledger_count": len(manifest),
                        "external_anchor_pending": True,
                    }
                )
                write_json(summary_path, summary)
                return summary

        manifest = _collect_manifest(cfg, settings, day)
        manifest_json = _manifest_json(manifest)
        previous_head = str(existing[-1].get("chain_head") or GENESIS_HEAD) if existing else GENESIS_HEAD
        stamp = now_utc()
        present_count = sum(1 for item in manifest if item["status"] == "present")
        row = {
            "anchor_date": day,
            "anchored_at_utc": stamp,
            "previous_chain_head": previous_head,
            "chain_head": _chain_head(previous_head, manifest_json, day),
            "manifest_sha256": hashlib.sha256(manifest_json.encode("utf-8")).hexdigest(),
            "ledger_manifest_json": manifest_json,
            "ledger_count": len(manifest),
            "present_count": present_count,
            "missing_count": len(manifest) - present_count,
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        }
        _append_chain_row(chain_path, row)
        head_path = _write_head_file(cfg, settings, row, manifest)
        final_verification = verify_ledger_chain(cfg, write_summary=True)
        summary.update(
            {
                "status": "ok" if final_verification["status"] == "ok" else "error",
                "chain_head": row["chain_head"],
                "previous_chain_head": previous_head,
                "manifest_sha256": row["manifest_sha256"],
                "head_path": str(head_path),
                "ledger_count": len(manifest),
                "present_count": present_count,
                "missing_count": len(manifest) - present_count,
                "external_anchor_pending": True,
                "verification_status": final_verification["status"],
            }
        )
        write_json(summary_path, summary)
        return summary


def ledger_paths_for_archive(cfg: EngineConfig, *, as_of_date: str | None = None) -> list[Path]:
    """Return enrolled evidence files plus chain material for WO-65 archives."""

    settings = _settings(cfg)
    paths: dict[str, Path] = {}
    for row in read_csv_rows(_output_path(cfg, settings["chain_file"])):
        if as_of_date and str(row.get("anchor_date") or "") > as_of_date:
            continue
        for item in _parse_manifest(row):
            if item.get("status") != "present":
                continue
            relative = _safe_relative(item.get("verification_path") or item.get("path"))
            path = cfg.output_root / relative
            if path.is_file():
                paths[relative] = path
    for key in ("chain_file", "head_file", "summary_file", "verification_file"):
        path = _output_path(cfg, settings[key])
        if path.is_file():
            paths[path.resolve().relative_to(cfg.output_root.resolve()).as_posix()] = path
    # WO-127 (Codex #364 P1): the restore marker must travel WITH the tree. A
    # restore boundary is a fact about the chain's history, not about one
    # archive's exclusion set: once a restore has happened, the pre-boundary
    # rows under the excluded prefixes can never be byte-verified again. Leaving
    # the marker out meant an archive built on a restored host and then restored
    # elsewhere lost that history and read those rows as tampered - which broke
    # archive creation outright the moment an operator exercised the documented
    # tighten-only option of putting the corpus back into recovery.
    provenance = _output_path_relative(cfg, RESTORE_PROVENANCE_FILE)
    if provenance.is_file():
        paths[RESTORE_PROVENANCE_FILE] = provenance
    return [paths[key] for key in sorted(paths)]


def main(config_path: str) -> dict[str, Any]:
    return anchor_ledgers(load_config(config_path))
