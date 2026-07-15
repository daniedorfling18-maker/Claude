from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRITER = ROOT / "scripts" / "write_vps_telemetry_manifest.py"


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _run_writer(repo: Path, output: Path, as_of: str) -> dict:
    completed = subprocess.run(
        [
            sys.executable,
            str(WRITER),
            "--repo-root",
            str(repo),
            "--output",
            str(output),
            "--as-of",
            as_of,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(output.read_text(encoding="utf-8"))


def _repo_with_origin(tmp_path: Path) -> tuple[Path, str]:
    origin = tmp_path / "origin.git"
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "first")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-u", "origin", "main")
    return repo, _git(repo, "rev-parse", "HEAD")


def test_manifest_tracks_alignment_and_persists_divergence_start(tmp_path: Path) -> None:
    repo, first_sha = _repo_with_origin(tmp_path)
    output = repo / "outputs" / "performance" / "vps_telemetry_manifest.json"
    marker = repo / "outputs" / "performance" / "deployed_git_rev"
    marker.parent.mkdir(parents=True)
    marker.write_text(first_sha + "\n", encoding="utf-8")

    aligned = _run_writer(repo, output, "2026-07-15T08:00:00Z")
    assert aligned["source_git_rev"] == first_sha
    assert aligned["checkout_git_rev"] == first_sha
    assert aligned["deployed_git_rev"] == first_sha
    assert aligned["divergence_started_at_utc"] == ""
    assert aligned["paper_trading_invoked"] is False
    assert aligned["live_trading_invoked"] is False

    (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "second")
    _git(repo, "push", "origin", "main")
    second_sha = _git(repo, "rev-parse", "HEAD")

    diverged = _run_writer(repo, output, "2026-07-15T08:30:00Z")
    assert diverged["source_git_rev"] == second_sha
    assert diverged["checkout_git_rev"] == second_sha
    assert diverged["deployed_git_rev"] == first_sha
    assert diverged["divergence_started_at_utc"] == "2026-07-15T08:30:00Z"

    repeated = _run_writer(repo, output, "2026-07-15T08:45:00Z")
    assert repeated["divergence_started_at_utc"] == "2026-07-15T08:30:00Z"


def test_manifest_uses_sealed_env_marker_only_when_file_is_absent(tmp_path: Path) -> None:
    repo, deployed_sha = _repo_with_origin(tmp_path)
    output = repo / "outputs" / "performance" / "vps_telemetry_manifest.json"
    (repo / ".env").write_text(f'PM_VPS_DEPLOYED_SHA="{deployed_sha}"\n', encoding="utf-8")

    payload = _run_writer(repo, output, "2026-07-15T09:00:00Z")

    assert payload["source_git_rev"] == deployed_sha
    assert payload["checkout_git_rev"] == deployed_sha
    assert payload["deployed_git_rev"] == deployed_sha
    assert payload["divergence_started_at_utc"] == ""
