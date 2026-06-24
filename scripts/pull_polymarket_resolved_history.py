"""Pull resolved Polymarket markets (World Cup + other) with their pre-close price
trajectories, and emit the predictive-engine training inputs:

  - outputs/polymarket_training/market_resolutions.csv   (clean binary settlements + targets)
  - outputs/polymarket_training/historical_price_snapshots.csv  (point-in-time midpoints)

Discovery pages the Gamma `markets?closed=true` endpoint; clean binary settlements are
classified with the engine's tested `infer_market_resolution_rows`. Price history comes
from the CLOB `prices-history` endpoint (startTs/endTs/fidelity window — the only form
that returns historical points), subsampled to a coarse grid and trimmed before close so
snapshots are genuinely pre-resolution.

IMPORTANT: the REST price series is a midpoint/price trajectory only. Order-book depth /
imbalance are live-WebSocket-only and cannot be backfilled here; the historical features
are therefore price-derived (momentum, volatility, time-to-close, logit). Run from the
repo root:  python scripts/pull_polymarket_resolved_history.py --target-clean 250
"""
from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from polymarket_predictive_engine.config import load_config  # noqa: E402
from polymarket_predictive_engine.price_history_collector import (  # noqa: E402
    SNAPSHOT_FIELDS,
    normalize_price_history_payload,
)
from polymarket_predictive_engine.resolution_collector import infer_market_resolution_rows  # noqa: E402
from polymarket_predictive_engine.utils import write_csv, write_json  # noqa: E402

GAMMA = "https://gamma-api.polymarket.com/markets"
CLOB_HISTORY = "https://clob.polymarket.com/prices-history"
_CA = "/root/.ccr/ca-bundle.crt"
_CTX = ssl.create_default_context(cafile=_CA) if os.path.exists(_CA) else ssl.create_default_context()

# Match only explicit World Cup / FIFA references. An earlier version also matched
# bare country names to catch fixtures, but that mislabelled geopolitical markets
# (e.g. "US x Iran ceasefire") as World Cup, so the tag is kept strict here.
WORLD_CUP = re.compile(r"world cup|world-cup|\bfifa\b|\bwcup\b|group [a-l] \b", re.I)


def _get(url: str, timeout: int = 40):
    req = urllib.request.Request(url, headers={"User-Agent": "polymarket-predictive-engine/1.0"})
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
        return json.loads(r.read().decode("utf-8"))


def _epoch(value: str) -> int | None:
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def discover(target_clean: int, max_pages: int, pause: float) -> list[dict]:
    """Page Gamma for clean binary settlements, ordered by volume.

    Only liquid (high-volume) closed markets carry a CLOB price history; recent-by-end-date
    markets are mostly zero-volume props with no series, so we page by ``volumeNum`` and stop
    once ``target_clean`` liquid clean settlements are collected (World Cup markets that traded
    are flagged in passing).
    """
    seen: dict[str, dict] = {}
    for page in range(max_pages):
        params = {"closed": "true", "limit": "100", "offset": str(page * 100),
                  "order": "volumeNum", "ascending": "false"}
        try:
            batch = _get(f"{GAMMA}?{urllib.parse.urlencode(params)}")
        except Exception as exc:
            print(f"  discover page {page} error: {exc}", flush=True)
            break
        if not isinstance(batch, list) or not batch:
            break
        for m in batch:
            rows, _ = infer_market_resolution_rows(m)
            if not rows or rows[0].get("resolution_quality") != "clean_settlement":
                continue
            cid = rows[0].get("condition_id") or rows[0].get("market_slug")
            if not cid or cid in seen:
                continue
            end = _epoch(m.get("endDate") or m.get("closedTime"))
            if not end:
                continue
            text = f"{m.get('slug','')} {m.get('question','')}"
            seen[cid] = {"rows": rows, "end_ts": end, "start_ts": _epoch(m.get("startDate")),
                         "is_world_cup": bool(WORLD_CUP.search(text)), "question": m.get("question", "")}
        wc = sum(1 for v in seen.values() if v["is_world_cup"])
        print(f"  volume page {page}: clean liquid markets so far={len(seen)} (world_cup={wc})", flush=True)
        if pause:
            time.sleep(pause)
        if len(seen) >= target_clean:
            break
    return list(seen.values())


def fetch_series(token: str, end_ts: int, start_ts: int | None, lookback_days: int, fidelity: int) -> list[dict]:
    """Return [{timestamp, price}] for a token, trying progressively wider windows.

    Uses a bounded startTs/endTs window with ``fidelity`` minutes per point (the only
    form that returns historical points); coarse fidelity keeps payloads small/fast.
    """
    windows = [(end_ts - lookback_days * 86400, end_ts), (end_ts - 2 * 86400, end_ts)]
    if start_ts and start_ts < end_ts:
        windows.append((max(start_ts, end_ts - 30 * 86400), end_ts))
    for start, end in windows:
        params = {"market": token, "startTs": str(int(start)), "endTs": str(int(end)), "fidelity": str(fidelity)}
        try:
            data = _get(f"{CLOB_HISTORY}?{urllib.parse.urlencode(params)}")
        except Exception:
            continue
        points = normalize_price_history_payload(data)
        if points:
            return points
    return []


