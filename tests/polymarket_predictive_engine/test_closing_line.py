from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from polymarket_predictive_engine.closing_line import (
    EVIDENCE_INSUFFICIENT,
    EVIDENCE_NEGATIVE,
    EVIDENCE_POSITIVE,
    build_closing_line_value,
    build_quote_history,
    position_clv_row,
)
from polymarket_predictive_engine.config import EngineConfig, load_config
from polymarket_predictive_engine.risk import kelly_fraction, risk_decision, shrunk_kelly_fraction
from polymarket_predictive_engine.utils import read_json, write_csv

AS_OF = datetime(2026, 7, 2, 0, 0, tzinfo=timezone.utc)


def _cfg(tmp_path: Path, settings: dict | None = None) -> EngineConfig:
    raw = {"paths": {"output_root": str(tmp_path / "outputs")}}
    if settings is not None:
        raw["closing_line_value"] = settings
    return EngineConfig(raw=raw, path=tmp_path / "cfg.yaml")


def _position(token: str, cohort: str, *, entry: float, opened: str, close: str, status: str = "open") -> dict:
    return {
        "shadow_position_id": f"pos_{token}",
        "signal_cohort": cohort,
        "category": "sports_other",
        "market_id": f"mkt_{token}",
        "token_id": token,
        "market_slug": f"slug-{token}",
        "question": f"Question {token}?",
        "status": status,
        "opened_at": opened,
        "close_time": close,
        "entry_price": entry,
    }


def _quote(token: str, when: str, mid: float, bid: float | None = None) -> dict:
    return {
        "asset_id": token,
        "source_timestamp": when,
        "collected_at_utc": when,
        "midpoint": mid,
        "best_bid": bid if bid is not None else "",
        "best_ask": "",
    }


def test_closing_line_uses_last_pre_close_quote(tmp_path: Path):
    cfg = _cfg(tmp_path)
    write_csv(
        cfg.output_root / "polymarket_shadow" / "shadow_positions.csv",
        [
            _position("tokA", "sports_other|worldcup", entry=0.50, opened="2026-07-01T10:00:00Z", close="2026-07-01T12:00:00Z"),
            _position("tokB", "sports_other|worldcup", entry=0.40, opened="2026-07-01T10:00:00Z", close="2026-07-05T12:00:00Z"),
            _position("tokC", "crypto|btc", entry=0.30, opened="2026-07-01T10:00:00Z", close="2026-07-01T12:00:00Z"),
        ],
    )
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            _quote("tokA", "2026-07-01T10:30:00Z", 0.52, bid=0.51),
            _quote("tokA", "2026-07-01T11:59:00Z", 0.56, bid=0.55),
            # Post-close quote must not be used as the closing line.
            _quote("tokA", "2026-07-01T12:30:00Z", 0.90, bid=0.89),
            _quote("tokB", "2026-07-01T11:00:00Z", 0.38, bid=0.37),
        ],
    )

    summary = build_closing_line_value(cfg, as_of=AS_OF)

    assert summary["positions_seen"] == 3
    assert summary["positions_scored"] == 2
    assert summary["positions_skipped_no_usable_quotes"] == 1
    assert summary["final_line_positions"] == 1
    assert summary["provisional_line_positions"] == 1
    assert summary["paper_trading_invoked"] is False
    assert summary["live_trading_invoked"] is False

    written = read_json(cfg.governance_root / "closing_line_value.json")
    assert written["positions_scored"] == 2
    assert (cfg.governance_root / "closing_line_value_positions.csv").exists()

    by_cohort = {row["signal_cohort"]: row for row in summary["cohorts"]}
    worldcup = by_cohort["sports_other|worldcup"]
    assert worldcup["positions"] == 2
    assert worldcup["final_positions"] == 1
    # One final sample can never clear the evidence bar: fail closed.
    assert worldcup["clv_evidence"] == EVIDENCE_INSUFFICIENT


def test_position_clv_values_are_exact(tmp_path: Path):
    quotes = build_quote_history(
        [
            _quote("tokA", "2026-07-01T10:30:00Z", 0.52, bid=0.51),
            _quote("tokA", "2026-07-01T11:59:00Z", 0.56, bid=0.55),
            _quote("tokA", "2026-07-01T12:30:00Z", 0.90, bid=0.89),
        ]
    )
    row = position_clv_row(
        _position("tokA", "c", entry=0.50, opened="2026-07-01T10:00:00Z", close="2026-07-01T12:00:00Z"),
        quotes,
        as_of=AS_OF,
    )
    assert row is not None
    assert row["line_kind"] == "closing"
    assert row["line_price"] == 0.56
    assert row["clv"] == 0.06
    assert row["clv_vs_bid"] == 0.05
    assert row["beat_close"] is True


