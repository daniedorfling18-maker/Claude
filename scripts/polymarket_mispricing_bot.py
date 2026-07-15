#!/usr/bin/env python3
"""Polymarket mispricing scanner and guarded trader.

This script is intentionally safe by default. It can run in three modes:

- scan: discover markets, pull order books, and write snapshots only
- dry_run: produce buy/sell signals and paper-trade logs, but never place orders
- live: disabled in this legacy scanner; raises before any order client exists

A model probability source is required for edge calculation. Supply either:
- POLYMARKET_MODEL_PROBABILITIES_CSV, default inputs/polymarket/model_probabilities.csv
- POLYMARKET_MODEL_PROBABILITIES_JSON, mapping token IDs or market/outcome keys to probabilities

Supported CSV columns:
- token_id, probability
- market_slug, outcome, probability
- event_slug, market_slug, outcome, probability
- question, outcome, probability

The bot buys outcomes where model probability exceeds the executable ask.
It sells only if the executable bid exceeds model probability and a positive token position
is known from POLYMARKET_POSITIONS_CSV or the Data API wallet lookup.
"""

from __future__ import annotations

import csv
import json
import math
import os
import signal
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class BotConfig:
    mode: str
    execute_live: bool
    gamma_host: str
    clob_host: str
    data_host: str
    query: str
    tag_id: str
    event_slug: str
    limit: int
    scan_seconds: int
    scan_interval_seconds: int
    min_edge: float
    max_spread: float
    max_order_usd: float
    min_ask_size: float
    min_bid_size: float
    order_type: str
    model_csv: Path
    positions_csv: Path
    output_dir: Path
    wallet_address: str
    allow_sell_without_position: bool
    geoblock_required: bool

    @classmethod
    def from_env(cls) -> "BotConfig":
        mode = os.getenv("PM_MODE", os.getenv("POLYMARKET_MODE", "dry_run")).strip().lower()
        if mode not in {"scan", "dry_run", "live"}:
            raise ValueError("PM_MODE must be scan, dry_run, or live")

        return cls(
            mode=mode,
            execute_live=_env_bool("POLYMARKET_EXECUTE_LIVE", default=False),
            gamma_host=os.getenv("POLYMARKET_GAMMA_HOST", "https://gamma-api.polymarket.com").rstrip("/"),
            clob_host=os.getenv("POLYMARKET_CLOB_HOST", "https://clob.polymarket.com").rstrip("/"),
            data_host=os.getenv("POLYMARKET_DATA_HOST", "https://data-api.polymarket.com").rstrip("/"),
            query=os.getenv("POLYMARKET_QUERY", "world cup").strip(),
            tag_id=os.getenv("POLYMARKET_TAG_ID", "").strip(),
            event_slug=os.getenv("POLYMARKET_EVENT_SLUG", "").strip(),
            limit=int(os.getenv("POLYMARKET_EVENT_LIMIT", "50")),
            scan_seconds=int(os.getenv("POLYMARKET_SCAN_SECONDS", "270")),
            scan_interval_seconds=max(5, int(os.getenv("POLYMARKET_SCAN_INTERVAL_SECONDS", "15"))),
            min_edge=float(os.getenv("POLYMARKET_MIN_EDGE", "0.04")),
            max_spread=float(os.getenv("POLYMARKET_MAX_SPREAD", "0.04")),
            max_order_usd=float(os.getenv("POLYMARKET_MAX_ORDER_USD", "5")),
            min_ask_size=float(os.getenv("POLYMARKET_MIN_ASK_SIZE", "5")),
            min_bid_size=float(os.getenv("POLYMARKET_MIN_BID_SIZE", "5")),
            order_type=os.getenv("POLYMARKET_ORDER_TYPE", "FOK").strip().upper(),
            model_csv=Path(os.getenv("POLYMARKET_MODEL_PROBABILITIES_CSV", "inputs/polymarket/model_probabilities.csv")),
            positions_csv=Path(os.getenv("POLYMARKET_POSITIONS_CSV", "inputs/polymarket/positions.csv")),
            output_dir=Path(os.getenv("POLYMARKET_OUTPUT_DIR", "outputs/polymarket")),
            wallet_address=os.getenv("POLYMARKET_WALLET_ADDRESS", "").strip(),
            allow_sell_without_position=_env_bool("POLYMARKET_ALLOW_SELL_WITHOUT_POSITION", default=False),
            geoblock_required=_env_bool("POLYMARKET_GEOBLOCK_REQUIRED", default=True),
        )


