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
from pathlib import Path
import re
from typing import Any, Iterable

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
        if token and key:
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


def _load_token_map(cfg: EngineConfig, settings: dict[str, Any]) -> dict[str, str]:
    """(market, outcome) -> token_id, built from a configured map file (default: the bot's
    market_snapshot.csv, which carries token_id + market_slug + outcome)."""
    path = Path(settings.get("token_map_path") or (cfg.output_root / "polymarket" / "market_snapshot.csv"))
    rows = read_csv_rows(path)
    if not rows:
        return {}
    cols = list(rows[0].keys())
    token_col = find_first_column(cols, TOKEN_FIELDS)
    group_col = find_first_column(cols, GROUP_FIELDS)
    outcome_col = find_first_column(cols, OUTCOME_FIELDS)
    if not token_col or not group_col or not outcome_col:
        return {}
    mapping: dict[str, str] = {}
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
    return mapping


# --------------------------------------------------------------------------- build
def build_sharp_anchor(cfg: EngineConfig, *, input_path: str | None = None) -> dict[str, Any]:
    settings = cfg.raw.get("sharp_anchor", {}) or {}
    method = str(settings.get("devig_method", "multiplicative")).lower()
    source_name = str(settings.get("source_name", "sharp_odds"))
    min_outcomes_per_market = max(2, int(settings.get("min_outcomes_per_market", 2) or 2))
    min_market_implied_sum = float(settings.get("min_market_implied_sum", 0.90) or 0.90)
    max_market_implied_sum = float(settings.get("max_market_implied_sum", 2.00) or 2.00)
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
    token_map = {} if token_col else _load_token_map(cfg, settings)
    worldcup_winner_tokens = {} if token_col else _worldcup_winner_token_map(cfg, settings)

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(group_col, "")) if group_col else "all"].append(row)

    out_rows: list[dict[str, Any]] = []
    skipped_unpriced = 0
    skipped_no_token = 0
    skipped_no_token_samples: list[dict[str, Any]] = []
    skipped_incomplete_markets = 0
    skipped_incomplete_market_rows = 0
    incomplete_market_samples: list[dict[str, Any]] = []
    worldcup_winner_token_joins = 0
    overrounds: list[float] = []
    for gkey, grows in groups.items():
        raw: list[float] = []
        kept: list[dict[str, Any]] = []
        for row in grows:
            implied = implied_from_decimal(safe_float(row.get(odds_col))) if odds_col else safe_float(row.get(implied_col))
            if implied is None or not 0.0 < implied < 1.0:
                skipped_unpriced += 1
                continue
            raw.append(implied)
            kept.append(row)
        if not raw:
            continue
        raw_sum = sum(raw)
        if len(raw) < min_outcomes_per_market or raw_sum < min_market_implied_sum or raw_sum > max_market_implied_sum:
            skipped_incomplete_markets += 1
            skipped_incomplete_market_rows += len(kept)
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
        for row, implied, prob in zip(kept, raw, fair):
            row_market_key = str(row.get("market_key", "") or "")
            token = (
                str(row.get(token_col, "")).strip()
                if token_col
                else token_map.get(_match_key(gkey, str(row.get(outcome_col, ""))), "")
            )
            if (
                not token
                and outcome_col
                and _looks_like_worldcup_outright(row, gkey, row_market_key)
            ):
                token = worldcup_winner_tokens.get(_team_key(row.get(outcome_col)), "")
                if token:
                    worldcup_winner_token_joins += 1
            if not token:
                skipped_no_token += 1
                if len(skipped_no_token_samples) < 20:
                    skipped_no_token_samples.append(
                        {
                            "market_slug": gkey,
                            "outcome": row.get(outcome_col, "") if outcome_col else "",
                            "market_key": row.get("market_key", ""),
                            "sport": row.get("sport", ""),
                            "reason": "unmapped_sharp_anchor_row",
                        }
                    )
                continue
            out_rows.append({
                "token_id": token,
                "probability": round(prob, 6),
                "market_slug": gkey,
                "outcome": row.get(outcome_col, "") if outcome_col else "",
                "decimal_odds": row.get(odds_col, "") if odds_col else "",
                "raw_implied_probability": round(implied, 6),
                "devig_method": method,
                "source": source_name,
            })

    write_csv(out_path, out_rows)
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
        "token_join": (
            "direct_token_id"
            if token_col
            else "market_outcome_map+worldcup_winner_team_map"
            if worldcup_winner_token_joins
            else "market_outcome_map"
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