def test_quotes_before_entry_do_not_count(tmp_path: Path):
    quotes = build_quote_history([_quote("tokA", "2026-07-01T09:00:00Z", 0.52)])
    row = position_clv_row(
        _position("tokA", "c", entry=0.50, opened="2026-07-01T10:00:00Z", close="2026-07-01T12:00:00Z"),
        quotes,
        as_of=AS_OF,
    )
    assert row is None


def test_cohort_evidence_classification(tmp_path: Path):
    cfg = _cfg(tmp_path, settings={"minimum_final_samples": 5, "bootstrap_iterations": 200})
    positions = []
    quote_rows = []
    for i in range(6):
        token = f"pos{i}"
        positions.append(_position(token, "cohort_up", entry=0.50, opened="2026-07-01T10:00:00Z", close="2026-07-01T12:00:00Z"))
        quote_rows.append(_quote(token, "2026-07-01T11:00:00Z", 0.55 + i * 0.001))
    for i in range(6):
        token = f"neg{i}"
        positions.append(_position(token, "cohort_down", entry=0.50, opened="2026-07-01T10:00:00Z", close="2026-07-01T12:00:00Z"))
        quote_rows.append(_quote(token, "2026-07-01T11:00:00Z", 0.45 - i * 0.001))
    write_csv(cfg.output_root / "polymarket_shadow" / "shadow_positions.csv", positions)
    write_csv(cfg.output_root / "polymarket_training" / "websocket_market_features.csv", quote_rows)

    summary = build_closing_line_value(cfg, as_of=AS_OF)
    by_cohort = {row["signal_cohort"]: row for row in summary["cohorts"]}
    assert by_cohort["cohort_up"]["clv_evidence"] == EVIDENCE_POSITIVE
    assert by_cohort["cohort_down"]["clv_evidence"] == EVIDENCE_NEGATIVE
    assert summary["positive_clv_cohorts"] == ["cohort_up"]


def test_final_clv_rows_survive_quote_retention_rolloff(tmp_path: Path):
    """Once a closing line is computed it is persisted; retention roll-off cannot erase it.

    Observed live: websocket features keep ~96h of quotes, so a position's pre-close
    quotes age out and its previously-final CLV row silently reverts to provisional or
    vanishes (focus finals dropped 2 -> 0 overnight). The history file must recover it.
    """
    cfg = _cfg(tmp_path)
    features_path = cfg.output_root / "polymarket_training" / "websocket_market_features.csv"
    write_csv(
        cfg.output_root / "polymarket_shadow" / "shadow_positions.csv",
        [_position("tokA", "sharp|worldcup", entry=0.50, opened="2026-07-01T10:00:00Z", close="2026-07-01T12:00:00Z")],
    )
    write_csv(features_path, [_quote("tokA", "2026-07-01T11:59:00Z", 0.56, bid=0.55)])

    first = build_closing_line_value(cfg, as_of=AS_OF)
    assert first["final_line_positions"] == 1
    assert first["final_rows_recovered_from_history"] == 0
    assert first["final_history_rows"] == 1
    assert (cfg.governance_root / "closing_line_final_history.csv").exists()

    # Retention roll-off: only a meaningless post-close quote remains. A fresh rebuild
    # would degrade the row to latest_provisional at 0.90; the persisted final must win.
    write_csv(features_path, [_quote("tokA", "2026-07-01T12:30:00Z", 0.90, bid=0.89)])
    second = build_closing_line_value(cfg, as_of=AS_OF)
    assert second["final_line_positions"] == 1
    assert second["final_rows_recovered_from_history"] == 1
    assert second["mean_final_clv"] == 0.06          # from the recorded 0.56 close, not 0.90
    by_cohort = {row["signal_cohort"]: row for row in second["cohorts"]}
    assert by_cohort["sharp|worldcup"]["final_positions"] == 1
    assert by_cohort["sharp|worldcup"]["final_beat_close_rate"] == 1.0   # beat_close survived CSV round-trip
    assert second["focus_view"]["focus_final_positions"] == 1

    # Quotes fully gone: still recovered, and the history file stays at one row (idempotent).
    write_csv(features_path, [_quote("tokZ", "2026-07-01T11:00:00Z", 0.50)])
    third = build_closing_line_value(cfg, as_of=AS_OF)
    assert third["final_line_positions"] == 1
    assert third["final_rows_recovered_from_history"] == 1
    assert third["final_history_rows"] == 1


