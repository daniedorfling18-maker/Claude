from __future__ import annotations

from collections.abc import Iterable, Mapping
import re
from typing import Any


POLICY_VERSION = "wo95_primary_hypothesis_discovery_v1"
FROZEN_UPDOWN_REASON = "frozen_crypto_updown_no_collection_priority"
PRIMARY_HYPOTHESES = (
    "h1_sharp_anchor",
    "h2_dutch_consistency",
    "h3_structural_smart_flow",
)
DEFAULT_PRIMARY_HYPOTHESIS_QUERIES: dict[str, tuple[str, ...]] = {
    "h1_sharp_anchor": ("world cup", "tennis", "nba", "mlb", "mma"),
    "h2_dutch_consistency": ("sports",),
    "h3_structural_smart_flow": ("politics", "elections", "fed", "economy", "stocks"),
}

_UPDOWN_RE = re.compile(
    r"(?<![a-z0-9])up(?:[\s/_-]*|[\s/_-]+or[\s/_-]+)down(?![a-z0-9])",
    re.IGNORECASE,
)
_RECORD_TEXT_FIELDS = (
    "event_slug",
    "event_title",
    "market_slug",
    "question",
    "category",
    "family",
    "signal_cohort",
    "cohort",
)


def query_key(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def is_frozen_updown_text(*values: Any) -> bool:
    return bool(_UPDOWN_RE.search(" ".join(str(value or "") for value in values)))


def is_frozen_updown_record(row: Any) -> bool:
    if isinstance(row, Mapping):
        values = [row.get(field) for field in _RECORD_TEXT_FIELDS]
    else:
        values = [getattr(row, field, None) for field in _RECORD_TEXT_FIELDS]
    return is_frozen_updown_text(*values)


def partition_active_queries(
    values: Iterable[Any],
    *,
    allow_blank: bool = False,
) -> tuple[list[str], list[dict[str, str]]]:
    allowed: list[str] = []
    excluded: list[dict[str, str]] = []
    seen_allowed: set[str] = set()
    seen_excluded: set[str] = set()
    for value in values:
        query = str(value or "").strip()
        key = query_key(query) or "__top_active__"
        if not query and not allow_blank:
            continue
        if is_frozen_updown_text(query):
            if key not in seen_excluded:
                excluded.append({"query": query, "reason": FROZEN_UPDOWN_REASON})
                seen_excluded.add(key)
            continue
        if key in seen_allowed:
            continue
        allowed.append(query)
        seen_allowed.add(key)
    return allowed, excluded


def _surface_settings(raw: Mapping[str, Any] | None) -> Mapping[str, Any]:
    raw = raw or {}
    nested = raw.get("research_surface_discovery")
    return nested if isinstance(nested, Mapping) else raw


def primary_hypothesis_lanes(raw: Mapping[str, Any] | None) -> dict[str, list[str]]:
    settings = _surface_settings(raw)
    configured = settings.get("primary_hypothesis_queries")
    configured = configured if isinstance(configured, Mapping) else {}
    lanes: dict[str, list[str]] = {}
    for hypothesis in PRIMARY_HYPOTHESES:
        values = configured.get(hypothesis, DEFAULT_PRIMARY_HYPOTHESIS_QUERIES[hypothesis])
        if not isinstance(values, (list, tuple)):
            values = [values]
        allowed, _excluded = partition_active_queries(values)
        if not allowed:
            allowed = list(DEFAULT_PRIMARY_HYPOTHESIS_QUERIES[hypothesis])
        lanes[hypothesis] = allowed
    return lanes


def primary_hypothesis_targets(
    raw: Mapping[str, Any] | None,
    *,
    scan_sequence: int,
) -> dict[str, str]:
    lanes = primary_hypothesis_lanes(raw)
    offset = max(0, int(scan_sequence) - 1)
    targets: dict[str, str] = {}
    used: set[str] = set()
    for hypothesis in PRIMARY_HYPOTHESES:
        candidates = lanes[hypothesis]
        start = offset % len(candidates)
        for candidate_offset in range(len(candidates)):
            candidate = candidates[(start + candidate_offset) % len(candidates)]
            key = query_key(candidate)
            if key and key not in used:
                targets[hypothesis] = candidate
                used.add(key)
                break
    return targets


def primary_hypothesis_coverage(
    selected_queries: Iterable[Any],
    raw: Mapping[str, Any] | None,
) -> dict[str, Any]:
    lanes = primary_hypothesis_lanes(raw)
    selected, _excluded = partition_active_queries(selected_queries)
    selected_keys = {query_key(query) for query in selected}
    by_hypothesis = {
        hypothesis: [query for query in lanes[hypothesis] if query_key(query) in selected_keys]
        for hypothesis in PRIMARY_HYPOTHESES
    }
    missing = [hypothesis for hypothesis in PRIMARY_HYPOTHESES if not by_hypothesis[hypothesis]]
    return {
        "policy_version": POLICY_VERSION,
        "required_hypotheses": list(PRIMARY_HYPOTHESES),
        "queries_by_hypothesis": by_hypothesis,
        "missing_hypotheses": missing,
        "status": "ok" if not missing else "starved",
    }
