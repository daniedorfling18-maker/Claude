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

    # CI / headless launch mode (no existing browser required):
    python scripts/submit_superbru_pick_cdp.py \
        --launch --headless \
        --email you@example.com --password secret \
        --home-team "Spain" --away-team "Saudi Arabia" \
        --new-pick "2-1" --dry-run

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

# JS: find a match row by canonical team name or any known alias, then locate score inputs.
FIND_ROW_JS = r"""
([homeTeam, awayTeam]) => {
  const TEAM_ALIASES = {
    algeria: ['algeria','alg','dza'],
    argentina: ['argentina','arg'],
    australia: ['australia','aus'],
    austria: ['austria','aut'],
    belgium: ['belgium','bel'],
    bosniaherzegovina: ['bosniaandherzegovina','bosniaherzegovina','bosnia','bih','bhi'],
    brazil: ['brazil','bra'],
    canada: ['canada','can'],
    capeverde: ['capeverde','caboverde','cpv'],
    colombia: ['colombia','col'],
    croatia: ['croatia','cro'],
    curacao: ['curacao','cur','cuw'],
    czechia: ['czechia','czechrepublic','cze'],
    drcongo: ['drcongo','drc','cod','congodr','democraticrepublicofthecongo','demrepcongo'],
    ecuador: ['ecuador','ecu'],
    egypt: ['egypt','egy'],
    england: ['england','eng'],
    france: ['france','fra'],
    germany: ['germany','ger','deutschland'],
    ghana: ['ghana','gha'],
    haiti: ['haiti','hti','hai'],
    iran: ['iran','iriran','iri','irn'],
    iraq: ['iraq','irq'],
    ivorycoast: ['ivorycoast','cotedivoire','civ','ci'],
    japan: ['japan','jpn'],
    jordan: ['jordan','jor'],
    mexico: ['mexico','mex'],
    morocco: ['morocco','mar'],
    netherlands: ['netherlands','holland','ned','nld'],
    newzealand: ['newzealand','nzl'],
    norway: ['norway','nor'],
    panama: ['panama','pan'],
    paraguay: ['paraguay','par'],
    portugal: ['portugal','por'],
    qatar: ['qatar','qat'],
    saudiarabia: ['saudiarabia','ksa','sau'],
    scotland: ['scotland','sco'],
    senegal: ['senegal','sen'],
    southafrica: ['southafrica','rsa','zaf'],
    southkorea: ['southkorea','korearepublic','korea','kor'],
    spain: ['spain','esp'],
    sweden: ['sweden','swe'],
    switzerland: ['switzerland','sui','che'],
    tunisia: ['tunisia','tun'],
    turkey: ['turkey','turkiye','tur'],
    unitedstates: ['unitedstates','unitedstatesofamerica','usa','us','america'],
    uruguay: ['uruguay','uru'],
    uzbekistan: ['uzbekistan','uzb'],
  };
  function norm(s) {
    return (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]/g, '');
  }
  function variants(team) {
    const n = norm(team);
    for (const [canonical, aliases] of Object.entries(TEAM_ALIASES)) {
      if (canonical === n || aliases.includes(n)) {
        return Array.from(new Set([n, canonical, ...aliases].filter(Boolean)));
      }
    }
    return [n].filter(Boolean);
  }
  function containsAny(text, candidates) {
    return candidates.some(candidate => candidate && text.includes(candidate));
  }

  const homeVariants = variants(homeTeam);
  const awayVariants = variants(awayTeam);

  const all = Array.from(document.querySelectorAll('tr, li, div, section, article'));
  let matchRow = null;
  for (const el of all) {
    const t = norm(el.innerText || el.textContent || '');
    if (containsAny(t, homeVariants) && containsAny(t, awayVariants) && t.length < 2000) {
      if (!matchRow || (el.innerText || '').length < (matchRow.innerText || '').length) {
        matchRow = el;
      }
    }
  }
  if (!matchRow) {
    return {
      found: false,
      reason: 'no element contains both team names or aliases',
      homeVariants,
      awayVariants,
    };
  }

  const inputs = Array.from(matchRow.querySelectorAll('input, select')).map(el => ({
    tag: el.tagName.toLowerCase(),
    type: el.type || '',
    name: el.name || '',
    id: el.id || '',
    className: el.className || '',
    placeholder: el.placeholder || '',
    value: el.value || '',
    visible: el.offsetParent !== null,
    maxlength: el.maxLength || ''
  }));

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
    matchedHomeAliases: homeVariants,
    matchedAwayAliases: awayVariants,
    inputs,
    buttons
  };
}
"""

