"""
Stealth Oddspedia probability scraper — parallel, adaptive-wait, full-market.

Anti-detection:
  1. Real Chrome binary (channel="chrome") — correct TLS and HTTP/2 fingerprint.
  2. --disable-blink-features=AutomationControlled + navigator.webdriver patch.
  3. Persistent user-data-dir — cf_clearance cookies survive across runs.

Speed:
  - page.wait_for_function() replaces a fixed settle sleep: fast SSR pages are
    ready in 1-3 s, no waiting for the full --settle-ms unless data is absent.
  - --max-workers (default 3) concurrent tabs inside the same context share the
    same Cloudflare session, so cookies are reused and no extra challenges appear.

Data:
  - All Nuxt probability market IDs are extracted (not just 100 and 800).
  - Known markets are parsed: 1X2 (100), Correct Score (800), and any
    over/under or BTTS market automatically detected by structure.
  - Outputs:
      oddspedia_probability_grids_auto.csv   — unchanged (backward-compatible)
      oddspedia_probability_summary_auto.csv — unchanged (backward-compatible)
      oddspedia_all_markets_auto.csv         — raw: one row per market per match
      oddspedia_markets_summary_auto.csv     — wide: 1X2 + parsed extras per match

Install:
    pip install playwright playwright-stealth
    python -m playwright install chrome

Usage:
    python scripts/scrape_oddspedia_stealth.py
    python scripts/scrape_oddspedia_stealth.py --no-chrome-channel --max-workers 2
    python scripts/scrape_oddspedia_stealth.py --max-matches 3
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Browser init script — removes automation fingerprint signals
# ---------------------------------------------------------------------------
_WEBDRIVER_PATCH = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array; } catch(e) {}
try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise; } catch(e) {}
try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol; } catch(e) {}
"""

_CF_PHRASES = ("just a moment", "checking your browser", "enable javascript and cookies")

# ---------------------------------------------------------------------------
# JS injected into the page to extract Nuxt state
# ---------------------------------------------------------------------------
_WAIT_JS = r"""
() => {
  try {
    const e = window.__NUXT__?.state?.event || window.__NUXT__?.event
             || (window.$nuxt?.$store?.state?.event);
    return !!(e && e.probabilities);
  } catch(e) { return false; }
}
"""

_EXTRACT_JS = r"""
() => {
  function getEvent() {
    const srcs = [];
    try { srcs.push(['nuxt_state_event', window.__NUXT__?.state?.event]); } catch(e) {}
    try { srcs.push(['nuxt_event', window.__NUXT__?.event]); } catch(e) {}
    try { srcs.push(['store_state_event', window.$nuxt?.$store?.state?.event]); } catch(e) {}
    for (const [name, ev] of srcs) {
      if (ev?.probabilities) return { name, ev };
    }
    return null;
  }
  const found = getEvent();
  if (!found) {
    return {
      sourceName: '', probabilities: null, marketIds: [],
      hasCorrectScore: false, hasAnyProbs: false,
      currentUrl: window.location.href, title: document.title,
      bodyText: document.body ? document.body.innerText.slice(0, 1000) : ''
    };
  }
  const { name, ev } = found;
  const probs = ev.probabilities;
  return {
    sourceName: name,
    probabilities: probs,
    marketIds: Object.keys(probs || {}),
    hasCorrectScore: !!(probs?.['800']?.probabilities),
    hasAnyProbs: !!(probs && Object.keys(probs).length > 0),
    currentUrl: window.location.href,
    title: document.title,
    bodyText: document.body ? document.body.innerText.slice(0, 1000) : ''
  };
}
"""

_CLICK_JS = r"""
() => {
  const wanted = ['score forecast', 'match probabilities', 'probabilities', 'correct score'];
  const nodes = Array.from(document.querySelectorAll('a, button, [role="button"], [tabindex], div, span'));
  const clicked = [];
  for (const node of nodes) {
    const text = (node.innerText || node.textContent || '').toLowerCase().trim();
    if (!text || text.length > 80) continue;
    if (wanted.some(w => text.includes(w))) {
      try { node.scrollIntoView({block: 'center'}); node.click(); clicked.push(text); } catch(e) {}
    }
  }
  return clicked.slice(0, 10);
}
"""

