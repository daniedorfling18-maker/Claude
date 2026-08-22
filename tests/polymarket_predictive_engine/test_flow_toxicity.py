"""WO-49 flow-toxicity conditioning tests."""
from __future__ import annotations

import gzip
from pathlib import Path

import pytest
import yaml

from polymarket_predictive_engine import flow_toxicity
from polymarket_predictive_engine import maker_carry_study
from polymarket_predictive_engine.cli import COMMANDS
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.flow_toxicity import WALLET_MARKOUT_FIELDS, build_flow_toxicity
from polymarket_predictive_engine.utils import read_csv_rows, read_json, write_csv, write_json


def _config(tmp_path: Path):
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    raw["flow_toxicity"] = {
        "enabled": True,
        "volume_bucket_usd": 100,
        "buckets": 10,
        "markout_horizon_minutes": 5,
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def _features(cfg, token: str, points: list[tuple[int, float]]) -> None:
    """Feature rows in the shape the normaliser actually writes.

    websocket_market_features.csv carries BOTH source_timestamp and
    collected_at_utc (websocket_normaliser.py:37-38, :70-71). A row with no
    collection time has unproven availability and is barred from the ranking
    window, so a fixture that omits it is not a normal row -- the ordinary case
    is a quote collected when it was sourced.
    """
    existing = read_csv_rows(cfg.output_root / "polymarket_training" / "websocket_market_features.csv")
    rows = [
        *existing,
        *[
            {"source_timestamp": stamp, "collected_at_utc": stamp, "asset_id": token, "midpoint": price}
            for stamp, price in points
        ],
    ]
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        rows,
        fieldnames=["source_timestamp", "collected_at_utc", "asset_id", "midpoint"],
    )


def _trade_summary(cfg, status: str = "ok", *, only: str | None = None) -> None:
    """Producer statuses the wallet axis fails closed against.

    trade_prints.csv has THREE writers and the guard takes the worst status
    across all of them, so a healthy fixture must state all three. `only` writes
    the named summary alone, for testing that one bad producer vetoes.
    """
    root = cfg.output_root / "polymarket_trade_prints"
    names = [
        "trade_prints_summary.json",
        "maker_portfolio_trade_prints_summary.json",
        "trade_print_backfill_summary.json",
    ]
    for name in names:
        row_status = status if (only is None or name == only) else "ok"
        payload = {"status": row_status, "generated_at_utc": "2026-08-22T00:00:00Z"}
        # The two collectors ALWAYS publish markets_polled in production
        # (trade_print_collector.py:373, :487); backfill never does. A fixture
        # that omits it is not a healthy summary.
        if name != "trade_print_backfill_summary.json":
            payload["markets_polled"] = 4
            payload["market_source_status"] = "websocket"
        write_json(root / name, payload)


def _leaderboard(cfg, producer_status: str = "ok") -> None:
    """Leaderboard rows AND the producer summary that vouches for them.

    wallet_intelligence_collector keeps the retained history when a refresh
    fails, so the rows alone do not establish that membership is current -- the
    producer's own status does.
    """
    write_csv(
        cfg.output_root / "wallet_intelligence" / "leaderboard_history.csv",
        [{"snapshot_date": "2026-07-10", "wallet": "smart1", "rank": "1"}],
        fieldnames=["snapshot_date", "wallet", "rank"],
    )
    write_json(
        cfg.output_root / "wallet_intelligence" / "wallet_intelligence_summary.json",
        {"status": producer_status, "generated_at_utc": "2026-08-22T00:00:00Z"},
    )


def test_planted_toxic_flow_scores_above_balanced_flow(tmp_path):
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _features(cfg, "tok-toxic", [(0, 0.5), (10_000, 0.5)])
    _features(cfg, "tok-balanced", [(0, 0.5), (10_000, 0.5)])
    trades = []
    for i in range(20):
        trades.append({"market": "0xtoxic", "asset_id": "tok-toxic", "side": "BUY", "price": 0.5, "size": 100, "timestamp": i})
        side = "BUY" if i % 2 == 0 else "SELL"
        trades.append({"market": "0xbalanced", "asset_id": "tok-balanced", "side": side, "price": 0.5, "size": 100, "timestamp": i})
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        trades,
        fieldnames=["market", "asset_id", "side", "price", "size", "timestamp"],
    )

    summary = build_flow_toxicity(cfg)

    assert summary["status"] == "ok"
    rows = {row["market"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity.csv")}
    assert float(rows["0xtoxic"]["toxicity_score"]) == 1.0
    assert float(rows["0xbalanced"]["toxicity_score"]) == 0.0
    assert float(rows["0xtoxic"]["vpin_raw"]) > float(rows["0xbalanced"]["vpin_raw"])
    assert summary["paper_trading_invoked"] is False
    assert summary["live_trading_invoked"] is False


def test_wallet_tier_markout_split_arithmetic(tmp_path):
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _features(cfg, "tok1", [(0, 0.5), (400, 0.6), (1_000, 0.5), (1_400, 0.4)])
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [
            {"market": "0xcond", "asset_id": "tok1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 100, "counterparty_wallet": "smart1"},
            {"market": "0xcond", "asset_id": "tok1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 1_000, "counterparty_wallet": "crowd1"},
        ],
        fieldnames=["market", "asset_id", "side", "price", "size", "timestamp", "counterparty_wallet"],
    )

    build_flow_toxicity(cfg)

    row = read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity.csv")[0]
    assert float(row["smart_fill_markout"]) == 0.1
    assert float(row["crowd_fill_markout"]) == -0.1
    assert row["smart_fill_count"] == "1"
    assert row["crowd_fill_count"] == "1"


def test_missing_wallet_intelligence_is_tolerated(tmp_path):
    cfg = _config(tmp_path)
    _features(cfg, "tok1", [(0, 0.5), (400, 0.51)])
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xcond", "asset_id": "tok1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 100}],
        fieldnames=["market", "asset_id", "side", "price", "size", "timestamp"],
    )

    summary = build_flow_toxicity(cfg)

    assert summary["status"] == "ok"
    assert summary["missing_wallet_data"] is True
    row = read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity.csv")[0]
    assert row["crowd_fill_count"] == "1"


def test_feature_archives_and_trade_prints_are_streamed_not_bulk_loaded(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    archive = cfg.output_root / "polymarket_training_archive"
    archive.mkdir(parents=True, exist_ok=True)
    with gzip.open(archive / "features_20260710T000000Z.csv.gz", "wt", encoding="utf-8", newline="") as handle:
        handle.write("source_timestamp,asset_id,midpoint\n400,tok1,0.6\n")
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [
            {
                "market": "0xcond",
                "asset_id": "tok1",
                "side": "BUY",
                "price": 0.5,
                "size": 100,
                "timestamp": 100,
                "counterparty_wallet": "smart1",
            }
        ],
        fieldnames=["market", "asset_id", "side", "price", "size", "timestamp", "counterparty_wallet"],
    )
    original_read_csv_rows = flow_toxicity.read_csv_rows

    def guarded_bulk_reader(path, *args, **kwargs):
        candidate = Path(path)
        if candidate.suffix == ".gz" or candidate.name in {"trade_prints.csv", "websocket_market_features.csv"}:
            raise AssertionError(f"bulk reader used for growing corpus: {candidate}")
        return original_read_csv_rows(path, *args, **kwargs)

    monkeypatch.setattr(flow_toxicity, "read_csv_rows", guarded_bulk_reader)

    summary = flow_toxicity.build_flow_toxicity(cfg)

    assert summary["status"] == "ok"
    assert summary["price_index_strategy"] == "disk_backed_streaming_sqlite"
    assert summary["feature_rows_scanned"] == 1
    assert summary["feature_rows_indexed"] == 1
    assert summary["price_index_disk_bytes"] > 0
    row = read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity.csv")[0]
    assert float(row["smart_fill_markout"]) == 0.1


def test_quote_sheet_surfaces_toxicity_column_and_rule(tmp_path):
    cfg = _config(tmp_path)
    out = cfg.output_root / "maker_carry"
    write_csv(
        out / "flow_toxicity.csv",
        [{"market": "0xcond", "toxicity_score": 0.95}],
        fieldnames=["market", "toxicity_score"],
    )
    summary = {
        "generated_at_utc": "2026-07-10T00:00:00Z",
        "portfolio_net_carry_usd_per_day": 1.0,
        "portfolio_net_carry_usd_per_month": 30.0,
        "portfolio_capital_usd": 100.0,
        "maker_gates": {"maker_verdict": "insufficient_evidence", "M_A_carry_evidence": {"state": "pending"}, "M_B_adverse_realism": {"state": "pending"}},
        "portfolio": [
            {
                "question": "Synthetic market",
                "condition_id": "0xcond",
                "quote_size_shares": 100,
                "quote_distance": 0.01,
                "capital_usd": 100,
                "net_carry_usd_per_day": 1.0,
                "event_risk_flags": [],
            }
        ],
    }

    maker_carry_study._write_quote_sheet(out, summary, {"min_daily_payout_usd": 1.0})

    sheet = (out / "maker_quote_sheet.md").read_text(encoding="utf-8")
    assert "toxicity" in sheet
    assert "toxicity>0.9" in sheet
    assert "8. Flow toxicity" in sheet


