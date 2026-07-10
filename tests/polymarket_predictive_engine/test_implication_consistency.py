"""WO-41 implication-network scanner tests.

The scanner is only trustworthy if it recovers planted structural violations
and refuses a clean synthetic family. These tests keep it measurement-only:
no paper/live trading side effects are allowed in any summary.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from polymarket_predictive_engine import cli, implication_consistency
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.utils import read_csv_rows


def _config(tmp_path: Path, **overrides):
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    raw["implication_networks"].update(
        {
            "event_pages": 1,
            "page_size": 100,
            "request_pause_seconds": 0,
            "deviation_threshold_per_basket": 0.005,
        }
    )
    raw["implication_networks"].update(overrides)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _market(question: str, ask: float, bid: float, slug: str | None = None):
    return {
        "question": question,
        "slug": slug or question.lower().replace(" ", "-").replace("?", ""),
        "bestAsk": ask,
        "bestBid": bid,
        "feesEnabled": False,
        "feeType": "",
    }


def _event(markets):
    return {
        "slug": "world-cup-linked-family",
        "title": "World Cup linked family",
        "volume24hr": 10000,
        "markets": markets,
    }


def test_planted_implication_violations_are_recovered(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    event = _event(
        [
            # Monotone-chain violation: winning is bid above the buyable final leg.
            _market("Will Argentina win the 2026 FIFA World Cup?", 0.43, 0.42, "arg-win"),
            _market("Will Argentina reach the final?", 0.30, 0.29, "arg-final"),
            _market("Will Argentina reach the semifinals?", 0.60, 0.59, "arg-sf"),
            # Continent aggregation violation: Europe bid exceeds France+Germany ask basket.
            _market("Will a team from Europe win the 2026 FIFA World Cup?", 0.76, 0.75, "europe-win"),
            _market("Will France win the 2026 FIFA World Cup?", 0.30, 0.29, "france-win"),
            _market("Will Germany win the 2026 FIFA World Cup?", 0.30, 0.29, "germany-win"),
            # Final-matchup violation: matchup bids sum above Argentina-final ask.
            _market("Will the 2026 FIFA World Cup final be Argentina vs Brazil?", 0.21, 0.20, "arg-bra-final"),
            _market("Will the 2026 FIFA World Cup final be Argentina vs France?", 0.21, 0.20, "arg-fra-final"),
        ]
    )

    def fake_get(url, params=None, timeout=None):
        assert url.endswith("/events")
        return _FakeResponse([event])

    monkeypatch.setattr(implication_consistency.requests, "get", fake_get)

    summary = implication_consistency.scan_implication_networks(cfg)

    assert summary["status"] == "ok"
    assert summary["paper_trading_invoked"] is False
    assert summary["live_trading_invoked"] is False
    assert summary["flagged_by_class"]["monotone_chain"] == 1
    assert summary["flagged_by_class"]["continent_aggregation"] == 1
    assert summary["flagged_by_class"]["final_matchup_sum"] == 1
    assert summary["best_net_violation_by_class"]["monotone_chain"] == 0.12
    assert summary["best_net_violation_by_class"]["continent_aggregation"] == 0.15
    assert summary["best_net_violation_by_class"]["final_matchup_sum"] == 0.1
    rows = read_csv_rows(cfg.output_root / "implication_consistency" / "implication_deviations.csv")
    assert {row["violation_class"] for row in rows} == {
        "monotone_chain",
        "continent_aggregation",
        "final_matchup_sum",
    }


def test_consistent_synthetic_family_flags_nothing(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    event = _event(
        [
            _market("Will Argentina win the 2026 FIFA World Cup?", 0.20, 0.19, "arg-win"),
            _market("Will Argentina reach the final?", 0.40, 0.39, "arg-final"),
            _market("Will Argentina reach the semifinals?", 0.50, 0.49, "arg-sf"),
            _market("Will a team from Europe win the 2026 FIFA World Cup?", 0.60, 0.59, "europe-win"),
            _market("Will France win the 2026 FIFA World Cup?", 0.30, 0.29, "france-win"),
            _market("Will Germany win the 2026 FIFA World Cup?", 0.30, 0.29, "germany-win"),
            _market("Will the 2026 FIFA World Cup final be Argentina vs Brazil?", 0.20, 0.19, "arg-bra-final"),
            _market("Will the 2026 FIFA World Cup final be Argentina vs France?", 0.20, 0.19, "arg-fra-final"),
        ]
    )

    def fake_get(url, params=None, timeout=None):
        return _FakeResponse([event])

    monkeypatch.setattr(implication_consistency.requests, "get", fake_get)

    summary = implication_consistency.scan_implication_networks(cfg)

    assert summary["status"] == "ok"
    assert summary["flagged_deviations"] == 0
    assert summary["flagged_by_class"] == {}
    rows = read_csv_rows(cfg.output_root / "implication_consistency" / "implication_deviations.csv")
    assert rows == []


def test_malformed_partial_families_are_skipped_not_crashed(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    bad_event = {
        "slug": "bad-family",
        "title": "Bad family",
        "markets": [
            {"question": "Will something vague happen?", "bestAsk": 0.4, "feesEnabled": False},
            {"question": "Will Argentina reach the final?", "feesEnabled": False},
            "not a market",
        ],
    }

    def fake_get(url, params=None, timeout=None):
        return _FakeResponse([bad_event])

    monkeypatch.setattr(implication_consistency.requests, "get", fake_get)

    summary = implication_consistency.scan_implication_networks(cfg)

    assert summary["status"] == "ok"
    assert summary["events_scanned"] == 1
    assert summary["classified_legs"] == 0
    assert summary["flagged_deviations"] == 0


def test_disabled_mode_writes_summary_without_network(tmp_path, monkeypatch):
    cfg = _config(tmp_path, enabled=False)

    def fake_get(url, params=None, timeout=None):  # pragma: no cover - should not be called
        raise AssertionError("network should not be called when disabled")

    monkeypatch.setattr(implication_consistency.requests, "get", fake_get)

    summary = implication_consistency.scan_implication_networks(cfg)

    assert summary["status"] == "disabled"
    assert summary["paper_trading_invoked"] is False
    assert summary["live_trading_invoked"] is False


def test_cli_exposes_implication_network_command():
    assert "scan-implication-networks" in cli.COMMANDS
