from __future__ import annotations

import asyncio
import importlib
import json
import sqlite3
import time
from typing import Any

from .config import EngineConfig, load_config
from .utils import now_utc, parse_timestamp, read_csv_rows, read_json, safe_float, write_csv, write_json

_TARGET_DECISIONS = {"collect_settlement_evidence", "candidate_for_focus"}
_PROFIT_SPRINT_TARGET_ACTIONS = {"WAIT_ACTIVE_WINDOW", "SHADOW_LABEL_GATE", "SHADOW_EDGE_WATCH"}
_DEFAULT_TARGET_FAMILIES = {
    "sports_other",
    "crypto_btc_special",
    "crypto_btc_updown_15m",
    "crypto_eth_updown_15m",
    "crypto_updown_event",
}
# These are families where local evidence already showed poor behaviour or where the
# current classifier is too generic for paper-quality routing. They can still appear
# in liquidity discovery diagnostics, but they should not crowd out websocket slots.
_EXCLUDED_FAMILIES = {
    "unknown",
    "crypto_btc_updown_5m",
    "crypto_sol_updown_5m",
    "crypto_xrp_updown_5m",
}
_FEEDBACK_TARGET_LEARNING_STATES = {
    "suppress_negative_price_action_and_broaden",
    "collect_model_validation_gap_price_action_evidence",
}


async def _collect_messages(
    url: str,
    seconds: int,
    subscription_message: dict[str, Any] | None,
    *,
    connect_timeout_seconds: float = 8.0,
) -> list[dict[str, Any]]:
    websockets = importlib.import_module("websockets")
    rows: list[dict[str, Any]] = []
    deadline = time.time() + max(0, seconds)
    async with websockets.connect(
        url,
        open_timeout=max(1.0, connect_timeout_seconds),
        close_timeout=1.0,
        ping_interval=20,
        ping_timeout=5,
    ) as ws:
        if subscription_message:
            await ws.send(json.dumps(subscription_message))
        while time.time() < deadline:
            try:
                message = await asyncio.wait_for(ws.recv(), timeout=max(0.1, min(1.0, deadline - time.time())))
            except asyncio.TimeoutError:
                continue
            except Exception:
                if rows:
                    break
                raise
            rows.append({"collected_at_utc": now_utc(), "message": message})
    return rows


def _existing_messages(path) -> list[dict[str, Any]]:
    existing = read_json(path, default=[])
    if not isinstance(existing, list):
        return []
    return [row for row in existing if isinstance(row, dict)]


def _boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _token_id(row: dict[str, Any]) -> str:
    return str(row.get("token_id") or row.get("asset_id") or row.get("outcome_token_id") or "").strip()


def _collection_setting(cfg: EngineConfig, settings: dict[str, Any], key: str, default: Any) -> Any:
    if key in settings:
        return settings.get(key)
    collection = cfg.raw.get("collection", {})
    if isinstance(collection, dict) and key in collection:
        return collection.get(key)
    return default


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        decoded = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _first_text(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        text = str(row.get(key) or "").strip()
        if text:
            return text
    return ""


def _position_close_time_text(row: dict[str, Any]) -> str:
    direct = _first_text(
        row,
        (
            "close_time",
            "market_close_time",
            "end_time",
            "endDate",
            "endDateIso",
            "closed_at",
        ),
    )
    if direct:
        return direct
    for payload_key in ("source_signal_json", "raw_payload_json", "prediction_payload_json"):
        payload = _json_dict(row.get(payload_key))
        nested = _position_close_time_text(payload) if payload else ""
        if nested:
            return nested
    return ""


def _paper_db_rows(cfg: EngineConfig, query: str) -> list[dict[str, Any]]:
    path = cfg.database_path
    if not path.exists():
        return []
    con: sqlite3.Connection | None = None
    try:
        con = sqlite3.connect(path, timeout=2)
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute(query).fetchall()]
    except sqlite3.Error:
        return []
    finally:
        if con is not None:
            con.close()


def _paper_order_payload_index(cfg: EngineConfig) -> dict[str, dict[str, Any]]:
    rows = read_csv_rows(cfg.output_root / "polymarket_portfolio" / "paper_orders.csv")
    rows.extend(
        _paper_db_rows(
            cfg,
            """
            SELECT market_id, token_id, created_at, source_signal_json
            FROM orders
            WHERE mode = 'paper'
            ORDER BY created_at, order_id
            """,
        )
    )
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = _json_dict(row.get("source_signal_json"))
        if not payload:
            continue
        market_id = str(row.get("market_id") or payload.get("market_id") or "").strip()
        token_id = _token_id(row) or _token_id(payload)
        if not token_id:
            continue
        payload = {**payload, "market_id": market_id or payload.get("market_id", ""), "token_id": token_id}
        if market_id:
            index[f"{market_id}|{token_id}"] = payload
        index[f"token:{token_id}"] = payload
    return index


def _open_position_rows(cfg: EngineConfig) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in read_csv_rows(cfg.output_root / "polymarket_shadow" / "shadow_positions.csv"):
        if str(row.get("status") or "").strip().lower() != "open":
            continue
        if not _token_id(row):
            continue
        rows.append({**row, "position_source": "shadow_position"})

    order_payloads = _paper_order_payload_index(cfg)
    paper_rows = read_csv_rows(cfg.output_root / "polymarket_portfolio" / "positions.csv")
    paper_rows.extend(
        _paper_db_rows(
            cfg,
            """
            SELECT *
            FROM positions
            WHERE status = 'open' AND quantity > 0
            ORDER BY updated_at, position_id
            """,
        )
    )
    for row in paper_rows:
        status = str(row.get("status") or "").strip().lower()
        quantity = safe_float(row.get("quantity"))
        if status and status != "open":
            continue
        if quantity is not None and quantity <= 0:
            continue
        token_id = _token_id(row)
        if not token_id:
            continue
        market_id = str(row.get("market_id") or "").strip()
        payload = order_payloads.get(f"{market_id}|{token_id}") or order_payloads.get(f"token:{token_id}") or {}
        enriched = {**payload, **row, "token_id": token_id, "position_source": "paper_position"}
        for key in ("close_time", "market_slug", "question", "outcome", "category", "event_id", "correlation_key"):
            if not str(enriched.get(key) or "").strip() and str(payload.get(key) or "").strip():
                enriched[key] = payload.get(key)
        if payload and not str(enriched.get("source_signal_json") or "").strip():
            enriched["source_signal_json"] = json.dumps(payload, sort_keys=True)
        rows.append(enriched)
    return rows


