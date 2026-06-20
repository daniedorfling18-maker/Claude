from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import pandas as pd

TEAM_ALIASES: dict[str, list[str]] = {
    "bosnia & herzegovina": ["bosnia", "bosnia herzegovina", "bosnia and herzegovina", "bih"],
    "czech republic": ["czech republic", "czechia"],
    "ivory coast": ["ivory coast", "cote divoire", "côte divoire", "cote d ivoire"],
    "curacao": ["curacao", "curaçao"],
    "dr congo": ["dr congo", "d r congo", "democratic republic of congo", "congo dr", "cod"],
    "south korea": ["south korea", "korea republic", "republic of korea"],
    "saudi arabia": ["saudi arabia", "ksa"],
    "united states": ["united states", "usa", "usmnt"],
    "new zealand": ["new zealand", "nzl"],
    "cape verde": ["cape verde", "cabo verde"],
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Discover Oddspedia match URLs from one or more public seed/listing pages."
    )
    parser.add_argument("--fixtures-csv", default="outputs/final_locked_picks/superbru_final_card.csv")
    parser.add_argument("--seed-urls-csv", default="inputs/oddspedia_seed_urls.csv")
    parser.add_argument("--out-csv", default="inputs/oddspedia_match_urls.csv")
    parser.add_argument("--out-json", default="outputs/oddspedia_url_discovery/oddspedia_url_discovery.json")
    parser.add_argument("--headful", action="store_true")
    parser.add_argument("--timeout-ms", type=int, default=45000)
    parser.add_argument("--settle-ms", type=int, default=6000)
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def norm_text(value: Any) -> str:
    text = txt(value).lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def slug_text(value: Any) -> str:
    return norm_text(value).replace(" ", "-")


def aliases(team: str) -> list[str]:
    base = norm_text(team)
    raw_aliases = TEAM_ALIASES.get(base, [base])
    out = {base, slug_text(base)}
    for alias in raw_aliases:
        out.add(norm_text(alias))
        out.add(slug_text(alias))
    return sorted(a for a in out if a)


def score_link(href: str, label: str, home: str, away: str) -> int:
    haystacks = [norm_text(href), slug_text(href), norm_text(label), slug_text(label)]
    home_aliases = aliases(home)
    away_aliases = aliases(away)
    home_hit = any(alias in hay for alias in home_aliases for hay in haystacks)
    away_hit = any(alias in hay for alias in away_aliases for hay in haystacks)
    if home_hit and away_hit:
        return 100
    if home_hit or away_hit:
        return 25
    return 0


def load_csv(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    return pd.read_csv(p).fillna("")


def load_fixtures(path: Path) -> pd.DataFrame:
    frame = load_csv(path)
    if frame.empty:
        raise FileNotFoundError(f"Missing or empty fixtures/card file: {path}")
    required = {"home_team", "away_team"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Fixtures/card file is missing columns: {sorted(missing)}")
    return frame


def load_seed_urls(path: Path) -> list[str]:
    frame = load_csv(path)
    if frame.empty:
        raise FileNotFoundError(
            f"Missing or empty seed URL file: {path}. Add at least one public Oddspedia competition/fixtures/listing URL."
        )
    urls = [txt(row.get("url")) for _, row in frame.iterrows() if txt(row.get("url"))]
    if not urls:
        raise ValueError(f"No URL values found in {path}")
    return urls


async def collect_links(seed_urls: list[str], args: argparse.Namespace) -> list[dict[str, str]]:
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "Playwright is required. Install with: pip install playwright && python -m playwright install chromium"
        ) from exc

    all_links: list[dict[str, str]] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not args.headful)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36"
            )
        )
        page = await context.new_page()
        for seed in seed_urls:
            print(f"Opening seed page: {seed}")
            try:
                await page.goto(seed, wait_until="networkidle", timeout=args.timeout_ms)
                await page.wait_for_timeout(args.settle_ms)
                links = await page.eval_on_selector_all(
                    "a[href]",
                    "els => els.map(a => ({href: a.getAttribute('href'), text: a.innerText || a.textContent || ''}))",
                )
                for link in links:
                    href = txt(link.get("href"))
                    if not href:
                        continue
                    full_url = urljoin(seed, href)
                    all_links.append({"seed_url": seed, "url": full_url, "label": txt(link.get("text"))})
            except Exception as exc:
                all_links.append({"seed_url": seed, "url": "", "label": "", "error": str(exc)})
        await context.close()
        await browser.close()
    return all_links


def match_links(fixtures: pd.DataFrame, links: list[dict[str, str]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for _, fixture in fixtures.iterrows():
        home = txt(fixture.get("home_team"))
        away = txt(fixture.get("away_team"))
        best: dict[str, Any] | None = None
        for link in links:
            url = txt(link.get("url"))
            if not url or "oddspedia" not in url.lower():
                continue
            score = score_link(url, txt(link.get("label")), home, away)
            if score <= 0:
                continue
            candidate = {
                "match_id": txt(fixture.get("match_id")) or f"{slug_text(home)}-{slug_text(away)}",
                "commence_time": txt(fixture.get("commence_time")),
                "home_team": home,
                "away_team": away,
                "url": url,
                "link_label": txt(link.get("label")),
                "seed_url": txt(link.get("seed_url")),
                "match_score": score,
            }
            if best is None or score > int(best["match_score"]):
                best = candidate
        if best and int(best["match_score"]) >= 100:
            rows.append(best)
        else:
            unmatched.append(
                {
                    "commence_time": txt(fixture.get("commence_time")),
                    "home_team": home,
                    "away_team": away,
                    "best_url": best.get("url") if best else "",
                    "best_score": best.get("match_score") if best else 0,
                }
            )
    return pd.DataFrame(rows), pd.DataFrame(unmatched)


def main() -> int:
    args = build_parser().parse_args()
    fixtures = load_fixtures(Path(args.fixtures_csv))
    seed_urls = load_seed_urls(Path(args.seed_urls_csv))

    import asyncio

    links = asyncio.run(collect_links(seed_urls, args))
    matched, unmatched = match_links(fixtures, links)

    out_csv = Path(args.out_csv)
    out_json = Path(args.out_json)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    matched.to_csv(out_csv, index=False)
    unmatched_path = out_json.with_name("oddspedia_url_discovery_unmatched.csv")
    unmatched.to_csv(unmatched_path, index=False)

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "seed_url_count": len(seed_urls),
        "link_count": len(links),
        "fixture_count": int(len(fixtures)),
        "matched_count": int(len(matched)),
        "unmatched_count": int(len(unmatched)),
        "out_csv": str(out_csv),
        "unmatched_csv": str(unmatched_path),
    }
    out_json.write_text(json.dumps({"summary": summary, "links": links}, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"Wrote {out_csv}")
    print(f"Wrote {unmatched_path}")
    print(f"Wrote {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
