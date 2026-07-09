"""WO-36 maker-carry actuarial study: is quoting for liquidity rewards a
credible route to $100/month?

Polymarket's live fee schedule (verified 2026-07-09) taxes takers at
rate x p x (1-p) per share while makers pay ZERO fees and earn daily
liquidity rewards under a quadratic scoring rule S(v, s) = ((v-s)/v)^2 x size,
paid at 00:00 UTC per market from a public per-market pot. That asymmetry is
the structural finding of the fee audit: the taker cost stack (~1.6-2.1c per
dollar on sports) does not exist on the maker side. This module measures -
never trades - whether the reward income minus expected adverse selection
clears the $100/month target (= $3.33/day).

Model, per candidate market (all inputs from free, key-less public APIs):

- Pot: Gamma ``clobRewards.rewardsDailyRate`` summed over live reward configs.
- Our hypothetical quote: ``rewards_min_size`` shares, both sides, at distance
  s = quote_distance_fraction x v from mid, where v = rewards_max_spread.
- Reward share: our quadratic score against the score of every resting order
  currently inside v on the live CLOB book (two-sided pool = min of the side
  totals, mirroring Polymarket's min(Q_bid, Q_ask) per-maker rule).
- Adverse selection: every mid move larger than s across one bar is assumed
  to fill our full quote on the wrong side at a per-share loss of |move| - s
  (reaction time = one bar). Charged as the WORSE of two windows - 24h of
  1-minute bars (microstructure noise) and 7 days of 10-minute bars (news-day
  gaps the calm last-24h window hides).
- Thin-book guard: a raw reward share above ``max_trusted_reward_share`` means
  almost nobody is resting inside the band right now (observed live on
  in-game esports books). Competition is endogenous - an empty band is a
  danger signal, not free money - so such rows are flagged untrusted and
  excluded from the portfolio.
- Markout charge (WO-36 step 2, registered 2026-07-09): bar moves approximate
  pick-offs; executed prints MEASURE them. Every real trade that swept
  through our hypothetical quote level gets a markout - where the mid stood
  ``markout_horizon_minutes`` after the fill versus our fill price - scaled
  by our queue share against the resting depth in the band. The candidate is
  charged the WORST of the bar windows and the markout estimate.
- Payout floor: Polymarket pays no reward below $1/market/day, so the sized
  portfolio only admits quotes whose gross reward clears that floor.

MAKER GATES (pre-registered 2026-07-09, before any accrued trend history):
  M-A carry evidence  - at least ``gate_min_runs_at_target`` daily study runs
                        with trusted net carry at/above target, including the
                        latest run.
  M-B adverse realism - every portfolio market carries a MEASURED markout
                        charge (empirical fills, not just bar approximations).
  M-C payout floor    - enforced by construction in the sizing loop.
All three passing yields ``evidence_supported_pending_human_decision`` -
never an order. Acting on the daily quote sheet is a human decision made
outside this system; the repo remains paper-only either way.

HONESTY CLAUSE (stamped into every report): simulated maker fills cannot be
verified - queue position, intra-minute jumps, and competitor reaction are
invisible - so the net-carry estimate is an UPPER BOUND for the share model
and an approximation for the pick-off charge. A YES here justifies a deeper
study and nothing else. Measurement only: no labels, no gates, no order
placement of any kind. The live-trading gates in AGENTS.md are untouched.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests

from .config import EngineConfig, load_config
from .utils import now_utc, read_csv_rows, safe_float, write_csv, write_json

DEFAULT_GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
DEFAULT_CLOB_BASE_URL = "https://clob.polymarket.com"
DEFAULT_DATA_API_BASE_URL = "https://data-api.polymarket.com"

MAKER_GATES_REGISTERED_AT_UTC = "2026-07-09T13:00:00Z"

# Markets whose question suggests scheduled binary announcements: quoting
# through the event is the classic maker blow-up, so the quote sheet flags it.
EVENT_RISK_KEYWORDS = (
    "fed", "rate", "cpi", "meeting", "announce", "decision", "election",
    "vote", "jobs report", "opec", "earnings",
)

CANDIDATE_FIELDS = [
    "question",
    "condition_id",
    "token_id",
    "neg_risk",
    "volume_24h_usd",
    "pot_usd_per_day",
    "rewards_min_size_shares",
    "rewards_max_spread_cents",
    "mid_price",
    "quote_distance",
    "competitor_score_bid",
    "competitor_score_ask",
    "our_score_per_side",
    "estimated_reward_share",
    "gross_reward_usd_per_day",
    "history_points",
    "pickoff_events_per_day",
    "adverse_usd_per_day_1min_24h",
    "adverse_usd_per_day_10min_7d",
    "adverse_usd_per_day_markout",
    "band_crossing_prints_per_day",
    "markout_measured",
    "adverse_selection_usd_per_day",
    "net_carry_usd_per_day",
    "capital_usd",
    "estimate_quality",
]


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = cfg.raw.get("maker_carry_study", {}) if isinstance(cfg.raw.get("maker_carry_study"), dict) else {}
    merged = {
        "enabled": True,
        "gamma_base_url": DEFAULT_GAMMA_BASE_URL,
        "clob_base_url": DEFAULT_CLOB_BASE_URL,
        "data_api_base_url": DEFAULT_DATA_API_BASE_URL,
        "universe_pages": 5,
        "page_size": 100,
        "min_daily_pot_usd": 25.0,
        "max_book_candidates": 20,
        # Quote midway into the qualifying band: far enough out to survive
        # noise, close enough in to score 25% of the max quadratic weight.
        "quote_distance_fraction": 0.5,
        "reaction_minutes": 1,
        # A raw share above this means the band is nearly empty of resting
        # competition (seen live on in-game esports books): untrusted.
        "max_trusted_reward_share": 0.05,
        # Net carry is ~linear in quote size while our share is small, so the
        # capital-constrained portfolio sizes quotes up - but never beyond
        # this multiple of rewards_min_size, past which the full-size-fill
        # pick-off model stops being conservative.
        "max_size_multiple": 5,
        "capital_cap_usd": 500.0,
        "target_net_usd_per_day": 3.33,
        # Markout (empirical adverse selection from executed prints).
        "markout_horizon_minutes": 5,
        "markout_min_prints": 20,
        "prints_limit": 500,
        # Polymarket pays no reward accrual below this per market per day.
        "min_daily_payout_usd": 1.0,
        # Gate M-A: daily runs at/above target required for the maker verdict.
        "gate_min_runs_at_target": 7,
        "request_timeout_seconds": 20,
        "request_pause_seconds": 0.15,
    }
    merged.update({k: v for k, v in raw.items() if v is not None})
    return merged


def _live_pot_usd(market: dict[str, Any], today: str) -> float:
    total = 0.0
    for reward in market.get("clobRewards") or []:
        if not isinstance(reward, dict):
            continue
        rate = safe_float(reward.get("rewardsDailyRate")) or 0.0
        end_date = str(reward.get("endDate") or "")
        if rate > 0 and (not end_date or end_date >= today):
            total += rate
    return total


def _first_token_id(market: dict[str, Any]) -> str:
    raw = market.get("clobTokenIds")
    tokens: list[Any]
    if isinstance(raw, str):
        try:
            tokens = json.loads(raw)
        except (ValueError, TypeError):
            return ""
    elif isinstance(raw, list):
        tokens = raw
    else:
        return ""
    return str(tokens[0]).strip() if tokens else ""


def _rewarded_universe(settings: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Active markets carrying a live maker-reward pot, largest pots first."""
    base = str(settings["gamma_base_url"]).rstrip("/")
    timeout = float(settings["request_timeout_seconds"])
    pause = float(settings["request_pause_seconds"])
    today = now_utc()[:10]
    universe: list[dict[str, Any]] = []
    errors: list[str] = []
    for page in range(int(settings["universe_pages"])):
        try:
            response = requests.get(
                f"{base}/markets",
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
            markets = response.json()
        except Exception as exc:
            errors.append(f"gamma page {page}: {type(exc).__name__}: {exc}")
            continue
        if not isinstance(markets, list) or not markets:
            break
        for market in markets:
            if not isinstance(market, dict):
                continue
            pot = _live_pot_usd(market, today)
            min_size = safe_float(market.get("rewardsMinSize")) or 0.0
            max_spread_cents = safe_float(market.get("rewardsMaxSpread")) or 0.0
            token_id = _first_token_id(market)
            if pot < float(settings["min_daily_pot_usd"]) or min_size <= 0 or max_spread_cents <= 0 or not token_id:
                continue
            universe.append(
                {
                    "question": str(market.get("question") or "").strip(),
                    "condition_id": str(market.get("conditionId") or "").strip(),
                    "token_id": token_id,
                    "neg_risk": bool(market.get("negRisk")),
                    "volume_24h_usd": round(safe_float(market.get("volume24hr")) or 0.0, 2),
                    "pot_usd_per_day": round(pot, 2),
                    "rewards_min_size_shares": min_size,
                    "rewards_max_spread_cents": max_spread_cents,
                }
            )
        if pause:
            time.sleep(pause)
    universe.sort(key=lambda row: row["pot_usd_per_day"], reverse=True)
    return universe, errors


def _quadratic_score(distance: float, v: float, size: float) -> float:
    if v <= 0 or distance < 0 or distance > v:
        return 0.0
    return ((v - distance) / v) ** 2 * size


def _book_competition(settings: dict[str, Any], token_id: str, v: float) -> dict[str, Any] | None:
    """Live-book quadratic score already resting inside the qualifying band."""
    try:
        response = requests.get(
            f"{str(settings['clob_base_url']).rstrip('/')}/book",
            params={"token_id": token_id},
            timeout=float(settings["request_timeout_seconds"]),
        )
        response.raise_for_status()
        book = response.json()
    except Exception:
        return None
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    if not bids or not asks:
        return None
    best_bid = safe_float(bids[-1].get("price"))
    best_ask = safe_float(asks[-1].get("price"))
    if best_bid is None or best_ask is None or best_ask <= best_bid:
        return None
    mid = (best_bid + best_ask) / 2
    scores = {"bid": 0.0, "ask": 0.0}
    depth = {"bid": 0.0, "ask": 0.0}
    for side, levels in (("bid", bids), ("ask", asks)):
        for level in levels:
            price = safe_float(level.get("price"))
            size = safe_float(level.get("size"))
            if price is None or size is None:
                continue
            distance = abs(mid - price)
            scores[side] += _quadratic_score(distance, v, size)
            if distance <= v:
                depth[side] += size
    return {
        "mid": mid,
        "bid_score": scores["bid"],
        "ask_score": scores["ask"],
        "bid_depth": depth["bid"],
        "ask_depth": depth["ask"],
    }


def _price_series(
    settings: dict[str, Any], token_id: str, *, interval: str, fidelity_minutes: int
) -> list[tuple[float, float]] | None:
    """(unix seconds, price) mid series from the public prices-history feed."""
    try:
        response = requests.get(
            f"{str(settings['clob_base_url']).rstrip('/')}/prices-history",
            params={"market": token_id, "interval": interval, "fidelity": fidelity_minutes},
            timeout=float(settings["request_timeout_seconds"]),
        )
        response.raise_for_status()
        history = response.json().get("history") or []
    except Exception:
        return None
    series: list[tuple[float, float]] = []
    for point in history:
        if not isinstance(point, dict):
            continue
        stamp = safe_float(point.get("t"))
        price = safe_float(point.get("p"))
        if stamp is not None and price is not None:
            series.append((stamp, price))
    series.sort(key=lambda item: item[0])
    return series


def _mid_at(series: list[tuple[float, float]], stamp: float, tolerance_seconds: float) -> float | None:
    """Nearest mid at/just before ``stamp`` within tolerance (series sorted)."""
    from bisect import bisect_right

    index = bisect_right(series, (stamp, float("inf"))) - 1
    if index < 0:
        return None
    point_stamp, price = series[index]
    return price if stamp - point_stamp <= tolerance_seconds else None


def _pickoff_from_series(
    series: list[tuple[float, float]] | None,
    quote_distance: float,
    quote_size: float,
    *,
    fidelity_minutes: int,
    min_points: int,
) -> dict[str, float] | None:
    """Pick-off charge per day from one price-history window.

    Every |move| > quote_distance across one bar is assumed to trade through
    our full resting size on the wrong side; the per-share loss is the move's
    excess over our distance from mid. Bars hide faster jumps, so this is an
    approximation, not a floor.
    """
    if series is None or len(series) < min_points:
        return None
    prices = [price for _, price in series]
    events = 0
    loss_usd = 0.0
    for previous, current in zip(prices, prices[1:]):
        excess = abs(current - previous) - quote_distance
        if excess > 0:
            events += 1
            loss_usd += excess * quote_size
    span_days = max((len(prices) - 1) * fidelity_minutes / 1440.0, 1e-9)
    return {
        "history_points": len(prices),
        "pickoff_events_per_day": round(events / span_days, 2),
        "adverse_usd_per_day": round(loss_usd / span_days, 4),
    }


def _recent_prints(settings: dict[str, Any], condition_id: str) -> list[dict[str, float]]:
    """Executed trades (price/size/aggressor side/stamp) from the data-API."""
    try:
        response = requests.get(
            f"{str(settings['data_api_base_url']).rstrip('/')}/trades",
            params={"market": condition_id, "limit": int(settings["prints_limit"])},
            timeout=float(settings["request_timeout_seconds"]),
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return []
    trades = payload if isinstance(payload, list) else payload.get("trades") or payload.get("data") or []
    prints: list[dict[str, float]] = []
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        price = safe_float(trade.get("price"))
        size = safe_float(trade.get("size"))
        stamp = safe_float(trade.get("timestamp") or trade.get("matchTime"))
        side = str(trade.get("side") or "").upper()
        if price is None or size is None or stamp is None or side not in {"BUY", "SELL"}:
            continue
        prints.append({"price": price, "size": size, "stamp": stamp, "side": side})
    return prints


def _markout_adverse(
    settings: dict[str, Any],
    prints: list[dict[str, float]],
    series: list[tuple[float, float]] | None,
    quote_distance: float,
    quote_size: float,
    depth: dict[str, float],
) -> dict[str, Any] | None:
    """Empirical pick-off charge from prints that swept through our level.

    A SELL print at/below mid - d would have filled our bid; a BUY print
    at/above mid + d would have filled our ask. The per-share loss is our
    fill price versus the mid ``markout_horizon_minutes`` later, weighted by
    our queue share q / (q + resting band depth on that side). Signed means
    can be negative (spread capture beats momentum); the CHARGE floors at 0.
    """
    if series is None or len(prints) < int(settings["markout_min_prints"]):
        return None
    horizon_seconds = float(settings["markout_horizon_minutes"]) * 60.0
    tolerance = 120.0
    crossing = 0
    loss_usd = 0.0
    stamps: list[float] = []
    for record in prints:
        stamps.append(record["stamp"])
        mid_then = _mid_at(series, record["stamp"], tolerance)
        mid_later = _mid_at(series, record["stamp"] + horizon_seconds, tolerance)
        if mid_then is None or mid_later is None:
            continue
        if record["side"] == "SELL" and record["price"] <= mid_then - quote_distance:
            fill_price = mid_then - quote_distance
            per_share = fill_price - mid_later  # we bought; positive = loss
            queue_share = quote_size / (quote_size + depth.get("bid", 0.0))
        elif record["side"] == "BUY" and record["price"] >= mid_then + quote_distance:
            fill_price = mid_then + quote_distance
            per_share = mid_later - fill_price  # we sold; positive = loss
            queue_share = quote_size / (quote_size + depth.get("ask", 0.0))
        else:
            continue
        crossing += 1
        loss_usd += per_share * min(record["size"], quote_size) * queue_share
    span_days = max((max(stamps) - min(stamps)) / 86400.0, 1.0 / 24.0)
    return {
        "prints_seen": len(prints),
        "band_crossing_prints_per_day": round(crossing / span_days, 2),
        "adverse_usd_per_day_markout": round(max(0.0, loss_usd / span_days), 4),
    }


def _adverse_selection(
    settings: dict[str, Any],
    token_id: str,
    condition_id: str,
    quote_distance: float,
    quote_size: float,
    depth: dict[str, float],
) -> dict[str, Any] | None:
    """Charge the WORST of the three estimates.

    The calm-last-24h failure mode is real (observed live: a market flat for a
    day, then a news gap): the 7-day/10-minute window prices event risk that
    the 1-minute window misses. The markout estimate measures what executed
    prints actually did to the passive side and is the only EMPIRICAL leg.
    """
    fast_fidelity = max(1, int(settings["reaction_minutes"]))
    fast_series = _price_series(settings, token_id, interval="1d", fidelity_minutes=fast_fidelity)
    slow_series = _price_series(settings, token_id, interval="1w", fidelity_minutes=10)
    fast = _pickoff_from_series(fast_series, quote_distance, quote_size, fidelity_minutes=fast_fidelity, min_points=30)
    slow = _pickoff_from_series(slow_series, quote_distance, quote_size, fidelity_minutes=10, min_points=30)
    if fast is None and slow is None:
        return None
    markout = _markout_adverse(
        settings, _recent_prints(settings, condition_id), fast_series, quote_distance, quote_size, depth
    )
    charges = [row["adverse_usd_per_day"] for row in (fast, slow) if row is not None]
    if markout is not None:
        charges.append(markout["adverse_usd_per_day_markout"])
    primary = fast or slow
    return {
        "history_points": primary["history_points"],
        "pickoff_events_per_day": primary["pickoff_events_per_day"],
        "adverse_usd_per_day_1min_24h": fast["adverse_usd_per_day"] if fast else None,
        "adverse_usd_per_day_10min_7d": slow["adverse_usd_per_day"] if slow else None,
        "adverse_usd_per_day_markout": markout["adverse_usd_per_day_markout"] if markout else None,
        "band_crossing_prints_per_day": markout["band_crossing_prints_per_day"] if markout else None,
        "markout_measured": markout is not None,
        "adverse_selection_usd_per_day": max(charges),
        "both_windows": fast is not None and slow is not None,
    }


def run_maker_carry_study(cfg: EngineConfig) -> dict[str, Any]:
    settings = _settings(cfg)
    out_root = cfg.output_root / "maker_carry"
    summary_path = out_root / "maker_carry_study.json"
    summary: dict[str, Any] = {
        "status": "disabled",
        "generated_at_utc": now_utc(),
        "work_order": "WO-36",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    if str(settings.get("enabled", True)).strip().lower() in {"0", "false", "no"}:
        write_json(summary_path, summary)
        return summary

    universe, errors = _rewarded_universe(settings)
    pause = float(settings["request_pause_seconds"])
    candidates: list[dict[str, Any]] = []
    for market in universe[: int(settings["max_book_candidates"])]:
        v = market["rewards_max_spread_cents"] / 100.0
        quote_distance = float(settings["quote_distance_fraction"]) * v
        quote_size = market["rewards_min_size_shares"]
        our_score = _quadratic_score(quote_distance, v, quote_size)
        competition = _book_competition(settings, market["token_id"], v)
        if competition is None or our_score <= 0:
            continue
        # Polymarket scores each maker at min(Q_bid, Q_ask); the resting pool's
        # two-sided total is bounded by its thinner side.
        pool = min(competition["bid_score"], competition["ask_score"])
        share = our_score / (our_score + pool) if (our_score + pool) > 0 else 0.0
        gross = market["pot_usd_per_day"] * share
        depth = {"bid": competition["bid_depth"], "ask": competition["ask_depth"]}
        adverse = _adverse_selection(
            settings, market["token_id"], market["condition_id"], quote_distance, quote_size, depth
        )
        row = {
            **market,
            "mid_price": round(competition["mid"], 4),
            "quote_distance": round(quote_distance, 4),
            "competitor_score_bid": round(competition["bid_score"], 1),
            "competitor_score_ask": round(competition["ask_score"], 1),
            "our_score_per_side": round(our_score, 1),
            "estimated_reward_share": round(share, 5),
            "gross_reward_usd_per_day": round(gross, 4),
            # Bid collateral plus inventory to quote the ask, both ~size x price.
            "capital_usd": round(quote_size * 2 * competition["mid"], 2),
        }
        if adverse is None:
            row.update(
                {
                    "history_points": 0,
                    "pickoff_events_per_day": None,
                    "adverse_usd_per_day_1min_24h": None,
                    "adverse_usd_per_day_10min_7d": None,
                    "adverse_usd_per_day_markout": None,
                    "band_crossing_prints_per_day": None,
                    "markout_measured": False,
                    "adverse_selection_usd_per_day": None,
                    "net_carry_usd_per_day": None,
                    "estimate_quality": "no_price_history",
                }
            )
        else:
            both_windows = adverse.pop("both_windows")
            row.update(adverse)
            row["net_carry_usd_per_day"] = round(gross - adverse["adverse_selection_usd_per_day"], 4)
            if share > float(settings["max_trusted_reward_share"]):
                row["estimate_quality"] = "thin_book_untrusted"
            elif not both_windows:
                row["estimate_quality"] = "single_window_history"
            else:
                row["estimate_quality"] = "book_and_history"
        candidates.append(row)
        if pause:
            time.sleep(pause)

    # Greedy sized portfolio: fully trusted estimates only (both history
    # windows, credible competition), quote size chosen per market to maximise
    # net carry inside the capital cap. share(k) = k*ours / (k*ours + pool)
    # has diminishing returns while the pick-off charge scales linearly, so
    # each market has a finite optimal size.
    portfolio: list[dict[str, Any]] = []
    capital = 0.0
    cap = float(settings["capital_cap_usd"])
    max_multiple = max(1, int(settings["max_size_multiple"]))
    trusted = [
        r
        for r in candidates
        if (r.get("net_carry_usd_per_day") or 0) > 0 and r.get("estimate_quality") == "book_and_history"
    ]
    payout_floor = float(settings["min_daily_payout_usd"])
    for row in sorted(trusted, key=lambda r: r["net_carry_usd_per_day"], reverse=True):
        ours = row["our_score_per_side"]
        pool_implied = ours / row["estimated_reward_share"] - ours if row["estimated_reward_share"] > 0 else 0.0
        best: tuple[float, int] | None = None
        for k in range(1, max_multiple + 1):
            if capital + k * row["capital_usd"] > cap:
                break
            share_k = (k * ours) / (k * ours + pool_implied) if (k * ours + pool_implied) > 0 else 0.0
            gross_k = row["pot_usd_per_day"] * share_k
            if gross_k < payout_floor:
                # Gate M-C by construction: accruals below Polymarket's $1/day
                # minimum are never paid, so this size earns exactly nothing.
                continue
            net_k = gross_k - k * row["adverse_selection_usd_per_day"]
            if best is None or net_k > best[0]:
                best = (net_k, k)
        if best is None or best[0] <= 0:
            continue
        net_k, k = best
        portfolio.append(
            {
                "question": row["question"],
                "condition_id": row["condition_id"],
                "size_multiple": k,
                "quote_size_shares": k * row["rewards_min_size_shares"],
                "quote_distance": row["quote_distance"],
                "capital_usd": round(k * row["capital_usd"], 2),
                "net_carry_usd_per_day": round(net_k, 4),
                "markout_measured": bool(row.get("markout_measured")),
                "event_risk_flags": [
                    keyword for keyword in EVENT_RISK_KEYWORDS if keyword in row["question"].lower()
                ],
            }
        )
        capital += k * row["capital_usd"]

    net_total = round(sum(r["net_carry_usd_per_day"] for r in portfolio), 2)
    target = float(settings["target_net_usd_per_day"])

    # MAKER GATES - pre-registered 2026-07-09 (see module docstring). The
    # trend ledger is read BEFORE this run appends, then the current run
    # counts itself, so a single day can never satisfy M-A.
    history_path = out_root / "maker_carry_history.csv"
    prior_runs = read_csv_rows(history_path)
    runs_at_target = sum(
        1 for run in prior_runs if (safe_float(run.get("portfolio_net_carry_usd_per_day")) or 0.0) >= target
    )
    latest_at_target = bool(portfolio) and net_total >= target
    if latest_at_target:
        runs_at_target += 1
    required_runs = int(settings["gate_min_runs_at_target"])
    gate_a_state = "pass" if latest_at_target and runs_at_target >= required_runs else "pending"
    gate_b_state = (
        "pass" if portfolio and all(entry["markout_measured"] for entry in portfolio) else "pending"
    )
    maker_verdict = (
        "evidence_supported_pending_human_decision"
        if gate_a_state == "pass" and gate_b_state == "pass"
        else "insufficient_evidence"
    )
    maker_gates = {
        "registered_at_utc": MAKER_GATES_REGISTERED_AT_UTC,
        "M_A_carry_evidence": {
            "state": gate_a_state,
            "runs_at_or_above_target": runs_at_target,
            "required_runs": required_runs,
            "latest_run_at_target": latest_at_target,
        },
        "M_B_adverse_realism": {
            "state": gate_b_state,
            "note": "every portfolio market must carry a MEASURED markout charge (empirical fills).",
        },
        "M_C_payout_floor": {
            "state": "pass_by_construction",
            "min_daily_payout_usd": float(settings["min_daily_payout_usd"]),
        },
        "maker_verdict": maker_verdict,
        "governance_note": (
            "A supported verdict NEVER places or authorises orders. Acting on the quote sheet is a "
            "human decision taken outside this system; the repo stays paper-only regardless."
        ),
    }

    summary.update(
        {
            "status": "ok" if candidates else ("failed" if errors else "no_candidates"),
            "universe_rewarded_markets": len(universe),
            "universe_pot_usd_per_day": round(sum(m["pot_usd_per_day"] for m in universe), 2),
            "candidates_measured": len(candidates),
            "candidates_thin_book_untrusted": sum(
                1 for r in candidates if r.get("estimate_quality") == "thin_book_untrusted"
            ),
            "portfolio_markets": len(portfolio),
            "portfolio": portfolio,
            "portfolio_capital_usd": round(capital, 2),
            "portfolio_net_carry_usd_per_day": net_total,
            "portfolio_net_carry_usd_per_month": round(net_total * 30, 2),
            "target_net_usd_per_day": target,
            "clears_100_per_month_target": bool(portfolio) and net_total >= target,
            "maker_gates": maker_gates,
            "assumptions": {
                "quote_size_shares": "rewards_min_size per market, both sides",
                "quote_distance": f"{settings['quote_distance_fraction']} x rewards_max_spread from mid",
                "share_model": "our quadratic score vs min(bid, ask) resting-book score inside the band",
                "adverse_selection_model": (
                    f"charge = worst of 24h@{settings['reaction_minutes']}min bars, 7d@10min bars, and the "
                    f"empirical markout of band-crossing prints at {settings['markout_horizon_minutes']}min, "
                    f"queue-share weighted against resting band depth"
                ),
                "thin_book_guard": f"raw share > {settings['max_trusted_reward_share']} excluded from portfolio",
            },
            "honesty_clause": (
                "Simulated maker fills are unverifiable (queue position, intra-minute jumps, competitor "
                "reaction). Net carry is an UPPER BOUND on the reward-share side and an approximation on "
                "the pick-off side. A YES here justifies deeper study only - never order placement."
            ),
            "errors": errors[:10],
        }
    )
    write_csv(out_root / "maker_carry_candidates.csv", candidates, fieldnames=CANDIDATE_FIELDS)
    write_json(summary_path, summary)
    # Daily trend ledger: pots, competition, and estimated carry move with the
    # calendar (esp. across the WC window), so keep a one-row-per-run history.
    history_fields = [
        "generated_at_utc",
        "universe_rewarded_markets",
        "universe_pot_usd_per_day",
        "portfolio_markets",
        "portfolio_capital_usd",
        "portfolio_net_carry_usd_per_day",
        "clears_100_per_month_target",
    ]
    prior_runs.append({field: summary.get(field) for field in history_fields})
    write_csv(history_path, prior_runs, fieldnames=history_fields)
    _write_quote_sheet(out_root, summary, settings)
    return summary


def _write_quote_sheet(out_root: Path, summary: dict[str, Any], settings: dict[str, Any]) -> None:
    """Human-readable daily quote sheet - research output, never an order.

    Exists so a human can act on a supported maker verdict OUTSIDE this
    system with their own judgement and capital. The system itself remains
    paper-only and never touches an exchange."""
    gates = summary.get("maker_gates", {})
    lines = [
        "# Maker quote sheet (WO-36) - RESEARCH OUTPUT, NOT ADVICE",
        "",
        f"Generated: {summary.get('generated_at_utc')}",
        f"Maker verdict: **{gates.get('maker_verdict', 'insufficient_evidence')}** "
        f"(M-A {gates.get('M_A_carry_evidence', {}).get('state')}, "
        f"M-B {gates.get('M_B_adverse_realism', {}).get('state')})",
        f"Estimated portfolio net carry: ${summary.get('portfolio_net_carry_usd_per_day')}/day "
        f"(~${summary.get('portfolio_net_carry_usd_per_month')}/month) on "
        f"${summary.get('portfolio_capital_usd')} capital - UPPER BOUND, see honesty clause.",
        "",
        "This system places NO orders. Acting on this sheet is a human decision,",
        "with human money, outside the bot's paper-only governance.",
        "",
        "| market | quote size (shares/side) | distance from mid | capital | est net/day | risk flags |",
        "|---|---|---|---|---|---|",
    ]
    for entry in summary.get("portfolio", []) or []:
        flags = ", ".join(entry.get("event_risk_flags") or []) or "-"
        lines.append(
            f"| {entry['question'][:60]} | {entry['quote_size_shares']:.0f} | "
            f"{entry['quote_distance']} | ${entry['capital_usd']} | "
            f"${entry['net_carry_usd_per_day']} | {flags} |"
        )
    lines += [
        "",
        "Standing rules (non-negotiable if a human ever acts on this):",
        "1. Never quote through a scheduled announcement (any flagged row: pull",
        "   quotes at least 24h before the event and stay out until it settles).",
        "2. Start at minimum size for a full reward day before any size-up.",
        f"3. Rewards below ${settings['min_daily_payout_usd']}/market/day pay NOTHING - stay above the floor.",
        "4. If realised fills exceed the modelled band-crossing rate, stop: the",
        "   markout model is being beaten by faster flow.",
        "5. Re-read this sheet daily; pots and competition move with the calendar.",
    ]
    (out_root / "maker_quote_sheet.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(config_path: str) -> dict[str, Any]:
    return run_maker_carry_study(load_config(config_path))
