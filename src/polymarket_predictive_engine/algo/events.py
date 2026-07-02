"""Quote events: the immutable market-data unit the algo layer consumes.

A :class:`QuoteEvent` mirrors one normalised websocket feature row (see
``websocket_normaliser.FEATURE_FIELDS``). Rows without a usable timestamp or
without any of bid/ask/midpoint are unusable for trading decisions and map to
``None``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..utils import parse_timestamp, safe_float


def _price(value: Any) -> float | None:
    price = safe_float(value)
    if price is None or not 0 < price < 1:
        return None
    return price


@dataclass(frozen=True)
class QuoteEvent:
    timestamp: datetime
    timestamp_utc: str
    market_id: str
    asset_id: str
    market_slug: str
    question: str
    category: str
    close_time: str
    best_bid: float | None
    best_ask: float | None
    midpoint: float | None
    spread: float | None
    top_bid_size: float | None
    top_ask_size: float | None
    bid_depth_1pct: float | None
    ask_depth_1pct: float | None
    bid_depth_5pct: float | None
    ask_depth_5pct: float | None
    book_imbalance: float | None

    @classmethod
    def from_feature_row(cls, row: dict[str, Any]) -> "QuoteEvent | None":
        timestamp = parse_timestamp(row.get("source_timestamp")) or parse_timestamp(row.get("collected_at_utc"))
        if timestamp is None:
            return None
        best_bid = _price(row.get("best_bid"))
        best_ask = _price(row.get("best_ask"))
        midpoint = _price(row.get("midpoint"))
        if midpoint is None and best_bid is not None and best_ask is not None and best_bid <= best_ask:
            midpoint = (best_bid + best_ask) / 2.0
        if best_bid is None and best_ask is None and midpoint is None:
            return None
        spread = safe_float(row.get("spread"))
        if spread is None and best_bid is not None and best_ask is not None:
            spread = max(0.0, best_ask - best_bid)
        return cls(
            timestamp=timestamp,
            timestamp_utc=timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
            market_id=str(row.get("market") or row.get("market_id") or ""),
            asset_id=str(row.get("asset_id") or row.get("token_id") or ""),
            market_slug=str(row.get("market_slug") or ""),
            question=str(row.get("question") or ""),
            category=str(row.get("category") or "unknown"),
            close_time=str(row.get("close_time") or ""),
            best_bid=best_bid,
            best_ask=best_ask,
            midpoint=midpoint,
            spread=spread,
            top_bid_size=safe_float(row.get("top_bid_size")),
            top_ask_size=safe_float(row.get("top_ask_size")),
            bid_depth_1pct=safe_float(row.get("bid_depth_1pct")),
            ask_depth_1pct=safe_float(row.get("ask_depth_1pct")),
            bid_depth_5pct=safe_float(row.get("bid_depth_5pct")),
            ask_depth_5pct=safe_float(row.get("ask_depth_5pct")),
            book_imbalance=safe_float(row.get("book_imbalance")),
        )
