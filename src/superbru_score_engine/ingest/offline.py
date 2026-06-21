from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .base import MatchOdds
from .normalise import normalise_generic_events


@dataclass
class OfflineJsonProvider:
    path: str | Path

    name: str = "offline_json"

    def fetch_odds(self) -> list[MatchOdds]:
        try:
            data = json.loads(Path(self.path).read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Failed to load offline odds from {self.path}: {exc}") from exc
        return normalise_generic_events(data)
