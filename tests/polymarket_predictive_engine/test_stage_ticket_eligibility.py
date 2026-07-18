"""WO-99 executable stage-ticket notification tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from polymarket_predictive_engine import stage_ticket_eligibility as stage_ticket
from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.maker_carry_study import _attach_portfolio_toxicity
from polymarket_predictive_engine.stage_ticket_eligibility import (
    NTFY_ENVIRONMENT_VARIABLE,
    NTFY_MESSAGE,
    OUTPUT_FILE,
    OWNER_ALERT_FILE,
    evaluate_stage_ticket_eligibility,
)
from polymarket_predictive_engine.utils import read_json, write_csv, write_json


STUDY_AT = "2026-07-18T08:00:00Z"
RUN_AT = "2026-07-18T12:00:00Z"


def _cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(
        raw={"paths": {"output_root": str(tmp_path / "outputs")}},
        path=tmp_path / "config.yaml",
    )


def _decision(
    generated_at: str = RUN_AT,
    *,
    study_generated_at: str = STUDY_AT,
    action: str = "fund_100_min_size_single_calmest_market",
    freshness: str = "fresh",
    kill_status: str = "clear",
) -> dict[str, Any]:
    return {
        "status": "ok",
        "generated_at_utc": generated_at,
        "inputs_snapshot": {
            "maker_carry_study_generated_at_utc": study_generated_at,
        },
        "indicated_action": action,
        "composition_stability": {"most_recurrent_market": "0xmarket"},
        "kill_criteria_status": {
            "status": kill_status,
            "kill_input_freshness": {"state": freshness},
        },
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }


def _study(*, study_generated_at: str = STUDY_AT, **overrides: Any) -> dict[str, Any]:
    candidate = {
        "condition_id": "0xmarket",
        "token_id": "YES",
        "question": "Will the objective event occur?",
        "resolution_risk": "low",
        "event_start_time_utc": "2026-07-21T12:00:00Z",
        "rewards_min_size_shares": 50.0,
        "mid_price": 0.5,
        "toxicity_score": 0.5,
        "toxicity_generated_at_utc": "2026-07-18T11:00:00Z",
    }
    candidate.update(overrides)
    return {
        "status": "ok",
        "generated_at_utc": study_generated_at,
        "portfolio": [candidate],
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }


def _reconciliation(
    cfg: EngineConfig,
    *,
    state: str = "clean",
    generated_at: str = "2026-07-18T11:30:00Z",
) -> None:
    write_json(
        cfg.output_root / "performance" / "wallet_reconciliation.json",
        {
            "status": "ok",
            "reconciliation_status": state,
            "generated_at_utc": generated_at,
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )


def _prior_not_eligible(cfg: EngineConfig) -> None:
    write_json(
        cfg.output_root / OUTPUT_FILE,
        {"state": "not_eligible", "generated_at_utc": "2026-07-18T11:00:00Z"},
    )


def test_all_registered_conditions_pass_and_auditable_alert_is_non_sensitive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(tmp_path)
    _reconciliation(cfg)
    _prior_not_eligible(cfg)
    monkeypatch.delenv(NTFY_ENVIRONMENT_VARIABLE, raising=False)

    result = evaluate_stage_ticket_eligibility(
        cfg,
        decision_policy=_decision(),
        study=_study(),
        as_of=RUN_AT,
    )

    assert result["state"] == "eligible"
    assert result["first_failing_condition"] is None
    assert all(row["passed"] for row in result["conditions"].values())
    assert result["notification"]["delivery_state"] == "dashboard_only_env_unset"
    assert result["owner_alert_written"] is True
    alert = read_json(cfg.output_root / OWNER_ALERT_FILE)
    assert alert["message"] == NTFY_MESSAGE
    assert "0xmarket" not in str(alert)
    assert "$100" not in str(alert)
    assert alert["paper_trading_invoked"] is False
    assert alert["live_trading_invoked"] is False


def test_ntfy_fires_exactly_once_on_transition_and_never_persists_topic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(tmp_path)
    _reconciliation(cfg)
    _prior_not_eligible(cfg)
    private_topic = "https://ntfy.example/private-owner-topic"
    monkeypatch.setenv(NTFY_ENVIRONMENT_VARIABLE, private_topic)
    calls: list[tuple[str, str, float]] = []

    def sender(url: str, message: str, timeout: float) -> int:
        calls.append((url, message, timeout))
        return 200

    first = evaluate_stage_ticket_eligibility(
        cfg,
        decision_policy=_decision(),
        study=_study(),
        as_of=RUN_AT,
        ntfy_sender=sender,
    )
    second = evaluate_stage_ticket_eligibility(
        cfg,
        decision_policy=_decision(),
        study=_study(),
        as_of=RUN_AT,
        ntfy_sender=sender,
    )

    assert calls == [(private_topic, NTFY_MESSAGE, 10.0)]
    assert first["notification"]["delivery_state"] == "delivered"
    assert second["notification"]["delivery_state"] == "unchanged_no_push"
    persisted_text = (cfg.output_root / OUTPUT_FILE).read_text(encoding="utf-8")
    alert_text = (cfg.output_root / OWNER_ALERT_FILE).read_text(encoding="utf-8")
    assert private_topic not in persisted_text
    assert private_topic not in alert_text


def test_ntfy_failure_is_non_blocking_and_exception_text_is_not_persisted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(tmp_path)
    _reconciliation(cfg)
    _prior_not_eligible(cfg)
    private_topic = "https://ntfy.example/do-not-leak"
    monkeypatch.setenv(NTFY_ENVIRONMENT_VARIABLE, private_topic)

    def failing_sender(url: str, message: str, timeout: float) -> int:
        raise RuntimeError(f"transport failed for {url}")

    result = evaluate_stage_ticket_eligibility(
        cfg,
        decision_policy=_decision(),
        study=_study(),
        as_of=RUN_AT,
        ntfy_sender=failing_sender,
    )

    assert result["state"] == "eligible"
    assert result["notification"]["delivery_state"] == "failed_non_blocking"
    assert result["notification"]["error_type"] == "RuntimeError"
    assert private_topic not in (cfg.output_root / OUTPUT_FILE).read_text(encoding="utf-8")


def test_ntfy_http_contract_posts_only_the_fixed_plain_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}

    class Response:
        status = 204

        def getcode(self) -> int:
            return self.status

        def __enter__(self):
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

    def fake_urlopen(request, timeout: float):
        observed.update(
            {
                "url": request.full_url,
                "method": request.get_method(),
                "data": request.data,
                "content_type": request.headers.get("Content-type"),
                "timeout": timeout,
            }
        )
        return Response()

    monkeypatch.setattr(stage_ticket.urllib.request, "urlopen", fake_urlopen)

    status = stage_ticket._post_ntfy(
        "https://ntfy.example/private-topic",
        NTFY_MESSAGE,
        10.0,
    )

    assert status == 204
    assert observed == {
        "url": "https://ntfy.example/private-topic",
        "method": "POST",
        "data": NTFY_MESSAGE.encode("utf-8"),
        "content_type": "text/plain; charset=utf-8",
        "timeout": 10.0,
    }


def test_exact_48_hour_guard_and_clock_advance_are_fail_closed(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _reconciliation(cfg)

    exact = evaluate_stage_ticket_eligibility(
        cfg,
        decision_policy=_decision(),
        study=_study(event_start_time_utc="2026-07-20T12:00:00Z"),
        as_of=RUN_AT,
    )
    outside = evaluate_stage_ticket_eligibility(
        cfg,
        decision_policy=_decision(),
        study=_study(event_start_time_utc="2026-07-20T12:00:01Z"),
        as_of=RUN_AT,
    )
    advanced_run = "2026-07-18T12:00:02Z"
    advanced = evaluate_stage_ticket_eligibility(
        cfg,
        decision_policy=_decision(advanced_run),
        study=_study(event_start_time_utc="2026-07-20T12:00:01Z"),
        as_of=advanced_run,
    )

    condition = "current_candidate_risk_controls"
    assert exact["state"] == "not_eligible"
    assert exact["conditions"][condition]["hours_until_scheduled_time"] == 48.0
    assert outside["state"] == "eligible"
    assert advanced["state"] == "not_eligible"
    assert advanced["conditions"][condition]["outside_48h_guard"] is False


@pytest.mark.parametrize(
    ("case", "decision_changes", "candidate_changes", "reconciliation_state", "first_failure"),
    [
        (
            "not_a_funding_action",
            {"action": "continue_study_until_policy_date"},
            {},
            "clean",
            "funding_action_and_kill_clear",
        ),
        (
            "kill_input_not_fresh",
            {"freshness": "stale"},
            {},
            "clean",
            "fresh_kill_inputs_and_reconciliation",
        ),
        (
            "toxicity_missing",
            {},
            {"toxicity_score": None, "toxicity_generated_at_utc": None},
            "clean",
            "current_candidate_risk_controls",
        ),
        (
            "resolution_high",
            {},
            {"resolution_risk": "high"},
            "clean",
            "current_candidate_risk_controls",
        ),
        (
            "scheduled_time_missing",
            {},
            {"event_start_time_utc": "", "end_date_utc": ""},
            "clean",
            "current_candidate_risk_controls",
        ),
        (
            "capital_above_stage",
            {},
            {"rewards_min_size_shares": 101.0, "mid_price": 0.5},
            "clean",
            "minimum_quote_capital_within_stage",
        ),
        (
            "malformed_declared_minimum_does_not_use_fallback",
            {},
            {
                "rewards_min_size_shares": "bad",
                "quote_size_shares": 50.0,
                "size_multiple": 1,
            },
            "clean",
            "minimum_quote_capital_within_stage",
        ),
        (
            "malformed_declared_mid_does_not_use_fallback",
            {},
            {"mid_price": "bad", "reference_mid_price": 0.5},
            "clean",
            "minimum_quote_capital_within_stage",
        ),
        (
            "reconciliation_unclean",
            {},
            {},
            "DISCREPANCY",
            "fresh_kill_inputs_and_reconciliation",
        ),
    ],
)
def test_missing_malformed_or_unsafe_inputs_never_become_eligible(
    tmp_path: Path,
    case: str,
    decision_changes: dict[str, Any],
    candidate_changes: dict[str, Any],
    reconciliation_state: str,
    first_failure: str,
) -> None:
    cfg = _cfg(tmp_path / case)
    _reconciliation(cfg, state=reconciliation_state)

    result = evaluate_stage_ticket_eligibility(
        cfg,
        decision_policy=_decision(**decision_changes),
        study=_study(**candidate_changes),
        as_of=RUN_AT,
    )

    assert result["state"] == "not_eligible"
    assert result["first_failing_condition"] == first_failure
    assert result["paper_trading_invoked"] is False
    assert result["live_trading_invoked"] is False
    assert result["order_placement_invoked"] is False


@pytest.mark.parametrize(
    ("stale_input", "first_failure"),
    [
        ("study", "current_candidate_risk_controls"),
        ("toxicity", "current_candidate_risk_controls"),
        ("reconciliation", "fresh_kill_inputs_and_reconciliation"),
    ],
)
def test_prior_utc_day_inputs_are_stale_and_not_eligible(
    tmp_path: Path,
    stale_input: str,
    first_failure: str,
) -> None:
    cfg = _cfg(tmp_path / stale_input)
    prior_day = "2026-07-17T23:59:59Z"
    _reconciliation(
        cfg,
        generated_at=(prior_day if stale_input == "reconciliation" else "2026-07-18T11:30:00Z"),
    )
    study_at = prior_day if stale_input == "study" else STUDY_AT
    toxicity_at = prior_day if stale_input == "toxicity" else "2026-07-18T11:00:00Z"

    result = evaluate_stage_ticket_eligibility(
        cfg,
        decision_policy=_decision(study_generated_at=study_at),
        study=_study(
            study_generated_at=study_at,
            toxicity_generated_at_utc=toxicity_at,
        ),
        as_of=RUN_AT,
    )

    assert result["state"] == "not_eligible"
    assert result["first_failing_condition"] == first_failure


def test_malformed_declared_toxicity_cannot_fall_back_to_valid_csv(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _reconciliation(cfg)
    write_csv(
        cfg.output_root / "maker_carry" / "flow_toxicity.csv",
        [
            {
                "generated_at_utc": "2026-07-18T11:00:00Z",
                "market": "0xmarket",
                "asset_id": "YES",
                "toxicity_score": 0.1,
            }
        ],
        fieldnames=["generated_at_utc", "market", "asset_id", "toxicity_score"],
    )

    result = evaluate_stage_ticket_eligibility(
        cfg,
        decision_policy=_decision(),
        study=_study(
            toxicity_score="malformed",
            toxicity_generated_at_utc="2026-07-18T11:00:00Z",
        ),
        as_of=RUN_AT,
    )

    risk = result["conditions"]["current_candidate_risk_controls"]
    assert risk["toxicity_available"] is False
    assert risk["toxicity_score"] is None
    assert result["state"] == "not_eligible"


def test_first_eligible_observation_without_prior_not_eligible_does_not_push(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _cfg(tmp_path)
    _reconciliation(cfg)
    monkeypatch.setenv(NTFY_ENVIRONMENT_VARIABLE, "https://ntfy.example/topic")
    calls: list[str] = []

    result = evaluate_stage_ticket_eligibility(
        cfg,
        decision_policy=_decision(),
        study=_study(),
        as_of=RUN_AT,
        ntfy_sender=lambda _url, message, _timeout: calls.append(message) or 200,
    )

    assert result["state"] == "eligible"
    assert result["previous_state"] == "missing"
    assert result["state_transition"] == "none"
    assert calls == []


def test_maker_study_snapshots_measured_toxicity_without_manufacturing_coverage(
    tmp_path: Path,
) -> None:
    out_root = tmp_path / "outputs" / "maker_carry"
    write_csv(
        out_root / "flow_toxicity.csv",
        [
            {
                "generated_at_utc": "2026-07-18T10:00:00Z",
                "market": "0xmeasured",
                "asset_id": "YES",
                "toxicity_score": 0.42,
            }
        ],
        fieldnames=["generated_at_utc", "market", "asset_id", "toxicity_score"],
    )
    portfolio = [
        {"condition_id": "0xmeasured", "token_id": "YES"},
        {"condition_id": "0xunmeasured", "token_id": "NO"},
    ]

    _attach_portfolio_toxicity(out_root, portfolio)

    assert portfolio[0]["toxicity_score"] == 0.42
    assert portfolio[0]["toxicity_generated_at_utc"] == "2026-07-18T10:00:00Z"
    assert "toxicity_score" not in portfolio[1]


def test_duplicate_current_toxicity_rows_use_conservative_maximum(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _reconciliation(cfg)
    write_csv(
        cfg.output_root / "maker_carry" / "flow_toxicity.csv",
        [
            {
                "generated_at_utc": "2026-07-18T11:00:00Z",
                "market": "0xmarket",
                "asset_id": "YES",
                "toxicity_score": 0.4,
            },
            {
                "generated_at_utc": "2026-07-18T11:00:00Z",
                "market": "0xmarket",
                "asset_id": "NO",
                "toxicity_score": 0.95,
            },
        ],
        fieldnames=["generated_at_utc", "market", "asset_id", "toxicity_score"],
    )

    result = evaluate_stage_ticket_eligibility(
        cfg,
        decision_policy=_decision(),
        study=_study(toxicity_score=None, toxicity_generated_at_utc=None),
        as_of=RUN_AT,
    )

    risk = result["conditions"]["current_candidate_risk_controls"]
    assert risk["toxicity_score"] == 0.95
    assert risk["toxicity_within_limit"] is False
    assert result["state"] == "not_eligible"
