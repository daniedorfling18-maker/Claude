from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from .config import EngineConfig, load_config
from .utils import normalize_slug, now_utc, parse_timestamp, read_csv_rows, safe_float, write_csv, write_json
from .websocket_collector import collect_websocket
from .websocket_normaliser import normalize_websocket_file

DEFAULT_GAMMA_BASE_URL = "https://gamma-api.polymarket.com/markets"
COMPLETE = "complete"
INCOMPLETE = "incomplete"
STALE = "stale"
AMBIGUOUS = "ambiguous"
DUPLICATE = "duplicate"
UNRESOLVED = "unresolved"

ACTIONABLE_FIELDS = [
    "cycle",
    "generated_at_utc",
    "event_key",
    "event_title",
    "category",
    "outcome_count",
    "ask_sum",
    "net_ask_sum",
    "gross_lock_pct",
    "net_lock_pct",
    "capital_required",
    "net_locked_profit",
    "expected_annualized_return",
    "days_to_resolution",
    "max_executable_shares",
    "completeness_status",
    "dry_run_only",
    "orders_placed",
    "unresolved_or_cancellation_risk",
]
REJECTED_FIELDS = ACTIONABLE_FIELDS + ["rejection_reason"]
STATUS_FIELDS = [
    "cycle",
    "generated_at_utc",
    "event_key",
    "event_title",
    "category",
    "outcome_count",
    "expected_outcome_count",
    "captured_outcome_count",
    "completeness_status",
    "ask_sum",
    "net_ask_sum",
    "rejection_reason",
]


