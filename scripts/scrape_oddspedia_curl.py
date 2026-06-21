"""
scrape_oddspedia_curl.py

HTTP-only replacement for scrape_oddspedia_stealth.py.  Uses curl_cffi to
impersonate Chrome's TLS fingerprint so Cloudflare lets requests through
without needing a real browser.  Evaluates the window.__NUXT__ SSR payload
with a Node.js subprocess to extract probabilities, then calls the
getMatchMaxOddsByGroup API for de-vigged BTTS and OU probabilities.

Requirements:
    pip install curl_cffi pandas
    node >= 16
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from curl_cffi import requests as cffi_requests


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Scrape Oddspedia via curl_cffi (no browser required).")
    p.add_argument("--urls-csv", default="inputs/oddspedia_match_urls.csv")
    p.add_argument("--out-csv", default="inputs/smartbet_grids/oddspedia_probability_grids_auto.csv")
    p.add_argument("--out-summary-csv", default="inputs/smartbet_grids/oddspedia_probability_summary_auto.csv")
    p.add_argument("--out-markets-csv", default="inputs/smartbet_grids/oddspedia_all_markets_auto.csv")
    p.add_argument("--out-markets-summary-csv", default="inputs/smartbet_grids/oddspedia_markets_summary_auto.csv")
    p.add_argument("--max-workers", type=int, default=4, help="Concurrent HTTP workers")
    p.add_argument("--max-matches", type=int, default=0, help="0 = all")
    p.add_argument("--impersonate", default="chrome124", help="curl_cffi impersonate target")
    p.add_argument("--timeout", type=int, default=20, help="Request timeout in seconds")
    return p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def txt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and math.isnan(v):
        return ""
    return str(v).strip()


def to_float(v: Any) -> float | None:
    try:
        s = txt(v).replace("%", "")
        return float(s) if s else None
    except Exception:
        return None


def slugify(v: Any) -> str:
    t = txt(v).lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


def match_key(home: Any, away: Any) -> str:
    return f"{slugify(home)}-{slugify(away)}"


def devig_two(o1: float | None, o2: float | None) -> tuple[float | None, float | None]:
    if o1 is None or o2 is None or o1 <= 0 or o2 <= 0:
        return None, None
    p1, p2 = 1 / o1, 1 / o2
    total = p1 + p2
    if total <= 0:
        return None, None
    return round(p1 / total * 100, 2), round(p2 / total * 100, 2)


# ---------------------------------------------------------------------------
# Nuxt state extraction via Node.js
# ---------------------------------------------------------------------------

_NODE_EXTRACT = """
const window = {};
try { %s } catch(e) {}
const st = window.__NUXT__?.state || null;
const p100 = st?.event?.probabilities?.['100'] || null;
const p800 = st?.event?.probabilities?.['800'] || null;
const probs = p800?.probabilities || {};
const best = p800?.odds || null;
const eventId = st?.event?.event?.id || null;
console.log(JSON.stringify({
    eventId,
    p100,
    probs,
    bestVal: best?.value || null,
    bestBookie: best?.bookie || null,
    bestScore: best?.handicap_name || null,
}));
"""


def eval_nuxt(nuxt_js: str) -> dict[str, Any] | None:
    code = _NODE_EXTRACT % nuxt_js
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as f:
            f.write(code)
            fname = f.name
        result = subprocess.run(["node", fname], capture_output=True, text=True, timeout=15)
        Path(fname).unlink(missing_ok=True)
        if result.returncode != 0 or not result.stdout.strip():
            return None
        return json.loads(result.stdout.strip())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Odds API parsing (BTTS + OU)
# ---------------------------------------------------------------------------

def _odds_val(entry: dict, key: str) -> float | None:
    return to_float((entry.get(key) or {}).get("odds_value"))


def parse_btts(data: dict | None) -> dict[str, Any]:
    if not data:
        return {}
    market = (data.get("odds") or {}).get("1100") or {}
    odds = market.get("odds") or {}
    p_yes, p_no = devig_two(_odds_val(odds, "o1"), _odds_val(odds, "o2"))
    result: dict[str, Any] = {}
    if p_yes is not None:
        result["p_btts_yes"] = p_yes
        result["p_btts_no"] = p_no
    return result


def parse_ou(data: dict | None) -> dict[str, Any]:
    if not data:
        return {}
    market = (data.get("odds") or {}).get("401") or {}
    lines: list[dict] = []
    if market.get("main"):
        lines.append(market["main"])
    if isinstance(market.get("alternative"), list):
        lines.extend(market["alternative"])
    result: dict[str, Any] = {}
    for entry in lines:
        name = txt(entry.get("name_en") or entry.get("name"))
        if not re.fullmatch(r"\d+(\.\d+)?", name):
            continue
        line_val = to_float(name)
        if line_val is None or line_val > 6.5:
            continue
        odds = entry.get("odds") or {}
        p_over, p_under = devig_two(_odds_val(odds, "o1"), _odds_val(odds, "o2"))
        if p_over is not None:
            key = name.replace(".", "_")
            result[f"p_over_{key}"] = p_over
            result[f"p_under_{key}"] = p_under
    return result


# ---------------------------------------------------------------------------
# Per-match scrape
# ---------------------------------------------------------------------------

_OTHER_MAP = {
    "Other_1": ("other_home_win", None, None),
    "Other_X": ("other_draw", None, None),
    "Other_2": ("other_away_win", None, None),
}


def scrape_match(row: dict[str, Any], impersonate: str, timeout: int) -> dict[str, Any]:
    mid = txt(row.get("match_id")) or match_key(row.get("home_team"), row.get("away_team"))
    home = txt(row.get("home_team"))
    away = txt(row.get("away_team"))
    commence = txt(row.get("commence_time"))
    urls = [txt(row.get("url")), txt(row.get("alt_url"))]

    session = cffi_requests.Session()
    headers = {"accept-language": "en-GB,en;q=0.9"}

    nuxt_data: dict[str, Any] | None = None
    used_url = ""
    url_type = ""

    for ui, url in enumerate(urls):
        if not url:
            continue
        try:
            r = session.get(url, impersonate=impersonate, headers=headers, timeout=timeout)
            html = r.text
            if "Just a moment" in html or "cf-challenge" in html:
                continue
            idx = html.find("window.__NUXT__")
            if idx == -1:
                continue
            end = html.find("</script>", idx)
            nuxt_js = html[idx:end].strip() if end != -1 else html[idx:idx + 500000]
            nuxt_data = eval_nuxt(nuxt_js)
            if nuxt_data and nuxt_data.get("probs"):
                used_url = url
                url_type = "url" if ui == 0 else "alt_url"
                break
        except Exception:
            continue

    if not nuxt_data or not nuxt_data.get("probs"):
        return {
            "match_id": mid, "home": home, "away": away, "commence": commence,
            "grid_rows": [], "summary": None, "market_rows": [],
            "market_summary": None, "miss": True,
        }

    probs: dict[str, float] = nuxt_data["probs"]
    p100 = nuxt_data.get("p100") or []
    event_id = nuxt_data.get("eventId")
    best_val = txt(nuxt_data.get("bestVal"))
    best_bookie = txt(nuxt_data.get("bestBookie"))
    best_score = txt(nuxt_data.get("bestScore"))
    winner_home = float(p100[0]) if len(p100) > 0 else None
    winner_draw = float(p100[1]) if len(p100) > 1 else None
    winner_away = float(p100[2]) if len(p100) > 2 else None

    # Fetch BTTS and OU odds via API
    btts_probs: dict[str, Any] = {}
    ou_probs: dict[str, Any] = {}
    if event_id:
        api_base = f"https://oddspedia.com/api/v1/getMatchMaxOddsByGroup?matchId={event_id}&inplay=0&geoCode=US&geoState=&language=en"
        ref_headers = {**headers, "referer": used_url}
        for gid, parser, target in [
            (11, parse_btts, "btts_probs"),
            (4, parse_ou, "ou_probs"),
        ]:
            try:
                resp = session.get(f"{api_base}&marketGroupId={gid}", impersonate=impersonate,
                                   headers=ref_headers, timeout=timeout)
                api_data = resp.json().get("data") if resp.status_code == 200 else None
                parsed = parser(api_data)
                if target == "btts_probs":
                    btts_probs = parsed
                else:
                    ou_probs = parsed
            except Exception:
                pass

    # Build grid rows (one per score)
    common = {
        "match_id": mid, "commence_time": commence, "home_team": home, "away_team": away,
        "winner_home_pct": winner_home, "winner_draw_pct": winner_draw, "winner_away_pct": winner_away,
        "best_odds_value": best_val, "best_odds_bookie": best_bookie, "best_odds_scoreline": best_score,
        "source_url_type": url_type, "source_url": used_url, "current_url": used_url,
        "source_name": "nuxt_state_event",
    }
    grid_rows = []
    for score_key, pct in probs.items():
        if score_key in _OTHER_MAP:
            bucket, hg, ag = _OTHER_MAP[score_key]
        else:
            m = re.fullmatch(r"(\d+)-(\d+)", score_key)
            if m:
                hg, ag = int(m.group(1)), int(m.group(2))
                bucket = "exact"
            else:
                hg, ag, bucket = None, None, "unknown"
        grid_rows.append({
            **common,
            "score_key": score_key,
            "home_goals": hg if hg is not None else "",
            "away_goals": ag if ag is not None else "",
            "score_bucket": bucket,
            "probability_pct": pct,
        })

    # Compute modal score
    exact_rows = [(k, v) for k, v in probs.items() if re.fullmatch(r"\d+-\d+", k)]
    modal_score, modal_pct = max(exact_rows, key=lambda x: x[1]) if exact_rows else ("", "")

    summary = {
        **common,
        "correct_score_count": len(exact_rows),
        "modal_correct_score": modal_score,
        "modal_correct_score_pct": modal_pct,
    }

    # Markets summary
    market_summary: dict[str, Any] = {
        "match_id": mid, "commence_time": commence, "home_team": home, "away_team": away,
        "source_url": used_url, "current_url": used_url, "source_name": "nuxt_state_event",
        "p_home_win": winner_home, "p_draw": winner_draw, "p_away_win": winner_away,
        "has_correct_score": bool(exact_rows),
        "available_market_ids": "100,800", "total_markets": 2,
        "xhr_payloads_captured": 0,
        **btts_probs,
        **ou_probs,
    }
    if event_id:
        market_summary["event_id"] = event_id

    return {
        "match_id": mid, "home": home, "away": away, "commence": commence,
        "grid_rows": grid_rows, "summary": summary, "market_rows": [],
        "market_summary": market_summary, "miss": False,
        "modal": modal_score, "modal_pct": modal_pct,
        "event_id": event_id,
        "btts_yes": btts_probs.get("p_btts_yes"), "ou25": ou_probs.get("p_over_2_5"),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args = build_parser().parse_args()
    urls_path = Path(args.urls_csv)
    if not urls_path.exists():
        print(f"ERROR: {urls_path} not found.")
        return 1

    rows_df = pd.read_csv(urls_path).fillna("")
    rows = rows_df.to_dict(orient="records")
    if args.max_matches:
        rows = rows[:args.max_matches]

    t0 = time.time()
    n = len(rows)
    grid_rows_all: list[dict] = []
    summaries: list[dict] = []
    market_summaries: list[dict] = []
    misses = 0

    results: dict[int, dict] = {}

    with ThreadPoolExecutor(max_workers=args.max_workers) as pool:
        futures = {pool.submit(scrape_match, row, args.impersonate, args.timeout): i
                   for i, row in enumerate(rows)}
        for fut in as_completed(futures):
            i = futures[fut]
            row = rows[i]
            label = f"[{i+1}/{n}] {txt(row.get('home_team'))} vs {txt(row.get('away_team'))}"
            try:
                res = fut.result()
                results[i] = res
                if res["miss"]:
                    print(f"  {label}: MISS — no data")
                    misses += 1
                else:
                    grid_rows_all.extend(res["grid_rows"])
                    summaries.append(res["summary"])
                    market_summaries.append(res["market_summary"])
                    print(f"  {label}: OK  {len(res['grid_rows'])} score rows  "
                          f"modal={res['modal']} ({res['modal_pct']}%)  "
                          f"btts_yes={res['btts_yes']}  ou25_over={res['ou25']}")
            except Exception as exc:
                print(f"  {label}: ERROR — {exc}")
                misses += 1

    elapsed = round(time.time() - t0, 1)

    # Sort grid rows by match order then score_key
    order = {txt(rows[i].get("match_id")) or match_key(rows[i].get("home_team"), rows[i].get("away_team")): i
             for i in range(len(rows))}
    grid_rows_all.sort(key=lambda r: (order.get(r["match_id"], 9999), r["score_key"]))

    grid_df = pd.DataFrame(grid_rows_all)
    summary_df = pd.DataFrame(summaries)
    markets_df = pd.DataFrame(market_summaries)

    for path_str, df in [
        (args.out_csv, grid_df),
        (args.out_summary_csv, summary_df),
        (args.out_markets_csv, pd.DataFrame()),  # no per-row market data in curl mode
        (args.out_markets_summary_csv, markets_df),
    ]:
        p = Path(path_str)
        p.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(p, index=False)
        print(f"Wrote {p}  ({len(df)} rows)")

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": elapsed,
        "seconds_per_match": round(elapsed / n, 1) if n else 0,
        "input_match_count": n,
        "max_workers": args.max_workers,
        "matches_with_correct_score": len(summaries),
        "miss_count": misses,
        "correct_score_row_count": len(grid_rows_all),
    }
    print(f"\nTime: {elapsed}s  ({payload['seconds_per_match']}s/match with {args.max_workers} workers)")
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
