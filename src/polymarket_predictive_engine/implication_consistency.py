"""WO-41 implication-network consistency scanner.

Mutually exclusive sum-to-one groups are only the simplest structural
constraint. Linked binary markets also have no-arbitrage inequalities:

* a team winning implies reaching the final, which implies reaching a
  semifinal;
* a continent winner market should match the basket of member-nation winner
  markets when the visible family is exhaustive enough to test;
* exact final-matchup markets involving team T should sum to the market for
  T reaching the final.

This module measures those inequalities net of taker fees. It writes a
timestamped deviations ledger and summary only. It does not place, amend, or
cancel orders and it does not feed promotion gates.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import json
import re
import time
from typing import Any

import requests

from .config import EngineConfig, load_config
from .event_group_consistency import DEFAULT_GAMMA_BASE_URL, _fee_rate, _taker_fee_per_share
from .sharp_anchor import _team_key
from .utils import now_utc, read_csv_rows, safe_float, write_csv, write_json


LEDGER_FIELDS = [
    "scanned_at_utc",
    "violation_class",
    "event_slug",
    "event_title",
    "relation_key",
    "flagged_direction",
    "lhs_label",
    "rhs_label",
    "lhs_price",
    "rhs_price",
    "fee_charge",
    "net_violation_per_basket",
    "leg_count",
    "details_json",
    "volume_24h_usd",
]

CONTINENT_ALIASES = {
    "africa": "africa",
    "caf": "africa",
    "asia": "asia",
    "afc": "asia",
    "europe": "europe",
    "uefa": "europe",
    "northamerica": "northamerica",
    "northandcentralamerica": "northamerica",
    "concacaf": "northamerica",
    "southamerica": "southamerica",
    "conmebol": "southamerica",
    "oceania": "oceania",
    "ofc": "oceania",
}

TEAM_CONTINENT = {
    # Europe / UEFA
    "albania": "europe",
    "austria": "europe",
    "belgium": "europe",
    "croatia": "europe",
    "czechia": "europe",
    "denmark": "europe",
    "england": "europe",
    "france": "europe",
    "germany": "europe",
    "greece": "europe",
    "hungary": "europe",
    "italy": "europe",
    "netherlands": "europe",
    "norway": "europe",
    "poland": "europe",
    "portugal": "europe",
    "romania": "europe",
    "scotland": "europe",
    "serbia": "europe",
    "slovakia": "europe",
    "slovenia": "europe",
    "spain": "europe",
    "sweden": "europe",
    "switzerland": "europe",
    "turkey": "europe",
    "ukraine": "europe",
    "wales": "europe",
    # South America / CONMEBOL
    "argentina": "southamerica",
    "bolivia": "southamerica",
    "brazil": "southamerica",
    "chile": "southamerica",
    "colombia": "southamerica",
    "ecuador": "southamerica",
    "paraguay": "southamerica",
    "peru": "southamerica",
    "uruguay": "southamerica",
    "venezuela": "southamerica",
    # North/Central America / CONCACAF
    "canada": "northamerica",
    "costarica": "northamerica",
    "haiti": "northamerica",
    "honduras": "northamerica",
    "jamaica": "northamerica",
    "mexico": "northamerica",
    "panama": "northamerica",
    "unitedstates": "northamerica",
    # Africa / CAF
    "algeria": "africa",
    "cameroon": "africa",
    "egypt": "africa",
    "ghana": "africa",
    "ivorycoast": "africa",
    "morocco": "africa",
    "nigeria": "africa",
    "senegal": "africa",
    "southafrica": "africa",
    "tunisia": "africa",
    # Asia / AFC
    "australia": "asia",
    "iran": "asia",
    "iraq": "asia",
    "japan": "asia",
    "qatar": "asia",
    "saudiarabia": "asia",
    "southkorea": "asia",
    "uzbekistan": "asia",
    # Oceania / OFC
    "newzealand": "oceania",
}


@dataclass(frozen=True)
class MarketLeg:
    event_slug: str
    event_title: str
    market_slug: str
    question: str
    leg_type: str
    label: str
    team_key: str = ""
    continent_key: str = ""
    matchup_key: str = ""
    teams: tuple[str, str] = ("", "")
    ask: float | None = None
    bid: float | None = None
    fee_rate: float = 0.0
    volume_24h_usd: float = 0.0


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = cfg.raw.get("implication_networks", {}) if isinstance(cfg.raw.get("implication_networks"), dict) else {}
    merged = {
        "enabled": True,
        "gamma_base_url": DEFAULT_GAMMA_BASE_URL,
        "event_pages": 3,
        "page_size": 100,
        "deviation_threshold_per_basket": 0.005,
        "max_ledger_rows": 100000,
        "request_timeout_seconds": 20,
        "request_pause_seconds": 0.1,
        "min_continent_member_markets": 2,
        "min_matchups_per_team": 2,
        # Same fee defaults as WO-34. Sports takers pay 0.05 x p x (1-p)
        # per share; unknown enabled fee types fail conservative.
        "fee_rate_by_type": {"sports_fees_v2": 0.05},
        "fee_rate_when_enabled_unknown": 0.07,
    }
    merged.update({k: v for k, v in raw.items() if v is not None})
    return merged


def _norm_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("-", " ")).strip()


def _market_question(market: dict[str, Any]) -> str:
    parts = [
        market.get("question"),
        market.get("title"),
        market.get("slug"),
        market.get("market_slug"),
    ]
    return " ".join(_norm_text(part) for part in parts if str(part or "").strip())


def _strip_team(value: str) -> str:
    text = re.sub(r"\b(?:the|2026|fifa|world|cup|winner|market)\b", " ", value, flags=re.I)
    return _norm_text(text).strip(" ?.,:;")


def _continent_key(value: str) -> str:
    key = _team_key(value)
    return CONTINENT_ALIASES.get(key, "")


def _parse_matchup(text: str) -> tuple[str, str] | None:
    patterns = [
        r"world cup final (?:be )?(.*?)\s+(?:vs\.?|v\.?)\s+(.*?)(?:\?|$)",
        r"final matchup (?:be )?(.*?)\s+(?:vs\.?|v\.?)\s+(.*?)(?:\?|$)",
        r"exact final (?:be )?(.*?)\s+(?:vs\.?|v\.?)\s+(.*?)(?:\?|$)",
        r"(.*?)\s+(?:vs\.?|v\.?)\s+(.*?)\s+(?:be|in|to make|make).*(?:world cup )?final",
        r"(.*?)\s+(?:vs\.?|v\.?)\s+(.*?)\s+final matchup",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        left = _strip_team(match.group(1))
        right = _strip_team(match.group(2))
        if left and right:
            left_key = _team_key(left)
            right_key = _team_key(right)
            if left_key and right_key and left_key != right_key:
                return left, right
    return None


def _parse_worldcup_leg(text: str) -> tuple[str, str, str, tuple[str, str]]:
    matchup = _parse_matchup(text)
    if matchup is not None:
        left, right = matchup
        left_key = _team_key(left)
        right_key = _team_key(right)
        ordered = tuple(sorted((left_key, right_key)))
        return "final_matchup", " vs ".join(matchup), "", ordered

    continent_patterns = [
        r"(?:team|nation|country)\s+from\s+([A-Za-z &/]+?)\s+win",
        r"will\s+([A-Za-z &/]+?)\s+(?:team|nation|country)\s+win",
        r"will\s+([A-Za-z &/]+?)\s+win\s+(?:the\s+)?(?:2026\s+)?(?:fifa\s+)?world cup",
    ]
    for pattern in continent_patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        raw = _strip_team(match.group(1))
        continent = _continent_key(raw)
        if continent:
            return "continent_winner", raw, continent, ("", "")

    stage_patterns = [
        ("reaches_semifinal", r"(?:will\s+)?(.+?)\s+(?:reach|make|advance to|qualify for|go to)\s+(?:the\s+)?(?:semi finals|semifinals|semi final|semi-final|semis)"),
        ("reaches_final", r"(?:will\s+)?(.+?)\s+(?:reach|make|advance to|qualify for|go to)\s+(?:the\s+)?final"),
        ("winner", r"(?:will\s+)?(.+?)\s+win\s+(?:the\s+)?(?:2026\s+)?(?:fifa\s+)?world cup"),
        ("winner", r"(.+?)\s+to\s+win\s+(?:the\s+)?(?:2026\s+)?(?:fifa\s+)?world cup"),
    ]
    for leg_type, pattern in stage_patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        label = _strip_team(match.group(1))
        team_key = _team_key(label)
        if team_key and team_key not in CONTINENT_ALIASES:
            return leg_type, label, "", ("", "")
    return "", "", "", ("", "")


def _classify_market(event: dict[str, Any], market: dict[str, Any], settings: dict[str, Any]) -> MarketLeg | None:
    text = _market_question(market)
    leg_type, label, continent, teams = _parse_worldcup_leg(text)
    if not leg_type:
        return None
    ask = safe_float(market.get("bestAsk"))
    bid = safe_float(market.get("bestBid"))
    if ask is None and bid is None:
        return None
    team_key = _team_key(label) if leg_type in {"winner", "reaches_final", "reaches_semifinal"} else ""
    matchup_key = "::".join(teams) if leg_type == "final_matchup" else ""
    return MarketLeg(
        event_slug=str(event.get("slug") or "").strip(),
        event_title=str(event.get("title") or event.get("question") or "").strip(),
        market_slug=str(market.get("slug") or market.get("market_slug") or market.get("conditionId") or "").strip(),
        question=str(market.get("question") or market.get("title") or "").strip(),
        leg_type=leg_type,
        label=label,
        team_key=team_key,
        continent_key=continent,
        matchup_key=matchup_key,
        teams=teams,
        ask=ask,
        bid=bid,
        fee_rate=_fee_rate(market, settings),
        volume_24h_usd=safe_float(market.get("volume24hr") or event.get("volume24hr")) or 0.0,
    )


def _buy_cost(leg: MarketLeg) -> tuple[float, float] | None:
    if leg.ask is None or not 0 < leg.ask < 1:
        return None
    fee = _taker_fee_per_share(leg.fee_rate, leg.ask)
    return leg.ask + fee, fee


def _sell_credit(leg: MarketLeg) -> tuple[float, float] | None:
    if leg.bid is None or not 0 < leg.bid < 1:
        return None
    fee = _taker_fee_per_share(leg.fee_rate, leg.bid)
    return leg.bid - fee, fee


def _best_buy(legs: list[MarketLeg]) -> MarketLeg | None:
    candidates = [(cost_fee[0], leg) for leg in legs if (cost_fee := _buy_cost(leg)) is not None]
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def _best_sell(legs: list[MarketLeg]) -> MarketLeg | None:
    candidates = [(credit_fee[0], leg) for leg in legs if (credit_fee := _sell_credit(leg)) is not None]
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def _violation_row(
    *,
    scanned_at: str,
    violation_class: str,
    event: dict[str, Any],
    relation_key: str,
    flagged_direction: str,
    lhs_label: str,
    rhs_label: str,
    lhs_price: float,
    rhs_price: float,
    fee_charge: float,
    net: float,
    leg_count: int,
    details: dict[str, Any],
    volume_24h_usd: float,
) -> dict[str, Any]:
    return {
        "scanned_at_utc": scanned_at,
        "violation_class": violation_class,
        "event_slug": str(event.get("slug") or "").strip(),
        "event_title": str(event.get("title") or event.get("question") or "").strip(),
        "relation_key": relation_key,
        "flagged_direction": flagged_direction,
        "lhs_label": lhs_label,
        "rhs_label": rhs_label,
        "lhs_price": round(lhs_price, 6),
        "rhs_price": round(rhs_price, 6),
        "fee_charge": round(fee_charge, 6),
        "net_violation_per_basket": round(net, 6),
        "leg_count": leg_count,
        "details_json": json.dumps(details, sort_keys=True),
        "volume_24h_usd": round(volume_24h_usd, 2),
    }


def _subset_rows(
    event: dict[str, Any],
    scanned_at: str,
    threshold: float,
    narrow: MarketLeg,
    broad: MarketLeg,
    relation: str,
) -> list[dict[str, Any]]:
    sell = _sell_credit(narrow)
    buy = _buy_cost(broad)
    if sell is None or buy is None:
        return []
    sell_credit, sell_fee = sell
    buy_cost, buy_fee = buy
    net = sell_credit - buy_cost
    if net <= threshold:
        return []
    return [
        _violation_row(
            scanned_at=scanned_at,
            violation_class="monotone_chain",
            event=event,
            relation_key=f"{narrow.team_key}:{relation}",
            flagged_direction="sell_narrow_buy_broad",
            lhs_label=f"{narrow.label} {narrow.leg_type}",
            rhs_label=f"{broad.label} {broad.leg_type}",
            lhs_price=float(narrow.bid or 0.0),
            rhs_price=float(broad.ask or 0.0),
            fee_charge=sell_fee + buy_fee,
            net=net,
            leg_count=2,
            details={
                "narrow_market": narrow.market_slug,
                "broad_market": broad.market_slug,
                "logic": "subset event cannot be more expensive than its superset after taker fees",
            },
            volume_24h_usd=max(narrow.volume_24h_usd, broad.volume_24h_usd),
        )
    ]


def _sum_buy_cost(legs: list[MarketLeg]) -> tuple[float, float] | None:
    costs: list[tuple[float, float]] = []
    for leg in legs:
        item = _buy_cost(leg)
        if item is None:
            return None
        costs.append(item)
    return sum(item[0] for item in costs), sum(item[1] for item in costs)


def _sum_sell_credit(legs: list[MarketLeg]) -> tuple[float, float] | None:
    credits: list[tuple[float, float]] = []
    for leg in legs:
        item = _sell_credit(leg)
        if item is None:
            return None
        credits.append(item)
    return sum(item[0] for item in credits), sum(item[1] for item in credits)


def _aggregation_rows(
    event: dict[str, Any],
    scanned_at: str,
    settings: dict[str, Any],
    continent: MarketLeg,
    members: list[MarketLeg],
) -> list[dict[str, Any]]:
    threshold = float(settings["deviation_threshold_per_basket"])
    if len(members) < int(settings["min_continent_member_markets"]):
        return []
    rows: list[dict[str, Any]] = []
    member_buys = _sum_buy_cost(members)
    continent_sell = _sell_credit(continent)
    if member_buys is not None and continent_sell is not None:
        member_cost, member_fee = member_buys
        continent_credit, continent_fee = continent_sell
        net = continent_credit - member_cost
        if net > threshold:
            rows.append(
                _violation_row(
                    scanned_at=scanned_at,
                    violation_class="continent_aggregation",
                    event=event,
                    relation_key=continent.continent_key,
                    flagged_direction="sell_continent_buy_members",
                    lhs_label=f"{continent.label} continent winner",
                    rhs_label=" + ".join(member.label for member in members),
                    lhs_price=float(continent.bid or 0.0),
                    rhs_price=sum(float(member.ask or 0.0) for member in members),
                    fee_charge=continent_fee + member_fee,
                    net=net,
                    leg_count=1 + len(members),
                    details={"continent_market": continent.market_slug, "member_markets": [member.market_slug for member in members]},
                    volume_24h_usd=max([continent.volume_24h_usd, *[member.volume_24h_usd for member in members]], default=0.0),
                )
            )
    member_sells = _sum_sell_credit(members)
    continent_buy = _buy_cost(continent)
    if member_sells is not None and continent_buy is not None:
        member_credit, member_fee = member_sells
        continent_cost, continent_fee = continent_buy
        net = member_credit - continent_cost
        if net > threshold:
            rows.append(
                _violation_row(
                    scanned_at=scanned_at,
                    violation_class="continent_aggregation",
                    event=event,
                    relation_key=continent.continent_key,
                    flagged_direction="sell_members_buy_continent",
                    lhs_label=" + ".join(member.label for member in members),
                    rhs_label=f"{continent.label} continent winner",
                    lhs_price=sum(float(member.bid or 0.0) for member in members),
                    rhs_price=float(continent.ask or 0.0),
                    fee_charge=member_fee + continent_fee,
                    net=net,
                    leg_count=1 + len(members),
                    details={"continent_market": continent.market_slug, "member_markets": [member.market_slug for member in members]},
                    volume_24h_usd=max([continent.volume_24h_usd, *[member.volume_24h_usd for member in members]], default=0.0),
                )
            )
    return rows


def _matchup_rows(
    event: dict[str, Any],
    scanned_at: str,
    settings: dict[str, Any],
    final_leg: MarketLeg,
    matchup_legs: list[MarketLeg],
) -> list[dict[str, Any]]:
    threshold = float(settings["deviation_threshold_per_basket"])
    if len(matchup_legs) < int(settings["min_matchups_per_team"]):
        return []
    rows: list[dict[str, Any]] = []
    matchup_buys = _sum_buy_cost(matchup_legs)
    final_sell = _sell_credit(final_leg)
    if matchup_buys is not None and final_sell is not None:
        matchup_cost, matchup_fee = matchup_buys
        final_credit, final_fee = final_sell
        net = final_credit - matchup_cost
        if net > threshold:
            rows.append(
                _violation_row(
                    scanned_at=scanned_at,
                    violation_class="final_matchup_sum",
                    event=event,
                    relation_key=final_leg.team_key,
                    flagged_direction="sell_reaches_final_buy_matchups",
                    lhs_label=f"{final_leg.label} reaches final",
                    rhs_label=" + ".join(leg.label for leg in matchup_legs),
                    lhs_price=float(final_leg.bid or 0.0),
                    rhs_price=sum(float(leg.ask or 0.0) for leg in matchup_legs),
                    fee_charge=final_fee + matchup_fee,
                    net=net,
                    leg_count=1 + len(matchup_legs),
                    details={"final_market": final_leg.market_slug, "matchup_markets": [leg.market_slug for leg in matchup_legs]},
                    volume_24h_usd=max([final_leg.volume_24h_usd, *[leg.volume_24h_usd for leg in matchup_legs]], default=0.0),
                )
            )
    matchup_sells = _sum_sell_credit(matchup_legs)
    final_buy = _buy_cost(final_leg)
    if matchup_sells is not None and final_buy is not None:
        matchup_credit, matchup_fee = matchup_sells
        final_cost, final_fee = final_buy
        net = matchup_credit - final_cost
        if net > threshold:
            rows.append(
                _violation_row(
                    scanned_at=scanned_at,
                    violation_class="final_matchup_sum",
                    event=event,
                    relation_key=final_leg.team_key,
                    flagged_direction="sell_matchups_buy_reaches_final",
                    lhs_label=" + ".join(leg.label for leg in matchup_legs),
                    rhs_label=f"{final_leg.label} reaches final",
                    lhs_price=sum(float(leg.bid or 0.0) for leg in matchup_legs),
                    rhs_price=float(final_leg.ask or 0.0),
                    fee_charge=matchup_fee + final_fee,
                    net=net,
                    leg_count=1 + len(matchup_legs),
                    details={"final_market": final_leg.market_slug, "matchup_markets": [leg.market_slug for leg in matchup_legs]},
                    volume_24h_usd=max([final_leg.volume_24h_usd, *[leg.volume_24h_usd for leg in matchup_legs]], default=0.0),
                )
            )
    return rows


def _scan_event(event: dict[str, Any], settings: dict[str, Any], scanned_at: str) -> tuple[int, list[dict[str, Any]]]:
    markets = [m for m in event.get("markets") or [] if isinstance(m, dict) and not m.get("closed")]
    legs = [leg for market in markets if (leg := _classify_market(event, market, settings)) is not None]
    by_team_type: dict[str, dict[str, list[MarketLeg]]] = defaultdict(lambda: defaultdict(list))
    continent_legs: dict[str, list[MarketLeg]] = defaultdict(list)
    winner_by_continent: dict[str, dict[str, list[MarketLeg]]] = defaultdict(lambda: defaultdict(list))
    matchup_by_team: dict[str, dict[str, list[MarketLeg]]] = defaultdict(lambda: defaultdict(list))

    for leg in legs:
        if leg.team_key:
            by_team_type[leg.team_key][leg.leg_type].append(leg)
        if leg.leg_type == "continent_winner" and leg.continent_key:
            continent_legs[leg.continent_key].append(leg)
        if leg.leg_type == "winner" and leg.team_key:
            continent = TEAM_CONTINENT.get(leg.team_key)
            if continent:
                winner_by_continent[continent][leg.team_key].append(leg)
        if leg.leg_type == "final_matchup":
            for team in leg.teams:
                if team:
                    matchup_by_team[team][leg.matchup_key].append(leg)

    rows: list[dict[str, Any]] = []
    for team_key, stages in by_team_type.items():
        winner = _best_sell(stages.get("winner", []))
        final_buy = _best_buy(stages.get("reaches_final", []))
        if winner is not None and final_buy is not None:
            rows.extend(_subset_rows(event, scanned_at, float(settings["deviation_threshold_per_basket"]), winner, final_buy, "winner_le_final"))
        final_sell = _best_sell(stages.get("reaches_final", []))
        semifinal_buy = _best_buy(stages.get("reaches_semifinal", []))
        if final_sell is not None and semifinal_buy is not None:
            rows.extend(
                _subset_rows(event, scanned_at, float(settings["deviation_threshold_per_basket"]), final_sell, semifinal_buy, "final_le_semifinal")
            )

    for continent_key, continent_candidates in continent_legs.items():
        continent = _best_sell(continent_candidates) or _best_buy(continent_candidates)
        if continent is None:
            continue
        members: list[MarketLeg] = []
        for team_legs in winner_by_continent.get(continent_key, {}).values():
            member = _best_buy(team_legs) or _best_sell(team_legs)
            if member is not None:
                members.append(member)
        rows.extend(_aggregation_rows(event, scanned_at, settings, continent, members))

    for team_key, stages in by_team_type.items():
        final_leg = _best_sell(stages.get("reaches_final", [])) or _best_buy(stages.get("reaches_final", []))
        if final_leg is None:
            continue
        matchups: list[MarketLeg] = []
        for matchup_legs in matchup_by_team.get(team_key, {}).values():
            matchup = _best_buy(matchup_legs) or _best_sell(matchup_legs)
            if matchup is not None:
                matchups.append(matchup)
        rows.extend(_matchup_rows(event, scanned_at, settings, final_leg, matchups))

    return len(legs), rows


def scan_implication_networks(cfg: EngineConfig) -> dict[str, Any]:
    settings = _settings(cfg)
    out_root = cfg.output_root / "implication_consistency"
    ledger_path = out_root / "implication_deviations.csv"
    summary_path = out_root / "implication_scan.json"
    generated_at = now_utc()
    summary: dict[str, Any] = {
        "status": "disabled",
        "generated_at_utc": generated_at,
        "work_order": "WO-41",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    if str(settings.get("enabled", True)).strip().lower() in {"0", "false", "no"}:
        write_json(summary_path, summary)
        return summary

    base = str(settings["gamma_base_url"]).rstrip("/")
    timeout = float(settings["request_timeout_seconds"])
    pause = float(settings["request_pause_seconds"])
    scanned_events = 0
    classified_legs = 0
    flagged: list[dict[str, Any]] = []
    errors: list[str] = []
    for page in range(int(settings["event_pages"])):
        try:
            response = requests.get(
                f"{base}/events",
                params={
                    "closed": "false",
                    "order": "volume24hr",
                    "ascending": "false",
                    "limit": int(settings["page_size"]),
                    "offset": page * int(settings["page_size"]),
                },
                timeout=timeout,
            )
            response.raise_for_status()
            events = response.json()
        except Exception as exc:
            errors.append(f"gamma events page {page}: {type(exc).__name__}: {exc}")
            continue
        if not isinstance(events, list) or not events:
            break
        for event in events:
            if not isinstance(event, dict):
                continue
            scanned_events += 1
            leg_count, rows = _scan_event(event, settings, generated_at)
            classified_legs += leg_count
            flagged.extend(rows)
        if pause:
            time.sleep(pause)

    ledger = read_csv_rows(ledger_path)
    ledger.extend(flagged)
    max_rows = int(settings["max_ledger_rows"])
    if max_rows > 0 and len(ledger) > max_rows:
        ledger = ledger[-max_rows:]
    write_csv(ledger_path, ledger, fieldnames=LEDGER_FIELDS)

    by_class: dict[str, list[float]] = defaultdict(list)
    for row in flagged:
        by_class[str(row.get("violation_class") or "")].append(float(row.get("net_violation_per_basket") or 0.0))
    summary.update(
        {
            "status": "ok" if scanned_events or not errors else "failed",
            "events_scanned": scanned_events,
            "classified_legs": classified_legs,
            "flagged_deviations": len(flagged),
            "flagged_by_class": {key: len(values) for key, values in sorted(by_class.items())},
            "best_net_violation_by_class": {key: max(values) for key, values in sorted(by_class.items())},
            "deviation_threshold_per_basket": float(settings["deviation_threshold_per_basket"]),
            "ledger_rows": len(ledger),
            "ledger_path": str(ledger_path),
            "note": (
                "Measurement-only Frechet/Boole implication consistency scan. Rows are structural "
                "deviation evidence only; no signals, gates, order placement, amendment, or cancellation are invoked."
            ),
            "errors": errors[:10],
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        }
    )
    write_json(summary_path, summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return scan_implication_networks(load_config(config_path))
