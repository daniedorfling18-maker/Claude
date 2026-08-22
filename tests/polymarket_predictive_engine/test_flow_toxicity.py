"""WO-49 flow-toxicity conditioning tests."""
from __future__ import annotations

import gzip
from pathlib import Path

import yaml

from polymarket_predictive_engine import flow_toxicity
from polymarket_predictive_engine import maker_carry_study
from polymarket_predictive_engine.cli import COMMANDS
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.flow_toxicity import build_flow_toxicity
from polymarket_predictive_engine.utils import read_csv_rows, write_csv


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
    existing = read_csv_rows(cfg.output_root / "polymarket_training" / "websocket_market_features.csv")
    rows = [*existing, *[{"source_timestamp": stamp, "asset_id": token, "midpoint": price} for stamp, price in points]]
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        rows,
        fieldnames=["source_timestamp", "asset_id", "midpoint"],
    )


def _leaderboard(cfg) -> None:
    write_csv(
        cfg.output_root / "wallet_intelligence" / "leaderboard_history.csv",
        [{"snapshot_date": "2026-07-10", "wallet": "smart1", "rank": "1"}],
        fieldnames=["snapshot_date", "wallet", "rank"],
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
    assert summary["wallets_scored"] == 2


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
    assert read_csv_rows(wallet_path) == []
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
