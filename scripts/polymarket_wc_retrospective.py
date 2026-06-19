#!/usr/bin/env python3
"""Audit Polymarket match-result coverage for WC 2026 fixtures.

This deliberately starts as a diagnostic-only tool. Polymarket can be useful as
an external sentiment/outcome market, but it often lists outrights, group
markets, and props whose resolution rules do not match Superbru score picks.

The script searches public Polymarket market discovery for each fixture, rejects
props/outrights, and reports only plausible match-result markets. If a fixture
has a kickoff time and a candidate market exposes CLOB token IDs, the script can
also fetch the last YES-price snapshot before kickoff from Polymarket's public
price-history endpoint.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

GAMMA_SEARCH_URL = "https://gamma-api.polymarket.com/public-search"
CLOB_PRICE_HISTORY_URL = "https://clob.polymarket.com/prices-history"

TEAM_ALIASES: dict[str, tuple[str, ...]] = {
    "ALG": ("algeria",),
    "ARG": ("argentina",),
    "AUS": ("australia",),
    "AUT": ("austria",),
    "BEL": ("belgium",),
    "BIH": ("bosnia", "bosnia and herzegovina", "bosnia & herzegovina"),
    "BRA": ("brazil",),
    "CAN": ("canada",),
    "CIV": ("ivory coast", "cote d'ivoire", "cote divoire"),
    "COD": ("congo dr", "dr congo", "democratic republic of congo"),
    "COL": ("colombia",),
    "CPV": ("cape verde", "cabo verde"),
    "CRO": ("croatia",),
    "CUR": ("curacao", "curacao"),
    "CZE": ("czechia", "czech republic"),
    "ECU": ("ecuador",),
    "EGY": ("egypt",),
    "ENG": ("england",),
    "ESP": ("spain",),
    "FRA": ("france",),
    "GER": ("germany",),
    "GHA": ("ghana",),
    "HAI": ("haiti",),
    "IRI": ("iran",),
    "IRQ": ("iraq",),
    "JOR": ("jordan",),
    "JPN": ("japan",),
    "KOR": ("south korea", "korea republic"),
    "KSA": ("saudi arabia",),
    "MAR": ("morocco",),
    "MEX": ("mexico",),
    "NED": ("netherlands", "holland"),
    "NOR": ("norway",),
    "NZL": ("new zealand",),
    "PAN": ("panama",),
    "PAR": ("paraguay",),
    "POR": ("portugal",),
    "QAT": ("qatar",),
    "RSA": ("south africa",),
    "SCO": ("scotland",),
    "SEN": ("senegal",),
    "SUI": ("switzerland",),
    "SWE": ("sweden",),
    "TUN": ("tunisia",),
    "TUR": ("turkey", "turkiye"),
    "URU": ("uruguay",),
    "USA": ("united states", "usa", "usmnt"),
    "UZB": ("uzbekistan",),
}

TEAM_TO_CODE = {alias: code for code, aliases in TEAM_ALIASES.items() for alias in aliases}

PROP_TERMS = (
    "announcer",
    "announcers",
    "say during",
    "mention",
    "player",
    "goalscorer",
    "top goalscorer",
    "cards",
    "corners",
    "penalty",
    "var",
    "red card",
    "yellow card",
)

OUTRIGHT_TERMS = (
    "group",
    "winner",
    "champion",
    "continent",
    "reach",
    "advance",
    "qualify",
    "round of",
    "quarterfinal",
    "semifinal",
    "final",
    "unbeaten",
    "nation of",
)

MATCH_RESULT_TERMS = (" beat ", " vs ", " vs. ", "defeat")


@dataclass(frozen=True)
class Fixture:
    home: str
    away: str
    commence_time: str = ""
    actual_home: int | None = None
    actual_away: int | None = None

    @property
    def home_code(self) -> str:
        return team_code(self.home)

    @property
    def away_code(self) -> str:
        return team_code(self.away)


@dataclass(frozen=True)
class CandidateMarket:
    fixture: Fixture
    question: str
    slug: str
    active: bool | None
    closed: bool | None
    volume: float | None
    liquidity: float | None
    outcome_prices: str
    clob_token_ids: str
    yes_token_id: str
    match_type: str
    pre_kickoff_yes_price: float | None = None
    pre_kickoff_price_time: str = ""


def main() -> int:
    args = parse_args()
    fixtures = load_fixtures(args)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[CandidateMarket] = []
    misses: list[Fixture] = []
    for fixture in fixtures:
        candidates = search_fixture_markets(fixture, pause_seconds=args.pause_seconds)
        if args.fetch_history:
            candidates = [with_pre_kickoff_price(candidate, args.before_kickoff_minutes) for candidate in candidates]
        rows.extend(candidates)
        if not candidates:
            misses.append(fixture)

    candidates_path = out_dir / "polymarket_wc_candidates.csv"
    misses_path = out_dir / "polymarket_wc_misses.csv"
    summary_path = out_dir / "polymarket_wc_1x2_summary.csv"
    write_candidates(candidates_path, rows)
    write_misses(misses_path, misses)
    summaries = build_1x2_summaries(rows)
    write_1x2_summaries(summary_path, summaries)

    print(f"Polymarket WC retrospective audit")
    print(f"fixtures checked: {len(fixtures)}")
    print(f"candidate match-result markets found: {len(rows)}")
    print(f"fixtures with usable 1X2 Polymarket snapshot: {len(summaries)}")
    print(f"fixtures with no usable Polymarket match-result market: {len(misses)}")
    print(f"candidates: {candidates_path}")
    print(f"1x2 summary: {summary_path}")
    print(f"misses: {misses_path}")
    scored = [row for row in summaries if row["favourite_correct"] != ""]
    if scored:
        hit_rate = sum(1 for row in scored if row["favourite_correct"] == "True") / len(scored)
        print(f"Polymarket favourite hit rate on scored snapshots: {hit_rate:.1%} ({len(scored)} matches)")
    if rows:
        print("\nTop candidates:")
        for row in rows[: min(20, len(rows))]:
            price = "" if row.pre_kickoff_yes_price is None else f" preKO={row.pre_kickoff_yes_price:.3f}"
            print(f"- {row.fixture.home} vs {row.fixture.away}: {row.question} [{row.match_type}]{price}")
    else:
        print("\nNo usable match-result markets found. Do not blend Polymarket into production for this sample.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit Polymarket WC match-result market coverage")
    parser.add_argument("--fixtures", action="append", default=[], help="Fixture CSV path. May be supplied more than once.")
    parser.add_argument("--use-grid-validation-matches", action="store_true", help="Also include historical matches from wc_grid_validation.py")
    parser.add_argument("--fetch-history", action="store_true", help="Fetch last YES price before kickoff for candidates with token IDs")
    parser.add_argument("--before-kickoff-minutes", type=int, default=90, help="Target snapshot before kickoff")
    parser.add_argument("--pause-seconds", type=float, default=0.2, help="Polite pause between public API searches")
    parser.add_argument("--out-dir", default="outputs/polymarket-wc-retrospective")
    return parser.parse_args()


def load_fixtures(args: argparse.Namespace) -> list[Fixture]:
    fixtures: dict[tuple[str, str], Fixture] = {}

    for path_text in args.fixtures:
        path = Path(path_text)
        if not path.exists():
            raise FileNotFoundError(path)
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                fixture = Fixture(
                    home=row.get("home_team", "").strip(),
                    away=row.get("away_team", "").strip(),
                    commence_time=row.get("commence_time", "").strip(),
                )
                add_fixture(fixtures, fixture)

    if args.use_grid_validation_matches:
        from scripts.wc_grid_validation import MATCHES

        for home, away, actual_home, actual_away, *_ in MATCHES:
            add_fixture(
                fixtures,
                Fixture(home=home, away=away, actual_home=int(actual_home), actual_away=int(actual_away)),
            )

    if not fixtures:
        raise SystemExit("Provide at least one --fixtures file or --use-grid-validation-matches")
    return list(fixtures.values())


def add_fixture(fixtures: dict[tuple[str, str], Fixture], fixture: Fixture) -> None:
    key = (team_code(fixture.home), team_code(fixture.away))
    if not key[0] or not key[1]:
        return
    current = fixtures.get(key)
    if current is None:
        fixtures[key] = fixture
        return
    fixtures[key] = Fixture(
        home=current.home or fixture.home,
        away=current.away or fixture.away,
        commence_time=current.commence_time or fixture.commence_time,
        actual_home=current.actual_home if current.actual_home is not None else fixture.actual_home,
        actual_away=current.actual_away if current.actual_away is not None else fixture.actual_away,
    )


def search_fixture_markets(fixture: Fixture, pause_seconds: float = 0.2) -> list[CandidateMarket]:
    home_alias = TEAM_ALIASES.get(fixture.home_code, (fixture.home.lower(),))[0]
    away_alias = TEAM_ALIASES.get(fixture.away_code, (fixture.away.lower(),))[0]
    queries = [
        f"World Cup {home_alias} {away_alias}",
        f"Will {home_alias} beat {away_alias} World Cup",
        f"{home_alias} vs {away_alias} World Cup",
    ]
    candidates: list[CandidateMarket] = []
    seen: set[str] = set()
    for query in queries:
        payload = http_json(GAMMA_SEARCH_URL, {"q": query})
        for market in iter_markets(payload):
            candidate = classify_market(fixture, market)
            if candidate and candidate.slug not in seen:
                seen.add(candidate.slug)
                candidates.append(candidate)
        time.sleep(max(0.0, pause_seconds))
    return sorted(candidates, key=lambda c: ((c.volume or 0.0), (c.liquidity or 0.0)), reverse=True)


def iter_markets(payload: dict[str, Any]) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    for market in payload.get("markets") or []:
        if isinstance(market, dict):
            markets.append(market)
    for event in payload.get("events") or []:
        event_context = " ".join(
            str(value or "")
            for value in (
                event.get("title"),
                event.get("slug"),
                " ".join(str(tag.get("label", "")) for tag in event.get("tags") or [] if isinstance(tag, dict)),
            )
        )
        for market in event.get("markets") or []:
            if isinstance(market, dict):
                enriched = dict(market)
                enriched["_event_context"] = event_context
                markets.append(enriched)
    return markets


def classify_market(fixture: Fixture, market: dict[str, Any]) -> CandidateMarket | None:
    question = str(market.get("question") or market.get("title") or "")
    slug = str(market.get("slug") or "")
    event_context = str(market.get("_event_context") or "")
    text = normalize_text(f"{question} {slug} {event_context}")
    if "wbc" in text or "baseball" in text:
        return None
    if "world cup" not in text and "fifa" not in text and "wc 26" not in text:
        return None
    if not contains_team(text, fixture.home_code) or not contains_team(text, fixture.away_code):
        return None
    if any(term in text for term in PROP_TERMS):
        return None
    if any(term in text for term in OUTRIGHT_TERMS) and not any(term in text for term in MATCH_RESULT_TERMS):
        return None
    if not any(term in text for term in MATCH_RESULT_TERMS):
        return None

    match_type = "binary_home_win"
    home_aliases = TEAM_ALIASES.get(fixture.home_code, (fixture.home.lower(),))
    away_aliases = TEAM_ALIASES.get(fixture.away_code, (fixture.away.lower(),))
    if "end in a draw" in text or "finish in a draw" in text:
        match_type = "draw_market"
    elif any(f"will {normalize_text(alias)} win" in text for alias in home_aliases):
        match_type = "home_win_market"
    elif any(f"will {normalize_text(alias)} win" in text for alias in away_aliases):
        match_type = "away_win_market"
    elif any(
        f"will {normalize_text(home_alias)} beat {normalize_text(away_alias)}" in text
        for home_alias in home_aliases
        for away_alias in away_aliases
    ):
        match_type = "home_win_market"
    elif any(
        f"will {normalize_text(away_alias)} beat {normalize_text(home_alias)}" in text
        for home_alias in home_aliases
        for away_alias in away_aliases
    ):
        match_type = "away_win_market"
    elif " vs " in text or " vs. " in text:
        match_type = "match_result_unknown_resolution"

    token_ids = parse_jsonish_list(market.get("clobTokenIds"))
    yes_token_id = str(token_ids[0]) if token_ids else ""
    return CandidateMarket(
        fixture=fixture,
        question=question,
        slug=slug,
        active=bool_or_none(market.get("active")),
        closed=bool_or_none(market.get("closed")),
        volume=float_or_none(market.get("volume") or market.get("volumeNum")),
        liquidity=float_or_none(market.get("liquidity") or market.get("liquidityNum")),
        outcome_prices=str(market.get("outcomePrices") or ""),
        clob_token_ids=str(market.get("clobTokenIds") or ""),
        yes_token_id=yes_token_id,
        match_type=match_type,
    )


def with_pre_kickoff_price(candidate: CandidateMarket, before_kickoff_minutes: int) -> CandidateMarket:
    if not candidate.fixture.commence_time or not candidate.yes_token_id:
        return candidate
    kickoff = parse_iso(candidate.fixture.commence_time)
    target_ts = int(kickoff.timestamp()) - before_kickoff_minutes * 60
    start_ts = target_ts - 24 * 3600
    payload = http_json(
        CLOB_PRICE_HISTORY_URL,
        {
            "market": candidate.yes_token_id,
            "startTs": start_ts,
            "endTs": target_ts,
            "interval": "1h",
            "fidelity": 60,
        },
    )
    history = payload.get("history") or []
    if not history:
        return candidate
    latest = max((point for point in history if "t" in point and "p" in point), key=lambda point: point["t"], default=None)
    if not latest:
        return candidate
    return CandidateMarket(
        **{
            **candidate.__dict__,
            "pre_kickoff_yes_price": float_or_none(latest.get("p")),
            "pre_kickoff_price_time": datetime.fromtimestamp(float(latest["t"]), tz=timezone.utc).isoformat(),
        }
    )


def http_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    query = urlencode({key: value for key, value in params.items() if value is not None})
    request = Request(f"{url}?{query}", headers={"User-Agent": "superbru-score-engine/0.1"})
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def write_candidates(path: Path, rows: list[CandidateMarket]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "home",
                "away",
                "commence_time",
                "actual_score",
                "question",
                "slug",
                "match_type",
                "active",
                "closed",
                "volume",
                "liquidity",
                "outcome_prices",
                "yes_token_id",
                "pre_kickoff_yes_price",
                "pre_kickoff_price_time",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "home": row.fixture.home,
                    "away": row.fixture.away,
                    "commence_time": row.fixture.commence_time,
                    "actual_score": actual_score(row.fixture),
                    "question": row.question,
                    "slug": row.slug,
                    "match_type": row.match_type,
                    "active": row.active,
                    "closed": row.closed,
                    "volume": row.volume,
                    "liquidity": row.liquidity,
                    "outcome_prices": row.outcome_prices,
                    "yes_token_id": row.yes_token_id,
                    "pre_kickoff_yes_price": row.pre_kickoff_yes_price,
                    "pre_kickoff_price_time": row.pre_kickoff_price_time,
                }
            )


def write_misses(path: Path, rows: list[Fixture]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["home", "away", "commence_time", "actual_score"])
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "home": row.home,
                    "away": row.away,
                    "commence_time": row.commence_time,
                    "actual_score": actual_score(row),
                }
            )


def build_1x2_summaries(rows: list[CandidateMarket]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[CandidateMarket]] = {}
    for row in rows:
        key = (row.fixture.home_code, row.fixture.away_code, row.fixture.commence_time)
        grouped.setdefault(key, []).append(row)

    summaries: list[dict[str, Any]] = []
    for _, candidates in grouped.items():
        by_type: dict[str, CandidateMarket] = {}
        for market_type in ("home_win_market", "draw_market", "away_win_market"):
            typed = [row for row in candidates if row.match_type == market_type and row.pre_kickoff_yes_price is not None]
            if typed:
                by_type[market_type] = max(typed, key=lambda row: ((row.volume or 0.0), (row.liquidity or 0.0)))
        if set(by_type) != {"home_win_market", "draw_market", "away_win_market"}:
            continue
        fixture = next(iter(by_type.values())).fixture
        raw_home = float(by_type["home_win_market"].pre_kickoff_yes_price or 0.0)
        raw_draw = float(by_type["draw_market"].pre_kickoff_yes_price or 0.0)
        raw_away = float(by_type["away_win_market"].pre_kickoff_yes_price or 0.0)
        total = raw_home + raw_draw + raw_away
        if total <= 0:
            continue
        probs = {
            "home": raw_home / total,
            "draw": raw_draw / total,
            "away": raw_away / total,
        }
        favourite = max(probs, key=probs.get)
        actual = actual_outcome(fixture)
        summaries.append(
            {
                "home": fixture.home,
                "away": fixture.away,
                "commence_time": fixture.commence_time,
                "actual_score": actual_score(fixture),
                "raw_home": raw_home,
                "raw_draw": raw_draw,
                "raw_away": raw_away,
                "sum_raw": total,
                "p_home": probs["home"],
                "p_draw": probs["draw"],
                "p_away": probs["away"],
                "favourite": favourite,
                "actual_outcome": actual,
                "favourite_correct": "" if actual == "" else str(favourite == actual),
                "home_market": by_type["home_win_market"].question,
                "draw_market": by_type["draw_market"].question,
                "away_market": by_type["away_win_market"].question,
                "snapshot_times": "|".join(
                    row.pre_kickoff_price_time
                    for row in (
                        by_type["home_win_market"],
                        by_type["draw_market"],
                        by_type["away_win_market"],
                    )
                    if row.pre_kickoff_price_time
                ),
            }
        )
    return summaries


def write_1x2_summaries(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "home",
        "away",
        "commence_time",
        "actual_score",
        "raw_home",
        "raw_draw",
        "raw_away",
        "sum_raw",
        "p_home",
        "p_draw",
        "p_away",
        "favourite",
        "actual_outcome",
        "favourite_correct",
        "home_market",
        "draw_market",
        "away_market",
        "snapshot_times",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def actual_score(fixture: Fixture) -> str:
    if fixture.actual_home is None or fixture.actual_away is None:
        return ""
    return f"{fixture.actual_home}-{fixture.actual_away}"


def actual_outcome(fixture: Fixture) -> str:
    if fixture.actual_home is None or fixture.actual_away is None:
        return ""
    if fixture.actual_home > fixture.actual_away:
        return "home"
    if fixture.actual_home < fixture.actual_away:
        return "away"
    return "draw"


def team_code(name: str) -> str:
    raw = normalize_text(name)
    if raw.upper() in TEAM_ALIASES:
        return raw.upper()
    return TEAM_TO_CODE.get(raw, raw.upper())


def contains_team(text: str, code: str) -> bool:
    aliases = TEAM_ALIASES.get(code, (code.lower(),))
    return any(re.search(rf"\b{re.escape(normalize_text(alias))}\b", text) for alias in aliases)


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("-", " ").replace("_", " ").lower()).strip()


def parse_jsonish_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


def bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def parse_iso(value: str) -> datetime:
    text = value.strip().replace("Z", "+00:00")
    return datetime.fromisoformat(text).astimezone(timezone.utc)


if __name__ == "__main__":
    raise SystemExit(main())
