"""Sharp-odds anchor: turn a sharper independent market into the mispricing-alpha fundamental.

The mispricing-alpha overlay can consume a "fundamental" probability per outcome token, but the
default source (``repo_worldcup_winner_probabilities.csv``) is derived from de-vigged *bookmaker
consensus* - which is essentially the same estimate the Polymarket price already reflects, so it
cannot add edge (betting consensus into a liquid line is -vig).

This module bridges a genuinely *sharper* book - Pinnacle / Betfair Exchange closing or in-play
odds, which are the sharpest public probabilities available - into that slot:

  raw sharp odds  ->  remove the vig (de-vig within each mutually-exclusive market)
                  ->  join to the Polymarket outcome token
                  ->  write token_id,probability in the fundamental contract.

Point the config's ``mispricing_alpha.fundamental_probability_paths`` at the output and the existing
haircut + cross-check machinery treats it as the sharp anchor. The de-vig and join are pure and
unit-tested; the network/odds acquisition is left to the operator (provide an odds CSV or wire an
``external_feeds`` URL) so this stays a deterministic, leakage-free transform.
"""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import re
from typing import Any, Iterable

import requests

from .config import EngineConfig, load_config
from .utils import (
    find_first_column,
    normalize_slug,
    now_utc,
    read_csv_rows,
    safe_float,
    write_csv,
    write_json,
)

GROUP_FIELDS = ["market_slug", "market_id", "condition_id", "event_slug", "event", "group", "market"]
OUTCOME_FIELDS = ["outcome", "selection", "team", "runner", "name", "question"]
DECIMAL_ODDS_FIELDS = ["decimal_odds", "odds", "price_decimal", "decimal"]
IMPLIED_FIELDS = ["implied_probability", "implied", "fair_probability", "probability"]
TOKEN_FIELDS = ["token_id", "asset_id", "clob_token_id"]
TEAM_ALIASES = {
    "usa": "unitedstates",
    "us": "unitedstates",
    "unitedstatesofamerica": "unitedstates",
    "czech": "czechia",
    "czechrepublic": "czechia",
    "bosnia": "bosniaandherzegovina",
    "bosniaherzegovina": "bosniaandherzegovina",
    "cotedivoire": "ivorycoast",
    "congodr": "drcongo",
    "drc": "drcongo",
    "democraticrepublicofcongo": "drcongo",
    "capeverde": "capeverde",
    "curaao": "curacao",
}
CONFEDERATION_TEAM_KEYS = {
    "africa",
    "caf",
    "asia",
    "afc",
    "europe",
    "uefa",
    "northamerica",
    "concacaf",
    "southamerica",
    "conmebol",
    "oceania",
    "ocf",
    "ofc",
}
DEFAULT_GAMMA_PUBLIC_SEARCH = "https://gamma-api.polymarket.com/public-search"
DEFAULT_PUBLIC_SEARCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Origin": "https://polymarket.com",
    "Referer": "https://polymarket.com/",
}


# --------------------------------------------------------------------------- pure de-vig math
def implied_from_decimal(odds: float | None) -> float | None:
    """Bookmaker-implied probability from decimal odds (``1/odds``); None if odds <= 1."""
    if odds is None or odds <= 1.0:
        return None
    return 1.0 / odds


def devig_multiplicative(raw: list[float]) -> list[float]:
    """Proportionally rescale implied probabilities so they sum to 1 (removes the overround)."""
    total = sum(raw)
    if total <= 0:
        return list(raw)
    return [r / total for r in raw]


def devig_power(raw: list[float], *, tol: float = 1e-10, max_iter: int = 200) -> list[float]:
    """Power-method de-vig: find ``k >= 1`` with ``sum(b_i**k) = 1`` and return ``b_i**k``.

    Each ``b_i = 1/odds < 1``, so raising to a larger power shrinks the sum monotonically from the
    overround (sum > 1 at k=1) down to 1. This deflates favourites less than longshots, which fits
    the empirical favourite-longshot bias better than a flat proportional rescale.
    """
    clipped = [max(1e-12, min(1 - 1e-12, r)) for r in raw]
    if sum(clipped) <= 1.0:                       # no overround to remove
        return devig_multiplicative(clipped)
    lo, hi = 1.0, 50.0
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        total = sum(r ** mid for r in clipped)
        if abs(total - 1.0) < tol:
            break
        if total > 1.0:
            lo = mid
        else:
            hi = mid
    k = (lo + hi) / 2.0
    powered = [r ** k for r in clipped]
    total = sum(powered)
    return [p / total for p in powered] if total > 0 else powered


def devig(raw: list[float], method: str = "multiplicative") -> list[float]:
    return devig_power(raw) if str(method).lower() == "power" else devig_multiplicative(raw)


# --------------------------------------------------------------------------- token resolution
def _match_key(group: str, outcome: str) -> str:
    return f"{normalize_slug(group)}::{normalize_slug(outcome)}"


def _team_key(value: object) -> str:
    key = re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
    return TEAM_ALIASES.get(key, key)


