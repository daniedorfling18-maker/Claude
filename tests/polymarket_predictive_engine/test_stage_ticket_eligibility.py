"""WO-99 stage-ticket eligibility and transition-only notification tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from polymarket_predictive_engine import stage_ticket_eligibility as mod
from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.utils import write_csv, write_json

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
CANDIDATE = "0xabc"


def _cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(raw={"paths": {"output_root": str(tmp_path / "outputs")}}, path=tmp_path / "c.yaml")


def _write_healthy_inputs(cfg: EngineConfig, **overrides) -> None:
    out = cfg.output_root / "maker_carry"
    policy = {
        "indicated_action": overrides.get("indicated_action", "fund_100_min_size_single_calmest_market"),
        "kill_criteria_status": {
            "status": overrides.get("kill", "clear"),
            "kill_input_freshness": {"state": overrides.get("freshness", "fresh")},
        },
        "composition_stability": {"most_recurrent_market": overrides.get("candidate", CANDIDATE)},
    }
    write_json(out / "decision_policy.json", policy)
    portfolio_row = {
        "condition_id": CANDIDATE,
        "question": "Calm test market?",
        "resolution_risk": overrides.get("resolution_risk", "medium"),
        "event_start_time_utc": overrides.get("event_start", ""),
        "size_multiple": overrides.get("size_multiple", 2),
        "capital_usd": overrides.get("capital_usd", 80.0),
    }
    write_json(out / "maker_carry_study.json", {"portfolio": [portfolio_row]})
    write_csv(
        out / "flow_toxicity.csv",
        [{"market": CANDIDATE, "toxicity_score": overrides.get("toxicity", 0.5)}],
    )
    write_json(
        cfg.output_root / "performance" / "wallet_reconciliation.json",
        {"reconciliation_status": overrides.get("reconciliation", "clean")},
    )


def test_all_conditions_pass_reads_eligible(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _write_healthy_inputs(cfg)
    state, rows, summary = mod.evaluate_stage_ticket_eligibility(cfg, as_of=NOW)
    assert state == "eligible"
    assert all(row["passed"] for row in rows)
    assert summary["minimum_quote_capital_usd"] == 40.0


def test_each_registered_condition_fails_closed(tmp_path: Path) -> None:
    cases = [
        {"indicated_action": "continue_study_until_policy_date"},
        {"kill": "triggered"},
        {"candidate": "0xother"},
        {"toxicity": 0.97},
        {"resolution_risk": "high"},
        {"event_start": (NOW + timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")},
        {"capital_usd": 420.0},
        {"freshness": "stale"},
        {"reconciliation": "DISCREPANCY"},
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


def test_notification_fires_only_on_transition(tmp_path: Path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)
    _write_healthy_inputs(cfg)
    sent: list[str] = []
    monkeypatch.setenv(mod.NTFY_ENV_VAR, "https://ntfy.sh/test-topic")
    monkeypatch.setattr(
        mod,
        "_notify",
        lambda url: sent.append(url) or {"attempted": True, "delivered": True},
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
    cfg = _cfg(tmp_path)
    _write_healthy_inputs(cfg)
    monkeypatch.delenv(mod.NTFY_ENV_VAR, raising=False)
    monkeypatch.setattr(mod, "_notify", lambda url: (_ for _ in ()).throw(AssertionError("must not send")))
    payload = mod.run_stage_ticket_eligibility(cfg, as_of=NOW)
    assert payload["notification"] == {"attempted": False, "channel_configured": False}


def test_send_failure_never_blocks_the_run(tmp_path: Path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)
    _write_healthy_inputs(cfg)
    monkeypatch.setenv(mod.NTFY_ENV_VAR, "https://ntfy.sh/test-topic")
    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    payload = mod.run_stage_ticket_eligibility(cfg, as_of=NOW)
    assert payload["state"] == "eligible"
    assert payload["notification"]["attempted"] is True
    assert payload["notification"]["delivered"] is False
