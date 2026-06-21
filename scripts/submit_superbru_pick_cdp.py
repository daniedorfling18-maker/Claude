"""
submit_superbru_pick_cdp.py

Attaches to an already-open Chrome CDP session and submits a corrected
score pick on SuperBru. Uses multiple selector strategies with a
diagnostic fallback so the DOM layout can be inspected if anything fails.

Usage:
    python scripts/submit_superbru_pick_cdp.py \
        --cdp-url http://127.0.0.1:9222 \
        --pool-url "https://www.superbru.com/worldcup_predictor/pool_view.php?..." \
        --home-team "France" --away-team "Iraq" \
        --new-pick "2-0" \
        --dry-run          # inspect only, don't click submit

    # Dump DOM to see what inputs exist (use when building/debugging):
    python scripts/submit_superbru_pick_cdp.py --inspect-only ...
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DIAGNOSTICS_DIR = Path("outputs/pregame_checks/submit_diagnostics")

# JS: find all visible input elements with context (surrounding text, parent id/class)
INSPECT_JS = r"""
() => {
  function parentText(el, depth) {
    let node = el, text = '';
    for (let i = 0; i < depth && node; i++) {
      text = (node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 200);
      if (text.length > 5) break;
      node = node.parentElement;
    }
    return text;
  }
  return Array.from(document.querySelectorAll('input, select')).map(el => ({
    tag: el.tagName.toLowerCase(),
    type: el.type || '',
    name: el.name || '',
    id: el.id || '',
    className: el.className || '',
    value: el.value || '',
    placeholder: el.placeholder || '',
    maxlength: el.maxLength || '',
    parentText: parentText(el, 5),
    rect: el.getBoundingClientRect ? (() => { const r = el.getBoundingClientRect(); return {top: r.top, left: r.left, width: r.width, height: r.height}; })() : {},
    visible: el.offsetParent !== null
  }));
}
"""

# JS: find a match row by home/away team name text, then locate score inputs within it
FIND_ROW_JS = r"""
(homeTeam, awayTeam) => {
  function norm(s) { return (s || '').toLowerCase().replace(/[^a-z0-9]/g, ''); }
  const hn = norm(homeTeam), an = norm(awayTeam);

  // Walk all elements to find one whose text contains both team names
  const all = Array.from(document.querySelectorAll('tr, li, div, section, article'));
  let matchRow = null;
  for (const el of all) {
    const t = norm(el.innerText || el.textContent || '');
    if (t.includes(hn) && t.includes(an) && t.length < 2000) {
      // Prefer the most specific (smallest) container
      if (!matchRow || (el.innerText || '').length < (matchRow.innerText || '').length) {
        matchRow = el;
      }
    }
  }
  if (!matchRow) return { found: false, reason: 'no element contains both team names' };

  // Collect all inputs inside the match row
  const inputs = Array.from(matchRow.querySelectorAll('input, select')).map(el => ({
    tag: el.tagName.toLowerCase(),
    type: el.type || '',
    name: el.name || '',
    id: el.id || '',
    placeholder: el.placeholder || '',
    value: el.value || '',
    visible: el.offsetParent !== null,
    maxlength: el.maxLength || ''
  }));

  // Also look for nearby submit/save buttons
  const buttons = Array.from(matchRow.querySelectorAll('button, input[type=submit], a[class*=save], a[class*=submit]')).map(b => ({
    tag: b.tagName.toLowerCase(),
    type: b.type || '',
    text: (b.innerText || b.value || b.textContent || '').trim().slice(0, 80),
    id: b.id || '',
    className: b.className || ''
  }));

  return {
    found: true,
    rowTag: matchRow.tagName,
    rowId: matchRow.id || '',
    rowClass: matchRow.className || '',
    rowText: (matchRow.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 300),
    inputs,
    buttons
  };
}
"""

# JS: set value on an input by id/name and fire change/input events
SET_INPUT_JS = r"""
(selector, value) => {
  const el = document.querySelector(selector);
  if (!el) return { ok: false, reason: 'element not found: ' + selector };
  const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  nativeInputValueSetter.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  return { ok: true, selector, value, newValue: el.value };
}
"""

# JS: click a button/element by CSS selector
CLICK_JS = r"""
(selector) => {
  const el = document.querySelector(selector);
  if (!el) return { ok: false, reason: 'element not found: ' + selector };
  el.click();
  return { ok: true, selector, text: (el.innerText || el.value || '').trim().slice(0, 80) };
}
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Submit a SuperBru score pick via Chrome CDP.")
    p.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    p.add_argument("--pool-url", default="https://www.superbru.com/worldcup_predictor/pool_view.php?t=1296&p=13236623&g=32&view=matches")
    p.add_argument("--home-team", required=True)
    p.add_argument("--away-team", required=True)
    p.add_argument("--new-pick", required=True, help="Score in H-A format e.g. 2-0")
    p.add_argument("--settle-ms", type=int, default=8000)
    p.add_argument("--timeout-ms", type=int, default=60000)
    p.add_argument("--dry-run", action="store_true", help="Inspect DOM only, do not set values or click submit")
    p.add_argument("--inspect-only", action="store_true", help="Dump all input elements and exit")
    p.add_argument("--diagnostics-dir", default=str(DIAGNOSTICS_DIR))
    return p