def position_tokens(cfg: EngineConfig, *, as_of: Any | None = None) -> list[dict[str, Any]]:
    """Return open shadow/paper position tokens that must stay in websocket coverage.

    Known close times are retained until market close plus
    ``position_grace_hours``. Rows missing close time are retained while the
    position is open because dropping them would make CLV/settlement evidence
    impossible to recover; the reserved slot cap bounds that coverage.
    """
    settings = cfg.raw.get("websocket_market_data", {})
    if not isinstance(settings, dict):
        settings = {}
    if not _boolish(_collection_setting(cfg, settings, "use_position_targets", True)):
        return []
    grace_hours = safe_float(_collection_setting(cfg, settings, "position_grace_hours", 6))
    if grace_hours is None or grace_hours < 0:
        grace_hours = 6.0
    include_missing_close = _boolish(_collection_setting(cfg, settings, "include_position_tokens_without_close_time", True))
    as_of_dt = parse_timestamp(as_of) or parse_timestamp(now_utc())
    if as_of_dt is None:
        return []

    selected: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    for row in _open_position_rows(cfg):
        token_id = _token_id(row)
        if not token_id:
            continue
        close_text = _position_close_time_text(row)
        close_dt = parse_timestamp(close_text)
        if close_dt is not None:
            seconds_after_close = (as_of_dt - close_dt).total_seconds()
            if seconds_after_close > grace_hours * 3600.0:
                continue
            close_state = "within_grace" if seconds_after_close >= 0 else "future_close"
        else:
            if not include_missing_close:
                continue
            close_state = "missing_close_time"
        target = {
            "token_id": token_id,
            "asset_id": token_id,
            "market_id": row.get("market_id", ""),
            "market_slug": row.get("market_slug", ""),
            "question": row.get("question", ""),
            "outcome": row.get("outcome", ""),
            "family": row.get("family") or row.get("category") or row.get("signal_cohort") or row.get("position_source", ""),
            "category": row.get("category", ""),
            "close_time": close_text,
            "position_source": row.get("position_source", ""),
            "position_close_state": close_state,
            "open_position_target": True,
            "selection_reason": "open_position",
            "websocket_target_reason": "open_position",
        }
        existing = seen.get(token_id)
        if existing is not None:
            sources = {
                item
                for item in str(existing.get("position_source") or "").split("+")
                if item
            }
            source = str(target.get("position_source") or "")
            if source:
                sources.add(source)
            existing["position_source"] = "+".join(sorted(sources))
            if not str(existing.get("close_time") or "").strip() and close_text:
                existing["close_time"] = close_text
                existing["position_close_state"] = close_state
            continue
        selected.append(target)
        seen[token_id] = target

    def rank(row: dict[str, Any]) -> tuple[int, float, str]:
        close_dt = parse_timestamp(row.get("close_time"))
        if close_dt is None:
            return (1, float("inf"), str(row.get("token_id") or ""))
        return (0, close_dt.timestamp(), str(row.get("token_id") or ""))

    selected.sort(key=rank)
    return selected


