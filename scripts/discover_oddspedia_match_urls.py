from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

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

KNOWN_MULTI_WORD_TEAMS = {
    "cape verde",
    "costa rica",
    "cote divoire",
    "côte divoire",
    "czech republic",
    "dr congo",
    "ivory coast",
    "new zealand",
    "north macedonia",
    "saudi arabia",
    "south africa",
    "south korea",
    "united arab emirates",
    "united states",
}

EVENT_SLUG_RE = re.compile(r"^(?P<slug>[a-z0-9][a-z0-9-]*?)-(?P<match_key>\d+)$")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Discover all Oddspedia match URLs from one or more public seed/listing pages."
    )
    parser.add_argument("--fixtures-csv", default="outputs/final_locked_picks/superbru_final_card.csv")
    parser.add_argument("--seed-urls-csv", default="inputs/oddspedia_seed_urls.csv")
    parser.add_argument("--out-csv", default="inputs/oddspedia_match_urls.csv")
    parser.add_argument("--out-json", default="outputs/oddspedia_url_discovery/oddspedia_url_discovery.json")
    parser.add_argument("--headful", action="store_true")
    parser.add_argument("--timeout-ms", type=int, default=60000)
    parser.add_argument("--settle-ms", type=int, default=8000)
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


def display_text(value: Any) -> str:
    words = []
    for word in norm_text(value).split():
        if word in {"dr", "usa", "uk", "uae"}:
            words.append(word.upper())
        else:
            words.append(word.capitalize())
    return " ".join(words)


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
        print(f"Fixture/card file not found or empty: {path}. Continuing with all discovered Oddspedia events.")
        return pd.DataFrame()
    required = {"home_team", "away_team"}
    missing = required - set(frame.columns)
    if missing:
        print(f"Fixture/card file is missing columns {sorted(missing)}. Continuing without fixture matching.")
        return pd.DataFrame()
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


def canonical_oddspedia_url(url: str) -> str:
    parsed = urlparse(txt(url))
    if not parsed.netloc:
        return txt(url)
    netloc = parsed.netloc.lower().replace("www.", "")
    path = parsed.path.rstrip("/")
    if "oddspedia.com" not in netloc:
        return txt(url)
    return urlunparse(("https", "oddspedia.com", path, "", "", ""))


def extract_oddspedia_event(url: str) -> dict[str, str] | None:
    canonical = canonical_oddspedia_url(url)
    parsed = urlparse(canonical)
    netloc = parsed.netloc.lower().replace("www.", "")
    if "oddspedia.com" not in netloc:
        return None

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[0] != "football":
        return None

    last = parts[-1].lower()
    match = EVENT_SLUG_RE.match(last)
    if not match:
        return None

    slug = match.group("slug")
    match_key = match.group("match_key")
    if slug in {"football", "world", "world-cup"}:
        return None

    return {
        "url": canonical,
        "url_slug": slug,
        "match_key": match_key,
    }


def known_team_slugs(fixtures: pd.DataFrame) -> set[str]:
    known = {slug_text(team) for team in KNOWN_MULTI_WORD_TEAMS}
    known.update(slug_text(team) for team in TEAM_ALIASES)
    for alias_list in TEAM_ALIASES.values():
        known.update(slug_text(alias) for alias in alias_list)
    if not fixtures.empty:
        for _, fixture in fixtures.iterrows():
            known.add(slug_text(fixture.get("home_team")))
            known.add(slug_text(fixture.get("away_team")))
    return {team for team in known if team}


def infer_teams_from_slug(slug: str, known_slugs: set[str]) -> tuple[str, str]:
    tokens = [token for token in slug.split("-") if token]
    if len(tokens) < 2:
        return display_text(slug), ""

    best: tuple[int, int, str, str] | None = None
    for idx in range(1, len(tokens)):
        home_slug = "-".join(tokens[:idx])
        away_slug = "-".join(tokens[idx:])
        score = 0
        if home_slug in known_slugs:
            score += 3
        if away_slug in known_slugs:
            score += 3
        if " ".join(tokens[:idx]) in KNOWN_MULTI_WORD_TEAMS:
            score += 2
        if " ".join(tokens[idx:]) in KNOWN_MULTI_WORD_TEAMS:
            score += 2
        balance_penalty = abs(idx - (len(tokens) - idx))
        candidate = (score, -balance_penalty, home_slug, away_slug)
        if best is None or candidate > best:
            best = candidate

    assert best is not None
    _, _, home_slug, away_slug = best
    return display_text(home_slug), display_text(away_slug)


def best_fixture_match(
    fixtures: pd.DataFrame,
    url: str,
    label: str,
) -> tuple[pd.Series | None, int]:
    if fixtures.empty:
        return None, 0

    best_fixture: pd.Series | None = None
    best_score = 0
    for _, fixture in fixtures.iterrows():
        score = score_link(url, label, txt(fixture.get("home_team")), txt(fixture.get("away_team")))
        if score > best_score:
            best_score = score
            best_fixture = fixture
    return best_fixture, best_score


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
                await page.goto(seed, wait_until="domcontentloaded", timeout=args.timeout_ms)
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


