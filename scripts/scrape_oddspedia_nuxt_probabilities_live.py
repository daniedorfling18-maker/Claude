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
        description="Scrape Oddspedia winner and correct-score probabilities directly from live Nuxt page state with URL/alt_url fallback."
    )
    parser.add_argument("--urls-csv", default="inputs/oddspedia_match_urls.csv")
    parser.add_argument("--out-csv", default="inputs/smartbet_grids/oddspedia_probability_grids_auto.csv")
    parser.add_argument("--out-summary-csv", default="inputs/smartbet_grids/oddspedia_probability_summary_auto.csv")
    parser.add_argument("--out-json", default="outputs/oddspedia_probability_extract/oddspedia_live_probability_extract_summary.json")
    parser.add_argument("--diagnostics-dir", default="outputs/oddspedia_probability_extract/live_diagnostics")
    parser.add_argument("--headful", action="store_true")
    parser.add_argument("--timeout-ms", type=int, default=60000)
    parser.add_argument("--settle-ms", type=int, default=12000)
    parser.add_argument("--post-click-ms", type=int, default=8000)
    parser.add_argument("--max-matches", type=int, default=0, help="Optional cap for testing. 0 means all rows.")
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


def slugify(value: Any) -> str:
    text = txt(value).lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def safe_name(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", txt(value)).strip("_")
    return text[:120] or "match"


def load_rows(path: Path, max_matches: int = 0) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing URL CSV: {path}")
    frame = pd.read_csv(path).fillna("")
    out: list[dict[str, str]] = []
    for _, row in frame.iterrows():
        data = {k: txt(row.get(k)) for k in frame.columns}
        if not data.get("url") and not data.get("alt_url"):
            continue
        if not data.get("match_id"):
            data["match_id"] = f"{slugify(data.get('home_team'))}-{slugify(data.get('away_team'))}"
        out.append(data)
    if max_matches and max_matches > 0:
        return out[:max_matches]
    return out


def score_parts(score_key: str) -> tuple[str, str, str]:
    if re.fullmatch(r"\d+-\d+", score_key):
        home_goals, away_goals = score_key.split("-", 1)
        return home_goals, away_goals, "exact"
    if score_key == "Other_1":
        return "Other", "Other", "other_home_win"
    if score_key == "Other_X":
        return "Other", "Other", "other_draw"
    if score_key == "Other_2":
        return "Other", "Other", "other_away_win"
    return "", "", "unknown"


def rows_from_probabilities(row: dict[str, str], source_url: str, current_url: str, source_name: str, probabilities: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    winner = probabilities.get("100")
    correct_score = probabilities.get("800")
    winner_home = winner_draw = winner_away = None
    if isinstance(winner, list) and len(winner) >= 3:
        winner_home = to_float(winner[0])
        winner_draw = to_float(winner[1])
        winner_away = to_float(winner[2])

    score_probs: dict[str, Any] = {}
    odds_value = odds_bookie = odds_handicap = ""
    if isinstance(correct_score, dict):
        raw_probs = correct_score.get("probabilities")
        if isinstance(raw_probs, dict):
            score_probs = raw_probs
        odds = correct_score.get("odds")
        if isinstance(odds, dict):
            odds_value = txt(odds.get("value"))
            odds_bookie = txt(odds.get("bookie"))
            odds_handicap = txt(odds.get("handicap_name"))

    grid_rows: list[dict[str, Any]] = []
    for score_key, probability in score_probs.items():
        home_goals, away_goals, bucket = score_parts(txt(score_key))
        grid_rows.append(
            {
                "match_id": row.get("match_id", ""),
                "commence_time": row.get("commence_time", ""),
                "home_team": row.get("home_team", ""),
                "away_team": row.get("away_team", ""),
                "market_id": "800",
                "market_name": "Correct Score",
                "score_key": txt(score_key),
                "home_goals": home_goals,
                "away_goals": away_goals,
                "score_bucket": bucket,
                "probability_pct": to_float(probability),
                "winner_home_pct": winner_home,
                "winner_draw_pct": winner_draw,
                "winner_away_pct": winner_away,
                "best_odds_value": odds_value,
                "best_odds_bookie": odds_bookie,
                "best_odds_scoreline": odds_handicap,
                "source_url": source_url,
                "current_url": current_url,
                "source_name": source_name,
            }
        )

    modal_score = ""
    modal_probability = None
    exact_rows = [r for r in grid_rows if r["score_bucket"] == "exact" and r["probability_pct"] is not None]
    if exact_rows:
        best = max(exact_rows, key=lambda r: float(r["probability_pct"]))
        modal_score = txt(best["score_key"])
        modal_probability = best["probability_pct"]

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
        "modal_correct_score_pct": modal_probability,
        "best_odds_value": odds_value,
        "best_odds_bookie": odds_bookie,
        "best_odds_scoreline": odds_handicap,
        "source_url": source_url,
        "current_url": current_url,
        "source_name": source_name,
    }
    return grid_rows, summary


EXTRACT_JS = r"""
() => {
  function getProbabilities() {
    const candidates = [];
    try { candidates.push(['nuxt_state_event', window.__NUXT__ && window.__NUXT__.state && window.__NUXT__.state.event]); } catch(e) {}
    try { candidates.push(['nuxt_event', window.__NUXT__ && window.__NUXT__.event]); } catch(e) {}
    try { candidates.push(['store_state_event', window.$nuxt && window.$nuxt.$store && window.$nuxt.$store.state && window.$nuxt.$store.state.event]); } catch(e) {}
    for (const [name, event] of candidates) {
      if (event && event.probabilities && event.probabilities['800'] && event.probabilities['800'].probabilities) {
        return {sourceName: name, probabilities: event.probabilities};
      }
    }
    for (const [name, event] of candidates) {
      if (event && event.probabilities) {
        return {sourceName: name, probabilities: event.probabilities};
      }
    }
    return {sourceName: '', probabilities: null};
  }
  const result = getProbabilities();
  return {
    currentUrl: window.location.href,
    title: document.title,
    bodyText: document.body ? document.body.innerText.slice(0, 20000) : '',
    sourceName: result.sourceName,
    probabilities: result.probabilities,
    hasCorrectScore: !!(result.probabilities && result.probabilities['800'] && result.probabilities['800'].probabilities),
  };
}
"""


AUTO_CLICK_JS = r"""
() => {
  const wanted = ['score forecast', 'match probabilities', 'probabilities', 'correct score'];
  const nodes = Array.from(document.querySelectorAll('a, button, [role="button"], [tabindex]'));
  let clicked = [];
  for (const node of nodes) {
    const text = (node.innerText || node.textContent || '').toLowerCase().trim();
    if (!text) continue;
    if (wanted.some(w => text.includes(w))) {
      try { node.click(); clicked.push(text.slice(0, 80)); } catch (e) {}
    }
  }
  window.scrollTo(0, Math.floor(document.body.scrollHeight * 0.35));
  return clicked;
}
"""


async def try_page(page: Any, url: str, args: argparse.Namespace) -> dict[str, Any]:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=args.timeout_ms)
    except PlaywrightTimeoutError:
        print(f"WARNING: domcontentloaded timeout for {url}; trying current state.")
    await page.wait_for_timeout(args.settle_ms)
    state = await page.evaluate(EXTRACT_JS)
    if state.get("hasCorrectScore"):
        return state
    clicked = await page.evaluate(AUTO_CLICK_JS)
    if clicked:
        print(f"  clicked: {clicked[:4]}")
    await page.wait_for_timeout(args.post_click_ms)
    state = await page.evaluate(EXTRACT_JS)
    state["clicked"] = clicked
    return state


