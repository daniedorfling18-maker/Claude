from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


RESULT_COLUMNS = [
    "match_id", "commence_time", "home_team", "away_team", "actual_home_goals",
    "actual_away_goals", "actual_score", "actual_outcome", "status", "is_completed",
    "score_source", "source_url_type", "source_url", "current_url", "source_name",
    "scraped_at_utc",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Attach to an already-open Chrome CDP session and extract Oddspedia final scores/results."
    )
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    parser.add_argument("--urls-csv", default="inputs/oddspedia_match_urls.csv")
    parser.add_argument("--out-csv", default="outputs/backtesting/oddspedia_results_backfill.csv")
    parser.add_argument("--out-json", default="outputs/backtesting/oddspedia_results_backfill_summary.json")
    parser.add_argument("--cache-csv", default="outputs/backtesting/oddspedia_results_backfill_cache.csv")
    parser.add_argument("--diagnostics-dir", default="outputs/backtesting/oddspedia_results_diagnostics")
    parser.add_argument("--settle-ms", type=int, default=9000)
    parser.add_argument("--timeout-ms", type=int, default=90000)
    parser.add_argument("--max-matches", type=int, default=0)
    parser.add_argument("--completed-only", action="store_true")
    parser.add_argument("--force-full-refresh", action="store_true")
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return txt(value).lower() in {"true", "1", "yes", "y", "completed"}


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
    rows: list[dict[str, str]] = []
    for _, r in frame.iterrows():
        row = {k: txt(r.get(k)) for k in frame.columns}
        if not row.get("match_id"):
            row["match_id"] = f"{slugify(row.get('home_team'))}-{slugify(row.get('away_team'))}"
        if row.get("url") or row.get("alt_url"):
            rows.append(row)
    return rows[:max_matches] if max_matches and max_matches > 0 else rows


def load_cache(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=RESULT_COLUMNS)
    try:
        frame = pd.read_csv(path).fillna("")
    except Exception:
        return pd.DataFrame(columns=RESULT_COLUMNS)
    for col in RESULT_COLUMNS:
        if col not in frame.columns:
            frame[col] = ""
    return frame[RESULT_COLUMNS].copy()


def row_identity(row: dict[str, Any]) -> str:
    mid = txt(row.get("match_id"))
    if mid:
        return mid
    return f"{slugify(row.get('home_team'))}-{slugify(row.get('away_team'))}"


def row_rank(row: dict[str, Any]) -> tuple[int, str]:
    score = 0
    if boolish(row.get("is_completed")):
        score += 100
    if txt(row.get("actual_score")):
        score += 20
    if txt(row.get("score_source")):
        score += 5
    return score, txt(row.get("scraped_at_utc"))


def normalise_result_row(row: dict[str, Any]) -> dict[str, Any]:
    out = {col: row.get(col, "") for col in RESULT_COLUMNS}
    if not txt(out.get("match_id")):
        out["match_id"] = f"{slugify(out.get('home_team'))}-{slugify(out.get('away_team'))}"
    out["is_completed"] = boolish(out.get("is_completed"))
    return out


