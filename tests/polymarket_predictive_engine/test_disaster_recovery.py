"""WO-65 size-capped snapshot and tested-restore controls."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
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
    assert result["size_cap_mb"] == 50
    assert result["rpo"]["paper_stage_max_rpo_hours"] == 168
    assert result["rpo"]["pre_live_max_rpo_hours"] == 24


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

    assert "MAX_ARCHIVE_BYTES=52428800" in push
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
