"""
Stealth Oddspedia probability scraper.

Combines three things that together get past Cloudflare Bot Management
without a manual challenge-solve step in most sessions:

  1. Real Chrome binary (channel="chrome") — correct TLS and HTTP/2 fingerprint.
  2. --disable-blink-features=AutomationControlled + navigator.webdriver patch —
     removes the most obvious automation signal.
  3. Persistent user-data-dir — Cloudflare cf_clearance cookies survive across
     runs, so the challenge is only ever solved on the very first launch (if at all).

If a challenge page is still detected, the script waits up to --cf-wait-ms for it
to auto-resolve (Cloudflare's JS challenge resolves itself within ~5 s on real
Chrome), then falls back to a single manual input() only as a last resort.

Install (in addition to the base project install):
    python -m playwright install chrome

Usage:
    python scripts/scrape_oddspedia_stealth.py
    python scripts/scrape_oddspedia_stealth.py --no-chrome-channel   # bundled Chromium
    python scripts/scrape_oddspedia_stealth.py --max-matches 3       # quick test
"""
from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

_WEBDRIVER_PATCH = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array; } catch(e) {}
try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise; } catch(e) {}
try { delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol; } catch(e) {}
"""

_CF_PHRASES = ("just a moment", "checking your browser", "enable javascript and cookies")

_EXTRACT_JS = r"""
() => {
  const candidates = [];
  try { candidates.push(['nuxt_state_event', window.__NUXT__?.state?.event]); } catch(e) {}
  try { candidates.push(['nuxt_event', window.__NUXT__?.event]); } catch(e) {}
  try { candidates.push(['store_state_event', window.$nuxt?.$store?.state?.event]); } catch(e) {}
  for (const [name, event] of candidates) {
    if (event?.probabilities?.['800']?.probabilities) {
      return {
        sourceName: name, probabilities: event.probabilities, hasCorrectScore: true,
        currentUrl: window.location.href, title: document.title,
        bodyText: document.body ? document.body.innerText.slice(0, 1000) : ''
      };
    }
  }
  for (const [name, event] of candidates) {
    if (event?.probabilities) {
      return {
        sourceName: name, probabilities: event.probabilities, hasCorrectScore: false,
        currentUrl: window.location.href, title: document.title,
        bodyText: document.body ? document.body.innerText.slice(0, 1000) : ''
      };
    }
  }
  return {
    sourceName: '', probabilities: null, hasCorrectScore: false,
    currentUrl: window.location.href, title: document.title,
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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--urls-csv", default="inputs/oddspedia_match_urls.csv")
    p.add_argument("--out-csv", default="inputs/smartbet_grids/oddspedia_probability_grids_auto.csv")
    p.add_argument("--out-summary-csv", default="inputs/smartbet_grids/oddspedia_probability_summary_auto.csv")
    p.add_argument("--out-json", default="outputs/oddspedia_probability_extract/oddspedia_stealth_summary.json")
    p.add_argument("--diagnostics-dir", default="outputs/oddspedia_probability_extract/stealth_diagnostics")
    p.add_argument("--user-data-dir", default=".playwright-oddspedia-stealth-profile")
    p.add_argument("--seed-url", default="https://oddspedia.com/football/world/world-cup")
    p.add_argument("--settle-ms", type=int, default=10000, help="Wait after page load before extracting (ms)")
    p.add_argument("--post-click-ms", type=int, default=6000, help="Extra wait after clicking score tab (ms)")
    p.add_argument("--timeout-ms", type=int, default=90000)
    p.add_argument("--cf-wait-ms", type=int, default=30000, help="Max time to wait for Cloudflare auto-resolve (ms)")
    p.add_argument("--max-matches", type=int, default=0)
    p.add_argument("--no-chrome-channel", action="store_true", help="Use bundled Chromium instead of installed Chrome")
    p.add_argument("--manual-on-missing", action="store_true", help="Pause for manual interaction when grid missing")
    return p


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


def rows_from_state(
    row: dict[str, str], state: dict[str, Any], url_type: str, url: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    probs = state.get("probabilities") or {}
    winner = probs.get("100")
    correct = probs.get("800")
    winner_home = winner_draw = winner_away = None
    if isinstance(winner, list) and len(winner) >= 3:
        winner_home = to_float(winner[0])
        winner_draw = to_float(winner[1])
        winner_away = to_float(winner[2])
    score_probs: dict = {}
    odds_value = odds_bookie = odds_handicap = ""
    if isinstance(correct, dict):
        raw = correct.get("probabilities")
        if isinstance(raw, dict):
            score_probs = raw
        odds = correct.get("odds")
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
            "current_url": txt(state.get("currentUrl")),
            "source_name": txt(state.get("sourceName")),
        })
    exact_rows = [r for r in grid_rows if r["score_bucket"] == "exact" and r["probability_pct"] is not None]
    modal_score = ""
    modal_pct = None
    if exact_rows:
        best = max(exact_rows, key=lambda r: float(r["probability_pct"]))
        modal_score = txt(best["score_key"])
        modal_pct = best["probability_pct"]
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
        "current_url": txt(state.get("currentUrl")),
        "source_name": txt(state.get("sourceName")),
    }
    return grid_rows, summary


