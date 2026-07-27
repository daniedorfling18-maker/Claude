"""WO-65 size-capped snapshot and tested-restore controls."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import json
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
from polymarket_predictive_engine.utils import read_json, write_json


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


def _config(tmp_path: Path, *, wallet: str = ""):
    raw = yaml.safe_load((ROOT / "polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    raw["maker_live_test"]["wallet_address"] = wallet
    raw["trading"]["mode"] = "paper"
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