@dataclass(frozen=True)
class OutcomeToken:
    event_slug: str
    event_title: str
    market_slug: str
    question: str
    condition_id: str
    close_time: str
    outcome: str
    token_id: str
    gamma_price: float | None
    tick_size: str
    neg_risk: bool


@dataclass(frozen=True)
class Book:
    token_id: str
    best_bid: float | None
    best_ask: float | None
    bid_size: float
    ask_size: float
    spread: float | None
    tick_size: str
    min_order_size: float
    neg_risk: bool


@dataclass(frozen=True)
class Opportunity:
    timestamp: str
    action: str
    event_slug: str
    event_title: str
    market_slug: str
    question: str
    outcome: str
    token_id: str
    fair_probability: float
    executable_price: float
    edge: float
    best_bid: float | None
    best_ask: float | None
    spread: float | None
    size_usd: float
    shares: float
    reason: str


_SHUTDOWN = False


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _on_signal(signum: int, frame: Any) -> None:
    global _SHUTDOWN
    _SHUTDOWN = True
    print(f"received signal {signum}; finishing current scan", flush=True)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def http_json(url: str, *, timeout: int = 20, method: str = "GET", body: bytes | None = None) -> Any:
    request = Request(
        url,
        data=body,
        method=method,
        headers={
            "accept": "application/json",
            "user-agent": "superbru-polymarket-mispricing-bot/0.1",
        },
    )
    with urlopen(request, timeout=timeout) as response:  # nosec B310 - known HTTPS API endpoints
        payload = response.read().decode("utf-8")
    return json.loads(payload)


def decode_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return [part.strip() for part in text.split(",") if part.strip()]
    return []


def parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


def parse_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def norm(text: Any) -> str:
    return str(text or "").strip().lower()


def key_parts(*parts: Any) -> str:
    return "|".join(norm(part) for part in parts)


