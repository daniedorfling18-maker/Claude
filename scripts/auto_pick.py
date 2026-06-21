"""
auto_pick.py

Runs on a schedule. Logs into SuperBru, scans every game tab for kickoff time
and pick state, then for any game kicking off within WINDOW_MINUTES:
  1. Calls Claude to predict the score.
  2. Submits via submit_superbru_pick_cdp.run() — the tested submission script.

Requires env vars: ANTHROPIC_API_KEY, SUPERBRU_EMAIL, SUPERBRU_PASSWORD
(or pass via CLI args).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import types
from datetime import datetime, timezone, timedelta
from pathlib import Path


# ── JavaScript helpers ────────────────────────────────────────────────────────

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
  // ── Kickoff time: try many selectors ──────────────────────────────────────
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

  // Fallback: scan page text for a date/time pattern
  if (!kickoffText && !kickoffTs) {
    const body = (document.body && document.body.innerText) || '';
    const m = body.match(
      /\b(\w{3}\s+\d{1,2}\s+\w{3}\s+\d{2}:\d{2}|\d{1,2}\s+\w+\s+\d{4}\s+\d{2}:\d{2}|\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4}\s+\d{2}:\d{2}|\d{2}:\d{2})\b/
    );
    if (m) kickoffText = m[0];
  }

  // ── Score inputs ──────────────────────────────────────────────────────────
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


# ── Kickoff time parsing ──────────────────────────────────────────────────────

def parse_kickoff(text: str | None, ts: str | None, ref: datetime) -> datetime | None:
    """Attempt to parse a kickoff datetime from SuperBru DOM text/timestamp into UTC."""
    if ts:
        try:
            return datetime.fromisoformat(ts.replace('Z', '+00:00')).astimezone(timezone.utc)
        except Exception:
            pass
        try:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc)
        except Exception:
            pass

    if not text:
        return None

    text = text.strip()
    year = ref.year

    for fmt in [
        "%a %d %b %H:%M",   # "Sun 22 Jun 18:00"
        "%d %b %H:%M",       # "22 Jun 18:00"
        "%d %b %Y %H:%M",    # "22 Jun 2026 18:00"
        "%d/%m/%Y %H:%M",    # "22/06/2026 18:00"
        "%d-%m-%Y %H:%M",    # "22-06-2026 18:00"
        "%H:%M",             # "18:00"  (assume today)
    ]:
        try:
            dt = datetime.strptime(text[:20], fmt)
            if dt.year == 1900:
                dt = dt.replace(year=year)
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue

    return None


# ── Claude prediction ─────────────────────────────────────────────────────────

def predict_pick(home: str, away: str) -> str:
    """Call Claude to predict the score. Returns 'H-A' string."""
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("pip install anthropic")

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=50,
        messages=[{
            "role": "user",
            "content": (
                f"Predict a 2026 FIFA World Cup match score.\n"
                f"Match: {home} vs {away}\n"
                f"Consider FIFA rankings, recent form, head-to-head, group stage context.\n"
                f"Reply with ONLY the score in H-A format (e.g. 2-1). Nothing else."
            )
        }]
    )
    raw = msg.content[0].text.strip()
    m = re.search(r"\b(\d+)-(\d+)\b", raw)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    raise ValueError(f"Unexpected model response: {raw!r}")


# ── SuperBru login ────────────────────────────────────────────────────────────

async def login(page, args) -> bool:
    await page.goto(args.login_url, wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_timeout(2000)

    for sel in [
        "button[aria-label='CONFIRM']", "button[aria-label='Accept All']",
        "#qc-cmp2-container button[mode='primary']",
        "button:has-text('Accept')", "button:has-text('Confirm')",
    ]:
        try:
            await page.click(sel, timeout=2000)
            await page.wait_for_timeout(2000)
            break
        except Exception:
            continue

    try:
        await page.fill("input[type=email]",    args.email,    timeout=3000)
        await page.fill("input[type=password]", args.password, timeout=3000)
    except Exception:
        return False

    for sel in ["button[type=submit]", "input[type=submit]", "button:has-text('Log')"]:
        try:
            await page.click(sel, timeout=3000)
            break
        except Exception:
            continue

    await page.wait_for_timeout(4000)
    return "login" not in page.url.lower()


# ── Submit via the tested submit script ───────────────────────────────────────

async def submit_pick(args, home_team: str, away_team: str, pick: str, out_dir: Path) -> dict:
    """Load submit_superbru_pick_cdp and call its run() to submit a single pick."""
    import importlib.util
    import pathlib

    submit_path = pathlib.Path(__file__).parent / "submit_superbru_pick_cdp.py"
    spec = importlib.util.spec_from_file_location("submit_superbru_pick_cdp", submit_path)
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


# ── Main run ──────────────────────────────────────────────────────────────────

async def run(args) -> dict:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise RuntimeError("pip install playwright && python -m playwright install chromium")

    now    = datetime.now(timezone.utc)
    window = timedelta(minutes=args.window_minutes)
    results: list[dict] = []
    subtabs: list = []

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Phase 1: scan all fixtures ────────────────────────────────────────────
    games_to_submit: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=args.headless)
        page    = await browser.new_page()

        print("Logging in (fixture scan)...")
        if not await login(page, args):
            await browser.close()
            return {"status": "login_failed", "run_at_utc": now.isoformat()}

        print("Navigating to pool...")
        await page.goto(args.pool_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(15000)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(2000)
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(1000)

        subtabs = await page.evaluate(FIND_SUBTABS_JS)
        print(f"Found {len(subtabs)} game tabs\n")

        for tab in subtabs:
            game_id = tab["gameId"]
            label   = tab["label"]

            if " v " not in label:
                continue
            home_team, away_team = (s.strip() for s in label.split(" v ", 1))

            clicked = await page.evaluate(CLICK_TAB_JS, [game_id])
            if not clicked:
                print(f"[{game_id}] {label}: tab not found")
                continue
            await page.wait_for_timeout(5000)

            info       = await page.evaluate(EXTRACT_MATCH_JS)
            kickoff_dt = parse_kickoff(info.get("kickoffText"), info.get("kickoffTs"), now)
            time_until = (kickoff_dt - now) if kickoff_dt else None
            in_window  = time_until is not None and timedelta(0) <= time_until <= window

            ko_str  = kickoff_dt.isoformat() if kickoff_dt else f"unknown ({info.get('kickoffText')!r})"
            til_str = f"{time_until.total_seconds()/60:.0f}min" if time_until is not None else "?"
            print(f"[{game_id}] {label}")
            print(f"  kickoff={ko_str}  in_window={in_window}  until={til_str}")
            print(f"  current={info.get('homeVal')}-{info.get('awayVal')}  "
                  f"locked={info.get('locked')}  inputs={info.get('inputsFound')}")

            entry: dict = {
                "game_id":       game_id,
                "game":          label,
                "home_team":     home_team,
                "away_team":     away_team,
                "kickoff_utc":   kickoff_dt.isoformat() if kickoff_dt else None,
                "kickoff_raw":   info.get("kickoffText"),
                "minutes_until": round(time_until.total_seconds()/60) if time_until else None,
                "current_pick":  f"{info.get('homeVal')}-{info.get('awayVal')}",
                "locked":        info.get("locked"),
            }

            if info.get("locked"):
                entry["status"] = "locked_skipped"
                results.append(entry)
                print("  → skipped (locked)\n")
                continue

            if not info.get("inputsFound"):
                entry["status"] = "no_inputs_skipped"
                results.append(entry)
                print("  → skipped (no score inputs found)\n")
                continue

            if not in_window:
                entry["status"] = "not_in_window"
                results.append(entry)
                print("  → skipped (not in window)\n")
                continue

            games_to_submit.append(entry)
            print("  → queued for prediction + submission\n")

        await browser.close()

    # ── Phase 2: predict + submit each game ───────────────────────────────────
    for entry in games_to_submit:
        home_team = entry["home_team"]
        away_team = entry["away_team"]

        try:
            pick = predict_pick(home_team, away_team)
            print(f"[{entry['game_id']}] {entry['game']} → predicted: {pick}")
        except Exception as exc:
            entry["status"] = "prediction_failed"
            entry["error"]  = str(exc)
            results.append(entry)
            print(f"[{entry['game_id']}] prediction failed: {exc}")
            continue

        entry["predicted_pick"] = pick

        if args.dry_run:
            entry["status"] = "dry_run"
            results.append(entry)
            print(f"[{entry['game_id']}] DRY RUN — not submitted")
            continue

        print(f"[{entry['game_id']}] Submitting {pick} via submit_superbru_pick_cdp...")
        try:
            submit_result = await submit_pick(args, home_team, away_team, pick, out_dir)
            entry["status"]        = submit_result.get("status", "unknown")
            entry["submit_result"] = submit_result
            print(f"[{entry['game_id']}] → {entry['status']}")
        except Exception as exc:
            entry["status"] = "submit_failed"
            entry["error"]  = str(exc)
            print(f"[{entry['game_id']}] submit failed: {exc}")

        results.append(entry)

    summary = {
        "run_at_utc":     now.isoformat(),
        "window_minutes": args.window_minutes,
        "dry_run":        args.dry_run,
        "total_games":    len(subtabs),
        "results":        results,
        "submitted":      sum(1 for r in results if r.get("status") == "submitted"),
        "skipped":        sum(1 for r in results if r.get("status", "").endswith("skipped")),
        "not_in_window":  sum(1 for r in results if r.get("status") == "not_in_window"),
    }

    ts = now.strftime("%Y%m%dT%H%M%SZ")
    (out_dir / f"{ts}_auto_pick.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Auto-pick SuperBru using Claude predictions.")
    p.add_argument("--email",    default=os.environ.get("SUPERBRU_EMAIL", ""))
    p.add_argument("--password", default=os.environ.get("SUPERBRU_PASSWORD", ""))
    p.add_argument("--login-url",  default="https://www.superbru.com/login")
    p.add_argument("--pool-url",   default=(
        "https://www.superbru.com/worldcup_predictor/pool_view.php"
        "?t=1296&p=13236623&view=matches"
    ))
    p.add_argument("--window-minutes", type=int, default=120,
                   help="Submit for games kicking off within this many minutes from now")
    p.add_argument("--headless", action="store_true", default=True)
    p.add_argument("--headed",   dest="headless", action="store_false")
    p.add_argument("--dry-run",  action="store_true")
    p.add_argument("--out-dir",  default="outputs/pregame_checks/auto_pick")
    return p


def main() -> int:
    args   = build_parser().parse_args()
    result = asyncio.run(run(args))
    print("\n" + json.dumps(result, indent=2, default=str))
    return 0 if result.get("status") != "login_failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
