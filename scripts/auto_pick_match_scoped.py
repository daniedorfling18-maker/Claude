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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
({gameId, label}) => {
  function clean(text) {
    return (text || '').replace(/\s+/g, ' ').trim();
  }

  function isVisible(el) {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function commonAncestor(a, b) {
    if (!a || !b) return a || b || null;
    const seen = new Set();
    let x = a;
    while (x) {
      seen.add(x);
      x = x.parentElement;
    }
    x = b;
    while (x) {
      if (seen.has(x)) return x;
      x = x.parentElement;
    }
    return null;
  }

  function extractKickoff(root) {
    if (!root) return {kickoffText: null, kickoffTs: null, kickoffSource: null};

    const candidates = Array.from(root.querySelectorAll(
      '[class*=kickoff],[class*=kick-off],[class*=match-time],[class*=fixture-time],' +
      '[class*=match-date],[class*=fixture-date],[class*=game-time],' +
      'time,[datetime],[data-kickoff],[data-timestamp],[data-time],' +
      '[class*=date],[class*=time]'
    )).filter(isVisible);

    for (const el of candidates) {
      const ts = el.getAttribute('datetime') || el.getAttribute('data-kickoff') ||
                 el.getAttribute('data-timestamp') || el.getAttribute('data-time');
      const txt = clean(el.innerText || el.textContent || '');

      if (ts) return {kickoffText: txt, kickoffTs: ts, kickoffSource: 'scoped_attr'};

      const fullDate = txt.match(/\b\d{1,2}\s+\w+\s+\d{1,2}:\d{2}\b/);
      if (fullDate) return {kickoffText: fullDate[0], kickoffTs: null, kickoffSource: 'scoped_text_date_time'};

      const timeOnly = txt.match(/\b\d{1,2}[:\-]\d{2}\b/);
      if (timeOnly) return {kickoffText: txt, kickoffTs: null, kickoffSource: 'scoped_text_time'};
    }

    const body = clean(root.innerText || root.textContent || '');
    const m = body.match(/\b(\w{3}\s+\d{1,2}\s+\w+\s+\d{2}:\d{2}|\d{1,2}\s+\w+\s+\d{4}\s+\d{2}:\d{2}|\d{1,2}\s+\w+\s+\d{1,2}:\d{2}|\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4}\s+\d{2}:\d{2}|\d{2}:\d{2})\b/);
    if (m) return {kickoffText: m[0], kickoffTs: null, kickoffSource: 'scoped_body'};

    return {kickoffText: null, kickoffTs: null, kickoffSource: null};
  }

  // Scope reads to THIS game's picker. Document-wide first-match reads returned a
  // DIFFERENT game's inputs whenever several pickers shared the page (2026-07-08:
  // current_pick/locked/kickoff read from the wrong row can mark a game
  // already_current and silently skip a needed submit).
  const numericGameId = String(gameId || '').replace(/^game/, '');
  const gameRoot = numericGameId
    ? (document.querySelector('#soccer-picker' + numericGameId)
       || document.querySelector('[data-bru-game-id="' + numericGameId + '"]'))
    : null;

  const visibleLeft = Array.from((gameRoot || document).querySelectorAll('input.soccer-left-score')).filter(isVisible);
  const visibleRight = Array.from((gameRoot || document).querySelectorAll('input.soccer-right-score')).filter(isVisible);

  const hi = visibleLeft[0] || (gameRoot || document).querySelector('input.soccer-left-score');
  const ai = visibleRight[0] || (gameRoot || document).querySelector('input.soccer-right-score');

  let scope = commonAncestor(hi, ai) || gameRoot || document.body;

  let kickoff = {kickoffText: null, kickoffTs: null, kickoffSource: null};
  let probe = scope;

  while (probe && probe !== document.documentElement) {
    kickoff = extractKickoff(probe);
    if (kickoff.kickoffText || kickoff.kickoffTs) break;
    probe = probe.parentElement;
  }

  return {
    kickoffText: kickoff.kickoffText,
    kickoffTs: kickoff.kickoffTs,
    kickoffSource: kickoff.kickoffSource,
    homeVal: hi ? hi.value : null,
    awayVal: ai ? ai.value : null,
    locked: hi ? (hi.disabled || hi.readOnly) : null,
    inputsFound: !!(hi && ai)
  };
}
"""



EXTRACT_TABLES_JS = r"""
() => {
  function clean(text) { return (text || '').replace(/\s+/g, ' ').trim(); }
  return Array.from(document.querySelectorAll('table')).map((table, idx) => ({
    index: idx,
    matrix: Array.from(table.querySelectorAll('tr')).map(row =>
      Array.from(row.querySelectorAll('th,td')).map(cell => clean(cell.innerText || cell.textContent))
    ).filter(r => r.some(c => c.length > 0))
  }));
}
"""


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def normalise_score_pick(value: Any) -> str:
    text = txt(value)
    match = re.search(r"(\d+)\s*[-–]\s*(\d+)", text)
    if not match:
        return text
    return f"{int(match.group(1))}-{int(match.group(2))}"


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


def local_page_timezone(name: str | None) -> timezone | ZoneInfo:
    clean = txt(name) or "Africa/Johannesburg"
    try:
        return ZoneInfo(clean)
    except ZoneInfoNotFoundError:
        print(f"warning: unknown page timezone {clean!r}; falling back to Africa/Johannesburg")
        return ZoneInfo("Africa/Johannesburg")


def parse_kickoff(
    text: str | None,
    ts: str | None,
    ref: datetime,
    page_timezone: str | None = "Africa/Johannesburg",
) -> datetime | None:
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
    local_tz = local_page_timezone(page_timezone)

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
            return dt.replace(tzinfo=local_tz).astimezone(timezone.utc)
        except Exception:
            continue
    return None


# ─── Leaderboard scraping and pool-position intelligence ─────────────────────


def _extract_pool_id(pool_url: str) -> str | None:
    """Extract the p=XXXXX pool ID from a Superbru URL."""
    m = re.search(r"[?&]p=(\d+)", pool_url)
    return m.group(1) if m else None


def _dedupe_urls(urls: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for url in urls:
        clean = txt(url)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
    return out


def _replace_query_param(url: str, key: str, value: str) -> str:
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    updated = [(k, v) for k, v in query if k != key]
    updated.append((key, value))
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(updated), "")
    )


def leaderboard_url_candidates_from_pool_url(pool_url: str) -> list[str]:
    """
    Build likely leaderboard URLs for the *same* pool as pool_url.

    Always keeps the pool ID (p=XXXXX) so we never land on a different pool.
    SuperBru has moved pool views between pool_view.php and pool.php over time,
    so scheduled runs try both known shapes before failing.
    """
    pool_url = txt(pool_url)
    pool_id = _extract_pool_id(pool_url)
    parts = urllib.parse.urlsplit(pool_url)
    query = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    tournament = query.get("t", "")
    group = query.get("g", "")
    base = f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else "https://www.superbru.com"
    path_prefix = "/".join(parts.path.split("/")[:-1])
    if path_prefix and not path_prefix.startswith("/"):
        path_prefix = "/" + path_prefix

    urls: list[str] = []
    if "pool_view.php" in pool_url:
        urls.append(_replace_query_param(pool_url, "view", "leaderboard"))

    if "pool.php" in pool_url:
        url = re.sub(r"tab=\w+", "tab=leaderboard", pool_url)
        url = re.sub(r"#tab=\w+", "", url)
        if "tab=leaderboard" not in url:
            sep = "&" if "?" in url else "?"
            url = url + sep + "tab=leaderboard"
        urls.append(url + "#tab=leaderboard")
        urls.append(url)

    if pool_id and path_prefix:
        common_query = {"p": pool_id}
        if tournament:
            common_query["t"] = tournament
        if group:
            common_query["g"] = group
        view_query = urllib.parse.urlencode({**common_query, "view": "leaderboard"})
        tab_query = urllib.parse.urlencode({**common_query, "tab": "leaderboard"})
        urls.extend(
            [
                f"{base}{path_prefix}/pool_view.php?{view_query}",
                f"{base}{path_prefix}/pool.php?{tab_query}#tab=leaderboard",
                f"{base}{path_prefix}/pool.php?{tab_query}",
            ]
        )

    urls.append(pool_url)
    return _dedupe_urls(urls)


def leaderboard_url_from_pool_url(pool_url: str) -> str:
    """Return the primary leaderboard URL candidate for compatibility."""
    candidates = leaderboard_url_candidates_from_pool_url(pool_url)
    return candidates[0] if candidates else pool_url


def _is_lb_row(row: list[str]) -> bool:
    if len(row) < 3:
        return False
    rank_ok = bool(re.fullmatch(r"\d+", row[0].strip()))
    points_ok = bool(re.fullmatch(r"\d+(?:\.\d+)?", row[-1].strip()))
    has_player = any(re.search(r"[A-Za-z]", cell) for cell in row[1:-1])
    return rank_ok and points_ok and has_player


def _parse_leaderboard(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract rank/player/points from raw table matrices returned by EXTRACT_TABLES_JS."""
    best: list[dict[str, Any]] = []
    for table in tables:
        matrix = table.get("matrix") or []
        data_rows = [row for row in matrix if _is_lb_row(row)]
        if len(data_rows) <= len(best):
            continue
        parsed: list[dict[str, Any]] = []
        for row in data_rows:
            try:
                rank = int(row[0].strip())
                points = float(row[-1].strip())
            except (ValueError, IndexError):
                continue
            player = next((c.strip() for c in row[1:-1] if re.search(r"[A-Za-z]", c)), "")
            if player:
                parsed.append({"rank": rank, "player": player, "current_points": points})
        if parsed:
            best = parsed
    return sorted(best, key=lambda r: r["rank"])


