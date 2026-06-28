from __future__ import annotations

import pytest


_POLYMARKET_SCAN_ENV = (
    "POLYMARKET_QUERIES",
    "POLYMARKET_SCAN_QUERY_MODE",
    "POLYMARKET_MAX_SCAN_QUERIES",
    "POLYMARKET_ADAPTIVE_SCAN_PRIORITY",
)


@pytest.fixture(autouse=True)
def isolate_polymarket_scan_environment(monkeypatch):
    """Keep local launcher overrides from changing deterministic test expectations."""
    for name in _POLYMARKET_SCAN_ENV:
        monkeypatch.delenv(name, raising=False)
