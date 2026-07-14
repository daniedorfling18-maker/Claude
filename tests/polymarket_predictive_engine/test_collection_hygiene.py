"""WO-71 API-spend suppression and corpus-retention tests."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import gzip
import os
from pathlib import Path

from polymarket_predictive_engine import corpus_retention
from polymarket_predictive_engine.cli import COMMANDS
from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.corpus_retention import (
    REGISTERED_ARCHIVE_MAX_MB,
    REGISTERED_WINDOWS,
    _settings as retention_settings,
    run_corpus_retention,
)
from polymarket_predictive_engine.sharp_odds_fetch import fetch_sharp_odds
from polymarket_predictive_engine.sharp_spend_suppression import (
    build_sharp_spend_suppression,
    permitted_markets,
)
from polymarket_predictive_engine.utils import read_csv_rows, read_json, write_csv


def _cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "sharp_anchor_coverage": {"zero_join_cycles_before_flag": 2},
            "sharp_odds_fetch": {
                "sports": [{"key": "basketball_nba", "markets": "h2h"}],
                "output_path": str(tmp_path / "sharp.csv"),
                "fetch_interval_minutes": 0,
                "validate_sports": True,
            },
            "collection_hygiene": {
                "zero_join_cycles_before_suppression": 2,
                "suppressed_probe_interval_hours": 24,
                "websocket_feature_raw_days": 3,
                "trade_print_raw_days": 2,
                "official_book_raw_days": 2,
                "training_archive_compaction_age_hours": 1,
                "training_archive_days": 5,
                "corpus_archive_max_mb": 100,
                "orphan_temp_max_age_hours": 1,
            },
            "websocket_market_data": {"feature_retention_hours": 72},
        },
        path=tmp_path / "config.yaml",
    )


def _coverage_history(cfg: EngineConfig, rows: list[dict]) -> None:
    write_csv(
        cfg.governance_root / "sharp_anchor_coverage_history.csv",
        rows,
        fieldnames=[
            "anchor_generated_at_utc",
            "sport",
            "market_key",
            "rows_fetched",
            "rows_mapped",
        ],
    )


def _gzip_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else ["source_timestamp"]
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_zero_join_family_enters_suppression_and_join_exits(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    configs = [{"sport": "basketball_nba", "markets": ["h2h"]}]
    _coverage_history(
        cfg,
        [
            {"anchor_generated_at_utc": "2026-07-10T00:00:00Z", "sport": "basketball_nba", "market_key": "h2h", "rows_fetched": 8, "rows_mapped": 0},
            {"anchor_generated_at_utc": "2026-07-11T00:00:00Z", "sport": "basketball_nba", "market_key": "h2h", "rows_fetched": 8, "rows_mapped": 0},
        ],
    )

    suppressed = build_sharp_spend_suppression(
        cfg,
        configs,
        as_of=datetime(2026, 7, 11, 1, tzinfo=timezone.utc),
    )

    assert suppressed["status"] == "active"
    assert suppressed["families"][0]["action"] == "suppress"
    assert permitted_markets(suppressed, "basketball_nba", ["h2h"]) == ([], ["h2h"])
    assert suppressed["config_changed"] is False

    _coverage_history(
        cfg,
        [
            {"anchor_generated_at_utc": "2026-07-10T00:00:00Z", "sport": "basketball_nba", "market_key": "h2h", "rows_fetched": 8, "rows_mapped": 0},
            {"anchor_generated_at_utc": "2026-07-11T00:00:00Z", "sport": "basketball_nba", "market_key": "h2h", "rows_fetched": 8, "rows_mapped": 0},
            {"anchor_generated_at_utc": "2026-07-12T00:00:00Z", "sport": "basketball_nba", "market_key": "h2h", "rows_fetched": 8, "rows_mapped": 3},
        ],
    )
    restored = build_sharp_spend_suppression(
        cfg,
        configs,
        as_of=datetime(2026, 7, 12, 1, tzinfo=timezone.utc),
    )

    assert restored["status"] == "clear"
    assert restored["families"][0]["action"] == "fetch"
    assert "normal cadence restored" in restored["families"][0]["reason"]


def test_suppressed_family_moves_to_slow_probe_when_due(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    configs = [{"sport": "basketball_nba", "markets": ["h2h"]}]
    _coverage_history(
        cfg,
        [
            {"anchor_generated_at_utc": "2026-07-10T00:00:00Z", "sport": "basketball_nba", "market_key": "h2h", "rows_fetched": 8, "rows_mapped": 0},
            {"anchor_generated_at_utc": "2026-07-11T00:00:00Z", "sport": "basketball_nba", "market_key": "h2h", "rows_fetched": 8, "rows_mapped": 0},
        ],
    )

    probe = build_sharp_spend_suppression(
        cfg,
        configs,
        as_of=datetime(2026, 7, 12, 1, tzinfo=timezone.utc),
    )

    assert probe["families"][0]["action"] == "slow_probe"
    assert permitted_markets(probe, "basketball_nba", ["h2h"]) == (["h2h"], [])


def test_fetch_spends_no_paid_request_while_family_is_suppressed(tmp_path: Path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)
    monkeypatch.setenv("THE_ODDS_API_KEY", "synthetic-key")
    _coverage_history(
        cfg,
        [
            {"anchor_generated_at_utc": "2999-07-10T00:00:00Z", "sport": "basketball_nba", "market_key": "h2h", "rows_fetched": 8, "rows_mapped": 0},
            {"anchor_generated_at_utc": "2999-07-11T00:00:00Z", "sport": "basketball_nba", "market_key": "h2h", "rows_fetched": 8, "rows_mapped": 0},
        ],
    )
    calls: list[str] = []

    class Response:
        headers = {}

        def raise_for_status(self):
            return None

        def json(self):
            return [{"key": "basketball_nba"}]

    def fake_get(url, **kwargs):
        calls.append(url)
        if not url.endswith("/sports"):
            raise AssertionError("suppressed family must not issue a paid odds request")
        return Response()

    monkeypatch.setattr("polymarket_predictive_engine.sharp_odds_fetch.requests.get", fake_get)

    result = fetch_sharp_odds(cfg)

    assert calls == ["https://api.the-odds-api.com/v4/sports"]
    assert result["requests_used"] == 0
    assert result["spend_suppression"]["requests_suppressed_this_run"] == 1
    assert result["status"] == "skipped_coverage_suppression"
    artifact = read_json(cfg.governance_root / "sharp_fetch_suppression.json")
    assert artifact["families"][0]["suppressed"] is True
    assert artifact["paper_trading_invoked"] is False
    assert artifact["live_trading_invoked"] is False


def test_retention_compacts_before_prune_keeps_fresh_rows_and_never_touches_ledgers(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    output = cfg.output_root
    trade_path = output / "polymarket_trade_prints" / "trade_prints.csv"
    write_csv(
        trade_path,
        [
            {"trade_id": "old", "timestamp": "2026-07-09T00:00:00Z", "collected_at_utc": "2026-07-09T00:00:00Z"},
            {"trade_id": "fresh", "timestamp": "2026-07-12T12:00:00Z", "collected_at_utc": "2026-07-12T12:00:00Z"},
        ],
    )
    official = output / "maker_carry" / "official_books" / "market-1.csv.gz"
    _gzip_csv(
        official,
        [
            {"condition_id": "m1", "source_timestamp": "2026-07-09T00:00:00Z"},
            {"condition_id": "m1", "source_timestamp": "2026-07-12T12:00:00Z"},
        ],
    )
    training = output / "polymarket_training_archive" / "features_old.csv.gz"
    _gzip_csv(training, [{"asset_id": "a", "source_timestamp": "2026-07-09T00:00:00Z"}])
    old_mtime = datetime(2026, 7, 9, tzinfo=timezone.utc).timestamp()
    os.utime(training, (old_mtime, old_mtime))
    stale_temp = output / "polymarket_training" / ".features.csv.1.tmp"
    stale_temp.parent.mkdir(parents=True, exist_ok=True)
    stale_temp.write_bytes(b"incomplete" * 100)
    stale_mtime = datetime(2026, 7, 2, tzinfo=timezone.utc).timestamp()
    os.utime(stale_temp, (stale_mtime, stale_mtime))

    ledger = output / "performance" / "cost_ledger.csv"
    write_csv(ledger, [{"cost_ref": "immutable", "cost_usd": 1.0}])
    ledger_before = ledger.read_bytes()

    result = run_corpus_retention(
        cfg,
        as_of=datetime(2026, 7, 13, tzinfo=timezone.utc),
    )

    assert result["status"] == "ok"
    assert [row["trade_id"] for row in read_csv_rows(trade_path)] == ["fresh"]
    with gzip.open(official, "rt", encoding="utf-8") as handle:
        official_rows = list(csv.DictReader(handle))
    assert [row["source_timestamp"] for row in official_rows] == ["2026-07-12T12:00:00Z"]
    archives = list((output / "polymarket_training_archive").glob("daily_*.csv.gz"))
    assert archives
    assert not training.exists()
    assert not stale_temp.exists()
    assert ledger.read_bytes() == ledger_before
    assert result["decision_ledgers_touched"] is False
    assert result["bytes_archived"] > 0
    assert result["bytes_pruned_raw"] > 0
    assert result["orphan_temp_cleanup"]["bytes_deleted"] > 0
    assert "projected_daily_growth_bytes" in result["disk_projection"]
    assert result["paper_trading_invoked"] is False
    assert result["live_trading_invoked"] is False


def test_retention_counts_producer_managed_websocket_rows_without_materialising(tmp_path: Path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)
    websocket_path = cfg.output_root / "polymarket_training" / "websocket_market_features.csv"
    write_csv(
        websocket_path,
        [
            {"asset_id": "a", "source_timestamp": "2026-07-12T00:00:00Z"},
            {"asset_id": "b", "source_timestamp": "2026-07-12T00:01:00Z"},
        ],
    )
    original = corpus_retention.read_csv_rows

    def guarded_read(path):
        if Path(path) == websocket_path:
            raise AssertionError("producer-managed websocket rows must be streamed")
        return original(path)

    monkeypatch.setattr(corpus_retention, "read_csv_rows", guarded_read)

    result = run_corpus_retention(
        cfg,
        as_of=datetime(2026, 7, 13, tzinfo=timezone.utc),
        dry_run=True,
    )

    websocket = next(row for row in result["corpora"] if row["corpus"] == "websocket_features")
    assert websocket["rows_seen"] == 2
    assert websocket["rows_retained"] == 2


def test_training_archive_compaction_streams_and_deduplicates_across_runs(tmp_path: Path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)
    root = cfg.output_root / "polymarket_training_archive"
    first = root / "features_first.csv.gz"
    second = root / "features_second.csv.gz"
    duplicate = {"asset_id": "a", "source_timestamp": "2026-07-09T00:00:00Z", "best_bid": "0.4"}
    _gzip_csv(first, [duplicate, {"asset_id": "b", "source_timestamp": "2026-07-09T00:01:00Z", "best_bid": "0.5"}])
    _gzip_csv(second, [duplicate])
    old_mtime = datetime(2026, 7, 9, tzinfo=timezone.utc).timestamp()
    os.utime(first, (old_mtime, old_mtime))
    os.utime(second, (old_mtime, old_mtime))
    original = corpus_retention._read_csv_any

    def guarded_materialisation(path):
        if Path(path).name.startswith(("features_", "daily_training_archive_")):
            raise AssertionError("training archive compaction must stream")
        return original(path)

    monkeypatch.setattr(corpus_retention, "_read_csv_any", guarded_materialisation)

    first_run = run_corpus_retention(
        cfg,
        as_of=datetime(2026, 7, 13, tzinfo=timezone.utc),
    )

    archives = list(root.glob("daily_training_archive_*.csv.gz"))
    assert len(archives) == 1
    with gzip.open(archives[0], "rt", encoding="utf-8", newline="") as handle:
        assert [row["asset_id"] for row in csv.DictReader(handle)] == ["a", "b"]
    training = next(row for row in first_run["corpora"] if row["corpus"] == "training_archive")
    assert training["rows_archived"] == 3
    assert not first.exists()
    assert not second.exists()

    third = root / "features_third.csv.gz"
    _gzip_csv(third, [duplicate, {"asset_id": "c", "source_timestamp": "2026-07-09T00:02:00Z", "best_bid": "0.6"}])
    os.utime(third, (old_mtime, old_mtime))
    run_corpus_retention(
        cfg,
        as_of=datetime(2026, 7, 13, 0, 5, tzinfo=timezone.utc),
    )

    with gzip.open(archives[0], "rt", encoding="utf-8", newline="") as handle:
        assert [row["asset_id"] for row in csv.DictReader(handle)] == ["a", "b", "c"]


def test_archive_age_bound_and_dry_run_are_deterministic(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    old_archive = cfg.output_root / "polymarket_training_archive" / "daily_trade_prints_20260701_abcdef123456.csv.gz"
    _gzip_csv(old_archive, [{"trade_id": "old", "timestamp": "2026-07-01T00:00:00Z"}])

    dry = run_corpus_retention(
        cfg,
        as_of=datetime(2026, 7, 13, tzinfo=timezone.utc),
        dry_run=True,
    )
    assert old_archive.exists()
    assert dry["archive"]["status"] == "would_prune"

    applied = run_corpus_retention(
        cfg,
        as_of=datetime(2026, 7, 13, 0, 5, tzinfo=timezone.utc),
    )
    assert not old_archive.exists()
    assert applied["archive"]["files_pruned"] == 1


def test_retention_overrides_only_tighten_and_paths_cannot_be_extended(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg.raw["collection_hygiene"].update(
        {
            "trade_print_raw_days": 999,
            "official_book_raw_days": 1,
            "training_archive_days": 999,
            "corpus_archive_max_mb": 9999,
            "corpora": ["performance/cost_ledger.csv"],
        }
    )

    settings = retention_settings(cfg)

    assert settings["trade_print_raw_days"] == REGISTERED_WINDOWS["trade_print_raw_days"]
    assert settings["official_book_raw_days"] == 1
    assert settings["training_archive_days"] == REGISTERED_WINDOWS["training_archive_days"]
    assert settings["archive_max_mb"] == REGISTERED_ARCHIVE_MAX_MB
    assert "corpora" not in settings


def test_cli_and_daily_scheduler_wire_collection_hygiene() -> None:
    scheduler = Path("scripts/run_vps_ops_scheduler.sh").read_text(encoding="utf-8")

    assert "corpus-retention" in COMMANDS
    assert "polymarket_predictive_engine.cli corpus-retention" in scheduler
    assert scheduler.index("corpus-retention") < scheduler.index("anchor-ledgers")
