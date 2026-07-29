"""Secret-safe request latency observations for bounded batch orchestrators."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit


LATENCY_LOG_ENV = "PM_HARVEST_LATENCY_LOG"


def record_upstream_latency(url: str, duration_seconds: float) -> None:
    """Append host/path-tail latency when a harvest supplied a private log path.

    Query strings, URL userinfo, headers, and request parameters are deliberately
    never accepted by this interface, so token ids and credentials cannot enter
    the receipt through request telemetry.
    """

    log_path = os.environ.get(LATENCY_LOG_ENV)
    if not log_path:
        return
    parsed = urlsplit(url)
    host = (parsed.hostname or "unknown").lower()
    path_tail = Path(parsed.path.rstrip("/")).name or "/"
    row = {
        "host": host,
        "path_tail": path_tail,
        "duration_seconds": round(max(0.0, float(duration_seconds)), 6),
    }
    encoded = (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    try:
        descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    except OSError:
        return
    try:
        try:
            os.write(descriptor, encoded)
        except OSError:
            pass
    finally:
        os.close(descriptor)
