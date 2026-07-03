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


def classify_market_family(row: dict[str, Any]) -> str:
    """Resolve a stable research family from point-in-time market metadata.

    This is intentionally a metadata classifier, not a promotion gate. Mapping a
    row from ``unknown`` to e.g. ``macro_rates`` only creates a cleaner evidence
    bucket; the existing cohort, validation, and promotion gates still decide
    whether that bucket may ever be paper-traded.
    """
    if is_worldcup_winner_market(row):
        return "worldcup_2026_winner"
    category = str(row.get("category") or "").strip().lower().replace(" ", "_")
    text = _joined_text(
        row,
        (
            "category",
            "market_slug",
            "question",
            "event_slug",
            "event_title",
            "event_id",
            "market_id",
            "outcome",
        ),
    )

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
        interval = _crypto_updown_interval(row, text)
        return f"crypto_{asset}_updown_{interval}" if asset else f"crypto_updown_{interval}"

    if any(term in text for term in ("fed", "fomc", "interest rate", "interest rates", "rate cut", "rate hike", "bps")):
        return "macro_rates"
    if any(term in text for term in ("inflation", "cpi", "economy", "recession", "gdp", "unemployment")):
        return "macro_economy"
    if any(term in text for term in ("largest company", "market cap", "stock market", "stocks", "s p 500", "nasdaq", "dow jones")):
        return "equities_macro"

    if any(term in text for term in ("wimbledon", "us open", "australian open", "french open", "atp", "wta", "itf", "tennis")):
        if any(term in text for term in ("winner", "to win", "champion")):
            return "tennis_tennis_winner"
        if "set" in text:
            return "tennis_tennis_set"
        if "total" in text:
            return "tennis_tennis_total"
        return "tennis_match"
    if any(term in text for term in ("lol", "league of legends", "cs2", "counter strike", "valorant", "dota", "rainbow six", "honor of kings", "talent gaming", "esports", "e sports")) or " hok " in f" {text} ":
        return "esports_match"

    if any(term in text for term in ("openai", "anthropic", "chatgpt", "claude", "grok", "gemini", "best ai model", "ai model", "xai")):
        return "ai_model_leader"
    if any(term in text for term in ("scotus", "supreme court", "lawsuit", "legal", "ban", "approval", "regulation", "regulator")):
        return "policy_legal"
    if any(term in text for term in ("weather", "hurricane", "temperature", "rainfall", "snowfall", "wildfire")):
        return "weather"
    if any(term in text for term in ("gta vi", "gta 6", "grammy", "oscars", "box office", "album", "movie", "netflix")):
        return "culture_entertainment"
    if any(term in text for term in ("strait of hormuz", "war", "ceasefire", "tariff", "election", "president", "minister")):
        return "geopolitics"

    if "bitcoin" in text or " btc " in f" {text} ":
        return "crypto_btc_special"
    if "ethereum" in text or " eth " in f" {text} ":
        return "crypto_eth_special"
    if "solana" in text or " sol " in f" {text} ":
        return "crypto_sol_special"
    if "xrp" in text or "ripple" in text:
        return "crypto_xrp_special"
    if "crypto" in text:
        return "crypto_policy_special" if any(term in text for term in ("tax", "regulat", "policy")) else "crypto_special"

    if "nba" in text or "basketball" in text:
        return "basketball_nba_match" if "nba" in text else "basketball_match"
    if "mlb" in text or "baseball" in text:
        return "baseball_mlb_match" if "mlb" in text else "baseball_match"
    if any(term in text for term in ("mma", "ufc", "fight night", "mixed martial arts")):
        return "mma_match"
    if any(term in text for term in ("soccer", "fifa", "premier league", "la liga", "serie a", "bundesliga")):
        return "soccer_match"
    sports_terms = ("nfl", "nhl", "football", "golf")
    if any(term in text for term in sports_terms):
        return "sports_other"
    if category and category not in {"unknown", "uncategorised", "uncategorized"}:
        return category
    return "unknown"


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
    if category and category not in {"unknown", "uncategorised", "uncategorized"}:
        if category.replace("_", "") in {
            "sport",
            "sports",
            "sportsother",
            "tennis",
            "basketball",
            "baseball",
            "mma",
            "soccer",
        }:
            return classify_market_family(row)
        return category
    return classify_market_family(row)