def discover_events(fixtures: pd.DataFrame, links: list[dict[str, str]]) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    known_slugs = known_team_slugs(fixtures)
    events_by_key: dict[str, dict[str, Any]] = {}

    for discovered_order, link in enumerate(links, start=1):
        url = txt(link.get("url"))
        event = extract_oddspedia_event(url)
        if event is None:
            continue

        match_key = event["match_key"]
        canonical_url = event["url"]
        label = txt(link.get("label"))
        record = events_by_key.setdefault(
            match_key,
            {
                "match_key": match_key,
                "url_slug": event["url_slug"],
                "urls": [],
                "labels": [],
                "seed_urls": [],
                "first_discovered_order": discovered_order,
            },
        )
        if canonical_url not in record["urls"]:
            record["urls"].append(canonical_url)
        if label and label not in record["labels"]:
            record["labels"].append(label)
        seed_url = txt(link.get("seed_url"))
        if seed_url and seed_url not in record["seed_urls"]:
            record["seed_urls"].append(seed_url)

    rows: list[dict[str, Any]] = []
    matched_fixture_indices: set[int] = set()

    for record in sorted(events_by_key.values(), key=lambda r: int(r["first_discovered_order"])):
        primary_url = record["urls"][0] if record["urls"] else ""
        alt_url = next((u for u in record["urls"][1:] if u != primary_url), "")
        label = " | ".join(record["labels"])
        best_fixture, match_score = best_fixture_match(fixtures, primary_url, label)

        if best_fixture is not None and match_score >= 100:
            home = txt(best_fixture.get("home_team"))
            away = txt(best_fixture.get("away_team"))
            match_id = txt(best_fixture.get("match_id")) or f"{slug_text(home)}-{slug_text(away)}"
            commence_time = txt(best_fixture.get("commence_time"))
            matched_fixture_indices.add(int(best_fixture.name))
        else:
            home, away = infer_teams_from_slug(record["url_slug"], known_slugs)
            match_id = record["url_slug"]
            commence_time = ""

        rows.append(
            {
                "match_id": match_id,
                "commence_time": commence_time,
                "home_team": home,
                "away_team": away,
                "url": primary_url,
                "alt_url": alt_url,
                "match_key": record["match_key"],
                "match_score": match_score,
                "is_fixture_match": match_score >= 100,
                "link_label": label,
                "seed_url": " | ".join(record["seed_urls"]),
                "url_slug": record["url_slug"],
                "discovered_url_count": len(record["urls"]),
                "discovered_order": record["first_discovered_order"],
            }
        )

    unmatched: list[dict[str, Any]] = []
    if not fixtures.empty:
        for idx, fixture in fixtures.iterrows():
            if int(idx) in matched_fixture_indices:
                continue
            home = txt(fixture.get("home_team"))
            away = txt(fixture.get("away_team"))
            best_url = ""
            best_score = 0
            for link in links:
                url = txt(link.get("url"))
                if not url or "oddspedia" not in url.lower():
                    continue
                score = score_link(url, txt(link.get("label")), home, away)
                if score > best_score:
                    best_score = score
                    best_url = url
            unmatched.append(
                {
                    "commence_time": txt(fixture.get("commence_time")),
                    "home_team": home,
                    "away_team": away,
                    "best_url": best_url,
                    "best_score": best_score,
                }
            )

    stats = {
        "oddspedia_event_count": len(rows),
        "fixture_matched_event_count": sum(1 for row in rows if row["is_fixture_match"]),
        "fixture_unmatched_count": len(unmatched),
    }
    return pd.DataFrame(rows), pd.DataFrame(unmatched), stats


def main() -> int:
    args = build_parser().parse_args()
    fixtures = load_fixtures(Path(args.fixtures_csv))
    seed_urls = load_seed_urls(Path(args.seed_urls_csv))

    import asyncio

    links = asyncio.run(collect_links(seed_urls, args))
    discovered, unmatched, discovery_stats = discover_events(fixtures, links)

    out_csv = Path(args.out_csv)
    out_json = Path(args.out_json)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    discovered.to_csv(out_csv, index=False)
    unmatched_path = out_json.with_name("oddspedia_url_discovery_unmatched.csv")
    unmatched.to_csv(unmatched_path, index=False)

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "seed_url_count": len(seed_urls),
        "link_count": len(links),
        "fixture_count": int(len(fixtures)),
        "out_csv": str(out_csv),
        "unmatched_csv": str(unmatched_path),
        **discovery_stats,
    }
    out_json.write_text(
        json.dumps(
            {
                "summary": summary,
                "links": links,
                "events": discovered.to_dict(orient="records"),
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))
    print(f"Wrote {out_csv}")
    print(f"Wrote {unmatched_path}")
    print(f"Wrote {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
