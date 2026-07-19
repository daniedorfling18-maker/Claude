from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
ROLLBACK = ROOT / "scripts" / "rollback_vps_paper_deploy.py"
SERVICES = (
    "polymarket-paper-live",
    "polymarket-dashboard",
    "superbru-auto-pick-watchdog",
    "vps-ops-scheduler",
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _write(repo: Path, relative: str, value: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _fixture(tmp_path: Path) -> tuple[Path, str, str, Path, Path, Path]:
    repo = tmp_path / "deploy"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "ci@example.invalid")
    _git(repo, "config", "user.name", "CI")
    _write(repo, ".gitignore", ".env\n")
    _write(repo, "src/app.py", "VERSION = 'old'\n")
    _write(repo, "outputs/state.json", '{"state":"old"}\n')
    _write(repo, "outputs/performance/deployed_git_rev", "placeholder\n")
    _write(repo, "docker-compose.vps-paper.yml", "services: {}\n")
    _write(repo, "scripts/check_polymarket_vps_paper.sh", "#!/usr/bin/env sh\nexit 0\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "last known good")
    old = _git(repo, "rev-parse", "HEAD")
    _write(repo, "outputs/performance/deployed_git_rev", old + "\n")
    _git(repo, "add", "outputs/performance/deployed_git_rev")
    _git(repo, "commit", "--amend", "--no-edit")
    old = _git(repo, "rev-parse", "HEAD")
    # The marker is runtime evidence and need not be self-referential in the
    # fixture commit; the external backup carries the exact deployed SHA.
    _write(repo, "src/app.py", "VERSION = 'candidate'\n")
    _git(repo, "add", "src/app.py")
    _git(repo, "commit", "-m", "candidate")
    candidate = _git(repo, "rev-parse", "HEAD")

    _write(repo, "outputs/state.json", '{"state":"runtime-after-candidate"}\n')
    _write(repo, "outputs/performance/deployed_git_rev", candidate + "\n")
    _write(repo, ".env", "PM_VPS_DEPLOYED_SHA=candidate\nSECRET=target\n")

    env_backup = tmp_path / "env.backup"
    env_backup.write_text("PM_VPS_DEPLOYED_SHA=old\nSECRET=preserved\n", encoding="utf-8")
    marker_backup = tmp_path / "marker.backup"
    marker_backup.write_text(old + "\n", encoding="utf-8")
    docker_log = tmp_path / "docker.log"
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, sys\n"
        "pathlib.Path(os.environ['FAKE_DOCKER_LOG']).open('a', encoding='utf-8').write(' '.join(sys.argv[1:]) + '\\n')\n"
        "if len(sys.argv) > 2 and sys.argv[1:3] == ['image', 'inspect']:\n"
        "    print('sha256:last-known-good')\n"
        "if 'ps' in sys.argv and '--services' in sys.argv:\n"
        f"    print({' '.join(SERVICES)!r}.replace(' ', '\\n'))\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    return repo, old, candidate, env_backup, marker_backup, fake_docker


def _rollback(
    repo: Path,
    old: str,
    candidate: str,
    env_backup: Path,
    marker_backup: Path,
    fake_docker: Path,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["FAKE_DOCKER_LOG"] = str(fake_docker.with_name("docker.log"))
    command = [
        sys.executable,
        str(ROLLBACK),
        "--repo",
        str(repo),
        "--rollback-ref",
        old,
        "--env-backup",
        str(env_backup),
        "--marker-backup",
        str(marker_backup),
        "--marker-was-present",
        "--image-backup-tag",
        "polymarket-paper-vps:rollback-last-known-good",
        "--docker-command",
        str(fake_docker),
        "--telemetry-writer",
        str(ROOT / "scripts" / "write_vps_telemetry_manifest.py"),
        "--failed-target-sha",
        candidate,
        "--health-attempts",
        "1",
        "--health-interval-seconds",
        "0",
    ]
    for service in SERVICES:
        command.extend(("--required-service", service))
    return subprocess.run(command, capture_output=True, text=True, env=env)


def test_actual_rollback_restores_source_env_marker_image_and_stack(tmp_path: Path) -> None:
    repo, old, candidate, env_backup, marker_backup, fake_docker = _fixture(tmp_path)

    result = _rollback(repo, old, candidate, env_backup, marker_backup, fake_docker)

    assert result.returncode == 0, result.stderr or result.stdout
    assert _git(repo, "rev-parse", "HEAD") == old
    assert (repo / "src/app.py").read_text(encoding="utf-8") == "VERSION = 'old'\n"
    assert (repo / "outputs/state.json").read_text(encoding="utf-8") == '{"state":"runtime-after-candidate"}\n'
    assert (repo / ".env").read_bytes() == env_backup.read_bytes()
    assert (repo / "outputs/performance/deployed_git_rev").read_bytes() == marker_backup.read_bytes()
    assert _git(repo, "stash", "list") == ""

    calls = fake_docker.with_name("docker.log").read_text(encoding="utf-8")
    assert "compose -f docker-compose.vps-paper.yml stop --timeout 60" in calls
    assert "image tag polymarket-paper-vps:rollback-last-known-good polymarket-paper-vps:latest" in calls
    assert calls.count("image inspect --format {{.Id}}") == 2
    assert "compose -f docker-compose.vps-paper.yml up -d --no-build --force-recreate" in calls
    report = json.loads((repo / "outputs/performance/vps_deploy_rollback.json").read_text())
    assert report["status"] == "PASS"
    assert report["decision"] == "ROLLED_BACK_TO_LAST_KNOWN_GOOD"
    assert report["runtime_evidence_deleted"] is False
    assert report["restored_image_id"] == "sha256:last-known-good"
    assert report["telemetry_manifest_refreshed"] is True
    manifest = json.loads(
        (repo / "outputs/performance/vps_telemetry_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["checkout_git_rev"] == old
    assert manifest["deployed_git_rev"] == old
    assert report["paper_trading_invoked"] is False
    assert report["live_trading_invoked"] is False


def test_rollback_refuses_source_dirt_before_stopping_stack(tmp_path: Path) -> None:
    repo, old, candidate, env_backup, marker_backup, fake_docker = _fixture(tmp_path)
    _write(repo, "src/app.py", "UNREVIEWED LOCAL SOURCE\n")

    result = _rollback(repo, old, candidate, env_backup, marker_backup, fake_docker)

    assert result.returncode == 1
    assert _git(repo, "rev-parse", "HEAD") == candidate
    assert not fake_docker.with_name("docker.log").exists()
    report = json.loads((repo / "outputs/performance/vps_deploy_rollback.json").read_text())
    assert report["status"] == "FAIL"
    assert "source_dirty_paths" in report["error"]
    assert report["stack_was_stopped"] is False
