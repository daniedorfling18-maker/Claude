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
        description="Dump Oddspedia page state, resource URLs and JSON-ish network responses for debugging probability extraction."
    )
    parser.add_argument("--urls-csv", default="inputs/oddspedia_match_urls_test.csv")
    parser.add_argument("--out-dir", default="outputs/oddspedia_page_debug")
    parser.add_argument("--headful", action="store_true")
    parser.add_argument("--timeout-ms", type=int, default=60000)
    parser.add_argument("--settle-ms", type=int, default=45000)
    return parser


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")
    return cleaned[:120] or "payload"


def load_urls(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing URL CSV: {path}")
    rows = pd.read_csv(path).fillna("").to_dict(orient="records")
    out: list[dict[str, str]] = []
    for row in rows:
        url = txt(row.get("url"))
        if not url:
            continue
        out.append(
            {
                "match_id": txt(row.get("match_id")) or safe_name(url.rsplit("/", 1)[-1]),
                "home_team": txt(row.get("home_team")),
                "away_team": txt(row.get("away_team")),
                "url": url,
            }
        )
    if not out:
        raise ValueError(f"No URL rows found in {path}")
    return out


async def run_debug(args: argparse.Namespace, rows: list[dict[str, str]], out_dir: Path) -> list[dict[str, Any]]:
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except Exception as exc:
        raise RuntimeError("Install Playwright with: pip install playwright && python -m playwright install chromium") from exc

    out_dir.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, Any]] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not args.headful)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122 Safari/537.36",
            locale="en-GB",
        )
        page = await context.new_page()

        for row in rows:
            match_id = safe_name(row["match_id"])
            network_items: list[dict[str, Any]] = []
            json_dir = out_dir / f"{match_id}_network_json"
            json_dir.mkdir(parents=True, exist_ok=True)

            async def handle_response(response: Any) -> None:
                url = response.url
                headers = {k.lower(): v for k, v in response.headers.items()}
                content_type = headers.get("content-type", "")
                if "json" not in content_type and "/api/" not in url and "oddspedia" not in url:
                    return
                item: dict[str, Any] = {
                    "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                    "url": url,
                    "status": response.status,
                    "content_type": content_type,
                }
                try:
                    body = await response.text()
                    item["body_preview"] = body[:500]
                    if "json" in content_type or body[:1] in ("{", "["):
                        file_name = json_dir / f"{len(network_items)+1:04d}_{safe_name(url.rsplit('/', 1)[-1])}.txt"
                        file_name.write_text(body, encoding="utf-8")
                        item["body_file"] = str(file_name)
                except Exception as exc:
                    item["error"] = str(exc)
                network_items.append(item)

            page.on("response", handle_response)
            print(f"Opening {row['home_team']} vs {row['away_team']} :: {row['url']}")
            try:
                await page.goto(row["url"], wait_until="domcontentloaded", timeout=args.timeout_ms)
            except PlaywrightTimeoutError:
                print("WARNING: domcontentloaded timed out; continuing with current page state.")
            await page.wait_for_timeout(args.settle_ms)

            state = await page.evaluate(
                """
                () => {
                  const resources = performance.getEntriesByType('resource').map(r => r.name);
                  const nuxt = window.__NUXT__ || null;
                  const store = window.$nuxt && window.$nuxt.$store ? window.$nuxt.$store.state : null;
                  const bodyText = document.body ? document.body.innerText : '';
                  return {resources, nuxt, store, bodyText};
                }
                """
            )

            state_path = out_dir / f"{match_id}_page_state.json"
            resources_path = out_dir / f"{match_id}_resource_urls.txt"
            body_path = out_dir / f"{match_id}_body_text.txt"
            network_path = out_dir / f"{match_id}_network_summary.json"
            state_path.write_text(json.dumps({"row": row, "nuxt": state.get("nuxt"), "store": state.get("store")}, indent=2, default=str), encoding="utf-8")
            resources_path.write_text("\n".join(state.get("resources") or []), encoding="utf-8")
            body_path.write_text(state.get("bodyText") or "", encoding="utf-8")
            network_path.write_text(json.dumps(network_items, indent=2), encoding="utf-8")

            combined_text = json.dumps(state, default=str).lower()
            summaries.append(
                {
                    "match_id": row["match_id"],
                    "home_team": row["home_team"],
                    "away_team": row["away_team"],
                    "url": row["url"],
                    "resource_count": len(state.get("resources") or []),
                    "network_item_count": len(network_items),
                    "has_nuxt": state.get("nuxt") is not None,
                    "has_store": state.get("store") is not None,
                    "contains_probability": "probab" in combined_text or "probability" in combined_text,
                    "contains_correct_score": "correct score" in combined_text or "correct_score" in combined_text,
                    "contains_smartbet": "smartbet" in combined_text or "smart bet" in combined_text,
                    "state_file": str(state_path),
                    "resource_file": str(resources_path),
                    "body_file": str(body_path),
                    "network_file": str(network_path),
                    "network_json_dir": str(json_dir),
                }
            )
            page.remove_listener("response", handle_response)

        await context.close()
        await browser.close()
    return summaries


def main() -> int:
    args = build_parser().parse_args()
    rows = load_urls(Path(args.urls_csv))
    out_dir = Path(args.out_dir)
    import asyncio

    summaries = asyncio.run(run_debug(args, rows, out_dir))
    summary_path = out_dir / "oddspedia_page_debug_summary.json"
    summary_csv = out_dir / "oddspedia_page_debug_summary.csv"
    summary_path.write_text(json.dumps(summaries, indent=2), encoding="utf-8")
    pd.DataFrame(summaries).to_csv(summary_csv, index=False)
    print(json.dumps({"rows": len(summaries), "out_dir": str(out_dir), "summary_csv": str(summary_csv)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

