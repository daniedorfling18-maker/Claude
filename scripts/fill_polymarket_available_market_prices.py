from __future__ import annotations

import argparse
import csv
import json
import re
import time
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd


GAMMA_SEARCH_URL = "https://gamma-api.polymarket.com/public-search"
CLOB_PRICE_URL = "https://clob.polymarket.com/price"
MATCH_RESULT_SELECTIONS = ("home_win", "draw", "away_win")

TEAM_ALIASES_RAW: dict[str, tuple[str, ...]] = {
    "Bosnia & Herzegovina": ("Bosnia and Herzegovina", "Bosnia-Herzegovina", "Bosnia"),
    "Cape Verde": ("Cabo Verde",),
    "Curacao": ("Curaçao",),
    "Czech Republic": ("Czechia",),
    "DR Congo": ("Democratic Republic of Congo", "Congo DR", "Congo-Kinshasa", "D R Congo"),
    "Ivory Coast": ("Cote d'Ivoire", "Côte d'Ivoire", "Cote d Ivoire"),
    "South Korea": ("Korea Republic", "Republic of Korea"),
    "United States": ("USA", "U.S.A.", "USMNT", "U.S. Men's National Team"),
    "Netherlands": ("Holland",),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fill a Polymarket prices CSV for available market types. "
            "Currently auto-discovers fixture match-result markets and can preserve user-supplied futures rows."
        )
    )
    parser.add_argument("--predictions-csv", default="outputs/latest/predictions.csv")
    parser.add_argument("--existing-prices-csv", default="")
    parser.add_argument("--out-csv", default="inputs/polymarket_available_market_prices.csv")
    parser.add_argument("--market-types", default="match_result")
    parser.add_argument("--limit-per-type", type=int, default=10)
    parser.add_argument("--sleep-seconds", type=float, default=0.15)
    return parser


