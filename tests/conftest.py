"""Suite-wide safeguards for hermetic offline tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _offline_notification_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let an inherited production-only notification URL reach a test."""
    monkeypatch.delenv("OPS_OWNER_NTFY_TOPIC_URL", raising=False)
