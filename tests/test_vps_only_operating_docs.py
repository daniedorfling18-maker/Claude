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
    assert "**WO-100:** rebuild closed PR #243 from current main" in work_orders
    assert "**WO-101:** rebuild closed PR #242 from current main" in work_orders
    assert "fix exact-token identity, sharp-source" in work_orders
    assert "implement an actual tested rollback" in work_orders
    assert "authenticated HTTPS or a private VPN" in work_orders
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
