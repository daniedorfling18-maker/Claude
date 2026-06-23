from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from .config import EngineConfig, load_config
from .utils import discover_files, find_first_column, infer_category, normalize_slug, parse_timestamp, read_csv_rows, safe_float, write_csv

FORBIDDEN_FEATURE_HINTS = ["winner", "winning", "resolved", "settled", "payout", "final_result", "settlement"]


def _reject_leakage_columns(columns: list[str]) -> None:
    bad = [c for c in columns if any(h in c.lower() for h in FORBIDDEN_FEATURE_HINTS)]
    if bad:
        raise ValueError("Potential leakage columns present. Remove before feature building: " + ", ".join(bad))


def build_features(cfg: EngineConfig) -> list[dict[str, Any]]:
    files = discover_files(cfg.data_root, ["outputs/polymarket_wide/**/raw_market_snapshots.csv", "outputs/polymarket_fixed/**/raw_market_snapshots.csv"])
    features: list[dict[str, Any]] = []
    lineage: list[dict[str, Any]] = []
    for path in files:
        rows = read_csv_rows(path)
        if not rows:
            continue
        cols = list(rows[0].keys())
        _reject_leakage_columns(cols)
        market_col = find_first_column(cols, cfg.raw.get("schema", {}).get("market_id_fields", ["market_id", "condition_id", "id", "slug"]))
        token_col = find_first_column(cols, cfg.raw.get("schema", {}).get("token_id_fields", ["token_id", "asset_id", "outcome_token_id"]))
        ts_col = find_first_column(cols, cfg.raw.get("schema", {}).get("timestamp_fields", ["snapshot_timestamp", "timestamp", "collected_at"]))
        question_col = find_first_column(cols, ["question", "title", "market_question"])
        slug_col = find_first_column(cols, ["slug", "market_slug"])
        bid_col = find_first_column(cols, ["best_bid", "bid"])
        ask_col = find_first_column(cols, ["best_ask", "ask"])
        price_col = find_first_column(cols, ["midpoint", "price", "last_price", "probability"])
        liq_col = find_first_column(cols, ["liquidity", "liquidity_num", "depth"])
        vol_col = find_first_column(cols, ["volume", "volume_num"])
        close_col = find_first_column(cols, ["close_time", "end_time", "closed_at", "end_date"])
        if not market_col or not token_col or not ts_col:
            continue
        rows = sorted(rows, key=lambda r: (r.get(market_col, ""), r.get(token_col, ""), r.get(ts_col, "")))
        history: dict[tuple[str, str], deque[tuple[Any, float]]] = defaultdict(deque)
        for row in rows:
            ts = parse_timestamp(row.get(ts_col))
            if not ts:
                continue
            market = row.get(market_col, "")
            token = row.get(token_col, "")
            bid = safe_float(row.get(bid_col or ""))
            ask = safe_float(row.get(ask_col or ""))
            midpoint = safe_float(row.get(price_col or ""))
            if midpoint is None and bid is not None and ask is not None:
                midpoint = (bid + ask) / 2
            if midpoint is None:
                continue
            spread = (ask - bid) if ask is not None and bid is not None else ""
            close = parse_timestamp(row.get(close_col or ""))
            ttc = ((close - ts).total_seconds() / 3600) if close else ""
            key = (market, token)
            past = list(history[key])
            def change(hours: float) -> str:
                candidates = [(old_ts, old_p) for old_ts, old_p in past if (ts - old_ts).total_seconds() / 3600 >= hours]
                if not candidates:
                    return ""
                return midpoint - candidates[-1][1]
            recent = [p for old_ts, p in past if (ts - old_ts).total_seconds() <= 24*3600]
            vol = ""
            if len(recent) > 1:
                mean = sum(recent) / len(recent)
                vol = (sum((p - mean) ** 2 for p in recent) / (len(recent) - 1)) ** 0.5
            q = row.get(question_col or "", "")
            slug = normalize_slug(row.get(slug_col or "") or q)
            f = {
                "market_id": market,
                "token_id": token,
                "prediction_timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "category": infer_category(path),
                "midpoint": midpoint,
                "best_bid": bid if bid is not None else "",
                "best_ask": ask if ask is not None else "",
                "spread": spread,
                "executable_buy_price": ask if ask is not None else midpoint,
                "implied_probability": midpoint,
                "price_change_1h": change(1),
                "price_change_6h": change(6),
                "price_change_24h": change(24),
                "rolling_volatility_24h": vol,
                "liquidity": safe_float(row.get(liq_col or "")) or "",
                "volume": safe_float(row.get(vol_col or "")) or "",
                "time_to_close_hours": ttc,
                "hour_of_day": ts.hour,
                "day_of_week": ts.weekday(),
                "snapshots_so_far": len(past),
                "question_length": len(q),
                "slug_tokens": len([x for x in slug.split("-") if x]),
                "word_will": int("will" in q.lower()),
                "word_by": int("by" in q.lower()),
                "word_before": int("before" in q.lower()),
                "word_after": int("after" in q.lower()),
                "word_or": int(" or " in q.lower()),
                "word_and": int(" and " in q.lower()),
            }
            features.append(f)
            history[key].append((ts, midpoint))
            lineage.append({"feature_file": str(path), "market_id": market, "token_id": token, "prediction_timestamp": f["prediction_timestamp"], "source_timestamp_column": ts_col, "point_in_time_rule": "history uses rows at or before prediction_timestamp only"})
    out_root = cfg.output_root / "polymarket_training"
    write_csv(out_root / "features.csv", features)
    dictionary = [{"feature": k, "description": "point-in-time engineered feature"} for k in sorted({k for r in features for k in r.keys()})]
    write_csv(out_root / "feature_dictionary.csv", dictionary)
    write_csv(cfg.governance_root / "feature_lineage.csv", lineage)
    return features


def main(config_path: str) -> list[dict[str, Any]]:
    return build_features(load_config(config_path))