def _is_confederation_token(value: object) -> bool:
    text = str(value or "").lower()
    key = _team_key(value)
    return key in CONFEDERATION_TEAM_KEYS or any(marker in text for marker in ("(caf)", "(afc)", "(uefa)", "(concacaf)", "(conmebol)", "(ocf)", "(ofc)"))


def _team_from_worldcup_question(value: object) -> str:
    match = re.match(r"\s*Will\s+(.*?)\s+win\s+the\s+2026\s+FIFA\s+World\s+Cup\?\s*$", str(value or ""), re.I)
    return match.group(1).strip() if match else ""


def _match_event_slug(home: object, away: object) -> str:
    return normalize_slug(f"{home} vs {away}")


def _match_subject_from_question(value: object) -> tuple[str, str] | None:
    """Return (subject team, opponent team) only for clear binary match-win questions."""
    text = str(value or "").strip()
    patterns = [
        r"^Will\s+(.*?)\s+beat\s+(.*?)(?:\?|$)",
        r"^Will\s+(.*?)\s+defeat\s+(.*?)(?:\?|$)",
        r"^Will\s+(.*?)\s+win\s+against\s+(.*?)(?:\?|$)",
    ]
    for pattern in patterns:
        match = re.match(pattern, text, re.I)
        if match:
            return match.group(1).strip(), match.group(2).strip()
    return None


def _event_teams_from_text(value: object) -> tuple[str, str] | None:
    text = str(value or "").strip()
    if not text:
        return None
    compact = re.sub(r"\s+", " ", text)
    patterns = [
        r"^(.*?)\s+vs\.?\s+(.*?)(?:\s+\d{4}-\d{2}-\d{2})?$",
        r"^(.*?)\s+v\.?\s+(.*?)(?:\s+\d{4}-\d{2}-\d{2})?$",
        r"^(.*?)-vs-(.*?)(?:-\d{4}-\d{2}-\d{2})?$",
    ]
    for pattern in patterns:
        match = re.match(pattern, compact, re.I)
        if match:
            left = match.group(1).replace("-", " ").strip(" .")
            right = match.group(2).replace("-", " ").strip(" .")
            if left and right:
                return left, right
    return None


def _event_teams_from_question(value: object) -> tuple[str, str] | None:
    text = str(value or "").strip()
    patterns = [
        r"^Who\s+will\s+win\s+(.*?)\s+vs\.?\s+(.*?)(?:\?|$)",
        r"^Who\s+will\s+win\s+(.*?)\s+v\.?\s+(.*?)(?:\?|$)",
        r"^Will\s+(.*?)\s+vs\.?\s+(.*?)\s+end\s+in\s+a\s+draw(?:\?|$)",
        r"^Will\s+(.*?)\s+v\.?\s+(.*?)\s+end\s+in\s+a\s+draw(?:\?|$)",
        r"^Will\s+(.*?)\s+(?:advance|advances|qualify|qualifies|go\s+through|progress)\s+against\s+(.*?)(?:\?|$)",
    ]
    for pattern in patterns:
        match = re.match(pattern, text, re.I)
        if match:
            left = match.group(1).strip(" .")
            right = match.group(2).strip(" .")
            if left and right:
                return left, right
    return None


def _h2h_market_shape_blocker(row: dict[str, Any]) -> str:
    text = " ".join(
        str(row.get(key) or "")
        for key in ("question", "market_slug", "slug", "market", "group", "event", "event_slug")
    ).lower()
    if re.search(r"\b(advance|advances|qualify|qualifies|qualification|go through|progress)\b", text):
        return "advance_market_needs_composite_fair"
    if re.search(r"\b(spread|handicap|total|over|under|goals?|points?|corners?|cards?)\b", text):
        return "ambiguous_market_shape"
    return ""


