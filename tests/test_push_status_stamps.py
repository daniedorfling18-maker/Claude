"""WO-121 fix round: the push-status stamps must tell the truth.

The 2026-07-28 deploy log surfaced `anchor_push_status.json` reading "failed"
on a healthy host. Root cause: `push_vps_anchor.sh` initialises
PUSH_STATUS="failed" and only ever set it to "ok" on an actual push - but the
ROUTINE outcome of every scheduled run after the day's first successful push
is the "already present on branch" early exit, which kept the failure default.
Separately, a run that lost the lock race stamped "lock_held" over whatever
the concurrent run had recorded, erasing a fresh "ok".

These tests EXECUTE the real script against a local bare origin instead of
asserting on its text, because a content assertion cannot distinguish an
honest stamp from a wrong one.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ANCHOR_SCRIPT = Path("scripts/push_vps_anchor.sh").resolve()
TELEMETRY_SCRIPT = Path("scripts/push_vps_telemetry.sh")


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _sandbox(tmp_path: Path) -> tuple[Path, Path]:
    """A repo with a file:// bare origin, an anchor head, and a private TMPDIR."""
    origin = tmp_path / "origin.git"
    _git("init", "--bare", str(origin), cwd=tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", cwd=repo)
    _git("remote", "add", "origin", str(origin), cwd=repo)
    head = repo / "outputs" / "performance" / "ledger_anchor_head.json"
    head.parent.mkdir(parents=True)
    head.write_text(
        json.dumps({"anchor_date": "2026-07-29", "chain_head_sha256": "ab" * 32}),
        encoding="utf-8",
    )
    return repo, head


def _run_anchor(repo: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = dict(
        os.environ,
        VPS_ANCHOR_REPO_DIR=str(repo),
        TMPDIR=str(tmp_path / "locks"),
    )
    (tmp_path / "locks").mkdir(exist_ok=True)
    return subprocess.run(
        ["sh", str(ANCHOR_SCRIPT)], cwd=repo, env=env, capture_output=True, text=True
    )


def _stamp(repo: Path) -> dict[str, object]:
    return json.loads(
        (repo / "outputs" / "performance" / "anchor_push_status.json").read_text(encoding="utf-8")
    )


def test_anchor_first_push_stamps_ok_and_already_present_stays_ok(tmp_path: Path) -> None:
    repo, _ = _sandbox(tmp_path)

    first = _run_anchor(repo, tmp_path)
    assert first.returncode == 0, first.stderr
    assert "pushed durable ledger anchor" in first.stdout
    assert _stamp(repo)["status"] == "ok"

    # The routine outcome of every later run that day: identical anchor already
    # durably on the branch. The lane's contract is satisfied - the stamp must
    # say so, not "failed" (which kept the publication-bridge incident lit).
    second = _run_anchor(repo, tmp_path)
    assert second.returncode == 0, second.stderr
    assert "already present" in second.stdout
    assert _stamp(repo)["status"] == "ok"


def test_anchor_divergent_same_date_anchor_still_stamps_failed(tmp_path: Path) -> None:
    # No blanket success: a same-date anchor with DIFFERENT bytes is a genuine
    # anomaly (the script refuses to replace it) and must keep reading failed.
    repo, head = _sandbox(tmp_path)
    assert _run_anchor(repo, tmp_path).returncode == 0

    head.write_text(
        json.dumps({"anchor_date": "2026-07-29", "chain_head_sha256": "cd" * 32}),
        encoding="utf-8",
    )
    third = _run_anchor(repo, tmp_path)
    assert third.returncode == 0
    assert "refusing to replace" in third.stderr
    assert _stamp(repo)["status"] == "failed"


def test_anchor_lock_loser_never_clobbers_the_winners_stamp(tmp_path: Path) -> None:
    repo, _ = _sandbox(tmp_path)
    locks = tmp_path / "locks"
    locks.mkdir(exist_ok=True)
    (locks / "push_vps_anchor.lock").mkdir()

    stamp_path = repo / "outputs" / "performance" / "anchor_push_status.json"
    winner = (
        '{"generated_at_utc":"2026-07-29T09:00:00Z","status":"ok",'
        '"paper_trading_invoked":false,"live_trading_invoked":false}\n'
    )
    stamp_path.write_text(winner, encoding="utf-8")

    result = _run_anchor(repo, tmp_path)
    assert result.returncode == 0
    assert "another anchor push is running" in result.stderr
    assert stamp_path.read_text(encoding="utf-8") == winner, (
        "a lock-race loser must leave the concurrent run's stamp untouched"
    )


def test_telemetry_lock_loser_uses_the_same_no_clobber_seam() -> None:
    # The telemetry sibling shares the mechanism proven above behaviourally;
    # executing it end-to-end needs the credential guard and manifest writer,
    # so pin the seam itself: the lock-race branch disables stamping instead of
    # writing a "lock_held" status over the winner's outcome.
    text = TELEMETRY_SCRIPT.read_text(encoding="utf-8")
    assert 'STAMP_ENABLED=0' in text
    assert 'PUSH_STATUS="lock_held"' not in text
    assert '[ "$STAMP_ENABLED" = "1" ] || return 0' in text
    anchor_text = Path("scripts/push_vps_anchor.sh").read_text(encoding="utf-8")
    assert 'PUSH_STATUS="lock_held"' not in anchor_text
