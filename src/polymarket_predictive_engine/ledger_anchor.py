"""WO-61 tamper-evident anchoring for investor-facing evidence ledgers.

The chain stores a byte length and SHA-256 prefix digest for every enrolled
ledger on each UTC day.  A later append therefore leaves every prior anchor
verifiable, while a change to any previously anchored byte identifies the
first affected date.  This module is reporting infrastructure only: no gate,
sizing rule, policy, broker, or order path reads its outputs.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any

from .config import EngineConfig, load_config
from .runtime_lock import runtime_lock
from .utils import ensure_dir, now_utc, read_csv_rows, write_json


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
        "missing_excluded_tolerated": 0,
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
    tolerated_missing_prefixes: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Verify chain linkage and every historical byte prefix in row order.

    ``tolerated_missing_prefixes`` (WO-123) exists for ONE caller: the
    disaster-recovery restore check, where a registered set of regenerable
    corpora is deliberately excluded from the archive. Only ABSENCE of a
    matching path is tolerated, and only when the caller opts in; a present
    file whose anchored digest changed is still a broken chain, and any
    missing path outside the prefixes is still a broken chain. Default is
    empty, so every other caller's behaviour is byte-identical.
    """

    settings = _settings(cfg)
    tolerated = tuple(str(prefix) for prefix in tolerated_missing_prefixes if str(prefix))
    chain_path = _output_path(cfg, settings["chain_file"])
    verification_path = _output_path(cfg, settings["verification_file"])
    result = _verification_base(chain_path=chain_path, as_of_date=as_of_date)
    rows = read_csv_rows(chain_path)
    expected_previous = GENESIS_HEAD
    previous_date = ""
    for row in rows:
        anchor_date = str(row.get("anchor_date") or "")
        if as_of_date and anchor_date > as_of_date:
            continue
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
                if not anchored_path.is_file():
                    if tolerated and relative.startswith(tolerated):
                        # WO-123: excluded from the DR archive by registration
                        # and recoverable by re-harvest. Absence only - a
                        # PRESENT file with a changed digest still breaks below.
                        result["missing_excluded_tolerated"] += 1
                        continue
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
    return [paths[key] for key in sorted(paths)]


def main(config_path: str) -> dict[str, Any]:
    return anchor_ledgers(load_config(config_path))