# Clicks "Goals", "Over/Under", "BTTS" tabs to trigger deferred XHR market payloads
_CLICK_MARKET_TABS_JS = r"""
() => {
  const wanted = [
    'goals', 'over/under', 'over under', 'both teams to score', 'btts',
    'total goals', 'asian handicap', 'draw no bet', 'double chance'
  ];
  const selectors = 'a, button, [role="tab"], [role="button"], nav li, li, .tab, .tabs__item';
  const nodes = Array.from(document.querySelectorAll(selectors));
  const clicked = [];
  const seen = new Set();
  for (const node of nodes) {
    const text = (node.innerText || node.textContent || '').toLowerCase().trim();
    if (!text || text.length > 60 || seen.has(text)) continue;
    if (wanted.some(w => text.includes(w))) {
      seen.add(text);
      try { node.scrollIntoView({block: 'center'}); node.click(); clicked.push(text); } catch(e) {}
    }
  }
  return clicked.slice(0, 10);
}
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--urls-csv", default="inputs/oddspedia_match_urls.csv")
    p.add_argument("--out-csv", default="inputs/smartbet_grids/oddspedia_probability_grids_auto.csv")
    p.add_argument("--out-summary-csv", default="inputs/smartbet_grids/oddspedia_probability_summary_auto.csv")
    p.add_argument("--out-markets-csv", default="inputs/smartbet_grids/oddspedia_all_markets_auto.csv")
    p.add_argument("--out-markets-summary-csv", default="inputs/smartbet_grids/oddspedia_markets_summary_auto.csv")
    p.add_argument("--out-json", default="outputs/oddspedia_probability_extract/oddspedia_stealth_summary.json")
    p.add_argument("--diagnostics-dir", default="outputs/oddspedia_probability_extract/stealth_diagnostics")
    p.add_argument("--user-data-dir", default=".playwright-oddspedia-stealth-profile")
    p.add_argument("--seed-url", default="https://oddspedia.com/football/world/world-cup")
    p.add_argument("--max-workers", type=int, default=3, help="Concurrent tabs (default 3)")
    p.add_argument("--nuxt-wait-ms", type=int, default=12000, help="Max wait for Nuxt data to appear (ms)")
    p.add_argument("--post-click-ms", type=int, default=5000, help="Extra wait after clicking score tab (ms)")
    p.add_argument("--timeout-ms", type=int, default=90000)
    p.add_argument("--cf-wait-ms", type=int, default=30000, help="Max wait for Cloudflare auto-resolve (ms)")
    p.add_argument("--max-matches", type=int, default=0)
    p.add_argument("--market-tab-wait-ms", type=int, default=3000, help="Wait after clicking BTTS/OU market tabs (ms)")
    p.add_argument("--no-chrome-channel", action="store_true", help="Use bundled Chromium instead of installed Chrome")
    p.add_argument("--manual-on-missing", action="store_true")
    return p


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def txt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and math.isnan(v):
        return ""
    return str(v).strip()


def to_float(v: Any) -> float | None:
    try:
        return float(str(v).replace("%", "").strip())
    except Exception:
        return None


def slugify(v: Any) -> str:
    t = txt(v).lower().replace("&", " and ")
    t = re.sub(r"[^a-z0-9]+", "-", t)
    return t.strip("-")


def safe_name(v: Any) -> str:
    t = re.sub(r"[^A-Za-z0-9_.-]+", "_", txt(v)).strip("_")
    return t[:120] or "match"


def load_rows(path: Path, max_matches: int = 0) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing URL CSV: {path}")
    frame = pd.read_csv(path).fillna("")
    rows: list[dict[str, str]] = []
    for _, r in frame.iterrows():
        row = {k: txt(r.get(k)) for k in frame.columns}
        if not row.get("match_id"):
            row["match_id"] = f"{slugify(row.get('home_team'))}-{slugify(row.get('away_team'))}"
        if row.get("url") or row.get("alt_url"):
            rows.append(row)
    return rows[:max_matches] if max_matches and max_matches > 0 else rows


def is_cloudflare_challenge(body_text: str) -> bool:
    lower = body_text.lower()
    return any(phrase in lower for phrase in _CF_PHRASES)


# ---------------------------------------------------------------------------
# Market parsing
# ---------------------------------------------------------------------------

def score_parts(key: str) -> tuple[str, str, str]:
    if re.fullmatch(r"\d+-\d+", key):
        h, a = key.split("-", 1)
        return h, a, "exact"
    if key == "Other_1":
        return "Other", "Other", "other_home_win"
    if key == "Other_X":
        return "Other", "Other", "other_draw"
    if key == "Other_2":
        return "Other", "Other", "other_away_win"
    return "", "", "unknown"


def parse_correct_score(
    row: dict[str, str], market: Any, url_type: str, url: str, current_url: str, source_name: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parse market 800 (correct score) into grid rows + summary."""
    winner_home = to_float(row.get("_winner_home"))
    winner_draw = to_float(row.get("_winner_draw"))
    winner_away = to_float(row.get("_winner_away"))

    score_probs: dict = {}
    odds_value = odds_bookie = odds_handicap = ""
    if isinstance(market, dict):
        raw = market.get("probabilities")
        if isinstance(raw, dict):
            score_probs = raw
        odds = market.get("odds")
        if isinstance(odds, dict):
            odds_value = txt(odds.get("value"))
            odds_bookie = txt(odds.get("bookie"))
            odds_handicap = txt(odds.get("handicap_name"))

    grid_rows: list[dict[str, Any]] = []
    for score_key, pct in score_probs.items():
        h, a, bucket = score_parts(txt(score_key))
        grid_rows.append({
            "match_id": row.get("match_id", ""),
            "commence_time": row.get("commence_time", ""),
            "home_team": row.get("home_team", ""),
            "away_team": row.get("away_team", ""),
            "score_key": txt(score_key),
            "home_goals": h,
            "away_goals": a,
            "score_bucket": bucket,
            "probability_pct": to_float(pct),
            "winner_home_pct": winner_home,
            "winner_draw_pct": winner_draw,
            "winner_away_pct": winner_away,
            "best_odds_value": odds_value,
            "best_odds_bookie": odds_bookie,
            "best_odds_scoreline": odds_handicap,
            "source_url_type": url_type,
            "source_url": url,
            "current_url": current_url,
            "source_name": source_name,
        })

    exact_rows = [r for r in grid_rows if r["score_bucket"] == "exact" and r["probability_pct"] is not None]
    modal_score, modal_pct = "", None
    if exact_rows:
        best = max(exact_rows, key=lambda r: float(r["probability_pct"]))
        modal_score, modal_pct = txt(best["score_key"]), best["probability_pct"]

    summary = {
        "match_id": row.get("match_id", ""),
        "commence_time": row.get("commence_time", ""),
        "home_team": row.get("home_team", ""),
        "away_team": row.get("away_team", ""),
        "winner_home_pct": winner_home,
        "winner_draw_pct": winner_draw,
        "winner_away_pct": winner_away,
        "correct_score_count": len(grid_rows),
        "modal_correct_score": modal_score,
        "modal_correct_score_pct": modal_pct,
        "best_odds_value": odds_value,
        "best_odds_bookie": odds_bookie,
        "best_odds_scoreline": odds_handicap,
        "source_url_type": url_type,
        "source_url": url,
        "current_url": current_url,
        "source_name": source_name,
    }
    return grid_rows, summary


