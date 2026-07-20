"""WO-99 stage-ticket eligibility and transition-only notification tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from polymarket_predictive_engine import stage_ticket_eligibility as mod
from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.utils import write_csv, write_json

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
CANDIDATE = "0xabc"
TOKEN = "0xtoken"


def _cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(raw={"paths": {"output_root": str(tmp_path / "outputs")}}, path=tmp_path / "c.yaml")


def _write_healthy_inputs(cfg: EngineConfig, **overrides) -> None:
    out = cfg.output_root / "maker_carry"
    run_stamp = overrides.get("run_stamp", "2026-07-17T11:45:00Z")
    policy_stamp = overrides.get("policy_stamp", "2026-07-17T11:46:00Z")
    policy = {
        "generated_at_utc": policy_stamp,
        "indicated_action": overrides.get("indicated_action", "fund_100_min_size_single_calmest_market"),
        "kill_criteria_status": {
            "status": overrides.get("kill", "clear"),
            "kill_input_freshness": {"state": overrides.get("freshness", "fresh")},
        },
        "composition_stability": {"most_recurrent_market": overrides.get("candidate", CANDIDATE)},
        "inputs_snapshot": {"maker_carry_study_generated_at_utc": overrides.get("policy_study_ref", run_stamp)},
    }
    write_json(out / "decision_policy.json", policy)
    portfolio_row = {
        "condition_id": CANDIDATE,
        "token_id": overrides.get("token", TOKEN),
        "question": "Calm test market?",
        "resolution_risk": overrides.get("resolution_risk", "medium"),
        "event_start_time_utc": overrides.get("event_start", ""),
        "size_multiple": overrides.get("size_multiple", 2),
        "capital_usd": overrides.get("capital_usd", 80.0),
    }
    portfolio = overrides.get("portfolio", [portfolio_row])
    write_json(out / "maker_carry_study.json", {"generated_at_utc": run_stamp, "portfolio": portfolio})
    toxicity = overrides.get("toxicity", 0.5)
    raw_imbalance = overrides.get("raw_imbalance", 0.4)
    blocked = toxicity > 0.9 or (raw_imbalance is not None and raw_imbalance >= 0.9)
    write_csv(
        out / "flow_toxicity.csv",
        [{
            "generated_at_utc": overrides.get("toxicity_stamp", run_stamp),
            "market": CANDIDATE,
            "toxicity_score": toxicity,
            "vpin_raw": "" if raw_imbalance is None else raw_imbalance,
            "toxic_blocked": blocked,
        }],
    )
    write_json(
        cfg.output_root / "performance" / "wallet_reconciliation.json",
        {
            "generated_at_utc": overrides.get("reconciliation_stamp", run_stamp),
            "reconciliation_status": overrides.get("reconciliation", "clean"),
        },
    )
    write_json(
        out / "requote_alerts.json",
        {
            "generated_at_utc": overrides.get("requote_stamp", policy_stamp),
            "markets": [{"condition_id": overrides.get("candidate", CANDIDATE),
                         "alert_state": overrides.get("requote_state", "quotes_ok")}],
        },
    )
    # WO-105 sharp-linking qualification: healthy path publishes a fresh,
    # qualified artifact bound to the current study + replay versions. Overrides
    # let a test make it unqualified / stale / mismatched / version-drifted to
    # exercise the fail-closed condition.
    replay_stamp = overrides.get("replay_stamp", run_stamp)
    write_json(out / "maker_fill_replay.json", {"generated_at_utc": replay_stamp})
    write_json(
        out / "sharp_linking_qualification.json",
        {
            "qualified": overrides.get("sharp_qualified", True),
            "candidate_condition_id": overrides.get("qual_candidate", overrides.get("candidate", CANDIDATE)),
            "candidate_token_id": overrides.get("qual_token", overrides.get("token", TOKEN)),
            "generated_at_utc": overrides.get("qual_generated", run_stamp),
            "consumed_policy_generated_at": overrides.get("qual_policy", policy_stamp),
            "consumed_study_generated_at": overrides.get("qual_study", run_stamp),
            "consumed_replay_generated_at": overrides.get("qual_replay", replay_stamp),
            "first_failing_check": overrides.get("qual_first_failing"),
        },
    )


def test_all_conditions_pass_reads_eligible(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mod, "FUNDING_GOVERNANCE_RECONCILED", True)
    cfg = _cfg(tmp_path)
    _write_healthy_inputs(cfg)
    state, rows, summary = mod.evaluate_stage_ticket_eligibility(cfg, as_of=NOW)
    assert state == "eligible"
    assert all(row["passed"] for row in rows)
    assert summary["minimum_quote_capital_usd"] == 40.0
    assert summary["candidate_token_id"] == TOKEN


def test_each_registered_condition_fails_closed(tmp_path: Path) -> None:
    cases = [
        {"indicated_action": "continue_study_until_policy_date"},
        {"kill": "triggered"},
        {"candidate": "0xother"},
        {"toxicity": 0.97},
        {"raw_imbalance": 0.977},
        {"resolution_risk": "high"},
        {"event_start": (NOW + timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")},
        {"capital_usd": 420.0},
        {"freshness": "stale"},
        {"reconciliation": "DISCREPANCY"},
        {"requote_state": "pull_quotes_now"},
        {"sharp_qualified": False},
        {"qual_generated": "2026-07-17T10:00:00Z"},  # stale qualification (>30m)
        {"qual_candidate": "0xmismatch"},  # qualification about a different market
        {"qual_token": "0xother-token"},  # qualification about the other outcome token
        {"qual_policy": "2026-07-17T09:00:00Z"},  # qualification bound to an older policy version
        {"qual_study": "2026-07-17T09:00:00Z"},  # qualification bound to an older study version
        {"qual_replay": "2026-07-17T09:00:00Z"},  # qualification bound to an older replay version
        {"raw_imbalance": None},  # missing vpin_raw -> toxicity screen fails closed
        {"raw_imbalance": float("-inf")},  # P3-4: non-finite raw-imbalance fails closed
        {"toxicity": float("-inf")},  # P3-4: non-finite toxicity percentile fails closed
    ]
    for index, overrides in enumerate(cases):
        cfg = _cfg(tmp_path / f"case{index}")
        _write_healthy_inputs(cfg, **overrides)
        state, rows, _ = mod.evaluate_stage_ticket_eligibility(cfg, as_of=NOW)
        assert state == "not_eligible", overrides
        assert any(not row["passed"] for row in rows)


def test_missing_artifacts_evaluate_not_eligible(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    state, _, _ = mod.evaluate_stage_ticket_eligibility(cfg, as_of=NOW)
    assert state == "not_eligible"


def test_every_consumed_source_artifact_has_an_independent_staleness_block(tmp_path: Path) -> None:
    stale_fast = "2026-07-17T11:29:59Z"
    stale_daily = "2026-07-16T09:59:59Z"
    fresh_study = "2026-07-17T11:45:00Z"
    fresh_policy = "2026-07-17T11:46:00Z"
    cases = [
        (
            "decision_policy_source_fresh",
            {"policy_stamp": stale_fast, "qual_policy": stale_fast},
        ),
        (
            "maker_carry_study_source_fresh",
            {
                "run_stamp": stale_daily,
                "policy_study_ref": stale_daily,
                "toxicity_stamp": fresh_study,
                "reconciliation_stamp": fresh_study,
                "replay_stamp": fresh_study,
                "qual_generated": fresh_study,
                "qual_study": stale_daily,
                "qual_replay": fresh_study,
            },
        ),
        ("candidate_flow_toxicity_source_fresh", {"toxicity_stamp": stale_daily}),
        ("wallet_reconciliation_source_fresh", {"reconciliation_stamp": stale_daily}),
        ("requote_alerts_source_fresh", {"requote_stamp": stale_fast}),
        (
            "maker_fill_replay_source_fresh",
            {"replay_stamp": stale_fast, "qual_replay": stale_fast},
        ),
        (
            "sharp_linking_qualification_source_fresh",
            {"qual_generated": stale_fast},
        ),
    ]

    for index, (condition_name, overrides) in enumerate(cases):
        cfg = _cfg(tmp_path / f"stale{index}")
        _write_healthy_inputs(
            cfg,
            policy_stamp=overrides.get("policy_stamp", fresh_policy),
            **{key: value for key, value in overrides.items() if key != "policy_stamp"},
        )

        state, rows, summary = mod.evaluate_stage_ticket_eligibility(cfg, as_of=NOW)

        condition = next(row for row in rows if row["condition"] == condition_name)
        assert state == "not_eligible", condition_name
        assert condition["passed"] is False
        source_name = condition_name.removesuffix("_source_fresh")
        assert summary["source_freshness"][source_name]["fresh"] is False


def test_source_age_boundary_is_inclusive_then_fails_one_second_late(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    boundary = "2026-07-17T11:30:00Z"
    _write_healthy_inputs(cfg, policy_stamp=boundary, qual_policy=boundary)

    state, rows, _ = mod.evaluate_stage_ticket_eligibility(cfg, as_of=NOW)

    condition = next(row for row in rows if row["condition"] == "decision_policy_source_fresh")
    assert state == "eligible"
    assert condition["passed"] is True
    assert "age=1800.0s" in condition["detail"]

    stale = "2026-07-17T11:29:59Z"
    _write_healthy_inputs(cfg, policy_stamp=stale, qual_policy=stale)
    state, rows, _ = mod.evaluate_stage_ticket_eligibility(cfg, as_of=NOW)
    condition = next(row for row in rows if row["condition"] == "decision_policy_source_fresh")
    assert state == "not_eligible"
    assert condition["passed"] is False


def test_malformed_or_future_source_timestamp_fails_closed(tmp_path: Path) -> None:
    malformed_cfg = _cfg(tmp_path / "malformed")
    _write_healthy_inputs(malformed_cfg, policy_stamp="not-a-timestamp", qual_policy="not-a-timestamp")
    state, rows, _ = mod.evaluate_stage_ticket_eligibility(malformed_cfg, as_of=NOW)
    policy = next(row for row in rows if row["condition"] == "decision_policy_source_fresh")
    assert state == "not_eligible"
    assert policy["passed"] is False
    assert "age=missing" in policy["detail"]

    future_cfg = _cfg(tmp_path / "future")
    _write_healthy_inputs(future_cfg, requote_stamp="2026-07-17T12:00:01Z")
    state, rows, _ = mod.evaluate_stage_ticket_eligibility(future_cfg, as_of=NOW)
    requote = next(row for row in rows if row["condition"] == "requote_alerts_source_fresh")
    assert state == "not_eligible"
    assert requote["passed"] is False
    assert "age=-1.0s" in requote["detail"]


def test_notification_fires_only_on_transition(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mod, "FUNDING_GOVERNANCE_RECONCILED", True)
    cfg = _cfg(tmp_path)
    _write_healthy_inputs(cfg)
    sent: list[str] = []
    monkeypatch.setenv(mod.NTFY_ENV_VAR, "https://ntfy.sh/test-topic")
    monkeypatch.setattr(
        mod,
        "_notify",
        lambda url, message: sent.append(message) or {"attempted": True, "delivered": True},
    )
    first = mod.run_stage_ticket_eligibility(cfg, as_of=NOW)
    assert first["state"] == "eligible" and first["transitioned_to_eligible"] is True
    assert len(sent) == 1
    assert (cfg.output_root / mod.ALERT_RELATIVE).exists()

    second = mod.run_stage_ticket_eligibility(cfg, as_of=NOW)
    assert second["state"] == "eligible" and second["transitioned_to_eligible"] is False
    assert len(sent) == 1  # no repeat while state unchanged
    assert second["paper_trading_invoked"] is False
    assert second["live_trading_invoked"] is False


def test_unset_env_never_attempts_send(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mod, "FUNDING_GOVERNANCE_RECONCILED", True)
    cfg = _cfg(tmp_path)
    _write_healthy_inputs(cfg)
    monkeypatch.delenv(mod.NTFY_ENV_VAR, raising=False)
    monkeypatch.setattr(mod, "_notify", lambda url, message: (_ for _ in ()).throw(AssertionError("must not send")))
    payload = mod.run_stage_ticket_eligibility(cfg, as_of=NOW)
    assert payload["notification"] == {"attempted": False, "channel_configured": False}


def test_send_failure_never_blocks_the_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mod, "FUNDING_GOVERNANCE_RECONCILED", True)
    cfg = _cfg(tmp_path)
    _write_healthy_inputs(cfg)
    monkeypatch.setenv(mod.NTFY_ENV_VAR, "https://ntfy.sh/test-topic")
    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    payload = mod.run_stage_ticket_eligibility(cfg, as_of=NOW)
    assert payload["state"] == "eligible"
    assert payload["notification"]["attempted"] is True
    assert payload["notification"]["delivered"] is False


def test_governance_contradiction_fails_closed(tmp_path: Path, monkeypatch) -> None:
    # With the registry/WO-93 contradiction unresolved, a fully healthy ticket
    # still reads not_eligible on the governance condition alone.
    monkeypatch.setattr(mod, "FUNDING_GOVERNANCE_RECONCILED", False)
    cfg = _cfg(tmp_path)
    _write_healthy_inputs(cfg)
    state, rows, _ = mod.evaluate_stage_ticket_eligibility(cfg, as_of=NOW)
    assert state == "not_eligible"
    gov = next(r for r in rows if r["condition"] == "funding_governance_reconciled")
    assert gov["passed"] is False


def test_revocation_notification_on_eligible_to_not_eligible(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mod, "FUNDING_GOVERNANCE_RECONCILED", True)
    cfg = _cfg(tmp_path)
    _write_healthy_inputs(cfg)
    sent: list[str] = []
    monkeypatch.setenv(mod.NTFY_ENV_VAR, "https://ntfy.sh/test-topic")
    monkeypatch.setattr(mod, "_notify", lambda url, message: sent.append(message) or {"attempted": True, "delivered": True})
    first = mod.run_stage_ticket_eligibility(cfg, as_of=NOW)
    assert first["state"] == "eligible" and sent[-1] == mod.NTFY_MESSAGE
    # Make the candidate toxic -> not_eligible; a revocation must fire once.
    _write_healthy_inputs(cfg, toxicity=0.97)
    second = mod.run_stage_ticket_eligibility(cfg, as_of=NOW)
    assert second["state"] == "not_eligible" and second["revoked"] is True
    assert sent[-1] == mod.NTFY_REVOKE_MESSAGE
    assert second["notification"]["kind"] == "revocation"


def test_candidate_change_while_eligible_renotifies(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mod, "FUNDING_GOVERNANCE_RECONCILED", True)
    cfg = _cfg(tmp_path)
    _write_healthy_inputs(cfg)
    sent: list[str] = []
    monkeypatch.setenv(mod.NTFY_ENV_VAR, "https://ntfy.sh/test-topic")
    monkeypatch.setattr(mod, "_notify", lambda url, message: sent.append(message) or {"attempted": True, "delivered": True})
    mod.run_stage_ticket_eligibility(cfg, as_of=NOW)
    assert len(sent) == 1
    # Swap the eligible candidate to a different market; still eligible, but a
    # new eligible notice must fire (keyed on candidate, not just state).
    out = cfg.output_root / "maker_carry"
    write_json(out / "decision_policy.json", {
        "generated_at_utc": "2026-07-17T11:56:00Z",
        "indicated_action": "fund_100_min_size_single_calmest_market",
        "kill_criteria_status": {"status": "clear", "kill_input_freshness": {"state": "fresh"}},
        "composition_stability": {"most_recurrent_market": "0xdef"},
        "inputs_snapshot": {"maker_carry_study_generated_at_utc": "2026-07-17T11:55:00Z"},
    })
    write_json(out / "maker_carry_study.json", {"generated_at_utc": "2026-07-17T11:55:00Z", "portfolio": [{
        "condition_id": "0xdef", "token_id": "0xdef-token", "question": "Other calm market?", "resolution_risk": "medium",
        "event_start_time_utc": "", "size_multiple": 2, "capital_usd": 80.0}]})
    write_csv(out / "flow_toxicity.csv", [{"generated_at_utc": "2026-07-17T11:55:00Z", "market": "0xdef", "toxicity_score": 0.5, "vpin_raw": 0.4, "toxic_blocked": False}])
    write_json(out / "requote_alerts.json", {"generated_at_utc": "2026-07-17T11:56:00Z", "markets": [{"condition_id": "0xdef", "alert_state": "quotes_ok"}]})
    write_json(out / "maker_fill_replay.json", {"generated_at_utc": "2026-07-17T11:55:00Z"})
    write_json(out / "sharp_linking_qualification.json", {
        "qualified": True, "candidate_condition_id": "0xdef", "candidate_token_id": "0xdef-token",
        "generated_at_utc": "2026-07-17T11:56:00Z",
        "consumed_policy_generated_at": "2026-07-17T11:56:00Z",
        "consumed_study_generated_at": "2026-07-17T11:55:00Z", "consumed_replay_generated_at": "2026-07-17T11:55:00Z"})
    third = mod.run_stage_ticket_eligibility(cfg, as_of=NOW)
    assert third["state"] == "eligible" and third["candidate_changed_while_eligible"] is True
    assert len(sent) == 2  # re-notified on the candidate change


def test_duplicate_condition_rows_fail_closed_even_when_one_token_matches(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    duplicate = {
        "condition_id": CANDIDATE,
        "token_id": "0xother-token",
        "question": "Other outcome?",
        "resolution_risk": "medium",
        "event_start_time_utc": "",
        "size_multiple": 2,
        "capital_usd": 80.0,
    }
    _write_healthy_inputs(
        cfg,
        portfolio=[
            {
                "condition_id": CANDIDATE,
                "token_id": TOKEN,
                "question": "Calm test market?",
                "resolution_risk": "medium",
                "event_start_time_utc": "",
                "size_multiple": 2,
                "capital_usd": 80.0,
            },
            duplicate,
        ],
    )

    state, rows, _ = mod.evaluate_stage_ticket_eligibility(cfg, as_of=NOW)

    assert state == "not_eligible"
    identity = next(row for row in rows if row["condition"] == "candidate_in_current_portfolio")
    assert identity["passed"] is False
    assert "portfolio_matches=2" in identity["detail"]


def test_same_condition_new_token_renotifies(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mod, "FUNDING_GOVERNANCE_RECONCILED", True)
    cfg = _cfg(tmp_path)
    _write_healthy_inputs(cfg)
    sent: list[str] = []
    monkeypatch.setenv(mod.NTFY_ENV_VAR, "https://ntfy.sh/test-topic")
    monkeypatch.setattr(mod, "_notify", lambda url, message: sent.append(message) or {"attempted": True, "delivered": True})
    first = mod.run_stage_ticket_eligibility(cfg, as_of=NOW)
    assert first["candidate_token_id"] == TOKEN

    _write_healthy_inputs(cfg, token="0xreplacement-token")
    second = mod.run_stage_ticket_eligibility(cfg, as_of=NOW)

    assert second["state"] == "eligible"
    assert second["candidate_changed_while_eligible"] is True
    assert second["previous_candidate_token_id"] == TOKEN
    assert second["candidate_token_id"] == "0xreplacement-token"
    assert len(sent) == 2


def test_stale_policy_vs_study_run_fails_closed(tmp_path: Path, monkeypatch) -> None:
    # Policy consumed an older study run than the current study on disk (the
    # ~18-min churn skew) -> not_eligible on the coherence condition.
    monkeypatch.setattr(mod, "FUNDING_GOVERNANCE_RECONCILED", True)
    cfg = _cfg(tmp_path)
    _write_healthy_inputs(cfg, policy_study_ref="2026-07-17T11:15:00Z", run_stamp="2026-07-17T11:45:00Z")
    state, rows, _ = mod.evaluate_stage_ticket_eligibility(cfg, as_of=NOW)
    assert state == "not_eligible"
    coh = next(r for r in rows if r["condition"] == "policy_and_study_same_run")
    assert coh["passed"] is False