def subsample(points: list[dict], grid_minutes: int, cutoff_ts: int, cap: int) -> list[dict]:
    grid = max(1, grid_minutes) * 60
    kept: list[dict] = []
    last_bucket = None
    for p in points:
        ts = _epoch(p["timestamp"])
        if ts is None or ts > cutoff_ts:
            continue
        if grid_minutes > 0:
            bucket = ts // grid
            if bucket == last_bucket:
                continue
            last_bucket = bucket
        kept.append(p)
    return kept[-cap:] if cap and len(kept) > cap else kept


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="polymarket_predictive_config.example.yaml")
    ap.add_argument("--target-clean", type=int, default=250)
    ap.add_argument("--max-pages", type=int, default=40)
    ap.add_argument("--lookback-days", type=int, default=7)
    ap.add_argument("--fidelity", type=int, default=30, help="minutes per price point (API-side downsample)")
    ap.add_argument("--grid-minutes", type=int, default=0, help="extra client-side thinning; 0 = keep API grid")
    ap.add_argument("--close-buffer-hours", type=float, default=1.0)
    ap.add_argument("--max-snapshots-per-token", type=int, default=200)
    ap.add_argument("--min-points-per-token", type=int, default=5)
    ap.add_argument("--pause", type=float, default=0.12)
    args = ap.parse_args()

    cfg = load_config(args.config)
    out_root = cfg.output_root / "polymarket_training"
    out_root.mkdir(parents=True, exist_ok=True)

    print(f"Discovering up to {args.target_clean} clean binary resolved markets ...", flush=True)
    markets = discover(args.target_clean, args.max_pages, args.pause)
    print(f"Discovered {len(markets)} clean markets "
          f"({sum(m['is_world_cup'] for m in markets)} world-cup-tagged). Pulling price history ...", flush=True)

    resolutions: list[dict] = []
    snapshots: list[dict] = []
    markets_with_history = 0
    wc_with_history = 0
    for i, mk in enumerate(markets, 1):
        rows = mk["rows"]
        cutoff = int(mk["end_ts"] - args.close_buffer_hours * 3600)
        token_rows = {r["token_id"]: r for r in rows if r.get("token_id")}
        had_history = False
        for token, r in token_rows.items():
            series = subsample(
                fetch_series(token, mk["end_ts"], mk["start_ts"], args.lookback_days, args.fidelity),
                args.grid_minutes, cutoff, args.max_snapshots_per_token,
            )
            if len(series) < args.min_points_per_token:
                if args.pause:
                    time.sleep(args.pause)
                continue
            had_history = True
            for pt in series:
                snapshots.append({
                    "market_id": r.get("condition_id") or r.get("market_slug") or r.get("gamma_market_id") or "",
                    "market_slug": r.get("market_slug", ""),
                    "condition_id": r.get("condition_id", ""),
                    "gamma_market_id": r.get("gamma_market_id", ""),
                    "token_id": token,
                    "outcome": r.get("outcome", ""),
                    "category": ("worldcup" if mk["is_world_cup"] else (r.get("category") or "other")),
                    "timestamp": pt["timestamp"],
                    "midpoint": pt["price"],
                    "price": pt["price"],
                    "close_time": _iso(mk["end_ts"]),
                    "source": "clob_prices_history_pull",
                })
            if args.pause:
                time.sleep(args.pause)
        if had_history:
            markets_with_history += 1
            wc_with_history += int(mk["is_world_cup"])
            resolutions.extend(rows)
        if i % 25 == 0 or i == len(markets):
            print(f"  {i}/{len(markets)} markets | with_history={markets_with_history} "
                  f"(wc={wc_with_history}) | snapshots={len(snapshots)}", flush=True)

    write_csv(out_root / "market_resolutions.csv", resolutions)
    write_csv(out_root / "historical_price_snapshots.csv", snapshots, fieldnames=SNAPSHOT_FIELDS)
    clean_tokens = len({(r.get("condition_id"), r.get("token_id")) for r in resolutions
                        if r.get("resolution_quality") == "clean_settlement" and r.get("token_id")})
    summary = {
        "generated_at_utc": _iso(time.time()),
        "discovered_markets": len(markets),
        "markets_with_price_history": markets_with_history,
        "world_cup_markets_with_history": wc_with_history,
        "clean_token_resolutions": clean_tokens,
        "snapshot_rows": len(snapshots),
        "resolutions_file": str(out_root / "market_resolutions.csv"),
        "snapshots_file": str(out_root / "historical_price_snapshots.csv"),
    }
    write_json(cfg.governance_root / "resolved_history_pull_summary.json", summary)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