def compute_pool_standing(
    leaderboard: list[dict[str, Any]],
    my_player: str,
    chaser_range: float = 8.0,
) -> dict[str, Any]:
    """Compute my rank, points gap and strategic context from leaderboard rows."""
    if not leaderboard or not my_player:
        return {"status": "unavailable"}

    my_norm = norm_team(my_player)
    my_row: dict[str, Any] | None = next(
        (r for r in leaderboard if norm_team(r["player"]) == my_norm), None
    )
    if not my_row:
        my_row = next(
            (r for r in leaderboard if my_norm in norm_team(r["player"]) or norm_team(r["player"]) in my_norm),
            None,
        )
    if not my_row:
        return {"status": "player_not_found", "player": my_player, "leaderboard_size": len(leaderboard)}

    my_rank = int(my_row["rank"])
    my_points = float(my_row["current_points"])
    others = [r for r in leaderboard if norm_team(r["player"]) != norm_team(my_row["player"])]
    sorted_lb = sorted(leaderboard, key=lambda r: -float(r["current_points"]))
    leader = sorted_lb[0]
    leader_points = float(leader["current_points"])

    if my_rank == 1:
        second_points = float(sorted_lb[1]["current_points"]) if len(sorted_lb) > 1 else my_points
        leader_gap = my_points - second_points
        chasers_close = [r for r in others if (my_points - float(r["current_points"])) <= chaser_range]
        return {
            "status": "leading",
            "rank": 1,
            "my_points": my_points,
            "leader_gap": round(leader_gap, 2),
            "chasers_in_range": len(chasers_close),
            "chaser_names": [r["player"] for r in chasers_close[:5]],
            "leaderboard_size": len(leaderboard),
        }

    gap_to_leader = leader_points - my_points
    chasers_behind = [r for r in others if 0 < (my_points - float(r["current_points"])) <= chaser_range]
    return {
        "status": "chasing",
        "rank": my_rank,
        "my_points": my_points,
        "leader_name": txt(leader["player"]),
        "leader_points": leader_points,
        "gap_to_leader": round(gap_to_leader, 2),
        "chasers_behind": len(chasers_behind),
        "leaderboard_size": len(leaderboard),
    }


EXTRACT_PAGE_CONTEXT_JS = r"""
() => ({
  url: window.location.href,
  title: document.title || '',
  h1s: Array.from(document.querySelectorAll('h1,h2,h3,.pool-name,.competition-name,.pool-title'))
        .map(e => (e.innerText || e.textContent || '').replace(/\s+/g,' ').trim())
        .filter(t => t.length > 0),
  bodySnippet: (document.body ? document.body.innerText : '').slice(0, 3000)
})
"""


EXTRACT_LEADERBOARD_TEXT_JS = r"""
() => {
  function clean(text) { return (text || '').replace(/\s+/g, ' ').trim(); }
  const selectors = [
    'table tr',
    '[class*=leader] tr',
    '[class*=standing] tr',
    '[class*=rank] tr',
    '[class*=leader] li',
    '[class*=standing] li',
    '[class*=rank] li',
    '[class*=leader] [class*=row]',
    '[class*=standing] [class*=row]',
    '[class*=rank] [class*=row]',
    '[data-testid*=leader]',
    '[data-testid*=standing]'
  ];
  const rows = [];
  const seen = new Set();
  for (const selector of selectors) {
    for (const el of Array.from(document.querySelectorAll(selector))) {
      const text = clean(el.innerText || el.textContent || '');
      if (!text || seen.has(selector + '|' + text)) continue;
      seen.add(selector + '|' + text);
      rows.push({selector, text});
    }
  }
  const body = document.body ? (document.body.innerText || '') : '';
  for (const line of body.split(/\n+/).map(clean).filter(Boolean)) {
    if (/^\d{1,4}\s+.+\s+-?\d+(?:\.\d+)?(?:\s*(?:pts|points))?$/i.test(line)) {
      const key = 'body|' + line;
      if (!seen.has(key)) {
        seen.add(key);
        rows.push({selector: 'body_line', text: line});
      }
    }
  }
  return {
    url: window.location.href,
    title: document.title || '',
    rows: rows.slice(0, 250)
  };
}
"""


