from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]

# The seven elements the freeze paragraph names, transcribed from it verbatim:
# "an economic mechanism, independent unit, sample floor, cost model,
# multiple-test correction, stopping rule, and promotion/abandonment rule".
#
# An earlier revision of this list silently dropped "multiple-test correction"
# and inserted "Support gate" in its place. That made the list a description of
# what the new primaries happened to contain rather than a check against the
# standard, so it could not fail — and it omitted precisely the element both new
# primaries were missing, the selective-reporting control this registry exists
# to enforce. The list is now derived from the freeze paragraph.
FREEZE_PARAGRAPH_ELEMENTS = (
    "- Economic mechanism:",
    "- Independent unit:",
    "- Sample floor:",
    "- Cost model:",
    "- Multiple-test correction:",
    "- Stopping rule:",
    "- Promotion / abandonment:",
)

# House-shape elements required in addition to the freeze paragraph's seven:
# the support gate every primary is read through, and the A11 disclosure S8
# added after two successive estimators were each found incapable of measuring
# what they claimed.
ADDITIONAL_REQUIRED_ELEMENTS = (
    "- Support gate:",
    "- A11 bias-direction disclosure:",
)

REQUIRED_PRIMARY_ELEMENTS = FREEZE_PARAGRAPH_ELEMENTS + ADDITIONAL_REQUIRED_ELEMENTS

# Primaries added after the original 2026-07-12 freeze. Hypotheses 1-3 predate
# both the seven-element requirement and A11, so they are not held to the
# element check retroactively — back-applying it would be the retroactive
# rewriting this registry exists to prevent.
POST_FREEZE_PRIMARIES = ("H5", "H6")


def test_research_surface_is_frozen_to_exactly_five_registered_primaries() -> None:
    registry = (ROOT / "docs" / "EXPERIMENT_REGISTRY.md").read_text(encoding="utf-8")
    headings = re.findall(r"^## (H\d+) — PRIMARY: (.+)$", registry, flags=re.MULTILINE)

    assert headings == [
        ("H1", "Sharp-anchor maker carry"),
        ("H2", "Persistent dutch-book consistency opportunities"),
        ("H3", "Structural-bias / smart-flow cohorts with positive CLV"),
        ("H5", "Variance risk premium on crypto options"),
        ("H6", "Perpetual funding carry"),
    ]
    assert "2026-09-04 amendment below raises that to five" in registry
    assert re.search(r"strictly after\s+that merge commit", registry)
    assert "not a fourth primary" in registry.lower()


def test_every_post_freeze_primary_carries_the_required_elements() -> None:
    """A primary added after the freeze must carry all seven required elements
    plus the A11 bias-direction disclosure, or it is not registerable."""
    registry = (ROOT / "docs" / "EXPERIMENT_REGISTRY.md").read_text(encoding="utf-8")
    sections = re.split(r"^## ", registry, flags=re.MULTILINE)

    for label in POST_FREEZE_PRIMARIES:
        matching = [s for s in sections if s.startswith(f"{label} — PRIMARY:")]
        assert len(matching) == 1, f"{label} must appear exactly once as a primary"
        body = matching[0]
        missing = [e for e in REQUIRED_PRIMARY_ELEMENTS if e not in body]
        assert not missing, f"{label} is missing required elements: {missing}"


def _elements_named_by_the_freeze_paragraph(registry: str) -> tuple[str, ...]:
    """Derive the required element headings from the freeze paragraph itself.

    The freeze paragraph is the standard. Parsing it here — rather than writing
    the list out by hand — is what stops the guard being recalibrated to
    whatever the current primaries happen to contain, which is the defect this
    function exists to close.
    """
    flat = " ".join(registry.split())
    match = re.search(r"including an (.+?rule)\.", flat)
    assert match, "freeze paragraph no longer states the required elements"

    # "an economic mechanism, independent unit, ..., and promotion/abandonment rule"
    named = [part.strip() for part in match.group(1).split(",")]
    named = [re.sub(r"^(an|and) ", "", part) for part in named]

    # Prose name -> the bullet heading a primary must carry. Every prose name
    # must appear here; an unmapped one fails rather than being skipped.
    HEADINGS = {
        "economic mechanism": "- Economic mechanism:",
        "independent unit": "- Independent unit:",
        "sample floor": "- Sample floor:",
        "cost model": "- Cost model:",
        "multiple-test correction": "- Multiple-test correction:",
        "stopping rule": "- Stopping rule:",
        "promotion/abandonment rule": "- Promotion / abandonment:",
    }
    unmapped = [n for n in named if n not in HEADINGS]
    assert not unmapped, f"freeze paragraph names elements with no heading: {unmapped}"
    return tuple(HEADINGS[n] for n in named)


def test_required_element_list_is_derived_from_the_freeze_paragraph() -> None:
    """FREEZE_PARAGRAPH_ELEMENTS must equal what the freeze paragraph names.

    Mutating the module tuple — for instance dropping "multiple-test
    correction" and inserting "Support gate", which is the exact defect a
    previous revision shipped — must fail this test.
    """
    registry = (ROOT / "docs" / "EXPERIMENT_REGISTRY.md").read_text(encoding="utf-8")
    derived = _elements_named_by_the_freeze_paragraph(registry)

    assert FREEZE_PARAGRAPH_ELEMENTS == derived, (
        "the element list no longer matches the freeze paragraph it cites: "
        f"list={FREEZE_PARAGRAPH_ELEMENTS} paragraph={derived}"
    )


def test_post_freeze_primaries_are_anti_retroactive() -> None:
    """Each new primary must state that pre-merge observations do not count,
    which is what stops a hypothesis being registered around data already seen.

    Matching is whitespace-normalised: these are wrapped markdown prose, so a
    literal match would assert on line-wrapping rather than on meaning.
    """
    registry = (ROOT / "docs" / "EXPERIMENT_REGISTRY.md").read_text(encoding="utf-8")
    sections = re.split(r"^## ", registry, flags=re.MULTILINE)

    for label in POST_FREEZE_PRIMARIES:
        body = next(s for s in sections if s.startswith(f"{label} — PRIMARY:"))
        flat = " ".join(body.lower().split())
        assert "registered by the merge of this amendment" in flat
        assert "no collection before that merge counts" in flat


def test_agent_front_door_enforces_registry_without_repeating_dynamic_state() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8-sig")

    assert "docs/EXPERIMENT_REGISTRY.md" in agents
    assert "frozen to exactly the five primary hypotheses" in agents