def _parse_jsonish(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _clip_price(value: float) -> float:
    return min(1.0, max(0.0, value))


def _safe_dt(value: Any) -> datetime | None:
    parsed = parse_timestamp(value)
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _event_key(market: Mapping[str, Any]) -> str:
    for key in ("eventSlug", "event_slug", "groupSlug", "group_slug", "slug", "conditionId", "id"):
        value = _as_text(market.get(key)).strip()
        if value:
            return normalize_slug(value)
    question = _as_text(market.get("question")).strip()
    return normalize_slug(question) if question else "unknown-event"


def _event_title(market: Mapping[str, Any]) -> str:
    for key in ("groupItemTitle", "eventTitle", "title", "question", "slug"):
        value = _as_text(market.get(key)).strip()
        if value:
            return value
    return "unknown event"


def _resolution_dt(market: Mapping[str, Any]) -> datetime | None:
    for key in ("closedTime", "endDate", "endDateIso", "end_time", "close_time"):
        parsed = _safe_dt(market.get(key))
        if parsed is not None:
            return parsed
    return None


def _extract_complete_outcomes(market: Mapping[str, Any]) -> list[dict[str, Any]]:
    outcomes = [_as_text(x).strip() for x in _parse_jsonish(market.get("outcomes"))]
    token_ids = [_as_text(x).strip() for x in _parse_jsonish(market.get("clobTokenIds"))]
    if not outcomes or not token_ids or len(outcomes) != len(token_ids):
        return []
    rows: list[dict[str, Any]] = []
    for i, (outcome, token_id) in enumerate(zip(outcomes, token_ids)):
        rows.append({
            "outcome_index": i,
            "outcome": outcome,
            "outcome_key": normalize_slug(outcome),
            "token_id": token_id,
        })
    return rows


def fetch_gamma_market(locator: str, *, base_url: str = DEFAULT_GAMMA_BASE_URL, timeout_seconds: int = 20) -> dict[str, Any] | None:
    """Fetch a Gamma market by slug, condition id, or id.

    This is intentionally read-only. It never submits orders and it only retrieves
    market metadata required to prove outcome-set completeness.
    """
    locator = str(locator or "").strip()
    if not locator:
        return None
    for param in ("slug", "condition_id", "conditionId", "id"):
        query = urllib.parse.urlencode({param: locator})
        url = f"{base_url}?{query}"
        request = urllib.request.Request(url, headers={"User-Agent": "polymarket-predictive-engine/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # nosec B310: public configured API
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            continue
        rows: list[Any]
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = payload.get("data") or payload.get("markets") or payload.get("results") or [payload]
        else:
            rows = []
        for row in rows:
            if isinstance(row, dict):
                return row
    return None


def fetch_complete_outcome_universe(
    seed: Mapping[str, Any],
    *,
    fetcher: Callable[[str], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Return a complete outcome universe candidate and completeness diagnostics."""
    fetcher = fetcher or (lambda locator: fetch_gamma_market(locator))
    locators = []
    for key in ("market_slug", "slug", "market_id", "condition_id", "conditionId", "id"):
        value = _as_text(seed.get(key)).strip()
        if value and value not in locators:
            locators.append(value)
    if not locators and seed.get("gamma_market"):
        market = seed["gamma_market"]
    else:
        market = None
        for locator in locators:
            market = fetcher(locator)
            if market:
                break
    if not isinstance(market, dict):
        return {"status": UNRESOLVED, "market": {}, "outcomes": [], "reason": "gamma market could not be fetched"}

    outcomes = _extract_complete_outcomes(market)
    if not outcomes:
        return {"status": INCOMPLETE, "market": market, "outcomes": [], "reason": "missing or mismatched outcomes/clobTokenIds"}

    outcome_keys = [row["outcome_key"] for row in outcomes]
    token_ids = [row["token_id"] for row in outcomes]
    if len(set(outcome_keys)) != len(outcome_keys) or len(set(token_ids)) != len(token_ids):
        return {"status": DUPLICATE, "market": market, "outcomes": outcomes, "reason": "duplicate outcome names or token ids"}

    if _truthy(market.get("closed")) or _truthy(market.get("archived")):
        return {"status": UNRESOLVED, "market": market, "outcomes": outcomes, "reason": "market is closed or archived"}

    if len(outcomes) < 2:
        return {"status": AMBIGUOUS, "market": market, "outcomes": outcomes, "reason": "less than two outcomes"}

    return {"status": COMPLETE, "market": market, "outcomes": outcomes, "reason": "complete outcome set from Gamma"}


def latest_live_books(rows: Iterable[Mapping[str, Any]], *, stale_after_seconds: int = 600) -> dict[str, dict[str, Any]]:
    """Latest book row per token id, marked stale if older than threshold."""
    latest: dict[str, dict[str, Any]] = {}
    now = datetime.now(timezone.utc)
    for row in rows:
        token_id = _as_text(row.get("asset_id") or row.get("token_id")).strip()
        if not token_id:
            continue
        collected = _safe_dt(row.get("collected_at_utc")) or _safe_dt(row.get("source_timestamp"))
        previous = latest.get(token_id)
        previous_dt = _safe_dt(previous.get("collected_at_utc")) if previous else None
        if previous is None or (collected is not None and (previous_dt is None or collected > previous_dt)):
            best_ask = safe_float(row.get("best_ask"))
            best_bid = safe_float(row.get("best_bid"))
            top_ask_size = safe_float(row.get("top_ask_size"))
            ask_depth = safe_float(row.get("ask_depth_1pct")) or top_ask_size
            age = (now - collected).total_seconds() if collected else math.inf
            latest[token_id] = {
                **{str(k): v for k, v in row.items()},
                "token_id": token_id,
                "best_ask": best_ask,
                "best_bid": best_bid,
                "top_ask_size": top_ask_size,
                "ask_depth": ask_depth,
                "book_age_seconds": age,
                "stale": bool(age > stale_after_seconds),
            }
    return latest


def _annualized_return(roi: float | None, days: float | None) -> float | None:
    if roi is None or days is None or days <= 0:
        return None
    try:
        return (1.0 + roi) ** (365.0 / days) - 1.0
    except Exception:
        return None


def evaluate_dutch_book_candidate(
    seed: Mapping[str, Any],
    live_books: Mapping[str, Mapping[str, Any]],
    *,
    fetcher: Callable[[str], dict[str, Any] | None] | None = None,
    settings: Mapping[str, Any] | None = None,
    cycle: int = 1,
) -> tuple[dict[str, Any] | None, dict[str, Any], dict[str, Any]]:
    """Evaluate one complete event candidate.

    Returns (actionable, rejected, status). Exactly one of actionable/rejected is
    populated. No order payload is ever created.
    """
    settings = settings or {}
    generated_at = now_utc()
    universe = fetch_complete_outcome_universe(seed, fetcher=fetcher)
    market = universe.get("market") or {}
    outcomes = universe.get("outcomes") or []
    event_key = _event_key(market or seed)
    event_title = _event_title(market or seed)
    category = _as_text(market.get("category") or seed.get("category") or "unknown")
    resolution = _resolution_dt(market)
    days_to_resolution = None
    if resolution is not None:
        days_to_resolution = max(0.0, (resolution - datetime.now(timezone.utc)).total_seconds() / 86400.0)

    base = {
        "cycle": cycle,
        "generated_at_utc": generated_at,
        "event_key": event_key,
        "event_title": event_title,
        "category": category,
        "outcome_count": len(outcomes),
        "expected_outcome_count": len(outcomes),
        "captured_outcome_count": 0,
        "ask_sum": None,
        "net_ask_sum": None,
        "gross_lock_pct": None,
        "net_lock_pct": None,
        "capital_required": 0.0,
        "net_locked_profit": 0.0,
        "expected_annualized_return": None,
        "days_to_resolution": days_to_resolution,
        "max_executable_shares": 0.0,
        "completeness_status": universe["status"],
        "dry_run_only": True,
        "orders_placed": 0,
        "unresolved_or_cancellation_risk": "missing_resolution_date" if resolution is None else "open_until_resolution",
    }
    if universe["status"] != COMPLETE:
        rejected = {**base, "rejection_reason": universe.get("reason", "outcome universe is not complete")}
        status = {k: rejected.get(k) for k in STATUS_FIELDS}
        return None, rejected, status

    stale_books: list[str] = []
    missing_books: list[str] = []
    asks: list[float] = []
    depths: list[float] = []
    slippage = float(settings.get("slippage_buffer", settings.get("slippage", 0.01)))
    fee = float(settings.get("fee_buffer", settings.get("transaction_cost", 0.0)))

    for outcome in outcomes:
        token_id = outcome["token_id"]
        book = live_books.get(token_id)
        if not book or safe_float(book.get("best_ask")) is None:
            missing_books.append(token_id)
            continue
        if book.get("stale"):
            stale_books.append(token_id)
        ask = float(book["best_ask"])
        depth = safe_float(book.get("ask_depth")) or safe_float(book.get("top_ask_size")) or 0.0
        asks.append(ask)
        depths.append(max(0.0, depth))

    base["captured_outcome_count"] = len(asks)
    if missing_books or len(asks) != len(outcomes):
        rejected = {**base, "completeness_status": INCOMPLETE, "rejection_reason": "missing executable ask for one or more outcomes"}
        status = {k: rejected.get(k) for k in STATUS_FIELDS}
        return None, rejected, status
    if stale_books:
        rejected = {**base, "completeness_status": STALE, "rejection_reason": "one or more outcome books are stale"}
        status = {k: rejected.get(k) for k in STATUS_FIELDS}
        return None, rejected, status
    if min(depths or [0.0]) <= 0:
        rejected = {**base, "completeness_status": COMPLETE, "rejection_reason": "low-depth lock: no positive ask depth across all outcomes"}
        status = {k: rejected.get(k) for k in STATUS_FIELDS}
        return None, rejected, status

    ask_sum = sum(asks)
    net_asks = [_clip_price(a + slippage + fee) for a in asks]
    net_ask_sum = sum(net_asks)
    gross_lock = 1.0 - ask_sum
    net_lock = 1.0 - net_ask_sum
    base.update({
        "ask_sum": ask_sum,
        "net_ask_sum": net_ask_sum,
        "gross_lock_pct": gross_lock,
        "net_lock_pct": net_lock,
    })

    if ask_sum >= 1.0:
        rejected = {**base, "rejection_reason": "complete set is not an executable Dutch-book: ask_sum >= 1"}
        status = {k: rejected.get(k) for k in STATUS_FIELDS}
        return None, rejected, status
    if net_ask_sum >= 1.0:
        rejected = {**base, "rejection_reason": "net lock fails after slippage/fee buffer"}
        status = {k: rejected.get(k) for k in STATUS_FIELDS}
        return None, rejected, status

    bankroll = float(settings.get("bankroll", 1000))
    liquidity_cap_fraction = float(settings.get("liquidity_cap_fraction", 0.05))
    max_event_exposure = float(settings.get("maximum_event_exposure", settings.get("maximum_single_market_exposure", 0.02)))
    min_capital = float(settings.get("minimum_capital_required", 1.0))
    min_annualized = float(settings.get("minimum_annualized_return", 0.02))
    max_days = float(settings.get("maximum_days_to_resolution", 1095))

    max_shares_by_depth = min(depths)
    max_shares_by_liquidity = min(depths) * liquidity_cap_fraction if liquidity_cap_fraction > 0 else max_shares_by_depth
    max_capital = bankroll * max_event_exposure
    max_shares_by_exposure = max_capital / net_ask_sum if net_ask_sum > 0 else 0.0
    executable_shares = max(0.0, min(max_shares_by_depth, max_shares_by_liquidity, max_shares_by_exposure))
    capital = executable_shares * net_ask_sum
    locked_profit = executable_shares * net_lock
    roi = locked_profit / capital if capital > 0 else None
    annualized = _annualized_return(roi, days_to_resolution)
    base.update({
        "capital_required": capital,
        "net_locked_profit": locked_profit,
        "expected_annualized_return": annualized,
        "max_executable_shares": executable_shares,
    })

    if capital < min_capital:
        rejected = {**base, "rejection_reason": f"low-depth lock: capital_required {capital:.4f} < {min_capital:.4f}"}
        status = {k: rejected.get(k) for k in STATUS_FIELDS}
        return None, rejected, status
    if days_to_resolution is None:
        rejected = {**base, "rejection_reason": "missing resolution date prevents annualized return test"}
        status = {k: rejected.get(k) for k in STATUS_FIELDS}
        return None, rejected, status
    if days_to_resolution > max_days:
        rejected = {**base, "rejection_reason": f"resolution horizon too long: {days_to_resolution:.1f} days > {max_days:.1f}"}
        status = {k: rejected.get(k) for k in STATUS_FIELDS}
        return None, rejected, status
    if annualized is None or annualized < min_annualized:
        rejected = {**base, "rejection_reason": "annualized return below threshold"}
        status = {k: rejected.get(k) for k in STATUS_FIELDS}
        return None, rejected, status

    actionable = {k: base.get(k) for k in ACTIONABLE_FIELDS}
    status = {k: actionable.get(k) for k in STATUS_FIELDS}
    return actionable, {}, status


def _seed_rows(cfg: EngineConfig, limit: int | None = None) -> list[dict[str, Any]]:
    seeds: dict[str, dict[str, Any]] = {}
    files = [
        cfg.output_root / "polymarket_predictions" / "predictions.csv",
        cfg.output_root / "polymarket_training" / "market_resolutions.csv",
        cfg.output_root / "polymarket_training" / "historical_resolutions.csv",
    ]
    for path in files:
        for row in read_csv_rows(path, limit=limit):
            locator = row.get("market_slug") or row.get("slug") or row.get("market_id") or row.get("condition_id") or row.get("conditionId") or ""
            if locator:
                seeds.setdefault(locator, dict(row))
    return list(seeds.values())[:limit] if limit else list(seeds.values())


def scan_dutch_book_arbitrage(
    cfg: EngineConfig,
    *,
    fetcher: Callable[[str], dict[str, Any] | None] | None = None,
    cycle: int = 1,
) -> dict[str, Any]:
    settings = dict(cfg.raw.get("dutch_book_monitor", {}) or {})
    if "bankroll" not in settings:
        settings["bankroll"] = cfg.raw.get("risk", {}).get("bankroll", 1000)
    if "slippage" not in settings:
        settings["slippage"] = cfg.raw.get("costs", {}).get("slippage", 0.01)
    if "transaction_cost" not in settings:
        settings["transaction_cost"] = cfg.raw.get("costs", {}).get("transaction_cost", 0.0)
    if "liquidity_cap_fraction" not in settings:
        settings["liquidity_cap_fraction"] = cfg.raw.get("risk", {}).get("liquidity_cap_fraction", 0.05)
    if "maximum_single_market_exposure" not in settings:
        settings["maximum_single_market_exposure"] = cfg.raw.get("risk", {}).get("maximum_single_market_exposure", 0.02)

    stale_after = int(settings.get("stale_after_seconds", 600))
    seed_limit = settings.get("seed_limit")
    seed_limit_int = int(seed_limit) if seed_limit not in (None, "") else None
    book_rows = read_csv_rows(cfg.output_root / "polymarket_training" / "websocket_market_features.csv")
    books = latest_live_books(book_rows, stale_after_seconds=stale_after)
    seeds = _seed_rows(cfg, limit=seed_limit_int)

    actionable: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    seen_events: set[str] = set()
    for seed in seeds:
        event_hint = _as_text(seed.get("event_slug") or seed.get("market_slug") or seed.get("slug") or seed.get("condition_id") or seed.get("market_id"))
        if event_hint in seen_events:
            continue
        seen_events.add(event_hint)
        candidate, reject, status = evaluate_dutch_book_candidate(seed, books, fetcher=fetcher, settings=settings, cycle=cycle)
        if candidate:
            actionable.append(candidate)
        if reject:
            rejected.append(reject)
        statuses.append(status)

    out = cfg.output_root / "polymarket_dutch_book"
    write_csv(out / "actionable_candidates.csv", actionable, fieldnames=ACTIONABLE_FIELDS)
    write_csv(out / "rejected_candidates.csv", rejected, fieldnames=REJECTED_FIELDS)
    write_csv(out / "group_status.csv", statuses, fieldnames=STATUS_FIELDS)
    summary = {
        "status": "ok",
        "dry_run_only": True,
        "orders_placed": 0,
        "cycle": cycle,
        "generated_at_utc": now_utc(),
        "seed_events": len(seeds),
        "live_books": len(books),
        "actionable_candidates": len(actionable),
        "rejected_candidates": len(rejected),
        "output_dir": str(out),
    }
    write_json(out / "summary.json", summary)
    write_json(out / "actionable_candidates.json", actionable)
    write_json(out / "rejected_candidates.json", rejected)
    return summary


def run_dutch_book_monitor(
    cfg: EngineConfig,
    *,
    cycles: int | None = None,
    sleep_seconds: int | None = None,
    websocket_seconds: int | None = None,
) -> dict[str, Any]:
    settings = cfg.raw.get("dutch_book_monitor", {}) or {}
    if cycles is None:
        cycles = int(settings.get("cycles", 1))
    if sleep_seconds is None:
        sleep_seconds = int(settings.get("sleep_seconds", 60))
    if websocket_seconds is None:
        websocket_seconds = int(settings.get("websocket_seconds", 60))
    cycles = max(1, cycles)
    summaries: list[dict[str, Any]] = []
    for cycle in range(1, cycles + 1):
        collect_websocket(cfg, websocket_seconds=websocket_seconds)
        normalize_websocket_file(cfg)
        summaries.append(scan_dutch_book_arbitrage(cfg, cycle=cycle))
        if cycle < cycles and sleep_seconds > 0:
            time.sleep(sleep_seconds)
    final = {
        "status": "ok",
        "dry_run_only": True,
        "orders_placed": 0,
        "cycles": cycles,
        "last_summary": summaries[-1] if summaries else {},
    }
    write_json(cfg.output_root / "polymarket_dutch_book" / "monitor_summary.json", final)
    return final


def main(config_path: str) -> dict[str, Any]:
    return scan_dutch_book_arbitrage(load_config(config_path))