def _page_is_target_pool(context: dict[str, Any], pool_id: str | None, pool_name_keywords: list[str]) -> bool:
    """
    Return True only if the current page is for our specific pool.

    Checks (in order):
    1. Page URL contains the pool ID (p=XXXXX)
    2. Page title or headings contain any of the pool_name_keywords
    """
    if pool_id:
        if f"p={pool_id}" in context.get("url", ""):
            return True

    text_blob = " ".join([
        context.get("title", ""),
        " ".join(context.get("h1s", [])),
        context.get("bodySnippet", "")[:500],
    ]).lower()

    return any(kw.lower() in text_blob for kw in pool_name_keywords if kw)


def _parse_leaderboard_text_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for item in items:
        text = txt(item.get("text"))
        candidates = [text]
        if "\n" in text:
            candidates.extend(part.strip() for part in text.splitlines())
        for raw_line in candidates:
            line = re.sub(r"\s+", " ", txt(raw_line))
            if not line:
                continue
            match = re.match(
                r"^(?P<rank>\d{1,4})\s+(?P<middle>.+?)\s+(?P<points>-?\d+(?:\.\d+)?)(?:\s*(?:pts|points))?$",
                line,
                flags=re.IGNORECASE,
            )
            if not match:
                continue
            middle = txt(match.group("middle"))
            if not re.search(r"[A-Za-z]", middle):
                continue
            movement = ""
            movement_match = re.match(r"^(?P<movement>[+\-]?\d+(?:\.\d+)?)\s+(?P<player>.+)$", middle)
            if movement_match and re.search(r"[A-Za-z]", movement_match.group("player")):
                movement = movement_match.group("movement")
                player = txt(movement_match.group("player"))
            else:
                player = middle
            player = re.sub(r"\s+", " ", player).strip(" -–—")
            if not player:
                continue
            rank = int(match.group("rank"))
            points = float(match.group("points"))
            key = (rank, norm_team(player))
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "rank": rank,
                    "movement_or_yellow_caps": movement,
                    "player": player,
                    "current_points": points,
                }
            )
    return sorted(rows, key=lambda row: int(row["rank"]))


async def _write_leaderboard_diagnostic(page: Any, diagnostics_dir: Path | None, label: str) -> None:
    if not diagnostics_dir:
        return
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_name(f"leaderboard_{label}")[:120]
    try:
        await page.screenshot(path=str(diagnostics_dir / f"{stem}.png"), full_page=True)
    except Exception:
        pass
    try:
        html = await page.content()
        (diagnostics_dir / f"{stem}.html").write_text(html, encoding="utf-8")
    except Exception:
        pass
    state: dict[str, Any] = {"label": label}
    for key, script in {
        "context": EXTRACT_PAGE_CONTEXT_JS,
        "tables": EXTRACT_TABLES_JS,
        "text_candidates": EXTRACT_LEADERBOARD_TEXT_JS,
    }.items():
        try:
            state[key] = await page.evaluate(script)
        except Exception as exc:
            state[key] = {"error": f"{type(exc).__name__}: {exc}"}
    (diagnostics_dir / f"{stem}.json").write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