# JS: set value on an input by CSS selector and fire events to trigger auto-save
SET_INPUT_JS = r"""
([selector, value]) => {
  const el = document.querySelector(selector);
  if (!el) return { ok: false, reason: 'element not found: ' + selector };
  const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  nativeInputValueSetter.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  el.dispatchEvent(new Event('blur', { bubbles: true }));
  return { ok: true, selector, value, newValue: el.value };
}
"""

# JS: force SuperBru's own soccer save path and wait for its server confirmation.
#
# Root cause of the 2026-07-07/08 Spain-Belgium failures: SuperBru saves soccer
# picks from a jQuery change handler that initSoccer() binds DIRECTLY to the
# inputs present at bind time (no event delegation). Setting a value
# programmatically fires "change" into a void whenever that binding is missing,
# so nothing ever calls ajax/save_pick.php and the pick silently evaporates on
# reload. Instead of hoping the handler exists, call the exact function it
# would call — brupicks.soccer.processPick — and then poll the state that
# savePick's success callback updates ($("#soccer-picker<id>").data("bru-existing")
# becomes "<left>-<right>") until the server has really accepted the pick.
# processPick validates and dedupes first, so this is a no-op when the pick is
# already saved.
FORCE_SAVE_AND_CONFIRM_JS = r"""
async ([selector, homeGoals, awayGoals, timeoutMs]) => {
  const el = document.querySelector(selector);
  if (!el) return { supported: false, reason: 'input not found: ' + selector };
  const gameNode = el.closest('[data-bru-game-id]');
  const gameId = gameNode ? parseInt(gameNode.getAttribute('data-bru-game-id'), 10) : NaN;
  const expected = parseInt(homeGoals, 10) + '-' + parseInt(awayGoals, 10);
  const jq = window.jQuery || window.$;
  const bp = window.brupicks;
  if (!Number.isFinite(gameId) || !jq || !bp || !bp.soccer || typeof bp.soccer.processPick !== 'function') {
    return { supported: false, reason: 'brupicks soccer save API unavailable', gameId: String(gameId) };
  }
  const picker = jq('#soccer-picker' + gameId);
  const readExisting = () => String(picker.data('bru-existing') || '');
  const readStatus = () => ((jq('#picker' + gameId + ' .pick-status').text() || '').replace(/\s+/g, ' ').trim());
  if (readExisting() === expected) {
    return { supported: true, confirmed: true, alreadySaved: true, gameId, expected, existing: expected, statusText: readStatus() };
  }
  bp.soccer.processPick(gameId, picker.data('bru-max-soccer-pick'));
  const deadline = Date.now() + (parseInt(timeoutMs, 10) || 15000);
  while (Date.now() < deadline) {
    if (readExisting() === expected) {
      return { supported: true, confirmed: true, gameId, expected, existing: readExisting(), statusText: readStatus() };
    }
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  return { supported: true, confirmed: false, gameId, expected, existing: readExisting(), statusText: readStatus() };
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

# JS: click the subtab using canonical team name or aliases before finding the row.
CLICK_SUBTAB_JS = r"""
([homeTeam, awayTeam]) => {
  const TEAM_ALIASES = {
    algeria: ['algeria','alg','dza'], argentina: ['argentina','arg'], australia: ['australia','aus'], austria: ['austria','aut'],
    belgium: ['belgium','bel'], bosniaherzegovina: ['bosniaandherzegovina','bosniaherzegovina','bosnia','bih','bhi'], brazil: ['brazil','bra'], canada: ['canada','can'],
    capeverde: ['capeverde','caboverde','cpv'], colombia: ['colombia','col'], croatia: ['croatia','cro'], curacao: ['curacao','cur','cuw'],
    czechia: ['czechia','czechrepublic','cze'], drcongo: ['drcongo','drc','cod','congodr','democraticrepublicofthecongo','demrepcongo'], ecuador: ['ecuador','ecu'], egypt: ['egypt','egy'],
    england: ['england','eng'], france: ['france','fra'], germany: ['germany','ger','deutschland'], ghana: ['ghana','gha'], haiti: ['haiti','hti','hai'],
    iran: ['iran','iriran','iri','irn'], iraq: ['iraq','irq'], ivorycoast: ['ivorycoast','cotedivoire','civ','ci'], japan: ['japan','jpn'], jordan: ['jordan','jor'],
    mexico: ['mexico','mex'], morocco: ['morocco','mar'], netherlands: ['netherlands','holland','ned','nld'], newzealand: ['newzealand','nzl'], norway: ['norway','nor'],
    panama: ['panama','pan'], paraguay: ['paraguay','par'], portugal: ['portugal','por'], qatar: ['qatar','qat'], saudiarabia: ['saudiarabia','ksa','sau'],
    scotland: ['scotland','sco'], senegal: ['senegal','sen'], southafrica: ['southafrica','rsa','zaf'], southkorea: ['southkorea','korearepublic','korea','kor'],
    spain: ['spain','esp'], sweden: ['sweden','swe'], switzerland: ['switzerland','sui','che'], tunisia: ['tunisia','tun'], turkey: ['turkey','turkiye','tur'],
    unitedstates: ['unitedstates','unitedstatesofamerica','usa','us','america'], uruguay: ['uruguay','uru'], uzbekistan: ['uzbekistan','uzb'],
  };
  function norm(s) {
    return (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]/g, '');
  }
  function variants(team) {
    const n = norm(team);
    for (const [canonical, aliases] of Object.entries(TEAM_ALIASES)) {
      if (canonical === n || aliases.includes(n)) return Array.from(new Set([n, canonical, ...aliases].filter(Boolean)));
    }
    return [n].filter(Boolean);
  }
  function containsAny(text, candidates) {
    return candidates.some(candidate => candidate && text.includes(candidate));
  }

  const homeVariants = variants(homeTeam);
  const awayVariants = variants(awayTeam);
  const controls = Array.from(document.querySelectorAll('[data-brutip][data-bru-tab]'));

  for (const c of controls) {
    const tip = norm(c.getAttribute('data-brutip') || '');
    if (containsAny(tip, homeVariants) && containsAny(tip, awayVariants)) {
      c.click();
      return c.getAttribute('data-bru-tab') + ': ' + c.getAttribute('data-brutip');
    }
  }

  for (const c of controls) {
    const tip = norm(c.getAttribute('data-brutip') || '');
    if (containsAny(tip, homeVariants) || containsAny(tip, awayVariants)) {
      c.click();
      return c.getAttribute('data-bru-tab') + ': ' + c.getAttribute('data-brutip');
    }
  }
  return null;
}
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Submit a SuperBru score pick via Chrome CDP or headless launch.")
    p.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    p.add_argument("--pool-url", default="https://www.superbru.com/worldcup_predictor/pool_view.php?t=1296&p=13236623&g=37&view=matches")
    p.add_argument("--home-team", required=True)
    p.add_argument("--away-team", required=True)
    p.add_argument("--new-pick", required=True, help="Score in H-A format e.g. 2-0")
    p.add_argument("--settle-ms", type=int, default=8000)
    p.add_argument("--timeout-ms", type=int, default=60000)
    p.add_argument("--dry-run", action="store_true", help="Inspect DOM only, do not set values or click submit")
    p.add_argument("--inspect-only", action="store_true", help="Dump all input elements and exit")
    p.add_argument("--diagnostics-dir", default=str(DIAGNOSTICS_DIR))
    # Launch mode: use playwright launch() + login instead of attaching to existing CDP session
    p.add_argument("--launch", action="store_true", help="Launch headless Chromium and log in (CI mode, no existing browser required)")
    p.add_argument("--email", default="", help="SuperBru email (required for --launch)")
    p.add_argument("--password", default="", help="SuperBru password (required for --launch)")
    p.add_argument("--login-url", default="https://www.superbru.com/login")
    p.add_argument("--headless", action="store_true", default=True)
    p.add_argument("--headed", dest="headless", action="store_false")
    return p