def _market_structure(market: Any) -> str:
    if isinstance(market, list):
        return "list"
    if isinstance(market, dict):
        if "probabilities" in market:
            return "dict_with_probs"
        return "dict"
    return type(market).__name__


def _extract_ou_btts_from_payload(payload: Any) -> dict[str, Any]:
    """
    Attempt to pull over/under and BTTS percentages from a raw XHR payload.
    Returns a flat dict of field_name -> value for anything we can identify.
    """
    extras: dict[str, Any] = {}
    if not isinstance(payload, dict):
        return extras

    blob = json.dumps(payload).lower()

    # Walk recursively looking for over/under line structures
    def walk(obj: Any, path: str = "") -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                k_lower = str(k).lower()
                walk(v, f"{path}.{k}" if path else k)
                # Look for numeric probability pairs keyed by line value
                if re.match(r"^\d+(\.\d+)?$", str(k)):
                    val = to_float(v)
                    if val is not None:
                        col = f"ou_line_{str(k).replace('.', '_')}"
                        if col not in extras:
                            extras[col] = val
                # BTTS signals
                if k_lower in ("yes", "btts_yes", "both_teams_score"):
                    val = to_float(v)
                    if val is not None and "p_btts_yes" not in extras:
                        extras["p_btts_yes"] = val
                if k_lower in ("no", "btts_no"):
                    val = to_float(v)
                    if val is not None and "p_btts_no" not in extras:
                        extras["p_btts_no"] = val
        elif isinstance(obj, list):
            for item in obj:
                walk(item, path)

    walk(payload)
    return extras