def _position_priority_rows(cfg: EngineConfig, settings: dict[str, Any], *, max_assets: int) -> list[dict[str, Any]]:
    if max_assets <= 0:
        return []
    requested_slots = int(_collection_setting(cfg, settings, "position_token_slots", 10) or 10)
    if requested_slots <= 0:
        return []
    half_cap = max(1, (max_assets + 1) // 2)
    slot_cap = min(max_assets, requested_slots, half_cap)
    return position_tokens(cfg)[:slot_cap]


def _with_position_targets(
    cfg: EngineConfig,
    settings: dict[str, Any],
    selected: list[dict[str, Any]],
    *,
    max_assets: int,
) -> list[dict[str, Any]]:
    position_rows = _position_priority_rows(cfg, settings, max_assets=max_assets)
    if not position_rows:
        return selected[:max_assets]
    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for row in position_rows + selected:
        token_id = _token_id(row)
        if not token_id or token_id in seen_ids:
            continue
        out.append(row)
        seen_ids.add(token_id)
        if len(out) >= max_assets:
            break
    return out


def _match_keys(row: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    token = _token_id(row)
    if token:
        keys.add(f"token:{token}")
    slug = str(row.get("market_slug") or row.get("slug") or "").strip().lower()
    outcome = str(row.get("outcome") or row.get("selection") or "").strip().lower()
    if slug and outcome:
        keys.add(f"slug:{slug}|outcome:{outcome}")
    return keys


def _target_families(cfg: EngineConfig, settings: dict[str, Any]) -> set[str]:
    configured = settings.get("liquidity_target_families") or []
    if configured:
        return {str(family).strip() for family in configured if str(family).strip()}
    rows = read_csv_rows(cfg.governance_root / "family_viability_leaderboard.csv")
    families = {
        str(row.get("family") or "").strip()
        for row in rows
        if str(row.get("decision") or "").strip() in _TARGET_DECISIONS
    }
    families = {family for family in families if family and family not in _EXCLUDED_FAMILIES}
    return families or set(_DEFAULT_TARGET_FAMILIES)


def _candidate_rank(row: dict[str, Any]) -> tuple[float, bool, float, float, float]:
    return (
        safe_float(row.get("feedback_broaden_match_score")) or 0.0,
        _boolish(row.get("fast_feedback_liquidity_candidate")),
        safe_float(row.get("liquidity")) or 0.0,
        -(safe_float(row.get("spread")) or 999.0),
        -(safe_float(row.get("time_to_close_hours")) or 999999.0),
    )


def _append_unique(values: list[str], value: Any) -> None:
    text = str(value or "").strip()
    if not text:
        return
    key = text.lower()
    if key not in {item.lower() for item in values}:
        values.append(text)


def _feedback_collection_queries(payload: dict[str, Any], focus_payload: dict[str, Any]) -> list[str]:
    queries: list[str] = []
    for key in ("paper_confirmation_preview", "promotion_candidate_preview"):
        rows = payload.get(key, [])
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    _append_unique(queries, row.get("recommended_collection_query"))
    for raw_query in payload.get("collection_queries", []) or []:
        _append_unique(queries, raw_query)
    for raw_query in payload.get("model_validation_gap_queries", []) or []:
        _append_unique(queries, raw_query)
    for raw_query in focus_payload.get("collection_queries", []) or []:
        _append_unique(queries, raw_query)
    price_action_model = focus_payload.get("price_action_model", {}) or {}
    if isinstance(price_action_model, dict):
        for raw_query in price_action_model.get("validation_gap_queries", []) or []:
            _append_unique(queries, raw_query)
    return queries


def _feedback_has_collection_target(payload: dict[str, Any], focus_payload: dict[str, Any]) -> bool:
    learning_state = str(payload.get("learning_state") or "")
    price_action_model = focus_payload.get("price_action_model", {}) or {}
    focus_validation_gap = (
        isinstance(price_action_model, dict)
        and _boolish(price_action_model.get("validation_gap_needs_collection"))
    )
    return bool(
        learning_state in _FEEDBACK_TARGET_LEARNING_STATES
        or _boolish(payload.get("model_validation_gap_active"))
        or focus_validation_gap
        or safe_float(payload.get("paper_confirmation_candidates"))
        or safe_float(payload.get("promotion_candidates"))
        or safe_float(payload.get("positive_collect_candidates"))
    )


def _family_prefixes_for_collection_query(query: str) -> list[str]:
    text = str(query or "").strip().lower()
    mapped: list[str] = []
    if not text:
        return mapped
    if any(term in text for term in ("world", "cup", "fifa", "soccer", "football", "sports")):
        mapped.extend(["worldcup", "sports_other"])
    if "tennis" in text:
        mapped.append("tennis")
    if any(term in text for term in ("esports", "e-sports", "cs2", "counter-strike", "dota", "lol", "league of legends")):
        mapped.append("esports")
    if any(term in text for term in ("fed", "interest rate", "interest-rate", "rates", "bps")):
        mapped.append("macro_rates")
    if any(term in text for term in ("economy", "inflation", "cpi", "recession", "gdp", "jobs", "unemployment")):
        mapped.append("macro_economy")
    if any(term in text for term in ("stock", "stocks", "equities", "s&p", "nasdaq")):
        mapped.append("equities_macro")
    if "bitcoin" in text or "btc" in text:
        mapped.append("crypto_btc")
    if "ethereum" in text or text == "eth" or " eth " in f" {text} ":
        mapped.append("crypto_eth")
    if "solana" in text or text == "sol" or " sol " in f" {text} ":
        mapped.append("crypto_sol")
    if "xrp" in text or "ripple" in text:
        mapped.append("crypto_xrp")
    if any(term in text for term in ("ai", "openai", "anthropic", "xai", "google", "meta")):
        mapped.append("ai_model")
    out: list[str] = []
    for prefix in mapped:
        _append_unique(out, prefix)
    return out


def _row_search_text(row: dict[str, Any]) -> str:
    return " ".join(
        str(row.get(key) or "")
        for key in ("family", "category", "market_slug", "question", "outcome", "signal_cohort")
    ).lower()


def _query_requires_updown(query: str) -> bool:
    text = str(query or "").strip().lower().replace("-", " ")
    compact = text.replace(" ", "")
    return "updown" in compact or "up or down" in text


def _row_is_updown(row: dict[str, Any]) -> bool:
    text = _row_search_text(row).replace("-", " ")
    compact = text.replace(" ", "")
    return "updown" in compact or "up or down" in text


def _feedback_query_match_score(query: str, row: dict[str, Any]) -> float | None:
    prefixes = _family_prefixes_for_collection_query(query)
    if not _matches_feedback_broaden_family(row, prefixes):
        return None
    score = 10.0
    if _query_requires_updown(query):
        if not _row_is_updown(row):
            return None
        score += 100.0
    elif _row_is_updown(row):
        # A broad "bitcoin" style query can still use up/down rows, but an
        # explicit "btc updown" query should outrank generic crypto markets.
        score += 5.0
    if any(str(row.get("family") or "").lower().startswith(prefix + "_updown") for prefix in prefixes):
        score += 15.0
    if str(row.get("market_slug") or "").strip():
        score += 1.0
    return score


def _feedback_broaden_family_prefixes(cfg: EngineConfig, settings: dict[str, Any]) -> list[str]:
    if not _boolish(settings.get("feedback_broaden_target_enabled", True)):
        return []
    payload = read_json(cfg.governance_root / "price_action_feedback.json", default={}) or {}
    if not isinstance(payload, dict):
        return []
    focus_payload = read_json(cfg.governance_root / "research_focus.json", default={}) or {}
    if not isinstance(focus_payload, dict):
        focus_payload = {}
    if not _feedback_has_collection_target(payload, focus_payload):
        return []
    prefixes: list[str] = []
    for raw_query in _feedback_collection_queries(payload, focus_payload):
        for prefix in _family_prefixes_for_collection_query(raw_query):
            _append_unique(prefixes, prefix)
    return prefixes


def _matches_feedback_broaden_family(row: dict[str, Any], prefixes: list[str]) -> bool:
    if not prefixes:
        return False
    family = str(row.get("family") or row.get("category") or "unknown").strip().lower()
    if not family or family in _EXCLUDED_FAMILIES:
        return False
    return any(family.startswith(prefix) or family == prefix for prefix in prefixes)


def _profit_sprint_rank(row: dict[str, Any]) -> tuple[float, float, float, float]:
    return (
        safe_float(row.get("profit_sprint_target_priority")) or 0.0,
        safe_float(row.get("profit_sprint_target_score")) or 0.0,
        safe_float(row.get("liquidity")) or 0.0,
        -(safe_float(row.get("spread")) or 999.0),
    )


def _feedback_broaden_rows(cfg: EngineConfig, settings: dict[str, Any], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not _boolish(settings.get("feedback_broaden_target_enabled", True)):
        return []
    payload = read_json(cfg.governance_root / "price_action_feedback.json", default={}) or {}
    if not isinstance(payload, dict):
        return []
    focus_payload = read_json(cfg.governance_root / "research_focus.json", default={}) or {}
    if not isinstance(focus_payload, dict):
        focus_payload = {}
    if not _feedback_has_collection_target(payload, focus_payload):
        return []
    query_priorities: dict[str, float] = {}

    def add_query(raw_query: Any, priority: float) -> None:
        query = str(raw_query or "").strip()
        if not query:
            return
        key = query.lower()
        query_priorities[key] = max(priority, query_priorities.get(key, 0.0))

    for key in ("paper_confirmation_preview", "promotion_candidate_preview"):
        rows = payload.get(key, [])
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    add_query(row.get("recommended_collection_query"), 60.0)
    for raw_query in payload.get("collection_queries", []) or []:
        add_query(raw_query, 40.0)
    for raw_query in payload.get("model_validation_gap_queries", []) or []:
        add_query(raw_query, 30.0)
    for raw_query in focus_payload.get("collection_queries", []) or []:
        add_query(raw_query, 20.0)
    price_action_model = focus_payload.get("price_action_model", {}) or {}
    if isinstance(price_action_model, dict):
        for raw_query in price_action_model.get("validation_gap_queries", []) or []:
            add_query(raw_query, 20.0)
    queries = [query for query in _feedback_collection_queries(payload, focus_payload) if query.lower() in query_priorities]
    if not queries:
        return []
    rows: list[dict[str, Any]] = []
    for row in candidates:
        matches = [
            (score + query_priorities.get(query.lower(), 0.0), query)
            for query in queries
            for score in [_feedback_query_match_score(query, row)]
            if score is not None
        ]
        if not matches:
            continue
        score, query = max(matches, key=lambda item: item[0])
        enriched = dict(row)
        enriched["feedback_broaden_target"] = True
        enriched["feedback_broaden_family_prefixes"] = ",".join(_family_prefixes_for_collection_query(query))
        enriched["feedback_broaden_query"] = query
        enriched["feedback_broaden_match_score"] = score
        rows.append(enriched)
    rows.sort(key=_candidate_rank, reverse=True)
    max_rows = int(settings.get("feedback_broaden_target_assets", 4) or 4)
    max_per_family = int(settings.get("feedback_broaden_target_assets_per_family", 2) or 2)
    return _balanced_by_family(rows, max_assets=max_rows, max_per_family=max_per_family)


def _tag_feedback_broaden_targets(rows: list[dict[str, Any]], feedback_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not feedback_rows:
        return rows
    feedback_by_id = {_token_id(row): row for row in feedback_rows if _token_id(row)}
    tagged: list[dict[str, Any]] = []
    for row in rows:
        token_id = _token_id(row)
        if token_id and token_id in feedback_by_id:
            enriched = dict(row)
            enriched["feedback_broaden_target"] = True
            enriched["feedback_broaden_family_prefixes"] = feedback_by_id[token_id].get("feedback_broaden_family_prefixes", "")
            enriched["feedback_broaden_query"] = feedback_by_id[token_id].get("feedback_broaden_query", "")
            enriched["feedback_broaden_match_score"] = feedback_by_id[token_id].get("feedback_broaden_match_score", "")
            tagged.append(enriched)
        else:
            tagged.append(row)
    return tagged


def _balanced_by_family(rows: list[dict[str, Any]], *, max_assets: int, max_per_family: int) -> list[dict[str, Any]]:
    if max_assets <= 0:
        return []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        family = str(row.get("family") or row.get("category") or "unknown")
        grouped.setdefault(family, []).append(row)
    for family_rows in grouped.values():
        family_rows.sort(key=_candidate_rank, reverse=True)

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    families = sorted(grouped.keys(), key=lambda family: _candidate_rank(grouped[family][0]), reverse=True)
    per_family_limit = max(1, max_per_family)
    for depth in range(per_family_limit):
        added = False
        for family in families:
            family_rows = grouped[family]
            if depth >= len(family_rows):
                continue
            row = family_rows[depth]
            token_id = _token_id(row)
            if not token_id or token_id in selected_ids:
                continue
            selected.append(row)
            selected_ids.add(token_id)
            added = True
            if len(selected) >= max_assets:
                return selected
        if not added:
            break

    # Fill any remaining slots with best available rows, still avoiding duplicates.
    for row in sorted(rows, key=_candidate_rank, reverse=True):
        token_id = _token_id(row)
        if not token_id or token_id in selected_ids:
            continue
        selected.append(row)
        selected_ids.add(token_id)
        if len(selected) >= max_assets:
            break
    return selected


def _research_target_rank(row: dict[str, Any]) -> tuple[float, float, float, float]:
    return (
        safe_float(row.get("liquidity")) or 0.0,
        -(safe_float(row.get("relative_spread")) or 999.0),
        -(safe_float(row.get("spread")) or 999.0),
        -(safe_float(row.get("time_to_close_hours")) or 999999.0),
    )


def _research_liquidity_rows(
    rows: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    settings: dict[str, Any],
    *,
    max_assets: int,
) -> list[dict[str, Any]]:
    """Fill unused websocket capacity with broader research-only targets.

    Strict tradable rows stay first.  These rows are for learning bid/ask
    behaviour in under-covered families; they do not make a market paper
    tradable and the downstream broker still requires governed signal rows.
    """
    if not _boolish(settings.get("include_research_liquidity_targets", True)):
        return []
    remaining_slots = max(0, max_assets - len(selected))
    if remaining_slots <= 0:
        return []
    max_rows = min(remaining_slots, int(settings.get("max_research_target_assets", remaining_slots) or remaining_slots))
    max_per_family = int(settings.get("max_research_target_assets_per_family", 6) or 6)
    min_liquidity = safe_float(settings.get("research_min_liquidity"))
    if min_liquidity is None:
        min_liquidity = 25.0
    max_spread = safe_float(settings.get("research_max_spread"))
    if max_spread is None:
        max_spread = 0.12
    max_relative_spread = safe_float(settings.get("research_max_relative_spread"))
    if max_relative_spread is None:
        max_relative_spread = 0.60

    selected_ids = {_token_id(row) for row in selected if _token_id(row)}
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        token_id = _token_id(row)
        if not token_id or token_id in selected_ids or token_id in seen:
            continue
        family = str(row.get("family") or row.get("category") or "unknown").strip()
        if not family or family in _EXCLUDED_FAMILIES:
            continue
        liquidity = safe_float(row.get("liquidity")) or 0.0
        spread = safe_float(row.get("spread"))
        relative_spread = safe_float(row.get("relative_spread"))
        if spread is None:
            continue
        if relative_spread is None:
            ask = safe_float(row.get("best_ask") or row.get("ask") or row.get("executable_price"))
            relative_spread = spread / ask if ask and ask > 0 else None
        if liquidity < min_liquidity:
            continue
        if spread > max_spread:
            continue
        if relative_spread is not None and relative_spread > max_relative_spread:
            continue
        enriched = dict(row)
        enriched["research_liquidity_target"] = True
        enriched["websocket_target_reason"] = "broader_repricing_learning"
        enriched["tradable_liquidity_candidate"] = row.get("tradable_liquidity_candidate", "")
        candidates.append(enriched)
        seen.add(token_id)
    candidates.sort(key=_research_target_rank, reverse=True)
    return _balanced_by_family(candidates, max_assets=max_rows, max_per_family=max_per_family)


def _with_research_targets(
    rows: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    settings: dict[str, Any],
    *,
    max_assets: int,
) -> list[dict[str, Any]]:
    if len(selected) >= max_assets:
        return selected[:max_assets]
    additions = _research_liquidity_rows(rows, selected, settings, max_assets=max_assets)
    if not additions:
        return selected[:max_assets]
    selected_ids = {_token_id(row) for row in selected if _token_id(row)}
    out = list(selected)
    for row in additions:
        token_id = _token_id(row)
        if token_id and token_id not in selected_ids:
            out.append(row)
            selected_ids.add(token_id)
        if len(out) >= max_assets:
            break
    return out[:max_assets]


def _profit_sprint_target_index(cfg: EngineConfig) -> dict[str, dict[str, Any]]:
    rows = read_csv_rows(cfg.governance_root / "profit_sprint_targets.csv")
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        action = str(row.get("target_action") or "").strip()
        if action not in _PROFIT_SPRINT_TARGET_ACTIONS:
            continue
        for key in _match_keys(row):
            current = index.get(key)
            if current is None or (safe_float(row.get("target_score")) or 0.0) > (safe_float(current.get("target_score")) or 0.0):
                index[key] = row
    return index


def _profit_sprint_priority_rows(cfg: EngineConfig, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    index = _profit_sprint_target_index(cfg)
    if not index:
        return []
    selected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for row in candidates:
        matched_target: dict[str, Any] | None = None
        for key in _match_keys(row):
            matched_target = index.get(key)
            if matched_target:
                break
        if not matched_target:
            continue
        token_id = _token_id(row)
        if not token_id or token_id in seen_ids:
            continue
        enriched = {
            **row,
            "profit_sprint_target_action": matched_target.get("target_action", ""),
            "profit_sprint_target_score": matched_target.get("target_score", ""),
            "profit_sprint_target_priority": matched_target.get("priority", ""),
            "profit_sprint_recommended_query": matched_target.get("recommended_query", ""),
            "profit_sprint_target_reason": matched_target.get("reason", ""),
        }
        selected.append(enriched)
        seen_ids.add(token_id)
    selected.sort(key=_profit_sprint_rank, reverse=True)
    return selected


def _strategy_v2_rank(row: dict[str, Any]) -> tuple[bool, float, float, float]:
    return (
        str(row.get("latest_status") or "") == "shadow_candidate",
        safe_float(row.get("mark_pnl_usdc")) or 0.0,
        safe_float(row.get("latest_risk_adjusted_anchor_edge")) or safe_float(row.get("entry_risk_adjusted_anchor_edge")) or 0.0,
        safe_float(row.get("latest_liquidity")) or 0.0,
    )


def _strategy_v2_priority_rows(cfg: EngineConfig, settings: dict[str, Any]) -> list[dict[str, Any]]:
    if not _boolish(settings.get("use_strategy_v2_targets", True)):
        return []
    max_rows = int(settings.get("max_strategy_v2_target_assets", 6) or 6)
    if max_rows <= 0:
        return []
    rows = read_csv_rows(cfg.output_root / "polymarket_strategy_v2" / "strategy_v2_forward_evidence.csv")
    selected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for row in sorted(rows, key=_strategy_v2_rank, reverse=True):
        token_id = _token_id(row)
        if not token_id or token_id in seen_ids:
            continue
        if str(row.get("resolved_evidence") or "").strip().lower() == "true":
            continue
        enriched = {
            **row,
            "token_id": token_id,
            "family": row.get("family") or row.get("signal_cohort") or "strategy_v2",
            "strategy_v2_target": True,
            "strategy_v2_signal_cohort": row.get("signal_cohort", ""),
            "strategy_v2_mark_pnl_usdc": row.get("mark_pnl_usdc", ""),
            "strategy_v2_risk_adjusted_anchor_edge": row.get("latest_risk_adjusted_anchor_edge")
            or row.get("entry_risk_adjusted_anchor_edge")
            or "",
        }
        selected.append(enriched)
        seen_ids.add(token_id)
        if len(selected) >= max_rows:
            break
    return selected


def _current_positive_analogue_priority_rows(cfg: EngineConfig, settings: dict[str, Any]) -> list[dict[str, Any]]:
    """Reserve exact websocket tokens for current positive analogue scout entries."""
    if not _boolish(settings.get("use_current_positive_analogue_targets", True)):
        return []
    max_rows = int(settings.get("max_current_positive_analogue_target_assets", 4) or 4)
    if max_rows <= 0:
        return []
    focus = read_json(cfg.governance_root / "research_focus.json", default={}) or {}
    if not isinstance(focus, dict):
        return []
    payload = focus.get("price_action_current_positive_analogues")
    if not isinstance(payload, dict):
        return []
    targets = payload.get("targets")
    if not isinstance(targets, list):
        return []
    liquidity_proxy = safe_float(settings.get("current_positive_analogue_liquidity_proxy"))
    if liquidity_proxy is None or liquidity_proxy <= 0:
        liquidity_proxy = safe_float(settings.get("research_min_liquidity")) or 25.0
    selected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for target in targets:
        if not isinstance(target, dict):
            continue
        token_id = _token_id(target)
        if not token_id or token_id in seen_ids:
            continue
        bid = safe_float(target.get("latest_bid"))
        ask = safe_float(target.get("latest_ask"))
        spread = safe_float(target.get("latest_spread"))
        midpoint = (bid + ask) / 2.0 if bid is not None and ask is not None else ""
        liquidity = safe_float(target.get("liquidity"))
        if liquidity is None or liquidity <= 0:
            liquidity = liquidity_proxy
        selected.append(
            {
                "token_id": token_id,
                "market_slug": target.get("market_slug", ""),
                "question": target.get("question", ""),
                "outcome": target.get("outcome", ""),
                "family": target.get("family", "current_positive_analogue"),
                "best_bid": "" if bid is None else bid,
                "best_ask": "" if ask is None else ask,
                "midpoint": midpoint,
                "spread": "" if spread is None else spread,
                "relative_spread": "" if spread is None or ask is None or ask <= 0 else spread / ask,
                "liquidity": liquidity,
                "current_positive_analogue_target": True,
                "current_positive_analogue_validation_roi": target.get("validation_roi", ""),
                "current_positive_analogue_robust_gap": target.get("robust_validation_roi_gap", ""),
                "websocket_target_reason": "reserve_current_positive_analogue_for_forward_bid_tracking",
            }
        )
        seen_ids.add(token_id)
        if len(selected) >= max_rows:
            break
    return selected


def _liquidity_target_rows(cfg: EngineConfig, settings: dict[str, Any]) -> list[dict[str, Any]]:
    max_assets = int(settings.get("max_liquidity_target_assets", settings.get("max_assets", 24)) or 24)
    if not _boolish(settings.get("use_liquidity_targets", True)):
        return _with_position_targets(cfg, settings, [], max_assets=max_assets)
    watchlist_path = cfg.output_root / "polymarket_liquidity_discovery" / "liquidity_watchlist.csv"
    rows = read_csv_rows(watchlist_path)
    target_all_liquid_families = _boolish(settings.get("target_all_liquid_families", True))
    families = set() if target_all_liquid_families else _target_families(cfg, settings)
    max_per_family = int(settings.get("max_liquidity_target_assets_per_family", 4) or 4)
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        family = str(row.get("family") or row.get("category") or "").strip()
        token_id = _token_id(row)
        if not token_id or token_id in seen:
            continue
        if family in _EXCLUDED_FAMILIES:
            continue
        if families and family not in families:
            continue
        if not _boolish(row.get("tradable_liquidity_candidate")):
            continue
        seen.add(token_id)
        candidates.append(row)
    candidates.sort(key=_candidate_rank, reverse=True)

    current_analogue_rows = _current_positive_analogue_priority_rows(cfg, settings)
    strategy_v2_rows = _strategy_v2_priority_rows(cfg, settings)
    priority_rows = _profit_sprint_priority_rows(cfg, candidates)
    reserved_ids = {_token_id(row) for row in current_analogue_rows if _token_id(row)}
    if not priority_rows and not strategy_v2_rows:
        feedback_rows = _feedback_broaden_rows(cfg, settings, candidates)
        if not feedback_rows:
            selected = current_analogue_rows[:max_assets]
            selected_ids = {_token_id(row) for row in selected if _token_id(row)}
            remaining_candidates = [row for row in candidates if _token_id(row) not in selected_ids]
            selected.extend(
                _balanced_by_family(
                    remaining_candidates,
                    max_assets=max_assets - len(selected),
                    max_per_family=max_per_family,
                )
            )
            return _with_position_targets(
                cfg,
                settings,
                _with_research_targets(rows, selected, settings, max_assets=max_assets),
                max_assets=max_assets,
            )
        reserve = min(len(feedback_rows), max(0, max_assets - 1))
        base_rows = [
            row
            for row in candidates
            if _token_id(row) not in {_token_id(item) for item in feedback_rows[:reserve]}
            and _token_id(row) not in reserved_ids
        ]
        selected = current_analogue_rows[:max_assets]
        selected.extend(
            _balanced_by_family(
                base_rows,
                max_assets=max_assets - reserve - len(selected),
                max_per_family=max_per_family,
            )
        )
        selected_ids = {_token_id(row) for row in selected}
        for row in feedback_rows:
            token_id = _token_id(row)
            if token_id and token_id not in selected_ids:
                selected.append(row)
                selected_ids.add(token_id)
            if len(selected) >= max_assets:
                break
        selected = _tag_feedback_broaden_targets(selected[:max_assets], feedback_rows)
        return _with_position_targets(
            cfg,
            settings,
            _with_research_targets(rows, selected, settings, max_assets=max_assets),
            max_assets=max_assets,
        )

    feedback_rows = _feedback_broaden_rows(cfg, settings, candidates)
    feedback_reserve = min(
        len(feedback_rows),
        max(0, int(settings.get("feedback_broaden_target_assets", 4) or 4)),
        max(0, max_assets - len(current_analogue_rows) - 1),
    )
    normal_budget = max_assets - feedback_reserve
    selected: list[dict[str, Any]] = current_analogue_rows[:normal_budget]
    selected_ids: set[str] = {_token_id(row) for row in selected if _token_id(row)}
    for row in strategy_v2_rows:
        token_id = _token_id(row)
        if not token_id or token_id in selected_ids:
            continue
        selected.append(row)
        selected_ids.add(token_id)
        if len(selected) >= normal_budget:
            break

    for row in priority_rows:
        if len(selected) >= normal_budget:
            break
        token_id = _token_id(row)
        if not token_id or token_id in selected_ids:
            continue
        selected.append(row)
        selected_ids.add(token_id)
        if len(selected) >= normal_budget:
            break

    remaining = [row for row in candidates if _token_id(row) not in selected_ids]
    selected.extend(_balanced_by_family(remaining, max_assets=normal_budget - len(selected), max_per_family=max_per_family))
    selected_ids = {_token_id(row) for row in selected}
    for row in feedback_rows:
        token_id = _token_id(row)
        if token_id and token_id not in selected_ids:
            selected.append(row)
            selected_ids.add(token_id)
        if len(selected) >= max_assets:
            break
    if len(selected) < max_assets:
        remaining = [row for row in candidates if _token_id(row) not in selected_ids]
        selected.extend(_balanced_by_family(remaining, max_assets=max_assets - len(selected), max_per_family=max_per_family))
    selected = _tag_feedback_broaden_targets(selected[:max_assets], feedback_rows)
    return _with_position_targets(
        cfg,
        settings,
        _with_research_targets(rows, selected, settings, max_assets=max_assets),
        max_assets=max_assets,
    )


def _asset_ids_from_rows(rows: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for row in rows:
        token_id = _token_id(row)
        if token_id and token_id not in seen:
            seen.add(token_id)
            ids.append(token_id)
    return ids


def _family_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        family = str(row.get("family") or row.get("category") or "unknown")
        counts[family] = counts.get(family, 0) + 1
    return dict(sorted(counts.items()))


def _fast_feedback_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        if not _boolish(row.get("fast_feedback_liquidity_candidate")):
            continue
        family = str(row.get("family") or row.get("category") or "unknown")
        counts[family] = counts.get(family, 0) + 1
    return dict(sorted(counts.items()))


def _profit_sprint_target_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        action = str(row.get("profit_sprint_target_action") or "")
        if not action:
            continue
        counts[action] = counts.get(action, 0) + 1
    return dict(sorted(counts.items()))


def _strategy_v2_target_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        if not _boolish(row.get("strategy_v2_target")):
            continue
        cohort = str(row.get("strategy_v2_signal_cohort") or row.get("family") or "strategy_v2")
        counts[cohort] = counts.get(cohort, 0) + 1
    return dict(sorted(counts.items()))


def _current_positive_analogue_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        if not _boolish(row.get("current_positive_analogue_target")):
            continue
        family = str(row.get("family") or "current_positive_analogue")
        counts[family] = counts.get(family, 0) + 1
    return dict(sorted(counts.items()))


def _feedback_broaden_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        if not _boolish(row.get("feedback_broaden_target")):
            continue
        family = str(row.get("family") or row.get("category") or "unknown")
        counts[family] = counts.get(family, 0) + 1
    return dict(sorted(counts.items()))


def _research_target_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        if not _boolish(row.get("research_liquidity_target")):
            continue
        family = str(row.get("family") or row.get("category") or "unknown")
        counts[family] = counts.get(family, 0) + 1
    return dict(sorted(counts.items()))


def _position_target_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        if not _boolish(row.get("open_position_target")):
            continue
        source = str(row.get("position_source") or "open_position")
        counts[source] = counts.get(source, 0) + 1
    return dict(sorted(counts.items()))


def _subscription_variants(asset_ids: list[str], settings: dict[str, Any], *, dynamic_ids: bool) -> list[dict[str, Any]]:
    if not dynamic_ids:
        configured = settings.get("subscription_message")
        return [configured] if isinstance(configured, dict) else [{"markets": asset_ids, "type": "market"}]
    return [
        {"assets_ids": asset_ids, "type": "market"},
        {"asset_ids": asset_ids, "type": "market"},
        {"markets": asset_ids, "type": "market"},
    ]


def _collect_with_variants(
    *,
    url: str,
    seconds: int,
    variants: list[dict[str, Any]],
    connect_timeout_seconds: float,
) -> tuple[list[dict[str, Any]], str, list[str]]:
    errors: list[str] = []
    if not variants:
        return [], "", errors
    per_attempt = max(5, int(seconds / max(1, len(variants))))
    for variant in variants:
        try:
            rows = asyncio.run(
                _collect_messages(
                    url,
                    per_attempt,
                    variant,
                    connect_timeout_seconds=connect_timeout_seconds,
                )
            )
        except Exception as exc:  # noqa: BLE001 - try the next supported envelope
            errors.append(f"{list(variant.keys())}:{type(exc).__name__}: {exc}")
            continue
        if rows:
            return rows, json.dumps(variant, sort_keys=True), errors
        errors.append(f"{list(variant.keys())}:no_messages")
    return [], "", errors


def collect_websocket(cfg: EngineConfig, websocket_seconds: int = 60) -> dict[str, Any]:
    settings = cfg.raw.get("websocket_market_data", {})
    out_root = cfg.output_root / "polymarket_websocket"
    out_root.mkdir(parents=True, exist_ok=True)
    try:
        importlib.import_module("websockets")
    except Exception:
        summary = {"status": "skipped", "reason": "websockets dependency is not installed", "messages": 0}
        write_json(out_root / "websocket_summary.json", summary)
        return summary

    target_rows = _liquidity_target_rows(cfg, settings)
    dynamic_ids = _asset_ids_from_rows(target_rows)
    sprint_counts = _profit_sprint_target_counts(target_rows)
    strategy_v2_counts = _strategy_v2_target_counts(target_rows)
    current_analogue_counts = _current_positive_analogue_counts(target_rows)
    feedback_counts = _feedback_broaden_counts(target_rows)
    research_counts = _research_target_counts(target_rows)
    position_counts = _position_target_counts(target_rows)
    if current_analogue_counts and strategy_v2_counts and sprint_counts:
        target_source = "current_positive_analogue+strategy_v2_forward_evidence+profit_sprint_targets+liquidity_watchlist"
    elif current_analogue_counts and strategy_v2_counts:
        target_source = "current_positive_analogue+strategy_v2_forward_evidence+liquidity_watchlist"
    elif current_analogue_counts and sprint_counts:
        target_source = "current_positive_analogue+profit_sprint_targets+liquidity_watchlist"
    elif current_analogue_counts:
        target_source = "current_positive_analogue+liquidity_watchlist"
    elif strategy_v2_counts and sprint_counts:
        target_source = "strategy_v2_forward_evidence+profit_sprint_targets+liquidity_watchlist"
    elif strategy_v2_counts:
        target_source = "strategy_v2_forward_evidence+liquidity_watchlist"
    elif sprint_counts:
        target_source = "profit_sprint_targets+liquidity_watchlist"
    else:
        target_source = "liquidity_watchlist" if dynamic_ids else "configured_market_ids"
    if feedback_counts and dynamic_ids:
        target_source += "+price_action_feedback_broaden"
    if research_counts and dynamic_ids:
        target_source += "+research_repricing_coverage"
    if position_counts and dynamic_ids:
        position_target_total = sum(position_counts.values())
        target_source = "open_positions" if position_target_total == len(target_rows) else f"open_positions+{target_source}"
    if target_rows:
        write_csv(cfg.governance_root / "websocket_liquidity_targets.csv", target_rows)

    market_ids = dynamic_ids or list(settings.get("market_ids", []) or [])
    if not market_ids:
        summary = {
            "status": "skipped",
            "reason": "no websocket market_ids configured and no liquidity targets available",
            "messages": 0,
            "target_source": target_source,
            "target_assets": 0,
        }
        write_json(out_root / "websocket_summary.json", summary)
        return summary
    url = str(settings.get("url", "wss://ws-subscriptions-clob.polymarket.com/ws/market"))
    variants = _subscription_variants(market_ids, settings, dynamic_ids=bool(dynamic_ids))
    output_file = out_root / "websocket_messages.json"
    latest_output_file = out_root / "websocket_messages_latest.json"
    existing_rows = _existing_messages(output_file)
    connect_timeout = float(settings.get("connect_timeout_seconds", 8.0))
    new_rows, selected_subscription, variant_errors = _collect_with_variants(
        url=url,
        seconds=websocket_seconds,
        variants=variants,
        connect_timeout_seconds=connect_timeout,
    )
    if not new_rows:
        write_json(latest_output_file, [])
        summary = {
            "status": "error",
            "reason": "; ".join(variant_errors[-5:]) or "websocket returned no messages",
            "messages": len(existing_rows),
            "new_messages": 0,
            "existing_messages": len(existing_rows),
            "seconds": websocket_seconds,
            "output_file": str(output_file),
            "latest_output_file": str(latest_output_file),
            "append_mode": True,
            "target_source": target_source,
            "target_assets": len(market_ids),
            "target_family_counts": _family_counts(target_rows),
            "target_fast_feedback_family_counts": _fast_feedback_counts(target_rows),
            "target_profit_sprint_counts": sprint_counts,
            "target_strategy_v2_counts": strategy_v2_counts,
            "target_current_positive_analogue_counts": current_analogue_counts,
            "target_feedback_broaden_counts": feedback_counts,
            "target_research_counts": research_counts,
            "target_position_counts": position_counts,
            "subscription_attempts": len(variants),
        }
        write_json(out_root / "websocket_summary.json", summary)
        return summary
    combined_rows = existing_rows + new_rows
    max_messages = int(settings.get("max_messages", 0) or 0)
    dropped_messages = 0
    if max_messages > 0 and len(combined_rows) > max_messages:
        dropped_messages = len(combined_rows) - max_messages
        combined_rows = combined_rows[-max_messages:]
    write_json(output_file, combined_rows)
    write_json(latest_output_file, new_rows)
    summary = {
        "status": "collected",
        "messages": len(combined_rows),
        "new_messages": len(new_rows),
        "existing_messages": len(existing_rows),
        "dropped_messages": dropped_messages,
        "max_messages": max_messages,
        "total_messages": len(combined_rows),
        "seconds": websocket_seconds,
        "output_file": str(output_file),
        "latest_output_file": str(latest_output_file),
        "append_mode": True,
        "target_source": target_source,
        "target_assets": len(market_ids),
        "target_family_counts": _family_counts(target_rows),
        "target_fast_feedback_family_counts": _fast_feedback_counts(target_rows),
        "target_profit_sprint_counts": sprint_counts,
        "target_strategy_v2_counts": strategy_v2_counts,
        "target_current_positive_analogue_counts": current_analogue_counts,
        "target_feedback_broaden_counts": feedback_counts,
        "target_research_counts": research_counts,
        "target_position_counts": position_counts,
        "target_file": str(cfg.governance_root / "websocket_liquidity_targets.csv") if target_rows else "",
        "selected_subscription": selected_subscription,
        "subscription_attempts": len(variants),
        "subscription_errors": variant_errors[-5:],
    }
    write_json(out_root / "websocket_summary.json", summary)
    return summary


def collect_websocket_from_config(config_path: str, websocket_seconds: int = 60) -> dict[str, Any]:
    return collect_websocket(load_config(config_path), websocket_seconds=websocket_seconds)