def parse_pick(pick: str) -> tuple[str, str]:
    m = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", pick.strip())
    if not m:
        raise ValueError(f"Invalid pick format '{pick}' — expected H-A e.g. 2-0")
    return m.group(1), m.group(2)


def selector_for_input(inp: dict[str, Any]) -> str | None:
    if inp.get("id"):
        return f"#{inp['id']}"
    if inp.get("name"):
        return f"input[name='{inp['name']}']"
    return None


def pick_score_inputs(inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return inputs that look like score entry fields (numeric, small maxlength, visible)."""
    candidates = []
    for inp in inputs:
        if not inp.get("visible"):
            continue
        t = inp.get("type", "").lower()
        ml = inp.get("maxlength", "")
        is_numeric = t in ("number", "text", "") or "score" in inp.get("name", "").lower() or "goal" in inp.get("name", "").lower()
        is_small = str(ml) in ("1", "2", "3", "") or not ml
        if is_numeric and is_small:
            candidates.append(inp)
    return candidates


async def run(args: argparse.Namespace) -> dict[str, Any]:
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except Exception as exc:
        raise RuntimeError("pip install playwright && python -m playwright install chromium") from exc

    diag_dir = Path(args.diagnostics_dir)
    diag_dir.mkdir(parents=True, exist_ok=True)
    home_goals, away_goals = parse_pick(args.new_pick)
    slug = re.sub(r"[^a-z0-9]+", "-", f"{args.home_team}-{args.away_team}".lower()).strip("-")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    result: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "home_team": args.home_team,
        "away_team": args.away_team,
        "new_pick": args.new_pick,
        "dry_run": args.dry_run,
        "status": "pending",
    }

    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(args.cdp_url)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()

        print(f"Navigating to pool URL: {args.pool_url}")
        try:
            await page.goto(args.pool_url, wait_until="domcontentloaded", timeout=args.timeout_ms)
        except PlaywrightTimeoutError:
            print("Timeout on page load — checking current state anyway.")
        await page.wait_for_timeout(args.settle_ms)

        # Save full page HTML for diagnostics
        html = await page.content()
        (diag_dir / f"{slug}_{ts}_page.html").write_text(html, encoding="utf-8")

        if args.inspect_only:
            inputs = await page.evaluate(INSPECT_JS)
            dump = diag_dir / f"{slug}_{ts}_inputs.json"
            dump.write_text(json.dumps(inputs, indent=2), encoding="utf-8")
            print(f"Found {len(inputs)} input elements. Wrote: {dump}")
            for inp in inputs:
                if inp.get("visible"):
                    print(f"  [{inp['type']:10s}] id={inp['id']!r:30s} name={inp['name']!r:30s} val={inp['value']!r:10s}  ctx={inp['parentText'][:80]!r}")
            result["status"] = "inspect_only"
            result["inputs_found"] = len(inputs)
            await browser.close()
            return result

        # Find the match row
        print(f"Locating match row: {args.home_team} vs {args.away_team}")
        row_info = await page.evaluate(FIND_ROW_JS, args.home_team, args.away_team)
        (diag_dir / f"{slug}_{ts}_row.json").write_text(json.dumps(row_info, indent=2), encoding="utf-8")

        if not row_info.get("found"):
            result["status"] = "failed"
            result["reason"] = row_info.get("reason", "match row not found")
            print(f"ERROR: {result['reason']}")
            print(f"Diagnostics written to {diag_dir}")
            await browser.close()
            return result

        print(f"Found row ({row_info['rowTag']}): {row_info['rowText'][:120]}")
        inputs = row_info.get("inputs", [])
        buttons = row_info.get("buttons", [])
        score_inputs = pick_score_inputs(inputs)

        print(f"  {len(inputs)} inputs in row, {len(score_inputs)} look like score fields, {len(buttons)} buttons")
        for i in score_inputs:
            print(f"    input: type={i['type']!r} name={i['name']!r} id={i['id']!r} val={i['value']!r}")
        for b in buttons:
            print(f"    button: {b['text']!r} id={b['id']!r}")

        if len(score_inputs) < 2:
            result["status"] = "failed"
            result["reason"] = f"expected ≥2 score inputs in row, found {len(score_inputs)}. Run --inspect-only to debug."
            print(f"ERROR: {result['reason']}")
            await browser.close()
            return result

        # Score inputs: assume first = home goals, second = away goals
        home_sel = selector_for_input(score_inputs[0])
        away_sel = selector_for_input(score_inputs[1])

        if not home_sel or not away_sel:
            result["status"] = "failed"
            result["reason"] = "could not build CSS selectors for score inputs — no id or name attributes found"
            print(f"ERROR: {result['reason']}")
            await browser.close()
            return result

        print(f"  home selector: {home_sel}  →  {home_goals}")
        print(f"  away selector: {away_sel}  →  {away_goals}")

        if args.dry_run:
            print("DRY RUN — skipping set/submit.")
            result["status"] = "dry_run"
            result["home_selector"] = home_sel
            result["away_selector"] = away_sel
            await browser.close()
            return result

        # Set values
        r_home = await page.evaluate(SET_INPUT_JS, home_sel, home_goals)
        r_away = await page.evaluate(SET_INPUT_JS, away_sel, away_goals)
        print(f"  Set home: {r_home}")
        print(f"  Set away: {r_away}")

        if not r_home.get("ok") or not r_away.get("ok"):
            result["status"] = "failed"
            result["reason"] = f"failed to set inputs: home={r_home}, away={r_away}"
            await browser.close()
            return result

        await page.wait_for_timeout(1000)

        # Click the save/submit button in the row (first found)
        submitted = False
        for btn in buttons:
            btn_sel = None
            if btn.get("id"):
                btn_sel = f"#{btn['id']}"
            elif btn.get("className"):
                # Use first class only as a selector
                first_class = btn["className"].split()[0]
                btn_sel = f".{first_class}"
            if btn_sel:
                click_result = await page.evaluate(CLICK_JS, btn_sel)
                print(f"  Click {btn_sel}: {click_result}")
                if click_result.get("ok"):
                    submitted = True
                    break

        # If no row-level button found, try common global save selectors
        if not submitted:
            for fallback_sel in ["button[type=submit]", "input[type=submit]", "a.save-picks", "button.save", "#save-picks", "#submit-picks"]:
                click_result = await page.evaluate(CLICK_JS, fallback_sel)
                if click_result.get("ok"):
                    print(f"  Submitted via fallback: {fallback_sel}")
                    submitted = True
                    break

        if not submitted:
            result["status"] = "partial"
            result["reason"] = "values set but no submit button found — may need manual click or page uses auto-save"
            print(f"WARNING: {result['reason']}")
        else:
            await page.wait_for_timeout(2000)
            result["status"] = "submitted"

        # Screenshot for verification
        screenshot = diag_dir / f"{slug}_{ts}_after.png"
        await page.screenshot(path=str(screenshot))
        result["screenshot"] = str(screenshot)
        print(f"Screenshot: {screenshot}")

        await browser.close()

    return result


def main() -> int:
    args = build_parser().parse_args()
    import asyncio
    result = asyncio.run(run(args))
    print(json.dumps(result, indent=2, default=str))
    ok = result.get("status") in ("submitted", "dry_run", "inspect_only", "partial")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
