from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import requests

from .config import EngineConfig, load_config
from .utils import now_utc, write_csv, write_json


def _read_csv_path(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig", errors="replace") as f:
        return [dict(row) for row in csv.DictReader(f)]


def _read_json_path(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        data = payload.get("data") or payload.get("rows") or payload.get("results")
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        return [payload]
    return []


def _fetch_url(url: str, feed_format: str, timeout: int) -> list[dict[str, Any]]:
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    if feed_format == "csv":
        lines = response.text.splitlines()
        return [dict(row) for row in csv.DictReader(lines)]
    payload = response.json()
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        data = payload.get("data") or payload.get("rows") or payload.get("results")
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        return [payload]
    return []


def collect_external_feeds(cfg: EngineConfig) -> dict[str, Any]:
    settings = cfg.raw.get("external_feeds", {})
    feeds = list(settings.get("feeds", []) or [])
    timeout = int(settings.get("request_timeout_seconds", 30))
    rows: list[dict[str, Any]] = []
    quality: list[dict[str, Any]] = []

    for feed in feeds:
        name = str(feed.get("name", "unnamed_feed"))
        feed_format = str(feed.get("format", "json")).lower()
        try:
            if feed.get("path"):
                path = Path(feed["path"])
                feed_rows = _read_csv_path(path) if feed_format == "csv" else _read_json_path(path)
            elif feed.get("url"):
                feed_rows = _fetch_url(str(feed["url"]), feed_format, timeout)
            else:
                feed_rows = []
            for row in feed_rows:
                rows.append({"feed_name": name, "collected_at_utc": now_utc(), **row})
            quality.append({"feed_name": name, "status": "ok", "rows": len(feed_rows)})
        except Exception as exc:
            quality.append({"feed_name": name, "status": "error", "error": str(exc), "rows": 0})

    out_root = cfg.output_root / "polymarket_external_feeds"
    out_root.mkdir(parents=True, exist_ok=True)
    write_csv(out_root / "external_feed_rows.csv", rows)
    write_csv(out_root / "external_feed_quality.csv", quality)
    summary = {"status": "collected", "feeds": len(feeds), "rows": len(rows), "quality_rows": len(quality), "output_file": str(out_root / "external_feed_rows.csv")}
    write_json(out_root / "external_feed_summary.json", summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return collect_external_feeds(load_config(config_path))