def parse_pick(pick: str) -> tuple[str, str]:
    m = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", pick.strip())
    if not m:
        raise ValueError(f"Invalid pick format '{pick}' — expected H-A e.g. 2-0")
    return m.group(1), m.group(2)


def selector_for_input(inp: dict[str, Any], position: int = 0) -> str | None:
    if inp.get("id"):
        return f"#{inp['id']}"
    if inp.get("name"):
        return f"input[name='{inp['name']}']"
    cls = inp.get("className", "")
    if "soccer-left-score" in cls:
        return "input.soccer-left-score"
    if "soccer-right-score" in cls:
        return "input.soccer-right-score"
    # Fallback: nth editable-dropdown in page order
    if "editable-dropdown" in cls:
        return f"input.editable-dropdown:nth-of-type({position + 1})"
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


def _normalise_goal_value(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text


def score_values_from_row(row_info: dict[str, Any]) -> list[str]:
    """Return the visible score values from a SuperBru row inspection."""
    inputs = row_info.get("inputs", []) if isinstance(row_info, dict) else []
    return [_normalise_goal_value(inp.get("value")) for inp in pick_score_inputs(inputs)]


def saved_score_matches(row_info: dict[str, Any], home_goals: str, away_goals: str) -> bool:
    values = score_values_from_row(row_info)
    if len(values) < 2:
        return False
    return values[0] == str(home_goals) and values[1] == str(away_goals)


async def verify_saved_pick(
    page: Any,
    args: argparse.Namespace,
    diag_dir: Path,
    slug: str,
    ts: str,
    home_goals: str,
    away_goals: str,
) -> dict[str, Any]:
    """Reload SuperBru and prove the submitted score persisted on the site."""
    verification: dict[str, Any] = {
        "ok": False,
        "expected_pick": f"{home_goals}-{away_goals}",
        "method": "reload_and_read_score_inputs",
    }
    try:
        await page.wait_for_timeout(2500)
        try:
            await page.reload(wait_until="domcontentloaded", timeout=args.timeout_ms)
        except Exception as exc:
            verification["reload_warning"] = f"{type(exc).__name__}: {exc}"
            print("Warning: reload after submit did not complete; checking current page state anyway.")
        await page.wait_for_timeout(args.settle_ms)
        subtab_clicked = await page.evaluate(CLICK_SUBTAB_JS, [args.home_team, args.away_team])
        if subtab_clicked:
            verification["subtab_clicked"] = subtab_clicked
            await page.wait_for_timeout(4000)
        row_info = await page.evaluate(FIND_ROW_JS, [args.home_team, args.away_team])
        verify_path = diag_dir / f"{slug}_{ts}_verify_row.json"
        verify_path.write_text(json.dumps(row_info, indent=2), encoding="utf-8")
        values = score_values_from_row(row_info)
        verification.update(
            {
                "row_found": bool(row_info.get("found")),
                "saved_pick": "-".join(values[:2]) if len(values) >= 2 else "",
                "score_values": values,
                "row_path": str(verify_path),
                "ok": saved_score_matches(row_info, home_goals, away_goals),
            }
        )
    except Exception as exc:  # pragma: no cover - defensive live-browser path
        verification.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    return verification


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
        if args.launch:
            if not args.email or not args.password:
                raise ValueError("--email and --password are required in --launch mode")
            browser = await pw.chromium.launch(headless=args.headless)
            page = await browser.new_page()

            # Login flow (mirrors inspect_superbru_dom_ci.py)
            print(f"Navigating to login: {args.login_url}")
            await page.goto(args.login_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)

            consent_selectors = [
                "button[aria-label='CONFIRM']", "button[aria-label='Accept All']",
                "button[aria-label='Accept all']", "button[aria-label='I Accept']",
                "#qc-cmp2-container button[mode='primary']",
                ".qc-cmp2-summary-buttons button:last-child",
                "button:has-text('Accept')", "button:has-text('Confirm')",
                "button:has-text('I agree')", "button:has-text('Agree')",
                "#accept-cookie-policy",
            ]
            for sel in consent_selectors:
                try:
                    await page.click(sel, timeout=2000)
                    print(f"  Dismissed consent overlay via: {sel}")
                    await page.wait_for_timeout(2500)
                    break
                except Exception:
                    continue
            try:
                await page.click("button[aria-label='Close success modal']", timeout=2000)
                await page.wait_for_timeout(1000)
            except Exception:
                pass

            email_selectors = [
                "input[type=email]",
                "input[name=email]",
                "input[name=username]",
                "input[id*=email]",
                "input[id*=user]",
            ]
            password_selectors = ["input[type=password]", "input[name=password]", "input[id*=pass]"]
            submit_selectors = [
                "button[type=submit]",
                "input[type=submit]",
                "button:has-text('Log')",
                "button:has-text('Sign')",
            ]

            for attempt in range(2):
                if attempt:
                    print("  Login still showing after first submit; retrying once after a longer settle.")
                    await page.wait_for_timeout(2000)
                for sel in email_selectors:
                    try:
                        await page.fill(sel, args.email, timeout=2500)
                        print(f"  Filled email via: {sel}")
                        break
                    except Exception:
                        continue
                for sel in password_selectors:
                    try:
                        await page.fill(sel, args.password, timeout=2500)
                        print(f"  Filled password via: {sel}")
                        break
                    except Exception:
                        continue
                submitted_login = False
                for sel in submit_selectors:
                    try:
                        await page.click(sel, timeout=3500)
                        print(f"  Submitted login via: {sel}")
                        submitted_login = True
                        break
                    except Exception:
                        continue
                if not submitted_login:
                    await page.keyboard.press("Enter")
                    print("  Submitted login via Enter")
                try:
                    await page.wait_for_url(lambda url: "login" not in str(url).lower(), timeout=10000)
                except Exception:
                    await page.wait_for_timeout(3000)
                if "login" not in page.url.lower():
                    break
            if "login" in page.url.lower():
                result["status"] = "login_failed"
                result["reason"] = f"Still on login page: {page.url}"
                print(f"ERROR: {result['reason']}")
                await browser.close()
                return result
            print(f"  Logged in. URL: {page.url}")
        else:
            browser = await pw.chromium.connect_over_cdp(args.cdp_url)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = context.pages[0] if context.pages else await context.new_page()

        print(f"Navigating to pool URL: {args.pool_url}")
        try:
            await page.goto(args.pool_url, wait_until="domcontentloaded", timeout=args.timeout_ms)
        except PlaywrightTimeoutError:
            print("Timeout on page load — checking current state anyway.")
        await page.wait_for_timeout(args.settle_ms)

        # Click the matching game subtab to trigger AJAX load of the pick form
        subtab_clicked = await page.evaluate(CLICK_SUBTAB_JS, [args.home_team, args.away_team])
        if subtab_clicked:
            print(f"Clicked game subtab: {subtab_clicked}")
            await page.wait_for_timeout(6000)
        else:
            print("No matching game subtab found; using current active tab.")

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
        row_info = await page.evaluate(FIND_ROW_JS, [args.home_team, args.away_team])
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
            print(f"    input: type={i['type']!r} name={i['name']!r} id={i['id']!r} cls={i.get('className','')!r} val={i['value']!r}")
        for b in buttons:
            print(f"    button: {b['text']!r} id={b['id']!r}")

        if len(score_inputs) < 2:
            result["status"] = "failed"
            result["reason"] = f"expected ≥2 score inputs in row, found {len(score_inputs)}. Run --inspect-only to debug."
            print(f"ERROR: {result['reason']}")
            await browser.close()
            return result

        # Score inputs: assume first = home goals, second = away goals
        home_sel = selector_for_input(score_inputs[0], position=0)
        away_sel = selector_for_input(score_inputs[1], position=1)

        if not home_sel or not away_sel:
            result["status"] = "failed"
            result["reason"] = "could not build CSS selectors for score inputs — no id, name, or recognised class found"
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
        r_home = await page.evaluate(SET_INPUT_JS, [home_sel, home_goals])
        r_away = await page.evaluate(SET_INPUT_JS, [away_sel, away_goals])
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

        # Do not trust the change-handler auto-save: drive SuperBru's own
        # processPick -> savePick path and wait for the server-confirmed state.
        confirmation = await page.evaluate(
            FORCE_SAVE_AND_CONFIRM_JS, [home_sel, str(home_goals), str(away_goals), 15000]
        )
        result["save_confirmation"] = confirmation
        if confirmation.get("supported"):
            if confirmation.get("confirmed"):
                print(f"  SuperBru confirmed save: existing={confirmation.get('existing')!r}")
            else:
                print(
                    "  SuperBru did NOT confirm the save within 15s: "
                    f"existing={confirmation.get('existing')!r} status={confirmation.get('statusText')!r}"
                )
        else:
            print(f"  SuperBru soccer save API unavailable ({confirmation.get('reason')}); relying on auto-save.")
            if not submitted:
                await page.wait_for_timeout(3000)
                print("Auto-save: no submit button needed (SuperBru saves on blur/change).")
            else:
                await page.wait_for_timeout(2000)

        verification = await verify_saved_pick(
            page,
            args,
            diag_dir,
            slug,
            ts,
            home_goals,
            away_goals,
        )
        result["post_submit_verification"] = verification
        if not verification.get("ok"):
            result["status"] = "failed"
            result["reason"] = (
                "post-submit verification failed: expected "
                f"{verification.get('expected_pick')} but SuperBru read back "
                f"{verification.get('saved_pick') or 'no saved score'}"
            )
            print(f"ERROR: {result['reason']}")
        else:
            result["status"] = "submitted"
            print(f"Verified SuperBru saved pick: {verification.get('saved_pick')}")

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