async def scrape_leaderboard_in_session(
    page: Any,
    pool_url: str,
    pool_name_keywords: list[str] | None = None,
    diagnostics_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """
    Scrape the leaderboard for the specific pool identified by pool_url.

    Uses the pool ID (p=XXXXX) and optional pool name keywords to verify we are
    on the right pool before accepting the scraped rows. Falls back to tab-click
    selectors but only accepts results that pass the pool identity check.
    """
    pool_id = _extract_pool_id(pool_url)
    keywords = pool_name_keywords or []
    url_candidates = leaderboard_url_candidates_from_pool_url(pool_url)
    print("  leaderboard URL candidates:")
    for url in url_candidates:
        print(f"    - {url}")

    async def _scrape_and_validate(label: str) -> list[dict[str, Any]]:
        context = await page.evaluate(EXTRACT_PAGE_CONTEXT_JS)
        current_url = context.get("url", "")

        if not _page_is_target_pool(context, pool_id, keywords):
            print(
                f"  leaderboard page failed pool check at {current_url!r} "
                f"(pool_id={pool_id!r}, keywords={keywords}). Skipping."
            )
            await _write_leaderboard_diagnostic(page, diagnostics_dir, f"{label}_wrong_pool")
            return []

        tables = await page.evaluate(EXTRACT_TABLES_JS)
        rows = _parse_leaderboard(tables)
        if not rows:
            text_state = await page.evaluate(EXTRACT_LEADERBOARD_TEXT_JS)
            rows = _parse_leaderboard_text_rows(text_state.get("rows") or [])
        if rows:
            print(f"  leaderboard scraped via {label}: {len(rows)} players from pool {pool_id!r}")
        else:
            print(f"  leaderboard candidate had no rows via {label} at {current_url!r}")
            await _write_leaderboard_diagnostic(page, diagnostics_dir, f"{label}_no_rows")
        return rows

    # ── Primary: navigate directly to the pool-specific leaderboard URL ────────
    for idx, lb_url in enumerate(url_candidates, start=1):
        try:
            await page.goto(lb_url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(5000)
            rows = await _scrape_and_validate(f"direct_url_{idx}")
            if rows:
                return rows
        except Exception as exc:
            print(f"  leaderboard direct URL failed ({lb_url}): {exc}")

    # ── Fallback: click a leaderboard tab only if we're still on the right pool ─
    # Re-navigate to the pool page first so tab-clicks are scoped to it.
    try:
        await page.goto(pool_url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(4000)
    except Exception as exc:
        print(f"  could not reload pool page for tab-click fallback: {exc}")
        return []

    for sel in [
        f"a[href*='p={pool_id}'][href*='leaderboard']" if pool_id else "",
        f"a[href*='p={pool_id}'][href*='standings']" if pool_id else "",
        "a[href*='leaderboard']", "a[href*='standings']",
        "[data-tab='leaderboard']", "[data-tab='standings']",
        "a:has-text('Leaderboard')", "a:has-text('Standings')",
    ]:
        if not sel:
            continue
        try:
            await page.click(sel, timeout=2500)
            await page.wait_for_timeout(4000)
            rows = await _scrape_and_validate(f"tab-click {sel!r}")
            if rows:
                return rows
        except Exception:
            continue

    return []


# ─── Defensive row builder for game_theory.defensive ─────────────────────────


def _derive_risk_tier(diag: dict[str, Any]) -> str:
    stability = float(diag.get("sensitivity_stability") or 1.0)
    if stability >= 0.85:
        return "low"
    if stability >= 0.65:
        return "medium"
    return "high"


def _derive_confidence_tier(recommended: Any) -> str:
    p_exact = float(getattr(recommended, "p_exact", 0.0))
    if p_exact >= 0.14:
        return "strong"
    if p_exact >= 0.09:
        return "medium"
    return "fragile"


def build_defensive_row(
    prediction: Any,
    orientation: str,
    card_row: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the row dict expected by game_theory.defensive.evaluate_match()."""

    def orient(scoreline: str) -> str:
        if orientation != "swapped" or not scoreline or "-" not in scoreline:
            return scoreline
        a, b = scoreline.split("-", 1)
        return f"{b}-{a}"

    tops = [c.scoreline for c in (prediction.top_candidates or [])]
    diag = prediction.diagnostics or {}

    ev_gap = 0.0
    if len(prediction.top_candidates) >= 2:
        ev_gap = float(prediction.top_candidates[0].expected_points - prediction.top_candidates[1].expected_points)

    risk_tier = txt(card_row.get("risk_tier") if card_row else "") or _derive_risk_tier(diag)
    confidence_tier = txt(card_row.get("confidence_tier") if card_row else "") or _derive_confidence_tier(prediction.recommended)

    return {
        "home_team": prediction.home_team,
        "away_team": prediction.away_team,
        "commence_time": prediction.commence_time,
        "recommended_scoreline": orient(prediction.recommended.scoreline),
        "expected_points": float(prediction.recommended.expected_points),
        "private_chase_scoreline": orient(prediction.private_chase_pick.scoreline),
        "modal_scoreline": orient(str(diag.get("modal_scoreline") or prediction.modal_score_pick.scoreline or "")),
        "top1_scoreline": orient(tops[0]) if len(tops) > 0 else "",
        "top2_scoreline": orient(tops[1]) if len(tops) > 1 else "",
        "top3_scoreline": orient(tops[2]) if len(tops) > 2 else "",
        "ev_gap_to_second": round(ev_gap, 4),
        "private_chase_ev_loss": float(diag.get("private_chase_ev_loss", 0.05)),
        "risk_tier": risk_tier,
        "confidence_tier": confidence_tier,
        "sensitivity_stability": float(diag.get("sensitivity_stability") or 1.0),
    }


def select_pool_adaptive_pick(
    prediction: Any,
    pool_standing: dict[str, Any],
    orientation: str,
    card_row: dict[str, Any] | None,
    leader_safe_buffer: float,
    chaser_range: float,
) -> dict[str, Any]:
    """
    Overlay pool-position intelligence on a fresh engine prediction.

    Leading with a tight gap  → defensive model (game_theory.defensive)
    Leading comfortably       → keep raw-EV prediction (EV-optimal)
    Chasing within range      → private_chase pick (differentiate from leader)
    Far behind / unknown      → prediction's recommended pick (configured strategy_mode)
    """

    def orient(scoreline: str) -> str:
        if orientation != "swapped" or not scoreline or "-" not in scoreline:
            return scoreline
        a, b = scoreline.split("-", 1)
        return f"{b}-{a}"

    raw_scoreline = orient(prediction.recommended.scoreline)
    raw_ev = float(prediction.recommended.expected_points)
    status = pool_standing.get("status", "unavailable")

    if status == "leading":
        leader_gap = float(pool_standing.get("leader_gap", 0.0))
        chasers = int(pool_standing.get("chasers_in_range", 0))

        if leader_gap < leader_safe_buffer and chasers > 0:
            try:
                from superbru_score_engine.game_theory.defensive import evaluate_match

                def_row = build_defensive_row(prediction, orientation, card_row)
                result = evaluate_match(def_row, leader_gap=leader_gap, chasers=chasers)
                defensive_scoreline = txt(result.get("leader_defensive_scoreline", ""))
                if defensive_scoreline:
                    return {
                        "scoreline": defensive_scoreline,
                        "strategy": "defensive_leader",
                        "leader_gap": leader_gap,
                        "chasers": chasers,
                        "ev_cost": float(result.get("ev_cost_vs_recommended", 0.0)),
                        "defensive_reason": txt(result.get("defensive_reason", "")),
                        "raw_ev_scoreline": raw_scoreline,
                        "expected_points": float(result.get("leader_expected_points", raw_ev)),
                    }
            except Exception as exc:
                print(f"  defensive model failed (non-blocking): {exc}")

        return {
            "scoreline": raw_scoreline,
            "strategy": "leading_comfortable" if leader_gap >= leader_safe_buffer else "leading_defensive_fallback",
            "leader_gap": leader_gap,
            "expected_points": raw_ev,
        }

    if status == "chasing":
        gap_to_leader = float(pool_standing.get("gap_to_leader", 999.0))
        if gap_to_leader <= chaser_range:
            chase_scoreline = orient(prediction.private_chase_pick.scoreline)
            return {
                "scoreline": chase_scoreline,
                "strategy": "private_chase",
                "gap_to_leader": gap_to_leader,
                "raw_ev_scoreline": raw_scoreline,
                "expected_points": float(prediction.private_chase_pick.expected_points),
                "leader_name": txt(pool_standing.get("leader_name", "")),
            }
        return {
            "scoreline": raw_scoreline,
            "strategy": "raw_ev_far_behind",
            "gap_to_leader": gap_to_leader,
            "expected_points": raw_ev,
        }

    return {
        "scoreline": raw_scoreline,
        "strategy": "pool_standing_unavailable",
        "expected_points": raw_ev,
    }


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


def pick_card_entries_in_window(args: argparse.Namespace, ref: datetime) -> list[dict[str, Any]]:
    """Build queue entries directly from the locked card as a page-scan fallback."""
    rows, column = load_pick_rows(Path(args.pick_card_csv))
    window = timedelta(minutes=args.window_minutes)
    late_grace = timedelta(minutes=max(0, int(getattr(args, "late_card_grace_minutes", 5))))
    entries: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        kickoff = parse_iso_datetime(row.get("commence_time") or row.get("kickoff_utc"))
        if kickoff is None:
            continue
        time_until = kickoff - ref
        if not (-late_grace <= time_until <= window):
            continue
        home_team = txt(row.get("home_team"))
        away_team = txt(row.get("away_team"))
        if not home_team or not away_team:
            continue
        entries.append(
            {
                "game_id": f"pick_card_{idx}",
                "game": f"{home_team} v {away_team}",
                "home_team": home_team,
                "away_team": away_team,
                "kickoff_utc": kickoff.isoformat(),
                "kickoff_raw": txt(row.get("commence_time") or row.get("kickoff_utc")),
                "kickoff_source": "pick_card_fallback",
                "minutes_until": round(time_until.total_seconds() / 60),
                "current_pick": "",
                "locked": False,
                "inputs_found": None,
                "status": "queued_from_pick_card_fallback",
                "pick_card_column": column,
                "pick_card_row": row,
            }
        )
    return entries


def merge_pick_card_fallback_queue(
    args: argparse.Namespace,
    ref: datetime,
    scan_results: list[dict[str, Any]],
    queued: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Add card-timed matches not discovered by page tab scanning."""
    existing = {
        (norm_team(item.get("home_team")), norm_team(item.get("away_team")), txt(item.get("kickoff_utc"))[:16])
        for item in queued
    }
    existing_by_teams = {
        (norm_team(item.get("home_team")), norm_team(item.get("away_team"))): item
        for item in queued
    }
    locked = {
        (norm_team(item.get("home_team")), norm_team(item.get("away_team")), txt(item.get("kickoff_utc"))[:16])
        for item in scan_results
        if item.get("status") == "locked_skipped"
    }
    locked_by_teams = {
        (norm_team(item.get("home_team")), norm_team(item.get("away_team")))
        for item in scan_results
        if item.get("status") == "locked_skipped" or item.get("locked") is True
    }
    scan_by_teams: dict[tuple[str, str], dict[str, Any]] = {}
    for item in scan_results:
        team_key = (norm_team(item.get("home_team")), norm_team(item.get("away_team")))
        if not team_key[0] or not team_key[1]:
            continue
        previous = scan_by_teams.get(team_key)
        current_pick = normalise_score_pick(item.get("current_pick"))
        previous_pick = normalise_score_pick(previous.get("current_pick")) if previous else ""
        if previous is None or (current_pick and not previous_pick) or item.get("inputs_found") is True:
            scan_by_teams[team_key] = item
    added: list[dict[str, Any]] = []
    merged = list(queued)
    for entry in pick_card_entries_in_window(args, ref):
        key = (norm_team(entry.get("home_team")), norm_team(entry.get("away_team")), txt(entry.get("kickoff_utc"))[:16])
        team_key = (key[0], key[1])
        if key in existing or key in locked:
            continue
        if team_key in locked_by_teams:
            continue
        if team_key in existing_by_teams:
            existing_entry = existing_by_teams[team_key]
            scan_kickoff = txt(existing_entry.get("kickoff_utc"))
            card_kickoff = txt(entry.get("kickoff_utc"))
            if scan_kickoff[:16] != card_kickoff[:16]:
                existing_entry["scan_kickoff_utc"] = scan_kickoff
                existing_entry["kickoff_utc"] = card_kickoff
                existing_entry["kickoff_raw"] = txt(entry.get("kickoff_raw"))
                existing_entry["kickoff_source"] = "pick_card_fallback_reconciled_scan_time"
                existing_entry["minutes_until"] = entry.get("minutes_until")
            existing_entry["pick_card_column"] = entry.get("pick_card_column")
            existing_entry["pick_card_row"] = entry.get("pick_card_row")
            existing.add(key)
            continue
        scanned = scan_by_teams.get(team_key)
        if scanned:
            entry["scan_game_id"] = scanned.get("game_id")
            entry["scan_status"] = scanned.get("status")
            entry["scan_kickoff_utc"] = scanned.get("kickoff_utc")
            entry["scan_kickoff_raw"] = scanned.get("kickoff_raw")
            if normalise_score_pick(scanned.get("current_pick")):
                entry["current_pick"] = scanned.get("current_pick")
            if "locked" in scanned:
                entry["locked"] = scanned.get("locked")
            if "inputs_found" in scanned:
                entry["inputs_found"] = scanned.get("inputs_found")
        merged.append(entry)
        added.append(entry)
        existing.add(key)
        existing_by_teams[team_key] = entry
    return merged, added


def should_keep_existing_pick_until_revision_window(entry: dict[str, Any], args: argparse.Namespace) -> bool:
    """Avoid churn after an early SuperBru pick is already visible on the page.

    The broad watchdog window exists to make sure every visible card gets a pick
    well before kickoff. Once SuperBru already shows a pick, keep it stable until
    the final revision window; otherwise a 15-minute watchdog can re-trade the
    same prediction every time odds or pool tactics move slightly.
    """
    if getattr(args, "dry_run", False):
        return False
    if not normalise_score_pick(entry.get("current_pick")):
        return False
    try:
        minutes_until = float(entry.get("minutes_until"))
    except (TypeError, ValueError):
        return False
    try:
        revision_window = float(getattr(args, "revision_window_minutes", 260))
    except (TypeError, ValueError):
        revision_window = 260.0
    if revision_window < 0:
        return False
    return minutes_until > revision_window


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


def recompute_pick_from_snapshot(
    snapshot: dict[str, Any],
    entry: dict[str, Any],
    config: Any,
    pool_standing: dict[str, Any] | None = None,
    card_row: dict[str, Any] | None = None,
    args: Any | None = None,
) -> dict[str, Any]:
    """Recompute the recommended scoreline from freshly fetched single-match odds.

    When pool_standing is provided the pick is additionally adjusted for pool
    position: defensive if leading with a tight margin, private-chase if within
    striking range of the leader. Any failure returns a non-"ok" status so the
    caller can fall back to the committed card pick.
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

        # Determine home/away orientation relative to the Superbru tab order.
        odds_home = norm_team(match.home_team)
        if odds_home == norm_team(entry.get("home_team")):
            orientation = "aligned"
        elif odds_home == norm_team(entry.get("away_team")):
            orientation = "swapped"
        else:
            return {"status": "failed", "error": "orientation_unconfirmed"}

        # Apply pool-position intelligence when leaderboard data is available.
        if pool_standing is not None and pool_standing.get("status") not in ("unavailable", "player_not_found"):
            leader_safe_buffer = float(getattr(args, "leader_safe_buffer", 5.0))
            chaser_range = float(getattr(args, "chaser_range", 8.0))
            adaptive = select_pool_adaptive_pick(
                prediction=prediction,
                pool_standing=pool_standing,
                orientation=orientation,
                card_row=card_row,
                leader_safe_buffer=leader_safe_buffer,
                chaser_range=chaser_range,
            )
            return {
                "status": "ok",
                "scoreline": adaptive["scoreline"],
                "orientation": orientation,
                "strategy": adaptive.get("strategy"),
                "expected_points": float(adaptive.get("expected_points", prediction.recommended.expected_points)),
                "pool_standing_status": pool_standing.get("status"),
                "pool_adaptive_pick": adaptive,
                "model_home_away_scoreline": f"{home_goals}-{away_goals}",
                "odds_home_team": match.home_team,
                "odds_away_team": match.away_team,
            }

        # No pool standing: use the engine's configured strategy_mode pick.
        scoreline = f"{home_goals}-{away_goals}" if orientation == "aligned" else f"{away_goals}-{home_goals}"
        return {
            "status": "ok",
            "scoreline": scoreline,
            "orientation": orientation,
            "strategy": f"engine_{prediction.strategy_mode}",
            "expected_points": float(prediction.recommended.expected_points),
            "model_home_away_scoreline": f"{home_goals}-{away_goals}",
            "odds_home_team": match.home_team,
            "odds_away_team": match.away_team,
        }
    except Exception as exc:  # non-blocking: fall back to the committed card pick
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}


async def login(page, args, diag_dir: Path | None = None) -> bool:
    if diag_dir:
        diag_dir.mkdir(parents=True, exist_ok=True)

    try:
        await page.goto(args.login_url, wait_until="domcontentloaded", timeout=45000)
    except Exception:
        await page.goto(args.login_url, wait_until="load", timeout=45000)

    await page.wait_for_timeout(3000)

    print(f"  login page title: {await page.title()!r}")
    print(f"  login page url: {page.url!r}")

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
            print(f"  consent dismissed via {sel}")
            await page.wait_for_timeout(2500)
            break
        except Exception:
            continue

    try:
        await page.click("button[aria-label='Close success modal']", timeout=2000)
        await page.wait_for_timeout(1000)
    except Exception:
        pass

    if diag_dir:
        await page.screenshot(path=str(diag_dir / "login_before_submit.png"), full_page=True)
        html = await page.content()
        (diag_dir / "login_before_submit.html").write_text(html, encoding="utf-8")
        inputs = await page.evaluate(
            "() => Array.from(document.querySelectorAll('input')).map(e => ({type:e.type,name:e.name,id:e.id,placeholder:e.placeholder,visible:e.offsetParent!==null}))"
        )
        (diag_dir / "login_inputs.json").write_text(json.dumps(inputs, indent=2), encoding="utf-8")
        print(f"  login inputs: {inputs}")

    email_filled = False
    for sel in [
        "input[type=email]", "input[name=email]", "input[name=username]",
        "input[id*=email]", "input[id*=user]", "input[name=login]", "input[name=user]"
    ]:
        try:
            await page.fill(sel, args.email, timeout=3000)
            print(f"  email filled via {sel}")
            email_filled = True
            break
        except Exception:
            continue

    password_filled = False
    for sel in ["input[type=password]", "input[name=password]", "input[id*=pass]"]:
        try:
            await page.fill(sel, args.password, timeout=3000)
            print(f"  password filled via {sel}")
            password_filled = True
            break
        except Exception:
            continue

    if not email_filled or not password_filled:
        print(f"  login form not found: email_filled={email_filled} password_filled={password_filled}")
        if diag_dir:
            await page.screenshot(path=str(diag_dir / "login_form_not_found.png"), full_page=True)
        return False

    submitted = False
    for sel in [
        "button[type=submit]", "input[type=submit]",
        "button:has-text('Log in')", "button:has-text('Login')",
        "button:has-text('Log')", "button:has-text('Sign in')", "button:has-text('Sign')"
    ]:
        try:
            await page.click(sel, timeout=3000)
            print(f"  submitted via {sel}")
            submitted = True
            break
        except Exception:
            continue

    if not submitted:
        await page.keyboard.press("Enter")
        print("  submitted via Enter")

    await page.wait_for_timeout(7000)

    print(f"  post-login title: {await page.title()!r}")
    print(f"  post-login url: {page.url!r}")

    login_ok = "login" not in page.url.lower()

    if not login_ok and diag_dir:
        await page.screenshot(path=str(diag_dir / "login_failed_after_submit.png"), full_page=True)
        html = await page.content()
        (diag_dir / "login_failed_after_submit.html").write_text(html, encoding="utf-8")
        state = {
            "title": await page.title(),
            "url": page.url,
            "email_filled": email_filled,
            "password_filled": password_filled,
            "submitted": submitted,
        }
        (diag_dir / "login_failed_state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")

    return login_ok


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


async def scan_superbru_matches(
    args: argparse.Namespace, out_dir: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str, dict[str, Any]]:
    from playwright.async_api import async_playwright

    now = datetime.now(timezone.utc)
    window = timedelta(minutes=args.window_minutes)
    results: list[dict[str, Any]] = []
    queued: list[dict[str, Any]] = []
    pool_standing: dict[str, Any] = {"status": "unavailable"}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=args.headless)
        page = await browser.new_page()

        login_diag = out_dir / "login_diagnostics"
        if not await login(page, args, diag_dir=login_diag):
            await browser.close()
            return results, queued, "login_failed", pool_standing

        # ── Leaderboard scrape (reuses auth session, no extra login) ──────────
        skip_lb = getattr(args, "skip_leaderboard", False)
        leader_player = getattr(args, "leader_player", "")
        pool_name_keywords: list[str] = []
        raw_kw = getattr(args, "leaderboard_pool_keywords", "") or ""
        if raw_kw:
            pool_name_keywords = [k.strip() for k in raw_kw.split(",") if k.strip()]

        if not skip_lb and leader_player:
            try:
                lb_rows = await scrape_leaderboard_in_session(page, args.pool_url, pool_name_keywords, out_dir / "leaderboard_diagnostics")
                if lb_rows:
                    chaser_range = float(getattr(args, "chaser_range", 8.0))
                    pool_standing = compute_pool_standing(lb_rows, leader_player, chaser_range)
                    pool_standing["leaderboard"] = lb_rows
                    print(
                        f"  pool standing: {pool_standing.get('status')} "
                        f"rank={pool_standing.get('rank')} "
                        f"gap={pool_standing.get('leader_gap') or pool_standing.get('gap_to_leader')}"
                    )
                else:
                    pool_standing = {"status": "no_leaderboard_rows_found"}
                    print("  leaderboard scrape returned no rows")
                (out_dir / "pool_standing.json").write_text(
                    json.dumps(pool_standing, indent=2, default=str), encoding="utf-8"
                )
            except Exception as exc:
                pool_standing = {"status": "leaderboard_scrape_failed", "error": str(exc)}
                print(f"  leaderboard scrape failed (non-blocking): {exc}")

        # ── Navigate to pool matches page ──────────────────────────────────────
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

            info = await page.evaluate(EXTRACT_MATCH_JS, {"gameId": game_id, "label": label})
            kickoff_dt = parse_kickoff(
                info.get("kickoffText"),
                info.get("kickoffTs"),
                now,
                page_timezone=getattr(args, "page_timezone", "Africa/Johannesburg"),
            )
            time_until = (kickoff_dt - now) if kickoff_dt else None
            in_window = time_until is not None and timedelta(0) <= time_until <= window

            entry: dict[str, Any] = {
                "game_id": game_id,
                "game": label,
                "home_team": home_team,
                "away_team": away_team,
                "kickoff_utc": kickoff_dt.isoformat() if kickoff_dt else None,
                "kickoff_raw": info.get("kickoffText"),
                "kickoff_source": info.get("kickoffSource"),
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

    return results, queued, "ok", pool_standing


async def run(args: argparse.Namespace) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    scan_results, queued, scan_status, pool_standing = await scan_superbru_matches(args, out_dir)
    if scan_status == "login_failed":
        return write_auto_pick_summary(
            out_dir,
            {
                "status": "login_failed",
                "run_at_utc": now.isoformat(),
                "mode": "match_scoped_locked_card_auto_pick",
                "window_minutes": args.window_minutes,
                "page_timezone": getattr(args, "page_timezone", "Africa/Johannesburg"),
                "dry_run": args.dry_run,
                "pick_card_csv": args.pick_card_csv,
                "pool_standing": pool_standing,
                "scan_results": scan_results,
                "queued_count": 0,
                "card_fallback_queued": 0,
                "results": [],
                "submitted": 0,
                "dry_run_count": 0,
                "no_pick_available": 0,
                "submit_failed": 0,
                "submission_failed": 0,
                "fresh_recompute_used": 0,
                "card_fallback_used": 0,
                "pick_changed_vs_card": 0,
                "defensive_picks_used": 0,
                "chase_picks_used": 0,
            },
        )
    queued, card_fallback_entries = merge_pick_card_fallback_queue(
        args,
        datetime.now(timezone.utc),
        scan_results,
        queued,
    )
    if card_fallback_entries:
        print(f"Queued {len(card_fallback_entries)} match(es) from locked-card kickoff fallback.")

    config = None
    config_error: str | None = None
    try:
        config = load_engine_config(args.config)
    except Exception as exc:  # non-blocking: recompute is skipped, card pick is used
        config_error = f"{type(exc).__name__}: {exc}"
        print(f"warning: could not load engine config {args.config!r}: {config_error}. Falling back to committed card picks.")

    # Resolve odds regions/markets from the engine config when not overridden, so the
    # pre-kickoff single-match pull mirrors the configured book universe and stays
    # quota-minimal (markets x regions credits per match) instead of a wide 4-region,
    # 3-market pull. The model ignores spreads while Asian handicap is disabled.
    provider = (getattr(config.providers, "the_odds_api", {}) or {}) if config is not None else {}
    if not args.odds_regions:
        args.odds_regions = provider.get("regions") or "eu"
    if not args.odds_markets:
        args.odds_markets = provider.get("markets") or "h2h,totals"
    print(f"Odds pull scope: regions={args.odds_regions!r} markets={args.odds_markets!r} "
          f"(~{len(args.odds_markets.split(',')) * len(args.odds_regions.split(','))} credits/match)")

    # Pass pool standing to recompute only when leaderboard data is usable.
    effective_pool_standing = (
        pool_standing
        if pool_standing.get("status") not in ("unavailable", "player_not_found", "no_leaderboard_rows_found", "leaderboard_scrape_failed")
        else None
    )
    if effective_pool_standing:
        print(f"Pool standing active: status={effective_pool_standing['status']} rank={effective_pool_standing.get('rank')}")
    else:
        print(f"Pool standing inactive ({pool_standing.get('status')}): picks use engine strategy_mode")

    submitted_results: list[dict[str, Any]] = []
    for entry in queued:
        pick_lookup = find_pick_from_card(entry, args.pick_card_csv)
        entry["pick_lookup"] = pick_lookup
        card_row = pick_lookup.get("card_row") if pick_lookup.get("status") == "found" else None
        card_pick = txt(pick_lookup.get("pick")) if pick_lookup.get("status") == "found" else ""

        if should_keep_existing_pick_until_revision_window(entry, args):
            current_pick = normalise_score_pick(entry.get("current_pick"))
            entry["status"] = "already_current"
            entry["selected_pick"] = current_pick
            entry["pick_source"] = "existing_superbru_pick_early_window"
            entry["pick_strategy"] = "freeze_until_revision_window"
            entry["submit_result"] = {
                "status": "already_current",
                "home_team": entry.get("home_team"),
                "away_team": entry.get("away_team"),
                "new_pick": current_pick,
                "dry_run": False,
                "reason": (
                    "SuperBru already shows a pick and kickoff is outside the final "
                    "revision window; skipped odds recompute to prevent pick churn."
                ),
            }
            submitted_results.append(entry)
            continue

        # Pull this match's odds right before kickoff and recompute a fresh pick so a
        # stale committed card cannot drive the submission. Pool-position intelligence
        # is applied on top of the fresh recompute when leaderboard data is available.
        entry["match_odds"] = fetch_match_odds_snapshot(args, entry, out_dir)
        if config is not None:
            fresh = recompute_pick_from_snapshot(
                entry["match_odds"],
                entry,
                config,
                pool_standing=effective_pool_standing,
                card_row=card_row,
                args=args,
            )
        else:
            fresh = {"status": "skipped", "reason": "config_unavailable", "error": config_error}
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
        entry["pick_strategy"] = fresh.get("strategy", "committed_card_fallback")

        if args.dry_run:
            entry["status"] = "dry_run"
            submitted_results.append(entry)
            continue

        if normalise_score_pick(entry.get("current_pick")) == normalise_score_pick(pick):
            entry["status"] = "already_current"
            entry["submit_result"] = {
                "status": "already_current",
                "home_team": entry.get("home_team"),
                "away_team": entry.get("away_team"),
                "new_pick": pick,
                "dry_run": False,
                "reason": "SuperBru already shows the selected pick; skipped duplicate submit.",
            }
            submitted_results.append(entry)
            continue

        try:
            submit_result = await submit_pick(args, entry["home_team"], entry["away_team"], pick, out_dir)
            if submit_result.get("status") == "login_failed":
                # Each match logs in on a fresh headless session; SuperBru bounced
                # the third login inside one run back to the login page
                # (2026-07-08). One paused retry absorbs that rate-limit flake;
                # a real credential problem still fails loudly on the retry.
                entry["login_retry_used"] = True
                await asyncio.sleep(45)
                submit_result = await submit_pick(args, entry["home_team"], entry["away_team"], pick, out_dir)
            entry["status"] = submit_result.get("status", "unknown")
            entry["submit_result"] = submit_result
        except Exception as exc:
            entry["status"] = "submit_failed"
            entry["error"] = str(exc)
        submitted_results.append(entry)

    submitted_count = sum(1 for item in submitted_results if item.get("status") == "submitted")
    already_current_count = sum(1 for item in submitted_results if item.get("status") == "already_current")
    successful_count = submitted_count + already_current_count
    dry_run_count = sum(1 for item in submitted_results if item.get("status") == "dry_run")
    no_pick_count = sum(1 for item in submitted_results if item.get("status") == "no_pick_available")
    failed_count = sum(
        1
        for item in submitted_results
        if item.get("status") in {"submit_failed", "failed", "login_failed", "unknown"}
    )
    if args.dry_run and queued:
        status = "dry_run"
    elif not queued:
        status = "no_queued_matches"
    elif successful_count == len(queued):
        status = "submitted" if submitted_count else "already_current"
    elif successful_count > 0:
        status = "partial_submission"
    elif failed_count > 0:
        status = "submit_failed"
    elif no_pick_count > 0:
        status = "no_pick_available"
    else:
        status = "no_submission"

    summary = {
        "status": status,
        "run_at_utc": now.isoformat(),
        "mode": "match_scoped_locked_card_auto_pick",
        "window_minutes": args.window_minutes,
        "page_timezone": getattr(args, "page_timezone", "Africa/Johannesburg"),
        "dry_run": args.dry_run,
        "pick_card_csv": args.pick_card_csv,
        "pool_standing": pool_standing,
        "scan_results": scan_results,
        "queued_count": len(queued),
        "card_fallback_queued": len(card_fallback_entries),
        "card_fallback_entries": card_fallback_entries,
        "results": submitted_results,
        "submitted": submitted_count,
        "already_current_count": already_current_count,
        "dry_run_count": dry_run_count,
        "no_pick_available": no_pick_count,
        "submit_failed": sum(1 for item in submitted_results if item.get("status") == "submit_failed"),
        "submission_failed": failed_count,
        "fresh_recompute_used": sum(1 for item in submitted_results if item.get("pick_source") == "live_odds_recompute"),
        "card_fallback_used": sum(1 for item in submitted_results if item.get("pick_source") == "committed_card_fallback"),
        "pick_changed_vs_card": sum(1 for item in submitted_results if item.get("pick_changed_vs_card")),
        "defensive_picks_used": sum(1 for item in submitted_results if str(item.get("pick_strategy", "")).startswith("defensive_leader")),
        "chase_picks_used": sum(1 for item in submitted_results if item.get("pick_strategy") == "private_chase"),
    }

    return write_auto_pick_summary(out_dir, summary)


def write_auto_pick_summary(out_dir: Path, summary: dict[str, Any]) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%dT%H%M%SZ")
    (out_dir / f"{ts}_auto_pick_match_scoped.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    (out_dir / "latest_auto_pick_match_scoped.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Match-scoped Auto Pick using committed SuperBru card.")
    parser.add_argument("--email", default=os.environ.get("SUPERBRU_EMAIL") or os.environ.get("SUPERBRU_USERNAME", ""))
    parser.add_argument("--password", default=os.environ.get("SUPERBRU_PASSWORD", ""))
    parser.add_argument("--login-url", default="https://www.superbru.com/login")
    parser.add_argument("--pool-url", default="https://www.superbru.com/worldcup_predictor/pool_view.php?t=1296&p=13236623&view=matches")
    parser.add_argument(
        "--page-timezone",
        default=os.environ.get("SUPERBRU_PAGE_TIMEZONE", "Africa/Johannesburg"),
        help=(
            "Timezone used for SuperBru text-only fixture times. The pool page displays local "
            "South Africa times by default, while the locked card stores UTC."
        ),
    )
    parser.add_argument("--window-minutes", type=int, default=20)
    parser.add_argument("--headless", dest="headless", action="store_true", default=True)
    parser.add_argument("--headed", dest="headless", action="store_false")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--require-submission",
        action="store_true",
        help=(
            "Exit non-zero when this scheduled window submits zero picks, or when any queued "
            "match is not submitted. Use this in CI so missed Superbru picks cannot pass silently."
        ),
    )
    parser.add_argument("--out-dir", default="outputs/pregame_checks/auto_pick")
    parser.add_argument("--pick-card-csv", default="outputs/final_locked_picks/superbru_final_card.csv")
    parser.add_argument(
        "--late-card-grace-minutes",
        type=int,
        default=5,
        help=(
            "When the page scan misses tabs, allow locked-card fallback matches this many minutes "
            "after kickoff. This mainly protects against small GitHub cron delays."
        ),
    )
    parser.add_argument("--config", default="config.yaml", help="Engine config used to recompute the pick from fresh single-match odds.")
    parser.add_argument("--odds-api-key", default=os.environ.get("THE_ODDS_API_KEY", ""))
    parser.add_argument("--odds-sport", default="soccer_fifa_world_cup")
    # Default regions/markets are resolved from the engine config's the_odds_api
    # provider when left unset, so the pre-kickoff pull uses the same book universe
    # and market structure the card was built on (cheaper and more comparable).
    parser.add_argument("--odds-regions", default=None)
    parser.add_argument("--odds-markets", default=None)
    parser.add_argument("--odds-lookup-window-minutes", type=int, default=90)
    parser.add_argument(
        "--revision-window-minutes",
        type=int,
        default=260,
        help=(
            "If a future SuperBru pick already exists, keep it unchanged until this many "
            "minutes before kickoff. This lets the broad watchdog fill the card early "
            "without re-submitting noisy odds/pool tactic changes every cycle."
        ),
    )
    parser.add_argument("--skip-match-odds", action="store_true")
    # Pool-position intelligence
    parser.add_argument(
        "--leader-player",
        default=os.environ.get("SUPERBRU_PLAYER_NAME", "Danie"),
        help="Your display name on the Superbru leaderboard.",
    )
    parser.add_argument(
        "--leader-safe-buffer",
        type=float,
        default=5.0,
        help="Points ahead threshold below which the defensive strategy activates (default 5).",
    )
    parser.add_argument(
        "--chaser-range",
        type=float,
        default=8.0,
        help="Points window for counting chasers / detecting pursuit (default 8).",
    )
    parser.add_argument(
        "--skip-leaderboard",
        action="store_true",
        help="Skip the leaderboard scrape; picks use the engine strategy_mode from config.",
    )
    parser.add_argument(
        "--leaderboard-pool-keywords",
        default=os.environ.get("SUPERBRU_POOL_KEYWORDS", "Moore Infinity,FIFA WC 26,World Cup 2026"),
        help=(
            "Comma-separated keywords that must appear in the leaderboard page to confirm it is "
            "the correct pool. Prevents accidentally reading another pool's standings. "
            "Default: 'Moore Infinity,FIFA WC 26,World Cup 2026'."
        ),
    )
    return parser


def exit_code_for_result(result: dict[str, Any], args: argparse.Namespace) -> int:
    if result.get("status") == "login_failed":
        return 1
    if getattr(args, "require_submission", False) and not getattr(args, "dry_run", False):
        queued = int(result.get("queued_count") or 0)
        submitted = int(result.get("submitted") or 0)
        already_current = int(result.get("already_current_count") or 0)
        successful = submitted + already_current
        if queued <= 0:
            print(
                "No Superbru matches were queued after page scan + locked-card fallback; "
                "not treating this broad scheduled slot as a submission failure.",
                file=sys.stderr,
            )
            return 0
        if successful <= 0:
            print(
                "ERROR: Superbru auto-pick neither submitted nor confirmed an already-current pick "
                "in a required scheduled window. "
                "Check outputs/pregame_checks/auto_pick/latest_auto_pick_match_scoped.json",
                file=sys.stderr,
            )
            return 2
        if queued and successful < queued:
            print(
                f"ERROR: Superbru auto-pick completed {successful}/{queued} queued picks "
                f"({submitted} submitted, {already_current} already current).",
                file=sys.stderr,
            )
            return 3
    return 0


def write_missing_credentials_summary(args: argparse.Namespace) -> dict[str, Any]:
    return write_auto_pick_summary(
        Path(args.out_dir),
        {
            "status": "missing_credentials",
            "run_at_utc": datetime.now(timezone.utc).isoformat(),
            "mode": "match_scoped_locked_card_auto_pick",
            "window_minutes": args.window_minutes,
            "page_timezone": getattr(args, "page_timezone", "Africa/Johannesburg"),
            "dry_run": args.dry_run,
            "pick_card_csv": args.pick_card_csv,
            "queued_count": 0,
            "results": [],
            "submitted": 0,
            "diagnostic": {
                "SUPERBRU_EMAIL_present": bool(args.email),
                "SUPERBRU_PASSWORD_present": bool(args.password),
            },
        },
    )


def main() -> int:
    args = build_parser().parse_args()
    if not args.email or not args.password:
        write_missing_credentials_summary(args)
        print("ERROR: SUPERBRU_EMAIL and SUPERBRU_PASSWORD must be set", file=sys.stderr)
        return 1
    result = asyncio.run(run(args))
    print("\n" + json.dumps(result, indent=2, default=str))
    return exit_code_for_result(result, args)


if __name__ == "__main__":
    raise SystemExit(main())