def is_cloudflare_challenge(body_text: str) -> bool:
    lower = body_text.lower()
    return any(phrase in lower for phrase in _CF_PHRASES)


async def wait_for_cf_resolve(page: Any, cf_wait_ms: int) -> bool:
    """Wait up to cf_wait_ms for Cloudflare challenge to auto-resolve. Returns True if resolved."""
    poll_ms = 3000
    elapsed = 0
    while elapsed < cf_wait_ms:
        await page.wait_for_timeout(poll_ms)
        elapsed += poll_ms
        body = await page.evaluate("() => document.body?.innerText?.slice(0, 500) || ''")
        if not is_cloudflare_challenge(body):
            print(f"  Cloudflare resolved after {elapsed}ms.")
            return True
    return False


async def scrape(
    args: argparse.Namespace, rows: list[dict[str, str]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except Exception as exc:
        raise RuntimeError("Install: pip install playwright && python -m playwright install chrome") from exc

    # Optional playwright-stealth integration
    _stealth_fn = None
    try:
        from playwright_stealth import stealth_async as _stealth_fn  # type: ignore[import]
        print("playwright-stealth available; stealth patches will be applied per page.")
    except ImportError:
        print("playwright-stealth not installed; using built-in patches only.")
        print("  Optional install: pip install playwright-stealth")

    diag_dir = Path(args.diagnostics_dir)
    diag_dir.mkdir(parents=True, exist_ok=True)
    grid_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []

    launch_kwargs: dict[str, Any] = {
        "headless": False,
        "locale": "en-GB",
        "viewport": {"width": 1440, "height": 900},
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
        # Patch navigator.webdriver for all pages born in this context
        await context.add_init_script(_WEBDRIVER_PATCH)

        page = context.pages[0] if context.pages else await context.new_page()
        if _stealth_fn:
            await _stealth_fn(page)

        print(f"Opening seed page: {args.seed_url}")
        try:
            await page.goto(args.seed_url, wait_until="domcontentloaded", timeout=args.timeout_ms)
        except PlaywrightTimeoutError:
            print("  Seed page timed out; checking state anyway.")
        await page.wait_for_timeout(3000)

        body = await page.evaluate("() => document.body?.innerText?.slice(0, 500) || ''")
        if is_cloudflare_challenge(body):
            print("Cloudflare challenge on seed page. Waiting for auto-resolve ...")
            resolved = await wait_for_cf_resolve(page, args.cf_wait_ms)
            if not resolved:
                print("Auto-resolve timed out. Please complete the challenge in the browser window.")
                input("Press ENTER when Oddspedia is accessible ...")
        else:
            print("  Seed page loaded (no challenge detected).")

        for idx, row in enumerate(rows, start=1):
            candidates = []
            if row.get("url"):
                candidates.append(("url", row["url"]))
            if row.get("alt_url") and row.get("alt_url") != row.get("url"):
                candidates.append(("alt_url", row["alt_url"]))

            found = False
            last_state: dict[str, Any] | None = None

            for url_type, url in candidates:
                print(f"[{idx}/{len(rows)}] {row.get('home_team')} vs {row.get('away_team')} :: {url_type}")
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=args.timeout_ms)
                except PlaywrightTimeoutError:
                    print("  page timeout; reading current state")
                await page.wait_for_timeout(args.settle_ms)

                body = await page.evaluate("() => document.body?.innerText?.slice(0, 500) || ''")
                if is_cloudflare_challenge(body):
                    print("  Challenge detected mid-session. Waiting for auto-resolve ...")
                    resolved = await wait_for_cf_resolve(page, args.cf_wait_ms)
                    if not resolved:
                        print("  Auto-resolve failed. Please complete challenge in browser.")
                        input("  Press ENTER when done: ")
                    # Re-navigate to the match page
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=args.timeout_ms)
                    except PlaywrightTimeoutError:
                        pass
                    await page.wait_for_timeout(args.settle_ms)

                state = await page.evaluate(_EXTRACT_JS)
                if not state.get("hasCorrectScore"):
                    clicked = await page.evaluate(_CLICK_JS)
                    if clicked:
                        print(f"  clicked {clicked[:4]}")
                    await page.wait_for_timeout(args.post_click_ms)
                    state = await page.evaluate(_EXTRACT_JS)
                    state["clicked"] = clicked

                last_state = state
                if state.get("hasCorrectScore"):
                    g, s = rows_from_state(row, state, url_type, url)
                    grid_rows.extend(g)
                    summaries.append(s)
                    print(f"  OK {len(g)} rows  modal={s['modal_correct_score']} {s['modal_correct_score_pct']}")
                    found = True
                    break
                print("  no grid found on this candidate")

            if not found and args.manual_on_missing:
                print("  Navigate to Score Forecast / Correct Score in the browser, then press ENTER (or s to skip).")
                ans = input("  ENTER retry / s skip: ").strip().lower()
                if ans != "s":
                    state = await page.evaluate(_EXTRACT_JS)
                    last_state = state
                    if state.get("hasCorrectScore"):
                        g, s = rows_from_state(row, state, "manual", txt(state.get("currentUrl")))
                        grid_rows.extend(g)
                        summaries.append(s)
                        print(f"  OK manual {len(g)} rows")
                        found = True

            if not found:
                name = safe_name(row.get("match_id"))
                body_file = diag_dir / f"{name}_body.txt"
                state_file = diag_dir / f"{name}_state.json"
                body_file.write_text(txt((last_state or {}).get("bodyText")), encoding="utf-8")
                state_file.write_text(
                    json.dumps({"row": row, "last_state": last_state}, indent=2, default=str), encoding="utf-8"
                )
                summaries.append({
                    "match_id": row.get("match_id", ""),
                    "commence_time": row.get("commence_time", ""),
                    "home_team": row.get("home_team", ""),
                    "away_team": row.get("away_team", ""),
                    "winner_home_pct": None,
                    "winner_draw_pct": None,
                    "winner_away_pct": None,
                    "correct_score_count": 0,
                    "modal_correct_score": "",
                    "modal_correct_score_pct": None,
                    "best_odds_value": "",
                    "best_odds_bookie": "",
                    "best_odds_scoreline": "",
                    "source_url_type": "",
                    "source_url": "",
                    "current_url": txt((last_state or {}).get("currentUrl")),
                    "source_name": txt((last_state or {}).get("sourceName")),
                })
                diagnostics.append({
                    "match_id": row.get("match_id", ""),
                    "home_team": row.get("home_team", ""),
                    "away_team": row.get("away_team", ""),
                    "body_file": str(body_file),
                    "state_file": str(state_file),
                })

        await context.close()
    return grid_rows, summaries, diagnostics


def main() -> int:
    args = build_parser().parse_args()
    rows = load_rows(Path(args.urls_csv), args.max_matches)
    if not rows:
        raise ValueError(f"No URL rows found in {args.urls_csv}")

    import asyncio

    grid_rows, summaries, diagnostics = asyncio.run(scrape(args, rows))

    out_csv = Path(args.out_csv)
    out_summary_csv = Path(args.out_summary_csv)
    out_json = Path(args.out_json)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_summary_csv.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)

    pd.DataFrame(grid_rows).to_csv(out_csv, index=False)
    pd.DataFrame(summaries).to_csv(out_summary_csv, index=False)

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_match_count": len(rows),
        "matches_with_grid": sum(1 for s in summaries if int(s.get("correct_score_count") or 0) > 0),
        "correct_score_row_count": len(grid_rows),
        "diagnostic_count": len(diagnostics),
        "diagnostics": diagnostics,
        "out_csv": str(out_csv),
        "out_summary_csv": str(out_summary_csv),
    }
    out_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(json.dumps(payload, indent=2, default=str))
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_summary_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