def test_cli_exposes_flow_toxicity():
    assert "flow-toxicity" in COMMANDS


def test_absolute_floor_blocks_one_sided_market_the_percentile_would_de_veto(tmp_path):
    # WO-102 de-veto hole: a fully one-sided market measured alongside equally
    # toxic peers gets a LOW percentile rank (index/(n-1)), which the old
    # percentile-only rule would clear. The absolute raw-imbalance floor must
    # still block it, universe-independent.
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    trades = []
    for market in ("0xtarget", "0xpeer1", "0xpeer2"):
        for i in range(20):
            trades.append({"market": market, "asset_id": f"tok-{market}", "side": "BUY",
                           "price": 0.5, "size": 100, "timestamp": i})
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        trades,
        fieldnames=["market", "asset_id", "side", "price", "size", "timestamp"],
    )

    build_flow_toxicity(cfg)

    rows = {row["market"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity.csv")}
    target = rows["0xtarget"]
    # Fully one-sided flow, but ranked lowest among equally-toxic peers.
    assert float(target["vpin_raw"]) >= 0.9
    assert float(target["toxicity_score"]) < 0.9  # percentile alone would clear it
    assert str(target["toxic_blocked"]).lower() == "true"  # absolute floor blocks
    assert "raw_imbalance" in target["toxicity_block_reasons"]


def test_wallet_markouts_are_published_for_wallets_off_the_leaderboard(tmp_path):
    """The market-axis table can only ever see 100 wallets; this one sees all of them.

    Measured 2026-08-15: of 475 markets scored from 200,000 fills, 176 had
    markout coverage but only 16 produced a smart-fill markout - because
    _top_wallets resolves "smart" to the latest leaderboard snapshot capped at
    100 wallets, and the mirror holds 2 snapshots naming the same 100. A wallet
    with real forward markout that is not on that list was invisible.
    """
    cfg = _config(tmp_path)
    _leaderboard(cfg)  # only "smart1" is on the leaderboard
    # Price rises after every trade, so a BUY marks out positive. The forward
    # point sits INSIDE [target, target+horizon] (targets 300/301, horizon 300).
    _features(cfg, "tok-a", [(0, 0.50), (310, 0.70)])
    trades = [
        {"market": "0xa", "asset_id": "tok-a", "wallet": "smart1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0},
        {"market": "0xa", "asset_id": "tok-a", "wallet": "unranked9", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 1},
    ]
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        trades,
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )

    summary = build_flow_toxicity(cfg)

    rows = {row["wallet"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv")}
    assert set(rows) == {"smart1", "unranked9"}, "a wallet off the leaderboard must still be measured"
    assert rows["smart1"]["on_current_leaderboard"] == "True"
    assert rows["unranked9"]["on_current_leaderboard"] == "False"
    # Both bought before the same rise, so both mark out positive and equally.
    assert float(rows["unranked9"]["markout_mean_total"]) > 0
    assert float(rows["unranked9"]["markout_mean_total"]) == float(rows["smart1"]["markout_mean_total"])
    assert summary["wallets_scored"] == 2
    assert summary["paper_trading_invoked"] is False


def test_wallet_markout_windows_split_by_whole_market_with_label_embargo(tmp_path):
    """The split is chronological, out-of-sample BY MARKET, and EMBARGOED.

    Two leaks, both real, both pinned here:

    1. Assigning fills to windows by timestamp alone lets a market traded on
       both sides of the median leak ranking-window effects into evaluation
       (Codex P1 on #451). A market belongs wholly to one window; one spanning
       the split belongs to NEITHER.
    2. A fill's markout LABEL is a price read one horizon LATER, so a market
       whose last fill lands just before the split has its label observed after
       evaluation fills have begun (Codex P2 on #451). The earlier version of
       this very test demonstrated the leak: its "ranking" fills at t=0/1 were
       labelled at t=310, while evaluation fills started at t=100. A market
       ranks only when its label window also closes before the split.

    Both exclusions are disclosed per wallet, under SEPARATE counters, never
    silently scored. Horizon is 300s (5 min) throughout.
    """
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    # Every forward point sits inside [target, target + 300] for its fills.
    _features(cfg, "tok-a", [(310, 0.70)])    # targets 300, 301
    _features(cfg, "tok-d", [(1210, 0.70)])   # target 1200
    _features(cfg, "tok-b", [(1310, 0.70)])   # targets 1300, 1301, 1305
    _features(cfg, "tok-c", [(310, 0.70), (1310, 0.70)])  # targets 302, 1302
    trades = [
        # market A: fills at 0/1, labels at 300/301 -> both close before the
        # split at 1000, so this is a genuine ranking market.
        {"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0},
        {"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 1},
        # market D: fill at 900 is before the split, but its label is read at
        # 1200 -- AFTER evaluation begins. Ranking-eligible by fill time,
        # embargoed by label time.
        {"market": "0xd", "asset_id": "tok-d", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 900},
        # market B: every fill at/after the split -> evaluation.
        {"market": "0xb", "asset_id": "tok-b", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 1000},
        {"market": "0xb", "asset_id": "tok-b", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 1001},
        {"market": "0xb", "asset_id": "tok-b", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 1005},
        # market C: fills straddle the split -> spanning, in NEITHER window.
        {"market": "0xc", "asset_id": "tok-c", "wallet": "w2", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 2},
        {"market": "0xc", "asset_id": "tok-c", "wallet": "w2", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 1002},
    ]
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        trades,
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )

    summary = build_flow_toxicity(cfg)
    rows = {row["wallet"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv")}

    # stamps sorted: [0, 1, 2, 900, 1000, 1001, 1002, 1005]; n=8, n//2=4 -> split 1000.
    assert summary["wallet_ranking_embargo_seconds"] == 300.0
    assert int(rows["w1"]["fills_total"]) == 6
    assert int(rows["w1"]["fills_ranking_window"]) == 2      # market A only
    assert int(rows["w1"]["fills_evaluation_window"]) == 3   # market B
    assert int(rows["w1"]["fills_label_embargoed"]) == 1     # market D
    assert int(rows["w1"]["fills_split_spanning"]) == 0
    # The embargoed fill is counted in the total and disclosed, but scored in
    # neither window -- the two window counts must not sum to the total.
    assert int(rows["w1"]["fills_ranking_window"]) + int(rows["w1"]["fills_evaluation_window"]) < int(
        rows["w1"]["fills_total"]
    )
    # w2 traded only the spanning market: counted, disclosed, never windowed,
    # and NOT confused with the embargo.
    assert int(rows["w2"]["fills_total"]) == 2
    assert int(rows["w2"]["fills_ranking_window"]) == 0
    assert int(rows["w2"]["fills_evaluation_window"]) == 0
    assert int(rows["w2"]["fills_split_spanning"]) == 2
    assert int(rows["w2"]["fills_label_embargoed"]) == 0
    assert summary["wallets_in_both_windows"] == 1
    assert summary["wallets_scored"] == 2


def test_ranking_fill_priced_after_the_split_is_embargoed(tmp_path):
    """The nominal label time is not enough; the ACTUAL observation decides.

    The market-level embargo tests `high + horizon < split`. That is necessary
    but not sufficient, because the staleness rule accepts an observation up to
    one further horizon late (Codex P1 wave-4 on #451). Codex's worked example,
    reproduced exactly: horizon 300, split 1000, a market whose last fill is at
    600 passes the nominal test (900 < 1000) and is classed "ranking" -- but its
    price is actually first observed at 1100, only 200s late so the staleness
    rule accepts it, and 1100 is inside the evaluation period. Scoring it would
    rank the wallet on an evaluation-period observation.

    The per-fill check catches exactly this and nothing else: the fill is
    counted and disclosed as embargoed, never scored into the ranking mean.
    """
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    # tok-r: fill at 600, target 900, first observation at 1100 -> 200s late
    # (inside the 300s tolerance) but PAST the split at 1000.
    _features(cfg, "tok-r", [(1100, 0.90)])
    # tok-e: the evaluation market that puts the split at 1000.
    _features(cfg, "tok-e", [(1310, 0.70), (1315, 0.70), (1320, 0.70)])
    trades = [
        {"market": "0xr", "asset_id": "tok-r", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 600},
        {"market": "0xe", "asset_id": "tok-e", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 1000},
        {"market": "0xe", "asset_id": "tok-e", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 1010},
    ]
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        trades,
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )

    build_flow_toxicity(cfg)
    rows = {row["wallet"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv")}

    # stamps [600, 1000, 1010]; n=3, n//2=1 -> split 1000. Market 0xr:
    # high=600, 600+300=900 < 1000, so the NOMINAL test ranks it.
    assert int(rows["w1"]["fills_ranking_window"]) == 0, (
        "the fill is nominally ranking but its price was observed at 1100, past the split"
    )
    assert int(rows["w1"]["fills_label_embargoed"]) == 1
    assert int(rows["w1"]["fills_evaluation_window"]) == 2
    assert int(rows["w1"]["fills_total"]) == 3
    # The +0.40 move on tok-r must not reach the ranking mean at all.
    assert rows["w1"]["markout_mean_ranking_window"] == ""


def test_enabled_run_with_no_wallets_still_states_its_evidence_class(tmp_path):
    """An empty enabled run must not emit a bare header (Codex P1 wave-5)."""
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _trade_summary(cfg)
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [],
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )

    summary = build_flow_toxicity(cfg)
    rows = read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv")

    assert len(rows) == 1
    assert rows[0]["artifact_status"] == "no_wallets_scored"
    assert rows[0]["paper_trading_invoked"] == "False"
    assert rows[0]["live_trading_invoked"] == "False"
    # The sentinel is not a scored wallet.
    assert summary["wallets_scored"] == 0
    # An empty corpus is not an error: status is "ok", not "no_trades". The
    # parent's "no_trades" label is a misnomer -- it fires when trades ARE
    # present but no market scored -- and the registered vacuous-state clause
    # says so, because the first draft of that clause asserted the opposite.
    assert summary["status"] == "ok"
    assert summary["markets_scored"] == 0


def test_stale_trade_corpus_is_stamped_on_every_wallet_row(tmp_path):
    """A partial upstream collection must be disclosed, not ranked as current.

    The previous run's capped ledger stays readable when a collection fails, so
    scoring it without consulting the producer would rank wallets on an
    undisclosed stale sample (Codex P1 wave-5 on #451). Every row is stamped
    rather than the artifact being blanked: a missing producer summary should
    let a reader reject the evidence, not silently destroy a working artifact.
    """
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    # Only the BACKFILL producer is partial; the other two say ok. One bad
    # writer of the shared ledger is enough, because any of the three can have
    # written the partial rows (Codex P1 wave-7 on #451).
    _trade_summary(cfg, status="partial", only="trade_print_backfill_summary.json")
    _features(cfg, "tok-a", [(310, 0.70)])
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0}],
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )

    summary = build_flow_toxicity(cfg)
    rows = {row["wallet"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv")}

    assert rows["w1"]["artifact_status"] == "upstream_partial"
    assert int(rows["w1"]["fills_total"]) == 1, "the row is still measured, just disclosed"
    assert summary["trade_corpus_status"] == "partial"


def test_non_finite_trade_prices_are_rejected_at_ingestion(tmp_path):
    """safe_float parses "nan"; a nan markout would score a wallet as measured.

    A nan price yields a nan markout that increments fills_total and poisons
    markout_total, so the wallet is emitted as SCORED with a nan mean instead of
    excluded as malformed -- and nan*size poisons usd_volume and therefore VPIN
    (Codex P1 wave-5 on #451). Rejected at the ingestion boundary, which is the
    only place covering both the wallet axis and the market axis.
    """
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _trade_summary(cfg)
    _features(cfg, "tok-a", [(310, 0.70)])
    trades = [
        {"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0},
        {"market": "0xa", "asset_id": "tok-a", "wallet": "wbad", "side": "BUY", "price": "nan", "size": 100, "timestamp": 1},
        {"market": "0xa", "asset_id": "tok-a", "wallet": "wbad2", "side": "BUY", "price": 0.5, "size": "inf", "timestamp": 2},
    ]
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        trades,
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )

    summary = build_flow_toxicity(cfg)
    rows = {row["wallet"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv")}

    assert "wbad" not in rows and "wbad2" not in rows
    assert "w1" in rows
    assert summary["malformed_trade_rows_excluded"] == 2
    # The market axis is protected by the same boundary guard.
    market = {row["market"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity.csv")}
    assert int(market["0xa"]["trades_seen"]) == 1


def test_the_ranking_split_is_frozen_across_runs(tmp_path):
    """A moving median recycles published evaluation evidence into ranking.

    The split was recomputed from the current corpus on every rebuild, so later
    fills advanced the median and a market that was EVALUATION yesterday --
    whose markout has already been published and inspected -- could become
    RANKING today (Codex P1 wave-5 on #451). That is the same circularity the
    split exists to prevent, spread across runs. The split is frozen at first
    computation and persisted.
    """
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _trade_summary(cfg)
    _features(cfg, "tok-a", [(310, 0.70)])
    _features(cfg, "tok-b", [(610, 0.70)])
    base = [
        {"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0},
        {"market": "0xb", "asset_id": "tok-b", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 300},
    ]
    trade_path = cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv"
    fields = ["market", "asset_id", "wallet", "side", "price", "size", "timestamp"]
    write_csv(trade_path, base, fieldnames=fields)

    first = build_flow_toxicity(cfg)
    split_path = cfg.output_root / "maker_carry" / "flow_toxicity_wallet_split.json"
    assert split_path.exists()
    frozen = read_json(split_path)["split_stamp"]
    assert first["wallet_split_was_frozen"] is False, "first run computes it"

    # A later collection arrives. The median of the NEW corpus is later than the
    # frozen split; without freezing, market 0xb would migrate windows.
    write_csv(
        trade_path,
        base + [
            {"market": "0xc", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 900},
            {"market": "0xd", "asset_id": "tok-b", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 1200},
        ],
        fieldnames=fields,
    )
    second = build_flow_toxicity(cfg)

    assert second["wallet_split_was_frozen"] is True
    assert read_json(split_path)["split_stamp"] == frozen, "the split must not move"


def test_a_stale_corpus_never_freezes_the_split(tmp_path):
    """Contamination must not outlive the run it happened in (Codex P1 wave-6).

    A first run against a partial ledger still called the freeze path and fixed
    the median of that contaminated corpus permanently; every later healthy run
    then reported wallet_split_was_frozen=true while reusing the bad boundary.
    Stamping the rows non-OK did not contain it, because the damage was in the
    persisted state, not the rows.
    """
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _trade_summary(cfg, status="partial")
    _features(cfg, "tok-a", [(310, 0.70)])
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0}],
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )

    summary = build_flow_toxicity(cfg)

    assert summary["trade_corpus_status"] == "partial"
    assert not (cfg.output_root / "maker_carry" / "flow_toxicity_wallet_split.json").exists(), (
        "a partial corpus must persist no split state at all"
    )
    assert summary["wallet_split_was_frozen"] is False


def test_a_corrupt_frozen_split_fails_closed(tmp_path):
    """An invalid frozen state must raise, not manufacture a new cutoff.

    Treating a corrupt file as a first run would compute a fresh median from
    data already observed as evaluation -- recreating the exact leakage the
    persistence exists to prevent, after any corruption or partial restore
    (Codex P1 wave-6 on #451).
    """
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _trade_summary(cfg)
    _features(cfg, "tok-a", [(310, 0.70)])
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0}],
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )
    split_path = cfg.output_root / "maker_carry" / "flow_toxicity_wallet_split.json"
    split_path.parent.mkdir(parents=True, exist_ok=True)
    split_path.write_text('{"split_stamp": "not-a-number"}', encoding="utf-8")

    # A previous successful run left rows on disk.
    _features(cfg, "tok-a", [(310, 0.70)])
    wallet_path = cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv"
    write_csv(
        wallet_path,
        [{"generated_at_utc": "old", "artifact_status": "ok", "wallet": "w1", "fills_total": 3}],
        fieldnames=WALLET_MARKOUT_FIELDS,
    )

    with pytest.raises(ValueError, match="refusing to manufacture"):
        build_flow_toxicity(cfg)

    # The raise must not leave the previous ranking readable: the harvest
    # records the failure and continues, so a stale "ok" row would be read as
    # current evidence (Codex P2 wave-8 on #451).
    rows = read_csv_rows(wallet_path)
    assert len(rows) == 1
    assert rows[0]["artifact_status"] == "invalid_frozen_split_state"
    assert rows[0]["wallet"] == ""
    assert read_json(cfg.output_root / "maker_carry" / "flow_toxicity_summary.json")["status"] == (
        "invalid_frozen_split_state"
    )


def test_split_artifact_states_its_own_invocation_flags(tmp_path):
    """The split file is independently persisted, so it needs its own flags."""
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _trade_summary(cfg)
    _features(cfg, "tok-a", [(310, 0.70)])
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0}],
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )

    build_flow_toxicity(cfg)
    state = read_json(cfg.output_root / "maker_carry" / "flow_toxicity_wallet_split.json")

    assert state["paper_trading_invoked"] is False
    assert state["live_trading_invoked"] is False
    # Published so erosion of the ranking sample is measurable against it.
    assert state["corpus_rows_at_freeze"] == 1


def test_non_finite_feature_midpoints_are_rejected_before_indexing(tmp_path):
    """The wave-5 guard covered trades only; features parse "inf"/"nan" too.

    An inf midpoint produced an infinite markout emitted as a successfully
    scored wallet mean, and a nan midpoint reached an INSERT into a
    `midpoint REAL NOT NULL` column where SQLite stores NaN as NULL and the
    constraint aborts the whole build (Codex P1 wave-6 on #451).
    """
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _trade_summary(cfg)
    # The inf point would be selected first; the finite one at 320 is the truth.
    _features(cfg, "tok-a", [(310, float("inf")), (320, 0.70)])
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0}],
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )

    summary = build_flow_toxicity(cfg)
    rows = {row["wallet"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv")}

    assert summary["status"] == "ok", "a nan/inf feature must not abort the build"
    assert float(rows["w1"]["markout_mean_total"]) == 0.2, "scored from the finite 0.70 point"


def test_all_producers_disabled_is_not_a_current_corpus(tmp_path):
    """Nothing refreshed the ledger, so retained rows are not current.

    With trade_prints disabled, all three producers write status="disabled"
    without touching the retained ledger. Accepting that as "ok" stamped stale
    rows as current AND let them permanently seed the frozen split (Codex P1
    wave-8 on #451). At least one producer must have actually succeeded.
    """
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _trade_summary(cfg, status="disabled")
    _features(cfg, "tok-a", [(310, 0.70)])
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0}],
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )

    summary = build_flow_toxicity(cfg)
    rows = {row["wallet"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv")}

    assert summary["trade_corpus_status"] == "no_producer_refreshed"
    assert rows["w1"]["artifact_status"] == "upstream_no_producer_refreshed"
    assert not (cfg.output_root / "maker_carry" / "flow_toxicity_wallet_split.json").exists(), (
        "an all-disabled corpus must not seed the frozen split"
    )


def test_ranking_label_embargoed_by_collection_time_not_venue_stamp(tmp_path):
    """A price sourced before the split but COLLECTED after it is look-ahead.

    _build_price_index kept only source_timestamp, so a delayed or replayed
    feature sourced at 900 and collected at 1100 counted as available at 900 --
    passing a split at 1000 and entering the ranking mean despite only existing
    during evaluation (Codex P1 wave-8 on #451). The embargo now takes the later
    of the two timestamps.
    """
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _trade_summary(cfg)
    # tok-r: fill at 600 -> target 900. The feature's VENUE stamp is 900 (before
    # the split at 1000) but it was COLLECTED at 1100 (after it).
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            {"source_timestamp": 900, "collected_at_utc": 1100, "asset_id": "tok-r", "midpoint": 0.90},
            {"source_timestamp": 1310, "collected_at_utc": 1310, "asset_id": "tok-e", "midpoint": 0.70},
            {"source_timestamp": 1315, "collected_at_utc": 1315, "asset_id": "tok-e", "midpoint": 0.70},
        ],
        fieldnames=["source_timestamp", "collected_at_utc", "asset_id", "midpoint"],
    )
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [
            {"market": "0xr", "asset_id": "tok-r", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 600},
            {"market": "0xe", "asset_id": "tok-e", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 1000},
            {"market": "0xe", "asset_id": "tok-e", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 1010},
        ],
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )

    build_flow_toxicity(cfg)
    rows = {row["wallet"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv")}

    assert int(rows["w1"]["fills_ranking_window"]) == 0, (
        "the venue stamp precedes the split but the price was not observable until after it"
    )
    assert int(rows["w1"]["fills_label_embargoed"]) == 1
    assert rows["w1"]["markout_mean_ranking_window"] == ""


def test_backfill_no_op_statuses_do_not_veto_a_healthy_harvest(tmp_path):
    """A successful no-op producer must not fail the whole corpus.

    backfill_trade_prints returns "skipped_all_completed" once its one-shot work
    is done and "no_candidate_markets" when there is nothing to backfill -- both
    set in an elif chain AFTER the error check, so both mean it ran and had
    nothing to do (trade_print_collector.py:666-673). The wave-7/8 tightening
    treated them as vetoes, which would stamp every wallet row non-OK on a
    NORMAL steady-state daily harvest and refuse to persist the split even
    though another producer had just refreshed the ledger (Codex P1 wave-9).
    """
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _features(cfg, "tok-a", [(310, 0.70)])
    root = cfg.output_root / "polymarket_trade_prints"
    # The realistic steady state: collector ok, replay disabled, backfill done.
    write_json(
        root / "trade_prints_summary.json",
        {"status": "ok", "markets_polled": 4, "market_source_status": "websocket"},
    )
    write_json(root / "maker_portfolio_trade_prints_summary.json", {"status": "disabled"})
    write_json(root / "trade_print_backfill_summary.json", {"status": "skipped_all_completed"})
    write_csv(
        root / "trade_prints.csv",
        [{"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0}],
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )

    summary = build_flow_toxicity(cfg)
    rows = {row["wallet"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv")}

    assert summary["trade_corpus_status"] == "ok"
    assert rows["w1"]["artifact_status"] == "ok"
    assert (cfg.output_root / "maker_carry" / "flow_toxicity_wallet_split.json").exists(), (
        "a healthy harvest must persist the split"
    )
    # The other no-op form behaves identically.
    write_json(root / "trade_print_backfill_summary.json", {"status": "no_candidate_markets"})
    assert build_flow_toxicity(cfg)["trade_corpus_status"] == "ok"


def test_same_stamp_features_break_ties_by_observation_not_price(tmp_path):
    """Which row wins must not depend on the numerical price ordering.

    websocket_normaliser includes collection time in its dedup key, so two rows
    can share a source_timestamp. Ordering the tie by midpoint made BOTH the
    selection and the embargo decision depend on which price happened to be
    numerically smaller (Codex P1 wave-9 on #451). Here the earlier-collected
    quote is the LARGER price, so a midpoint tiebreak would have picked the
    later one; observation order picks the one that was actually available.
    """
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _trade_summary(cfg)
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            {"source_timestamp": 310, "collected_at_utc": 400, "asset_id": "tok-a", "midpoint": 0.60},
            {"source_timestamp": 310, "collected_at_utc": 320, "asset_id": "tok-a", "midpoint": 0.70},
        ],
        fieldnames=["source_timestamp", "collected_at_utc", "asset_id", "midpoint"],
    )
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0}],
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )

    build_flow_toxicity(cfg)
    rows = {row["wallet"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv")}

    # Both rows represent the venue at 310, so the tie breaks on which was
    # AVAILABLE first: collected 320 beats collected 400. Markout is
    # 0.70 - 0.50 = 0.20. A midpoint tiebreak would have picked 0.60 (+0.10),
    # and preferring the later correction would also give +0.10 -- so this
    # assertion distinguishes all three orderings.
    assert float(rows["w1"]["markout_mean_total"]) == 0.2


def test_an_ok_status_without_a_poll_is_not_a_refresh(tmp_path):
    """status="ok" with markets_polled=0 is not evidence the ledger refreshed.

    collect_maker_replay_data reaches the explicit-market collector, which
    reports market_source_status="empty" and still writes status="ok" with
    markets_polled=0 (trade_print_collector.py:406-410, 484-487). Treating that
    as a refresh stamped retained rows current and could permanently freeze
    their stale split despite no venue request occurring (Codex P1 wave-11).
    """
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _features(cfg, "tok-a", [(310, 0.70)])
    root = cfg.output_root / "polymarket_trade_prints"
    write_json(root / "trade_prints_summary.json", {"status": "disabled"})
    write_json(
        root / "maker_portfolio_trade_prints_summary.json",
        {"status": "ok", "markets_polled": 0, "market_source_status": "empty"},
    )
    write_json(root / "trade_print_backfill_summary.json", {"status": "no_candidate_markets"})
    write_csv(
        root / "trade_prints.csv",
        [{"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0}],
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )

    summary = build_flow_toxicity(cfg)

    assert summary["trade_corpus_status"] == "no_producer_refreshed"
    assert not (cfg.output_root / "maker_carry" / "flow_toxicity_wallet_split.json").exists()
    # A producer that DID poll flips it back.
    write_json(
        root / "maker_portfolio_trade_prints_summary.json",
        {"status": "ok", "markets_polled": 3, "market_source_status": "websocket"},
    )
    assert build_flow_toxicity(cfg)["trade_corpus_status"] == "ok"
    # And a primary summary carrying only status="ok" -- malformed or legacy,
    # since that collector always publishes markets_polled -- is NOT evidence
    # (Codex P1 wave-13).
    write_json(root / "maker_portfolio_trade_prints_summary.json", {"status": "disabled"})
    write_json(root / "trade_prints_summary.json", {"status": "ok"})
    assert build_flow_toxicity(cfg)["trade_corpus_status"] == "no_producer_refreshed"


def test_invalid_split_state_does_not_freeze_the_toxicity_veto(tmp_path):
    """A wallet-side fault must not stop the market axis being rebuilt.

    flow_toxicity.csv feeds the toxicity VETO read by requote_alerts.py (:135,
    :508) and stage_ticket_eligibility.py. Raising before that table was
    rewritten -- which is what my wave-8 fix did -- meant a corrupt WALLET file
    left a market that had since turned toxic still reading clean, on every
    harvest, until an operator repaired an unrelated artifact (Codex P1
    wave-11). The wallet axis fails closed; the market axis stays current.
    """
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _trade_summary(cfg)
    _features(cfg, "tok-a", [(0, 0.5), (10_000, 0.5)])
    trades = [
        {"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": i}
        for i in range(20)
    ]
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        trades,
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )
    market_path = cfg.output_root / "maker_carry" / "flow_toxicity.csv"
    split_path = cfg.output_root / "maker_carry" / "flow_toxicity_wallet_split.json"

    assert build_flow_toxicity(cfg)["status"] == "ok"
    assert len(read_csv_rows(market_path)) == 1
    # Corrupt the WALLET split state only.
    split_path.write_text('{"split_stamp": true}', encoding="utf-8")
    market_path.unlink()

    with pytest.raises(ValueError, match="market-axis table WAS rebuilt"):
        build_flow_toxicity(cfg)

    # The veto table was rebuilt despite the wallet-side failure.
    assert market_path.exists()
    rows = read_csv_rows(market_path)
    assert len(rows) == 1 and rows[0]["market"] == "0xa"
    # The wallet artifact is invalidated, not left stale.
    wallet_rows = read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv")
    assert len(wallet_rows) == 1
    assert wallet_rows[0]["artifact_status"] == "invalid_frozen_split_state"


def test_a_late_collected_quote_is_still_the_right_market_state(tmp_path):
    """Late COLLECTION does not disqualify a quote from being the markout.

    Renamed and rewritten (Codex P1 wave-20): this test was born under the
    collapsed single-clock model, where a quote sourced at 310 but collected at
    1000 was stale-excluded and a later-sourced quote won. Wave-15 restored the
    two-clock contract and corrected the assertion to +0.40, but left the name
    and docstring describing the reverted behaviour -- so the test asserted the
    opposite of what it was called.

    The restored contract: the VENUE stamp decides which market state a markout
    measures, so a quote sourced at 310 IS the right price for a target of 300
    regardless of when it was collected. Late collection bars a quote from the
    RANKING window only, which this fixture does not exercise.
    """
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _trade_summary(cfg)
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            {"source_timestamp": 310, "collected_at_utc": 1000, "asset_id": "tok-a", "midpoint": 0.90},
            {"source_timestamp": 320, "collected_at_utc": 320, "asset_id": "tok-a", "midpoint": 0.70},
        ],
        fieldnames=["source_timestamp", "collected_at_utc", "asset_id", "midpoint"],
    )
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0}],
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )

    build_flow_toxicity(cfg)
    rows = {row["wallet"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv")}

    # Under the corrected model the 310-sourced row IS the right market state
    # for a target of 300, and late COLLECTION does not make it stale -- being
    # collected at 1000 only bars it from ranking, which this fixture does not
    # test. So it is used: 0.90 - 0.50 = 0.40, and nothing is stale-excluded.
    # The earlier expectation of 0.20 encoded the collapsed model, in which
    # staleness was measured on availability; that was the defect, not this.
    assert float(rows["w1"]["markout_mean_total"]) == 0.4
    assert int(rows["w1"]["fills_stale_price_excluded"]) == 0


def test_features_with_invalid_collection_times_are_rejected(tmp_path):
    """A present-but-unparseable collection time must not default to the venue.

    Defaulting manufactured an availability time we do not know, so an
    unverifiable label could appear available before the split and enter the
    ranking mean (Codex P1 wave-12). Absent is different from invalid: an absent
    column means the corpus predates it, and the venue stamp is the only
    evidence available.
    """
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _trade_summary(cfg)
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            {"source_timestamp": 310, "collected_at_utc": "junk", "asset_id": "tok-a", "midpoint": 0.90},
            {"source_timestamp": 330, "collected_at_utc": 330, "asset_id": "tok-a", "midpoint": 0.70},
        ],
        fieldnames=["source_timestamp", "collected_at_utc", "asset_id", "midpoint"],
    )
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0}],
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )

    build_flow_toxicity(cfg)
    rows = {row["wallet"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv")}

    # The junk-collection row is dropped; the valid 0.70 row scores.
    assert float(rows["w1"]["markout_mean_total"]) == 0.2


def test_a_malformed_markout_horizon_fails_closed(tmp_path):
    """Config corruption must not preserve or manufacture healthy evidence."""
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _trade_summary(cfg)
    _features(cfg, "tok-a", [(310, 0.70)])
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0}],
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )
    wallet_path = cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv"
    assert build_flow_toxicity(cfg)["status"] == "ok"

    raw = yaml.safe_load(Path(cfg.path).read_text(encoding="utf-8"))
    raw["flow_toxicity"]["markout_horizon_minutes"] = "not-a-number"
    Path(cfg.path).write_text(yaml.safe_dump(raw), encoding="utf-8")

    market_path = cfg.output_root / "maker_carry" / "flow_toxicity.csv"
    market_path.unlink()

    with pytest.raises(ValueError, match="finite positive number"):
        build_flow_toxicity(load_config(Path(cfg.path)))

    rows = read_csv_rows(wallet_path)
    assert len(rows) == 1
    assert rows[0]["artifact_status"] == "invalid_markout_horizon"
    # The toxicity VETO must stay current: its fields come from trade volume
    # buckets and do not depend on the horizon (Codex P1 wave-14). Leaving the
    # previous table meant a market that had turned toxic kept reading clean;
    # DELETING it would be worse still, since requote_alerts treats an absent
    # record as no block at all.
    assert market_path.exists()
    market_rows = read_csv_rows(market_path)
    assert len(market_rows) == 1 and market_rows[0]["market"] == "0xa"
    assert market_rows[0]["vpin_raw"] != ""
    # Markout columns are empty, because every markout is horizon-relative.
    assert market_rows[0]["smart_fill_markout"] == ""