def parse_all_markets(
    row: dict[str, str],
    probabilities: dict[str, Any],
    url: str,
    current_url: str,
    source_name: str,
    xhr_markets: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Extract every market ID from the Nuxt probabilities object, plus any extras
    captured via XHR network interception.

    Returns:
      market_rows  — one row per source (for oddspedia_all_markets_auto.csv)
      market_summary — wide dict with parsed 1X2 + inferred extras (for oddspedia_markets_summary_auto.csv)
    """
    ident = {
        "match_id": row.get("match_id", ""),
        "commence_time": row.get("commence_time", ""),
        "home_team": row.get("home_team", ""),
        "away_team": row.get("away_team", ""),
    }

    market_rows: list[dict[str, Any]] = []
    for market_id, market in probabilities.items():
        structure = _market_structure(market)
        try:
            data_json = json.dumps(market, default=str)
        except Exception:
            data_json = str(market)
        market_rows.append({
            **ident,
            "source_type": "nuxt",
            "market_id": txt(market_id),
            "market_structure": structure,
            "data_json": data_json,
            "source_url": url,
            "current_url": current_url,
            "source_name": source_name,
        })

    # XHR payloads captured during page load
    for xhr_url, xhr_payload in (xhr_markets or {}).items():
        try:
            data_json = json.dumps(xhr_payload, default=str)
        except Exception:
            data_json = str(xhr_payload)
        market_rows.append({
            **ident,
            "source_type": "xhr",
            "market_id": "xhr",
            "market_structure": _market_structure(xhr_payload),
            "data_json": data_json,
            "source_url": xhr_url,
            "current_url": current_url,
            "source_name": source_name,
        })

    # --- Parse known / inferred markets ---
    summary: dict[str, Any] = dict(ident)
    summary.update({"source_url": url, "current_url": current_url, "source_name": source_name})

    # Market 100: 1X2 — list [home_pct, draw_pct, away_pct]
    m100 = probabilities.get("100")
    if isinstance(m100, list) and len(m100) >= 3:
        summary["p_home_win"] = to_float(m100[0])
        summary["p_draw"] = to_float(m100[1])
        summary["p_away_win"] = to_float(m100[2])
    else:
        summary["p_home_win"] = summary["p_draw"] = summary["p_away_win"] = None

    # Market 800: correct score — already handled separately, record presence
    m800 = probabilities.get("800")
    summary["has_correct_score"] = isinstance(m800, dict) and bool(m800.get("probabilities"))

    summary["available_market_ids"] = ";".join(sorted(probabilities.keys()))
    summary["total_markets"] = len(probabilities)
    summary["xhr_payloads_captured"] = len(xhr_markets or {})

    # Scan Nuxt markets for over/under or binary markets not in known list
    for mid, market in probabilities.items():
        if mid in ("100", "800"):
            continue
        if isinstance(market, list) and len(market) == 2:
            pct_a, pct_b = to_float(market[0]), to_float(market[1])
            if pct_a is not None and pct_b is not None:
                key_a, key_b = f"market_{mid}_a", f"market_{mid}_b"
                if key_a not in summary:
                    summary[key_a] = pct_a
                    summary[key_b] = pct_b
        elif isinstance(market, dict) and isinstance(market.get("probabilities"), dict):
            inner: dict = market["probabilities"]
            ou_keys = [k for k in inner if re.match(r"^\d+(\.\d+)?$", str(k))]
            if ou_keys:
                col_prefix = f"market_{mid}"
                for k in sorted(ou_keys):
                    col = f"{col_prefix}_line_{k.replace('.', '_')}"
                    if col not in summary:
                        summary[col] = to_float(inner[k])

    # Merge anything found in XHR payloads
    for xhr_payload in (xhr_markets or {}).values():
        for k, v in _extract_ou_btts_from_payload(xhr_payload).items():
            if k not in summary:
                summary[k] = v

    return market_rows, summary


# ---------------------------------------------------------------------------
# Cloudflare helpers
# ---------------------------------------------------------------------------

async def wait_for_cf_resolve(page: Any, cf_wait_ms: int) -> bool:
    poll_ms = 3000
    elapsed = 0
    while elapsed < cf_wait_ms:
        await page.wait_for_timeout(poll_ms)
        elapsed += poll_ms
        body = await page.evaluate("() => document.body?.innerText?.slice(0, 500) || ''")
        if not is_cloudflare_challenge(body):
            print(f"  Cloudflare resolved after {elapsed} ms.")
            return True
    return False


# ---------------------------------------------------------------------------
# Per-match scraping coroutine
# ---------------------------------------------------------------------------

MatchResult = tuple[
    list[dict[str, Any]],   # grid_rows
    dict[str, Any] | None,  # summary_row
    list[dict[str, Any]],   # market_rows
    dict[str, Any] | None,  # market_summary_row
    dict[str, Any] | None,  # diagnostic
]


_MARKET_KEYWORDS = {"probability", "probabilities", "score", "correct", "btts", "over", "under", "total"}

def _looks_like_market_json(data: Any) -> bool:
    try:
        blob = json.dumps(data).lower()
    except Exception:
        return False
    return any(kw in blob for kw in _MARKET_KEYWORDS) and (
        re.search(r'"\d+-\d+"', blob) is not None
        or '"btts"' in blob
        or '"over"' in blob
        or '"under"' in blob
    )


async def scrape_match(
    sem: asyncio.Semaphore,
    context: Any,
    idx: int,
    total: int,
    row: dict[str, str],
    args: argparse.Namespace,
    diag_dir: Path,
) -> MatchResult:
    async with sem:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError

        page = await context.new_page()

        # Capture XHR/fetch responses that look like market data
        _xhr_markets: dict[str, Any] = {}

        async def _on_response(response: Any) -> None:
            try:
                ct = (response.headers.get("content-type") or "").lower()
                if "json" not in ct:
                    return
                data = await response.json()
                if _looks_like_market_json(data):
                    _xhr_markets[response.url] = data
            except Exception:
                pass

        page.on("response", _on_response)

        try:
            candidates = []
            if row.get("url"):
                candidates.append(("url", row["url"]))
            if row.get("alt_url") and row.get("alt_url") != row.get("url"):
                candidates.append(("alt_url", row["alt_url"]))

            last_state: dict[str, Any] | None = None

            for url_type, url in candidates:
                label = f"[{idx}/{total}] {row.get('home_team')} vs {row.get('away_team')}"
                print(f"{label} :: {url_type}")
                _xhr_markets.clear()

                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=args.timeout_ms)
                except PlaywrightTimeoutError:
                    print(f"  {label}: domcontentloaded timeout; reading state")

                # Adaptive wait: wait until Nuxt probabilities appear or timeout
                try:
                    await page.wait_for_function(_WAIT_JS, timeout=args.nuxt_wait_ms)
                except PlaywrightTimeoutError:
                    pass  # fall through to extract whatever is there

                # Mid-session Cloudflare check
                body = await page.evaluate("() => document.body?.innerText?.slice(0, 500) || ''")
                if is_cloudflare_challenge(body):
                    print(f"  {label}: Cloudflare challenge mid-session. Waiting for auto-resolve ...")
                    resolved = await wait_for_cf_resolve(page, args.cf_wait_ms)
                    if not resolved:
                        print(f"  {label}: Auto-resolve failed. Complete challenge in browser.")
                        input("  Press ENTER when done: ")
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=args.timeout_ms)
                        await page.wait_for_function(_WAIT_JS, timeout=args.nuxt_wait_ms)
                    except PlaywrightTimeoutError:
                        pass

                state = await page.evaluate(_EXTRACT_JS)
                if not state.get("hasCorrectScore"):
                    clicked = await page.evaluate(_CLICK_JS)
                    if clicked:
                        print(f"  {label}: clicked {clicked[:3]}")
                    await page.wait_for_timeout(args.post_click_ms)
                    state = await page.evaluate(_EXTRACT_JS)

                last_state = state
                probs: dict = state.get("probabilities") or {}

                if state.get("hasCorrectScore") or state.get("hasAnyProbs"):
                    current_url = txt(state.get("currentUrl"))
                    source_name = txt(state.get("sourceName"))

                    # Phase 2: click market tabs (Goals, BTTS, Over/Under) to trigger deferred XHR payloads
                    xhr_count_before = len(_xhr_markets)
                    clicked_tabs = await page.evaluate(_CLICK_MARKET_TABS_JS)
                    if clicked_tabs:
                        print(f"  {label}: market tabs clicked: {clicked_tabs[:3]}")
                        await page.wait_for_timeout(args.market_tab_wait_ms)
                        n_new_xhr = len(_xhr_markets) - xhr_count_before
                        if n_new_xhr:
                            print(f"  {label}: +{n_new_xhr} XHR payloads from market tabs")

                    # Inject winner probs for correct-score parser
                    m100 = probs.get("100")
                    row["_winner_home"] = str(to_float(m100[0]) or "") if isinstance(m100, list) and len(m100) > 0 else ""
                    row["_winner_draw"] = str(to_float(m100[1]) or "") if isinstance(m100, list) and len(m100) > 1 else ""
                    row["_winner_away"] = str(to_float(m100[2]) or "") if isinstance(m100, list) and len(m100) > 2 else ""

                    grid_rows, summary_row = parse_correct_score(
                        row, probs.get("800"), url_type, url, current_url, source_name
                    )
                    market_rows, market_summary = parse_all_markets(
                        row, probs, url, current_url, source_name, _xhr_markets
                    )

                    n = len(grid_rows)
                    modal = summary_row.get("modal_correct_score", "")
                    modal_pct = summary_row.get("modal_correct_score_pct", "")
                    n_markets = market_summary.get("total_markets", 0)
                    n_xhr = len(_xhr_markets)
                    print(f"  {label}: OK  {n} score rows  modal={modal} {modal_pct}  markets={n_markets}  xhr={n_xhr}")
                    return grid_rows, summary_row, market_rows, market_summary, None

                print(f"  {label}: no probabilities found on {url_type}")

            # All candidates exhausted — write diagnostic
            name = safe_name(row.get("match_id"))
            body_file = diag_dir / f"{name}_body.txt"
            state_file = diag_dir / f"{name}_state.json"
            body_file.write_text(txt((last_state or {}).get("bodyText")), encoding="utf-8")
            state_file.write_text(
                json.dumps({"row": row, "last_state": last_state}, indent=2, default=str), encoding="utf-8"
            )
            print(f"  [{idx}/{total}] MISS: {row.get('home_team')} vs {row.get('away_team')} — no data")
            empty_summary = {
                "match_id": row.get("match_id", ""),
                "commence_time": row.get("commence_time", ""),
                "home_team": row.get("home_team", ""),
                "away_team": row.get("away_team", ""),
                "winner_home_pct": None, "winner_draw_pct": None, "winner_away_pct": None,
                "correct_score_count": 0, "modal_correct_score": "", "modal_correct_score_pct": None,
                "best_odds_value": "", "best_odds_bookie": "", "best_odds_scoreline": "",
                "source_url_type": "", "source_url": "",
                "current_url": txt((last_state or {}).get("currentUrl")), "source_name": "",
            }
            diag = {
                "match_id": row.get("match_id", ""),
                "home_team": row.get("home_team", ""),
                "away_team": row.get("away_team", ""),
                "body_file": str(body_file),
                "state_file": str(state_file),
            }
            return [], empty_summary, [], None, diag
        finally:
            await page.close()


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def scrape(
    args: argparse.Namespace, rows: list[dict[str, str]]
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except Exception as exc:
        raise RuntimeError("Install: pip install playwright && python -m playwright install chrome") from exc

    _stealth_obj = None
    try:
        from playwright_stealth import Stealth  # type: ignore[import]
        _stealth_obj = Stealth()
        print("playwright-stealth 2.x: patches will be applied to context.")
    except ImportError:
        try:
            from playwright_stealth import stealth_async as _stealth_obj  # type: ignore[import]
            print("playwright-stealth 1.x loaded.")
        except ImportError:
            print("playwright-stealth not installed; using built-in patches only.")

    diag_dir = Path(args.diagnostics_dir)
    diag_dir.mkdir(parents=True, exist_ok=True)

    launch_kwargs: dict[str, Any] = {
        "headless": False,
        "locale": "en-GB",
        "viewport": {"width": 1440, "height": 900},
        "ignore_https_errors": True,
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if not args.no_chrome_channel:
        launch_kwargs["channel"] = "chrome"

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(args.user_data_dir, **launch_kwargs)
        await context.add_init_script(_WEBDRIVER_PATCH)

        if _stealth_obj is not None:
            try:
                await _stealth_obj.apply_stealth_async(context)
                print("playwright-stealth applied to context.")
            except AttributeError:
                pass

        # Warm up: load seed page so Cloudflare session is established before parallel tabs open
        seed_page = context.pages[0] if context.pages else await context.new_page()
        print(f"Warming up: {args.seed_url}")
        try:
            await seed_page.goto(args.seed_url, wait_until="domcontentloaded", timeout=args.timeout_ms)
        except Exception:
            pass
        await seed_page.wait_for_timeout(2000)

        body = await seed_page.evaluate("() => document.body?.innerText?.slice(0, 500) || ''")
        if is_cloudflare_challenge(body):
            print("Cloudflare challenge on seed page. Waiting for auto-resolve ...")
            resolved = await wait_for_cf_resolve(seed_page, args.cf_wait_ms)
            if not resolved:
                print("Please complete the Cloudflare challenge in the browser window.")
                input("Press ENTER when Oddspedia is accessible ...")
        else:
            print("  Seed loaded (no challenge).")
        # Park the seed page (don't close — closing the last page shuts the context)
        await seed_page.goto("about:blank")

        # Parallel scraping
        sem = asyncio.Semaphore(args.max_workers)
        tasks = [
            scrape_match(sem, context, idx + 1, len(rows), row, args, diag_dir)
            for idx, row in enumerate(rows)
        ]
        results = await asyncio.gather(*tasks)
        await context.close()

    # Flatten results (order is preserved by asyncio.gather)
    all_grid: list[dict[str, Any]] = []
    all_summary: list[dict[str, Any]] = []
    all_markets: list[dict[str, Any]] = []
    all_markets_summary: list[dict[str, Any]] = []
    all_diagnostics: list[dict[str, Any]] = []
    for grid_rows, summary_row, market_rows, market_summary, diag in results:
        all_grid.extend(grid_rows)
        if summary_row:
            all_summary.append(summary_row)
        all_markets.extend(market_rows)
        if market_summary:
            all_markets_summary.append(market_summary)
        if diag:
            all_diagnostics.append(diag)

    return all_grid, all_summary, all_markets, all_markets_summary, all_diagnostics


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    args = build_parser().parse_args()
    rows = load_rows(Path(args.urls_csv), args.max_matches)
    if not rows:
        raise ValueError(f"No URL rows found in {args.urls_csv}")

    t0 = datetime.now(timezone.utc)
    grid_rows, summaries, market_rows, market_summaries, diagnostics = asyncio.run(scrape(args, rows))
    elapsed = (datetime.now(timezone.utc) - t0).total_seconds()

    for path_arg in [args.out_csv, args.out_summary_csv, args.out_markets_csv, args.out_markets_summary_csv, args.out_json]:
        Path(path_arg).parent.mkdir(parents=True, exist_ok=True)

    pd.DataFrame(grid_rows).to_csv(args.out_csv, index=False)
    pd.DataFrame(summaries).to_csv(args.out_summary_csv, index=False)
    pd.DataFrame(market_rows).to_csv(args.out_markets_csv, index=False)
    pd.DataFrame(market_summaries).to_csv(args.out_markets_summary_csv, index=False)

    payload = {
        "generated_at_utc": t0.isoformat(),
        "elapsed_seconds": round(elapsed, 1),
        "seconds_per_match": round(elapsed / len(rows), 1) if rows else 0,
        "input_match_count": len(rows),
        "max_workers": args.max_workers,
        "matches_with_correct_score": sum(1 for s in summaries if int(s.get("correct_score_count") or 0) > 0),
        "matches_with_any_data": sum(1 for s in market_summaries if s),
        "correct_score_row_count": len(grid_rows),
        "total_market_rows": len(market_rows),
        "diagnostic_count": len(diagnostics),
        "diagnostics": diagnostics,
        "out_csv": args.out_csv,
        "out_summary_csv": args.out_summary_csv,
        "out_markets_csv": args.out_markets_csv,
        "out_markets_summary_csv": args.out_markets_summary_csv,
    }
    Path(args.out_json).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(json.dumps(payload, indent=2, default=str))
    print(f"\nWrote {args.out_csv}  ({len(grid_rows)} rows)")
    print(f"Wrote {args.out_summary_csv}  ({len(summaries)} rows)")
    print(f"Wrote {args.out_markets_csv}  ({len(market_rows)} rows)")
    print(f"Wrote {args.out_markets_summary_csv}  ({len(market_summaries)} rows)")
    print(f"Time: {elapsed:.1f}s  ({payload['seconds_per_match']}s/match with {args.max_workers} workers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
