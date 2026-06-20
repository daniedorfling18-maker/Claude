from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scrape Oddspedia probabilities using one persistent, interactive Chrome session."
    )
    parser.add_argument("--urls-csv", default="inputs/oddspedia_match_urls.csv")
    parser.add_argument("--out-csv", default="inputs/smartbet_grids/oddspedia_probability_grids_auto.csv")
    parser.add_argument("--out-summary-csv", default="inputs/smartbet_grids/oddspedia_probability_summary_auto.csv")
    parser.add_argument("--out-json", default="outputs/oddspedia_probability_extract/oddspedia_persistent_probability_extract_summary.json")
    parser.add_argument("--diagnostics-dir", default="outputs/oddspedia_probability_extract/persistent_diagnostics")
    parser.add_argument("--user-data-dir", default=".playwright-oddspedia-profile")
    parser.add_argument("--seed-url", default="https://oddspedia.com/football/world/world-cup")
    parser.add_argument("--timeout-ms", type=int, default=90000)
    parser.add_argument("--settle-ms", type=int, default=10000)
    parser.add_argument("--post-click-ms", type=int, default=6000)
    parser.add_argument("--max-matches", type=int, default=0)
    parser.add_argument("--no-chrome-channel", action="store_true", help="Use bundled Chromium instead of installed Chrome.")
    parser.add_argument("--manual-on-missing", action="store_true", help="Pause for manual page interaction when a grid is missing.")
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def to_float(value: Any) -> float | None:
    try:
        return float(str(value).replace("%", "").strip())
    except Exception:
        return None


