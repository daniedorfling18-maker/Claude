from __future__ import annotations

import re
from typing import Any


WORLDCUP_WINNER_CORRELATION_KEY = "worldcup_2026_winner"


def _joined_text(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    text = " ".join(str(row.get(key) or "") for key in keys).lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def is_worldcup_market(row: dict[str, Any]) -> bool:
    text = _joined_text(
        row,
        (
            "category",
            "market_slug",
            "question",
            "event_id",
            "correlation_key",
            "market_id",
        ),
    )
    return "worldcup" in text or "world cup" in text or "fifa" in text


def is_worldcup_winner_market(row: dict[str, Any]) -> bool:
    if not is_worldcup_market(row):
        return False
    text = _joined_text(row, ("category", "market_slug", "question", "event_id", "correlation_key"))
    if WORLDCUP_WINNER_CORRELATION_KEY in text:
        return True
    winner_terms = ("winner", "outright", "to win", "win the 2026", "win 2026", "champion")
    return any(term in text for term in winner_terms)


def normalised_correlation_key(row: dict[str, Any]) -> str:
    category = str(row.get("category") or "").strip().lower().replace(" ", "")
    if is_worldcup_winner_market(row) or category in {"worldcup", "fifaworldcup"}:
        return WORLDCUP_WINNER_CORRELATION_KEY
    return str(
        row.get("correlation_key")
        or row.get("event_id")
        or row.get("market_id")
        or row.get("market_slug")
        or ""
    )


def _crypto_updown_interval(row: dict[str, Any], text: str) -> str:
    if "updown 5m" in text or "updown 5 m" in text or re.search(r"\b5\s*(m|min|minute)s?\b", text):
        return "5m"
    if "updown 15m" in text or "updown 15 m" in text or re.search(r"\b15\s*(m|min|minute)s?\b", text):
        return "15m"
    if "daily" in text or re.search(
        r"\bon\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}",
        text,
    ):
        return "daily"
    return "event"


def signal_cohort(row: dict[str, Any]) -> str:
    if is_worldcup_winner_market(row):
        fundamental = row.get("fundamental_probability")
        has_fundamental = fundamental not in {None, ""}
        return "worldcup_2026_winner_fundamental" if has_fundamental else "worldcup_2026_winner_no_bookmaker"
    text = _joined_text(row, ("category", "market_slug", "question", "event_id", "market_id", "outcome"))
    outcome = str(row.get("outcome") or "").strip().lower()
    if "up or down" in text or "updown" in text:
        asset = ""
        if "xrp" in text or "ripple" in text:
            asset = "xrp"
        elif "bitcoin" in text or " btc " in f" {text} ":
            asset = "btc"
        elif "ethereum" in text or " eth " in f" {text} ":
            asset = "eth"
        elif "solana" in text or " sol " in f" {text} ":
            asset = "sol"
        if asset and outcome in {"up", "down"}:
            interval = _crypto_updown_interval(row, text)
            if row.get("crypto_model_status") == "scored":
                return f"exploratory_crypto_updown_live_model|crypto_{asset}_updown_{interval}|outcome={outcome}"
            if interval != "5m":
                return f"exploratory_historical_rule|crypto_{asset}_updown_{interval}|outcome={outcome}"
            if asset == "xrp" and outcome == "down":
                return f"exploratory_historical_rule|crypto_{asset}_updown_5m|outcome={outcome}"
            if asset in {"btc", "sol"} and outcome == "up":
                return f"exploratory_inverse_historical_rule|crypto_{asset}_updown_5m|outcome={outcome}"
            return f"exploratory_historical_rule|crypto_{asset}_updown_5m|outcome={outcome}"
    category = str(row.get("category") or "").strip().lower()
    return category or "uncategorised"
