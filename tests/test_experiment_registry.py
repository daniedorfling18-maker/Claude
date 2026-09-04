from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def test_research_surface_is_frozen_to_exactly_three_registered_primaries() -> None:
    registry = (ROOT / "docs" / "EXPERIMENT_REGISTRY.md").read_text(encoding="utf-8")
    headings = re.findall(r"^## H\d+ — PRIMARY: (.+)$", registry, flags=re.MULTILINE)

    assert headings == [
        "Sharp-anchor maker carry",
        "Persistent dutch-book consistency opportunities",
        "Structural-bias / smart-flow cohorts with positive CLV",
    ]
    assert "Exactly three primary hypotheses are permitted" in registry
    assert re.search(r"strictly after\s+that merge commit", registry)
    assert "not a fourth primary" in registry.lower()


def test_agent_front_door_enforces_registry_without_repeating_dynamic_state() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8-sig")

    assert "docs/EXPERIMENT_REGISTRY.md" in agents
    assert "frozen to exactly the three primary hypotheses" in agents
