"""WO-50 registered maker live-test decision policy tests."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from polymarket_predictive_engine import live_test_decision_policy as policy
from polymarket_predictive_engine.cli import COMMANDS
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.dashboard import render_dashboard
from polymarket_predictive_engine.live_test_decision_policy import run_decision_policy
from polymarket_predictive_engine.utils import read_json, write_csv, write_json


def _config(tmp_path: Path, *, live_configured: bool = False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    raw["decision_policy"] = {
        "enabled": True,
        "decision_date": "2026-07-20",
        "composition_stable_days": 7,
        "composition_required_recurrence": 4,
        "below_target_review_runs": 7,
        "below_target_review_threshold": 3,
        "stage0_capital_usd": 100.0,
        "stage1_capital_usd": 250.0,
        "stage2_capital_usd": 500.0,
        "stage1_min_consecutive_days": 7,
        "stage2_additional_days": 14,
        "stage2_reward_realisation_multiple": 0.5,
        "kill_cumulative_net_score_usd": -25.0,
        "kill_single_day_net_usd": -15.0,
        "kill_fill_overrun_days": 2,
        "fill_alert_multiple": 2.0,
        "kill_input_max_age_seconds": 1800,
        "kelly_fraction_cap": 1.0,
        "quarter_kelly_multiplier": 0.25,
    }
    raw["maker_live_test"]["enabled"] = True
    raw["maker_live_test"]["wallet_address"] = (
        "0x1111111111111111111111111111111111111111" if live_configured else ""
    )
    raw["maker_live_test"]["executor_wallet_address"] = ""
    raw["live_trading"]["enabled"] = False
    raw["trading"]["mode"] = "paper"
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def _out(cfg) -> Path:
    out = cfg.output_root / "maker_carry"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _study(gate_a: str = "pass", gate_b: str = "pass", *, top: str = "0xm1", net: float = 5.0) -> dict[str, Any]:
    return {
        "status": "ok",
        "generated_at_utc": "2026-07-19T08:00:00Z",
        "target_net_usd_per_day": 3.33,
        "portfolio": [
            {
                "condition_id": top,
                "question": f"top market {top}",
                "net_carry_usd_per_day": net,
            }
        ],
        "portfolio_net_carry_usd_per_day": net,
        "maker_gates": {
            "M_A_carry_evidence": {"state": gate_a},
            "M_B_adverse_realism": {"state": gate_b},
        },
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }


def _write_study(cfg, payload: dict[str, Any]) -> None:
    write_json(_out(cfg) / "maker_carry_study.json", payload)


def _write_carry_history(cfg, rows: list[dict[str, Any]]) -> None:
    write_csv(
        _out(cfg) / "maker_carry_history.csv",
        rows,
        fieldnames=[
            "generated_at_utc",
            "share_model",
            "portfolio_net_carry_usd_per_day",
            "top_portfolio_market",
            "top_portfolio_question",
        ],
    )


def _history(markets: list[str], *, nets: list[float] | None = None) -> list[dict[str, Any]]:
    nets = nets or [5.0] * len(markets)
    return [
        {
            "generated_at_utc": f"2026-07-{10 + idx:02d}T08:00:00Z",
            "share_model": "published_v2",
            "portfolio_net_carry_usd_per_day": nets[idx],
            "top_portfolio_market": market,
            "top_portfolio_question": f"question {market}",
        }
        for idx, market in enumerate(markets)
    ]


def _write_live_history(cfg, rows: list[dict[str, Any]]) -> None:
    write_csv(
        _out(cfg) / "maker_live_test_history.csv",
        rows,
        fieldnames=[
            "generated_at_utc",
            "net_score_usd",
            "daily_net_score_usd",
            "fills_last_24h",
            "modelled_fills_per_day",
            "fill_alert",
            "fill_alert_multiple",
            "scoreboard",
        ],
    )


def _live_rows(days: int, *, net: float = 1.0, fills: float = 1.0, modelled: float = 1.0) -> list[dict[str, Any]]:
    return [
        {
            "generated_at_utc": f"2026-07-{idx + 1:02d}T08:00:00Z",
            "net_score_usd": net + idx,
            "daily_net_score_usd": net,
            "fills_last_24h": fills,
            "modelled_fills_per_day": modelled,
            "fill_alert": False,
            "fill_alert_multiple": 2.0,
            "scoreboard": "winning_so_far",
        }
        for idx in range(days)
    ]


def test_policy_table_stable_pass_funds_single_calmest_market(tmp_path):
    cfg = _config(tmp_path)
    _write_study(cfg, _study(top="0xm1"))
    _write_carry_history(cfg, _history(["0xm1", "0xm1", "0xm2", "0xm1", "0xm3", "0xm1"]))

    result = run_decision_policy(cfg)

    assert result["indicated_action"] == "fund_100_min_size_single_calmest_market"
    assert result["composition_stability"]["most_recurrent_market"] == "0xm1"
    assert result["composition_stability"]["most_recurrent_count"] >= 4
    assert result["paper_trading_invoked"] is False
    assert result["live_trading_invoked"] is False


def test_policy_table_churning_pass_halves_recurrent_market_target(tmp_path):
    cfg = _config(tmp_path)
    _write_study(cfg, _study(top="0xm7"))
    _write_carry_history(cfg, _history(["0xm1", "0xm2", "0xm3", "0xm4", "0xm5", "0xm6"]))

    result = run_decision_policy(cfg)

    assert result["indicated_action"] == "fund_100_but_only_most_recurrent_market_half_target"
    assert result["composition_stability"]["stable"] is False


def test_policy_table_pending_on_registered_decision_date_defers(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    _write_study(cfg, _study(gate_a="pending", gate_b="pass"))
    _write_carry_history(cfg, _history(["0xm1", "0xm1", "0xm1", "0xm1"]))
    monkeypatch.setattr(policy, "now_utc", lambda: "2026-07-20T12:00:00Z")

    result = run_decision_policy(cfg)

    assert result["indicated_action"] == "defer_funding_continue_study"
    assert result["action_reason"] == "policy_decision_date_reached_with_pending_maker_gate"


def test_policy_table_below_target_after_gates_reachable_triggers_review(tmp_path):
    cfg = _config(tmp_path)
    _write_study(cfg, _study(top="0xm1", net=5.0))
    _write_carry_history(cfg, _history(["0xm1"] * 7, nets=[2.0, 2.5, 1.0, 2.2, 5.0, 6.0, 7.0]))

    result = run_decision_policy(cfg)

    assert result["indicated_action"] == "maker_lane_not_supported_program_review"
    assert result["inputs_snapshot"]["recent_below_target_runs"] == 4


def test_ladder_promotion_arithmetic_and_stage2_reward_floor(tmp_path):
    cfg = _config(tmp_path)
    _write_study(cfg, _study(top="0xm1"))
    _write_carry_history(cfg, _history(["0xm1"] * 7))
    _write_live_history(cfg, _live_rows(7))

    stage1 = run_decision_policy(cfg)

    assert stage1["ladder_stage_permitted"] == 1
    assert stage1["ladder"]["ladder_capital_usd"] == 250.0

    write_csv(
        _out(cfg) / "maker_carry_candidates.csv",
        [{"condition_id": "0xm1", "gross_reward_usd_per_day": 4.0}],
        fieldnames=["condition_id", "gross_reward_usd_per_day"],
    )
    write_json(_out(cfg) / "maker_live_test.json", {"status": "ok", "rewards_usd_total": 42.0})
    _write_live_history(cfg, _live_rows(21))

    stage2 = run_decision_policy(cfg)

    assert stage2["ladder_stage_permitted"] == 2
    assert stage2["ladder"]["ladder_capital_usd"] == 500.0
    assert stage2["ladder"]["stage2_reward_floor_usd"] == 42.0


def test_ladder_ignores_owner_activity_only_day_without_breaking_streak(tmp_path):
    cfg = _config(tmp_path)
    _write_study(cfg, _study(top="0xm1"))
    _write_carry_history(cfg, _history(["0xm1"] * 8))
    rows = _live_rows(8)
    rows[3]["scoreboard"] = "owner_activity_only"
    rows[3]["fills_last_24h"] = 0
    _write_live_history(cfg, rows)

    result = run_decision_policy(cfg)

    assert result["ladder_stage_permitted"] == 1
    assert result["ladder"]["consecutive_live_ok_days"] == 7
    assert result["ladder"]["owner_activity_days_ignored"] == 1


def test_quarter_kelly_cap_binds_below_ladder(tmp_path):
    cfg = _config(tmp_path)
    _write_study(cfg, _study(top="0xm1"))
    _write_carry_history(cfg, _history(["0xm1"] * 7, nets=[1.0, 9.0, 1.0, 9.0, 1.0, 9.0, 1.0]))
    _write_live_history(cfg, _live_rows(7))

    result = run_decision_policy(cfg)

    assert result["ladder_stage_permitted"] == 1
    assert result["sizing"]["binding_cap"] == "quarter_kelly"
    assert result["sizing"]["binding_capital_usd"] < result["ladder"]["ladder_capital_usd"]
    assert result["sizing"]["quarter_kelly_fraction"] < result["sizing"]["inline_quarter_kelly_fraction"]
    assert result["sizing"]["kelly_lineage"] == "risk.shrunk_kelly_fraction"


def test_short_history_shrunk_kelly_cannot_exceed_inline_cap():
    settings = dict(policy.DEFAULT_SETTINGS)
    settings["kelly_full_weight_days"] = 2  # tighten-only floor must ignore this loosening attempt
    history = _history(["0xm1"] * 7, nets=[1.0, 9.0, 1.0, 9.0, 1.0, 9.0, 1.0])

    sizing = policy._quarter_kelly_cap(history, 250.0, settings)

    assert sizing["kelly_observations"] == 7
    assert sizing["kelly_full_weight_days"] == 20
    assert sizing["kelly_shrinkage"] == 0.65
    assert 0 < sizing["quarter_kelly_fraction"] < sizing["inline_quarter_kelly_fraction"]
    assert sizing["kelly_capital_usd"] <= 250.0 * sizing["inline_quarter_kelly_fraction"]


def test_shrunk_kelly_equals_inline_at_large_sample_floor():
    settings = dict(policy.DEFAULT_SETTINGS)
    history = _history(["0xm1"] * 20, nets=[1.0, 9.0] * 10)

    sizing = policy._quarter_kelly_cap(history, 250.0, settings)

    assert sizing["kelly_observations"] == 20
    assert sizing["kelly_shrinkage"] == 0.0
    assert sizing["quarter_kelly_fraction"] == sizing["inline_quarter_kelly_fraction"]


def test_ladder_still_binds_when_it_is_the_smaller_ceiling():
    settings = dict(policy.DEFAULT_SETTINGS)
    settings["quarter_kelly_multiplier"] = 1.0
    history = _history(["0xm1"] * 20, nets=[5.0] * 20)

    sizing = policy._quarter_kelly_cap(history, 100.0, settings)

    assert sizing["inline_quarter_kelly_fraction"] == 1.0
    assert sizing["quarter_kelly_fraction"] == 1.0
    assert sizing["kelly_capital_usd"] == 100.0
    assert sizing["binding_capital_usd"] == 100.0
    assert sizing["binding_cap"] == "ladder"


def _history_with_capital(nets: list[float], capital: list[float]) -> list[dict[str, Any]]:
    rows = _history(["0xm1"] * len(nets), nets=nets)
    for row, cap in zip(rows, capital):
        row["portfolio_capital_usd"] = cap
    return rows


def test_daily_net_returns_drops_rows_without_positive_capital():
    rows = [
        {"portfolio_net_carry_usd_per_day": 5.0, "portfolio_capital_usd": 100.0},
        {"portfolio_net_carry_usd_per_day": 5.0, "portfolio_capital_usd": 0.0},
        {"portfolio_net_carry_usd_per_day": 5.0, "portfolio_capital_usd": None},
        {"portfolio_net_carry_usd_per_day": 5.0},
        {"portfolio_net_carry_usd_per_day": None, "portfolio_capital_usd": 100.0},
    ]

    assert policy._daily_net_returns(rows) == [0.05]


def test_kelly_fraction_is_unit_invariant_when_capital_present():
    # WO-104 item 4: Kelly is dimensionless. The same economics expressed in
    # dollars vs cents must produce an identical fraction; the old dollar-based
    # ``mean / std**2`` failed this by a factor of 100.
    settings = dict(policy.DEFAULT_SETTINGS)
    nets = [2.0, 4.0, 2.0, 4.0, 2.0, 4.0, 2.0]
    dollars = _history_with_capital(nets, [100.0] * 7)
    cents = _history_with_capital([n * 100 for n in nets], [10000.0] * 7)

    in_dollars = policy._quarter_kelly_cap(dollars, 250.0, settings)
    in_cents = policy._quarter_kelly_cap(cents, 250.0, settings)

    assert in_dollars["quarter_kelly_fraction"] == in_cents["quarter_kelly_fraction"]
    assert in_dollars["inline_quarter_kelly_fraction"] == in_cents["inline_quarter_kelly_fraction"]
    assert in_dollars["binding_capital_usd"] == in_cents["binding_capital_usd"]


def test_returns_normalised_kelly_loosens_versus_dollar_fallback():
    # Direction disclosure (owner-merged): normalising by capital removes the
    # unit-dependent under-sizing, so the returns-based overlay is >= the
    # retained conservative dollar fallback used when capital is unavailable.
    settings = dict(policy.DEFAULT_SETTINGS)
    nets = [2.0, 4.0, 2.0, 4.0, 2.0, 4.0, 2.0]
    with_capital = _history_with_capital(nets, [100.0] * 7)
    without_capital = _history(["0xm1"] * 7, nets=nets)

    loosened = policy._quarter_kelly_cap(with_capital, 250.0, settings)
    fallback = policy._quarter_kelly_cap(without_capital, 250.0, settings)

    assert loosened["binding_capital_usd"] >= fallback["binding_capital_usd"]
    assert loosened["quarter_kelly_fraction"] >= fallback["quarter_kelly_fraction"]


def test_kelly_observation_count_follows_usable_returns_not_dollar_rows():
    # #259: when only some rows carry capital, the shrinkage and reported
    # observation count must reflect the USABLE return observations, not the
    # larger dollar-row count (fewer observations => more shrinkage => tighter).
    settings = dict(policy.DEFAULT_SETTINGS)
    history = _history(["0xm1"] * 5, nets=[2.0, 4.0, 2.0, 4.0, 2.0])
    for row in history[:3]:  # older rows lack capital
        row["portfolio_capital_usd"] = ""
    for row in history[3:]:  # only the two newest rows have capital
        row["portfolio_capital_usd"] = 100.0

    sizing = policy._quarter_kelly_cap(history, 250.0, settings)

    assert sizing["kelly_observations"] == 2  # not 5


def test_returns_kelly_never_exceeds_ladder_cap():
    # Safety invariant survives the loosening: absolute exposure is bounded by
    # the ladder cap via ``binding = min(ladder_cap, kelly_capital)``, even when
    # the returns-based fraction saturates its own cap under a large multiplier.
    settings = dict(policy.DEFAULT_SETTINGS)
    settings["quarter_kelly_multiplier"] = 4.0
    history = _history_with_capital([4.0, 6.0] * 10, [100.0] * 20)

    sizing = policy._quarter_kelly_cap(history, 100.0, settings)

    assert sizing["binding_capital_usd"] <= 100.0
    assert sizing["kelly_capital_usd"] >= 0.0


def test_each_kill_criterion_trips_individually(tmp_path):
    cases = [
        (
            "cumulative_real_net_score",
            {"status": "ok", "net_score_usd": -25.0},
            _live_rows(1, net=1.0),
        ),
        (
            "single_day_net_score",
            {"status": "ok", "net_score_usd": 1.0},
            [{"generated_at_utc": "2026-07-01T08:00:00Z", "net_score_usd": 1.0, "daily_net_score_usd": -15.0}],
        ),
        (
            "fills_outrunning_model_two_days",
            {"status": "ok", "net_score_usd": 1.0},
            [
                {"generated_at_utc": "2026-07-01T08:00:00Z", "net_score_usd": 1.0, "daily_net_score_usd": 1.0, "fills_last_24h": 3.0, "modelled_fills_per_day": 1.0},
                {"generated_at_utc": "2026-07-02T08:00:00Z", "net_score_usd": 2.0, "daily_net_score_usd": 1.0, "fills_last_24h": 3.0, "modelled_fills_per_day": 1.0},
            ],
        ),
        (
            "uma_dispute_inventory",
            {"status": "ok", "net_score_usd": 1.0, "uma_dispute_detected": True},
            _live_rows(1, net=1.0),
        ),
        (
            "scoreboard_stop",
            {"status": "ok", "net_score_usd": 1.0, "scoreboard": "STOP_fills_outrunning_model"},
            _live_rows(1, net=1.0),
        ),
    ]
    for name, live_test, live_rows in cases:
        cfg = _config(tmp_path / name)
        _write_study(cfg, _study(top="0xm1"))
        _write_carry_history(cfg, _history(["0xm1"] * 7))
        write_json(_out(cfg) / "maker_live_test.json", live_test)
        _write_live_history(cfg, live_rows)

        result = run_decision_policy(cfg)

        assert result["indicated_action"] == "stop_quoting_review_before_resume"
        assert result["kill_criteria_status"]["criteria"][name]["triggered"] is True


def test_nan_first_day_cannot_mask_single_day_kill(tmp_path):
    # NaN fail-open (local-audit follow-up): a NaN first daily value used to
    # poison min() (min([nan, -20]) == nan; nan <= -15 is False), silently
    # disabling the single-day kill even with a real -$20 day on record. The
    # finite filter drops the NaN row so the losing day still triggers.
    cfg = _config(tmp_path)
    _write_study(cfg, _study(top="0xm1"))
    _write_carry_history(cfg, _history(["0xm1"] * 7))
    write_json(_out(cfg) / "maker_live_test.json", {"status": "ok", "net_score_usd": 1.0})
    _write_live_history(
        cfg,
        [
            {"generated_at_utc": "2026-07-01T08:00:00Z", "net_score_usd": 1.0, "daily_net_score_usd": "nan"},
            {"generated_at_utc": "2026-07-02T08:00:00Z", "net_score_usd": 1.0, "daily_net_score_usd": -20.0},
        ],
    )

    result = run_decision_policy(cfg)

    criteria = result["kill_criteria_status"]["criteria"]["single_day_net_score"]
    assert criteria["triggered"] is True
    assert criteria["worst_day"] == -20.0
    assert result["indicated_action"] == "stop_quoting_review_before_resume"


def test_nan_cumulative_score_reports_missing_not_comparing_false(tmp_path):
    # A NaN cumulative net_score_usd must be treated as MISSING (value None),
    # not slip through `is not None` into a permanently-False comparison.
    cfg = _config(tmp_path)
    _write_study(cfg, _study(top="0xm1"))
    _write_carry_history(cfg, _history(["0xm1"] * 7))
    write_json(_out(cfg) / "maker_live_test.json", {"status": "ok", "net_score_usd": "nan"})
    _write_live_history(cfg, _live_rows(1, net=1.0))

    result = run_decision_policy(cfg)

    criteria = result["kill_criteria_status"]["criteria"]["cumulative_real_net_score"]
    assert criteria["triggered"] is False
    assert criteria["value"] is None  # missing, not NaN


def test_nan_carry_rows_drop_from_kelly_observations():
    # A NaN carry row must not enter the dollar fallback's mean/std nor inflate
    # kelly_observations (which would REDUCE shrinkage — a loosening).
    settings = dict(policy.DEFAULT_SETTINGS)
    history = _history(["0xm1"] * 7, nets=[2.0, 4.0, 2.0, 4.0, 2.0, 4.0, 2.0])
    history[2]["portfolio_net_carry_usd_per_day"] = "nan"

    sizing = policy._quarter_kelly_cap(history, 250.0, settings)

    assert sizing["kelly_observations"] == 6  # NaN row excluded
    assert sizing["daily_net_mean_usd"] == sizing["daily_net_mean_usd"]  # not NaN


def test_nan_capital_rows_drop_from_returns():
    # _daily_net_returns must skip rows whose net or capital is non-finite.
    rows = [
        {"portfolio_net_carry_usd_per_day": 5.0, "portfolio_capital_usd": 100.0},
        {"portfolio_net_carry_usd_per_day": "nan", "portfolio_capital_usd": 100.0},
        {"portfolio_net_carry_usd_per_day": 5.0, "portfolio_capital_usd": "inf"},
    ]

    assert policy._daily_net_returns(rows) == [0.05]


def test_no_live_data_tolerance_and_missing_study(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    # Pin "now" before the registered decision_date (2026-07-20) so the study-missing
    # rule (priority 6) governs; otherwise, once the suite runs on/after that date,
    # the decision-date-pending rule (priority 5) fires first (clock-drift, see #338).
    monkeypatch.setattr(policy, "now_utc", lambda: "2026-07-15T08:00:00Z")

    result = run_decision_policy(cfg)

    assert result["status"] == "ok"
    assert result["indicated_action"] == "collect_maker_carry_study"
    assert result["ladder_stage_permitted"] == 0
    assert result["kill_criteria_status"]["status"] == "clear"
    assert result["kill_criteria_status"]["kill_input_freshness"]["state"] == "inactive_pre_live"
    assert result["kill_data_stale"] is False
    assert read_json(_out(cfg) / "decision_policy.json")["paper_trading_invoked"] is False


def test_live_stage_with_stale_kill_inputs_forces_stop(tmp_path, monkeypatch):
    cfg = _config(tmp_path, live_configured=True)
    _write_study(cfg, _study(top="0xm1"))
    _write_carry_history(cfg, _history(["0xm1"] * 7))
    write_json(
        _out(cfg) / "maker_live_test.json",
        {
            "status": "ok",
            "generated_at_utc": "2026-07-14T10:00:00Z",
            "net_score_usd": 2.0,
            "scoreboard": "winning_so_far",
        },
    )
    _write_live_history(
        cfg,
        [
            {
                "generated_at_utc": "2026-07-14T10:00:00Z",
                "net_score_usd": 2.0,
                "daily_net_score_usd": 2.0,
                "fills_last_24h": 1.0,
                "modelled_fills_per_day": 1.0,
                "fill_alert": False,
                "fill_alert_multiple": 2.0,
                "scoreboard": "winning_so_far",
            }
        ],
    )
    monkeypatch.setattr(policy, "now_utc", lambda: "2026-07-14T11:00:00Z")

    result = run_decision_policy(cfg)
    freshness = result["kill_criteria_status"]["kill_input_freshness"]

    assert result["indicated_action"] == "stop_quoting_review_before_resume"
    assert result["action_reason"] == "kill_input_data_stale"
    assert result["kill_data_stale"] is True
    assert result["kill_criteria_status"]["criteria"]["kill_data_stale"]["triggered"] is True
    assert freshness["guard_active"] is True
    assert freshness["age_seconds"] == 3600.0
    assert freshness["maximum_age_seconds"] == 1800.0
    assert "maker_live_test_wallet_configured" in freshness["guard_activation_reasons"]
    assert not result["indicated_action"].startswith(("fund_", "continue_"))


def test_live_stage_with_fresh_kill_inputs_preserves_existing_policy(tmp_path, monkeypatch):
    cfg = _config(tmp_path, live_configured=True)
    _write_study(cfg, _study(top="0xm1"))
    _write_carry_history(cfg, _history(["0xm1", "0xm1", "0xm2", "0xm1", "0xm3", "0xm1"]))
    write_json(
        _out(cfg) / "maker_live_test.json",
        {
            "status": "ok",
            "generated_at_utc": "2026-07-14T10:50:00Z",
            "net_score_usd": 2.0,
            "scoreboard": "winning_so_far",
        },
    )
    _write_live_history(
        cfg,
        [
            {
                "generated_at_utc": "2026-07-14T10:50:00Z",
                "net_score_usd": 2.0,
                "daily_net_score_usd": 2.0,
                "fills_last_24h": 1.0,
                "modelled_fills_per_day": 1.0,
                "fill_alert": False,
                "fill_alert_multiple": 2.0,
                "scoreboard": "winning_so_far",
            }
        ],
    )
    monkeypatch.setattr(policy, "now_utc", lambda: "2026-07-14T11:00:00Z")

    result = run_decision_policy(cfg)

    assert result["indicated_action"] == "fund_100_min_size_single_calmest_market"
    assert result["action_reason"] == "M_A_and_M_B_pass_with_stable_top_portfolio_market"
    assert result["kill_criteria_status"]["status"] == "clear"
    assert result["kill_data_stale"] is False
    assert result["kill_criteria_status"]["kill_input_freshness"]["state"] == "fresh"
    assert result["kill_criteria_status"]["kill_input_freshness"]["age_seconds"] == 600.0


def test_kill_input_freshness_override_can_only_tighten(tmp_path):
    cfg = _config(tmp_path)
    cfg.raw["decision_policy"]["kill_input_max_age_seconds"] = 99_999
    assert policy.effective_policy_settings(cfg)["kill_input_max_age_seconds"] == 1800

    cfg.raw["decision_policy"]["kill_input_max_age_seconds"] = 300
    assert policy.effective_policy_settings(cfg)["kill_input_max_age_seconds"] == 300


def test_live_guard_activation_covers_config_ladder_ledger_and_heartbeat(tmp_path):
    configured = _config(tmp_path / "configured", live_configured=True)
    configured_state = policy._live_stage_activation(configured, {"stage": 0})
    assert configured_state["active"] is True
    assert "maker_live_test_wallet_configured" in configured_state["reasons"]

    cfg = _config(tmp_path / "artifacts")
    assert policy._live_stage_activation(cfg, {"stage": 0})["active"] is False
    ladder = policy._live_stage_activation(cfg, {"stage": 1})
    assert "ladder_stage_permitted_above_zero" in ladder["reasons"]

    write_csv(
        cfg.output_root / policy.EXECUTION_LEDGER,
        [{"recorded_at_utc": "2026-07-14T10:00:00Z"}],
        fieldnames=["recorded_at_utc"],
    )
    ledger = policy._live_stage_activation(cfg, {"stage": 0})
    assert "live_execution_ledger_exists" in ledger["reasons"]

    write_json(
        cfg.output_root / policy.EXECUTOR_HEARTBEAT,
        {"heartbeat_at_utc": "2026-07-14T10:00:00Z"},
    )
    heartbeat = policy._live_stage_activation(cfg, {"stage": 0})
    assert "live_executor_heartbeat_exists" in heartbeat["reasons"]


def test_quote_sheet_patch_is_top_of_sheet_and_idempotent(tmp_path):
    cfg = _config(tmp_path)
    _write_study(cfg, _study(top="0xm1"))
    _write_carry_history(cfg, _history(["0xm1"] * 7))
    (_out(cfg) / "maker_quote_sheet.md").write_text("# Maker quote sheet\n\nExisting content\n", encoding="utf-8")

    run_decision_policy(cfg)
    run_decision_policy(cfg)

    sheet = (_out(cfg) / "maker_quote_sheet.md").read_text(encoding="utf-8")
    assert sheet.count("<!-- decision-policy:start -->") == 1
    assert "Registered decision policy (WO-50)" in sheet
    assert "fund_100_min_size_single_calmest_market" in sheet


def test_cli_and_dashboard_payload_surface_decision_policy(tmp_path):
    cfg = _config(tmp_path)
    _write_study(cfg, _study(top="0xm1"))
    _write_carry_history(cfg, _history(["0xm1"] * 7))
    result = run_decision_policy(cfg)

    rendered = render_dashboard(cfg)

    data = read_json(cfg.output_root / "polymarket_dashboard" / "dashboard_data.json")
    html = Path(rendered["dashboard_file"]).read_text(encoding="utf-8")
    assert "decision-policy" in COMMANDS
    assert data["maker_lane"]["decision_policy"]["indicated_action"] == result["indicated_action"]
    assert "policy: " in html
    assert "makerPolicy" in html
