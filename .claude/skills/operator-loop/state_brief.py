#!/usr/bin/env python3
"""One-page decision state for the operator tick. Read-only.

Reads Git and the repository's own documents. With network it also asks GitHub
for open pull requests and the required gate's last run, through `gh api`.
It starts nothing, writes nothing, and never contacts a venue. Every section
degrades to "unavailable" rather than failing, because a brief that crashes on
one missing source is a brief nobody reads.

    python3 .claude/skills/operator-loop/state_brief.py [--no-network] [--repo PATH]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

REGISTER = "docs/POLYMARKET_CODEX_WORK_ORDERS.md"
EVIDENCE = "docs/EVIDENCE_STATE_2026-09-13.md"
THESIS = "docs/VENTURE_THESIS.md"
CHARTER = "docs/POLYMARKET_QUANT_MODE_CHARTER.md"
ESCALATIONS = ".claude/skills/operator-loop/escalations.md"
GITHUB_REPO = "daniedorfling18-maker/Claude"
GATE_WORKFLOW = "required-pr-gate.yml"
TERMINAL_VERDICT_DATE = dt.date(2026, 8, 19)

STATUS_LINE = re.compile(r"^## (WO-\S+) — (.+) — `([A-Za-z-]+)`", re.M)
EVIDENCE_ROW = re.compile(r"^\| `([^`]+)` \| `([^`]+)` \|", re.M)


def sh(args: list[str], cwd: Path, timeout: int = 60) -> str:
    out = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError((out.stderr or out.stdout).strip().splitlines()[-1:] or ["non-zero exit"])
    return out.stdout.strip()


def days_since(iso: str, today: dt.date) -> int:
    return (today - dt.date.fromisoformat(iso[:10])).days


def section(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def repository(root: Path, today: dt.date, network: bool) -> None:
    section("REPOSITORY")
    try:
        if network:
            sh(["git", "fetch", "-q", "origin", "main"], root, timeout=120)
        sha = sh(["git", "rev-parse", "--short", "origin/main"], root)
        date = sh(["git", "log", "-1", "--format=%cs", "origin/main"], root)
        print(f"main            {sha}  {date}  ({days_since(date, today)} days old)")
    except Exception as exc:  # noqa: BLE001
        print(f"main            unavailable: {exc}")
    try:
        branch = sh(["git", "branch", "--show-current"], root) or "(detached)"
        ahead = sh(["git", "rev-list", "--count", "origin/main..HEAD"], root)
        behind = sh(["git", "rev-list", "--count", "HEAD..origin/main"], root)
        print(f"checkout        {branch}  +{ahead} / -{behind} vs main")
    except Exception as exc:  # noqa: BLE001
        print(f"checkout        unavailable: {exc}")
    if network:
        try:
            heads = sh(["git", "ls-remote", "--heads", "origin"], root, timeout=120).splitlines()
            print(f"remote branches {len(heads)}")
        except Exception as exc:  # noqa: BLE001
            print(f"remote branches unavailable: {exc}")


def landing(root: Path, today: dt.date, network: bool) -> None:
    section("LANDING")
    if not network:
        print("skipped (--no-network)")
        return
    try:
        raw = sh(["gh", "api", f"repos/{GITHUB_REPO}/pulls?state=open&per_page=50"], root, timeout=60)
        prs = json.loads(raw)
        print(f"open pull requests  {len(prs)}")
        for p in sorted(prs, key=lambda p: p["created_at"]):
            age = days_since(p["created_at"], today)
            print(f"  #{p['number']:<4} {age:>3}d  {p['title'][:70]}")
    except Exception as exc:  # noqa: BLE001
        print(f"open pull requests  unavailable: {exc}")
    try:
        raw = sh(["gh", "api", f"repos/{GITHUB_REPO}/actions/workflows/{GATE_WORKFLOW}/runs?per_page=1"], root, timeout=60)
        run = json.loads(raw)["workflow_runs"][0]
        print(f"required gate       last run {run['status']}/{run.get('conclusion')}  created {run['created_at'][:10]}  ({days_since(run['created_at'], today)} days ago)")
    except Exception as exc:  # noqa: BLE001
        print(f"required gate       unavailable: {exc}")


def work_orders(root: Path) -> None:
    section("WORK ORDERS (register)")
    try:
        text = (root / REGISTER).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"unavailable: {exc}")
        return
    entries = STATUS_LINE.findall(text)
    counts: dict[str, int] = {}
    for _, _, status in entries:
        counts[status] = counts.get(status, 0) + 1
    print("by status       " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])))
    print("most recent")
    for wo, title, status in entries[-6:]:
        print(f"  {wo:<9} {status:<14} {title[:60]}")


def evidence(root: Path) -> None:
    section("EVIDENCE (last VPS snapshot, 2026-08-21)")
    try:
        text = (root / EVIDENCE).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"unavailable: {exc}")
        return
    rows = EVIDENCE_ROW.findall(text)
    if not rows:
        print("no rows parsed")
    for line, cls in rows:
        print(f"  {line:<40} {cls}")


def telemetry(root: Path, network: bool) -> None:
    section("TELEMETRY MIRROR")
    if not network:
        print("skipped (--no-network)")
        return
    try:
        out = sh(["git", "ls-remote", "origin", "refs/heads/vps-telemetry"], root, timeout=120)
        sha = out.split()[0][:7] if out else "absent"
        print(f"origin/vps-telemetry  {sha}  (fetch the ref to date it; last known movement 2026-08-21)")
    except Exception as exc:  # noqa: BLE001
        print(f"origin/vps-telemetry  unavailable: {exc}")


def thesis(root: Path, today: dt.date) -> None:
    section("VENTURE THESIS")
    try:
        charter = (root / CHARTER).read_text(encoding="utf-8")
        verdict_no = "no_for_tested_edge_classes" in charter
    except OSError:
        verdict_no = None
    exists = (root / THESIS).is_file()
    print(f"document        {'present' if exists else 'MISSING'} ({THESIS})")
    if verdict_no:
        elapsed = (today - TERMINAL_VERDICT_DATE).days
        print(f"outcome         B — terminal verdict NO on {TERMINAL_VERDICT_DATE}; {elapsed} days elapsed")
        print("trigger         no new trading lanes for 30 days; then customer discovery; then a WRITTEN decision")
        print(f"windows         first 30 days {'elapsed' if elapsed > 30 else 'running'}; second 30 days {'elapsed' if elapsed > 60 else 'running' if elapsed > 30 else 'not started'}")
    else:
        print("outcome         undetermined from the charter")


def escalations(root: Path) -> None:
    section("ESCALATIONS (owner decisions pending)")
    try:
        text = (root / ESCALATIONS).read_text(encoding="utf-8")
    except OSError:
        print("none recorded (file absent)")
        return
    open_items = [l for l in text.splitlines() if l.startswith("- [ ]")]
    done_items = [l for l in text.splitlines() if l.startswith("- [x]")]
    print(f"open {len(open_items)}   decided {len(done_items)}")
    for item in open_items:
        print(f"  {item[6:][:100]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-network", action="store_true", help="Git and GitHub reads that need the network are skipped")
    parser.add_argument("--repo", type=Path, default=None, help="repository root (default: derived from this file)")
    args = parser.parse_args(argv)
    root = (args.repo or Path(__file__).resolve().parents[3]).resolve()
    today = dt.date.today()
    print(f"OPERATOR STATE BRIEF  {today}  {root.name}")
    repository(root, today, not args.no_network)
    landing(root, today, not args.no_network)
    work_orders(root)
    evidence(root)
    telemetry(root, not args.no_network)
    thesis(root, today)
    escalations(root)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
