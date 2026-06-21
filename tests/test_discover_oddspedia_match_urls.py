from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "discover_oddspedia_match_urls.py"
spec = importlib.util.spec_from_file_location("discover_oddspedia_match_urls", MODULE_PATH)
discover = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(discover)


def test_discover_events_keeps_found_events_outside_locked_fixtures() -> None:
    fixtures = pd.DataFrame(
        [
            {
                "match_id": "spain-saudi-arabia",
                "commence_time": "2026-06-21T16:00:00Z",
                "home_team": "Spain",
                "away_team": "Saudi Arabia",
            }
        ]
    )
    links = [
        {
            "seed_url": "https://oddspedia.com/football/world/world-cup",
            "url": "https://oddspedia.com/football/spain-saudi-arabia-1654576",
            "label": "Spain Saudi Arabia",
        },
        {
            "seed_url": "https://oddspedia.com/football/world/world-cup",
            "url": "https://oddspedia.com/football/argentina-austria-1654585",
            "label": "Argentina Austria",
        },
    ]

    discovered, unmatched, stats = discover.discover_events(fixtures, links)

    assert len(discovered) == 2
    assert set(discovered["match_key"]) == {"1654576", "1654585"}
    assert discovered.loc[discovered["match_key"].eq("1654576"), "is_fixture_match"].iloc[0] is True
    assert discovered.loc[discovered["match_key"].eq("1654585"), "is_fixture_match"].iloc[0] is False
    assert unmatched.empty
    assert stats["oddspedia_event_count"] == 2
    assert stats["fixture_matched_event_count"] == 1


def test_discover_events_groups_duplicate_event_urls_and_keeps_alt_url() -> None:
    fixtures = pd.DataFrame()
    links = [
        {
            "seed_url": "https://oddspedia.com/football/world/world-cup",
            "url": "https://oddspedia.com/football/germany-ivory-coast-1654563?foo=bar",
            "label": "Germany Ivory Coast",
        },
        {
            "seed_url": "https://oddspedia.com/football/world/world-cup",
            "url": "https://oddspedia.com/football/ivory-coast-germany-1654563",
            "label": "Ivory Coast Germany",
        },
    ]

    discovered, unmatched, stats = discover.discover_events(fixtures, links)

    assert len(discovered) == 1
    row = discovered.iloc[0]
    assert row["match_key"] == "1654563"
    assert row["url"] == "https://oddspedia.com/football/germany-ivory-coast-1654563"
    assert row["alt_url"] == "https://oddspedia.com/football/ivory-coast-germany-1654563"
    assert row["home_team"] == "Germany"
    assert row["away_team"] == "Ivory Coast"
    assert unmatched.empty
    assert stats["oddspedia_event_count"] == 1
