from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
UPDATER = ROOT / "scripts" / "update_vps_checkout_preserving_runtime.py"


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        capture_output=True,
        text=True,
    )


def _write(repo: Path, relative: str, value: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _repo_with_target(tmp_path: Path, *, target_output: str | None = None) -> tuple[Path, str]:
    repo = tmp_path / "deploy"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "ci@example.invalid")
    _git(repo, "config", "user.name", "CI")
    _write(repo, "src/app.py", "VERSION = 1\n")
    _write(repo, "outputs/state.json", '{"state":"seed"}\n')
    _write(repo, "inputs/polymarket/positions.csv", "market,size\n")
    _write(repo, "data/fixtures.csv", "id\n")
    _write(repo, "work/.keep", "")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    _git(repo, "switch", "-c", "target")
    _write(repo, "src/app.py", "VERSION = 2\n")
    if target_output is not None:
        _write(repo, "outputs/state.json", target_output)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "target")
    target = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "switch", "main")
    return repo, target


def _update(repo: Path, target: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(UPDATER),
            "--repo",
            str(repo),
            "--target-ref",
            target,
            "--branch",
            "main",
            "--report",
            "outputs/performance/vps_checkout_update.json",
        ],
        capture_output=True,
        text=True,
    )


def test_update_fast_forwards_and_preserves_runtime_state(tmp_path: Path):
    repo, target = _repo_with_target(tmp_path)
    _write(repo, "outputs/state.json", '{"state":"runtime"}\n')
    ledger = repo / "work" / "polymarket" / "ledger.sqlite"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_bytes(b"runtime-ledger")

    result = _update(repo, target)

    assert result.returncode == 0, result.stderr or result.stdout
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == target
    assert (repo / "src" / "app.py").read_text(encoding="utf-8") == "VERSION = 2\n"
    assert (repo / "outputs" / "state.json").read_text(encoding="utf-8") == '{"state":"runtime"}\n'
    assert ledger.read_bytes() == b"runtime-ledger"
    assert _git(repo, "stash", "list").stdout.strip() == ""
    report = json.loads((repo / "outputs" / "performance" / "vps_checkout_update.json").read_text())
    assert report["decision"] == "CHECKOUT_UPDATED_RUNTIME_PRESERVED"
    assert report["runtime_state_reapplied"] is True
    assert report["runtime_evidence_deleted"] is False
    assert report["untracked_runtime_touched"] is False


def test_update_refuses_tracked_source_changes(tmp_path: Path):
    repo, target = _repo_with_target(tmp_path)
    original_head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _write(repo, "src/app.py", "LOCAL SOURCE CHANGE\n")

    result = _update(repo, target)

    assert result.returncode == 4
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == original_head
    assert (repo / "src" / "app.py").read_text(encoding="utf-8") == "LOCAL SOURCE CHANGE\n"
    payload = json.loads(result.stdout)
    assert payload["decision"] == "KEEP_EXISTING_CHECKOUT"
    assert payload["source_dirty_paths"] == ["src/app.py"]


def test_update_refuses_incoming_runtime_collision(tmp_path: Path):
    repo, target = _repo_with_target(tmp_path, target_output='{"state":"target"}\n')
    original_head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _write(repo, "outputs/state.json", '{"state":"runtime"}\n')

    result = _update(repo, target)

    assert result.returncode == 4
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == original_head
    assert (repo / "outputs" / "state.json").read_text(encoding="utf-8") == '{"state":"runtime"}\n'
    payload = json.loads(result.stdout)
    assert payload["incoming_runtime_collisions"] == ["outputs/state.json"]


def test_docker_context_excludes_runtime_corpora_and_secrets():
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    for required in (".git", ".env", "outputs", "work", "data", "tests", "docs", "*.sqlite"):
        assert required in ignored
    assert "inputs" not in ignored