async def scrape_all(rows: list[dict[str, str]], args: argparse.Namespace, diagnostics_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        raise RuntimeError("Install Playwright with: pip install playwright && python -m playwright install chromium") from exc

    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    all_grid_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not args.headful)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122 Safari/537.36",
            locale="en-GB",
        )
        for idx, row in enumerate(rows, start=1):
            label = f"{row.get('home_team')} vs {row.get('away_team')}"
            candidates = []
            if row.get("url"):
                candidates.append(("url", row["url"]))
            if row.get("alt_url") and row.get("alt_url") != row.get("url"):
                candidates.append(("alt_url", row["alt_url"]))

            found = False
            last_state: dict[str, Any] | None = None
            for candidate_name, url in candidates:
                page = await context.new_page()
                print(f"[{idx}/{len(rows)}] {label} :: {candidate_name} :: {url}")
                try:
                    state = await try_page(page, url, args)
                    last_state = state
                    if state.get("hasCorrectScore") and isinstance(state.get("probabilities"), dict):
                        grid_rows, summary = rows_from_probabilities(
                            row,
                            source_url=url,
                            current_url=txt(state.get("currentUrl")),
                            source_name=txt(state.get("sourceName")),
                            probabilities=state["probabilities"],
                        )
                        all_grid_rows.extend(grid_rows)
                        summaries.append(summary)
                        print(f"  OK: {len(grid_rows)} score rows, modal={summary.get('modal_correct_score')} {summary.get('modal_correct_score_pct')}")
                        found = True
                        await page.close()
                        break
                    print("  no correct-score probability grid found on this candidate")
                except Exception as exc:
                    last_state = {"error": str(exc), "currentUrl": url, "bodyText": ""}
                    print(f"  ERROR: {exc}")
                finally:
                    if not page.is_closed():
                        await page.close()

            if not found:
                diag_name = safe_name(row.get("match_id"))
                body_file = diagnostics_dir / f"{diag_name}_body_text.txt"
                state_file = diagnostics_dir / f"{diag_name}_last_state.json"
                body = txt((last_state or {}).get("bodyText"))
                body_file.write_text(body, encoding="utf-8")
                state_file.write_text(json.dumps({"row": row, "last_state": last_state}, indent=2, default=str), encoding="utf-8")
                diagnostics.append(
                    {
                        "match_id": row.get("match_id", ""),
                        "home_team": row.get("home_team", ""),
                        "away_team": row.get("away_team", ""),
                        "url": row.get("url", ""),
                        "alt_url": row.get("alt_url", ""),
                        "body_file": str(body_file),
                        "state_file": str(state_file),
                        "reason": "no probabilities.800.probabilities found on url or alt_url",
                    }
                )
                summaries.append(
                    {
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
                        "source_url": "",
                        "current_url": txt((last_state or {}).get("currentUrl")),
                        "source_name": txt((last_state or {}).get("sourceName")),
                    }
                )
        await context.close()
        await browser.close()
    return all_grid_rows, summaries, diagnostics


def main() -> int:
    args = build_parser().parse_args()
    rows = load_rows(Path(args.urls_csv), args.max_matches)
    if not rows:
        raise ValueError(f"No URL rows found in {args.urls_csv}")

    import asyncio

    grid_rows, summaries, diagnostics = asyncio.run(scrape_all(rows, args, Path(args.diagnostics_dir)))

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
    print(f"Wrote {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
