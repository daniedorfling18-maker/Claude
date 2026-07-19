from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_canonical_front_doors_are_vps_only() -> None:
    agents = _text("AGENTS.md")
    claude = _text("CLAUDE.md")
    readme = _text("README.md")
    current_state = _text("docs/POLYMARKET_CURRENT_STATE.md")

    for name, text in {
        "AGENTS.md": agents,
        "CLAUDE.md": claude,
        "README.md": readme,
    }.items():
        assert "VPS only" in text or "VPS-only" in text, name
        assert "local-first" not in text.lower(), name

    assert "Do not start any of the following on the local workstation" in agents
    assert "docker-compose.vps-paper.yml" in agents
    assert "outputs/performance/operating_state.json" in agents
    assert "manually maintained snapshot formerly stored here was retired" in current_state


def test_legacy_local_runbooks_are_loudly_archived() -> None:
    expected_markers = {
        "docs/POLYMARKET_SHADOW_RESEARCH_RUNBOOK.md": "Retired local runbook",
        "docs/POLYMARKET_RESEARCH_README.md": "Historical snapshot",
        "docs/POLYMARKET_PAPER_TRADING_LOOP.md": "Retired local runbook",
        "docs/RUNNING_LEAN.md": "Historical local-capacity guide",
        "docs/POLYMARKET_RUNTIME_CONTEXT_20260628.md": "Archived context",
    }

    for path, marker in expected_markers.items():
        first_lines = "\n".join(_text(path).splitlines()[:10])
        assert marker in first_lines, path
        assert "AGENTS.md" in first_lines, path


def test_work_order_queue_distinguishes_non_buildable_states() -> None:
    work_orders = _text("docs/POLYMARKET_CODEX_WORK_ORDERS.md")

    assert "Current queue for Codex (reconciled 2026-07-19)" in work_orders
    assert "remains CLOSED and WO-67 remains BLOCKED" in work_orders
    assert "Each scope requires its own PR" in work_orders
    assert "Never install dependencies, run the engine, or run tests on the local" in work_orders
    assert "complete unfiltered `python -m pytest -q`" in work_orders
    assert "bounded, isolated VPS" in work_orders
    assert 'Run `pip install -e ".[dev]"` once, then `pytest` before pushing' not in work_orders
    assert "This queue reports accepted and open work; it does not grant authorization" in work_orders
    assert "every frozen/registered-surface change" in work_orders
    assert "owner-authored commit or an owner-approved pull" in work_orders
    assert "**WO-100 — ACCEPTED BUT BLOCKED:**" in work_orders
    assert "**WO-101 — ACCEPTED DIAGNOSTIC:**" in work_orders
    assert "registered sharp venue keys" in work_orders
    assert "PR #305 therefore proposes removal" in work_orders
    assert "Review corrections #295–#298 and #302–#337 remain open" in work_orders
    assert "accepted `AGENTS.md` names" in work_orders
    assert "there is no separate" in work_orders
    assert "explicit owner-authored approval text" in work_orders
    assert "accepts assignments only from direct" in work_orders
    assert "WO-105 threshold-pin" in work_orders
    assert "current proposal" in work_orders
    assert "is not buildable" in work_orders
    assert "a corrected, disclosed bridge later reached `main`" not in work_orders
    assert "under the owner's 2026-07-19 instruction" not in work_orders
    assert "PR #295 corrects independent-workflow revision" in work_orders
    assert "Accepted `main` does not yet satisfy that audit element" in work_orders
    assert "#298, stacked after #297" in work_orders
    assert "Dashboard transport — CODE ACCEPTED, NOT YET PRODUCTION-PROVEN" in work_orders
    assert "PR #287" in work_orders
    assert "merged into the stacked #286 branch" in work_orders
    assert "**WO-111 — PROPOSED, NOT ACCEPTED:**" in work_orders
    assert "provisional WO-109" in work_orders
    assert "remains unregistered" in work_orders
    assert "Accepted `main` through PR #269" not in work_orders
    assert "pending independent review/merge" not in work_orders
    assert "WO-98 is implemented and awaiting publication" not in work_orders
    assert "Next buildable: WO-92" not in work_orders
    wo92_heading = work_orders.split("## WO-92", 1)[1].splitlines()[0]
    assert "done (2026-07-15, PR #234)" in wo92_heading
    assert "ENGINEERING_STANDARDS.md" in work_orders
    assert "ROOT CAUSE CORRECTED by line audit" in work_orders
    assert "WO-33/34/35 remain governed by WO-101's leakage" in work_orders
    assert "WO-48, WO-67, WO-73 item 4, and WO-75 item 2 remain" in work_orders
    assert "WO-70 and WO-72 remain deferred" in work_orders
    assert "WO-76 remains registration-only" in work_orders
    assert "Queue now: **WO-80**" not in work_orders

    wo91 = work_orders.split("## WO-91", 1)[1].split("## WO-90", 1)[0]
    assert "Fail-safe direction:" in wo91
    assert "Day-after check:" in wo91
    assert "recorded CLOB `/prices-history` payload" in wo91