def test_unproven_availability_cannot_enter_the_ranking_window(tmp_path):
    """No collection time means no proof the label was observable pre-split.

    A legacy or replayed archive row carries only a venue stamp, so its actual
    availability is unknown and may be later than the split; ranking on it is
    look-ahead (Codex P1 wave-13). Rejecting such rows outright would zero the
    artifact on any legacy archive, so they stay measurable and are barred from
    the ranking window only.
    """
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _trade_summary(cfg)
    # tok-a's feature has NO collected_at_utc; tok-e's does.
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            {"source_timestamp": 310, "collected_at_utc": "", "asset_id": "tok-a", "midpoint": 0.70},
            {"source_timestamp": 1310, "collected_at_utc": 1310, "asset_id": "tok-e", "midpoint": 0.70},
            {"source_timestamp": 1315, "collected_at_utc": 1315, "asset_id": "tok-e", "midpoint": 0.70},
        ],
        fieldnames=["source_timestamp", "collected_at_utc", "asset_id", "midpoint"],
    )
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [
            {"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0},
            {"market": "0xe", "asset_id": "tok-e", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 1000},
            {"market": "0xe", "asset_id": "tok-e", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 1010},
        ],
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )

    build_flow_toxicity(cfg)
    rows = {row["wallet"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv")}

    # Market 0xa would rank on fill time (0 + 300 < split 1000), but its label
    # has no proven availability, so it is embargoed instead.
    assert int(rows["w1"]["fills_ranking_window"]) == 0
    assert int(rows["w1"]["fills_label_embargoed"]) == 1
    # Still measured: it counts toward the total and the evaluation window is
    # unaffected.
    assert int(rows["w1"]["fills_total"]) == 3
    assert int(rows["w1"]["fills_evaluation_window"]) == 2


def test_unavailable_leaderboard_is_not_reported_as_confirmed_absence(tmp_path):
    """Unknown membership must not render as a definite False.

    With leaderboard_history.csv absent, _top_wallets returns an empty set and
    every wallet was emitted with on_current_leaderboard=False and
    artifact_status="ok" -- so "confirmed absent from the current leaderboard"
    and "membership could not be determined" were indistinguishable, and a
    potentially ranked wallet read as definitively unranked (Codex P2 wave-14).
    """
    cfg = _config(tmp_path)
    # No _leaderboard(cfg): the dependency is missing.
    _trade_summary(cfg)
    _features(cfg, "tok-a", [(310, 0.70)])
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0}],
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )

    summary = build_flow_toxicity(cfg)
    rows = {row["wallet"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv")}

    assert rows["w1"]["on_current_leaderboard"] == "unknown"
    assert rows["w1"]["artifact_status"] == "leaderboard_unavailable"
    # Still measured -- markouts do not depend on the leaderboard.
    assert float(rows["w1"]["markout_mean_total"]) == 0.2
    assert summary["missing_wallet_data"] is True


def test_finite_but_out_of_domain_trade_values_are_rejected(tmp_path):
    """price=1.5 is not a print and size<=0 is not a fill (Codex P2 wave-15).

    Both are finite, so the wave-5 non-finite guard let them through: an
    out-of-range binary-market price produces a meaningless markout, and a
    nonpositive size increments the wallet's fill counters while contributing
    no VPIN volume -- a successful markout published for something that never
    traded.
    """
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _trade_summary(cfg)
    _features(cfg, "tok-a", [(310, 0.70)])
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [
            {"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0},
            {"market": "0xa", "asset_id": "tok-a", "wallet": "wbad", "side": "BUY", "price": 1.5, "size": 100, "timestamp": 1},
            {"market": "0xa", "asset_id": "tok-a", "wallet": "wzero", "side": "BUY", "price": 0.5, "size": 0, "timestamp": 2},
            {"market": "0xa", "asset_id": "tok-a", "wallet": "wneg", "side": "BUY", "price": -0.2, "size": 5, "timestamp": 3},
        ],
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )

    summary = build_flow_toxicity(cfg)
    rows = {row["wallet"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv")}

    assert set(rows) == {"w1"}
    assert summary["malformed_trade_rows_excluded"] == 3
    market = {row["market"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity.csv")}
    assert int(market["0xa"]["trades_seen"]) == 1


def test_an_unusable_leaderboard_snapshot_is_unavailable_not_empty(tmp_path):
    """A nonempty file with blank wallet cells is not "nobody is ranked"."""
    cfg = _config(tmp_path)
    write_csv(
        cfg.output_root / "wallet_intelligence" / "leaderboard_history.csv",
        [{"snapshot_date": "2026-07-10", "wallet": "", "rank": "1"}],
        fieldnames=["snapshot_date", "wallet", "rank"],
    )
    write_json(
        cfg.output_root / "wallet_intelligence" / "wallet_intelligence_summary.json",
        {"status": "ok", "generated_at_utc": "2026-08-22T00:00:00Z"},
    )
    _trade_summary(cfg)
    _features(cfg, "tok-a", [(310, 0.70)])
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0}],
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )

    summary = build_flow_toxicity(cfg)
    rows = {row["wallet"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv")}

    assert summary["missing_wallet_data"] is True
    assert rows["w1"]["on_current_leaderboard"] == "unknown"
    assert rows["w1"]["artifact_status"] == "leaderboard_unavailable"


def test_a_changed_horizon_invalidates_the_frozen_split(tmp_path):
    """The embargo is horizon-relative, so a cutoff frozen under another one
    is not reusable -- shortening the horizon could move a previously
    embargoed pre-split market into ranking after its markout was published.
    """
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _trade_summary(cfg)
    _features(cfg, "tok-a", [(310, 0.70)])
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0}],
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )
    assert build_flow_toxicity(cfg)["status"] == "ok"
    state = read_json(cfg.output_root / "maker_carry" / "flow_toxicity_wallet_split.json")
    assert state["horizon_seconds"] == 300.0

    raw = yaml.safe_load(Path(cfg.path).read_text(encoding="utf-8"))
    raw["flow_toxicity"]["markout_horizon_minutes"] = 2
    Path(cfg.path).write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="refusing to manufacture"):
        build_flow_toxicity(load_config(Path(cfg.path)))

    wallet_rows = read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv")
    assert wallet_rows[0]["artifact_status"] == "invalid_frozen_split_state"


def test_a_fill_without_a_venue_timestamp_is_rejected(tmp_path):
    """Collection time must never stand in for fill time (Codex P1 wave-17).

    trade_print_collector persists a BLANK timestamp when /trades omits both
    `timestamp` and `matchTime`, beside a collected_at_utc of now(). Falling
    back made a delayed or backfilled historical fill look as though it occurred
    when it was FETCHED, moving both its markout target and its window
    classification by hours or days. This is the same venue-vs-collection
    conflation fixed on the FEATURE side; it sat on the trade side in the
    identical `or` idiom.
    """
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _trade_summary(cfg)
    _features(cfg, "tok-a", [(310, 0.70)])
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [
            {"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5,
             "size": 100, "timestamp": 0, "collected_at_utc": 0},
            {"market": "0xa", "asset_id": "tok-a", "wallet": "wnostamp", "side": "BUY", "price": 0.5,
             "size": 100, "timestamp": "", "collected_at_utc": 99999},
        ],
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp", "collected_at_utc"],
    )

    build_flow_toxicity(cfg)
    rows = {row["wallet"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv")}

    assert set(rows) == {"w1"}, "the fill with no venue timestamp must not be scored at 99999"


def test_a_failed_leaderboard_refresh_renders_membership_unknown(tmp_path):
    """Retained rows survive a failed refresh, so they are not authoritative."""
    cfg = _config(tmp_path)
    _leaderboard(cfg, producer_status="failed")
    _trade_summary(cfg)
    _features(cfg, "tok-a", [(310, 0.70)])
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xa", "asset_id": "tok-a", "wallet": "smart1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0}],
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )

    summary = build_flow_toxicity(cfg)
    rows = {row["wallet"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv")}

    # smart1 IS in the retained file, but the refresh failed, so membership is
    # unknown rather than a definitive True.
    assert summary["missing_wallet_data"] is True
    assert rows["smart1"]["on_current_leaderboard"] == "unknown"
    assert rows["smart1"]["artifact_status"] == "leaderboard_unavailable"
    # The LEGACY market-axis tier split must be untouched: smart1 is in the
    # retained top-100, so its fill is still classified smart. Returning an
    # empty wallet set here would silently rewrite smart_fill_count,
    # crowd_fill_count and both tier markouts in the parent's registered
    # artifact (Codex P1 wave-18).
    market = {row["market"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity.csv")}
    assert int(market["0xa"]["smart_fill_count"]) == 1
    assert int(market["0xa"]["crowd_fill_count"]) == 0


def test_a_contended_run_writes_nothing(tmp_path):
    """The check/create/build sequence around the split is serialised."""
    from polymarket_predictive_engine.runtime_lock import acquire_runtime_lock, release_runtime_lock

    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _trade_summary(cfg)
    _features(cfg, "tok-a", [(310, 0.70)])
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0}],
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )

    held = acquire_runtime_lock(cfg, "flow_toxicity")
    try:
        summary = build_flow_toxicity(cfg)
    finally:
        release_runtime_lock(held)

    assert summary["status"] == "skipped_locked"
    assert summary["paper_trading_invoked"] is False
    # Nothing was written: no split state can have been created by the loser.
    assert not (cfg.output_root / "maker_carry" / "flow_toxicity_wallet_split.json").exists()
    assert not (cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv").exists()


def test_a_split_state_without_provenance_flags_is_invalid(tmp_path):
    """The split artifact must assert its own evidence class to be reused.

    A persisted split with the invocation flags absent -- or with either set
    true -- was reused indefinitely and reported as frozen, while the file
    itself claimed an unknown or forbidden provenance (Codex P1 wave-18).
    """
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _trade_summary(cfg)
    _features(cfg, "tok-a", [(310, 0.70)])
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0}],
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )
    assert build_flow_toxicity(cfg)["status"] == "ok"
    split_path = cfg.output_root / "maker_carry" / "flow_toxicity_wallet_split.json"
    state = read_json(split_path)

    # Flags absent.
    stripped = {k: v for k, v in state.items() if not k.endswith("_trading_invoked")}
    write_json(split_path, stripped)
    with pytest.raises(ValueError, match="refusing to manufacture"):
        build_flow_toxicity(cfg)

    # Flag present but true.
    write_json(split_path, {**state, "live_trading_invoked": True})
    with pytest.raises(ValueError, match="refusing to manufacture"):
        build_flow_toxicity(cfg)

    # The untouched original is still reusable.
    write_json(split_path, state)
    assert build_flow_toxicity(cfg)["wallet_split_was_frozen"] is True


