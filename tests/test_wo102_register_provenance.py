from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_wo102_register_records_provenance_without_claiming_authorization() -> None:
    work_orders = (
        ROOT / "docs" / "POLYMARKET_CODEX_WORK_ORDERS.md"
    ).read_text(encoding="utf-8")
    section = work_orders.split("## WO-102", 1)[1].split("## WO-99", 1)[0]
    lowered = section.lower()

    assert "owner-approved" not in lowered
    assert "owner approved" not in lowered
    assert "does not assert or grant" in lowered
    assert "funding remains closed and wo-67 remains blocked" in lowered
