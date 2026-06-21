from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

KEYWORDS = ("correct", "score", "scoreline", "probability", "odds", "market")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Discover public JSON/XHR payloads on Oddspedia match pages that may contain correct-score probability grids. "
            "This does not bypass logins, captchas, robots controls or paywalls."
        )
    )
    parser.add_argument("--urls-csv", default="inputs/oddspedia_match_urls.csv")
    parser.add_argument("--out-dir", default="outputs/oddspedia_api_discovery")
    parser.add_argument("--headful", action="store_true")
    parser.add_argument("--timeout-ms", type=int, default=45000)
    parser.add_argument("--settle-ms", type=int, default=6000)
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")
    return cleaned[:120] or "payload"


def looks_relevant_url(url: str) -> bool:
    lower = url.lower()
    return any(word in lower for word in KEYWORDS) or "oddspedia" in lower and "api" in lower


def looks_relevant_json(value: Any) -> bool:
    try:
        blob = json.dumps(value).lower()
    except Exception:
        blob = str(value).lower()
    return any(word in blob for word in KEYWORDS) and re.search(r"\b\d+\s*-\s*\d+\b", blob) is not None


def read_urls(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing URL file: {path}. Create it from inputs/oddspedia_match_urls.template.csv")
    rows = pd.read_csv(path).fillna("").to_dict(orient="records")
    result: list[dict[str, str]] = []
    for row in rows:
        url = txt(row.get("url"))
        if not url:
            continue
        result.append(
            {
                "match_id": txt(row.get("match_id")),
                "home_team": txt(row.get("home_team")),
                "away_team": txt(row.get("away_team")),
                "url": url,
            }
        )
    if not result:
        raise ValueError(f"No usable URLs found in {path}")
    return result


async def run_browser(args: argparse.Namespace, rows: list[dict[str, str]], out_dir: Path) -> list[dict[str, Any]]:
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "Playwright is required for discovery. Install with: pip install playwright && python -m playwright install chromium"
        ) from exc

    discoveries: list[dict[str, Any]] = []
    payload_dir = out_dir / "payloads"
    payload_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not args.headful)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36"
            )
        )
        page = await context.new_page()

        async def handle_response(response: Any) -> None:
            url = response.url
            headers = {k.lower(): v for k, v in response.headers.items()}
            content_type = headers.get("content-type", "")
            if "json" not in content_type and not looks_relevant_url(url):
                return
            try:
                data = await response.json()
            except Exception:
                return
            if not (looks_relevant_url(url) or looks_relevant_json(data)):
                return
            payload_name = safe_name(url.split("?")[0].split("/")[-1] or "payload")
            payload_path = payload_dir / f"{len(discoveries)+1:04d}_{payload_name}.json"
            payload_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            discoveries.append(
                {
                    "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                    "url": url,
                    "status": response.status,
                    "content_type": content_type,
                    "payload_file": str(payload_path),
                }
            )

        page.on("response", handle_response)

        for row in rows:
            print(f"Opening {row['home_team']} vs {row['away_team']} :: {row['url']}")
            try:
                await page.goto(row["url"], wait_until="domcontentloaded", timeout=args.timeout_ms)
                await page.wait_for_timeout(args.settle_ms)
            except Exception as exc:
                discoveries.append(
                    {
                        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                        "source_page": row["url"],
                        "home_team": row["home_team"],
                        "away_team": row["away_team"],
                        "error": str(exc),
                    }
                )

        await context.close()
        await browser.close()
    return discoveries


def main() -> int:
    args = build_parser().parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = read_urls(Path(args.urls_csv))

    import asyncio

    discoveries = asyncio.run(run_browser(args, rows, out_dir))
    discovery_path = out_dir / "oddspedia_discovered_payloads.json"
    discovery_jsonl = out_dir / "oddspedia_discovered_payloads.jsonl"
    discovery_path.write_text(json.dumps(discoveries, indent=2), encoding="utf-8")
    with discovery_jsonl.open("w", encoding="utf-8") as handle:
        for item in discoveries:
            handle.write(json.dumps(item) + "\n")

    print(json.dumps({"discovery_count": len(discoveries), "out_dir": str(out_dir)}, indent=2))
    print(f"Wrote {discovery_path}")
    print(f"Wrote {discovery_jsonl}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