def load_model_probabilities(config: BotConfig) -> dict[str, float]:
    probs: dict[str, float] = {}

    raw_json = os.getenv("POLYMARKET_MODEL_PROBABILITIES_JSON", "").strip()
    if raw_json:
        try:
            payload = json.loads(raw_json)
            if not isinstance(payload, dict):
                raise ValueError("POLYMARKET_MODEL_PROBABILITIES_JSON must be an object")
            for k, v in payload.items():
                p = parse_float(v)
                if p is not None and 0.0 <= p <= 1.0:
                    probs[norm(k)] = p
        except Exception as exc:
            raise ValueError(f"Invalid POLYMARKET_MODEL_PROBABILITIES_JSON: {exc}") from exc

    if config.model_csv.exists():
        with config.model_csv.open("r", newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                p = parse_float(row.get("probability") or row.get("fair_probability") or row.get("model_probability"))
                if p is None or not (0.0 <= p <= 1.0):
                    continue

                token_id = row.get("token_id") or row.get("asset_id")
                if token_id:
                    probs[norm(token_id)] = p

                event_slug = row.get("event_slug")
                market_slug = row.get("market_slug") or row.get("slug")
                question = row.get("question")
                outcome = row.get("outcome") or row.get("label")

                if market_slug and outcome:
                    probs[key_parts(market_slug, outcome)] = p
                if event_slug and market_slug and outcome:
                    probs[key_parts(event_slug, market_slug, outcome)] = p
                if question and outcome:
                    probs[key_parts(question, outcome)] = p

    return probs


def load_positions(config: BotConfig) -> dict[str, float]:
    positions: dict[str, float] = {}

    if config.positions_csv.exists():
        with config.positions_csv.open("r", newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                token_id = row.get("token_id") or row.get("asset_id")
                shares = parse_float(row.get("shares") or row.get("size") or row.get("balance"))
                if token_id and shares and shares > 0:
                    positions[norm(token_id)] = positions.get(norm(token_id), 0.0) + shares

    if config.wallet_address:
        try:
            url = f"{config.data_host}/positions?{urlencode({'user': config.wallet_address})}"
            payload = http_json(url, timeout=15)
            rows = payload if isinstance(payload, list) else payload.get("positions", []) if isinstance(payload, dict) else []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                token_id = row.get("asset") or row.get("asset_id") or row.get("token_id") or row.get("tokenId")
                shares = parse_float(row.get("size") or row.get("shares") or row.get("balance") or row.get("amount"))
                if token_id and shares and shares > 0:
                    positions[norm(token_id)] = positions.get(norm(token_id), 0.0) + shares
        except Exception as exc:
            print(f"warning: could not load live positions for {config.wallet_address}: {exc}", flush=True)

    return positions


def fair_probability_for(token: OutcomeToken, probs: dict[str, float]) -> float | None:
    for candidate in (
        token.token_id,
        key_parts(token.market_slug, token.outcome),
        key_parts(token.event_slug, token.market_slug, token.outcome),
        key_parts(token.question, token.outcome),
    ):
        p = probs.get(norm(candidate))
        if p is not None:
            return p
    return None


def check_geoblock(config: BotConfig) -> dict[str, Any]:
    try:
        geo = http_json("https://polymarket.com/api/geoblock", timeout=20)
    except Exception as exc:
        if config.geoblock_required:
            raise RuntimeError(f"geoblock check failed and POLYMARKET_GEOBLOCK_REQUIRED=true: {exc}") from exc
        print(f"warning: geoblock check failed but is not required: {exc}", flush=True)
        return {"blocked": None, "error": str(exc)}

    if geo.get("blocked"):
        raise RuntimeError(f"Polymarket trading blocked for this runner IP: {geo}")
    return geo


def discover_events(config: BotConfig) -> list[dict[str, Any]]:
    if config.event_slug:
        url = f"{config.gamma_host}/events?{urlencode({'slug': config.event_slug})}"
        payload = http_json(url)
        return payload if isinstance(payload, list) else [payload]

    query = config.query.lower()
    if query and _env_bool("POLYMARKET_PUBLIC_SEARCH_ENABLED", default=True):
        search_params = {
            "q": config.query,
            "events_status": "active",
            "limit_per_type": os.getenv("POLYMARKET_PUBLIC_SEARCH_LIMIT_PER_TYPE", "10"),
            "search_tags": "false",
            "search_profiles": "false",
        }
        try:
            payload = http_json(f"{config.gamma_host}/public-search?{urlencode(search_params)}")
            events = payload.get("events", []) if isinstance(payload, dict) else []
            if isinstance(events, list) and events:
                return events
        except Exception as exc:
            print(f"warning: public-search failed query={config.query!r}: {exc}", flush=True)

    params = {
        "active": "true",
        "closed": "false",
        "limit": str(config.limit),
        "order": "volume_24hr",
        "ascending": "false",
    }
    if config.tag_id:
        params["tag_id"] = config.tag_id
        params["related_tags"] = "true"
    url = f"{config.gamma_host}/events?{urlencode(params)}"
    events = http_json(url)
    if not isinstance(events, list):
        return []

    if not query:
        return events

    query_terms = [term for term in query.replace("-", " ").split() if term]
    filtered = []
    for event in events:
        haystack = " ".join(
            str(event.get(k, ""))
            for k in ("slug", "title", "ticker", "description", "category")
        ).lower()
        if all(term in haystack for term in query_terms) or any(term in haystack for term in query_terms):
            filtered.append(event)
    return filtered


def extract_tokens(events: list[dict[str, Any]]) -> list[OutcomeToken]:
    tokens: list[OutcomeToken] = []

    for event in events:
        event_slug = str(event.get("slug") or "")
        event_title = str(event.get("title") or event.get("question") or event_slug)
        markets = event.get("markets") or []
        if not isinstance(markets, list):
            continue

        for market in markets:
            if not isinstance(market, dict):
                continue

            if not parse_bool(market.get("enableOrderBook") or market.get("enable_order_book"), default=True):
                continue
            if parse_bool(market.get("closed"), default=False) or not parse_bool(market.get("active"), default=True):
                continue

            market_slug = str(market.get("slug") or "")
            question = str(market.get("question") or market.get("title") or market_slug)
            condition_id = str(market.get("conditionId") or market.get("condition_id") or "")
            close_time = str(
                market.get("endDate")
                or market.get("endDateIso")
                or market.get("end_time")
                or event.get("endDate")
                or event.get("endDateIso")
                or ""
            )
            outcomes = decode_list(market.get("outcomes"))
            outcome_prices = decode_list(market.get("outcomePrices") or market.get("outcome_prices"))
            token_ids = decode_list(
                market.get("clobTokenIds")
                or market.get("clob_token_ids")
                or market.get("tokenIds")
                or market.get("token_ids")
            )
            tick_size = str(
                market.get("minimum_tick_size")
                or market.get("minimumTickSize")
                or market.get("tick_size")
                or "0.01"
            )
            neg_risk = parse_bool(market.get("neg_risk") or market.get("negRisk"), default=False)

            if len(token_ids) != len(outcomes):
                continue

            for idx, token_id in enumerate(token_ids):
                if not token_id:
                    continue
                gamma_price = parse_float(outcome_prices[idx] if idx < len(outcome_prices) else None)
                tokens.append(
                    OutcomeToken(
                        event_slug=event_slug,
                        event_title=event_title,
                        market_slug=market_slug,
                        question=question,
                        condition_id=condition_id,
                        close_time=close_time,
                        outcome=str(outcomes[idx]),
                        token_id=str(token_id),
                        gamma_price=gamma_price,
                        tick_size=tick_size,
                        neg_risk=neg_risk,
                    )
                )

    return tokens


def get_orderbook(config: BotConfig, token: OutcomeToken) -> Book | None:
    url = f"{config.clob_host}/book?{urlencode({'token_id': token.token_id})}"
    payload = http_json(url, timeout=20)
    if not isinstance(payload, dict):
        return None

    bids_raw = payload.get("bids") or []
    asks_raw = payload.get("asks") or []

    bids: list[tuple[float, float]] = []
    asks: list[tuple[float, float]] = []

    for row in bids_raw:
        if not isinstance(row, dict):
            continue
        price = parse_float(row.get("price"))
        size = parse_float(row.get("size"))
        if price is not None and size is not None:
            bids.append((price, size))

    for row in asks_raw:
        if not isinstance(row, dict):
            continue
        price = parse_float(row.get("price"))
        size = parse_float(row.get("size"))
        if price is not None and size is not None:
            asks.append((price, size))

    best_bid_row = max(bids, key=lambda x: x[0], default=None)
    best_ask_row = min(asks, key=lambda x: x[0], default=None)
    best_bid = best_bid_row[0] if best_bid_row else None
    best_ask = best_ask_row[0] if best_ask_row else None
    bid_size = best_bid_row[1] if best_bid_row else 0.0
    ask_size = best_ask_row[1] if best_ask_row else 0.0
    spread = best_ask - best_bid if best_bid is not None and best_ask is not None else None

    return Book(
        token_id=token.token_id,
        best_bid=best_bid,
        best_ask=best_ask,
        bid_size=bid_size,
        ask_size=ask_size,
        spread=spread,
        tick_size=str(payload.get("tick_size") or token.tick_size or "0.01"),
        min_order_size=parse_float(payload.get("min_order_size")) or 0.0,
        neg_risk=parse_bool(payload.get("neg_risk"), default=token.neg_risk),
    )


def build_opportunities(
    tokens: list[OutcomeToken],
    books: dict[str, Book],
    probabilities: dict[str, float],
    positions: dict[str, float],
    config: BotConfig,
) -> list[Opportunity]:
    opportunities: list[Opportunity] = []
    ts = utc_now()

    for token in tokens:
        fair = fair_probability_for(token, probabilities)
        if fair is None:
            continue

        book = books.get(token.token_id)
        if book is None:
            continue
        if book.spread is None or book.spread > config.max_spread:
            continue

        # Buy when model says the outcome is underpriced at the executable ask.
        if book.best_ask is not None and book.ask_size >= config.min_ask_size:
            edge = fair - book.best_ask
            if edge >= config.min_edge:
                size_usd = min(config.max_order_usd, max(0.0, book.ask_size * book.best_ask))
                if size_usd >= max(book.min_order_size, 1.0):
                    shares = size_usd / book.best_ask if book.best_ask > 0 else 0.0
                    opportunities.append(
                        Opportunity(
                            timestamp=ts,
                            action="BUY",
                            event_slug=token.event_slug,
                            event_title=token.event_title,
                            market_slug=token.market_slug,
                            question=token.question,
                            outcome=token.outcome,
                            token_id=token.token_id,
                            fair_probability=fair,
                            executable_price=book.best_ask,
                            edge=edge,
                            best_bid=book.best_bid,
                            best_ask=book.best_ask,
                            spread=book.spread,
                            size_usd=size_usd,
                            shares=shares,
                            reason="fair_probability_above_best_ask",
                        )
                    )

        # Sell only if we know we own shares, unless explicitly overridden.
        known_position = positions.get(norm(token.token_id), 0.0)
        can_sell = config.allow_sell_without_position or known_position > 0
        if can_sell and book.best_bid is not None and book.bid_size >= config.min_bid_size:
            edge = book.best_bid - fair
            if edge >= config.min_edge:
                max_shares = book.bid_size
                if not config.allow_sell_without_position:
                    max_shares = min(max_shares, known_position)
                shares = min(max_shares, config.max_order_usd / book.best_bid if book.best_bid > 0 else 0.0)
                size_usd = shares * book.best_bid
                if shares > 0 and size_usd >= max(book.min_order_size, 1.0):
                    opportunities.append(
                        Opportunity(
                            timestamp=ts,
                            action="SELL",
                            event_slug=token.event_slug,
                            event_title=token.event_title,
                            market_slug=token.market_slug,
                            question=token.question,
                            outcome=token.outcome,
                            token_id=token.token_id,
                            fair_probability=fair,
                            executable_price=book.best_bid,
                            edge=edge,
                            best_bid=book.best_bid,
                            best_ask=book.best_ask,
                            spread=book.spread,
                            size_usd=size_usd,
                            shares=shares,
                            reason="best_bid_above_fair_probability",
                        )
                    )

    opportunities.sort(key=lambda x: x.edge, reverse=True)
    return opportunities


class LiveExecutor:
    """Fail-closed placeholder for the legacy scanner.

    The canonical packaged engine keeps live trading skeleton-only behind
    governance gates. This older script should not carry a second dormant order
    path.
    """

    def __init__(self, config: BotConfig):
        self.config = config
        raise RuntimeError(
            "Live execution is disabled in this paper-trading foundation. "
            "Use the packaged engine governance path after forward validation."
        )

    def place(self, opportunity: Opportunity, book: Book) -> dict[str, Any]:
        raise RuntimeError(
            "Live execution is disabled in this legacy scanner; "
            f"refusing {opportunity.action} for {book.token_id}."
        )


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def opportunity_to_row(opp: Opportunity) -> dict[str, Any]:
    return {
        "timestamp": opp.timestamp,
        "action": opp.action,
        "event_slug": opp.event_slug,
        "event_title": opp.event_title,
        "market_slug": opp.market_slug,
        "question": opp.question,
        "outcome": opp.outcome,
        "token_id": opp.token_id,
        "fair_probability": f"{opp.fair_probability:.6f}",
        "executable_price": f"{opp.executable_price:.6f}",
        "edge": f"{opp.edge:.6f}",
        "best_bid": "" if opp.best_bid is None else f"{opp.best_bid:.6f}",
        "best_ask": "" if opp.best_ask is None else f"{opp.best_ask:.6f}",
        "spread": "" if opp.spread is None else f"{opp.spread:.6f}",
        "size_usd": f"{opp.size_usd:.2f}",
        "shares": f"{opp.shares:.6f}",
        "reason": opp.reason,
    }


def token_snapshot_row(token: OutcomeToken, book: Book | None, fair: float | None) -> dict[str, Any]:
    return {
        "timestamp": utc_now(),
        "event_slug": token.event_slug,
        "event_title": token.event_title,
        "market_slug": token.market_slug,
        "condition_id": token.condition_id,
        "close_time": token.close_time,
        "question": token.question,
        "outcome": token.outcome,
        "token_id": token.token_id,
        "gamma_price": "" if token.gamma_price is None else f"{token.gamma_price:.6f}",
        "fair_probability": "" if fair is None else f"{fair:.6f}",
        "best_bid": "" if book is None or book.best_bid is None else f"{book.best_bid:.6f}",
        "best_ask": "" if book is None or book.best_ask is None else f"{book.best_ask:.6f}",
        "spread": "" if book is None or book.spread is None else f"{book.spread:.6f}",
        "bid_size": "" if book is None else f"{book.bid_size:.6f}",
        "ask_size": "" if book is None else f"{book.ask_size:.6f}",
        "tick_size": token.tick_size if book is None else book.tick_size,
        "neg_risk": token.neg_risk if book is None else book.neg_risk,
    }


def run_once(config: BotConfig, live_executor: LiveExecutor | None = None) -> tuple[int, int]:
    probabilities = load_model_probabilities(config)
    positions = load_positions(config)

    events = discover_events(config)
    tokens = extract_tokens(events)

    print(
        f"{utc_now()} discovered events={len(events)} tokens={len(tokens)} "
        f"model_probabilities={len(probabilities)} positions={len(positions)}",
        flush=True,
    )

    books: dict[str, Book] = {}
    snapshot_rows: list[dict[str, Any]] = []

    for token in tokens:
        try:
            book = get_orderbook(config, token)
            if book:
                books[token.token_id] = book
            snapshot_rows.append(token_snapshot_row(token, book, fair_probability_for(token, probabilities)))
        except Exception as exc:
            print(f"warning: failed orderbook token={token.token_id}: {exc}", flush=True)

    opportunities = build_opportunities(tokens, books, probabilities, positions, config)
    opportunity_rows = [opportunity_to_row(opp) for opp in opportunities]

    config.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(
        config.output_dir / "market_snapshot.csv",
        snapshot_rows,
        [
            "timestamp",
            "event_slug",
            "event_title",
            "market_slug",
            "condition_id",
            "close_time",
            "question",
            "outcome",
            "token_id",
            "gamma_price",
            "fair_probability",
            "best_bid",
            "best_ask",
            "spread",
            "bid_size",
            "ask_size",
            "tick_size",
            "neg_risk",
        ],
    )
    write_csv(
        config.output_dir / "opportunities.csv",
        opportunity_rows,
        [
            "timestamp",
            "action",
            "event_slug",
            "event_title",
            "market_slug",
            "question",
            "outcome",
            "token_id",
            "fair_probability",
            "executable_price",
            "edge",
            "best_bid",
            "best_ask",
            "spread",
            "size_usd",
            "shares",
            "reason",
        ],
    )

    execution_rows: list[dict[str, Any]] = []
    for opp in opportunities:
        book = books.get(opp.token_id)
        if book is None:
            continue
        row = opportunity_to_row(opp)
        if config.mode == "live" and config.execute_live and live_executor is not None:
            try:
                response = live_executor.place(opp, book)
                row["execution_mode"] = "live"
                row["execution_response"] = json.dumps(response, default=str)
                print(
                    f"LIVE {opp.action} {opp.outcome} edge={opp.edge:.4f} "
                    f"price={opp.executable_price:.4f} response={response}",
                    flush=True,
                )
            except Exception as exc:
                row["execution_mode"] = "live_error"
                row["execution_response"] = f"{type(exc).__name__}: {exc}"
                print(f"error: live execution failed for {opp.token_id}: {exc}", flush=True)
        else:
            row["execution_mode"] = config.mode
            row["execution_response"] = "not_submitted"
            print(
                f"{config.mode.upper()} {opp.action} {opp.outcome} edge={opp.edge:.4f} "
                f"price={opp.executable_price:.4f} size_usd={opp.size_usd:.2f} "
                f"{opp.question}",
                flush=True,
            )
        execution_rows.append(row)

    write_csv(
        config.output_dir / "execution_log.csv",
        execution_rows,
        [
            "timestamp",
            "execution_mode",
            "action",
            "event_slug",
            "event_title",
            "market_slug",
            "question",
            "outcome",
            "token_id",
            "fair_probability",
            "executable_price",
            "edge",
            "best_bid",
            "best_ask",
            "spread",
            "size_usd",
            "shares",
            "reason",
            "execution_response",
        ],
    )

    return len(tokens), len(opportunities)


def main() -> int:
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    config = BotConfig.from_env()
    config.output_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"starting Polymarket bot mode={config.mode} execute_live={config.execute_live} "
        f"query={config.query!r} tag_id={config.tag_id!r} event_slug={config.event_slug!r} "
        f"scan_seconds={config.scan_seconds} interval={config.scan_interval_seconds}s",
        flush=True,
    )

    live_executor = None
    if config.mode == "live":
        if not config.execute_live:
            raise RuntimeError("PM_MODE=live requires POLYMARKET_EXECUTE_LIVE=true")
        geo = check_geoblock(config)
        print(f"geoblock={geo}", flush=True)
        live_executor = LiveExecutor(config)
    else:
        print("geoblock check skipped because this run is not live execution", flush=True)

    deadline = time.monotonic() + max(1, config.scan_seconds)
    scans = 0
    total_opportunities = 0

    while not _SHUTDOWN and time.monotonic() < deadline:
        scans += 1
        try:
            _token_count, opp_count = run_once(config, live_executor)
            total_opportunities += opp_count
        except Exception:
            traceback.print_exc()
        remaining = deadline - time.monotonic()
        if remaining <= 0 or _SHUTDOWN:
            break
        time.sleep(min(config.scan_interval_seconds, remaining))

    print(f"completed scans={scans} total_opportunities={total_opportunities}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
