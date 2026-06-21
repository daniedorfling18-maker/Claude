"""
inspect_superbru_dom_ci.py

CI-friendly SuperBru DOM inspector. Launches Chrome via Playwright
(no existing CDP session required), logs in, navigates to the picks
page, and dumps all input elements plus a screenshot.

Run locally or in GitHub Actions. Credentials via args (use secrets in CI).

Usage:
    python scripts/inspect_superbru_dom_ci.py \
        --email "you@example.com" \
        --password "yourpassword" \
        --pool-url "https://www.superbru.com/worldcup_predictor/pool_view.php?..." \
        --home-team "Spain" --away-team "Saudi Arabia" \
        --out-dir outputs/pregame_checks/submit_diagnostics
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


INSPECT_INPUTS_JS = r"""
() => {
  function parentText(el, depth) {
    let node = el, text = '';
    for (let i = 0; i < depth && node; i++) {
      const t = (node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim();
      if (t.length > 5 && t.length < 500) { text = t; break; }
      node = node.parentElement;
    }
    return text.slice(0, 200);
  }
  return Array.from(document.querySelectorAll('input, select, textarea')).map(el => ({
    tag: el.tagName.toLowerCase(),
    type: el.type || '',
    name: el.name || '',
    id: el.id || '',
    className: (el.className || '').slice(0, 80),
    value: el.value || '',
    placeholder: el.placeholder || '',
    maxlength: el.maxLength > 0 ? el.maxLength : '',
    visible: !!(el.offsetParent !== null || el.getBoundingClientRect().width > 0),
    parentText: parentText(el, 6),
    rect: (() => { const r = el.getBoundingClientRect(); return {top: Math.round(r.top), left: Math.round(r.left), w: Math.round(r.width), h: Math.round(r.height)}; })()
  }));
}
"""

FIND_MATCH_ROW_JS = r"""
(homeTeam, awayTeam) => {
  function norm(s) { return (s || '').toLowerCase().replace(/[^a-z0-9]/g, ''); }
  const hn = norm(homeTeam), an = norm(awayTeam);
  const candidates = Array.from(document.querySelectorAll('tr, li, div[class*=match], div[class*=fixture], div[class*=game], section'));
  let best = null;
  for (const el of candidates) {
    const t = norm(el.innerText || el.textContent || '');
    if (t.includes(hn) && t.includes(an) && t.length < 3000) {
      if (!best || t.length < norm(best.innerText || best.textContent || '').length) best = el;
    }
  }
  if (!best) return { found: false };
  const inputs = Array.from(best.querySelectorAll('input, select')).map(el => ({
    tag: el.tagName.toLowerCase(),
    type: el.type || '',
    name: el.name || '',
    id: el.id || '',
    className: (el.className || '').slice(0, 80),
    value: el.value || '',
    placeholder: el.placeholder || '',
    maxlength: el.maxLength > 0 ? el.maxLength : '',
    visible: el.offsetParent !== null
  }));
  const buttons = Array.from(best.querySelectorAll('button, input[type=submit], a[class*=save], a[class*=submit], a[class*=btn]')).map(b => ({
    tag: b.tagName.toLowerCase(),
    type: b.type || '',
    text: (b.innerText || b.value || b.textContent || '').replace(/\s+/g,' ').trim().slice(0, 100),
    id: b.id || '',
    className: (b.className || '').slice(0, 80)
  }));
  return {
    found: true,
    rowTag: best.tagName,
    rowId: best.id || '',
    rowClass: (best.className || '').slice(0, 80),
    rowText: (best.innerText || '').replace(/\s+/g,' ').trim().slice(0, 400),
    inputs,
    buttons
  };
}
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Inspect SuperBru pick-entry DOM via Playwright (login-capable).")
    p.add_argument("--email", required=True, help="SuperBru login email")
    p.add_argument("--password", required=True, help="SuperBru login password")
    p.add_argument("--login-url", default="https://www.superbru.com/login")
    p.add_argument("--pool-url", default="https://www.superbru.com/worldcup_predictor/pool_view.php?t=1296&p=13236623&g=32&view=matches")
    p.add_argument("--home-team", default="Spain")
    p.add_argument("--away-team", default="Saudi Arabia")
    p.add_argument("--settle-ms", type=int, default=8000)
    p.add_argument("--out-dir", default="outputs/pregame_checks/submit_diagnostics")
    p.add_argument("--headless", action="store_true", default=True)
    p.add_argument("--headed", dest="headless", action="store_false")
    return p


async def run(args: argparse.Namespace) -> dict:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise RuntimeError("pip install playwright && python -m playwright install chromium")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    result: dict = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "home_team": args.home_team,
        "away_team": args.away_team,
        "status": "pending",
    }

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=args.headless)
        page = await browser.new_page()

        # ── Login ────────────────────────────────────────────────────────────
        print(f"Navigating to login: {args.login_url}")
        await page.goto(args.login_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)

        # Dismiss GDPR/cookie consent overlay if present (Quantcast CMP, InMobi Choice, etc.)
        consent_selectors = [
            "button[aria-label='CONFIRM']",
            "button[aria-label='Accept All']",
            "button[aria-label='Accept all']",
            "button[aria-label='I Accept']",
            "#qc-cmp2-container button[mode='primary']",
            ".qc-cmp2-summary-buttons button:last-child",
            "button:has-text('Accept')",
            "button:has-text('Confirm')",
            "button:has-text('I agree')",
            "button:has-text('Agree')",
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

        # Dismiss the "Your privacy choices have been saved" success modal if it appeared
        try:
            await page.click("button[aria-label='Close success modal']", timeout=2000)
            print("  Dismissed consent success modal.")
            await page.wait_for_timeout(1000)
        except Exception:
            pass

        await page.screenshot(path=str(out_dir / f"{ts}_login_page.png"))
        login_html = await page.content()
        (out_dir / f"{ts}_login_page.html").write_text(login_html, encoding="utf-8")

        # Dump inputs at this point for diagnostics
        pre_fill_inputs = await page.evaluate(INSPECT_INPUTS_JS)
        (out_dir / f"{ts}_login_inputs.json").write_text(json.dumps(pre_fill_inputs, indent=2), encoding="utf-8")
        print(f"  Inputs found after consent handling: {len(pre_fill_inputs)}")

        # Try common email/password selectors
        email_selectors = ["input[type=email]", "input[name=email]", "input[name=username]", "input[id*=email]", "input[id*=user]", "input[name=login]", "input[name=user]"]
        password_selectors = ["input[type=password]", "input[name=password]", "input[id*=pass]"]
        submit_selectors = ["button[type=submit]", "input[type=submit]", "button:has-text('Log')", "button:has-text('Sign')", "a:has-text('Log')"]

        email_filled = False
        for sel in email_selectors:
            try:
                await page.fill(sel, args.email, timeout=2000)
                print(f"  Filled email via: {sel}")
                email_filled = True
                break
            except Exception:
                continue

        pass_filled = False
        for sel in password_selectors:
            try:
                await page.fill(sel, args.password, timeout=2000)
                print(f"  Filled password via: {sel}")
                pass_filled = True
                break
            except Exception:
                continue

        if not email_filled or not pass_filled:
            await page.screenshot(path=str(out_dir / f"{ts}_login_screenshot.png"))
            result["status"] = "login_form_not_found"
            result["reason"] = (
                f"email_filled={email_filled} pass_filled={pass_filled}. "
                f"Check {ts}_login_inputs.json ({len(pre_fill_inputs)} inputs found) and "
                f"{ts}_login_page.html for the actual DOM."
            )
            print(f"ERROR: {result['reason']}")
            await browser.close()
            return result

        submitted = False
        for sel in submit_selectors:
            try:
                await page.click(sel, timeout=3000)
                print(f"  Clicked submit via: {sel}")
                submitted = True
                break
            except Exception:
                continue

        if not submitted:
            await page.keyboard.press("Enter")
            print("  Pressed Enter to submit login form.")

        await page.wait_for_timeout(4000)
        await page.screenshot(path=str(out_dir / f"{ts}_after_login.png"))
        current_url = page.url
        print(f"  Post-login URL: {current_url}")

        if "login" in current_url.lower():
            result["status"] = "login_failed"
            result["reason"] = f"Still on login page after submit: {current_url}"
            print(f"ERROR: {result['reason']}")
            await browser.close()
            return result

        print(f"Logged in. Navigating to pool: {args.pool_url}")
        await page.goto(args.pool_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(args.settle_ms)

        await page.screenshot(path=str(out_dir / f"{ts}_pool_page.png"))
        pool_html = await page.content()
        (out_dir / f"{ts}_pool_page.html").write_text(pool_html, encoding="utf-8")
        print(f"  Pool URL: {page.url}")

        # ── Dump all inputs ──────────────────────────────────────────────────
        all_inputs = await page.evaluate(INSPECT_INPUTS_JS)
        inputs_path = out_dir / f"{ts}_all_inputs.json"
        inputs_path.write_text(json.dumps(all_inputs, indent=2), encoding="utf-8")
        print(f"\nAll inputs on pool page ({len(all_inputs)} total):")
        visible = [i for i in all_inputs if i.get("visible")]
        print(f"  {len(visible)} visible:")
        for inp in visible:
            print(f"  [{inp['type']:10s}] id={inp['id']!r:30s} name={inp['name']!r:30s} "
                  f"maxlen={str(inp['maxlength']):4s} val={inp['value']!r:8s}  ctx={inp['parentText'][:80]!r}")

        # ── Find match row ───────────────────────────────────────────────────
        print(f"\nSearching for match row: {args.home_team} vs {args.away_team}")
        row = await page.evaluate(FIND_MATCH_ROW_JS, args.home_team, args.away_team)
        row_path = out_dir / f"{ts}_match_row.json"
        row_path.write_text(json.dumps(row, indent=2), encoding="utf-8")

        if row.get("found"):
            print(f"  Found row ({row['rowTag']} id={row['rowId']!r} class={row['rowClass']!r})")
            print(f"  Row text: {row['rowText'][:200]!r}")
            print(f"  Inputs in row ({len(row['inputs'])}):")
            for inp in row["inputs"]:
                print(f"    [{inp['type']:10s}] id={inp['id']!r:30s} name={inp['name']!r:25s} visible={inp['visible']}")
            print(f"  Buttons in row ({len(row['buttons'])}):")
            for btn in row["buttons"]:
                print(f"    [{btn['tag']:8s}] id={btn['id']!r:20s} text={btn['text']!r}")
        else:
            print(f"  Match row NOT found. Check {row_path} and pool page HTML.")

        result.update({
            "status": "ok",
            "pool_url_landed": page.url,
            "all_inputs_count": len(all_inputs),
            "visible_inputs_count": len(visible),
            "match_row_found": row.get("found", False),
            "match_row_input_count": len(row.get("inputs", [])),
            "match_row_button_count": len(row.get("buttons", [])),
            "out_dir": str(out_dir),
            "files": {
                "pool_html": str(out_dir / f"{ts}_pool_page.html"),
                "all_inputs": str(inputs_path),
                "match_row": str(row_path),
                "screenshot": str(out_dir / f"{ts}_pool_page.png"),
            },
        })

        await browser.close()

    return result


def main() -> int:
    args = build_parser().parse_args()
    import asyncio
    result = asyncio.run(run(args))
    print("\n" + json.dumps(result, indent=2, default=str))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