def test_missing_close_time_is_backfilled_and_cached(tmp_path: Path, monkeypatch):
    """2026-07-10 Gate A pipe repair: 59 closed positions had NO close_time
    (only crypto slugs were inferred at the writer), so zero finals were ever
    graded. The grader must backfill from Gamma once, cache the answer, and
    then grade a proper closing line."""
    import polymarket_predictive_engine.closing_line as cl

    cfg = _cfg(tmp_path)
    calls = {"n": 0}

    def fake_fetch(position, **kwargs):
        calls["n"] += 1
        return "2026-07-01T12:00:00Z" if position["market_id"] == "mkt_tokA" else None

    monkeypatch.setattr(cl, "_fetch_gamma_close_time", fake_fetch)
    write_csv(
        cfg.output_root / "polymarket_shadow" / "shadow_positions.csv",
        [
            _position("tokA", "sports_other|worldcup", entry=0.50, opened="2026-07-01T10:00:00Z", close=""),
            _position("tokB", "sports_other|tennis", entry=0.40, opened="2026-07-01T10:00:00Z", close=""),
        ],
    )
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            _quote("tokA", "2026-07-01T11:30:00Z", 0.62, bid=0.61),
            _quote("tokB", "2026-07-01T11:30:00Z", 0.45, bid=0.44),
        ],
    )

    summary = build_closing_line_value(cfg, as_of=AS_OF)

    assert summary["close_time_repair"] == {"backfilled": 1, "still_missing": 1, "lookups": 2}
    assert summary["final_line_positions"] == 1
    history = cfg.governance_root / "closing_line_final_history.csv"
    assert "closing" in history.read_text(encoding="utf-8")
    repairs = read_json(cfg.governance_root / "close_time_repairs.json")
    assert repairs["mkt_tokA"] == "2026-07-01T12:00:00Z"
    assert repairs["mkt_tokB"] == ""  # negative-cached: no repeat lookups

    # Second run: cache answers everything, the fetch is never called again.
    summary2 = build_closing_line_value(cfg, as_of=AS_OF)
    assert calls["n"] == 2
    assert summary2["close_time_repair"]["lookups"] == 0
    assert summary2["close_time_repair"]["backfilled"] == 1


def test_settled_positions_without_local_quotes_grade_from_official_history(tmp_path: Path, monkeypatch):
    """2026-07-10 second-stage repair: 64/69 live positions were skipped with
    no usable quotes (websocket table only tracks ~80 assets). A settled
    position must grade from the official CLOB price history instead, and the
    final must persist to the append-only history so later runs recover it
    without re-fetching."""
    import polymarket_predictive_engine.closing_line as cl

    cfg = _cfg(tmp_path)
    calls = {"n": 0}

    def fake_fetch(token_id, close_time, opened_at, **kwargs):
        calls["n"] += 1
        return ("2026-07-01T11:50:00Z", 0.63)

    monkeypatch.setattr(cl, "_fetch_price_history_close_line", fake_fetch)
    write_csv(
        cfg.output_root / "polymarket_shadow" / "shadow_positions.csv",
        [
            # Settled, but its token has NO rows in the features table.
            _position("tokGone", "worldcup", entry=0.50, opened="2026-07-01T10:00:00Z", close="2026-07-01T12:00:00Z"),
            # Not yet closed: must stay provisional, no fallback fetch.
            _position("tokOpen", "worldcup", entry=0.40, opened="2026-07-01T10:00:00Z", close="2026-09-01T12:00:00Z"),
        ],
    )
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [_quote("tokOpen", "2026-07-01T11:30:00Z", 0.45, bid=0.44)],
    )

    summary = build_closing_line_value(cfg, as_of=AS_OF)

    assert summary["finals_recovered_from_price_history"] == 1
    assert summary["final_line_positions"] == 1
    assert summary["positions_skipped_no_usable_quotes"] == 0
    assert calls["n"] == 1
    history = (cfg.governance_root / "closing_line_final_history.csv").read_text(encoding="utf-8")
    assert "tokGone" in history and "closing" in history

    # Second run: the persisted final is recovered; no second fetch.
    summary2 = build_closing_line_value(cfg, as_of=AS_OF)
    assert calls["n"] == 1
    assert summary2["final_rows_recovered_from_history"] == 1
    assert summary2["finals_recovered_from_price_history"] == 0