def _unique_pairs(values: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for group, outcome in values:
        key = (str(group), str(outcome))
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        unique.append(key)
    return unique


def _public_search_headers(settings: dict[str, Any]) -> dict[str, str]:
    headers = dict(DEFAULT_PUBLIC_SEARCH_HEADERS)
    configured = settings.get("public_search_headers")
    if isinstance(configured, dict):
        headers.update({str(key): str(value) for key, value in configured.items() if value is not None})
    return headers


def _json_list(value: object) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _worldcup_winner_tokens_from_public_search(settings: dict[str, Any]) -> dict[str, str]:
    if not settings.get("worldcup_public_search_enabled", False):
        return {}
    base_url = str(settings.get("worldcup_public_search_url") or DEFAULT_GAMMA_PUBLIC_SEARCH)
    timeout = int(settings.get("worldcup_public_search_timeout_seconds", 20) or 20)
    queries = settings.get("worldcup_public_search_queries") or ["fifa world cup", "world cup winner"]
    mapping: dict[str, str] = {}
    for query in queries:
        try:
            response = requests.get(
                base_url,
                params={
                    "q": str(query),
                    "events_status": "active",
                    "limit_per_type": str(settings.get("worldcup_public_search_limit_per_type", 20) or 20),
                    "search_tags": "false",
                    "search_profiles": "false",
                },
                headers=_public_search_headers(settings),
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:  # noqa: BLE001 - public-search fallback is optional
            continue
        events = payload.get("events", []) if isinstance(payload, dict) else []
        for event in events if isinstance(events, list) else []:
            event_slug = str(event.get("slug") or "").lower()
            event_title = str(event.get("title") or event.get("question") or "").strip().lower()
            if event_slug != "world-cup-winner" and event_title != "world cup winner":
                continue
            markets = event.get("markets") or []
            for market in markets if isinstance(markets, list) else []:
                if not isinstance(market, dict):
                    continue
                team = _team_from_worldcup_question(market.get("question"))
                if not team or _is_confederation_token(team):
                    continue
                outcomes = [str(item).strip().lower() for item in _json_list(market.get("outcomes"))]
                tokens = [str(item).strip() for item in _json_list(market.get("clobTokenIds"))]
                try:
                    yes_index = outcomes.index("yes")
                except ValueError:
                    yes_index = 0
                token = tokens[yes_index] if yes_index < len(tokens) else ""
                if token:
                    mapping.setdefault(_team_key(team), token)
    return mapping


def _worldcup_winner_token_map(cfg: EngineConfig, settings: dict[str, Any]) -> dict[str, str]:
    """team_key -> YES token_id for Polymarket 2026 World Cup winner markets."""
    path = Path(settings.get("token_map_path") or (cfg.output_root / "polymarket" / "market_snapshot.csv"))
    rows = read_csv_rows(path)
    mapping: dict[str, str] = {}
    for row in rows:
        token = str(row.get("token_id") or row.get("asset_id") or row.get("clob_token_id") or "").strip()
        outcome = str(row.get("outcome") or row.get("selection") or "").strip().lower()
        if not token or outcome not in {"yes", "y"}:
            continue
        team = _team_from_worldcup_question(row.get("question"))
        if not team:
            slug = str(row.get("market_slug") or row.get("slug") or "")
            slug_match = re.match(r"will-(.*?)-win-the-2026-fifa-world-cup(?:-|$)", slug, re.I)
            team = slug_match.group(1).replace("-", " ") if slug_match else ""
        if _is_confederation_token(team):
            continue
        key = _team_key(team)
        if key:
            mapping.setdefault(key, token)

    # The live paper loop now rotates across market families. If the current scanner snapshot is
    # crypto/tennis, it will not contain World Cup winner tokens. Preserve sharp-outright usability
    # by falling back to the repo-built World Cup winner detail file from the last World Cup scan.
    detail_path = cfg.output_root / "polymarket" / "repo_worldcup_winner_probabilities.csv"
    for row in read_csv_rows(detail_path):
        token = str(row.get("token_id") or "").strip()
        key = _team_key(row.get("team"))
        if token and key and not _is_confederation_token(row.get("team")):
            mapping.setdefault(key, token)
    for key, token in _worldcup_winner_tokens_from_public_search(settings).items():
        mapping.setdefault(key, token)
    return mapping


def _looks_like_worldcup_outright(row: dict[str, Any], group: str, market_key: str | None) -> bool:
    haystack = " ".join(
        str(value or "")
        for value in [
            group,
            market_key,
            row.get("market_key"),
            row.get("sport"),
            row.get("sport_title"),
            row.get("market"),
        ]
    ).lower()
    return (
        "world" in haystack
        and "cup" in haystack
        and ("winner" in haystack or "outright" in haystack or "outrights" in haystack)
    )


def _public_search_markets(payload: object) -> list[dict[str, Any]]:
    """Extract market dictionaries from known Polymarket public-search response shapes."""
    if not isinstance(payload, dict):
        return []
    markets: list[dict[str, Any]] = []
    root_markets = payload.get("markets")
    if isinstance(root_markets, list):
        markets.extend(item for item in root_markets if isinstance(item, dict))
    events = payload.get("events")
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict):
                continue
            event_title = event.get("title") or event.get("question") or event.get("slug") or ""
            event_slug = event.get("slug") or ""
            event_markets = event.get("markets")
            if isinstance(event_markets, list):
                for item in event_markets:
                    if not isinstance(item, dict):
                        continue
                    enriched = dict(item)
                    enriched.setdefault("_event_title", event_title)
                    enriched.setdefault("_event_slug", event_slug)
                    markets.append(enriched)
    return markets


def _yes_token_from_market(market: dict[str, Any]) -> str:
    outcomes = [str(item).strip().lower() for item in _json_list(market.get("outcomes"))]
    tokens = [
        str(item).strip()
        for item in _json_list(
            market.get("clobTokenIds")
            or market.get("clob_token_ids")
            or market.get("clobTokenIDs")
            or market.get("tokens")
        )
    ]
    try:
        yes_index = outcomes.index("yes")
    except ValueError:
        yes_index = 0
    return tokens[yes_index] if yes_index < len(tokens) else ""


def _h2h_public_search_queries(rows: Iterable[dict[str, Any]], *, limit: int) -> list[str]:
    if limit <= 0:
        return []
    buckets: dict[str, list[str]] = {}
    bucket_seen: dict[str, set[str]] = {}
    global_seen: set[str] = set()
    sport_order: list[str] = []
    for row in rows:
        market_key = str(row.get("market_key") or row.get("market") or "").lower()
        if market_key != "h2h":
            continue
        slug = str(row.get("market_slug") or row.get("event_slug") or row.get("event") or row.get("group") or "").strip()
        if not slug:
            continue
        query = normalize_slug(slug).replace("-", " ").strip()
        if not query or query in global_seen:
            continue
        sport = str(row.get("sport") or row.get("sport_title") or "unknown").strip() or "unknown"
        if sport not in buckets:
            buckets[sport] = []
            bucket_seen[sport] = set()
            sport_order.append(sport)
        if query in bucket_seen[sport]:
            continue
        buckets[sport].append(query)
        bucket_seen[sport].add(query)
        global_seen.add(query)

    queries: list[str] = []
    index_by_sport = {sport: 0 for sport in sport_order}
    while len(queries) < limit:
        added = False
        for sport in sport_order:
            index = index_by_sport[sport]
            sport_queries = buckets.get(sport, [])
            if index >= len(sport_queries):
                continue
            queries.append(sport_queries[index])
            index_by_sport[sport] = index + 1
            added = True
            if len(queries) >= limit:
                break
        if not added:
            break
    return queries


def _h2h_public_market_keys(market: dict[str, Any]) -> list[tuple[str, str]]:
    question = str(market.get("question") or "").strip()
    event_teams = _event_teams_from_text(market.get("_event_title")) or _event_teams_from_text(market.get("_event_slug"))

    explicit_subject = _match_subject_from_question(question)
    if explicit_subject:
        subject, opponent = explicit_subject
        return [
            (_match_event_slug(subject, opponent), subject),
            (_match_event_slug(opponent, subject), subject),
        ]

    win_match = re.match(r"^Will\s+(.*?)\s+win(?:\s+on\s+\d{4}-\d{2}-\d{2})?(?:\?|$)", question, re.I)
    if win_match and event_teams:
        subject = win_match.group(1).strip()
        left, right = event_teams
        if _team_key(subject) not in {_team_key(left), _team_key(right)}:
            return []
        return [
            (_match_event_slug(left, right), subject),
            (_match_event_slug(right, left), subject),
        ]

    if event_teams and re.search(r"\b(end|finish|result)\b.*\b(in\s+a\s+)?draw\b|\bdraw\?", question, re.I):
        left, right = event_teams
        return [
            (_match_event_slug(left, right), "Draw"),
            (_match_event_slug(right, left), "Draw"),
        ]
    return []


def _h2h_token_map_keys(row: dict[str, Any], outcome: str) -> tuple[list[tuple[str, str]], str]:
    """Return h2h sharp-anchor keys for a local token-map row.

    Local snapshots are preferable to public-search enrichment when they already
    carry the relevant market.  This parser is intentionally conservative: it
    maps only explicit 90-minute/result win-or-draw markets and refuses
    advance/qualify/handicap/total shapes because those need different fairs.
    """
    blocker = _h2h_market_shape_blocker(row)
    question = str(row.get("question") or "").strip()
    outcome_text = str(outcome or "").strip()
    outcome_key = _team_key(outcome_text)
    outcome_lower = outcome_text.lower()
    is_yes = outcome_lower in {"yes", "y"}

    explicit_subject = _match_subject_from_question(question)
    if explicit_subject:
        if blocker:
            subject, opponent = explicit_subject
            return [], blocker
        if not is_yes:
            return [], ""
        subject, opponent = explicit_subject
        return _unique_pairs(
            [
                (_match_event_slug(subject, opponent), subject),
                (_match_event_slug(opponent, subject), subject),
            ]
        ), ""

    text_candidates = [
        question,
        row.get("event"),
        row.get("event_slug"),
        row.get("market_slug"),
        row.get("slug"),
        row.get("market"),
        row.get("group"),
    ]
    event_teams: tuple[str, str] | None = None
    for value in text_candidates:
        event_teams = _event_teams_from_question(value) or _event_teams_from_text(value)
        if event_teams:
            break
    if not event_teams:
        return [], blocker

    left, right = event_teams
    left_key = _team_key(left)
    right_key = _team_key(right)
    if blocker:
        # If the row identifies a team outcome in this fixture, surface the
        # blocker against the corresponding sharp h2h row instead of silently
        # leaving it as a generic unmapped anchor.
        if is_yes:
            win_match = re.match(r"^Will\s+(.*?)\s+win(?:\s+on\s+\d{4}-\d{2}-\d{2})?(?:\?|$)", question, re.I)
            advance_match = re.match(
                r"^Will\s+(.*?)\s+(?:advance|advances|qualify|qualifies|go\s+through|progress)\s+against\s+(.*?)(?:\?|$)",
                question,
                re.I,
            )
            subject = (
                win_match.group(1).strip()
                if win_match
                else advance_match.group(1).strip()
                if advance_match
                else ""
            )
            if _team_key(subject) in {left_key, right_key}:
                return _unique_pairs(
                    [
                        (_match_event_slug(left, right), subject),
                        (_match_event_slug(right, left), subject),
                    ]
                ), blocker
        if outcome_key in {left_key, right_key, "draw"}:
            return _unique_pairs(
                [
                    (_match_event_slug(left, right), outcome_text),
                    (_match_event_slug(right, left), outcome_text),
                ]
            ), blocker
        return [], blocker

    win_match = re.match(r"^Will\s+(.*?)\s+win(?:\s+on\s+\d{4}-\d{2}-\d{2})?(?:\?|$)", question, re.I)
    if is_yes and win_match:
        subject = win_match.group(1).strip()
        if _team_key(subject) in {left_key, right_key}:
            return _unique_pairs(
                [
                    (_match_event_slug(left, right), subject),
                    (_match_event_slug(right, left), subject),
                ]
            ), ""

    if is_yes and re.search(r"\b(end|finish|result)\b.*\b(in\s+a\s+)?draw\b|\bdraw\?", question, re.I):
        return _unique_pairs(
            [
                (_match_event_slug(left, right), "Draw"),
                (_match_event_slug(right, left), "Draw"),
            ]
        ), ""

    if outcome_key in {left_key, right_key} or outcome_lower == "draw":
        return _unique_pairs(
            [
                (_match_event_slug(left, right), outcome_text),
                (_match_event_slug(right, left), outcome_text),
            ]
        ), ""
    return [], ""


def _h2h_match_tokens_from_public_search(
    settings: dict[str, Any],
    sharp_rows: Iterable[dict[str, Any]],
) -> tuple[dict[str, str], dict[str, Any]]:
    """Map h2h sharp rows to Polymarket binary YES tokens using exact fixture public search.

    This intentionally maps only explicit YES-side public markets: "Will Team beat Team?",
    "Will Team win on DATE?", and "Will Team vs Team end in a draw?". It never maps a NO token to
    the opponent because h2h markets can include draws and other non-win outcomes.
    """
    if not settings.get("match_public_search_enabled", False):
        return {}, {"enabled": False, "queries": 0, "tokens": 0}

    max_queries = max(0, int(settings.get("match_public_search_max_queries", 20) or 20))
    queries = _h2h_public_search_queries(sharp_rows, limit=max_queries)
    if not queries:
        return {}, {"enabled": True, "queries": 0, "tokens": 0}

    base_url = str(settings.get("match_public_search_url") or settings.get("worldcup_public_search_url") or DEFAULT_GAMMA_PUBLIC_SEARCH)
    timeout = int(
        settings.get("match_public_search_timeout_seconds")
        or settings.get("worldcup_public_search_timeout_seconds")
        or 20
    )
    limit_per_type = str(settings.get("match_public_search_limit_per_type", 10) or 10)
    mapping: dict[str, str] = {}
    failed_queries = 0
    markets_seen = 0
    for query in queries:
        try:
            response = requests.get(
                base_url,
                params={
                    "q": query,
                    "events_status": "active",
                    "limit_per_type": limit_per_type,
                    "search_tags": "false",
                    "search_profiles": "false",
                },
                headers=_public_search_headers(settings),
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:  # noqa: BLE001 - public-search enrichment is optional and fail-closed
            failed_queries += 1
            continue
        markets = _public_search_markets(payload)
        markets_seen += len(markets)
        for market in markets:
            token = _yes_token_from_market(market)
            if not token:
                continue
            for group, outcome in _h2h_public_market_keys(market):
                mapping.setdefault(_match_key(group, outcome), token)
    return mapping, {
        "enabled": True,
        "queries": len(queries),
        "failed_queries": failed_queries,
        "markets_seen": markets_seen,
        "tokens": len(set(mapping.values())),
        "sample_queries": queries[:10],
    }


def _coverage_bucket(
    coverage: dict[tuple[str, str], dict[str, Any]],
    row: dict[str, Any],
) -> dict[str, Any]:
    sport = str(row.get("sport") or row.get("sport_title") or "unknown").strip() or "unknown"
    market_key = str(row.get("market_key") or row.get("market") or "unknown").strip() or "unknown"
    key = (sport, market_key)
    if key not in coverage:
        coverage[key] = {
            "sport": sport,
            "market_key": market_key,
            "rows_in": 0,
            "priced_rows": 0,
            "fundamental_rows": 0,
            "skipped_unpriced": 0,
            "skipped_no_token": 0,
            "incomplete_market_rows": 0,
            "direct_token_joins": 0,
            "token_map_joins": 0,
            "h2h_public_search_token_joins": 0,
            "worldcup_winner_token_joins": 0,
            "advance_composite_token_joins": 0,
        }
    return coverage[key]


def _load_token_map(
    cfg: EngineConfig,
    settings: dict[str, Any],
) -> tuple[dict[str, str], dict[str, str], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """(market, outcome) -> token_id, built from a configured map file (default: the bot's
    market_snapshot.csv, which carries token_id + market_slug + outcome)."""
    path = Path(settings.get("token_map_path") or (cfg.output_root / "polymarket" / "market_snapshot.csv"))
    rows = read_csv_rows(path)
    if not rows:
        return {}, {}, [], {}
    cols = list(rows[0].keys())
    token_col = find_first_column(cols, TOKEN_FIELDS)
    group_col = find_first_column(cols, GROUP_FIELDS)
    outcome_col = find_first_column(cols, OUTCOME_FIELDS)
    if not token_col or not group_col or not outcome_col:
        return {}, {}, [], {}
    mapping: dict[str, str] = {}
    blocked: dict[str, str] = {}
    blocked_samples: list[dict[str, Any]] = []
    advance_composite_targets: dict[str, dict[str, Any]] = {}
    for row in rows:
        token = str(row.get(token_col, "")).strip()
        if token:
            outcome = str(row.get(outcome_col, ""))
            mapping.setdefault(_match_key(str(row.get(group_col, "")), outcome), token)
            home = str(row.get("home_team") or "").strip()
            away = str(row.get("away_team") or "").strip()
            if home and away:
                mapping.setdefault(_match_key(_match_event_slug(home, away), outcome), token)
                mapping.setdefault(_match_key(_match_event_slug(away, home), outcome), token)
            question_subject = _match_subject_from_question(row.get("question"))
            if question_subject and outcome.strip().lower() in {"yes", "y"}:
                subject, opponent = question_subject
                mapping.setdefault(_match_key(_match_event_slug(subject, opponent), subject), token)
                mapping.setdefault(_match_key(_match_event_slug(opponent, subject), subject), token)
            h2h_keys, blocker = _h2h_token_map_keys(row, outcome)
            if blocker:
                for group, blocked_outcome in h2h_keys:
                    key = _match_key(group, blocked_outcome)
                    blocked.setdefault(key, blocker)
                    if blocker == "advance_market_needs_composite_fair" and outcome.strip().lower() in {"yes", "y"}:
                        advance_composite_targets.setdefault(
                            key,
                            {
                                "token_id": token,
                                "market_slug": row.get(group_col, ""),
                                "question": row.get("question", ""),
                                "outcome": blocked_outcome,
                                "reason": blocker,
                            },
                        )
                if len(blocked_samples) < 20:
                    blocked_samples.append(
                        {
                            "market_slug": row.get(group_col, ""),
                            "question": row.get("question", ""),
                            "outcome": outcome,
                            "reason": blocker,
                        }
                    )
            else:
                for group, mapped_outcome in h2h_keys:
                    mapping.setdefault(_match_key(group, mapped_outcome), token)
    return mapping, blocked, blocked_samples, advance_composite_targets


def _bool_setting(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _advance_draw_split(settings: dict[str, Any]) -> float:
    split = safe_float(settings.get("advance_composite_draw_split"))
    if split is None:
        split = safe_float(settings.get("advance_draw_split"))
    if split is None:
        split = 0.50
    return max(0.0, min(1.0, split))


# --------------------------------------------------------------------------- build
def build_sharp_anchor(cfg: EngineConfig, *, input_path: str | None = None) -> dict[str, Any]:
    settings = cfg.raw.get("sharp_anchor", {}) or {}
    method = str(settings.get("devig_method", "multiplicative")).lower()
    source_name = str(settings.get("source_name", "sharp_odds"))
    min_outcomes_per_market = max(2, int(settings.get("min_outcomes_per_market", 2) or 2))
    min_market_implied_sum = float(settings.get("min_market_implied_sum", 0.90) or 0.90)
    max_market_implied_sum = float(settings.get("max_market_implied_sum", 2.00) or 2.00)
    advance_composite_enabled = _bool_setting(settings.get("advance_composite_enabled"), default=True)
    advance_draw_split = _advance_draw_split(settings)
    in_path = Path(input_path or settings.get("input_path") or "inputs/polymarket/sharp_odds.csv")
    out_dir = cfg.output_root / "polymarket_training"
    out_path = out_dir / "sharp_fundamental_probabilities.csv"

    rows = read_csv_rows(in_path)
    if not rows:
        write_csv(out_path, [])
        summary = {"status": "no_input", "input_path": str(in_path), "rows_in": 0,
                   "fundamental_rows": 0, "output_file": str(out_path), "generated_at_utc": now_utc()}
        write_json(cfg.governance_root / "sharp_anchor_summary.json", summary)
        return summary

    cols = list(rows[0].keys())
    group_col = find_first_column(cols, GROUP_FIELDS)
    outcome_col = find_first_column(cols, OUTCOME_FIELDS)
    odds_col = find_first_column(cols, DECIMAL_ODDS_FIELDS)
    implied_col = find_first_column(cols, IMPLIED_FIELDS)
    token_col = find_first_column(cols, TOKEN_FIELDS)
    has_direct_tokens = bool(token_col and any(str(row.get(token_col, "")).strip() for row in rows))
    token_map, token_map_blockers, token_map_blocked_samples, advance_composite_targets = _load_token_map(cfg, settings)
    h2h_public_tokens, h2h_public_search = _h2h_match_tokens_from_public_search(settings, rows)
    worldcup_winner_tokens = _worldcup_winner_token_map(cfg, settings)
    coverage_by_sport_market: dict[tuple[str, str], dict[str, Any]] = {}

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        _coverage_bucket(coverage_by_sport_market, row)["rows_in"] += 1
        groups[str(row.get(group_col, "")) if group_col else "all"].append(row)

    out_rows: list[dict[str, Any]] = []
    skipped_unpriced = 0
    skipped_no_token = 0
    skipped_no_token_samples: list[dict[str, Any]] = []
    skipped_incomplete_markets = 0
    skipped_incomplete_market_rows = 0
    incomplete_market_samples: list[dict[str, Any]] = []
    worldcup_winner_token_joins = 0
    direct_token_joins = 0
    token_map_joins = 0
    h2h_public_search_token_joins = 0
    advance_composite_token_joins = 0
    overrounds: list[float] = []
    for gkey, grows in groups.items():
        raw: list[float] = []
        kept: list[dict[str, Any]] = []
        for row in grows:
            coverage = _coverage_bucket(coverage_by_sport_market, row)
            implied = implied_from_decimal(safe_float(row.get(odds_col))) if odds_col else safe_float(row.get(implied_col))
            if implied is None or not 0.0 < implied < 1.0:
                skipped_unpriced += 1
                coverage["skipped_unpriced"] += 1
                continue
            raw.append(implied)
            kept.append(row)
            coverage["priced_rows"] += 1
        if not raw:
            continue
        raw_sum = sum(raw)
        if len(raw) < min_outcomes_per_market or raw_sum < min_market_implied_sum or raw_sum > max_market_implied_sum:
            skipped_incomplete_markets += 1
            skipped_incomplete_market_rows += len(kept)
            for row in kept:
                _coverage_bucket(coverage_by_sport_market, row)["incomplete_market_rows"] += 1
            if len(incomplete_market_samples) < 20:
                incomplete_market_samples.append(
                    {
                        "market_slug": gkey,
                        "priced_outcomes": len(raw),
                        "implied_probability_sum": round(raw_sum, 6),
                        "reason": (
                            "too_few_priced_outcomes"
                            if len(raw) < min_outcomes_per_market
                            else "implied_sum_below_complete_market_floor"
                            if raw_sum < min_market_implied_sum
                            else "implied_sum_above_sanity_ceiling"
                        ),
                    }
                )
            continue
        overrounds.append(raw_sum)
        fair = devig(raw, method)
        fair_by_outcome: dict[str, dict[str, Any]] = {}
        if outcome_col:
            for fair_row, fair_implied, fair_prob in zip(kept, raw, fair):
                outcome_key = _team_key(fair_row.get(outcome_col))
                if outcome_key:
                    fair_by_outcome.setdefault(
                        outcome_key,
                        {
                            "probability": fair_prob,
                            "raw_implied_probability": fair_implied,
                            "outcome": fair_row.get(outcome_col, ""),
                        },
                    )
        for row, implied, prob in zip(kept, raw, fair):
            row_market_key = str(row.get("market_key", "") or "")
            coverage = _coverage_bucket(coverage_by_sport_market, row)
            outcome_text = str(row.get(outcome_col, "")) if outcome_col else ""
            token = str(row.get(token_col, "")).strip() if token_col else ""
            join_source = ""
            output_probability = prob
            output_source = source_name
            output_outcome = row.get(outcome_col, "") if outcome_col else ""
            output_extra: dict[str, Any] = {}
            if token:
                direct_token_joins += 1
                join_source = "direct_token_joins"
            elif outcome_col:
                token = token_map.get(_match_key(gkey, outcome_text), "")
                if token:
                    token_map_joins += 1
                    join_source = "token_map_joins"
                else:
                    token = h2h_public_tokens.get(_match_key(gkey, outcome_text), "")
                    if token:
                        h2h_public_search_token_joins += 1
                        join_source = "h2h_public_search_token_joins"
            if (
                not token
                and outcome_col
                and _looks_like_worldcup_outright(row, gkey, row_market_key)
            ):
                token = worldcup_winner_tokens.get(_team_key(row.get(outcome_col)), "")
                if token:
                    worldcup_winner_token_joins += 1
                    join_source = "worldcup_winner_token_joins"
            composite_blocker_reason = ""
            if not token and outcome_col:
                composite_target = advance_composite_targets.get(_match_key(gkey, outcome_text))
                if composite_target:
                    draw_fair = fair_by_outcome.get("draw")
                    subject_fair = fair_by_outcome.get(_team_key(outcome_text))
                    if not advance_composite_enabled:
                        composite_blocker_reason = "advance_market_needs_composite_fair"
                    elif not draw_fair:
                        composite_blocker_reason = "advance_market_needs_composite_fair_missing_draw"
                    elif subject_fair:
                        token = str(composite_target.get("token_id") or "")
                        if token:
                            output_probability = min(
                                1.0,
                                max(
                                    0.0,
                                    float(subject_fair["probability"])
                                    + float(draw_fair["probability"]) * advance_draw_split,
                                ),
                            )
                            output_source = f"{source_name}_advance_composite"
                            output_outcome = f"{outcome_text} advance".strip()
                            output_extra = {
                                "anchor_type": "advance_composite_from_h2h",
                                "composite_method": "regulation_win_plus_draw_share",
                                "draw_advance_share": round(advance_draw_split, 6),
                                "regulation_win_probability": round(float(subject_fair["probability"]), 6),
                                "draw_probability": round(float(draw_fair["probability"]), 6),
                                "composite_target_market_slug": composite_target.get("market_slug", ""),
                                "composite_target_question": composite_target.get("question", ""),
                            }
                            advance_composite_token_joins += 1
                            join_source = "advance_composite_token_joins"
            if not token:
                skipped_no_token += 1
                coverage["skipped_no_token"] += 1
                skip_reason = (
                    composite_blocker_reason
                    or token_map_blockers.get(_match_key(gkey, outcome_text), "")
                    if outcome_col
                    else ""
                ) or "unmapped_sharp_anchor_row"
                if len(skipped_no_token_samples) < 20:
                    skipped_no_token_samples.append(
                        {
                            "market_slug": gkey,
                            "outcome": row.get(outcome_col, "") if outcome_col else "",
                            "market_key": row.get("market_key", ""),
                            "sport": row.get("sport", ""),
                            "reason": skip_reason,
                        }
                    )
                continue
            coverage["fundamental_rows"] += 1
            if join_source:
                coverage[join_source] += 1
            out_row = {
                "token_id": token,
                "probability": round(output_probability, 6),
                "market_slug": gkey,
                "outcome": output_outcome,
                "decimal_odds": row.get(odds_col, "") if odds_col else "",
                "raw_implied_probability": round(implied, 6),
                "devig_method": method,
                "source": output_source,
            }
            out_row.update(output_extra)
            out_rows.append(out_row)

    write_csv(out_path, out_rows)
    token_join_parts: list[str] = []
    if direct_token_joins or has_direct_tokens:
        token_join_parts.append("direct_token_id")
    if token_map_joins or (worldcup_winner_token_joins and not direct_token_joins and not has_direct_tokens):
        token_join_parts.append("market_outcome_map")
    if h2h_public_search_token_joins:
        token_join_parts.append("match_public_search")
    if worldcup_winner_token_joins:
        token_join_parts.append("worldcup_winner_team_map")
    if advance_composite_token_joins:
        token_join_parts.append("advance_composite_h2h")
    token_join_label = "+".join(token_join_parts) if token_join_parts else (
        "direct_token_id" if has_direct_tokens else "market_outcome_map"
    )
    summary = {
        "status": "built",
        "input_path": str(in_path),
        "devig_method": method,
        "rows_in": len(rows),
        "markets": len(groups),
        "fundamental_rows": len(out_rows),
        "skipped_unpriced": skipped_unpriced,
        "skipped_no_token": skipped_no_token,
        "skipped_no_token_samples": skipped_no_token_samples,
        "skipped_incomplete_markets": skipped_incomplete_markets,
        "skipped_incomplete_market_rows": skipped_incomplete_market_rows,
        "incomplete_market_samples": incomplete_market_samples,
        "min_outcomes_per_market": min_outcomes_per_market,
        "min_market_implied_sum": min_market_implied_sum,
        "max_market_implied_sum": max_market_implied_sum,
        "mean_overround_removed": round(sum(overrounds) / len(overrounds) - 1.0, 4) if overrounds else 0.0,
        "token_join": token_join_label,
        "direct_token_joins": direct_token_joins,
        "token_map_joins": token_map_joins,
        "h2h_public_search": h2h_public_search,
        "h2h_public_search_tokens_available": len(set(h2h_public_tokens.values())),
        "h2h_public_search_token_joins": h2h_public_search_token_joins,
        "advance_composite_enabled": advance_composite_enabled,
        "advance_composite_draw_split": advance_draw_split,
        "advance_composite_targets_available": len(advance_composite_targets),
        "advance_composite_token_joins": advance_composite_token_joins,
        "token_map_h2h_blocked_samples": token_map_blocked_samples,
        "coverage_by_sport_market": sorted(
            coverage_by_sport_market.values(),
            key=lambda item: (
                -int(item.get("rows_in", 0) or 0),
                str(item.get("sport") or ""),
                str(item.get("market_key") or ""),
            ),
        ),
        "worldcup_winner_tokens_available": len(worldcup_winner_tokens),
        "worldcup_winner_token_joins": worldcup_winner_token_joins,
        "output_file": str(out_path),
        "note": "De-vigged sharp-book fair probabilities in the fundamental contract. Point "
                "mispricing_alpha.fundamental_probability_paths at output_file to use as the anchor.",
        "generated_at_utc": now_utc(),
    }
    write_json(cfg.governance_root / "sharp_anchor_summary.json", summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return build_sharp_anchor(load_config(config_path))
