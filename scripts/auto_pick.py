"""
auto_pick.py

Runs on a schedule. Logs into SuperBru, scans every game tab for kickoff time
and pick state, then for any game kicking off within WINDOW_MINUTES:
  1. Calls Claude (Haiku) to predict the score.
  2. Submits the pick via blur/change auto-save.

Requires env vars: ANTHROPIC_API_KEY, SUPERBRU_EMAIL, SUPERBRU_PASSWORD
(or pass via CLI args).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path


# ── JavaScript helpers ────────────────────────────────────────────────────────────────────────────

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
  // ── Kickoff time: try many selectors ─────────────────────────────────────────────────
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

  // ── Score inputs ────────────────────────────────────────────────────────────────────
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

SET_INPUT_JS = r"""
([selector, value]) => {
  const el = document.querySelector(selector);
  if (!el) return { ok: false, reason: 'not found: ' + selector };
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  setter.call(el, value);
  el.dispatchEvent(new Event('input',  { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  el.dispatchEvent(new Event('blur',   { bubbles: true }));
  return { ok: true, value: el.value };
}
"""


# ── Kickoff time parsing ───────────────────────────────────────────────────────────────────

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
            # SuperBru times may be in a local TZ; assume UTC for now
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue

    return None


# ── Claude prediction ─────────────────────────────────────────────────────────────────────────

def predict_pick(home: str, away: str) -> str:
    """Call Claude Haiku to predict the score. Returns 'H-A' string."""
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("pip install anthropic")

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=15,
        messages=[{
            "role": "user",
            "content": (
                f"Predict a 2026 FIFA World Cup match score.\n"
                f"Match: {home} vs {away}\n"
                f"Use FIFA rankings, team quality, group stage dynamics.\n"
                f"Reply with ONLY the score in H-A format (e.g. 2-1). Nothing else."
            )
        }]
    )
    raw = msg.content[0].text.strip()
    m = re.search(r"\b(\d+)-(\d+)\b", raw)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    raise ValueError(f"Unexpected model response: {raw!r}")


# ── SuperBru login ────────────────────────────────────────────────────────────────────────────

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


# ── Main run ──────────────────────────────────────────────────────────────────────────────────

async def run(args) -> dict:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise RuntimeError("pip install playwright && python -m playwright install chromium")

    now    = datetime.now(timezone.utc)
    window = timedelta(minutes=args.window_minutes)
    results: list[dict] = []

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=args.headless)
        page    = await browser.new_page()

        print("Logging in...")
        if not await login(page, args):
            await browser.close()
            return {"status": "login_failed", "run_at_utc": now.isoformat()}

        print(f"Logged in. Navigating to pool...")
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
            label   = tab["label"]  # e.g. "Spain v Saudi Arabia"

            if " v " not in label:
                continue
            home_team, away_team = (s.strip() for s in label.split(" v ", 1))

            # Click tab to load AJAX content
            clicked = await page.evaluate(CLICK_TAB_JS, [game_id])
            if not clicked:
                print(f"[{game_id}] {label}: tab not found")
                continue
            await page.wait_for_timeout(5000)

            info       = await page.evaluate(EXTRACT_MATCH_JS)
            kickoff_dt = parse_kickoff(info.get("kickoffText"), info.get("kickoffTs"), now)
            time_until = (kickoff_dt - now) if kickoff_dt else None
            in_window  = time_until is not None and timedelta(0) <= time_until <= window

            ko_str   = kickoff_dt.isoformat() if kickoff_dt else f"unknown ({info.get('kickoffText')!r})"
            til_str  = f"{time_until.total_seconds()/60:.0f}min" if time_until is not None else "?"
            print(f"[{game_id}] {label}")
            print(f"  kickoff={ko_str}  in_window={in_window}  until={til_str}")
            print(f"  current={info.get('homeVal')}-{info.get('awayVal')}  "
                  f"locked={info.get('locked')}  inputs={info.get('inputsFound')}")

            entry: dict = {
                "game_id":    game_id,
                "game":       label,
                "home_team":  home_team,
                "away_team":  away_team,
                "kickoff_utc": kickoff_dt.isoformat() if kickoff_dt else None,
                "kickoff_raw": info.get("kickoffText"),
                "minutes_until": round(time_until.total_seconds()/60) if time_until else None,
                "current_pick": f"{info.get('homeVal')}-{info.get('awayVal')}",
                "locked": info.get("locked"),
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

            # Predict
            try:
                pick = predict_pick(home_team, away_team)
                print(f"  → predicted: {pick}")
            except Exception as exc:
                entry["status"] = "prediction_failed"
                entry["error"]  = str(exc)
                results.append(entry)
                print(f"  → prediction failed: {exc}\n")
                continue

            home_goals, away_goals = pick.split("-")
            entry["predicted_pick"] = pick

            if args.dry_run:
                entry["status"] = "dry_run"
                results.append(entry)
                print("  → DRY RUN — not submitted\n")
                continue

            # Submit
            r_h = await page.evaluate(SET_INPUT_JS, ["input.soccer-left-score",  home_goals])
            await page.wait_for_timeout(500)
            r_a = await page.evaluate(SET_INPUT_JS, ["input.soccer-right-score", away_goals])
            await page.wait_for_timeout(3000)

            entry["status"]   = "submitted"
            entry["r_home"]   = r_h
            entry["r_away"]   = r_a
            results.append(entry)
            print(f"  → submitted {pick}  (home={r_h}, away={r_a})\n")

        await browser.close()

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
    args  = build_parser().parse_args()
    result = asyncio.run(run(args))
    print("\n" + json.dumps(result, indent=2, default=str))
    return 0 if result.get("status") != "login_failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
