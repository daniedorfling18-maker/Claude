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
            mapping.setdefault(_match_key(str(row.get(group_col, "")), str(row.get(outcome_col, ""))), token)
    return mapping


# --------------------------------------------------------------------------- build
def build_sharp_anchor(cfg: EngineConfig, *, input_path: str | None = None) -> dict[str, Any]:
    settings = cfg.raw.get("sharp_anchor", {}) or {}
    method = str(settings.get("devig_method", "multiplicative")).lower()
    source_name = str(settings.get("source_name", "sharp_odds"))
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

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(group_col, "")) if group_col else "all"].append(row)

    out_rows: list[dict[str, Any]] = []
    skipped_unpriced = 0
    skipped_no_token = 0
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
        overrounds.append(sum(raw))
        fair = devig(raw, method)
        for row, implied, prob in zip(kept, raw, fair):
            token = (
                str(row.get(token_col, "")).strip()
                if token_col
                else token_map.get(_match_key(gkey, str(row.get(outcome_col, ""))), "")
            )
            if not token:
                skipped_no_token += 1
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
        "mean_overround_removed": round(sum(overrounds) / len(overrounds) - 1.0, 4) if overrounds else 0.0,
        "token_join": "direct_token_id" if token_col else "market_outcome_map",
        "output_file": str(out_path),
        "note": "De-vigged sharp-book fair probabilities in the fundamental contract. Point "
                "mispricing_alpha.fundamental_probability_paths at output_file to use as the anchor.",
        "generated_at_utc": now_utc(),
    }
    write_json(cfg.governance_root / "sharp_anchor_summary.json", summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return build_sharp_anchor(load_config(config_path))