def cache_to_map(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for _, row in frame.iterrows():
        norm = normalise_result_row({k: row.get(k, "") for k in frame.columns})
        key = row_identity(norm)
        if key:
            out[key] = norm
    return out


def select_rows_for_scrape(
    rows: list[dict[str, str]],
    cache: dict[str, dict[str, Any]],
    force_full_refresh: bool,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    if force_full_refresh or not cache:
        return rows, {
            "cache_mode": "force_full_refresh" if force_full_refresh else "cold_start",
            "cached_completed_count": sum(1 for r in cache.values() if boolish(r.get("is_completed")) and txt(r.get("actual_score"))),
            "skipped_completed_cached_count": 0,
        }

    selected: list[dict[str, str]] = []
    skipped = 0
    for row in rows:
        key = row_identity(row)
        cached = cache.get(key)
        if cached and boolish(cached.get("is_completed")) and txt(cached.get("actual_score")):
            skipped += 1
            continue
        selected.append(row)

    return selected, {
        "cache_mode": "incremental",
        "cached_completed_count": sum(1 for r in cache.values() if boolish(r.get("is_completed")) and txt(r.get("actual_score"))),
        "skipped_completed_cached_count": skipped,
    }


def merge_results(
    cached: dict[str, dict[str, Any]],
    scraped: list[dict[str, Any]],
    completed_only: bool,
) -> tuple[list[dict[str, Any]], int]:
    merged = dict(cached)
    changed = 0
    for row in scraped:
        norm = normalise_result_row(row)
        if completed_only and not boolish(norm.get("is_completed")):
            continue
        key = row_identity(norm)
        if not key:
            continue
        existing = merged.get(key)
        if existing is None:
            merged[key] = norm
            changed += 1
            continue
        if row_rank(norm) >= row_rank(existing):
            before_score = txt(existing.get("actual_score"))
            before_status = txt(existing.get("status"))
            after_score = txt(norm.get("actual_score"))
            after_status = txt(norm.get("status"))
            if before_score != after_score or before_status != after_status:
                changed += 1
            merged[key] = norm

    rows = list(merged.values())
    if completed_only:
        rows = [r for r in rows if boolish(r.get("is_completed"))]
    return rows, changed


EXTRACT_RESULT_JS = r"""
() => {
  const candidates = [];
  try { candidates.push(['nuxt_state_event', window.__NUXT__?.state?.event]); } catch(e) {}
  try { candidates.push(['nuxt_event', window.__NUXT__?.event]); } catch(e) {}
  try { candidates.push(['store_state_event', window.$nuxt?.$store?.state?.event]); } catch(e) {}
  let sourceName = '';
  let event = null;
  for (const [name, candidate] of candidates) {
    if (candidate) { sourceName = name; event = candidate; break; }
  }
  let serialisedEvent = null;
  try { serialisedEvent = event ? JSON.parse(JSON.stringify(event)) : null; } catch(e) { serialisedEvent = null; }
  return {
    sourceName,
    event: serialisedEvent,
    currentUrl: window.location.href,
    title: document.title || '',
    bodyText: document.body ? document.body.innerText.slice(0, 50000) : ''
  };
}
"""

STATUS_WORDS_COMPLETE = {
    "complete", "completed", "finished", "ended", "ft", "full time", "full-time", "after penalties", "aet"
}
STATUS_WORDS_SCHEDULED = {"scheduled", "not started", "upcoming", "postponed", "cancelled", "canceled"}


def as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        return None
    s = txt(value)
    if re.fullmatch(r"\d+", s):
        return int(s)
    return None


def normalise_status(value: Any) -> str:
    s = txt(value)
    if not s:
        return ""
    low = re.sub(r"\s+", " ", s.lower())
    for word in STATUS_WORDS_COMPLETE:
        if low == word or word in low:
            return "completed"
    for word in STATUS_WORDS_SCHEDULED:
        if low == word or word in low:
            return word.replace(" ", "_")
    if "live" in low or "half" in low or "minute" in low:
        return "live"
    return s


def flatten_status_candidates(obj: Any, limit: int = 1000) -> list[str]:
    out: list[str] = []
    seen = 0

    def walk(item: Any) -> None:
        nonlocal seen
        if seen >= limit:
            return
        seen += 1
        if isinstance(item, dict):
            for k, v in item.items():
                lk = str(k).lower()
                if any(token in lk for token in ["status", "state", "phase", "period"]):
                    text = txt(v)
                    if text and len(text) <= 80:
                        out.append(text)
                walk(v)
        elif isinstance(item, list):
            for v in item:
                walk(v)

    walk(obj)
    return out


def score_from_mapping(mapping: dict[str, Any]) -> tuple[int | None, int | None, str]:
    key_pairs = [
        ("home", "away"),
        ("homeScore", "awayScore"),
        ("home_score", "away_score"),
        ("homeTeamScore", "awayTeamScore"),
        ("homeCurrent", "awayCurrent"),
        ("currentHome", "currentAway"),
        ("score1", "score2"),
        ("team1", "team2"),
        ("participant1", "participant2"),
        ("competitor1", "competitor2"),
    ]
    lowered = {str(k).lower(): k for k in mapping.keys()}
    for left, right in key_pairs:
        lk = lowered.get(left.lower())
        rk = lowered.get(right.lower())
        if lk is None or rk is None:
            continue
        h = as_int(mapping.get(lk))
        a = as_int(mapping.get(rk))
        if h is not None and a is not None and 0 <= h <= 25 and 0 <= a <= 25:
            return h, a, f"mapping:{lk}/{rk}"

    name = txt(mapping.get("name") or mapping.get("label") or mapping.get("type")).lower()
    if name in {"current", "fulltime", "full_time", "ft", "regular"}:
        h = as_int(mapping.get("home"))
        a = as_int(mapping.get("away"))
        if h is not None and a is not None:
            return h, a, f"named-score:{name}"

    return None, None, ""


def score_from_event(event: Any, limit: int = 5000) -> tuple[int | None, int | None, str]:
    candidates: list[tuple[int, int, str]] = []
    seen = 0

    def walk(item: Any, path: str = "event") -> None:
        nonlocal seen
        if seen >= limit:
            return
        seen += 1
        if isinstance(item, dict):
            h, a, source = score_from_mapping(item)
            if h is not None and a is not None:
                priority = 0
                low_path = path.lower()
                if any(token in low_path for token in ["score", "result", "scores"]):
                    priority += 10
                if any(token in low_path for token in ["full", "current", "ft"]):
                    priority += 5
                candidates.append((priority, h, a, f"{path}:{source}"))
            for k, v in item.items():
                walk(v, f"{path}.{k}")
        elif isinstance(item, list):
            if len(item) == 2:
                h = as_int(item[0])
                a = as_int(item[1])
                if h is not None and a is not None and 0 <= h <= 25 and 0 <= a <= 25:
                    priority = 6 if any(token in path.lower() for token in ["score", "result"]) else 1
                    candidates.append((priority, h, a, f"{path}:two-number-list"))
            for idx, v in enumerate(item):
                walk(v, f"{path}[{idx}]")

    walk(event)
    if not candidates:
        return None, None, ""
    best = sorted(candidates, key=lambda x: x[0], reverse=True)[0]
    return best[1], best[2], best[3]


def score_from_text(body: str, title: str, home: str, away: str) -> tuple[int | None, int | None, str]:
    combined = f"{title}\n{body}"
    low = combined.lower()
    snippets: list[str] = []
    home_l = home.lower()
    away_l = away.lower()
    if home_l and away_l:
        hi = low.find(home_l)
        ai = low.find(away_l)
        if hi >= 0 and ai >= 0:
            start = max(0, min(hi, ai) - 500)
            end = min(len(combined), max(hi, ai) + 700)
            snippets.append(combined[start:end])
    snippets.append(title)
    snippets.append(body[:5000])

    patterns = [
        r"(?<!\d)(\d{1,2})\s*[-:]\s*(\d{1,2})(?!\d)",
        r"(?<!\d)(\d{1,2})\s+FT\s+(\d{1,2})(?!\d)",
    ]
    for source_idx, snippet in enumerate(snippets):
        for pattern in patterns:
            for m in re.finditer(pattern, snippet, flags=re.IGNORECASE):
                h = int(m.group(1))
                a = int(m.group(2))
                if 0 <= h <= 25 and 0 <= a <= 25:
                    return h, a, f"text:{source_idx}:{m.group(0)}"
    return None, None, ""


def infer_status(event: Any, body_text: str) -> str:
    for cand in flatten_status_candidates(event):
        status = normalise_status(cand)
        if status:
            return status
    low = body_text.lower()
    if any(token in low for token in ["full time", "full-time", "finished", "ended"]):
        return "completed"
    if any(token in low for token in ["upcoming", "scheduled", "not started"]):
        return "scheduled"
    if any(token in low for token in ["live", "half time", "minute"]):
        return "live"
    return "unknown"


def outcome(home_goals: int | None, away_goals: int | None) -> str:
    if home_goals is None or away_goals is None:
        return "unknown"
    if home_goals > away_goals:
        return "home"
    if home_goals < away_goals:
        return "away"
    return "draw"


def extract_result(row: dict[str, str], state: dict[str, Any], candidate_type: str, candidate_url: str) -> dict[str, Any]:
    event = state.get("event")
    body = txt(state.get("bodyText"))
    title = txt(state.get("title"))
    h, a, source = score_from_event(event)
    if h is None or a is None:
        h, a, source = score_from_text(body, title, row.get("home_team", ""), row.get("away_team", ""))
    status = infer_status(event, body)
    return {
        "match_id": row.get("match_id", ""),
        "commence_time": row.get("commence_time", ""),
        "home_team": row.get("home_team", ""),
        "away_team": row.get("away_team", ""),
        "actual_home_goals": h,
        "actual_away_goals": a,
        "actual_score": f"{h}-{a}" if h is not None and a is not None else "",
        "actual_outcome": outcome(h, a),
        "status": status,
        "is_completed": status == "completed" or (h is not None and a is not None and status not in {"scheduled", "postponed", "cancelled", "canceled"}),
        "score_source": source,
        "source_url_type": candidate_type,
        "source_url": candidate_url,
        "current_url": txt(state.get("currentUrl")),
        "source_name": txt(state.get("sourceName")),
        "scraped_at_utc": datetime.now(timezone.utc).isoformat(),
    }


async def scrape(args: argparse.Namespace, rows: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except Exception as exc:
        raise RuntimeError("Install with: pip install playwright && python -m playwright install chromium") from exc

    diagnostics_dir = Path(args.diagnostics_dir)
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(args.cdp_url)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()

        for idx, row in enumerate(rows, start=1):
            candidates: list[tuple[str, str]] = []
            if row.get("url"):
                candidates.append(("url", row["url"]))
            if row.get("alt_url") and row.get("alt_url") != row.get("url"):
                candidates.append(("alt_url", row["alt_url"]))
            extracted: dict[str, Any] | None = None
            last_state: dict[str, Any] | None = None
            for candidate_type, candidate_url in candidates:
                print(f"[{idx}/{len(rows)}] {row.get('home_team')} vs {row.get('away_team')} :: {candidate_type} :: {candidate_url}")
                try:
                    await page.goto(candidate_url, wait_until="domcontentloaded", timeout=args.timeout_ms)
                except PlaywrightTimeoutError:
                    print("  timeout; checking current page state")
                await page.wait_for_timeout(args.settle_ms)
                state = await page.evaluate(EXTRACT_RESULT_JS)
                last_state = state
                result = extract_result(row, state, candidate_type, candidate_url)
                if result.get("actual_score") or result.get("status") not in {"unknown", ""}:
                    extracted = result
                    print(f"  result={result.get('actual_score') or 'no-score'} status={result.get('status')} source={result.get('score_source')}")
                    break
                print("  no result found on this candidate")
            if extracted is None:
                extracted = {
                    "match_id": row.get("match_id", ""),
                    "commence_time": row.get("commence_time", ""),
                    "home_team": row.get("home_team", ""),
                    "away_team": row.get("away_team", ""),
                    "actual_home_goals": None,
                    "actual_away_goals": None,
                    "actual_score": "",
                    "actual_outcome": "unknown",
                    "status": "unknown",
                    "is_completed": False,
                    "score_source": "",
                    "source_url_type": "",
                    "source_url": "",
                    "current_url": txt((last_state or {}).get("currentUrl")),
                    "source_name": txt((last_state or {}).get("sourceName")),
                    "scraped_at_utc": datetime.now(timezone.utc).isoformat(),
                }
                diag_id = safe_name(row.get("match_id"))
                body_file = diagnostics_dir / f"{diag_id}_body_text.txt"
                state_file = diagnostics_dir / f"{diag_id}_last_state.json"
                body_file.write_text(txt((last_state or {}).get("bodyText")), encoding="utf-8")
                state_file.write_text(json.dumps({"row": row, "last_state": last_state}, indent=2, default=str), encoding="utf-8")
                diagnostics.append({
                    "match_id": row.get("match_id", ""),
                    "home_team": row.get("home_team", ""),
                    "away_team": row.get("away_team", ""),
                    "body_file": str(body_file),
                    "state_file": str(state_file),
                })
            if not args.completed_only or bool(extracted.get("is_completed")):
                results.append(extracted)
        await browser.close()
    return results, diagnostics


def main() -> int:
    args = build_parser().parse_args()
    rows = load_rows(Path(args.urls_csv), args.max_matches)
    if not rows:
        raise ValueError(f"No URL rows found in {args.urls_csv}")

    out_csv = Path(args.out_csv)
    out_json = Path(args.out_json)
    cache_csv = Path(args.cache_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    cache_csv.parent.mkdir(parents=True, exist_ok=True)

    cache_frame = load_cache(cache_csv)
    if cache_frame.empty and out_csv.exists():
        cache_frame = load_cache(out_csv)
    cache = cache_to_map(cache_frame)

    rows_to_scrape, cache_plan = select_rows_for_scrape(rows, cache, args.force_full_refresh)
    if cache_plan["cache_mode"] == "incremental":
        print(
            f"Using cached Oddspedia results. Skipping {cache_plan['skipped_completed_cached_count']} completed matches; "
            f"scraping {len(rows_to_scrape)} missing or incomplete matches."
        )

    import asyncio
    results, diagnostics = asyncio.run(scrape(args, rows_to_scrape)) if rows_to_scrape else ([], [])
    merged_rows, changed_count = merge_results(cache, results, args.completed_only)

    out = pd.DataFrame([normalise_result_row(r) for r in merged_rows])
    for col in RESULT_COLUMNS:
        if col not in out.columns:
            out[col] = ""
    if not out.empty:
        out = out[RESULT_COLUMNS].sort_values(["commence_time", "match_id"]).reset_index(drop=True)
    else:
        out = pd.DataFrame(columns=RESULT_COLUMNS)

    out.to_csv(out_csv, index=False)
    out.to_csv(cache_csv, index=False)

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_match_count": len(rows),
        "scrape_candidate_count": len(rows_to_scrape),
        "result_row_count": len(out),
        "completed_result_count": sum(1 for _, r in out.iterrows() if boolish(r.get("is_completed"))),
        "score_found_count": sum(1 for _, r in out.iterrows() if bool(txt(r.get("actual_score")))),
        "new_or_updated_result_count": changed_count,
        "diagnostic_count": len(diagnostics),
        "diagnostics": diagnostics,
        "out_csv": str(out_csv),
        "cache_csv": str(cache_csv),
        **cache_plan,
    }
    out_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(json.dumps(payload, indent=2, default=str))
    print(f"Wrote {out_csv}")
    print(f"Wrote {cache_csv}")
    print(f"Wrote {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