def http_json(url: str, params: dict[str, Any], timeout: int = 20) -> Any:
    full_url = f"{url}?{urlencode(params, doseq=True)}"
    req = Request(full_url, headers={"User-Agent": "superbru-polymarket-available-market-fill/1.0"})
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_jsonish(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        return []
    value = value.strip()
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


def norm_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def team_alias_map() -> dict[str, set[str]]:
    aliases: dict[str, set[str]] = {}
    for canonical, raw_aliases in TEAM_ALIASES_RAW.items():
        terms = {norm_text(canonical), *(norm_text(alias) for alias in raw_aliases)}
        terms = {term for term in terms if term}
        for term in terms:
            aliases[term] = set(terms)
    return aliases


TEAM_ALIASES = team_alias_map()


def team_terms(team: str) -> set[str]:
    base = norm_text(team)
    if not base:
        return set()
    return {base, *TEAM_ALIASES.get(base, set())}


def team_query_terms(team: str) -> list[str]:
    base = norm_text(team)
    terms = team_terms(team)
    if base:
        ordered = [base] + sorted(term for term in terms if term != base and len(term) > 2)
    else:
        ordered = sorted(term for term in terms if len(term) > 2)
    # Avoid overly broad search queries such as just "korea" or "bosnia" unless they are the only option.
    broad = {"korea", "bosnia", "congo"}
    filtered = [term for term in ordered if term not in broad]
    return filtered or ordered


def text_contains_team(text: str, team: str) -> bool:
    text_n = norm_text(text)
    return any(term and term in text_n for term in team_terms(team))


def team_match(text: str, home_team: str, away_team: str) -> bool:
    return text_contains_team(text, home_team) and text_contains_team(text, away_team)


def requested_market_types(raw: str) -> set[str]:
    values = {item.strip().lower() for item in str(raw or "").split(",") if item.strip()}
    return values or {"match_result"}


def get_taker_buy_price(token_id: str, sleep_seconds: float) -> tuple[str, str]:
    time.sleep(sleep_seconds)
    data = http_json(CLOB_PRICE_URL, {"token_id": token_id, "side": "SELL"})
    price = data.get("price")
    if price is None:
        raise ValueError(f"No price returned for token_id={token_id}")
    return str(price), "clob_best_ask"


def outcome_selection(outcome: str, market_text: str, home_team: str, away_team: str) -> str:
    outcome_n = norm_text(outcome)
    market_n = norm_text(market_text)
    home_terms = team_terms(home_team)
    away_terms = team_terms(away_team)

    if outcome_n in {"draw", "tie"} or "draw" in outcome_n:
        return "draw"
    if any(term and term in outcome_n for term in home_terms):
        return "home_win"
    if any(term and term in outcome_n for term in away_terms):
        return "away_win"

    if outcome_n == "yes":
        if "draw" in market_n or "tie" in market_n:
            return "draw"
        home_win_terms = [
            f"{term} win" for term in home_terms
        ] + [
            f"{term} to win" for term in home_terms
        ] + [
            f"will {term} win" for term in home_terms
        ] + [
            f"{term} beats" for term in home_terms
        ]
        away_win_terms = [
            f"{term} win" for term in away_terms
        ] + [
            f"{term} to win" for term in away_terms
        ] + [
            f"will {term} win" for term in away_terms
        ] + [
            f"{term} beats" for term in away_terms
        ]
        if any(term in market_n for term in home_win_terms):
            return "home_win"
        if any(term in market_n for term in away_win_terms):
            return "away_win"

    return ""


def extract_market_tokens(market: dict[str, Any], home_team: str, away_team: str, sleep_seconds: float) -> dict[str, dict[str, Any]]:
    market_text = " ".join(
        str(market.get(key, ""))
        for key in ["question", "slug", "description", "groupItemTitle", "sportsMarketType"]
    )
    outcomes = [str(item) for item in parse_jsonish(market.get("outcomes"))]
    gamma_prices = [str(item) for item in parse_jsonish(market.get("outcomePrices"))]
    token_ids = [str(item) for item in parse_jsonish(market.get("clobTokenIds"))]

    found: dict[str, dict[str, Any]] = {}

    for idx, outcome in enumerate(outcomes):
        selection = outcome_selection(outcome, market_text, home_team, away_team)
        if selection not in MATCH_RESULT_SELECTIONS:
            continue

        token_id = token_ids[idx] if idx < len(token_ids) else ""
        price = ""
        price_source = ""

        if token_id:
            try:
                price, price_source = get_taker_buy_price(token_id, sleep_seconds)
            except Exception:
                price = gamma_prices[idx] if idx < len(gamma_prices) else ""
                price_source = "gamma_outcome_price_fallback" if price else "price_fetch_failed"
        else:
            price = gamma_prices[idx] if idx < len(gamma_prices) else ""
            price_source = "gamma_outcome_price" if price else "no_token_id"

        if price and selection not in found:
            found[selection] = {
                "yes_price": price,
                "price_source": price_source,
                "polymarket_market_id": market.get("id", ""),
                "polymarket_question": market.get("question", ""),
                "polymarket_outcome": outcome,
                "polymarket_token_id": token_id,
                "fees_enabled": str(market.get("feesEnabled", True)).lower(),
            }

    return found


def search_queries(home_team: str, away_team: str) -> list[str]:
    home_terms = team_query_terms(home_team)
    away_terms = team_query_terms(away_team)
    queries: list[str] = []
    for home in home_terms[:3]:
        for away in away_terms[:3]:
            queries.append(f"{home} {away}")
            queries.append(f"{home} vs {away}")
    deduped: list[str] = []
    seen: set[str] = set()
    for query in queries:
        if query not in seen:
            seen.add(query)
            deduped.append(query)
    return deduped


def find_match_result_prices(home_team: str, away_team: str, *, limit_per_type: int, sleep_seconds: float) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    debug: list[str] = []

    for query in search_queries(home_team, away_team):
        search = http_json(
            GAMMA_SEARCH_URL,
            {
                "q": query,
                "limit_per_type": limit_per_type,
                "events_status": "active",
                "search_profiles": "false",
                "search_tags": "false",
                "keep_closed_markets": 0,
            },
        )

        for event in search.get("events") or []:
            event_text = " ".join(
                str(event.get(key, ""))
                for key in ["title", "subtitle", "slug", "description", "ticker"]
            )
            if not team_match(event_text, home_team, away_team):
                debug.append(f"event_skip_team:{event.get('title')}")
                continue

            for market in event.get("markets") or []:
                if market.get("closed") is True or market.get("active") is False:
                    continue
                market_tokens = extract_market_tokens(market, home_team, away_team, sleep_seconds)
                for selection, token in market_tokens.items():
                    found.setdefault(
                        selection,
                        {
                            **token,
                            "polymarket_event": event.get("title", ""),
                            "matched_query": query,
                        },
                    )

                if all(selection in found for selection in MATCH_RESULT_SELECTIONS):
                    return found

    if debug and not found:
        found["_debug"] = {
            "polymarket_question": "; ".join(debug[:3]),
        }
    return found


def blank_price_row(row: pd.Series, selection: str) -> dict[str, Any]:
    return {
        "match_id": row.get("match_id", ""),
        "home_team": row.get("home_team", ""),
        "away_team": row.get("away_team", ""),
        "market_type": "match_result",
        "selection": selection,
        "yes_price": "",
        "fees_enabled": "true",
        "category": "sports",
        "taker_fee_rate": "",
        "price_source": "no_match",
        "polymarket_event": "",
        "polymarket_market_id": "",
        "polymarket_question": "",
        "polymarket_outcome": "",
        "polymarket_token_id": "",
        "matched_query": "",
    }


def build_match_result_rows(predictions_csv: str, *, limit_per_type: int, sleep_seconds: float) -> list[dict[str, Any]]:
    predictions = pd.read_csv(predictions_csv).fillna("")
    rows: list[dict[str, Any]] = []

    for _, prediction in predictions.iterrows():
        home = str(prediction.get("home_team", "")).strip()
        away = str(prediction.get("away_team", "")).strip()
        found = find_match_result_prices(home, away, limit_per_type=limit_per_type, sleep_seconds=sleep_seconds)

        for selection in MATCH_RESULT_SELECTIONS:
            base = blank_price_row(prediction, selection)
            token = found.get(selection)
            if token:
                base.update(
                    {
                        "yes_price": token.get("yes_price", ""),
                        "fees_enabled": token.get("fees_enabled", "true"),
                        "price_source": token.get("price_source", ""),
                        "polymarket_event": token.get("polymarket_event", ""),
                        "polymarket_market_id": token.get("polymarket_market_id", ""),
                        "polymarket_question": token.get("polymarket_question", ""),
                        "polymarket_outcome": token.get("polymarket_outcome", ""),
                        "polymarket_token_id": token.get("polymarket_token_id", ""),
                        "matched_query": token.get("matched_query", ""),
                    }
                )
            else:
                debug = found.get("_debug", {})
                base["polymarket_question"] = debug.get("polymarket_question", "")
            rows.append(base)

        filled = sum(1 for selection in MATCH_RESULT_SELECTIONS if found.get(selection, {}).get("yes_price"))
        print(f"{home} vs {away}: filled {filled}/3 match-result prices")

    return rows


def load_existing_rows(path: str) -> list[dict[str, Any]]:
    if not path:
        return []
    existing_path = Path(path)
    if not existing_path.exists():
        return []
    return pd.read_csv(existing_path).fillna("").to_dict("records")


def main() -> int:
    args = build_parser().parse_args()
    market_types = requested_market_types(args.market_types)

    rows: list[dict[str, Any]] = []
    if "match_result" in market_types:
        rows.extend(
            build_match_result_rows(
                args.predictions_csv,
                limit_per_type=args.limit_per_type,
                sleep_seconds=args.sleep_seconds,
            )
        )

    # Preserve any manually maintained futures rows from an existing price file.
    for existing in load_existing_rows(args.existing_prices_csv):
        if str(existing.get("market_type", "")).strip() != "match_result":
            rows.append(existing)

    out_path = Path(args.out_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "match_id",
        "home_team",
        "away_team",
        "market_type",
        "selection",
        "yes_price",
        "fees_enabled",
        "category",
        "taker_fee_rate",
        "price_source",
        "polymarket_event",
        "polymarket_market_id",
        "polymarket_question",
        "polymarket_outcome",
        "polymarket_token_id",
        "matched_query",
    ]

    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    filled = sum(1 for row in rows if str(row.get("yes_price", "")).strip())
    print()
    print(f"Wrote {out_path}")
    print(f"Filled {filled}/{len(rows)} prices")
    print("Missing rows mean Polymarket has no active matched market, or the search heuristic could not identify the outcome.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