def test_a_holder_failure_does_not_discard_a_fresh_leaderboard(tmp_path):
    """Aggregate "partial" must not condemn a leaderboard that did refresh.

    wallet_intelligence_collector sets "partial" when ANY request failed --
    including an unrelated HOLDER request -- so keying off the aggregate
    discarded a leaderboard that had just refreshed successfully and rendered
    every membership unknown (Codex P2 wave-19). leaderboard_rows_added is the
    per-dependency evidence.
    """
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    write_json(
        cfg.output_root / "wallet_intelligence" / "wallet_intelligence_summary.json",
        {"status": "partial", "leaderboard_rows_added": 100, "holder_rows_added": 0},
    )
    _trade_summary(cfg)
    _features(cfg, "tok-a", [(310, 0.70)])
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xa", "asset_id": "tok-a", "wallet": "smart1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0}],
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )

    summary = build_flow_toxicity(cfg)
    rows = {row["wallet"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv")}

    assert summary["missing_wallet_data"] is False
    assert rows["smart1"]["on_current_leaderboard"] == "True"
    assert rows["smart1"]["artifact_status"] == "ok"
    # Partial with NO leaderboard rows is still unknown.
    write_json(
        cfg.output_root / "wallet_intelligence" / "wallet_intelligence_summary.json",
        {"status": "partial", "leaderboard_rows_added": 0},
    )
    assert build_flow_toxicity(cfg)["missing_wallet_data"] is True


def test_an_invalid_horizon_does_not_condemn_a_sound_split(tmp_path):
    """One fault must not manufacture a second (Codex P2 wave-19).

    An invalid horizon substitutes a 5-minute fallback purely so the
    horizon-independent veto can rebuild. Comparing a sound persisted split
    against that fallback falsely labelled it invalid_frozen_split_state and
    overwrote the invalid-horizon sentinel, even though restoring the setting
    makes the split valid again.
    """
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _trade_summary(cfg)
    _features(cfg, "tok-a", [(130, 0.70)])
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0}],
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )
    raw = yaml.safe_load(Path(cfg.path).read_text(encoding="utf-8"))
    raw["flow_toxicity"]["markout_horizon_minutes"] = 2
    Path(cfg.path).write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert build_flow_toxicity(load_config(Path(cfg.path)))["status"] == "ok"

    raw["flow_toxicity"]["markout_horizon_minutes"] = "not-a-number"
    Path(cfg.path).write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="finite positive number"):
        build_flow_toxicity(load_config(Path(cfg.path)))

    # The sentinel names the REAL fault, not a manufactured split failure.
    rows = read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv")
    assert rows[0]["artifact_status"] == "invalid_markout_horizon"
    # Restoring the setting makes the split usable again -- it was never bad.
    raw["flow_toxicity"]["markout_horizon_minutes"] = 2
    Path(cfg.path).write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert build_flow_toxicity(load_config(Path(cfg.path)))["wallet_split_was_frozen"] is True


