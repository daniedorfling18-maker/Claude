from __future__ import annotations

import csv
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

import requests

from .config import EngineConfig
from .utils import ensure_dir, write_csv, write_json


GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"

SNAPSHOT_FILES = [
    "polymarket/ml/raw_market_snapshots.csv",
    "polymarket_wide/worldcup/ml/raw_market_snapshots.csv",
    "polymarket_wide/sports/ml/raw_market_snapshots.csv",
    "polymarket_wide/all/ml/raw_market_snapshots.csv",
]

CLEAN_WINNER_THRESHOLD = 0.999
CLEAN_LOSER_THRESHOLD = 0.001

PSEUDO_WINNER_THRESHOLD = 0.97
PSEUDO_LOSER_THRESHOLD = 0.03


def _read_csv_stream(path: Path):
    if not path.exists() or path.stat().st_size == 0:
        return
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield {str(k): "" if v is None else str(v) for k, v in row.items()}


def _first(row: dict[str, str], names: list[str]) -> str:
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _boolish(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _discover_snapshot_markets(cfg: EngineConfig) -> dict[str, dict[str, Any]]:
    markets: dict[str, dict[str, Any]] = {}

    for relative in SNAPSHOT_FILES:
        path = cfg.output_root / relative
        if not path.exists():
            continue

        for row in _read_csv_stream(path) or []:
            slug = _first(row, ["market_slug", "slug", "market"])
            token = _first(row, ["token_id", "asset_id", "clob_token_id", "token"])
            outcome = _first(row, ["outcome", "outcome_name", "name"])
            question = _first(row, ["question", "title", "description"])

            if not slug or not token:
                continue

            item = markets.setdefault(
                slug,
                {
                    "market_slug": slug,
                    "question_snapshot": question,
                    "snapshot_tokens": set(),
                    "snapshot_outcomes": set(),
                    "snapshot_rows": 0,
                    "snapshot_files": set(),
                },
            )
            item["snapshot_tokens"].add(token)
            if outcome:
                item["snapshot_outcomes"].add(outcome)
            item["snapshot_rows"] += 1
            item["snapshot_files"].add(str(path))

    return markets


def _fetch_gamma_market_by_slug(slug: str, session: requests.Session) -> dict[str, Any] | None:
    try:
        response = session.get(GAMMA_MARKETS_URL, params={"slug": slug}, timeout=30)
        if response.status_code == 422:
            return None
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return None

    rows = payload if isinstance(payload, list) else payload.get("data", [])
    if not rows:
        return None
    if isinstance(rows[0], dict):
        return rows[0]
    return None


def _classify_market_prices(
    *,
    closed: bool,
    active: bool,
    accepting_orders: bool,
    outcomes: list[str],
    tokens: list[str],
    prices: list[float],
) -> tuple[str, list[dict[str, Any]]]:
    if not outcomes or not tokens or not prices:
        return "missing_prices_or_tokens", []

    if not (len(outcomes) == len(tokens) == len(prices)):
        return "length_mismatch", []

    max_price = max(prices)
    min_price = min(prices)
    winner_count_clean = sum(1 for p in prices if p >= CLEAN_WINNER_THRESHOLD)
    loser_count_clean = sum(1 for p in prices if p <= CLEAN_LOSER_THRESHOLD)

    winner_count_pseudo = sum(1 for p in prices if p >= PSEUDO_WINNER_THRESHOLD)
    loser_count_pseudo = sum(1 for p in prices if p <= PSEUDO_LOSER_THRESHOLD)

    label_rows: list[dict[str, Any]] = []

    if closed:
        if winner_count_clean == 1 and loser_count_clean == len(prices) - 1:
            status = "clean_resolved"
            for outcome, token, price in zip(outcomes, tokens, prices):
                label_rows.append(
                    {
                        "label_type": "clean",
                        "target": 1 if price >= CLEAN_WINNER_THRESHOLD else 0,
                        "confidence": 1.0,
                        "outcome_price": price,
                        "outcome": outcome,
                        "token_id": token,
                    }
                )
            return status, label_rows
        return "closed_but_not_strict_1_0", []

    if active and accepting_orders:
        if winner_count_pseudo == 1 and loser_count_pseudo == len(prices) - 1:
            status = "pseudo_near_terminal"
            for outcome, token, price in zip(outcomes, tokens, prices):
                label_rows.append(
                    {
                        "label_type": "pseudo",
                        "target": 1 if price >= PSEUDO_WINNER_THRESHOLD else 0,
                        "confidence": max(price, 1.0 - price),
                        "outcome_price": price,
                        "outcome": outcome,
                        "token_id": token,
                    }
                )
            return status, label_rows

    if max_price >= PSEUDO_WINNER_THRESHOLD or min_price <= PSEUDO_LOSER_THRESHOLD:
        return "near_terminal_but_not_strict", []

    return "open_not_near_terminal", []


def collect_snapshot_labels(cfg: EngineConfig) -> dict[str, Any]:
    training_dir = cfg.output_root / "polymarket_training"
    governance_dir = cfg.governance_root

    ensure_dir(training_dir)
    ensure_dir(governance_dir)

    snapshot_markets = _discover_snapshot_markets(cfg)

    clean_rows: list[dict[str, Any]] = []
    pseudo_rows: list[dict[str, Any]] = []
    status_rows: list[dict[str, Any]] = []

    status_counts: Counter[str] = Counter()
    found_counts: Counter[str] = Counter()

    session = requests.Session()

    for idx, (slug, snap) in enumerate(snapshot_markets.items(), start=1):
        if idx % 25 == 0:
            print(f"Checked {idx}/{len(snapshot_markets)} snapshot markets")

        gamma = _fetch_gamma_market_by_slug(slug, session)

        if gamma is None:
            found_counts["not_found"] += 1
            status_counts["gamma_not_found"] += 1
            status_rows.append(
                {
                    "market_slug": slug,
                    "gamma_found": False,
                    "label_status": "gamma_not_found",
                    "question_snapshot": snap["question_snapshot"],
                    "question_gamma": "",
                    "closed": "",
                    "active": "",
                    "accepting_orders": "",
                    "enable_order_book": "",
                    "outcomes": "|".join(sorted(snap["snapshot_outcomes"])),
                    "gamma_outcomes": "",
                    "clob_token_ids": "",
                    "outcome_prices": "",
                    "snapshot_rows": snap["snapshot_rows"],
                    "snapshot_token_count": len(snap["snapshot_tokens"]),
                    "snapshot_files": "|".join(sorted(snap["snapshot_files"])),
                    "end_date": "",
                    "closed_time": "",
                    "updated_at": "",
                }
            )
            continue

        found_counts["found"] += 1

        outcomes = [str(x) for x in _parse_json_list(gamma.get("outcomes"))]
        tokens = [str(x) for x in _parse_json_list(gamma.get("clobTokenIds"))]
        prices_raw = _parse_json_list(gamma.get("outcomePrices"))
        prices = [_float_or_none(x) for x in prices_raw]

        if any(p is None for p in prices):
            prices_float: list[float] = []
        else:
            prices_float = [float(p) for p in prices if p is not None]

        closed = _boolish(gamma.get("closed"))
        active = _boolish(gamma.get("active"))
        accepting_orders = _boolish(gamma.get("acceptingOrders"))

        label_status, labels = _classify_market_prices(
            closed=closed,
            active=active,
            accepting_orders=accepting_orders,
            outcomes=outcomes,
            tokens=tokens,
            prices=prices_float,
        )

        status_counts[label_status] += 1

        base = {
            "market_slug": slug,
            "question_snapshot": snap["question_snapshot"],
            "question_gamma": gamma.get("question") or gamma.get("title") or "",
            "gamma_market_id": gamma.get("id", ""),
            "condition_id": gamma.get("conditionId", ""),
            "question_id": gamma.get("questionID", ""),
            "closed": closed,
            "active": active,
            "accepting_orders": accepting_orders,
            "enable_order_book": _boolish(gamma.get("enableOrderBook")),
            "end_date": gamma.get("endDate") or gamma.get("endDateIso") or "",
            "closed_time": gamma.get("closedTime") or "",
            "updated_at": gamma.get("updatedAt") or "",
            "gamma_outcomes": "|".join(outcomes),
            "gamma_tokens": "|".join(tokens),
            "gamma_prices": "|".join(str(x) for x in prices_float),
            "snapshot_rows": snap["snapshot_rows"],
            "snapshot_token_count": len(snap["snapshot_tokens"]),
            "snapshot_outcomes": "|".join(sorted(snap["snapshot_outcomes"])),
            "snapshot_files": "|".join(sorted(snap["snapshot_files"])),
            "label_status": label_status,
        }

        status_rows.append(base)

        for label in labels:
            row = {
                **base,
                **label,
                "pseudo_label": label["label_type"] == "pseudo",
            }
            if label["label_type"] == "clean":
                clean_rows.append(row)
            else:
                pseudo_rows.append(row)

        time.sleep(0.03)

    clean_path = training_dir / "clean_resolved_snapshot_labels.csv"
    pseudo_path = training_dir / "pseudo_label_candidates.csv"
    status_path = governance_dir / "snapshot_market_current_status.csv"
    summary_path = governance_dir / "snapshot_label_collection_summary.json"

    label_fieldnames = [
        "label_type",
        "pseudo_label",
        "market_slug",
        "question_gamma",
        "outcome",
        "token_id",
        "target",
        "confidence",
        "outcome_price",
        "label_status",
        "closed",
        "active",
        "accepting_orders",
        "enable_order_book",
        "end_date",
        "closed_time",
        "updated_at",
        "gamma_market_id",
        "condition_id",
        "question_id",
        "gamma_outcomes",
        "gamma_prices",
        "snapshot_rows",
        "snapshot_token_count",
        "snapshot_outcomes",
        "snapshot_files",
    ]

    status_fieldnames = [
        "market_slug",
        "gamma_found",
        "label_status",
        "question_snapshot",
        "question_gamma",
        "closed",
        "active",
        "accepting_orders",
        "enable_order_book",
        "end_date",
        "closed_time",
        "updated_at",
        "gamma_outcomes",
        "gamma_tokens",
        "gamma_prices",
        "snapshot_rows",
        "snapshot_token_count",
        "snapshot_outcomes",
        "snapshot_files",
    ]

    write_csv(clean_path, clean_rows, fieldnames=label_fieldnames)
    write_csv(pseudo_path, pseudo_rows, fieldnames=label_fieldnames)
    write_csv(status_path, status_rows, fieldnames=status_fieldnames)

    summary = {
        "status": "ok",
        "snapshot_markets": len(snapshot_markets),
        "gamma_found_counts": dict(found_counts),
        "label_status_counts": dict(status_counts),
        "clean_label_rows": len(clean_rows),
        "clean_label_markets": len({r["market_slug"] for r in clean_rows}),
        "pseudo_label_rows": len(pseudo_rows),
        "pseudo_label_markets": len({r["market_slug"] for r in pseudo_rows}),
        "clean_output": str(clean_path),
        "pseudo_output": str(pseudo_path),
        "status_output": str(status_path),
    }

    write_json(summary_path, summary)
    return summary
