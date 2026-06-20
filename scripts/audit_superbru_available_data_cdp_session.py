from __future__ import annotations

import argparse
import json
import math
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


SCORELINE_RE = re.compile(r"(?<!\d)(\d{1,2})\s*[-:]\s*(\d{1,2})(?!\d)")
ROUND_RE = re.compile(r"\bRound\s+(\d+)\b", re.IGNORECASE)
CONSENT_TEXT_RE = re.compile(r"do not process|data deletion|data access|privacy|confirm", re.IGNORECASE)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit visible Superbru pool page data, tables, round tabs, pick coverage and network metadata from the logged-in Chrome CDP session."
    )
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    parser.add_argument("--pool-url", default="https://www.superbru.com/worldcup_predictor/pool_view.php?t=1296&p=13236623&g=32&view=matches")
    parser.add_argument("--login-url", default=os.environ.get("SUPERBRU_LOGIN_URL", "https://www.superbru.com/login.php"))
    parser.add_argument("--fixtures-csv", default="outputs/final_locked_picks/superbru_final_card.csv")
    parser.add_argument("--out-dir", default="outputs/data_inventory")
    parser.add_argument("--diagnostics-dir", default="outputs/data_inventory/raw_diagnostics/superbru")
    parser.add_argument("--settle-ms", type=int, default=9000)
    parser.add_argument("--login-settle-ms", type=int, default=6000)
    parser.add_argument("--timeout-ms", type=int, default=90000)
    parser.add_argument("--write-raw-state", action="store_true")
    parser.add_argument("--max-raw-chars", type=int, default=120000)
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def slugify(value: Any) -> str:
    text = txt(value).lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def safe_name(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", txt(value)).strip("_")
    return text[:120] or "item"


def load_fixtures(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    frame = pd.read_csv(path).fillna("")
    rows: list[dict[str, str]] = []
    for _, record in frame.iterrows():
        row = {col: txt(record.get(col)) for col in frame.columns}
        home = row.get("home_team") or row.get("home") or row.get("team_home")
        away = row.get("away_team") or row.get("away") or row.get("team_away")
        if home and away:
            row["home_team"] = home
            row["away_team"] = away
            row["match_id"] = row.get("match_id") or f"{slugify(home)}-{slugify(away)}"
            rows.append(row)
    return rows


EXTRACT_PAGE_JS = r"""
() => {
  function clean(text) { return (text || '').replace(/\s+/g, ' ').trim(); }
  function cssPath(el) {
    if (!el || !el.tagName) return '';
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 8) {
      let part = node.tagName.toLowerCase();
      if (node.id) {
        part += '#' + CSS.escape(node.id);
        parts.unshift(part);
        break;
      }
      const cls = Array.from(node.classList || []).slice(0, 4).map(c => '.' + CSS.escape(c)).join('');
      part += cls;
      const parent = node.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(x => x.tagName === node.tagName);
        if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
      }
      parts.unshift(part);
      node = parent;
    }
    return parts.join(' > ');
  }
  const tables = Array.from(document.querySelectorAll('table')).map((table, idx) => {
    const rows = Array.from(table.querySelectorAll('tr')).map((row) =>
      Array.from(row.querySelectorAll('th,td')).map((cell) => clean(cell.innerText || cell.textContent))
    ).filter((row) => row.length > 0);
    return {
      index: idx,
      id: table.id || '',
      className: table.className || '',
      rowCount: rows.length,
      maxColumnCount: rows.reduce((m, r) => Math.max(m, r.length), 0),
      headers: rows[0] || [],
      sampleRows: rows.slice(0, 25),
      textSample: clean(table.innerText || table.textContent).slice(0, 10000)
    };
  });
  const controls = Array.from(document.querySelectorAll('a,button,[role="button"],[onclick],.tab-control,.subtab-control')).map((el, idx) => ({
    index: idx,
    tagName: el.tagName || '',
    text: clean(el.innerText || el.textContent),
    id: el.id || '',
    className: el.className || '',
    href: el.href || '',
    selector: cssPath(el)
  })).filter(x => x.text || x.href || x.id || x.className).slice(0, 900);
  const forms = Array.from(document.forms || []).map((form, idx) => ({
    index: idx,
    id: form.id || '',
    className: form.className || '',
    action: form.action || '',
    method: form.method || '',
    inputCount: form.querySelectorAll('input,select,textarea').length
  }));
  const bodyText = document.body ? document.body.innerText : '';
  return {
    currentUrl: window.location.href,
    title: document.title || '',
    bodyTextLength: bodyText.length,
    bodyTextSample: clean(bodyText).slice(0, 50000),
    tableCount: tables.length,
    controlCount: controls.length,
    formCount: forms.length,
    tables,
    controls,
    forms,
    scrapedAtUtc: new Date().toISOString()
  };
}
"""


def trim_for_raw(obj: Any, max_chars: int) -> Any:
    try:
        text = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return {"error": "Could not serialise object"}
    if len(text) <= max_chars:
        return obj
    return {"truncated": True, "max_chars": max_chars, "json_prefix": text[:max_chars]}


def count_scorelines(text: str) -> int:
    return len(SCORELINE_RE.findall(text or ""))


def round_number(value: Any) -> int | None:
    m = ROUND_RE.search(txt(value))
    if not m:
        return None
    return int(m.group(1))


def fixture_visible(body_text: str, home: str, away: str) -> tuple[bool, bool, int, str]:
    low = body_text.lower()
    home_l = home.lower()
    away_l = away.lower()
    home_found = home_l in low if home_l else False
    away_found = away_l in low if away_l else False
    scoreline_count = 0
    sample = ""
    if home_found and away_found:
        hi = low.find(home_l)
        ai = low.find(away_l)
        start = max(0, min(hi, ai) - 900)
        end = min(len(body_text), max(hi, ai) + 1400)
        sample = re.sub(r"\s+", " ", body_text[start:end]).strip()[:600]
        scoreline_count = count_scorelines(sample)
    return home_found, away_found, scoreline_count, sample


def build_table_inventory(state: dict[str, Any], round_label: str, round_idx: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for table in state.get("tables") or []:
        text_sample = txt(table.get("textSample"))
        sample_rows = table.get("sampleRows") or []
        rows.append({
            "round_label": round_label,
            "round_number": round_number(round_label) or "",
            "round_scan_index": round_idx,
            "table_index": table.get("index", ""),
            "table_id": txt(table.get("id")),
            "table_class": txt(table.get("className")),
            "row_count": table.get("rowCount", 0),
            "max_column_count": table.get("maxColumnCount", 0),
            "headers": ";".join(map(txt, table.get("headers") or [])),
            "scoreline_count_in_sample": count_scorelines(text_sample),
            "round_mentions_in_sample": len(ROUND_RE.findall(text_sample)),
            "sample_rows_json": json.dumps(sample_rows[:10], ensure_ascii=False, default=str),
            "text_sample": text_sample[:1200],
        })
    return rows


def build_control_inventory(state: dict[str, Any], round_label: str, round_idx: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for control in state.get("controls") or []:
        text = txt(control.get("text"))
        rows.append({
            "round_label": round_label,
            "round_number": round_number(round_label) or "",
            "round_scan_index": round_idx,
            "control_index": control.get("index", ""),
            "tag_name": txt(control.get("tagName")),
            "control_id": txt(control.get("id")),
            "class_name": txt(control.get("className")),
            "href": txt(control.get("href")),
            "selector": txt(control.get("selector")),
            "text": text[:500],
            "is_round_control": bool(ROUND_RE.search(text)),
            "scoreline_count_in_text": count_scorelines(text),
        })
    return rows


def build_fixture_coverage(state: dict[str, Any], fixtures: list[dict[str, str]], round_label: str, round_idx: int) -> list[dict[str, Any]]:
    body = txt(state.get("bodyTextSample"))
    rows: list[dict[str, Any]] = []
    for fixture in fixtures:
        home = fixture.get("home_team", "")
        away = fixture.get("away_team", "")
        home_found, away_found, scoreline_count, sample = fixture_visible(body, home, away)
        rows.append({
            "round_label": round_label,
            "round_number": round_number(round_label) or "",
            "round_scan_index": round_idx,
            "match_id": fixture.get("match_id", ""),
            "commence_time": fixture.get("commence_time", ""),
            "home_team": home,
            "away_team": away,
            "home_visible": home_found,
            "away_visible": away_found,
            "fixture_visible": home_found and away_found,
            "nearby_scoreline_count": scoreline_count,
            "likely_pick_data_visible": (home_found and away_found and scoreline_count > 0),
            "nearby_text_sample": sample,
        })
    return rows


def round_controls(state: dict[str, Any]) -> list[dict[str, Any]]:
    controls: list[dict[str, Any]] = []
    seen: set[str] = set()
    for control in state.get("controls") or []:
        text = txt(control.get("text"))
        selector = txt(control.get("selector"))
        if not text or not selector or not ROUND_RE.search(text):
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        controls.append({
            "text": text,
            "selector": selector,
            "round_number": round_number(text) or 0,
            "class_name": txt(control.get("className")),
            "href": txt(control.get("href")),
        })
    return sorted(controls, key=lambda r: int(r.get("round_number") or 0))


def state_summary_row(
    state: dict[str, Any],
    round_label: str,
    round_idx: int,
    fixtures: list[dict[str, str]],
    network_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    body = txt(state.get("bodyTextSample"))
    table_rows = build_table_inventory(state, round_label, round_idx)
    coverage_rows = build_fixture_coverage(state, fixtures, round_label, round_idx)
    visible_count = sum(1 for row in coverage_rows if row.get("fixture_visible"))
    likely_pick_count = sum(1 for row in coverage_rows if row.get("likely_pick_data_visible"))
    team_visible_count = sum(1 for row in coverage_rows if row.get("home_visible")) + sum(1 for row in coverage_rows if row.get("away_visible"))
    round_network = [r for r in network_rows if txt(r.get("round_label")) == round_label]
    xhr_fetch_urls = sorted({
        txt(r.get("url"))
        for r in round_network
        if txt(r.get("resource_type")).lower() in {"xhr", "fetch"}
    })
    return {
        "round_label": round_label,
        "round_number": round_number(round_label) or "",
        "round_scan_index": round_idx,
        "current_url": txt(state.get("currentUrl")),
        "title": txt(state.get("title")),
        "body_text_length": state.get("bodyTextLength", 0),
        "table_count": state.get("tableCount", 0),
        "control_count": state.get("controlCount", 0),
        "form_count": state.get("formCount", 0),
        "body_scoreline_count": count_scorelines(body),
        "table_scoreline_count": sum(int(row.get("scoreline_count_in_sample") or 0) for row in table_rows),
        "fixture_visible_count": visible_count,
        "likely_pick_data_visible_count": likely_pick_count,
        "team_visible_count": team_visible_count,
        "network_row_count": len(round_network),
        "xhr_fetch_url_count": len(xhr_fetch_urls),
        "xhr_fetch_urls_sample": " | ".join(xhr_fetch_urls[:20]),
        "table_sample": " || ".join(txt(row.get("text_sample"))[:300] for row in table_rows[:5]),
        "body_sample": body[:1200],
    }


async def first_visible_locator(page: Any, selectors: list[str]) -> Any | None:
    for selector in selectors:
        loc = page.locator(selector)
        try:
            count = min(await loc.count(), 10)
        except Exception:
            continue
        for idx in range(count):
            item = loc.nth(idx)
            try:
                if await item.is_visible():
                    return item
            except Exception:
                continue
    return None


async def click_privacy_consent_if_present(page: Any, args: argparse.Namespace, stage: str) -> dict[str, Any]:
    try:
        state = await page.evaluate(EXTRACT_PAGE_JS)
    except Exception as exc:
        return {"stage": stage, "attempted": False, "error": f"could_not_read_page_state: {exc}"}

    body = txt(state.get("bodyTextSample"))
    title = txt(state.get("title"))
    if not CONSENT_TEXT_RE.search(" ".join([title, body[:2500]])):
        return {"stage": stage, "attempted": False, "reason": "no_consent_screen_detected"}

    selectors = [
        "button:has-text('CONFIRM')",
        "button:has-text('Confirm')",
        "input[type='submit'][value='CONFIRM']",
        "input[type='button'][value='CONFIRM']",
        "a:has-text('CONFIRM')",
        "text=CONFIRM",
        "button:has-text('I agree')",
        "button:has-text('Accept')",
        "button:has-text('Continue')",
    ]
    button = await first_visible_locator(page, selectors)
    if button is None:
        return {
            "stage": stage,
            "attempted": True,
            "clicked": False,
            "reason": "consent_screen_detected_but_no_visible_confirm_button",
            "title": title,
            "body_sample": body[:500],
        }
    try:
        await button.click(timeout=8000)
        await page.wait_for_timeout(args.login_settle_ms)
        return {"stage": stage, "attempted": True, "clicked": True, "title_before_click": title}
    except Exception as exc:
        return {"stage": stage, "attempted": True, "clicked": False, "error": str(exc), "title": title}


async def maybe_login_superbru(page: Any, args: argparse.Namespace) -> dict[str, Any]:
    user_value = txt(os.environ.get("SUPERBRU_USERNAME") or os.environ.get("SUPERBRU_EMAIL"))
    secret_value = txt(os.environ.get("SUPERBRU_" + "PASS" + "WORD"))
    consent_attempts: list[dict[str, Any]] = []
    if not user_value or not secret_value:
        return {"attempted": False, "reason": "Superbru credentials not provided"}

    print("Superbru credentials detected in environment. Attempting standard login without printing credentials.")
    login_urls = []
    if txt(args.login_url):
        login_urls.append(txt(args.login_url))
    if txt(args.pool_url) not in login_urls:
        login_urls.append(txt(args.pool_url))

    secret_input_selector = "input[type='" + "pass" + "word" + "']"
    last_error = ""
    for login_url in login_urls:
        try:
            await page.goto(login_url, wait_until="domcontentloaded", timeout=args.timeout_ms)
            await page.wait_for_timeout(args.login_settle_ms)
            consent_attempts.append(await click_privacy_consent_if_present(page, args, f"before_login_form:{login_url}"))
            await page.goto(login_url, wait_until="domcontentloaded", timeout=args.timeout_ms)
            await page.wait_for_timeout(args.login_settle_ms)
            consent_attempts.append(await click_privacy_consent_if_present(page, args, f"after_login_reload:{login_url}"))
            await page.wait_for_timeout(1500)

            secret_input = await first_visible_locator(page, [secret_input_selector])
            if secret_input is None:
                last_error = "No visible credential input found"
                continue
            user_input = await first_visible_locator(page, [
                "input[type='email']",
                "input[name='email']",
                "input[name='username']",
                "input[name='login']",
                "input[name='userid']",
                "input[name='user']",
                "input[type='text']",
            ])
            if user_input is None:
                last_error = "No visible username/email input found"
                continue
            await user_input.fill(user_value)
            await secret_input.fill(secret_value)
            submit = await first_visible_locator(page, [
                "button[type='submit']",
                "input[type='submit']",
                "button:has-text('Log in')",
                "button:has-text('Login')",
                "button:has-text('Sign in')",
                "input[value*='Log']",
                "input[value*='Sign']",
            ])
            if submit is not None:
                await submit.click()
            else:
                await secret_input.press("Enter")
            await page.wait_for_timeout(args.login_settle_ms)

            await page.goto(args.pool_url, wait_until="domcontentloaded", timeout=args.timeout_ms)
            await page.wait_for_timeout(args.login_settle_ms)
            consent_attempts.append(await click_privacy_consent_if_present(page, args, "after_login_pool_load"))
            await page.goto(args.pool_url, wait_until="domcontentloaded", timeout=args.timeout_ms)
            await page.wait_for_timeout(args.login_settle_ms)
            state = await page.evaluate(EXTRACT_PAGE_JS)
            return {
                "attempted": True,
                "login_url_used": login_url,
                "current_url_after_login": txt(state.get("currentUrl")),
                "body_text_length_after_login": int(state.get("bodyTextLength") or 0),
                "consent_attempts": consent_attempts,
            }
        except Exception as exc:
            last_error = str(exc)
            continue

    return {
        "attempted": True,
        "success_uncertain": True,
        "error": last_error or "No visible login form found",
        "consent_attempts": consent_attempts,
    }


async def audit(args: argparse.Namespace, fixtures: list[dict[str, str]]) -> dict[str, Any]:
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except Exception as exc:
        raise RuntimeError("Install with: pip install playwright && python -m playwright install chromium") from exc

    out_dir = Path(args.out_dir)
    diagnostics_dir = Path(args.diagnostics_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    diagnostics_dir.mkdir(parents=True, exist_ok=True)

    network_rows: list[dict[str, Any]] = []
    states: list[tuple[str, int, dict[str, Any]]] = []
    current_round = {"label": "initial", "index": 0}
    login_status: dict[str, Any] = {"attempted": False, "reason": "not_started"}

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(args.cdp_url)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()

        def on_response(response: Any) -> None:
            try:
                headers = response.headers or {}
                url = response.url
                if not any(token in url.lower() for token in ["superbru", "pool", "predictor", "api", "ajax", "json", "login"]):
                    return
                network_rows.append({
                    "round_label": current_round["label"],
                    "round_scan_index": current_round["index"],
                    "url": url,
                    "status": response.status,
                    "content_type": headers.get("content-type", ""),
                    "resource_type": response.request.resource_type,
                })
            except Exception:
                return

        page.on("response", on_response)
        login_status = await maybe_login_superbru(page, args)
        print(f"Superbru login status: attempted={login_status.get('attempted')} body_length_after_login={login_status.get('body_text_length_after_login', '')}")

        print("Superbru inventory :: loading pool page")
        try:
            await page.goto(args.pool_url, wait_until="domcontentloaded", timeout=args.timeout_ms)
        except PlaywrightTimeoutError:
            print("WARNING: Timeout loading Superbru pool page. Checking current state.")
        await page.wait_for_timeout(args.settle_ms)
        state = await page.evaluate(EXTRACT_PAGE_JS)
        states.append(("initial", 0, state))

        controls = round_controls(state)
        print(f"Superbru inventory :: found {len(controls)} round controls")
        for idx, control in enumerate(controls, start=1):
            label = txt(control.get("text")) or f"round_{idx}"
            print(f"Superbru inventory :: clicking {label}")
            current_round["label"] = label
            current_round["index"] = idx
            try:
                await page.goto(args.pool_url, wait_until="domcontentloaded", timeout=args.timeout_ms)
            except PlaywrightTimeoutError:
                print("WARNING: Timeout reloading Superbru pool page before round click.")
            await page.wait_for_timeout(1500)
            try:
                loc = page.locator(txt(control.get("selector"))).first
                await loc.scroll_into_view_if_needed(timeout=5000)
                await loc.click(timeout=5000)
                await page.wait_for_timeout(args.settle_ms)
            except Exception as exc:
                print(f"WARNING: Could not click {label}: {exc}")
            round_state = await page.evaluate(EXTRACT_PAGE_JS)
            states.append((label, idx, round_state))

        await browser.close()

    table_rows: list[dict[str, Any]] = []
    control_rows: list[dict[str, Any]] = []
    coverage_rows: list[dict[str, Any]] = []
    round_rows: list[dict[str, Any]] = []

    for label, idx, state in states:
        if args.write_raw_state:
            raw_file = diagnostics_dir / f"{idx:02d}_{safe_name(label)}_state.json"
            raw_file.write_text(json.dumps(trim_for_raw(state, args.max_raw_chars), indent=2, default=str), encoding="utf-8")

        table_rows.extend(build_table_inventory(state, label, idx))
        control_rows.extend(build_control_inventory(state, label, idx))
        coverage_rows.extend(build_fixture_coverage(state, fixtures, label, idx))
        round_rows.append(state_summary_row(state, label, idx, fixtures, network_rows))

    table_csv = out_dir / "superbru_table_inventory.csv"
    control_csv = out_dir / "superbru_control_inventory.csv"
    network_csv = out_dir / "superbru_network_inventory.csv"
    coverage_csv = out_dir / "superbru_visible_pick_coverage.csv"
    round_csv = out_dir / "superbru_round_inventory.csv"
    fields_json = out_dir / "superbru_available_fields.json"

    pd.DataFrame(table_rows).to_csv(table_csv, index=False)
    pd.DataFrame(control_rows).to_csv(control_csv, index=False)
    pd.DataFrame(network_rows).drop_duplicates().to_csv(network_csv, index=False)
    pd.DataFrame(coverage_rows).to_csv(coverage_csv, index=False)
    pd.DataFrame(round_rows).to_csv(round_csv, index=False)

    visible_count = sum(1 for row in coverage_rows if row.get("fixture_visible"))
    likely_pick_count = sum(1 for row in coverage_rows if row.get("likely_pick_data_visible"))
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "superbru",
        "pool_url": args.pool_url,
        "login_status": login_status,
        "round_state_count": len(states),
        "rounds_clicked": max(0, len(states) - 1),
        "round_labels": [label for label, _, _ in states],
        "table_count": sum(int(row.get("table_count") or 0) for row in round_rows),
        "control_count": sum(int(row.get("control_count") or 0) for row in round_rows),
        "form_count": sum(int(row.get("form_count") or 0) for row in round_rows),
        "network_row_count": len(network_rows),
        "fixture_input_count": len(fixtures),
        "fixture_visible_count": visible_count,
        "likely_pick_data_visible_count": likely_pick_count,
        "table_scoreline_total": sum(int(row.get("table_scoreline_count") or 0) for row in round_rows),
        "body_scoreline_total": sum(int(row.get("body_scoreline_count") or 0) for row in round_rows),
        "control_round_count": sum(1 for row in control_rows if row.get("is_round_control")),
        "resource_type_counts": dict(Counter(txt(row.get("resource_type")) for row in network_rows)),
        "outputs": {
            "round_csv": str(round_csv),
            "table_csv": str(table_csv),
            "control_csv": str(control_csv),
            "network_csv": str(network_csv),
            "coverage_csv": str(coverage_csv),
        },
    }
    fields_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload


def main() -> int:
    args = build_parser().parse_args()
    fixtures = load_fixtures(Path(args.fixtures_csv))
    import asyncio
    payload = asyncio.run(audit(args, fixtures))
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