def safe_name(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", txt(value)).strip("_")
    return text[:120] or "match"


def slugify(value: Any) -> str:
    text = txt(value).lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


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


EXTRACT_JS = r"""
() => {
  const candidates = [];
  try { candidates.push(['nuxt_state_event', window.__NUXT__?.state?.event]); } catch(e) {}
  try { candidates.push(['nuxt_event', window.__NUXT__?.event]); } catch(e) {}
  try { candidates.push(['store_state_event', window.$nuxt?.$store?.state?.event]); } catch(e) {}
  for (const [name, event] of candidates) {
    if (event?.probabilities?.['800']?.probabilities) {
      return {
        sourceName: name,
        probabilities: event.probabilities,
        hasCorrectScore: true,
        currentUrl: window.location.href,
        title: document.title,
        bodyText: document.body ? document.body.innerText.slice(0, 25000) : ''
      };
    }
  }
  for (const [name, event] of candidates) {
    if (event?.probabilities) {
      return {
        sourceName: name,
        probabilities: event.probabilities,
        hasCorrectScore: false,
        currentUrl: window.location.href,
        title: document.title,
        bodyText: document.body ? document.body.innerText.slice(0, 25000) : ''
      };
    }
  }
  return {
    sourceName: '',
    probabilities: null,
    hasCorrectScore: false,
    currentUrl: window.location.href,
    title: document.title,
    bodyText: document.body ? document.body.innerText.slice(0, 25000) : ''
  };
}
"""


CLICK_JS = r"""
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


def score_parts(score_key: str) -> tuple[str, str, str]:
    if re.fullmatch(r"\d+-\d+", score_key):
        h, a = score_key.split("-", 1)
        return h, a, "exact"
    if score_key == "Other_1":
        return "Other", "Other", "other_home_win"
    if score_key == "Other_X":
        return "Other", "Other", "other_draw"
    if score_key == "Other_2":
        return "Other", "Other", "other_away_win"
    return "", "", "unknown"


def rows_from_state(row: dict[str, str], state: dict[str, Any], candidate_name: str, candidate_url: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    probs = state.get("probabilities") or {}
    winner = probs.get("100")
    correct = probs.get("800")
    winner_home = winner_draw = winner_away = None
    if isinstance(winner, list) and len(winner) >= 3:
        winner_home = to_float(winner[0])
        winner_draw = to_float(winner[1])
        winner_away = to_float(winner[2])
    score_probs = correct.get("probabilities") if isinstance(correct, dict) else {}
    if not isinstance(score_probs, dict):
        score_probs = {}
    odds = correct.get("odds") if isinstance(correct, dict) and isinstance(correct.get("odds"), dict) else {}
    rows: list[dict[str, Any]] = []
    for score_key, pct in score_probs.items():
        h, a, bucket = score_parts(txt(score_key))
        rows.append({
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
            "best_odds_value": txt(odds.get("value")),
            "best_odds_bookie": txt(odds.get("bookie")),
            "best_odds_scoreline": txt(odds.get("handicap_name")),
            "source_url_type": candidate_name,
            "source_url": candidate_url,
            "current_url": txt(state.get("currentUrl")),
            "source_name": txt(state.get("sourceName")),
        })
    exact = [r for r in rows if r["score_bucket"] == "exact" and r["probability_pct"] is not None]
    modal_score = ""
    modal_pct = None
    if exact:
        best = max(exact, key=lambda r: float(r["probability_pct"]))
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
        "correct_score_count": len(rows),
        "modal_correct_score": modal_score,
        "modal_correct_score_pct": modal_pct,
        "best_odds_value": txt(odds.get("value")),
        "best_odds_bookie": txt(odds.get("bookie")),
        "best_odds_scoreline": txt(odds.get("handicap_name")),
        "source_url_type": candidate_name,
        "source_url": candidate_url,
        "current_url": txt(state.get("currentUrl")),
        "source_name": txt(state.get("sourceName")),
    }
    return rows, summary


async def scrape(args: argparse.Namespace, rows: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except Exception as exc:
        raise RuntimeError("Install with: pip install playwright && python -m playwright install chromium") from exc

    diagnostics_dir = Path(args.diagnostics_dir)
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []

    async with async_playwright() as p:
        launch_kwargs: dict[str, Any] = {
            "headless": False,
            "locale": "en-GB",
            "viewport": {"width": 1440, "height": 1000},
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122 Safari/537.36",
        }
        if not args.no_chrome_channel:
            launch_kwargs["channel"] = "chrome"
        context = await p.chromium.launch_persistent_context(args.user_data_dir, **launch_kwargs)
        page = context.pages[0] if context.pages else await context.new_page()

        print(f"Opening seed page first: {args.seed_url}")
        try:
            await page.goto(args.seed_url, wait_until="domcontentloaded", timeout=args.timeout_ms)
        except PlaywrightTimeoutError:
            print("Seed page timed out; continue if the browser is usable.")
        print("If the site shows a check/challenge, complete it in the browser, then press ENTER here.")
        input("Press ENTER when Oddspedia is visible and usable...")

        for idx, row in enumerate(rows, start=1):
            candidates = []
            if row.get("url"):
                candidates.append(("url", row["url"]))
            if row.get("alt_url") and row.get("alt_url") != row.get("url"):
                candidates.append(("alt_url", row["alt_url"]))
            found = False
            last_state: dict[str, Any] | None = None
            for name, url in candidates:
                print(f"[{idx}/{len(rows)}] {row.get('home_team')} vs {row.get('away_team')} :: {name} :: {url}")
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=args.timeout_ms)
                except PlaywrightTimeoutError:
                    print("  page timeout; reading current state anyway")
                await page.wait_for_timeout(args.settle_ms)
                state = await page.evaluate(EXTRACT_JS)
                if not state.get("hasCorrectScore"):
                    clicked = await page.evaluate(CLICK_JS)
                    if clicked:
                        print(f"  clicked {clicked[:4]}")
                    await page.wait_for_timeout(args.post_click_ms)
                    state = await page.evaluate(EXTRACT_JS)
                    state["clicked"] = clicked
                last_state = state
                if state.get("hasCorrectScore"):
                    grid, summary = rows_from_state(row, state, name, url)
                    all_rows.extend(grid)
                    summaries.append(summary)
                    print(f"  OK {len(grid)} rows modal={summary['modal_correct_score']} {summary['modal_correct_score_pct']}")
                    found = True
                    break
                print("  no grid found on this candidate")

            if not found and args.manual_on_missing:
                print("  Manual recovery: click Score Forecast/Correct Score in the browser if visible, then press ENTER. Type s then ENTER to skip.")
                ans = input("  ENTER retry / s skip: ").strip().lower()
                if ans != "s":
                    state = await page.evaluate(EXTRACT_JS)
                    if state.get("hasCorrectScore"):
                        grid, summary = rows_from_state(row, state, "manual_current_page", txt(state.get("currentUrl")))
                        all_rows.extend(grid)
                        summaries.append(summary)
                        print(f"  OK manual {len(grid)} rows modal={summary['modal_correct_score']} {summary['modal_correct_score_pct']}")
                        found = True

            if not found:
                diag_id = safe_name(row.get("match_id"))
                body_file = diagnostics_dir / f"{diag_id}_body_text.txt"
                state_file = diagnostics_dir / f"{diag_id}_last_state.json"
                body_file.write_text(txt((last_state or {}).get("bodyText")), encoding="utf-8")
                state_file.write_text(json.dumps({"row": row, "last_state": last_state}, indent=2, default=str), encoding="utf-8")
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
    return all_rows, summaries, diagnostics


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
        "out_csv": str(out_csv),
        "out_summary_csv": str(out_summary_csv),
        "diagnostics": diagnostics,
    }
    out_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(json.dumps(payload, indent=2, default=str))
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_summary_csv}")
    print(f"Wrote {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