def test_focus_view_excludes_frozen_updown_cohorts(tmp_path: Path):
    cfg = _cfg(tmp_path, settings={"minimum_final_samples": 5, "bootstrap_iterations": 200})
    positions = []
    quote_rows = []
    # Focus cohort (sharp-anchor World Cup): should drive the focus headline.
    for i in range(6):
        token = f"wc{i}"
        positions.append(_position(token, "worldcup_sharp_anchor", entry=0.50, opened="2026-07-01T10:00:00Z", close="2026-07-01T12:00:00Z"))
        quote_rows.append(_quote(token, "2026-07-01T11:00:00Z", 0.55 + i * 0.001))
    # Frozen diagnostic cohort (crypto up/down): must be excluded from the focus read.
    for i in range(6):
        token = f"ud{i}"
        positions.append(_position(token, "crypto_updown_5m", entry=0.50, opened="2026-07-01T10:00:00Z", close="2026-07-01T12:00:00Z"))
        quote_rows.append(_quote(token, "2026-07-01T11:00:00Z", 0.40 - i * 0.001))
    write_csv(cfg.output_root / "polymarket_shadow" / "shadow_positions.csv", positions)
    write_csv(cfg.output_root / "polymarket_training" / "websocket_market_features.csv", quote_rows)

    summary = build_closing_line_value(cfg, as_of=AS_OF)
    focus = summary["focus_view"]

    assert focus["focus_cohorts"] == ["worldcup_sharp_anchor"]
    assert focus["frozen_cohorts"] == ["crypto_updown_5m"]
    assert focus["focus_positions"] == 6
    assert focus["frozen_positions"] == 6
    # Headline mixes both (net near zero); focus isolates the positive WC signal.
    assert focus["focus_mean_final_clv"] > 0
    assert focus["frozen_mean_final_clv"] < 0
    assert focus["focus_mean_final_clv"] > (summary["mean_final_clv"] or 0)
    assert focus["focus_positive_cohorts"] == ["worldcup_sharp_anchor"]
    # The frozen updown cohort must never appear as a focus positive cohort.
    assert "crypto_updown_5m" not in focus["focus_positive_cohorts"]


def test_focus_view_respects_configured_substrings(tmp_path: Path):
    cfg = _cfg(tmp_path, settings={"minimum_final_samples": 5, "diagnostic_cohort_substrings": ["tennis"]})
    positions = [
        _position("a", "tennis_h2h", entry=0.5, opened="2026-07-01T10:00:00Z", close="2026-07-01T12:00:00Z"),
        _position("b", "crypto_updown_5m", entry=0.5, opened="2026-07-01T10:00:00Z", close="2026-07-01T12:00:00Z"),
    ]
    quote_rows = [
        _quote("a", "2026-07-01T11:00:00Z", 0.55),
        _quote("b", "2026-07-01T11:00:00Z", 0.55),
    ]
    write_csv(cfg.output_root / "polymarket_shadow" / "shadow_positions.csv", positions)
    write_csv(cfg.output_root / "polymarket_training" / "websocket_market_features.csv", quote_rows)

    summary = build_closing_line_value(cfg, as_of=AS_OF)
    focus = summary["focus_view"]
    # Only the configured substring ("tennis") is frozen now; updown counts as focus.
    assert focus["frozen_cohorts"] == ["tennis_h2h"]
    assert focus["focus_cohorts"] == ["crypto_updown_5m"]


def test_shrunk_kelly_never_sizes_larger():
    plain = kelly_fraction(0.60, 0.50, cap=1.0)
    assert shrunk_kelly_fraction(0.60, 0.50, 1.0, shrinkage=0.0) == plain
    shrunk = shrunk_kelly_fraction(0.60, 0.50, 1.0, shrinkage=0.5)
    assert 0.0 < shrunk < plain
    assert shrunk_kelly_fraction(0.60, 0.50, 1.0, shrinkage=1.0) == 0.0
    # No edge stays at zero regardless of shrinkage.
    assert shrunk_kelly_fraction(0.40, 0.50, 1.0, shrinkage=0.5) == 0.0


def test_risk_decision_respects_kelly_shrinkage(tmp_path: Path):
    base = Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8")
    plain_path = tmp_path / "plain.yaml"
    plain_path.write_text(base, encoding="utf-8")
    shrunk_path = tmp_path / "shrunk.yaml"
    shrunk_path.write_text(base.replace("kelly_shrinkage: 0.0", "kelly_shrinkage: 0.9"), encoding="utf-8")
    signal = {
        "edge": 0.10,
        "confidence": 0.9,
        "spread": 0.01,
        "liquidity": 1000,
        "executable_price": 0.4,
        "calibrated_probability": 0.55,
        "time_to_close_hours": 24,
        "best_ask": 0.4,
        "top_ask_size": 1000,
        "ask_depth_1pct": 1000,
        "ask_depth_5pct": 1000,
        "websocket_quote_age_seconds": 30,
    }
    plain = risk_decision(load_config(plain_path), dict(signal))
    shrunk = risk_decision(load_config(shrunk_path), dict(signal))
    assert plain["approved"]
    assert plain["kelly_shrinkage"] == 0.0
    assert shrunk["kelly_shrinkage"] == 0.9
    if shrunk["approved"]:
        assert shrunk["size"] <= plain["size"]
        assert shrunk["kelly_fraction"] <= plain["kelly_fraction"]