def test_an_aborted_build_invalidates_stale_wallet_evidence(tmp_path):
    """A raise mid-build must not leave yesterday's rankings readable as ok."""
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _trade_summary(cfg)
    _features(cfg, "tok-a", [(310, 0.70)])
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0}],
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )
    wallet_path = cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv"
    assert build_flow_toxicity(cfg)["status"] == "ok"
    assert read_csv_rows(wallet_path)[0]["artifact_status"] == "ok"

    # A truncated archive: the index build raises part-way through.
    archive = cfg.output_root / "polymarket_training_archive"
    archive.mkdir(parents=True, exist_ok=True)
    (archive / "corrupt.csv.gz").write_bytes(b"not-gzip-at-all")

    with pytest.raises(Exception):
        build_flow_toxicity(cfg)

    rows = read_csv_rows(wallet_path)
    assert len(rows) == 1
    assert rows[0]["artifact_status"] == "build_failed"
    assert read_json(cfg.output_root / "maker_carry" / "flow_toxicity_summary.json")["status"] == "build_failed"


def test_an_all_rejected_corpus_is_not_an_empty_ledger(tmp_path):
    """Source rows that all fail parsing are a malformed producer output.

    Every row failing ingestion produced no trades, which then emitted the
    benign no_wallets_scored sentinel and summary status "ok" -- indistinguish-
    able from a legitimate quiet day (Codex P2 wave-20). The basic-shape
    rejections were also silent, never incrementing the malformed counter.
    """
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _trade_summary(cfg)
    _features(cfg, "tok-a", [(310, 0.70)])
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [
            {"market": "", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0},
            {"market": "0xa", "asset_id": "tok-a", "wallet": "w2", "side": "BUY", "price": 0.5, "size": 100, "timestamp": ""},
        ],
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )

    summary = build_flow_toxicity(cfg)
    rows = read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv")

    assert summary["status"] == "malformed_trade_corpus"
    assert summary["trade_source_rows"] == 2
    assert summary["malformed_trade_rows_excluded"] == 2
    assert len(rows) == 1 and rows[0]["artifact_status"] == "malformed_trade_corpus"


