"""WO-65 size-capped snapshot and tested-restore controls."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest
import yaml

from polymarket_predictive_engine.cli import COMMANDS, main as cli_main
from polymarket_predictive_engine.config import EngineConfig, load_config
from polymarket_predictive_engine.disaster_recovery import (
    DisasterRecoveryError,
    create_ledger_archive,
    verify_and_restore_archive,
)
from polymarket_predictive_engine.ledger_anchor import anchor_ledgers, verify_ledger_chain
from polymarket_predictive_engine.utils import read_json, safe_float, write_json


ROOT = Path(__file__).resolve().parents[2]


def test_tracked_vps_config_meets_pre_live_rpo_after_wallet_configuration() -> None:
    raw = yaml.safe_load(
        (ROOT / "polymarket_predictive_config.example.yaml").read_text(encoding="utf-8")
    )

    assert raw["maker_live_test"]["wallet_address"]
    assert raw["disaster_recovery"]["active_rpo_hours"] == 24
    assert raw["disaster_recovery"]["active_rpo_hours"] <= raw["disaster_recovery"][
        "pre_live_max_rpo_hours"
    ]


# WO-150: distinguishes "the flag key is entirely absent from maker_live_test"
# (tests 1 and 7's sibling case) from "explicitly set to None" (test 7) - both
# read as False through `_live_capital_context`'s `is True` check, but the
# fixture needs to be able to produce either shape on request.
_UNSET = object()


def _config(
    tmp_path: Path,
    *,
    wallet: str = "",
    trading_mode: str = "paper",
    wallet_read_only_monitoring=_UNSET,
):
    raw = yaml.safe_load((ROOT / "polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    raw["maker_live_test"]["wallet_address"] = wallet
    # WO-150: the deployed example config declares ITS OWN address read-only
    # monitoring (wallet_address_read_only_monitoring: true). A test exercising
    # a different, synthetic wallet must not silently inherit that declaration,
    # so the flag defaults to fully ABSENT here unless a caller opts in.
    if wallet_read_only_monitoring is _UNSET:
        raw["maker_live_test"].pop("wallet_address_read_only_monitoring", None)
    else:
        raw["maker_live_test"]["wallet_address_read_only_monitoring"] = wallet_read_only_monitoring
    raw["trading"]["mode"] = trading_mode
    raw["ledger_anchor"] = {
        "enabled": True,
        "external_anchor_branch": "vps-anchor",
        "ledger_globs": [
            {"glob": "audit/core.csv", "mode": "append_only"},
            {"glob": "audit/policy.json", "mode": "snapshot"},
            {"glob": "performance/cost_ledger.csv", "mode": "append_only"},
        ],
    }
    raw["disaster_recovery"] = {
        "enabled": True,
        "archive_file": "performance/ledger_state_archive.tar.gz",
        "archive_manifest_file": "performance/ledger_state_archive_manifest.json",
        "status_file": "performance/disaster_recovery_status.json",
        "restore_status_file": "performance/restore_verification_status.json",
        "archive_branch": "vps-archive",
        "size_cap_mb": 1,
        "active_rpo_hours": 168,
        "paper_stage_max_rpo_hours": 168,
        "pre_live_max_rpo_hours": 24,
        "lock_stale_seconds": 60,
    }
    path = tmp_path / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def _seed_two_day_chain(cfg):
    core = cfg.output_root / "audit" / "core.csv"
    policy = cfg.output_root / "audit" / "policy.json"
    costs = cfg.output_root / "performance" / "cost_ledger.csv"
    core.parent.mkdir(parents=True, exist_ok=True)
    costs.parent.mkdir(parents=True, exist_ok=True)
    core.write_bytes(b"date,value\n2026-07-10,1\n")
    policy.write_text('{"decision":"wait"}\n', encoding="utf-8")
    costs.write_bytes(b"date,category,usd,cost_ref,note\n2026-07-10,rail,1,rail:1,funding\n")
    anchor_ledgers(cfg, anchor_date="2026-07-10")
    with core.open("ab") as handle:
        handle.write(b"2026-07-11,2\n")
    with costs.open("ab") as handle:
        handle.write(b"2026-07-11,subscription,2,sub:1,data\n")
    policy.write_text('{"decision":"proceed"}\n', encoding="utf-8")
    anchor_ledgers(cfg, anchor_date="2026-07-11")
    return core, policy, costs


def _restored_config(cfg, output_root: Path) -> EngineConfig:
    raw = deepcopy(cfg.raw)
    raw["paths"]["output_root"] = str(output_root)
    raw["paths"]["data_root"] = str(output_root.parent)
    return EngineConfig(raw=raw, path=cfg.path)


def test_archive_round_trip_restores_full_historical_chain_and_costs(tmp_path: Path):
    cfg = _config(tmp_path)
    _seed_two_day_chain(cfg)
    heavy = cfg.output_root / "polymarket_training" / "excluded.bin"
    heavy.parent.mkdir(parents=True)
    heavy.write_bytes(b"x" * (2 * 1024 * 1024))

    built = create_ledger_archive(cfg, force=True)
    archive_path = Path(built["archive_path"])
    dry_run = verify_and_restore_archive(cfg, archive_path, dry_run=True)
    restored_root = tmp_path / "restored_outputs"
    applied = verify_and_restore_archive(
        cfg,
        archive_path,
        dry_run=False,
        destination_output_root=restored_root,
    )
    restored_chain = verify_ledger_chain(
        _restored_config(cfg, restored_root),
        as_of_date="2026-07-11",
        write_summary=False,
    )

    with tarfile.open(archive_path, "r:gz") as archive:
        names = set(archive.getnames())
    assert built["status"] == "ok"
    assert built["snapshot_date"] == "2026-07-11"
    assert built["archive_size_bytes"] <= 1024 * 1024
    assert built["post_build_restore_verification"] == {
        "status": "ok",
        "verified_through_date": "2026-07-11",
        "restore_applied": False,
    }
    assert "archive_manifest.json" in names
    assert "outputs/performance/cost_ledger.csv" in names
    assert "outputs/performance/ledger_anchor_snapshots/2026-07-10/audit/policy.json" in names
    assert "outputs/performance/ledger_anchor_snapshots/2026-07-11/audit/policy.json" in names
    assert not any("polymarket_training" in name for name in names)
    assert dry_run["status"] == "ok"
    assert dry_run["restore_applied"] is False
    assert applied["restore_applied"] is True
    assert restored_chain["status"] == "ok"
    assert restored_chain["verified_through_date"] == "2026-07-11"
    assert (restored_root / "performance" / "cost_ledger.csv").read_bytes().endswith(
        b"2026-07-11,subscription,2,sub:1,data\n"
    )
    assert built["paper_trading_invoked"] is False
    assert built["live_trading_invoked"] is False


def test_corrupt_archive_fails_dry_run_stamps_status_and_cli_is_nonzero(tmp_path: Path, capsys):
    cfg = _config(tmp_path)
    _seed_two_day_chain(cfg)
    built = create_ledger_archive(cfg, force=True)
    source = Path(built["archive_path"]).read_bytes()
    corrupt = tmp_path / "corrupt.tar.gz"
    corrupt.write_bytes(source[: max(1, len(source) // 2)])

    with pytest.raises(DisasterRecoveryError):
        verify_and_restore_archive(cfg, corrupt, dry_run=True)
    status = read_json(cfg.output_root / "performance" / "restore_verification_status.json")
    assert status["status"] == "error"
    assert status["failure_stamped"] is True

    code = cli_main(
        [
            "verify-ledger-archive",
            "--config",
            str(cfg.path),
            "--archive-path",
            str(corrupt),
        ]
    )
    error = capsys.readouterr().err
    assert code == 2
    assert "ERROR:" in error


def test_pre_live_rpo_tightening_is_enforced_fail_closed(tmp_path: Path):
    cfg = _config(tmp_path, wallet="0x" + "a" * 40)
    _seed_two_day_chain(cfg)

    with pytest.raises(DisasterRecoveryError, match="24h maximum"):
        create_ledger_archive(cfg, force=True)
    blocked = read_json(cfg.output_root / "performance" / "disaster_recovery_status.json")
    assert blocked["status"] == "error"
    assert "tighten disaster_recovery.active_rpo_hours" in blocked["error"]

    cfg.raw["disaster_recovery"]["active_rpo_hours"] = 24
    allowed = create_ledger_archive(cfg, force=True)
    assert allowed["status"] == "ok"
    assert allowed["rpo"]["live_capital_context"] is True
    assert allowed["rpo"]["active_rpo_hours"] == 24


def test_config_overrides_cannot_widen_registered_size_or_rpo_caps(tmp_path: Path):
    cfg = _config(tmp_path)
    _seed_two_day_chain(cfg)
    cfg.raw["disaster_recovery"].update(
        {
            "size_cap_mb": 500,
            "paper_stage_max_rpo_hours": 999,
            "pre_live_max_rpo_hours": 99,
            "active_rpo_hours": 169,
        }
    )

    with pytest.raises(DisasterRecoveryError, match="168h maximum"):
        create_ledger_archive(cfg, force=True)

    cfg.raw["disaster_recovery"]["active_rpo_hours"] = 168
    result = create_ledger_archive(cfg, force=True)
    assert result["size_cap_mb"] == 240
    assert result["rpo"]["paper_stage_max_rpo_hours"] == 168
    assert result["rpo"]["pre_live_max_rpo_hours"] == 24


def test_wo122b_registered_archive_ceiling_is_240mb_across_every_enforcement_point() -> None:
    # 2026-07-26 owner amendment: 50MB -> 240MB, because the append-only WO-61
    # ledger set outgrew 50MB on 2026-07-16 and killed disaster recovery for ten
    # days. The cap is enforced in FOUR independent places; if they drift, one
    # of them silently becomes the real limit.
    from polymarket_predictive_engine.disaster_recovery import _settings

    expected_mb = 240
    expected_bytes = expected_mb * 1024 * 1024

    # 1. code default / registered ceiling
    default_cfg = load_config(ROOT / "polymarket_predictive_config.example.yaml")
    assert _settings(default_cfg)["size_cap_mb"] == float(expected_mb)
    # 2. still tighten-only: config cannot widen past the registered ceiling
    default_cfg.raw["disaster_recovery"]["size_cap_mb"] = 10_000
    assert _settings(default_cfg)["size_cap_mb"] == float(expected_mb)
    # 3. deployed config
    raw = yaml.safe_load((ROOT / "polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    assert raw["disaster_recovery"]["size_cap_mb"] == expected_mb
    # 4. the shell push's independent hard remote cap
    push = (ROOT / "scripts" / "push_vps_archive.sh").read_text(encoding="utf-8")
    assert f"MAX_ARCHIVE_BYTES={expected_bytes}" in push
    assert f"{expected_mb}MB remote cap" in push
    # operator documentation must not still promise the old ceiling
    assert f"{expected_mb}MB" in (ROOT / "docs" / "RESTORE.md").read_text(encoding="utf-8")


def test_wo122_oversized_expanded_ledger_set_still_fails_closed(tmp_path: Path):
    # Production 2026-07-16..26: DR died because the WO-61 ledger set outgrew
    # the 50MB cap, which is enforced on the COMPRESSED archive, on the
    # UNCOMPRESSED source, AND on the EXPANDED content during the post-build
    # restore verification (a decompression-bomb guard). The expanded guard is
    # the binding one and is deliberately preserved: growing past the cap must
    # keep refusing, loudly, rather than silently shipping an unrestorable
    # archive. Restoring DR needs an owner decision on archive scope or cap,
    # not a quiet relaxation here.
    cfg = _config(tmp_path)
    cap_bytes = int(float(cfg.raw["disaster_recovery"]["size_cap_mb"]) * 1024 * 1024)
    core, _policy, _costs = _seed_two_day_chain(cfg)
    with core.open("ab") as handle:
        for _ in range(200_000):
            handle.write(b"2026-07-11,00000000000000000000000000000000000000000000000000\n")
    anchor_ledgers(cfg, anchor_date="2026-07-12")
    assert core.stat().st_size > cap_bytes

    with pytest.raises(DisasterRecoveryError, match="size cap"):
        create_ledger_archive(cfg, force=True)

    # The failure is stamped for telemetry rather than swallowed.
    status = read_json(cfg.output_root / "performance" / "disaster_recovery_status.json")
    assert status["status"] == "error"
    assert "size cap" in status["error"]
    # ...and the honest RPO fields make the dead-backup state visible.
    assert status["rpo"]["compliant"] is False


def test_wo122_source_ceiling_is_separate_from_the_registered_artifact_cap(tmp_path: Path):
    # The runtime source ceiling bounds memory/runtime, not the published
    # artifact, so it is separately configurable - and still fails closed.
    cfg = _config(tmp_path)
    cfg.raw["disaster_recovery"]["source_cap_mb"] = 1
    core, _policy, _costs = _seed_two_day_chain(cfg)
    with core.open("ab") as handle:
        handle.write(b"2026-07-12," + b"x" * (2 * 1024 * 1024) + b"\n")
    anchor_ledgers(cfg, anchor_date="2026-07-12")

    with pytest.raises(DisasterRecoveryError, match="runtime ceiling"):
        create_ledger_archive(cfg, force=True)


def test_wo122_rpo_compliance_reflects_observed_archive_age(tmp_path: Path):
    # `compliant` was hardcoded True, so the status file published
    # rpo.compliant=true beside a 233-hour archive age against a 24h RPO while
    # the builder had been failing for ten days.
    cfg = _config(tmp_path)
    _seed_two_day_chain(cfg)
    status_path = cfg.output_root / "performance" / "disaster_recovery_status.json"

    first = create_ledger_archive(cfg, force=True)
    # Never-archived / unknown age fails closed rather than claiming compliance.
    assert first["rpo"]["observed_within_rpo"] is False
    assert first["rpo"]["compliant"] is False
    assert first["rpo"]["configured_rpo_within_registered_ceiling"] is True

    status = read_json(status_path)
    status.update(
        {
            "remote_push_status": "ok",
            "last_remote_success_at_utc": status["generated_at_utc"],
            "last_remote_snapshot_date": status["snapshot_date"],
        }
    )
    write_json(status_path, status)
    fresh = create_ledger_archive(cfg)
    assert fresh["rpo"]["compliant"] is True  # a genuinely fresh archive

    stale_stamp = (datetime.now(timezone.utc) - timedelta(hours=233)).strftime("%Y-%m-%dT%H:%M:%SZ")
    status = read_json(status_path)
    status["last_remote_success_at_utc"] = stale_stamp
    write_json(status_path, status)
    stale = create_ledger_archive(cfg, force=True)

    assert stale["last_remote_archive_age_hours"] > 168
    assert stale["rpo"]["observed_within_rpo"] is False
    assert stale["rpo"]["compliant"] is False


def test_wo122_archive_script_reports_unsupported_host_interpreter():
    push = (ROOT / "scripts" / "push_vps_archive.sh").read_text(encoding="utf-8")
    # The engine declares requires-python >=3.10; on a 3.9 host the CLI import
    # dies with an opaque TypeError, which is what "local Python paths were
    # exhausted" actually meant in production.
    assert "sys.version_info >= (3, 10)" in push
    assert "requires-python" in push
    assert "no local fallback exists" in push
    assert 'BUILDER_STATE" -eq 2' in push
    assert ">=3.10" in (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def _enroll_training_corpus(cfg, *, size_bytes: int = 2 * 1024 * 1024) -> Path:
    """Enroll a heavy training corpus in the WO-61 chain, as production does.

    On 2026-07-26 this corpus was 476.6MB of a 505.6MB enrolled set - the reason
    the archive could not be built at all.
    """

    cfg.raw["ledger_anchor"]["ledger_globs"].append(
        {"glob": "polymarket_training/*.csv", "mode": "append_only"}
    )
    corpus = cfg.output_root / "polymarket_training" / "historical_bid_ask_v1.csv"
    corpus.parent.mkdir(parents=True, exist_ok=True)
    body = b"1784000000,0.41,0.59\n"
    corpus.write_bytes(b"ts,bid,ask\n" + body * (size_bytes // len(body)))
    return corpus


def _rebuild_archive_with_manifest(
    source: Path,
    destination: Path,
    *,
    manifest_updates: dict,
    drop_paths: tuple[str, ...] = (),
    add_paths: dict[str, bytes] | None = None,
) -> Path:
    """Repack an archive with a mutated manifest, keeping it internally valid.

    This is the tampered/legacy-archive attacker's position: the digests and file
    set still agree with the manifest, only the declared scope differs.
    """

    members: dict[str, bytes] = {}
    with tarfile.open(source, "r:gz") as archive:
        for member in archive.getmembers():
            handle = archive.extractfile(member)
            assert handle is not None
            members[member.name] = handle.read()
    manifest = json.loads(members.pop("archive_manifest.json").decode("utf-8"))
    manifest.update(manifest_updates)
    if drop_paths:
        manifest["files"] = [row for row in manifest["files"] if row["path"] not in drop_paths]
        for name in drop_paths:
            members.pop(name, None)
    for name, data in (add_paths or {}).items():
        members[name] = data
        manifest["files"] = [
            *[row for row in manifest["files"] if row["path"] != name],
            {
                "path": name,
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            },
        ]
    payload = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with tarfile.open(destination, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for name, data in (("archive_manifest.json", payload), *sorted(members.items())):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(data))
    return destination


def test_wo123_anchored_corpus_is_excluded_from_the_archive_and_restore_still_verifies(
    tmp_path: Path,
):
    # 2026-07-26 owner decision: exclude the re-harvestable corpora from the
    # recovery archive, KEEP them in the anchor chain. Restore verification then
    # has to report them as excluded-by-design rather than a broken chain.
    cfg = _config(tmp_path)
    corpus = _enroll_training_corpus(cfg)
    _seed_two_day_chain(cfg)

    built = create_ledger_archive(cfg, force=True)
    archive_path = Path(built["archive_path"])
    manifest = read_json(cfg.output_root / "performance" / "ledger_state_archive_manifest.json")
    with tarfile.open(archive_path, "r:gz") as archive:
        names = set(archive.getnames())

    # The corpus stays ANCHORED: it is in the chain manifests and the live chain
    # verifies it byte-for-byte, with no tolerance applied off the DR path.
    chain_rows = (cfg.output_root / "performance" / "ledger_anchor_chain.csv").read_text(
        encoding="utf-8"
    )
    assert "polymarket_training/historical_bid_ask_v1.csv" in chain_rows
    live_chain = verify_ledger_chain(cfg, as_of_date="2026-07-11", write_summary=False)
    assert live_chain["status"] == "ok"
    # WO-127: with no restore-provenance marker in the live tree, nothing is
    # excused - the corpus is present and byte-verified like any other ledger.
    assert live_chain["restored_unverifiable_tolerated"] == 0
    assert live_chain["restore_boundary_date"] is None

    # ...but it never enters the archive.
    assert built["status"] == "ok"
    assert not any("polymarket_training" in name for name in names)
    assert built["archive_excluded_paths"] == ["polymarket_training/"]
    assert built["archive_excluded_file_count"] == 1
    assert built["archive_excluded_bytes"] == corpus.stat().st_size
    assert manifest["excluded_path_prefixes"] == ["polymarket_training/"]
    assert manifest["excluded_files"] == [
        {
            "path": "outputs/polymarket_training/historical_bid_ask_v1.csv",
            "size_bytes": corpus.stat().st_size,
        }
    ]
    assert "regenerable by re-harvest" in manifest["excluded_reason"]
    assert manifest["uncompressed_bytes"] < corpus.stat().st_size

    # Restore verification passes and says exactly what it tolerated.
    dry_run = verify_and_restore_archive(cfg, archive_path, dry_run=True)
    assert dry_run["status"] == "ok"
    assert dry_run["excluded_path_prefixes_tolerated"] == ["polymarket_training/"]
    assert dry_run["restored_unverifiable_tolerated"] >= 1
    assert dry_run["restored_without_prefixes"] == ["polymarket_training/"]
    assert dry_run["restore_boundary_date"] == "2026-07-11"
    assert dry_run["ledger_chain_verification"]["verified_through_date"] == "2026-07-11"


def test_wo123_without_the_exclusion_the_same_ledger_set_still_fails_closed(tmp_path: Path):
    # Proves the exclusion is what unblocks DR, and that the size guards were
    # never relaxed to get there: put the corpus back in and the build still
    # refuses, loudly, exactly as it did for the ten dead days.
    cfg = _config(tmp_path)
    _enroll_training_corpus(cfg)
    _seed_two_day_chain(cfg)
    cfg.raw["disaster_recovery"]["excluded_path_prefixes"] = []

    with pytest.raises(DisasterRecoveryError, match="size cap"):
        create_ledger_archive(cfg, force=True)
    status = read_json(cfg.output_root / "performance" / "disaster_recovery_status.json")
    assert status["status"] == "error"
    assert status["archive_excluded_paths"] == []


def test_wo123_config_may_only_shrink_the_registered_exclusion_set() -> None:
    from polymarket_predictive_engine.disaster_recovery import (
        ARCHIVE_EXCLUDED_PREFIXES,
        _excluded_prefixes,
    )

    assert ARCHIVE_EXCLUDED_PREFIXES == ("polymarket_training/",)
    # Unset keeps the registration.
    assert _excluded_prefixes({}) == ARCHIVE_EXCLUDED_PREFIXES
    # Trailing-slash and leading-slash hygiene, so a prefix cannot match a sibling.
    assert _excluded_prefixes({"excluded_path_prefixes": ["polymarket_training"]}) == (
        "polymarket_training/",
    )
    # Shrinking is allowed: fewer exclusions means MORE recovery coverage.
    assert _excluded_prefixes({"excluded_path_prefixes": []}) == ()
    # Adding is not: config cannot quietly drop a ledger out of disaster recovery.
    assert _excluded_prefixes(
        {"excluded_path_prefixes": ["audit/", "performance/", "polymarket_training/"]}
    ) == ("polymarket_training/",)
    with pytest.raises(ValueError, match="list of path prefixes"):
        _excluded_prefixes({"excluded_path_prefixes": 7})


def test_wo123_archive_cannot_widen_its_own_restore_tolerance(tmp_path: Path):
    # The manifest is untrusted input. An archive that declares an unregistered
    # prefix and omits a real ledger must still fail the restore check, and a
    # pre-WO-123 archive (no declaration) gets no tolerance at all.
    cfg = _config(tmp_path)
    _enroll_training_corpus(cfg)
    _seed_two_day_chain(cfg)
    built = create_ledger_archive(cfg, force=True)
    source = Path(built["archive_path"])

    widened = _rebuild_archive_with_manifest(
        source,
        tmp_path / "widened.tar.gz",
        manifest_updates={"excluded_path_prefixes": ["polymarket_training/", "audit/"]},
        drop_paths=("outputs/audit/core.csv",),
    )
    with pytest.raises(DisasterRecoveryError, match="did not verify through"):
        verify_and_restore_archive(cfg, widened, dry_run=True)

    legacy = _rebuild_archive_with_manifest(
        source,
        tmp_path / "legacy.tar.gz",
        manifest_updates={"excluded_path_prefixes": []},
    )
    with pytest.raises(DisasterRecoveryError, match="did not verify through"):
        verify_and_restore_archive(cfg, legacy, dry_run=True)


def test_remote_success_controls_due_state_without_rebuilding(tmp_path: Path):
    cfg = _config(tmp_path)
    _seed_two_day_chain(cfg)
    first = create_ledger_archive(cfg, force=True)
    archive = Path(first["archive_path"])
    original = archive.read_bytes()
    status_path = cfg.output_root / "performance" / "disaster_recovery_status.json"
    status = read_json(status_path)
    status.update(
        {
            "remote_push_status": "ok",
            "last_remote_success_at_utc": status["generated_at_utc"],
            "last_remote_snapshot_date": status["snapshot_date"],
        }
    )
    write_json(status_path, status)

    second = create_ledger_archive(cfg)

    assert second["status"] == "not_due"
    assert second["last_remote_archive_age_hours"] < 168
    assert archive.read_bytes() == original


def test_scripts_are_bounded_single_commit_and_telemetry_visible():
    push = (ROOT / "scripts" / "push_vps_archive.sh").read_text(encoding="utf-8")
    telemetry = (ROOT / "scripts" / "push_vps_telemetry.sh").read_text(encoding="utf-8")
    restore = (ROOT / "scripts" / "restore_from_archive.sh").read_text(encoding="utf-8")

    # 2026-07-26 owner amendment: 50MB -> 240MB (see the dedicated
    # four-enforcement-point drift test above).
    assert "MAX_ARCHIVE_BYTES=251658240" in push
    assert 'commit-tree -m "VPS ledger archive' in push
    assert '"+$COMMIT:refs/heads/$BRANCH"' in push
    assert "commit-tree -p" not in push
    assert "polymarket_training" not in push
    assert 'VPS_ARCHIVE_REPO_DIR="$REPO_DIR" sh "$ARCHIVE_SCRIPT"' in telemetry
    assert "disaster_recovery_status.json" in push
    assert "--dry-run" in restore
    assert "verify-ledger-archive" in restore
    assert "snapshot-ledger-archive" in COMMANDS
    assert "verify-ledger-archive" in COMMANDS


@pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX sh is not available on this host")
def test_restore_shell_dry_run_passes_valid_and_exits_nonzero_on_corrupt_archive(tmp_path: Path):
    cfg = _config(tmp_path)
    _seed_two_day_chain(cfg)
    built = create_ledger_archive(cfg, force=True)
    source = Path(built["archive_path"]).read_bytes()
    corrupt = tmp_path / "corrupt-for-shell.tar.gz"
    corrupt.write_bytes(source[: max(1, len(source) // 3)])
    env = dict(os.environ)
    env["VPS_ARCHIVE_PYTHON"] = sys.executable
    env["PYTHONPATH"] = str(ROOT / "src")

    common = [
        "sh",
        str(ROOT / "scripts" / "restore_from_archive.sh"),
        "--dry-run",
        "--repo-dir",
        str(ROOT),
        "--config",
        str(cfg.path),
        "--archive",
    ]
    valid = subprocess.run(
        [*common, str(built["archive_path"])],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert valid.returncode == 0, valid.stderr
    assert '"status": "ok"' in valid.stdout

    result = subprocess.run(
        [
            *common,
            str(corrupt),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "ERROR:" in result.stderr


# --- WO-127: a restore must not wedge the anchor lane ---


def test_wo127_restore_then_anchor_run_verifies_instead_of_wedging(tmp_path: Path):
    # THE regression. On main, verify_and_restore_archive passed
    # tolerated_missing_prefixes but anchor_ledgers called verify_ledger_chain with
    # none, so a restore reported success and the very next production anchor run
    # read the excluded corpora as "anchored file is missing" -> blocked_broken_chain
    # -> head frozen, exit 1. Recovery handed over a tree that broke the tamper lane.
    from polymarket_predictive_engine.ledger_anchor import RESTORE_PROVENANCE_FILE

    cfg = _config(tmp_path)
    _enroll_training_corpus(cfg)
    _seed_two_day_chain(cfg)
    built = create_ledger_archive(cfg, force=True)

    restored_root = tmp_path / "restored_outputs"
    applied = verify_and_restore_archive(
        cfg, Path(built["archive_path"]), dry_run=False, destination_output_root=restored_root
    )
    assert applied["restore_applied"] is True
    assert applied["restored_without_prefixes"] == ["polymarket_training/"]

    restored_cfg = _restored_config(cfg, restored_root)
    # The corpus is genuinely absent from the restored tree...
    assert not (restored_root / "polymarket_training").exists()
    # ...and the provenance marker travelled with it, so the PRODUCTION anchor
    # lane - which passes no tolerance of its own - verifies clean.
    assert (restored_root / "performance" / "ledger_restore_provenance.json").is_file()
    assert RESTORE_PROVENANCE_FILE == "performance/ledger_restore_provenance.json"

    verification = verify_ledger_chain(restored_cfg, write_summary=False)
    assert verification["status"] == "ok"
    assert verification["restored_unverifiable_tolerated"] >= 1
    assert verification["restore_boundary_date"] == "2026-07-11"

    anchored = anchor_ledgers(restored_cfg, anchor_date="2026-07-12")
    assert anchored["status"] == "ok", anchored
    assert verify_ledger_chain(restored_cfg, write_summary=False)["status"] == "ok"


def test_wo127_reharvest_after_restore_does_not_wedge_the_chain(tmp_path: Path):
    # The permanent-wedge case the audit did not reach: absence-only tolerance
    # would excuse the missing corpus, but a re-harvest brings the file back with
    # DIFFERENT bytes, flipping the same historical rows to "anchored prefix digest
    # changed" - which absence tolerance cannot excuse. Those bytes are
    # unreproducible, so pre-boundary entries are unverifiable by design.
    cfg = _config(tmp_path)
    _enroll_training_corpus(cfg)
    _seed_two_day_chain(cfg)
    built = create_ledger_archive(cfg, force=True)
    restored_root = tmp_path / "restored_outputs"
    verify_and_restore_archive(
        cfg, Path(built["archive_path"]), dry_run=False, destination_output_root=restored_root
    )
    restored_cfg = _restored_config(cfg, restored_root)

    reharvested = restored_root / "polymarket_training" / "historical_bid_ask_v1.csv"
    reharvested.parent.mkdir(parents=True, exist_ok=True)
    reharvested.write_bytes(b"ts,bid,ask\n1784999999,0.77,0.23\n")

    verification = verify_ledger_chain(restored_cfg, write_summary=False)
    assert verification["status"] == "ok", verification["issues"]
    assert anchor_ledgers(restored_cfg, anchor_date="2026-07-12")["status"] == "ok"

    # A non-excluded restored ledger is still byte-verified. Flip a byte in place
    # so the failure is a DIGEST change rather than a short-file read error.
    core = restored_root / "audit" / "core.csv"
    tampered_bytes = bytearray(core.read_bytes())
    tampered_bytes[0] = ord("X")
    core.write_bytes(bytes(tampered_bytes))
    broken = verify_ledger_chain(restored_cfg, write_summary=False)
    assert broken["status"] == "broken"
    assert "anchored prefix digest changed" in broken["issues"][0]


def test_wo127_narrowing_exclusions_on_a_restored_host_still_builds(tmp_path: Path):
    # Codex review of #364 (P1). RESTORE.md documents narrowing
    # excluded_path_prefixes as a tighten-only operation that puts the corpus back
    # into recovery. On a RESTORED host that permanently broke archive creation:
    # the new archive declared no exclusions, so the post-build restore check wrote
    # no marker into the extracted tree, and the pre-restore rows were compared
    # against re-harvested bytes and rejected - every build, forever. A restore
    # boundary is a fact about the chain's history, so it must travel with the tree.
    cfg = _config(tmp_path)
    _enroll_training_corpus(cfg, size_bytes=4096)
    _seed_two_day_chain(cfg)
    built = create_ledger_archive(cfg, force=True)
    restored_root = tmp_path / "restored_outputs"
    verify_and_restore_archive(
        cfg, Path(built["archive_path"]), dry_run=False, destination_output_root=restored_root
    )
    restored_cfg = _restored_config(cfg, restored_root)
    marker = restored_root / "performance" / "ledger_restore_provenance.json"
    assert marker.is_file(), "the applied restore must record its boundary"
    inherited_boundary = read_json(marker)["restore_boundary_date"]

    # Re-harvest with different bytes, exactly as normal collection would.
    reharvested = restored_root / "polymarket_training" / "historical_bid_ask_v1.csv"
    reharvested.parent.mkdir(parents=True, exist_ok=True)
    reharvested.write_bytes(b"ts,bid,ask\n1784999999,0.77,0.23\n")
    assert anchor_ledgers(restored_cfg, anchor_date="2026-07-12")["status"] == "ok"

    # The documented tighten-only narrowing: put the corpus back into recovery.
    restored_cfg.raw["disaster_recovery"]["excluded_path_prefixes"] = []
    rebuilt = create_ledger_archive(restored_cfg, force=True)

    assert rebuilt["status"] == "ok"
    assert rebuilt["archive_excluded_paths"] == []
    # The corpus WAS re-harvested before this rebuild, so coverage is complete.
    assert rebuilt["archive_coverage_complete"] is True
    assert rebuilt["archive_pending_reharvest_paths"] == []
    assert rebuilt["post_build_restore_verification"]["status"] == "ok"
    # The marker travelled into the archive, so a restore of THIS archive also
    # knows the pre-boundary rows are unverifiable by design.
    with tarfile.open(Path(rebuilt["archive_path"]), "r:gz") as archive:
        names = set(archive.getnames())
    assert "outputs/performance/ledger_restore_provenance.json" in names
    assert "outputs/polymarket_training/historical_bid_ask_v1.csv" in names

    second_root = tmp_path / "restored_twice"
    applied = verify_and_restore_archive(
        cfg,
        Path(rebuilt["archive_path"]),
        dry_run=False,
        destination_output_root=second_root,
    )
    assert applied["status"] == "ok"
    carried = read_json(second_root / "performance" / "ledger_restore_provenance.json")
    assert carried["restore_boundary_date"] == inherited_boundary
    # Codex review of #364 (P2): the report must name the boundary actually
    # honoured, not this archive's snapshot date, or the operator is told the wrong
    # waiver scope. The archive's own snapshot date is 2026-07-12 here.
    assert applied["restore_boundary_date"] == inherited_boundary
    assert applied["restore_boundary_date"] != rebuilt["snapshot_date"]
    assert applied["restore_provenance_rejected"] is None
    # The boundary and the head it names travel as a pair, so the inherited
    # boundary keeps the inherited head - otherwise the marker would refuse itself.
    assert carried["restored_from_chain_head"] == read_json(marker)["restored_from_chain_head"]
    assert verify_ledger_chain(
        _restored_config(cfg, second_root), write_summary=False
    )["status"] == "ok"


def test_wo127_narrowing_before_reharvest_refuses_instead_of_publishing_a_gap(tmp_path: Path):
    # Codex review of #364, two P2s with one root. Narrowing the exclusion set on a
    # restored host before re-harvest recreates the corpus produced an archive
    # reporting NO exclusions while not containing the corpus - an absent file is
    # invisible to the builder. Reporting that was not enough: push_vps_archive.sh
    # gates only on `status`, so the incomplete archive would still force-replace the
    # SOLE remote snapshot and stamp the RPO as met. A diagnostic no consumer reads
    # is the fail-silent class this batch exists to remove, so the build REFUSES -
    # before the archive file is written, and stamped where the DR watchdog reads it.
    cfg = _config(tmp_path)
    _enroll_training_corpus(cfg, size_bytes=4096)
    _seed_two_day_chain(cfg)
    built = create_ledger_archive(cfg, force=True)
    restored_root = tmp_path / "restored_outputs"
    verify_and_restore_archive(
        cfg, Path(built["archive_path"]), dry_run=False, destination_output_root=restored_root
    )
    restored_cfg = _restored_config(cfg, restored_root)
    archive_path = restored_root / "performance" / "ledger_state_archive.tar.gz"
    assert not archive_path.exists()

    # Narrow the exclusions WITHOUT re-harvesting: the corpus is still absent.
    restored_cfg.raw["disaster_recovery"]["excluded_path_prefixes"] = []
    with pytest.raises(DisasterRecoveryError, match="would claim coverage it does not have"):
        create_ledger_archive(restored_cfg, force=True)

    status = read_json(restored_root / "performance" / "disaster_recovery_status.json")
    assert status["status"] == "error"
    assert status["failure_stamped"] is True
    assert status["archive_coverage_complete"] is False
    assert status["archive_pending_reharvest_paths"] == [
        "polymarket_training/historical_bid_ask_v1.csv"
    ]
    # No incomplete archive exists for the publisher to force-push as the snapshot.
    assert not archive_path.exists()

    # Re-harvest, and the same narrowing succeeds with complete coverage.
    reharvested = restored_root / "polymarket_training" / "historical_bid_ask_v1.csv"
    reharvested.parent.mkdir(parents=True, exist_ok=True)
    reharvested.write_bytes(b"ts,bid,ask\n1784999999,0.77,0.23\n")
    assert anchor_ledgers(restored_cfg, anchor_date="2026-07-12")["status"] == "ok"
    rebuilt = create_ledger_archive(restored_cfg, force=True)
    assert rebuilt["status"] == "ok"
    assert rebuilt["archive_coverage_complete"] is True


def test_wo127_the_archive_after_a_restore_still_builds(tmp_path: Path):
    # THE regression this work order exists to prevent, one layer up. Codex review of
    # #364 (P1) caught that the coverage refusal read "is this path excluded by scope"
    # from the list of EXISTING files the builder skipped - a list an absent path can
    # never appear in. Immediately after a restore every anchored corpus path is
    # absent by design while the configured prefix still declares it excluded, so the
    # very next scheduled archive was refused, wedging the recurring recovery lane
    # until collection recreated the corpus. A restore must not wedge the lane that
    # produced it, which is the whole point of WO-127.
    cfg = _config(tmp_path)
    _enroll_training_corpus(cfg, size_bytes=4096)
    _seed_two_day_chain(cfg)
    built = create_ledger_archive(cfg, force=True)
    restored_root = tmp_path / "restored_outputs"
    verify_and_restore_archive(
        cfg, Path(built["archive_path"]), dry_run=False, destination_output_root=restored_root
    )
    restored_cfg = _restored_config(cfg, restored_root)
    assert not (restored_root / "polymarket_training").exists()

    # Default (registered) exclusions still in force: the absent corpus is excluded
    # by scope, not a coverage gap.
    rebuilt = create_ledger_archive(restored_cfg, force=True)

    assert rebuilt["status"] == "ok"
    assert rebuilt["archive_pending_reharvest_paths"] == []
    assert rebuilt["archive_coverage_complete"] is True
    assert rebuilt["archive_excluded_paths"] == ["polymarket_training/"]


def test_wo127_relabelled_archive_cannot_borrow_an_inherited_waiver(tmp_path: Path):
    # Codex review of #364 (P2), the shape my earlier test missed: an archive built
    # while the prefix was EXCLUDED carries the inherited marker and no corpus, so
    # relabelling its manifest to declare no exclusions is self-consistent - the file
    # set already matches. The inherited prefix was then imported by the union,
    # historical rows waived, later missing_at_anchor rows tolerated by design, and it
    # restored ok while claiming full coverage. An archive that does not declare a
    # prefix excluded is asserting coverage of it.
    cfg = _config(tmp_path)
    corpus = _enroll_training_corpus(cfg, size_bytes=4096)
    _seed_two_day_chain(cfg)
    relative = corpus.relative_to(cfg.output_root).as_posix()
    built = create_ledger_archive(cfg, force=True)
    restored_root = tmp_path / "restored_outputs"
    verify_and_restore_archive(
        cfg, Path(built["archive_path"]), dry_run=False, destination_output_root=restored_root
    )
    restored_cfg = _restored_config(cfg, restored_root)

    # Honest archive on the restored host: prefix still excluded, corpus absent.
    honest = create_ledger_archive(restored_cfg, force=True)
    assert honest["archive_excluded_paths"] == ["polymarket_training/"]
    with tarfile.open(Path(honest["archive_path"]), "r:gz") as archive:
        assert f"outputs/{relative}" not in set(archive.getnames())
    assert verify_and_restore_archive(cfg, Path(honest["archive_path"]), dry_run=True)["status"] == "ok"

    # Relabel only the declaration. The file set is untouched and still self-consistent.
    # Codex review of #364 (P2): [""] and whitespace are the interesting forgeries -
    # every path satisfies startswith(""), so an unnormalised declaration became a
    # wildcard that "declared" a prefix the archive never named, while the inherited
    # marker still supplied the registered waiver. A prefix without its trailing slash
    # normalises to a valid declaration and must still be honoured.
    # ("audit/" is deliberately absent: that shape is refused earlier, by the
    # declares-and-includes contradiction check, which is a different guard.)
    for index, forged_declaration in enumerate(([], [""], ["   "], ["/"], ["../"])):
        relabelled = _rebuild_archive_with_manifest(
            Path(honest["archive_path"]),
            tmp_path / f"relabelled-{index}.tar.gz",
            manifest_updates={
                "excluded_path_prefixes": forged_declaration,
                "excluded_files": [],
                "excluded_file_count": 0,
            },
        )
        with pytest.raises(DisasterRecoveryError, match="does not declare excluded"):
            verify_and_restore_archive(cfg, relabelled, dry_run=True)

    # An honest declaration missing only its trailing slash still restores.
    tolerant = _rebuild_archive_with_manifest(
        Path(honest["archive_path"]),
        tmp_path / "unslashed.tar.gz",
        manifest_updates={"excluded_path_prefixes": ["polymarket_training"]},
    )
    assert verify_and_restore_archive(cfg, tolerant, dry_run=True)["status"] == "ok"


def test_wo127_present_but_unanchored_corpus_cannot_enter_the_archive(tmp_path: Path):
    # Codex review of #364 (P1). The absent-corpus refusal is not the only way a
    # narrowed archive can lie. When the restore and the re-harvest happen on the
    # archive's own snapshot day, anchor_ledgers returns already_anchored and writes
    # NO new row - so the corpus is present, passes the coverage check, and yet every
    # chain row naming it predates the boundary and is waived by the marker. Those
    # bytes would enter the recovery archive having never resumed tamper coverage:
    # attacker-chosen content with a self-consistent manifest would be accepted.
    cfg = _config(tmp_path)
    corpus = _enroll_training_corpus(cfg, size_bytes=4096)
    _seed_two_day_chain(cfg)
    relative = corpus.relative_to(cfg.output_root).as_posix()
    built = create_ledger_archive(cfg, force=True)
    restored_root = tmp_path / "restored_outputs"
    verify_and_restore_archive(
        cfg, Path(built["archive_path"]), dry_run=False, destination_output_root=restored_root
    )
    restored_cfg = _restored_config(cfg, restored_root)
    boundary = read_json(restored_root / "performance" / "ledger_restore_provenance.json")[
        "restore_boundary_date"
    ]

    # Re-harvest on the SAME UTC day as the boundary: no new anchor row exists.
    reharvested = restored_root / relative
    reharvested.parent.mkdir(parents=True, exist_ok=True)
    reharvested.write_bytes(b"ts,bid,ask\n1784999999,0.77,0.23\n")
    same_day = anchor_ledgers(restored_cfg, anchor_date=boundary)
    assert same_day["status"] == "already_anchored"

    restored_cfg.raw["disaster_recovery"]["excluded_path_prefixes"] = []
    with pytest.raises(DisasterRecoveryError, match="bytes no anchor attests"):
        create_ledger_archive(restored_cfg, force=True)

    status = read_json(restored_root / "performance" / "disaster_recovery_status.json")
    assert status["status"] == "error"
    assert status["archive_coverage_complete"] is False
    assert status["archive_unanchored_since_restore_paths"] == [relative]
    assert status["archive_pending_reharvest_paths"] == []

    # Once the daily lane anchors those bytes on a later UTC day, the same narrowing
    # is accepted - the corpus has resumed tamper coverage.
    assert anchor_ledgers(restored_cfg, anchor_date="2026-07-12")["status"] == "ok"
    rebuilt = create_ledger_archive(restored_cfg, force=True)
    assert rebuilt["status"] == "ok"
    assert rebuilt["archive_coverage_complete"] is True
    assert rebuilt["archive_unanchored_since_restore_paths"] == []


def test_wo127_archive_omitting_an_undeclared_corpus_is_refused_on_restore(tmp_path: Path):
    # Codex review of #364 (P2), the untrusted-input half: the inherited marker grants
    # absence tolerance for the registered prefixes even when THIS archive declares no
    # exclusions, so a self-consistent forged archive could omit the corpus, claim full
    # scope, and restore ok. The waiver covers only what predates the restore.
    cfg = _config(tmp_path)
    corpus = _enroll_training_corpus(cfg, size_bytes=4096)
    _seed_two_day_chain(cfg)
    relative = corpus.relative_to(cfg.output_root).as_posix()
    built = create_ledger_archive(cfg, force=True)
    restored_root = tmp_path / "restored_outputs"
    verify_and_restore_archive(
        cfg, Path(built["archive_path"]), dry_run=False, destination_output_root=restored_root
    )
    restored_cfg = _restored_config(cfg, restored_root)

    # Re-harvest and anchor a POST-boundary day, then build an honest archive.
    reharvested = restored_root / relative
    reharvested.parent.mkdir(parents=True, exist_ok=True)
    reharvested.write_bytes(b"ts,bid,ask\n1784999999,0.77,0.23\n")
    assert anchor_ledgers(restored_cfg, anchor_date="2026-07-12")["status"] == "ok"
    restored_cfg.raw["disaster_recovery"]["excluded_path_prefixes"] = []
    honest = create_ledger_archive(restored_cfg, force=True)
    assert verify_and_restore_archive(cfg, Path(honest["archive_path"]), dry_run=True)["status"] == "ok"

    # Forge it: drop the corpus while still declaring no exclusions. The waiver
    # covers only rows at or before the honoured boundary, and the re-harvest above
    # anchored a POST-boundary row recording this file as present - so the existing
    # chain verification refuses it. No extra guard is needed on this path, and this
    # test pins that, because the property is load-bearing rather than incidental.
    forged = _rebuild_archive_with_manifest(
        Path(honest["archive_path"]),
        tmp_path / "forged.tar.gz",
        manifest_updates={},
        drop_paths=(f"outputs/{relative}",),
    )
    with pytest.raises(DisasterRecoveryError, match="anchored file is missing"):
        verify_and_restore_archive(cfg, forged, dry_run=True)


def test_wo127_leading_slash_declaration_cannot_evade_the_contradiction_check(tmp_path: Path):
    # Codex review of #364 (P1): the contradiction check matched RAW declarations while
    # the restore path matched normalised ones. "/polymarket_training/" therefore missed
    # here and was normalised into the registered exclusion there, so the marker waived
    # the included member's pre-boundary digest and arbitrary self-consistent bytes
    # restored clean. Two readings of one untrusted field is a bypass by construction.
    cfg = _config(tmp_path)
    corpus = _enroll_training_corpus(cfg, size_bytes=4096)
    _seed_two_day_chain(cfg)
    relative = corpus.relative_to(cfg.output_root).as_posix()
    built = create_ledger_archive(cfg, force=True)

    for index, spelling in enumerate(
        ("/polymarket_training/", "polymarket_training", "  polymarket_training/  ")
    ):
        forged = _rebuild_archive_with_manifest(
            Path(built["archive_path"]),
            tmp_path / f"slashed-{index}.tar.gz",
            manifest_updates={"excluded_path_prefixes": [spelling]},
            add_paths={f"outputs/{relative}": b"ts,bid,ask\n9999999999,0.01,0.99\n"},
        )
        with pytest.raises(DisasterRecoveryError, match="excluded while also including"):
            verify_and_restore_archive(cfg, forged, dry_run=True)


def test_wo127_an_invalid_inherited_boundary_never_displaces_the_current_one(tmp_path: Path):
    # Codex review of #364 (P2), the second time: ranking candidates by date meant ANY
    # later inherited boundary won, including an invalid one - a future date, or a head
    # naming no row. Post-build restore then refused the marker, found the deliberately
    # excluded corpus missing, and failed archive creation. The wedge class again,
    # arriving through the ranking function. The current pair now simply wins.
    cfg = _config(tmp_path)
    _enroll_training_corpus(cfg, size_bytes=4096)
    _seed_two_day_chain(cfg)

    marker = cfg.output_root / "performance" / "ledger_restore_provenance.json"
    for boundary, head in (
        ("2099-01-01", "a" * 64),   # future
        ("2026-07-11", "f" * 64),   # head names no row
        ("2026-13-45", "a" * 64),   # not a date
    ):
        write_json(
            marker,
            {
                "work_order": "WO-127",
                "restore_boundary_date": boundary,
                "excluded_path_prefixes": ["polymarket_training/"],
                "restored_from_chain_head": head,
            },
        )
        # The anti-wedge property: the build succeeds and its own post-build restore
        # verifies, instead of the bogus inherited pair displacing the valid one and
        # failing archive creation outright.
        built = create_ledger_archive(cfg, force=True)
        assert built["status"] == "ok", boundary
        assert built["post_build_restore_verification"]["status"] == "ok", boundary

    # And on an APPLIED restore the marker written into the destination carries this
    # archive's own pair, never the bogus inherited one.
    destination = tmp_path / "restored_outputs"
    applied = verify_and_restore_archive(
        cfg, Path(built["archive_path"]), dry_run=False, destination_output_root=destination
    )
    installed = read_json(destination / "performance" / "ledger_restore_provenance.json")
    assert applied["status"] == "ok"
    assert installed["restore_boundary_date"] == built["snapshot_date"]
    assert installed["restored_from_chain_head"] == built["chain_head"]


def test_wo127_a_narrowed_target_config_requires_the_corpus_on_restore(tmp_path: Path):
    # Codex review of #364 (P2) corrected my reasoning, and the better argument won.
    # I based the missing-path exception on whether the ARCHIVE validly declared the
    # prefix. But whether a restore delivers the coverage REQUIRED is the restoring
    # host's property: if this host's config says to archive the corpus, a restore
    # lacking it does not satisfy this host, however the archive labelled itself.
    cfg = _config(tmp_path)
    _enroll_training_corpus(cfg, size_bytes=4096)
    _seed_two_day_chain(cfg)
    built = create_ledger_archive(cfg, force=True)
    assert built["archive_excluded_paths"] == ["polymarket_training/"]

    # Same archive, restored under a config that requires the corpus in recovery.
    narrowed = _restored_config(cfg, cfg.output_root)
    narrowed.raw["disaster_recovery"]["excluded_path_prefixes"] = []
    with pytest.raises(DisasterRecoveryError) as refused:
        verify_and_restore_archive(narrowed, Path(built["archive_path"]), dry_run=True)
    # Refused either by the scope guard or by chain verification once the waiver is
    # correctly withheld - both are the required outcome, and the corpus is named.
    assert "polymarket_training/historical_bid_ask_v1.csv" in str(refused.value)

    # Under the registered config the same archive restores, unchanged.
    assert verify_and_restore_archive(cfg, Path(built["archive_path"]), dry_run=True)["status"] == "ok"


def test_wo127_relabelled_archive_cannot_smuggle_unattested_corpus_bytes(tmp_path: Path):
    # Codex review of #364 (P1). The undeclared-omission guard checked only that a
    # path EXISTS, so an attacker could satisfy it by ADDING bytes. An archive built
    # on a restored host while the prefix was excluded carries the inherited marker
    # and only missing_at_anchor rows after that boundary; relabel it to declare no
    # exclusions and add arbitrary self-consistent corpus bytes, and nothing ever
    # digest-checks them - pre-boundary rows are waived by the inherited marker, and
    # missing_at_anchor rows perform no byte check. The forged corpus restored as
    # recovered evidence. Presence is not attestation.
    cfg = _config(tmp_path)
    corpus = _enroll_training_corpus(cfg, size_bytes=4096)
    _seed_two_day_chain(cfg)
    relative = corpus.relative_to(cfg.output_root).as_posix()
    built = create_ledger_archive(cfg, force=True)
    restored_root = tmp_path / "restored_outputs"
    verify_and_restore_archive(
        cfg, Path(built["archive_path"]), dry_run=False, destination_output_root=restored_root
    )
    restored_cfg = _restored_config(cfg, restored_root)

    # A post-boundary anchor on the restored host, where the corpus is absent: the
    # row records missing_at_anchor, which performs no byte check.
    assert anchor_ledgers(restored_cfg, anchor_date="2026-07-12")["status"] == "ok"
    honest = create_ledger_archive(restored_cfg, force=True)
    assert honest["archive_excluded_paths"] == ["polymarket_training/"]
    assert verify_and_restore_archive(cfg, Path(honest["archive_path"]), dry_run=True)["status"] == "ok"

    forged = _rebuild_archive_with_manifest(
        Path(honest["archive_path"]),
        tmp_path / "smuggled.tar.gz",
        manifest_updates={"excluded_path_prefixes": [], "excluded_files": [], "excluded_file_count": 0},
        add_paths={f"outputs/{relative}": b"ts,bid,ask\n9999999999,0.01,0.99\n"},
    )
    with pytest.raises(DisasterRecoveryError, match="no anchor attests"):
        verify_and_restore_archive(cfg, forged, dry_run=True)


def test_wo127_malformed_exclusion_config_stamps_error_instead_of_raising(tmp_path: Path):
    # _base_payload runs BEFORE create_ledger_archive's try block, so a malformed
    # exclusion config raised out of the builder with no status written at all -
    # reintroducing exactly the blind-failure class WO-122a removed.
    cfg = _config(tmp_path)
    _seed_two_day_chain(cfg)
    cfg.raw["disaster_recovery"]["excluded_path_prefixes"] = 7

    status_path = cfg.output_root / "performance" / "disaster_recovery_status.json"
    with pytest.raises(DisasterRecoveryError, match="list of path prefixes"):
        create_ledger_archive(cfg, force=True)

    status = read_json(status_path)
    assert status["status"] == "error"
    assert status["failure_stamped"] is True
    assert "list of path prefixes" in status["archive_exclusion_config_error"]


def test_wo127_archive_declaring_and_including_a_prefix_is_refused(tmp_path: Path):
    # Codex review of #364 (P1). WO-127 grants boundary-scoped verification
    # tolerance from the manifest's exclusion declaration, and installs the marker
    # BEFORE chain verification. So an archive that declares polymarket_training/
    # excluded while ALSO shipping a member under it had that member's anchored
    # digest skipped: attacker-chosen bytes with a self-consistent archive manifest
    # would restore with status ok. No archive this code builds takes that shape
    # (_archive_source_payloads reports excluded paths instead of adding them), so
    # it is refused at the untrusted-input boundary.
    cfg = _config(tmp_path)
    # Small enough that both forged archives stay under the 1MB test cap, so the
    # refusal below is provably the shape check and not the size guard.
    corpus = _enroll_training_corpus(cfg, size_bytes=4096)
    _seed_two_day_chain(cfg)
    built = create_ledger_archive(cfg, force=True)
    source = Path(built["archive_path"])
    assert built["archive_excluded_paths"], "the fixture must exercise a real exclusion"
    relative = corpus.relative_to(cfg.output_root).as_posix()

    contradictory = _rebuild_archive_with_manifest(
        source,
        tmp_path / "contradictory.tar.gz",
        manifest_updates={},
        add_paths={f"outputs/{relative}": b"ts,bid,ask\n9999999999,0.01,0.99\n"},
    )
    with pytest.raises(DisasterRecoveryError, match="excluded while also including"):
        verify_and_restore_archive(cfg, contradictory, dry_run=True)

    # The rejection is on the archive SHAPE, so it does not depend on the bytes
    # being wrong: even a byte-faithful copy of the live corpus is refused, because
    # the declaration and the payload contradict each other either way.
    faithful = _rebuild_archive_with_manifest(
        source,
        tmp_path / "faithful.tar.gz",
        manifest_updates={},
        add_paths={f"outputs/{relative}": corpus.read_bytes()},
    )
    with pytest.raises(DisasterRecoveryError, match="excluded while also including"):
        verify_and_restore_archive(cfg, faithful, dry_run=True)

    # And the honest archive built by this code still restores.
    assert verify_and_restore_archive(cfg, source, dry_run=True)["status"] == "ok"


# --- WO-150: live_capital_context must reflect BINDING capital, not the mere
# presence of a read-only monitoring address ---


_FAKE_WALLET = "0x" + "a" * 40


@pytest.mark.parametrize(
    "wallet, trading_mode, flag, expected_live_context, expected_max_hours",
    [
        # (1) no flag, address configured: today's behaviour preserved by default.
        (_FAKE_WALLET, "paper", _UNSET, True, 24.0),
        # (2) flag true, address configured, trading_mode paper: downgrades to
        # the paper-stage ceiling.
        (_FAKE_WALLET, "paper", True, False, 168.0),
        # (3) flag true but trading_mode live: the branch that can never be
        # downgraded by any config value.
        (_FAKE_WALLET, "live", True, True, 24.0),
        # (4) flag true but trading_mode off: the allowlist case - "off" is a
        # valid trading.mode (config.py:95-96) and must close conservatively.
        (_FAKE_WALLET, "off", True, True, 24.0),
        # (5) flag as the string "true": boolish coercion is deliberately not
        # used here, so a loose string must not downgrade the ceiling.
        (_FAKE_WALLET, "paper", "true", True, 24.0),
        # (6) flag as int 1: `1 == True` in Python (bool subclasses int), but
        # `1 is True` is False - only the latter may gate a safety bound.
        (_FAKE_WALLET, "paper", 1, True, 24.0),
        # (7) flag explicitly None: same as absent, not coerced.
        (_FAKE_WALLET, "paper", None, True, 24.0),
        # (8) empty wallet_address with flag true: the wallet term is inert
        # regardless of the flag, so the paper-stage ceiling still applies.
        ("", "paper", True, False, 168.0),
    ],
    ids=[
        "1_no_flag_wallet_configured",
        "2_flag_true_paper",
        "3_flag_true_live_mode_never_downgrades",
        "4_flag_true_off_mode_closes_conservative",
        "5_flag_string_true_not_coerced",
        "6_flag_int_one_not_coerced",
        "7_flag_none_not_coerced",
        "8_empty_wallet_term_inert_either_way",
    ],
)
def test_wo150_live_capital_context_predicate_matrix(
    tmp_path: Path,
    monkeypatch,
    wallet,
    trading_mode,
    flag,
    expected_live_context,
    expected_max_hours,
):
    from polymarket_predictive_engine.disaster_recovery import _settings, _validated_rpo

    if trading_mode == "live":
        # config.py:97-98 requires the dual opt-in env var before trading_mode
        # "live" even loads; unrelated to and does not enable any order path.
        monkeypatch.setenv("POLYMARKET_LIVE_TRADING", "1")
    cfg = _config(tmp_path, wallet=wallet, trading_mode=trading_mode, wallet_read_only_monitoring=flag)
    # Held at 24h throughout: it satisfies both ceilings (24h and 168h), so
    # every case here exercises ONLY which ceiling is selected, never the
    # config guard at :205 (that latent widening is test 9, below).
    cfg.raw["disaster_recovery"]["active_rpo_hours"] = 24

    rpo = _validated_rpo(cfg, _settings(cfg))

    assert rpo["live_capital_context"] is expected_live_context
    assert rpo["maximum_rpo_hours_for_context"] == expected_max_hours


def test_wo150_flag_widens_the_config_guard_but_only_with_the_flag(tmp_path: Path) -> None:
    """(9) THE CENTREPIECE - the only test that exercises what actually
    changes. With the flag and trading_mode paper, active_rpo_hours: 100
    validates (no ValueError) where the identical config raises today at
    :205-210; with the flag absent, the same config still raises."""
    from polymarket_predictive_engine.disaster_recovery import _settings, _validated_rpo

    with_flag = _config(tmp_path, wallet=_FAKE_WALLET, trading_mode="paper", wallet_read_only_monitoring=True)
    with_flag.raw["disaster_recovery"]["active_rpo_hours"] = 100
    rpo = _validated_rpo(with_flag, _settings(with_flag))  # must not raise
    assert rpo["live_capital_context"] is False
    assert rpo["maximum_rpo_hours_for_context"] == 168.0
    assert rpo["active_rpo_hours"] == 100.0

    without_flag = _config(tmp_path, wallet=_FAKE_WALLET, trading_mode="paper")
    without_flag.raw["disaster_recovery"]["active_rpo_hours"] = 100
    with pytest.raises(ValueError, match="24h maximum"):
        _validated_rpo(without_flag, _settings(without_flag))


def test_wo150_applied_compliance_bound_does_not_move_with_the_flag(tmp_path: Path) -> None:
    """(10) The applied bound does NOT move. With the flag true and a
    30-hour-old archive, rpo.compliant is False - identical to today, because
    compliance compares the OBSERVED age against active_rpo_hours (24.0,
    unchanged), never against maximum_rpo_hours_for_context (:217, untouched
    by this WO)."""
    cfg = _config(tmp_path, wallet=_FAKE_WALLET, trading_mode="paper", wallet_read_only_monitoring=True)
    cfg.raw["disaster_recovery"]["active_rpo_hours"] = 24
    _seed_two_day_chain(cfg)
    status_path = cfg.output_root / "performance" / "disaster_recovery_status.json"

    first = create_ledger_archive(cfg, force=True)
    assert first["rpo"]["live_capital_context"] is False
    assert first["rpo"]["maximum_rpo_hours_for_context"] == 168.0

    stale_stamp = (datetime.now(timezone.utc) - timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    status = read_json(status_path)
    status["last_remote_success_at_utc"] = stale_stamp
    write_json(status_path, status)
    stale = create_ledger_archive(cfg, force=True)

    assert stale["rpo"]["live_capital_context"] is False
    assert stale["rpo"]["active_rpo_hours"] == 24.0
    assert stale["rpo"]["compliant"] is False


def test_wo150_maximum_rpo_hours_for_context_boundary_pair(tmp_path: Path) -> None:
    """(11) Boundary pair on the reported field only: maximum_rpo_hours_for_context
    reads 168.0 with the flag and 24.0 without it, on an otherwise identical
    config."""
    from polymarket_predictive_engine.disaster_recovery import _settings, _validated_rpo

    with_flag = _config(tmp_path, wallet=_FAKE_WALLET, trading_mode="paper", wallet_read_only_monitoring=True)
    with_flag.raw["disaster_recovery"]["active_rpo_hours"] = 24
    assert _validated_rpo(with_flag, _settings(with_flag))["maximum_rpo_hours_for_context"] == 168.0

    without_flag = _config(tmp_path, wallet=_FAKE_WALLET, trading_mode="paper")
    without_flag.raw["disaster_recovery"]["active_rpo_hours"] = 24
    assert _validated_rpo(without_flag, _settings(without_flag))["maximum_rpo_hours_for_context"] == 24.0


def test_wo150_ceiling_literals_are_byte_identical_in_defaults_and_example_config() -> None:
    """(12) byte-identity: the three ceiling literals this WO must not move,
    both as computed by the code and as deployed in the example config."""
    from polymarket_predictive_engine.disaster_recovery import _settings

    cfg = load_config(ROOT / "polymarket_predictive_config.example.yaml")
    settings = _settings(cfg)
    assert settings["active_rpo_hours"] == 24.0
    assert settings["paper_stage_max_rpo_hours"] == 168.0
    assert settings["pre_live_max_rpo_hours"] == 24.0

    raw = yaml.safe_load((ROOT / "polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    assert raw["disaster_recovery"]["active_rpo_hours"] == 24
    assert raw["disaster_recovery"]["paper_stage_max_rpo_hours"] == 168
    assert raw["disaster_recovery"]["pre_live_max_rpo_hours"] == 24


@pytest.mark.parametrize(
    "corrupt_block",
    ["a string", 7, 1.5, True, ["wallet_address"]],
)
def test_wo150_corrupt_maker_live_test_block_selects_the_conservative_ceiling(corrupt_block: object) -> None:
    """A structurally corrupt `maker_live_test` is "doubt about its meaning",
    and WO-150's fail-safe sentence says doubt selects the CONSERVATIVE ceiling.

    Found by independent line audit of the first build: that version normalised
    a non-dict block to `{}`, which makes `wallet` empty, reads as inert, and
    selects the PERMISSIVE 168h branch — the one direction a corrupt config must
    never buy. A well-formed-but-absent block (`None`, missing) is a different
    case and deliberately keeps its permissive-for-paper behaviour.
    """
    from polymarket_predictive_engine.disaster_recovery import _live_capital_context

    class _Cfg:
        trading_mode = "paper"

        def __init__(self, block: object) -> None:
            self.raw = {"maker_live_test": block}

    assert _live_capital_context(_Cfg(corrupt_block)) is True, corrupt_block

    # The well-formed empty cases are unchanged: no wallet means no binding capital.
    for benign in (None, {}, {"wallet_address": ""}):
        assert _live_capital_context(_Cfg(benign)) is False, benign


# --- WO-146: the DR archive build trigger is its own RPO ceiling ---
#
# The build cadence moves 24.0h -> 20.0h. No RPO ceiling value moves:
# active_rpo_hours (24.0), paper_stage_max_rpo_hours (168.0), and
# pre_live_max_rpo_hours (24.0) are pinned unchanged by test (14) below. Tests
# are numbered to match the WO's own enumeration.


def test_wo146_test1_absent_config_uses_the_registered_default(tmp_path: Path) -> None:
    from polymarket_predictive_engine.disaster_recovery import _settings, _validated_rpo

    cfg = _config(tmp_path)
    cfg.raw["disaster_recovery"]["active_rpo_hours"] = 24
    rpo = _validated_rpo(cfg, _settings(cfg))

    assert rpo["archive_build_interval_hours"] == pytest.approx(20.0, abs=1e-12)
    assert rpo["archive_build_interval_source"] == "registered_default"
    assert rpo["archive_build_margin_hours"] == pytest.approx(3.5, abs=1e-12)


def test_wo146_test2_configured_value_inside_range_passes_through(tmp_path: Path) -> None:
    from polymarket_predictive_engine.disaster_recovery import _settings, _validated_rpo

    cfg = _config(tmp_path)
    cfg.raw["disaster_recovery"]["active_rpo_hours"] = 24
    cfg.raw["disaster_recovery"]["archive_build_interval_hours"] = 12.0
    rpo = _validated_rpo(cfg, _settings(cfg))

    assert rpo["archive_build_interval_hours"] == pytest.approx(12.0, abs=1e-12)
    assert rpo["archive_build_interval_source"] == "config"
    assert rpo["archive_build_margin_hours"] == pytest.approx(11.5, abs=1e-12)


def test_wo146_test3_below_floor_clamps_up_to_6_0(tmp_path: Path) -> None:
    from polymarket_predictive_engine.disaster_recovery import _settings, _validated_rpo

    cfg = _config(tmp_path)
    cfg.raw["disaster_recovery"]["active_rpo_hours"] = 24
    cfg.raw["disaster_recovery"]["archive_build_interval_hours"] = 2.0
    rpo = _validated_rpo(cfg, _settings(cfg))

    assert rpo["archive_build_interval_hours"] == pytest.approx(6.0, abs=1e-12)
    assert rpo["archive_build_interval_source"] == "clamped_to_floor"
    assert rpo["archive_build_margin_hours"] == pytest.approx(17.5, abs=1e-12)


def test_wo146_test4_above_ceiling_clamps_down_to_20_0(tmp_path: Path) -> None:
    from polymarket_predictive_engine.disaster_recovery import _settings, _validated_rpo

    cfg = _config(tmp_path)
    cfg.raw["disaster_recovery"]["active_rpo_hours"] = 24
    cfg.raw["disaster_recovery"]["archive_build_interval_hours"] = 48.0
    rpo = _validated_rpo(cfg, _settings(cfg))

    assert rpo["archive_build_interval_hours"] == pytest.approx(20.0, abs=1e-12)
    assert rpo["archive_build_interval_source"] == "clamped_to_ceiling"
    assert rpo["archive_build_margin_hours"] == pytest.approx(3.5, abs=1e-12)


def test_wo146_test5_non_numeric_value_fails_closed_before_any_archive_is_built(
    tmp_path: Path,
) -> None:
    cfg = _config(tmp_path)
    _seed_two_day_chain(cfg)
    cfg.raw["disaster_recovery"]["archive_build_interval_hours"] = "abc"

    with pytest.raises(DisasterRecoveryError):
        create_ledger_archive(cfg, force=True)

    status = read_json(cfg.output_root / "performance" / "disaster_recovery_status.json")
    assert status["status"] == "error"
    assert status["failure_stamped"] is True
    assert not Path(status["archive_path"]).exists()


def test_wo146_test6_non_finite_value_fails_closed_and_the_guard_is_proven(
    tmp_path: Path,
) -> None:
    # `safe_float("nan")` returns NaN, not None - proves the guard is needed,
    # not the parser: an unguarded `nan > ceiling` reads corrupt input as fresh.
    assert math.isnan(safe_float("nan"))

    cfg = _config(tmp_path)
    _seed_two_day_chain(cfg)
    cfg.raw["disaster_recovery"]["archive_build_interval_hours"] = float("nan")

    with pytest.raises(DisasterRecoveryError):
        create_ledger_archive(cfg, force=True)

    status = read_json(cfg.output_root / "performance" / "disaster_recovery_status.json")
    assert status["status"] == "error"
    assert status["failure_stamped"] is True
    assert not Path(status["archive_path"]).exists()


@pytest.mark.parametrize("bad_value", [0, -1.0])
def test_wo146_test7_zero_or_negative_value_fails_closed(tmp_path: Path, bad_value) -> None:
    cfg = _config(tmp_path)
    _seed_two_day_chain(cfg)
    cfg.raw["disaster_recovery"]["archive_build_interval_hours"] = bad_value

    with pytest.raises(DisasterRecoveryError):
        create_ledger_archive(cfg, force=True)

    status = read_json(cfg.output_root / "performance" / "disaster_recovery_status.json")
    assert status["status"] == "error"
    assert status["failure_stamped"] is True


def test_wo146_test8_not_due_before_the_build_interval_elapses(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.raw["disaster_recovery"]["active_rpo_hours"] = 24
    _seed_two_day_chain(cfg)
    create_ledger_archive(cfg, force=True)
    status_path = cfg.output_root / "performance" / "disaster_recovery_status.json"
    last = datetime.now(timezone.utc) - timedelta(hours=19.9)
    status = read_json(status_path)
    status.update(
        {
            "remote_push_status": "ok",
            "last_remote_success_at_utc": last.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "last_remote_snapshot_date": status["snapshot_date"],
        }
    )
    write_json(status_path, status)

    result = create_ledger_archive(cfg)

    assert result["status"] == "not_due"
    assert result["last_remote_archive_age_hours"] == pytest.approx(19.9, abs=0.01)
    assert result["rpo"]["compliant"] is True
    expected_next_due = (last + timedelta(hours=20.0)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert result["next_archive_due_at_utc"] == expected_next_due


def test_wo146_test9_regression_the_build_is_due_before_the_ceiling_is_breached(
    tmp_path: Path,
) -> None:
    # THE regression this WO exists to fix. Under unmodified `main` (the build
    # trigger and the compliance ceiling are the same 24.0h number), the
    # identical fixture below yields status "not_due" at age 20.5h and
    # compliant True - the archive is simply NOT REBUILT, and keeps aging
    # toward the ceiling it can only reach by breaching it. (Corrected after
    # line audit: an earlier version of this comment said the fixture yields
    # 24.5h / compliant False on main. It does not - that is what happens four
    # hours LATER, not what this fixture produces. The assertion below was
    # always right; the comment describing it was not, and this is the WO's
    # centrepiece regression proof.) With the 20.0h interval, the same elapsed
    # time is already due,
    # so the rebuild happens while the ceiling is still satisfied.
    cfg = _config(tmp_path)
    cfg.raw["disaster_recovery"]["active_rpo_hours"] = 24
    _seed_two_day_chain(cfg)
    create_ledger_archive(cfg, force=True)
    status_path = cfg.output_root / "performance" / "disaster_recovery_status.json"
    stale_stamp = (datetime.now(timezone.utc) - timedelta(hours=20.5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    status = read_json(status_path)
    status.update(
        {
            "remote_push_status": "ok",
            "last_remote_success_at_utc": stale_stamp,
            "last_remote_snapshot_date": status["snapshot_date"],
        }
    )
    write_json(status_path, status)

    result = create_ledger_archive(cfg)  # not forced: the due decision must trigger this alone

    assert result["status"] == "ok"
    assert result["last_remote_archive_age_hours"] == pytest.approx(20.5, abs=0.01)
    assert result["rpo"]["compliant"] is True


def test_wo146_test10_the_alarm_still_fires_past_the_unchanged_ceiling(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.raw["disaster_recovery"]["active_rpo_hours"] = 24
    _seed_two_day_chain(cfg)
    create_ledger_archive(cfg, force=True)
    status_path = cfg.output_root / "performance" / "disaster_recovery_status.json"
    stale_stamp = (datetime.now(timezone.utc) - timedelta(hours=24.5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    status = read_json(status_path)
    status.update(
        {
            "remote_push_status": "ok",
            "last_remote_success_at_utc": stale_stamp,
            "last_remote_snapshot_date": status["snapshot_date"],
        }
    )
    write_json(status_path, status)

    result = create_ledger_archive(cfg)

    assert result["status"] == "ok"
    assert result["last_remote_archive_age_hours"] == pytest.approx(24.5, abs=0.01)
    assert result["rpo"]["compliant"] is False


def test_wo146_test11_never_archived_host_still_alarms(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    _seed_two_day_chain(cfg)

    result = create_ledger_archive(cfg)  # not forced: due=True purely from absence

    assert result["status"] == "ok"
    assert result["last_remote_archive_age_hours"] is None
    assert result["rpo"]["observed_archive_age_hours"] is None
    assert result["rpo"]["compliant"] is False


def test_wo146_test12_nan_active_rpo_hours_fails_closed(tmp_path: Path) -> None:
    # 146.2: `nan <= 0` is False, so an unguarded active_rpo_hours: nan used to
    # pass validation. Strictly tightening - it can only stamp an error where
    # today's code would silently accept the corrupt value.
    cfg = _config(tmp_path)
    _seed_two_day_chain(cfg)
    cfg.raw["disaster_recovery"]["active_rpo_hours"] = float("nan")

    with pytest.raises(DisasterRecoveryError):
        create_ledger_archive(cfg, force=True)

    status = read_json(cfg.output_root / "performance" / "disaster_recovery_status.json")
    assert status["status"] == "error"
    assert status["failure_stamped"] is True


def test_wo146_test13_pre_live_ceiling_violation_message_is_unchanged(tmp_path: Path) -> None:
    cfg = _config(tmp_path, wallet="0x" + "a" * 40)
    _seed_two_day_chain(cfg)
    cfg.raw["disaster_recovery"]["active_rpo_hours"] = 200

    with pytest.raises(DisasterRecoveryError, match="24h maximum"):
        create_ledger_archive(cfg, force=True)


def test_wo146_test14_static_ceiling_literals_unchanged_in_example_config() -> None:
    raw = yaml.safe_load((ROOT / "polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    dr = raw["disaster_recovery"]

    assert dr["active_rpo_hours"] == 24
    assert dr["paper_stage_max_rpo_hours"] == 168
    assert dr["pre_live_max_rpo_hours"] == 24
    assert dr["size_cap_mb"] == 240


def test_wo146_test15_archive_build_interval_hours_caller_set_is_exhaustively_scanned() -> None:
    # Test (15): A3 - exhaustive scan of every root the WO names, anchored off
    # __file__ (never CWD), over src/scripts/tests/docs/.github plus root
    # yaml/yml/toml, excluding ROOT/".claude", asserting a non-zero visit
    # count.
    #
    # ESCALATION (see build report): the WO's enumerated text says the string
    # "appears in exactly the four touched files" once "docs" is one of the
    # scanned roots. That is not achievable without editing
    # docs/POLYMARKET_CODEX_WORK_ORDERS.md - not on the touch list, and it is
    # the WO's OWN registered specification, which necessarily names the
    # setting it registers. This test therefore asserts the demonstrable
    # property the scan exists to prove (no unexpected code location outside
    # the touched set references the setting name) by naming that one
    # pre-existing, out-of-scope, expected exception explicitly rather than
    # silently narrowing the scan to make the literal wording pass.
    root = Path(__file__).resolve().parents[2]
    roots = {
        "src": root / "src",
        "scripts": root / "scripts",
        "tests": root / "tests",
        "docs": root / "docs",
        ".github": root / ".github",
    }
    needle = "archive_build_interval_hours"

    visited_files = 0
    hits: set[str] = set()
    for scan_root in roots.values():
        if not scan_root.exists():
            continue
        for path in scan_root.rglob("*"):
            if not path.is_file():
                continue
            relative_parts = path.relative_to(root).parts
            # ROOT/".claude" is excluded, not any ancestor containing the
            # literal substring ".claude" - this worktree itself is checked
            # out under ".../.claude/worktrees/...", so every path's absolute
            # string contains ".claude" as an ANCESTOR of ROOT. "__pycache__"
            # holds compiled bytecode, which embeds source string literals
            # (including this setting's name) verbatim and is not a caller.
            if relative_parts[0] == ".claude" or "__pycache__" in relative_parts:
                continue
            visited_files += 1
            text = path.read_text(encoding="utf-8", errors="replace")
            if needle in text:
                hits.add(path.relative_to(root).as_posix())
    for pattern in ("*.yaml", "*.yml", "*.toml"):
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            visited_files += 1
            text = path.read_text(encoding="utf-8", errors="replace")
            if needle in text:
                hits.add(path.relative_to(root).as_posix())

    assert visited_files > 0
    touched = {
        "src/polymarket_predictive_engine/disaster_recovery.py",
        "scripts/push_vps_archive.sh",
        "polymarket_predictive_config.example.yaml",
        "tests/polymarket_predictive_engine/test_disaster_recovery.py",
    }
    registered_text_only = {"docs/POLYMARKET_CODEX_WORK_ORDERS.md"}
    assert hits == touched | registered_text_only


def test_wo146_test16_shell_advisory_carries_the_6_0_fallback() -> None:
    # Coverage limit stated honestly: the shell script is not executed by the
    # offline suite (it force-pushes a Git branch and is VPS-only), so this is
    # a text assertion, not behavioural.
    push = (ROOT / "scripts" / "push_vps_archive.sh").read_text(encoding="utf-8")
    assert "archive_build_interval_hours" in push
    assert "interval_hours = 6.0" in push
