"""
auto_pick_match_scoped.py

Scheduled SuperBru auto-pick runner.

Behaviour:
- Scans SuperBru for matches inside the pre-kickoff window.
- Reads the score from the committed SuperBru card.
- Fetches The Odds API data only for the queued match event.
- Submits the locked-card pick.
- Does not call Claude CLI.
- Does not run the daily robust pipeline.
- Does not fetch whole-tournament odds.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import importlib.util
import json
import math
import os
import re
import sys
import types
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

API_HOST = "https://api.the-odds-api.com"


FIND_SUBTABS_JS = r"""
() => Array.from(document.querySelectorAll('[data-brutip][data-bru-tab]')).map(el => ({
  gameId: el.getAttribute('data-bru-tab'),
  label: (el.getAttribute('data-brutip') || '').trim(),
  classes: (el.className || '').slice(0, 120)
}))
"""

CLICK_TAB_JS = r"""
([gameId]) => {
  const el = document.querySelector(`[data-bru-tab="${gameId}"]`);
  if (el) { el.click(); return true; }
  return false;
}
"""

EXTRACT_MATCH_JS = r"""
() => {
  let kickoffText = null, kickoffTs = null;
  const candidates = Array.from(document.querySelectorAll(
    '[class*=kickoff],[class*=kick-off],[class*=match-time],[class*=fixture-time],' +
    '[class*=match-date],[class*=fixture-date],[class*=game-time],' +
    'time,[datetime],[data-kickoff],[data-timestamp],[data-time],' +
    '[class*=date],[class*=time]'
  ));
  for (const el of candidates) {
    const ts = el.getAttribute('datetime') || el.getAttribute('data-kickoff') ||
               el.getAttribute('data-timestamp') || el.getAttribute('data-time');
    const txt = (el.innerText || el.textContent || '').replace(/\s+/g,' ').trim();
    if (ts)  { kickoffTs = ts; kickoffText = txt; break; }
    if (/\d{1,2}[:\-]\d{2}/.test(txt)) { kickoffText = txt; break; }
  }

  if (!kickoffText && !kickoffTs) {
    const body = (document.body && document.body.innerText) || '';
    const m = body.match(
      /\b(\w{3}\s+\d{1,2}\s+\w+\s+\d{2}:\d{2}|\d{1,2}\s+\w+\s+\d{4}\s+\d{2}:\d{2}|\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4}\s+\d{2}:\d{2}|\d{2}:\d{2})\b/
    );
    if (m) kickoffText = m[0];
  }

  const hi = document.querySelector('input.soccer-left-score');
  const ai = document.querySelector('input.soccer-right-score');

  return {
    kickoffText,
    kickoffTs,
    homeVal: hi ? hi.value : null,
    awayVal: ai ? ai.value : null,
    locked: hi ? (hi.disabled || hi.readOnly) : null,
    inputsFound: !!(hi && ai)
  };
}
"""


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def norm_team(value: Any) -> str:
    return "".join(ch.lower() for ch in txt(value) if ch.isalnum())


def safe_name(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "_", txt(value))
    return clean.strip("_") or "match"


def parse_iso_datetime(value: Any) -> datetime | None:
    text = txt(value)
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_kickoff(text: str | None, ts: str | None, ref: datetime) -> datetime | None:
    if ts:
        parsed = parse_iso_datetime(ts)
        if parsed:
            return parsed
        try:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc)
        except Exception:
            pass

    if not text:
        return None

    text = text.strip()
    year = ref.year

    for fmt in [
        "%a %d %b %H:%M",
        "%d %b %H:%M",
        "%d %b %Y %H:%M",
        "%d/%m/%Y %H:%M",
        "%d-%m-%Y %H:%M",
        "%H:%M",
    ]:
        try:
            dt = datetime.strptime(text[:20], fmt)
            if dt.year == 1900:
                dt = dt.replace(year=year)
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def pick_column(fieldnames: list[str]) -> str:
    for column in ["robust_locked_pick", "locked_pick", "final_pick", "recommended_scoreline", "pick"]:
        if column in fieldnames:
            return column
    raise ValueError(f"Could not find a pick column in card header: {fieldnames}")


def load_pick_rows(path: Path) -> tuple[list[dict[str, str]], str]:
    if not path.exists():
        raise FileNotFoundError(f"Pick card not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        if not reader.fieldnames:
            raise ValueError(f"Pick card has no header: {path}")
        return rows, pick_column(list(reader.fieldnames))


def card_row_matches(row: dict[str, Any], entry: dict[str, Any]) -> bool:
    return norm_team(row.get("home_team")) == norm_team(entry.get("home_team")) and norm_team(row.get("away_team")) == norm_team(entry.get("away_team"))


def find_pick_from_card(entry: dict[str, Any], card_csv: str) -> dict[str, Any]:
    path = Path(card_csv)
    rows, column = load_pick_rows(path)
    kickoff = parse_iso_datetime(entry.get("kickoff_utc"))
    matches = [row for row in rows if card_row_matches(row, entry)]

    if kickoff and len(matches) > 1:
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in matches:
            row_kickoff = parse_iso_datetime(row.get("commence_time"))
            delta = abs((row_kickoff - kickoff).total_seconds()) if row_kickoff else 999999.0
            scored.append((delta, row))
        scored.sort(key=lambda item: item[0])
        matches = [scored[0][1]]

    if not matches:
        return {"status": "not_found", "pick_card_csv": str(path), "pick_column": column}

    row = matches[0]
    return {
        "status": "found",
        "pick": txt(row.get(column)),
        "pick_card_csv": str(path),
        "pick_column": column,
        "card_row": row,
    }


def request_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "superbru-auto-picker/1.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def teams_match_event(event: dict[str, Any], home: str, away: str) -> bool:
    return {norm_team(event.get("home_team")), norm_team(event.get("away_team"))} == {norm_team(home), norm_team(away)}


def find_event(args: argparse.Namespace, entry: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str]:
    kickoff = parse_iso_datetime(entry.get("kickoff_utc"))
    if kickoff is None:
        return None, [], "missing_kickoff"

    start = kickoff - timedelta(minutes=args.odds_lookup_window_minutes)
    end = kickoff + timedelta(minutes=args.odds_lookup_window_minutes)
    params = {
        "apiKey": args.odds_api_key,
        "dateFormat": "iso",
        "commenceTimeFrom": iso_z(start),
        "commenceTimeTo": iso_z(end),
    }
    url = f"{API_HOST}/v4/sports/{args.odds_sport}/events/?" + urllib.parse.urlencode(params)
    events = request_json(url)
    if not isinstance(events, list):
        raise ValueError(f"Expected list response from The Odds API, got {type(events).__name__}")

    matches = [event for event in events if teams_match_event(event, entry["home_team"], entry["away_team"])]
    if not matches:
        return None, events, "event_not_found"

    matches.sort(key=lambda event: abs(((parse_iso_datetime(event.get("commence_time")) or kickoff) - kickoff).total_seconds()))
    return matches[0], events, "event_found"


def fetch_event_odds(args: argparse.Namespace, event_id: str) -> Any:
    params = {
        "apiKey": args.odds_api_key,
        "regions": args.odds_regions,
        "markets": args.odds_markets,
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }
    url = f"{API_HOST}/v4/sports/{args.odds_sport}/events/{event_id}/odds/?" + urllib.parse.urlencode(params)
    return request_json(url)


def h2h_summary(odds: Any) -> dict[str, Any]:
    if not isinstance(odds, dict):
        return {"status": "unavailable"}
    prices: dict[str, list[float]] = {}
    for bookmaker in odds.get("bookmakers", []) or []:
        for market in bookmaker.get("markets", []) or []:
            if market.get("key") != "h2h":
                continue
            for outcome in market.get("outcomes", []) or []:
                name = txt(outcome.get("name"))
                try:
                    price = float(outcome.get("price"))
                except Exception:
                    continue
                if name:
                    prices.setdefault(name, []).append(price)
    if not prices:
        return {"status": "no_h2h_prices"}
    averages = {name: sum(values) / len(values) for name, values in prices.items() if values}
    favourite = min(averages, key=averages.get)
    return {
        "status": "ok",
        "favourite": favourite,
        "average_decimal_prices": averages,
        "bookmaker_counts": {name: len(values) for name, values in prices.items()},
    }


def fetch_match_odds_snapshot(args: argparse.Namespace, entry: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    if args.skip_match_odds:
        return {"status": "skipped_by_flag"}
    if not args.odds_api_key:
        return {"status": "skipped_missing_api_key"}

    odds_dir = out_dir / "match_odds"
    odds_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_name(f"{entry['home_team']}_vs_{entry['away_team']}_{entry.get('game_id', '')}")
    odds_json_path = odds_dir / f"{stem}.json"
    summary_path = odds_dir / f"{stem}.summary.json"

    try:
        event, candidates, status = find_event(args, entry)
        if event is None:
            summary = {
                "status": status,
                "home_team": entry.get("home_team"),
                "away_team": entry.get("away_team"),
                "kickoff_utc": entry.get("kickoff_utc"),
                "candidate_event_count": len(candidates),
                "candidate_events": candidates,
            }
            summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            return summary

        odds = fetch_event_odds(args, txt(event.get("id")))
        odds_json_path.write_text(json.dumps(odds, indent=2), encoding="utf-8")
        summary = {
            "status": "fetched_single_match_odds",
            "event_id": event.get("id"),
            "home_team": entry.get("home_team"),
            "away_team": entry.get("away_team"),
            "kickoff_utc": entry.get("kickoff_utc"),
            "event": event,
            "odds_json": str(odds_json_path),
            "h2h_summary": h2h_summary(odds),
        }
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return summary
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        error = f"HTTP {exc.code} {exc.reason}: {body[:1000]}"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    summary = {
        "status": "odds_fetch_failed_non_blocking",
        "home_team": entry.get("home_team"),
        "away_team": entry.get("away_team"),
        "kickoff_utc": entry.get("kickoff_utc"),
        "error": error,
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def load_engine_config(path: str) -> Any:
    from superbru_score_engine.config import load_config

    return load_config(path)


def recompute_pick_from_snapshot(snapshot: dict[str, Any], entry: dict[str, Any], config: Any) -> dict[str, Any]:
    """Recompute the recommended scoreline from freshly fetched single-match odds.

    The returned scoreline is oriented to the Superbru tab's home/away order in
    `entry`, so it can be submitted directly. Any failure returns a non-"ok"
    status so the caller can fall back to the committed card pick.
    """
    if snapshot.get("status") != "fetched_single_match_odds":
        return {"status": "skipped", "reason": snapshot.get("status")}
    odds_path = snapshot.get("odds_json")
    if not odds_path or not Path(odds_path).exists():
        return {"status": "failed", "error": "odds_json_missing"}
    try:
        from superbru_score_engine.decision import SuperbruDecisionEngine
        from superbru_score_engine.ingest.normalise import normalise_the_odds_api_events
        from superbru_score_engine.model import OddsToScorelineModel
        from superbru_score_engine.model.ratings import RatingsStore

        odds_obj = json.loads(Path(odds_path).read_text(encoding="utf-8"))
        matches = normalise_the_odds_api_events([odds_obj])
        if not matches:
            return {"status": "failed", "error": "no_normalised_match"}
        match = matches[0]

        ratings = RatingsStore(config.paths.ratings_store, config.ratings)
        model = OddsToScorelineModel(config.model, ratings)
        decision = SuperbruDecisionEngine(
            config.superbru, config.model.candidate_grid_goals, config.public_pick, config.sensitivity
        )
        distribution = model.build_distribution(match)
        prediction = decision.predict(distribution)
        home_goals = int(prediction.recommended.home_goals)
        away_goals = int(prediction.recommended.away_goals)

        # The model scoreline is in the odds event's home/away order. Orient it to
        # the Superbru tab order before submitting.
        odds_home = norm_team(match.home_team)
        if odds_home == norm_team(entry.get("home_team")):
            scoreline = f"{home_goals}-{away_goals}"
            orientation = "aligned"
        elif odds_home == norm_team(entry.get("away_team")):
            scoreline = f"{away_goals}-{home_goals}"
            orientation = "swapped"
        else:
            # Cannot confirm orientation; fall back to the already-oriented card pick.
            return {"status": "failed", "error": "orientation_unconfirmed"}

        return {
            "status": "ok",
            "scoreline": scoreline,
            "orientation": orientation,
            "expected_points": float(prediction.recommended.expected_points),
            "model_home_away_scoreline": f"{home_goals}-{away_goals}",
            "odds_home_team": match.home_team,
            "odds_away_team": match.away_team,
        }
    except Exception as exc:  # non-blocking: fall back to the committed card pick
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}


async def login(page, args, diag_dir: Path | None = None) -> bool:
    try:
        await page.goto(args.login_url, wait_until="networkidle", timeout=45000)
    except Exception:
        await page.goto(args.login_url, wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_timeout(5000)

    if diag_dir:
        diag_dir.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(diag_dir / "login_page.png"), full_page=True)
        print(f"  page title: {await page.title()!r}  url: {page.url!r}")

    for sel in [
        "button[aria-label='CONFIRM']", "button[aria-label='Accept All']",
        "button[aria-label='Accept all']", "button[aria-label='I Accept']",
        "#qc-cmp2-container button[mode='primary']",
        ".qc-cmp2-summary-buttons button:last-child",
        "button:has-text('Accept')", "button:has-text('Confirm')",
        "button:has-text('I agree')", "button:has-text('Agree')",
        "#accept-cookie-policy",
    ]:
        try:
            await page.click(sel, timeout=2000)
            await page.wait_for_timeout(2500)
            break
        except Exception:
            continue

    for sel in ["input[type=email]", "input[name=email]", "input[name=username]", "input[id*=email]", "input[id*=user]", "input[name=login]", "input[name=user]"]:
        try:
            await page.fill(sel, args.email, timeout=3000)
            break
        except Exception:
            continue

    for sel in ["input[type=password]", "input[name=password]", "input[id*=pass]"]:
        try:
            await page.fill(sel, args.password, timeout=3000)
            break
        except Exception:
            continue

    submitted = False
    for sel in ["button[type=submit]", "input[type=submit]", "button:has-text('Log')", "button:has-text('Sign')"]:
        try:
            await page.click(sel, timeout=3000)
            submitted = True
            break
        except Exception:
            continue
    if not submitted:
        await page.keyboard.press("Enter")

    await page.wait_for_timeout(5000)
    return "login" not in page.url.lower()


async def submit_pick(args, home_team: str, away_team: str, pick: str, out_dir: Path) -> dict[str, Any]:
    submit_path = Path(__file__).parent / "submit_superbru_pick_cdp.py"
    spec = importlib.util.spec_from_file_location("submit_superbru_pick_cdp", submit_path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Could not load submit script: {submit_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    submit_args = types.SimpleNamespace(
        launch=True,
        headless=args.headless,
        email=args.email,
        password=args.password,
        login_url=args.login_url,
        pool_url=args.pool_url,
        home_team=home_team,
        away_team=away_team,
        new_pick=pick,
        settle_ms=8000,
        timeout_ms=60000,
        dry_run=args.dry_run,
        inspect_only=False,
        diagnostics_dir=str(out_dir / "submit_diagnostics"),
        cdp_url="http://127.0.0.1:9222",
    )
    return await mod.run(submit_args)


async def scan_superbru_matches(args: argparse.Namespace, out_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    from playwright.async_api import async_playwright

    now = datetime.now(timezone.utc)
    window = timedelta(minutes=args.window_minutes)
    results: list[dict[str, Any]] = []
    queued: list[dict[str, Any]] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=args.headless)
        page = await browser.new_page()

        login_diag = out_dir / "login_diagnostics"
        if not await login(page, args, diag_dir=login_diag):
            await browser.close()
            return results, queued, "login_failed"

        await page.goto(args.pool_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(15000)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(2000)
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(1000)

        subtabs = await page.evaluate(FIND_SUBTABS_JS)
        print(f"Found {len(subtabs)} game tabs")

        for tab in subtabs:
            game_id = tab["gameId"]
            label = tab["label"]
            if " v " not in label:
                continue
            home_team, away_team = (item.strip() for item in label.split(" v ", 1))

            clicked = await page.evaluate(CLICK_TAB_JS, [game_id])
            if not clicked:
                results.append({"game_id": game_id, "game": label, "status": "tab_not_found"})
                continue
            await page.wait_for_timeout(5000)

            info = await page.evaluate(EXTRACT_MATCH_JS)
            kickoff_dt = parse_kickoff(info.get("kickoffText"), info.get("kickoffTs"), now)
            time_until = (kickoff_dt - now) if kickoff_dt else None
            in_window = time_until is not None and timedelta(0) <= time_until <= window

            entry: dict[str, Any] = {
                "game_id": game_id,
                "game": label,
                "home_team": home_team,
                "away_team": away_team,
                "kickoff_utc": kickoff_dt.isoformat() if kickoff_dt else None,
                "kickoff_raw": info.get("kickoffText"),
                "minutes_until": round(time_until.total_seconds() / 60) if time_until else None,
                "current_pick": f"{info.get('homeVal')}-{info.get('awayVal')}",
                "locked": info.get("locked"),
                "inputs_found": info.get("inputsFound"),
            }

            print(f"[{game_id}] {label} kickoff={entry['kickoff_utc']} in_window={in_window}")

            if info.get("locked"):
                entry["status"] = "locked_skipped"
            elif not info.get("inputsFound"):
                entry["status"] = "no_inputs_skipped"
            elif not in_window:
                entry["status"] = "not_in_window"
            else:
                entry["status"] = "queued"
                queued.append(entry)

            results.append(entry)

        await browser.close()

    return results, queued, "ok"


async def run(args: argparse.Namespace) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    scan_results, queued, scan_status = await scan_superbru_matches(args, out_dir)
    if scan_status == "login_failed":
        return {"status": "login_failed", "run_at_utc": now.isoformat(), "scan_results": scan_results}

    config = None
    config_error: str | None = None
    try:
        config = load_engine_config(args.config)
    except Exception as exc:  # non-blocking: recompute is skipped, card pick is used
        config_error = f"{type(exc).__name__}: {exc}"
        print(f"warning: could not load engine config {args.config!r}: {config_error}. Falling back to committed card picks.")

    submitted_results: list[dict[str, Any]] = []
    for entry in queued:
        pick_lookup = find_pick_from_card(entry, args.pick_card_csv)
        entry["pick_lookup"] = pick_lookup
        card_pick = txt(pick_lookup.get("pick")) if pick_lookup.get("status") == "found" else ""

        # Pull this match's odds right before kickoff and recompute a fresh pick so a
        # stale committed card cannot drive the submission. The fresh pick wins; the
        # card pick is the fallback when the recompute is unavailable.
        entry["match_odds"] = fetch_match_odds_snapshot(args, entry, out_dir)
        fresh = recompute_pick_from_snapshot(entry["match_odds"], entry, config) if config is not None else {"status": "skipped", "reason": "config_unavailable", "error": config_error}
        entry["fresh_pick"] = fresh

        if fresh.get("status") == "ok" and txt(fresh.get("scoreline")):
            pick = txt(fresh["scoreline"])
            entry["pick_source"] = "live_odds_recompute"
        elif card_pick:
            pick = card_pick
            entry["pick_source"] = "committed_card_fallback"
        else:
            entry["status"] = "no_pick_available"
            submitted_results.append(entry)
            continue

        entry["card_pick"] = card_pick
        entry["selected_pick"] = pick
        entry["pick_changed_vs_card"] = bool(card_pick and pick != card_pick)

        if args.dry_run:
            entry["status"] = "dry_run"
            submitted_results.append(entry)
            continue

        try:
            submit_result = await submit_pick(args, entry["home_team"], entry["away_team"], pick, out_dir)
            entry["status"] = submit_result.get("status", "unknown")
            entry["submit_result"] = submit_result
        except Exception as exc:
            entry["status"] = "submit_failed"
            entry["error"] = str(exc)
        submitted_results.append(entry)

    summary = {
        "run_at_utc": now.isoformat(),
        "mode": "match_scoped_locked_card_auto_pick",
        "window_minutes": args.window_minutes,
        "dry_run": args.dry_run,
        "pick_card_csv": args.pick_card_csv,
        "scan_results": scan_results,
        "queued_count": len(queued),
        "results": submitted_results,
        "submitted": sum(1 for item in submitted_results if item.get("status") == "submitted"),
        "dry_run_count": sum(1 for item in submitted_results if item.get("status") == "dry_run"),
        "no_pick_available": sum(1 for item in submitted_results if item.get("status") == "no_pick_available"),
        "submit_failed": sum(1 for item in submitted_results if item.get("status") == "submit_failed"),
        "fresh_recompute_used": sum(1 for item in submitted_results if item.get("pick_source") == "live_odds_recompute"),
        "card_fallback_used": sum(1 for item in submitted_results if item.get("pick_source") == "committed_card_fallback"),
        "pick_changed_vs_card": sum(1 for item in submitted_results if item.get("pick_changed_vs_card")),
    }

    ts = now.strftime("%Y%m%dT%H%M%SZ")
    (out_dir / f"{ts}_auto_pick_match_scoped.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Match-scoped Auto Pick using committed SuperBru card.")
    parser.add_argument("--email", default=os.environ.get("SUPERBRU_EMAIL", ""))
    parser.add_argument("--password", default=os.environ.get("SUPERBRU_PASSWORD", ""))
    parser.add_argument("--login-url", default="https://www.superbru.com/login")
    parser.add_argument("--pool-url", default="https://www.superbru.com/worldcup_predictor/pool_view.php?t=1296&p=13236623&view=matches")
    parser.add_argument("--window-minutes", type=int, default=20)
    parser.add_argument("--headless", dest="headless", action="store_true", default=True)
    parser.add_argument("--headed", dest="headless", action="store_false")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out-dir", default="outputs/pregame_checks/auto_pick")
    parser.add_argument("--pick-card-csv", default="outputs/final_locked_picks/superbru_final_card.csv")
    parser.add_argument("--config", default="config.yaml", help="Engine config used to recompute the pick from fresh single-match odds.")
    parser.add_argument("--odds-api-key", default=os.environ.get("THE_ODDS_API_KEY", ""))
    parser.add_argument("--odds-sport", default="soccer_fifa_world_cup")
    parser.add_argument("--odds-regions", default="uk,eu,us,au")
    parser.add_argument("--odds-markets", default="h2h,spreads,totals")
    parser.add_argument("--odds-lookup-window-minutes", type=int, default=90)
    parser.add_argument("--skip-match-odds", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.email or not args.password:
        print("ERROR: SUPERBRU_EMAIL and SUPERBRU_PASSWORD must be set", file=sys.stderr)
        return 1
    result = asyncio.run(run(args))
    print("\n" + json.dumps(result, indent=2, default=str))
    return 0 if result.get("status") != "login_failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