def test_an_incomplete_leaderboard_is_not_authoritative(tmp_path):
    """A nonempty but INCOMPLETE top-100 cannot settle membership.

    The collector records leaderboard_probe_params.complete=false when the
    endpoint returned fewer than the requested limit. Trusting rows_added alone
    published ordinary True/False membership off a partial prefix, so wallets
    outside the fetched range read as definitively off the leaderboard (Codex P2
    wave-20).
    """
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    write_json(
        cfg.output_root / "wallet_intelligence" / "wallet_intelligence_summary.json",
        {"status": "partial", "leaderboard_rows_added": 12,
         "leaderboard_probe_params": {"complete": False}},
    )
    _trade_summary(cfg)
    _features(cfg, "tok-a", [(310, 0.70)])
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xa", "asset_id": "tok-a", "wallet": "smart1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0}],
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )

    summary = build_flow_toxicity(cfg)
    rows = {row["wallet"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv")}

    assert summary["missing_wallet_data"] is True
    assert rows["smart1"]["on_current_leaderboard"] == "unknown"
    # complete=true with the same partial status IS authoritative.
    write_json(
        cfg.output_root / "wallet_intelligence" / "wallet_intelligence_summary.json",
        {"status": "partial", "leaderboard_rows_added": 100,
         "leaderboard_probe_params": {"complete": True}},
    )
    assert build_flow_toxicity(cfg)["missing_wallet_data"] is False


def test_a_malformed_corpus_does_not_clear_the_market_veto(tmp_path):
    """A corrupt refresh must not silently un-block a toxic market.

    A nonempty ledger whose rows all fail parsing leaves rows_out empty, and
    writing a header-only flow_toxicity.csv would leave requote_alerts with no
    tox_record at all -- clearing the ACTIVE veto for every market (Codex P1
    wave-21). Same reasoning as the invalid-horizon path: absent fails OPEN, so
    stale-blocking is strictly safer.
    """
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _trade_summary(cfg)
    _features(cfg, "tok-a", [(0, 0.5), (10_000, 0.5)])
    good = [
        {"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": i}
        for i in range(20)
    ]
    fields = ["market", "asset_id", "wallet", "side", "price", "size", "timestamp"]
    trade_path = cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv"
    write_csv(trade_path, good, fieldnames=fields)
    assert build_flow_toxicity(cfg)["status"] == "ok"
    market_path = cfg.output_root / "maker_carry" / "flow_toxicity.csv"
    before = read_csv_rows(market_path)
    assert len(before) == 1

    # The next refresh is entirely corrupt.
    write_csv(
        trade_path,
        [{"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": ""}],
        fieldnames=fields,
    )
    summary = build_flow_toxicity(cfg)

    assert summary["status"] == "malformed_trade_corpus"
    assert summary["market_axis_preserved"] is True
    assert read_csv_rows(market_path) == before, "the active veto must survive a corrupt refresh"


def test_a_disabled_leaderboard_producer_yields_unknown_membership(tmp_path):
    """A disabled producer refreshes nothing, so membership is not current."""
    cfg = _config(tmp_path)
    _leaderboard(cfg, producer_status="disabled")
    _trade_summary(cfg)
    _features(cfg, "tok-a", [(310, 0.70)])
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xa", "asset_id": "tok-a", "wallet": "smart1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0}],
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )

    summary = build_flow_toxicity(cfg)
    rows = {row["wallet"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv")}

    assert summary["missing_wallet_data"] is True
    assert rows["smart1"]["on_current_leaderboard"] == "unknown"
    # The market-axis tier split still uses the retained top-100 (wave-18).
    market = {row["market"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity.csv")}
    assert int(market["0xa"]["smart_fill_count"]) == 1


def test_the_latest_intraday_snapshot_wins(tmp_path):
    """Two collector runs on one date must not be merged into one snapshot.

    _dedupe_latest retains same-day wallets that dropped out of the newer top
    100, so grouping by snapshot_date mixed both refreshes and could keep a
    stale wallet while excluding a current one (Codex P2 wave-21).
    """
    cfg = _config(tmp_path)
    write_csv(
        cfg.output_root / "wallet_intelligence" / "leaderboard_history.csv",
        [
            {"snapshot_date": "2026-07-10", "snapshot_at_utc": "2026-07-10T06:00:00Z", "wallet": "stale1", "rank": "1"},
            {"snapshot_date": "2026-07-10", "snapshot_at_utc": "2026-07-10T18:00:00Z", "wallet": "fresh1", "rank": "1"},
        ],
        fieldnames=["snapshot_date", "snapshot_at_utc", "wallet", "rank"],
    )
    write_json(
        cfg.output_root / "wallet_intelligence" / "wallet_intelligence_summary.json",
        {"status": "ok", "leaderboard_rows_added": 1, "leaderboard_probe_params": {"complete": True}},
    )
    _trade_summary(cfg)
    _features(cfg, "tok-a", [(310, 0.70)])
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [
            {"market": "0xa", "asset_id": "tok-a", "wallet": "fresh1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0},
            {"market": "0xa", "asset_id": "tok-a", "wallet": "stale1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 1},
        ],
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )

    build_flow_toxicity(cfg)
    rows = {row["wallet"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv")}

    assert rows["fresh1"]["on_current_leaderboard"] == "True"
    assert rows["stale1"]["on_current_leaderboard"] == "False", (
        "the 06:00 wallet dropped out of the 18:00 top-100 and is not current"
    )
    # The LEGACY market-axis tier split keeps the parent's DATE grouping, so
    # both same-day wallets remain smart there (Codex P1 wave-22). Narrowing to
    # the latest instant for the new column must not rewrite the parent's
    # smart_fill_count / crowd_fill_count -- that was the wave-18 defect
    # recurring in the same function four waves later.
    market = {row["market"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity.csv")}
    assert int(market["0xa"]["smart_fill_count"]) == 2
    assert int(market["0xa"]["crowd_fill_count"]) == 0


def test_wallet_without_any_forward_price_is_still_emitted(tmp_path):
    """A wallet with no measurable fill must appear, disclosed, not vanish.

    Opening the wallet's accounting only after the forward-price lookup meant a
    wallet whose every fill lacked a forward price was absent from the artifact
    entirely -- indistinguishable from a wallet that never traded (Codex P2 on
    #451). "Was not measurable" and "did not trade" are different facts.
    """
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _features(cfg, "tok-ok", [(310, 0.70)])
    # tok-none has NO feature rows at all: every fill on it is unmeasurable.
    trades = [
        {"market": "0xok", "asset_id": "tok-ok", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0},
        {"market": "0xnone", "asset_id": "tok-none", "wallet": "w2", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 1},
        {"market": "0xnone", "asset_id": "tok-none", "wallet": "w2", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 2},
    ]
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        trades,
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )

    summary = build_flow_toxicity(cfg)
    rows = {row["wallet"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv")}

    assert "w2" in rows, "a wallet whose fills were all unmeasurable must still be emitted"
    assert int(rows["w2"]["fills_missing_price"]) == 2
    assert int(rows["w2"]["fills_total"]) == 0
    assert rows["w2"]["markout_mean_total"] == ""
    # The market it traded is still credited as touched: it did trade there.
    assert int(rows["w2"]["markets_touched"]) == 1
    assert int(rows["w1"]["fills_missing_price"]) == 0
    assert int(rows["w1"]["fills_total"]) == 1
    assert rows["w1"]["artifact_status"] == "upstream_missing", (
        "no producer summary was written in this fixture, so every row is stamped"
    )
    # w2 is retained for COVERAGE but is not a scored wallet: it has no usable
    # label at all. Counting it made wallets_scored claim a healthy artifact in
    # which no markout exists (Codex P2 wave-21). The row stays; the count does
    # not include it.
    assert summary["wallets_scored"] == 1


def test_disabled_flow_toxicity_clears_the_wallet_artifact(tmp_path):
    """Disabled must not leave last run's rankings on disk looking current.

    The disabled early return precedes every artifact write, so a stale
    flow_toxicity_wallets.csv survived with its own generated_at_utc and read
    as current (Codex P2 on #451). The wallet artifact is new in this change,
    so its disabled-path behaviour is ours to define: header-only.
    """
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _features(cfg, "tok-a", [(310, 0.70)])
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        [{"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0}],
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )
    wallet_path = cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv"

    assert build_flow_toxicity(cfg)["status"] == "ok"
    assert len(read_csv_rows(wallet_path)) == 1

    raw = yaml.safe_load(Path(cfg.path).read_text(encoding="utf-8"))
    raw["flow_toxicity"]["enabled"] = False
    Path(cfg.path).write_text(yaml.safe_dump(raw), encoding="utf-8")
    disabled_cfg = load_config(Path(cfg.path))

    assert build_flow_toxicity(disabled_cfg)["status"] == "disabled"
    assert wallet_path.exists(), "the artifact stays, cleared -- absence is not a disclosure"
    rows = read_csv_rows(wallet_path)
    # A header-only file names the columns but ASSERTS nothing, so the disabled
    # artifact carries one sentinel row stating its own evidence class.
    assert len(rows) == 1
    assert rows[0]["artifact_status"] == "disabled"
    assert rows[0]["wallet"] == ""
    assert rows[0]["paper_trading_invoked"] == "False"
    assert rows[0]["live_trading_invoked"] == "False"
    assert "w1" not in {row["wallet"] for row in rows}, "the previous run's rankings are gone"
    header = wallet_path.read_text(encoding="utf-8").splitlines()[0]
    assert "fills_label_embargoed" in header and "fills_missing_price" in header


def test_wallet_markout_rejects_stale_prices_and_market_axis_is_unchanged(tmp_path):
    """A 5m markout read from a price a day late measures a different horizon.

    The wallet axis accepts a price only inside [target, target + horizon];
    later observations count the fill as stale-excluded (Codex P1 on #451).
    The market-axis smart/crowd columns keep the long-standing WO-49 lookup.
    """
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    # horizon 5m = 300s; target for a t=0 fill is 300; only feature is at
    # t=100_000 -> 99_700s past target, far beyond the one-horizon tolerance.
    _features(cfg, "tok-a", [(100_000, 0.90)])
    trades = [
        {"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0},
    ]
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        trades,
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )

    build_flow_toxicity(cfg)
    wallet_rows = {row["wallet"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv")}
    market_rows = {row["market"]: row for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity.csv")}

    assert int(wallet_rows["w1"]["fills_stale_price_excluded"]) == 1
    assert int(wallet_rows["w1"]["fills_total"]) == 0
    assert wallet_rows["w1"]["markout_mean_total"] == ""
    # Market axis unchanged: the stale point still scores there, as today.
    assert int(market_rows["0xa"]["crowd_fill_count"]) == 1


def test_wallet_artifact_states_its_own_invocation_flags(tmp_path):
    """AGENTS.md: every NEW artifact states paper/live_trading_invoked=false
    itself; the summary's copy does not satisfy the artifact-level invariant."""
    cfg = _config(tmp_path)
    _leaderboard(cfg)
    _features(cfg, "tok-a", [(310, 0.70)])  # inside [target, target+horizon]
    trades = [
        {"market": "0xa", "asset_id": "tok-a", "wallet": "w1", "side": "BUY", "price": 0.5, "size": 100, "timestamp": 0},
    ]
    write_csv(
        cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv",
        trades,
        fieldnames=["market", "asset_id", "wallet", "side", "price", "size", "timestamp"],
    )

    build_flow_toxicity(cfg)
    rows = read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity_wallets.csv")

    assert rows and rows[0]["paper_trading_invoked"] == "False"
    assert rows[0]["live_trading_invoked"] == "False"
