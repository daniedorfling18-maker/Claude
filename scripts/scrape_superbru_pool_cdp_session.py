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
        description="Attach to an already-open Chrome CDP session and capture Superbru pool tables from a logged-in session."
    )
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    parser.add_argument("--pool-url", default="https://www.superbru.com/worldcup_predictor/pool.php?p=13236623&tab=matches#tab=matches")
    parser.add_argument("--out-dir", default="outputs/superbru_pool")
    parser.add_argument("--leaderboard-out", default="inputs/pool_leaderboard_auto.csv")
    parser.add_argument("--settle-ms", type=int, default=12000)
    parser.add_argument("--timeout-ms", type=int, default=90000)
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def normalise_columns(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out.columns = [re.sub(r"\s+", "_", txt(c).lower()).strip("_") or f"col_{i}" for i, c in enumerate(out.columns)]
    return out


def flatten_text(value: Any) -> str:
    return re.sub(r"\s+", " ", txt(value))


EXTRACT_JS = r"""
() => {
  const tables = Array.from(document.querySelectorAll('table')).map((table, idx) => ({
    index: idx,
    id: table.id || '',
    className: table.className || '',
    caption: table.caption ? table.caption.innerText : '',
    text: table.innerText.slice(0, 20000),
    html: table.outerHTML
  }));
  const bodyText = document.body ? document.body.innerText.slice(0, 80000) : '';
  return {
    currentUrl: window.location.href,
    title: document.title || '',
    bodyText,
    tables,
    tableCount: tables.length,
    scrapedAtUtc: new Date().toISOString()
  };
}
"""


def table_score(frame: pd.DataFrame) -> int:
    cols = " ".join(map(str, frame.columns)).lower()
    sample = " ".join(frame.astype(str).head(10).fillna("").values.ravel()).lower()
    score = 0
    for token in ["player", "name", "points", "pts", "rank", "position", "pos"]:
        if token in cols:
            score += 3
        if token in sample:
            score += 1
    return score


def guess_leaderboard_table(tables: list[pd.DataFrame]) -> pd.DataFrame:
    if not tables:
        return pd.DataFrame()
    ranked = sorted(tables, key=table_score, reverse=True)
    return ranked[0].copy()


def find_player_column(frame: pd.DataFrame) -> str | None:
    for col in frame.columns:
        low = col.lower()
        if any(token in low for token in ["player", "name", "member", "participant", "user"]):
            return col
    # fallback: first mostly text column
    for col in frame.columns:
        values = frame[col].map(txt)
        non_empty = values[values != ""]
        if len(non_empty) and non_empty.map(lambda x: bool(re.search(r"[A-Za-z]", x))).mean() >= 0.6:
            return col
    return None


def find_points_column(frame: pd.DataFrame) -> str | None:
    preferred = []
    for col in frame.columns:
        low = col.lower()
        if any(token in low for token in ["points", "pts", "score", "total"]):
            preferred.append(col)
    candidates = preferred or list(frame.columns)
    best_col = None
    best_numeric = -1.0
    for col in candidates:
        vals = pd.to_numeric(frame[col].astype(str).str.extract(r"(-?\d+(?:\.\d+)?)")[0], errors="coerce")
        ratio = float(vals.notna().mean()) if len(vals) else 0.0
        if ratio > best_numeric:
            best_numeric = ratio
            best_col = col
    return best_col if best_numeric > 0 else None


def leaderboard_from_tables(tables: list[pd.DataFrame]) -> pd.DataFrame:
    raw = guess_leaderboard_table(tables)
    if raw.empty:
        return pd.DataFrame(columns=["player", "current_points"])
    frame = normalise_columns(raw).fillna("")
    player_col = find_player_column(frame)
    points_col = find_points_column(frame)
    if not player_col or not points_col:
        return pd.DataFrame(columns=["player", "current_points"])
    out = pd.DataFrame({
        "player": frame[player_col].map(flatten_text),
        "current_points": pd.to_numeric(frame[points_col].astype(str).str.extract(r"(-?\d+(?:\.\d+)?)")[0], errors="coerce"),
    })
    out = out[(out["player"] != "") & out["current_points"].notna()].copy()
    out = out.drop_duplicates("player")
    return out


async def scrape(args: argparse.Namespace) -> dict[str, Any]:
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except Exception as exc:
        raise RuntimeError("Install with: pip install playwright && python -m playwright install chromium") from exc

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(args.cdp_url)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(args.pool_url, wait_until="domcontentloaded", timeout=args.timeout_ms)
        except PlaywrightTimeoutError:
            print("Timeout loading pool page; checking current page state.")
        await page.wait_for_timeout(args.settle_ms)
        state = await page.evaluate(EXTRACT_JS)
        await browser.close()
        return state


def main() -> int:
    args = build_parser().parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    import asyncio
    state = asyncio.run(scrape(args))

    raw_json = out_dir / "superbru_pool_capture.json"
    body_txt = out_dir / "superbru_pool_body_text.txt"
    tables_dir = out_dir / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    raw_json.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    body_txt.write_text(txt(state.get("bodyText")), encoding="utf-8")

    parsed_tables: list[pd.DataFrame] = []
    for item in state.get("tables", []):
        html = txt(item.get("html"))
        try:
            frames = pd.read_html(html)
        except Exception:
            frames = []
        for frame_idx, frame in enumerate(frames):
            parsed = normalise_columns(frame).fillna("")
            parsed_tables.append(parsed)
            table_name = f"table_{int(item.get('index', len(parsed_tables))):02d}_{frame_idx:02d}.csv"
            parsed.to_csv(tables_dir / table_name, index=False)

    leaderboard = leaderboard_from_tables(parsed_tables)
    leaderboard_path = Path(args.leaderboard_out)
    leaderboard_path.parent.mkdir(parents=True, exist_ok=True)
    leaderboard.to_csv(leaderboard_path, index=False)

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "pool_url": args.pool_url,
        "current_url": state.get("currentUrl"),
        "title": state.get("title"),
        "table_count": state.get("tableCount"),
        "parsed_table_count": len(parsed_tables),
        "leaderboard_rows": int(len(leaderboard)),
        "raw_json": str(raw_json),
        "body_text": str(body_txt),
        "tables_dir": str(tables_dir),
        "leaderboard_out": str(leaderboard_path),
    }
    summary_path = out_dir / "superbru_pool_capture_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    print(f"Wrote {leaderboard_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
