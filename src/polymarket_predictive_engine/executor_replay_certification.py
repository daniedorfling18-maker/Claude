"""WO-74 executor replay-certification harness.

This module certifies a candidate *decision log* against an immutable scenario
contract.  It is deliberately outside any executor: it has no venue client,
credential loader, broker, signer, or order method.  The reference stub exists
only to prove the pre-amendment harness and is never canary authorisation.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .config import EngineConfig
from .utils import now_utc, safe_float, safe_int, write_csv, write_json


WORK_ORDER = "WO-74"
DEFAULT_HEARTBEAT_GAP_SECONDS = 1800
SIZE_MULTIPLE = 5
REQUIRED_SYNTHETIC_KINDS = (
    "event_start_book_clear",
    "five_share_minimum",
    "sub_five_tail",
    "spread_crossing",
    "news_gap_through_quote",
    "stale_websocket",
    "heartbeat_gap",
    "kill_criteria_day",
)
LEDGER_FIELDS = (
    "candidate_build_id",
    "scenario_id",
    "cycle",
    "action_id",
    "action_type",
    "condition_id",
    "token_id",
    "side",
    "price",
    "shares",
    "recorded_at_utc",
)
CONTRACT_RULES = (
    "scenario_coverage",
    "quote_sheet_boundary",
    "policy_caps",
    "five_share_multiple",
    "stop_cancellation",
    "flat_on_missing_or_stale_input",
    "dead_man_flatten",
    "action_ledger_completeness",
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = _mapping(cfg.raw.get("executor_replay_certification"))
    decision = _mapping(cfg.raw.get("decision_policy"))
    configured_gap = safe_int(raw.get("heartbeat_gap_seconds"))
    heartbeat_gap = min(DEFAULT_HEARTBEAT_GAP_SECONDS, configured_gap or DEFAULT_HEARTBEAT_GAP_SECONDS)
    stage_cap = safe_float(decision.get("stage0_capital_usd")) or 100.0
    current_policy = _mapping(_read_json(cfg.output_root / "maker_carry" / "decision_policy.json"))
    binding_cap = safe_float(_mapping(current_policy.get("sizing")).get("binding_capital_usd"))
    if binding_cap is not None and binding_cap > 0:
        stage_cap = min(stage_cap, binding_cap)
    return {
        "enabled": bool(raw.get("enabled", True)),
        "heartbeat_gap_seconds": max(1, heartbeat_gap),
        "max_official_scenarios": max(1, min(50, safe_int(raw.get("max_official_scenarios")) or 12)),
        "stage_cap_usd": round(max(0.01, stage_cap), 4),
        "scenario_corpus_file": str(raw.get("scenario_corpus_file") or "execution/replay_scenario_corpus.json"),
        "decision_log_file": str(raw.get("decision_log_file") or "execution/replay_candidate_decisions.json"),
        "execution_ledger_file": str(raw.get("execution_ledger_file") or "execution/replay_candidate_ledger.csv"),
        "certification_file": str(raw.get("certification_file") or "execution/replay_certification.json"),
    }


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _state(*, open_orders: int = 0, position_shares: float = 0.0, price: float = 0.5) -> dict[str, Any]:
    exposure = abs(position_shares) * price
    return {
        "open_orders": int(open_orders),
        "position_shares": round(float(position_shares), 6),
        "exposure_usd": round(exposure, 6),
        "open_order_notional_usd": round(open_orders * SIZE_MULTIPLE * price, 6),
        "capital_at_risk_usd": round(exposure + open_orders * SIZE_MULTIPLE * price, 6),
    }


def _ticket(condition_id: str, token_id: str, *, bid: float, ask: float, max_shares: int = 10) -> dict[str, Any]:
    return {
        "condition_id": condition_id,
        "token_id": token_id,
        "side": "BUY",
        "price": round(float(bid), 6),
        "best_bid": round(float(bid), 6),
        "best_ask": round(float(ask), 6),
        "tick_size": 0.01,
        "max_shares": int(max_shares),
    }


def _cycle(
    scenario_id: str,
    kind: str,
    *,
    condition_id: str,
    token_id: str,
    bid: float = 0.49,
    ask: float = 0.51,
    max_shares: int = 10,
    input_status: str = "fresh",
    control_state: str = "quotes_ok",
    heartbeat_age_seconds: int = 0,
    kill_triggered: bool = False,
    starting_state: Mapping[str, Any] | None = None,
    stage_cap_usd: float,
) -> dict[str, Any]:
    quoting_permitted = (
        input_status == "fresh"
        and control_state == "quotes_ok"
        and not kill_triggered
        and heartbeat_age_seconds <= DEFAULT_HEARTBEAT_GAP_SECONDS
        and ask > bid
        and max_shares >= SIZE_MULTIPLE
    )
    row = {
        "scenario_id": scenario_id,
        "scenario_kind": kind,
        "cycle": 1,
        "condition_id": condition_id,
        "token_id": token_id,
        "input_status": input_status,
        "control_state": control_state,
        "heartbeat_age_seconds": int(heartbeat_age_seconds),
        "kill_triggered": bool(kill_triggered),
        "stage_cap_usd": round(stage_cap_usd, 4),
        "quote_ticket": _ticket(condition_id, token_id, bid=bid, ask=ask, max_shares=max_shares),
        "starting_state": dict(starting_state or _state()),
        "expected": {
            "quoting_permitted": quoting_permitted,
            "must_end_flat": not quoting_permitted and (
                input_status in {"missing", "stale"}
                or control_state in {"pull_quotes_now", "STOP"}
                or kill_triggered
                or heartbeat_age_seconds > DEFAULT_HEARTBEAT_GAP_SECONDS
            ),
            "cancel_within_cycles": 1 if control_state in {"pull_quotes_now", "STOP"} else None,
            "dead_man_required": heartbeat_age_seconds > DEFAULT_HEARTBEAT_GAP_SECONDS,
        },
    }
    row["input_fingerprint"] = _digest({key: value for key, value in row.items() if key != "input_fingerprint"})
    return row


def _synthetic_scenarios(stage_cap_usd: float, heartbeat_gap_seconds: int) -> list[dict[str, Any]]:
    started = _state(open_orders=1, position_shares=5, price=0.49)
    definitions = [
        ("event_start_book_clear", {"input_status": "missing", "control_state": "pull_quotes_now", "starting_state": started}),
        ("five_share_minimum", {"max_shares": 5}),
        ("sub_five_tail", {"max_shares": 4}),
        ("spread_crossing", {"bid": 0.52, "ask": 0.51}),
        ("news_gap_through_quote", {"bid": 0.35, "ask": 0.65, "control_state": "pull_quotes_now", "starting_state": started}),
        ("stale_websocket", {"input_status": "stale", "starting_state": started}),
        ("heartbeat_gap", {"heartbeat_age_seconds": heartbeat_gap_seconds + 1, "starting_state": started}),
        ("kill_criteria_day", {"control_state": "STOP", "kill_triggered": True, "starting_state": started}),
    ]
    scenarios: list[dict[str, Any]] = []
    for kind, overrides in definitions:
        scenario_id = f"synthetic_{kind}"
        cycle = _cycle(
            scenario_id,
            kind,
            condition_id=f"wo74-{kind}",
            token_id=f"wo74-token-{kind}",
            stage_cap_usd=stage_cap_usd,
            **overrides,
        )
        # The effective threshold may only tighten the frozen 30-minute cap.
        if kind == "heartbeat_gap":
            cycle["expected"]["quoting_permitted"] = False
            cycle["expected"]["dead_man_required"] = True
            cycle["expected"]["must_end_flat"] = True
            cycle["input_fingerprint"] = _digest({k: v for k, v in cycle.items() if k != "input_fingerprint"})
        scenarios.append({"scenario_id": scenario_id, "source": "synthetic", "kind": kind, "cycles": [cycle]})
    return scenarios


def _read_official_rows(path: Path) -> list[dict[str, str]]:
    try:
        opener = gzip.open if path.suffix == ".gz" else path.open
        if path.suffix == ".gz":
            handle = opener(path, "rt", encoding="utf-8-sig", errors="replace", newline="")
        else:
            handle = opener("r", encoding="utf-8-sig", errors="replace", newline="")
        with handle:
            return [{str(k): "" if v is None else str(v) for k, v in row.items()} for row in csv.DictReader(handle)]
    except (OSError, csv.Error):
        return []


def _official_scenarios(cfg: EngineConfig, settings: Mapping[str, Any]) -> list[dict[str, Any]]:
    root = cfg.output_root / "maker_carry" / "official_books"
    paths = sorted(root.glob("*.csv.gz")) + sorted(root.glob("*.csv")) if root.exists() else []
    scenarios: list[dict[str, Any]] = []
    for path in paths:
        eligible: list[dict[str, Any]] = []
        for row in _read_official_rows(path):
            bid = safe_float(row.get("best_bid"))
            ask = safe_float(row.get("best_ask"))
            token_id = str(row.get("asset_id") or row.get("token_id") or "").strip()
            condition_id = str(row.get("condition_id") or path.name.split(".")[0]).strip()
            if bid is None or ask is None or not token_id or not condition_id:
                continue
            if not (0.05 <= bid <= 0.90 and bid < ask <= 0.99):
                continue
            eligible.append({"row": row, "bid": bid, "ask": ask, "token_id": token_id, "condition_id": condition_id})
        if not eligible:
            continue
        picked = eligible[-1]
        source_fingerprint = _digest(picked["row"])
        scenario_id = f"official_{_digest([path.name, source_fingerprint])[:16]}"
        cycle = _cycle(
            scenario_id,
            "recorded_official_book_window",
            condition_id=picked["condition_id"],
            token_id=picked["token_id"],
            bid=picked["bid"],
            ask=picked["ask"],
            max_shares=10,
            stage_cap_usd=float(settings["stage_cap_usd"]),
        )
        scenarios.append(
            {
                "scenario_id": scenario_id,
                "source": "wo44_official_book",
                "kind": "recorded_official_book_window",
                "source_file": path.relative_to(cfg.output_root).as_posix(),
                "source_row_fingerprint": source_fingerprint,
                "cycles": [cycle],
            }
        )
        if len(scenarios) >= int(settings["max_official_scenarios"]):
            break
    return scenarios


def build_replay_scenario_corpus(cfg: EngineConfig) -> dict[str, Any]:
    settings = _settings(cfg)
    scenarios = _official_scenarios(cfg, settings)
    scenarios.extend(_synthetic_scenarios(float(settings["stage_cap_usd"]), int(settings["heartbeat_gap_seconds"])))
    payload = {
        "work_order": WORK_ORDER,
        "generated_at_utc": now_utc(),
        "contract_version": 1,
        "heartbeat_gap_seconds": int(settings["heartbeat_gap_seconds"]),
        "size_multiple_shares": SIZE_MULTIPLE,
        "stage_cap_usd": float(settings["stage_cap_usd"]),
        "official_book_scenario_count": sum(row["source"] == "wo44_official_book" for row in scenarios),
        "synthetic_scenario_count": sum(row["source"] == "synthetic" for row in scenarios),
        "required_synthetic_kinds": list(REQUIRED_SYNTHETIC_KINDS),
        "scenarios": scenarios,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    payload["corpus_digest"] = _digest(scenarios)
    write_json(cfg.output_root / settings["scenario_corpus_file"], payload)
    return payload


def _flat(state: Mapping[str, Any]) -> bool:
    return (
        (safe_int(state.get("open_orders")) or 0) == 0
        and abs(safe_float(state.get("position_shares")) or 0.0) <= 1e-9
        and abs(safe_float(state.get("exposure_usd")) or 0.0) <= 1e-9
        and abs(safe_float(state.get("open_order_notional_usd")) or 0.0) <= 1e-9
        and abs(safe_float(state.get("capital_at_risk_usd")) or 0.0) <= 1e-9
    )


def write_reference_stub_log(
    cfg: EngineConfig,
    corpus: Mapping[str, Any],
    *,
    candidate_build_id: str = "wo74-reference-stub",
) -> tuple[Path, Path]:
    """Write a deterministic keyless reference log; never invoke an executor."""
    settings = _settings(cfg)
    decisions: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []
    action_counter = 0
    for scenario in corpus.get("scenarios", []):
        for cycle in _mapping(scenario).get("cycles", []):
            cycle = _mapping(cycle)
            expected = _mapping(cycle.get("expected"))
            ticket = _mapping(cycle.get("quote_ticket"))
            before = _mapping(cycle.get("starting_state"))
            actions: list[dict[str, Any]] = []

            def add_action(action_type: str, *, shares: float = 0.0) -> None:
                nonlocal action_counter
                action_counter += 1
                action_id = f"wo74-stub-{action_counter:04d}"
                action = {
                    "action_id": action_id,
                    "action_type": action_type,
                    "condition_id": str(ticket.get("condition_id") or ""),
                    "token_id": str(ticket.get("token_id") or ""),
                    "side": "SELL" if action_type == "flatten" else str(ticket.get("side") or ""),
                    "price": float(ticket.get("price") or 0.0) if action_type == "place" else 0.0,
                    "shares": float(shares),
                }
                actions.append(action)
                ledger.append(
                    {
                        "candidate_build_id": candidate_build_id,
                        "scenario_id": cycle["scenario_id"],
                        "cycle": cycle["cycle"],
                        **action,
                        "recorded_at_utc": now_utc(),
                    }
                )

            if expected.get("must_end_flat"):
                if (safe_int(before.get("open_orders")) or 0) > 0:
                    add_action("cancel")
                position = abs(safe_float(before.get("position_shares")) or 0.0)
                if position >= SIZE_MULTIPLE:
                    add_action("flatten", shares=position)
                after = _state()
            elif expected.get("quoting_permitted"):
                add_action("place", shares=SIZE_MULTIPLE)
                after = _state(open_orders=1, price=float(ticket.get("price") or 0.5))
            else:
                after = dict(before)
            decisions.append(
                {
                    "candidate_build_id": candidate_build_id,
                    "candidate_kind": "reference_stub",
                    "mode": "replay",
                    "scenario_id": cycle["scenario_id"],
                    "cycle": cycle["cycle"],
                    "input_fingerprint": cycle["input_fingerprint"],
                    "state_before": before,
                    "actions": actions,
                    "state_after": after,
                    "dead_man_triggered": bool(expected.get("dead_man_required")),
                }
            )
    decision_path = cfg.output_root / settings["decision_log_file"]
    ledger_path = cfg.output_root / settings["execution_ledger_file"]
    write_json(decision_path, decisions)
    write_csv(ledger_path, ledger, fieldnames=LEDGER_FIELDS)
    return decision_path, ledger_path


def _violation(rule: str, detail: str, *, scenario_id: str = "", cycle: Any = "") -> dict[str, Any]:
    return {"rule": rule, "scenario_id": scenario_id, "cycle": cycle, "detail": detail}


def _ledger_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except (OSError, csv.Error):
        return []


def certify_executor_replay(
    cfg: EngineConfig,
    *,
    decision_log_path: str | Path | None = None,
    execution_ledger_path: str | Path | None = None,
    generate_reference_stub: bool = False,
    candidate_build_id: str | None = None,
) -> dict[str, Any]:
    settings = _settings(cfg)
    corpus = build_replay_scenario_corpus(cfg)
    if generate_reference_stub:
        stub_id = str(candidate_build_id or "wo74-reference-stub").strip()
        decision_path, ledger_path = write_reference_stub_log(cfg, corpus, candidate_build_id=stub_id)
    else:
        decision_path = Path(decision_log_path) if decision_log_path else cfg.output_root / settings["decision_log_file"]
        ledger_path = Path(execution_ledger_path) if execution_ledger_path else cfg.output_root / settings["execution_ledger_file"]

    raw_decisions = _read_json(decision_path)
    decisions = [dict(row) for row in raw_decisions] if isinstance(raw_decisions, list) else []
    ledger = _ledger_rows(ledger_path)
    violations: list[dict[str, Any]] = []
    official_count = int(corpus.get("official_book_scenario_count") or 0)
    if official_count < 1:
        violations.append(_violation("scenario_coverage", "no recorded WO-44 official-book window is present"))
    synthetic_kinds = {str(_mapping(row).get("kind") or "") for row in corpus.get("scenarios", []) if _mapping(row).get("source") == "synthetic"}
    missing_kinds = sorted(set(REQUIRED_SYNTHETIC_KINDS) - synthetic_kinds)
    if missing_kinds:
        violations.append(_violation("scenario_coverage", f"missing synthetic kinds: {','.join(missing_kinds)}"))
    if not decisions:
        violations.append(_violation("scenario_coverage", "candidate decision log is missing or empty"))

    expected_cycles: dict[tuple[str, int], dict[str, Any]] = {}
    for scenario in corpus.get("scenarios", []):
        for cycle in _mapping(scenario).get("cycles", []):
            row = _mapping(cycle)
            expected_cycles[(str(row.get("scenario_id") or ""), int(row.get("cycle") or 0))] = row
    observed: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in decisions:
        key = (str(row.get("scenario_id") or ""), int(safe_int(row.get("cycle")) or 0))
        observed.setdefault(key, []).append(row)
    for key in sorted(set(expected_cycles) - set(observed)):
        violations.append(_violation("scenario_coverage", "missing decision row", scenario_id=key[0], cycle=key[1]))
    for key in sorted(set(observed) - set(expected_cycles)):
        violations.append(_violation("scenario_coverage", "unexpected decision row", scenario_id=key[0], cycle=key[1]))
    for key, rows in observed.items():
        if len(rows) != 1:
            violations.append(_violation("scenario_coverage", "decision row is not unique", scenario_id=key[0], cycle=key[1]))

    build_ids = {str(row.get("candidate_build_id") or "").strip() for row in decisions}
    build_ids.discard("")
    chosen_build = str(candidate_build_id or (next(iter(build_ids)) if len(build_ids) == 1 else "")).strip()
    if len(build_ids) != 1 or not chosen_build or (build_ids and chosen_build not in build_ids):
        violations.append(_violation("scenario_coverage", "exactly one non-empty candidate_build_id is required"))
    candidate_kinds = {str(row.get("candidate_kind") or "").strip() for row in decisions}
    reference_stub_used = generate_reference_stub or candidate_kinds == {"reference_stub"}

    action_rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for key, expected in expected_cycles.items():
        rows = observed.get(key, [])
        if len(rows) != 1:
            continue
        row = rows[0]
        scenario_id, cycle_number = key
        if str(row.get("mode") or "") != "replay":
            violations.append(_violation("scenario_coverage", "candidate mode must equal replay", scenario_id=scenario_id, cycle=cycle_number))
        if str(row.get("input_fingerprint") or "") != str(expected.get("input_fingerprint") or ""):
            violations.append(_violation("scenario_coverage", "input fingerprint mismatch", scenario_id=scenario_id, cycle=cycle_number))
        expected_state = _mapping(expected.get("starting_state"))
        before = _mapping(row.get("state_before"))
        if _digest(before) != _digest(expected_state):
            violations.append(_violation("scenario_coverage", "state_before does not match scenario", scenario_id=scenario_id, cycle=cycle_number))
        after = _mapping(row.get("state_after"))
        actions = row.get("actions") if isinstance(row.get("actions"), list) else []
        ticket = _mapping(expected.get("quote_ticket"))
        expected_flags = _mapping(expected.get("expected"))
        cap = float(expected.get("stage_cap_usd") or 0.0)
        place_actions: list[dict[str, Any]] = []
        for raw_action in actions:
            action = _mapping(raw_action)
            action_rows.append((row, action))
            action_type = str(action.get("action_type") or "")
            if action_type not in {"place", "cancel", "flatten"}:
                violations.append(_violation("action_ledger_completeness", f"unsupported action_type={action_type}", scenario_id=scenario_id, cycle=cycle_number))
            shares = safe_float(action.get("shares")) or 0.0
            if shares > 0 and abs((shares / SIZE_MULTIPLE) - round(shares / SIZE_MULTIPLE)) > 1e-9:
                violations.append(_violation("five_share_multiple", "positive action size is not a multiple of 5", scenario_id=scenario_id, cycle=cycle_number))
            if action_type == "place":
                place_actions.append(action)
                allowed = expected_flags.get("quoting_permitted") is True
                exact_ticket = (
                    str(action.get("condition_id") or "") == str(ticket.get("condition_id") or "")
                    and str(action.get("token_id") or "") == str(ticket.get("token_id") or "")
                    and str(action.get("side") or "") == str(ticket.get("side") or "")
                    and abs((safe_float(action.get("price")) or 0.0) - float(ticket.get("price") or 0.0)) <= 1e-9
                    and 0 < shares <= float(ticket.get("max_shares") or 0.0)
                )
                if not allowed or not exact_ticket:
                    violations.append(_violation("quote_sheet_boundary", "place action is outside the scenario quote sheet", scenario_id=scenario_id, cycle=cycle_number))
                notional = (safe_float(action.get("price")) or 0.0) * shares
                if notional > cap + 1e-9:
                    violations.append(_violation("policy_caps", "place notional exceeds stage cap", scenario_id=scenario_id, cycle=cycle_number))
        aggregate_place_notional = sum(
            (safe_float(action.get("price")) or 0.0) * (safe_float(action.get("shares")) or 0.0)
            for action in place_actions
        )
        if aggregate_place_notional > cap + 1e-9:
            violations.append(
                _violation(
                    "policy_caps",
                    "aggregate place notional exceeds stage cap",
                    scenario_id=scenario_id,
                    cycle=cycle_number,
                )
            )
        if not expected_flags.get("quoting_permitted") and place_actions:
            violations.append(_violation("quote_sheet_boundary", "quoting occurred while scenario prohibited quoting", scenario_id=scenario_id, cycle=cycle_number))
        capital_at_risk = safe_float(after.get("capital_at_risk_usd"))
        if capital_at_risk is None or capital_at_risk < -1e-9 or capital_at_risk > cap + 1e-9:
            violations.append(_violation("policy_caps", "state_after capital_at_risk is missing, negative, or above cap", scenario_id=scenario_id, cycle=cycle_number))
        if expected_flags.get("must_end_flat") and not _flat(after):
            if expected.get("input_status") in {"missing", "stale"}:
                rule = "flat_on_missing_or_stale_input"
            elif expected_flags.get("dead_man_required"):
                rule = "dead_man_flatten"
            else:
                rule = "stop_cancellation"
            violations.append(_violation(rule, "candidate did not end the cycle flat", scenario_id=scenario_id, cycle=cycle_number))
        if expected.get("control_state") in {"pull_quotes_now", "STOP"}:
            had_orders = (safe_int(before.get("open_orders")) or 0) > 0
            cancelled = any(str(action.get("action_type") or "") == "cancel" for action in actions)
            if had_orders and (not cancelled or (safe_int(after.get("open_orders")) or 0) != 0):
                violations.append(_violation("stop_cancellation", "pull/STOP did not cancel all within one cycle", scenario_id=scenario_id, cycle=cycle_number))
        if expected.get("input_status") in {"missing", "stale"} and not _flat(after):
            violations.append(_violation("flat_on_missing_or_stale_input", "missing/stale input did not fail flat", scenario_id=scenario_id, cycle=cycle_number))
        if expected_flags.get("dead_man_required"):
            flattened = (safe_float(before.get("position_shares")) or 0.0) == 0.0 or any(
                str(action.get("action_type") or "") == "flatten" for action in actions
            )
            if row.get("dead_man_triggered") is not True or not _flat(after) or not flattened:
                violations.append(_violation("dead_man_flatten", "heartbeat gap did not trigger a logged flat state", scenario_id=scenario_id, cycle=cycle_number))

    action_ids = [str(action.get("action_id") or "").strip() for _, action in action_rows]
    if any(not value for value in action_ids) or len(action_ids) != len(set(action_ids)):
        violations.append(_violation("action_ledger_completeness", "action IDs must be non-empty and unique"))
    selected_ledger = [row for row in ledger if str(row.get("candidate_build_id") or "") == chosen_build]
    ledger_by_id: dict[str, list[dict[str, str]]] = {}
    for row in selected_ledger:
        ledger_by_id.setdefault(str(row.get("action_id") or ""), []).append(row)
    if set(action_ids) != set(ledger_by_id):
        violations.append(_violation("action_ledger_completeness", "decision actions and execution-ledger action IDs differ"))
    for decision, action in action_rows:
        rows = ledger_by_id.get(str(action.get("action_id") or ""), [])
        if len(rows) != 1:
            violations.append(_violation("action_ledger_completeness", "action does not have exactly one ledger row"))
            continue
        ledger_row = rows[0]
        expected_fields = {
            "scenario_id": str(decision.get("scenario_id") or ""),
            "cycle": str(int(safe_int(decision.get("cycle")) or 0)),
            "action_type": str(action.get("action_type") or ""),
            "condition_id": str(action.get("condition_id") or ""),
            "token_id": str(action.get("token_id") or ""),
        }
        if any(str(ledger_row.get(key) or "") != value for key, value in expected_fields.items()):
            violations.append(_violation("action_ledger_completeness", "ledger row fields do not match decision action"))

    checks = {
        rule: {
            "status": "PASS" if not any(row["rule"] == rule for row in violations) else "FAIL",
            "violation_count": sum(row["rule"] == rule for row in violations),
        }
        for rule in CONTRACT_RULES
    }
    status = "PASS" if settings["enabled"] and not violations else "FAIL"
    if not settings["enabled"]:
        violations.append(_violation("scenario_coverage", "certification harness is disabled"))
        checks["scenario_coverage"] = {"status": "FAIL", "violation_count": checks["scenario_coverage"]["violation_count"] + 1}
    result = {
        "work_order": WORK_ORDER,
        "generated_at_utc": now_utc(),
        "status": status,
        "candidate_build_id": chosen_build or None,
        "candidate_kind": "reference_stub" if reference_stub_used else "executor_candidate",
        "reference_stub_used": reference_stub_used,
        "corpus_digest": corpus.get("corpus_digest"),
        "scenario_count": len(expected_cycles),
        "official_book_scenario_count": official_count,
        "synthetic_scenario_count": int(corpus.get("synthetic_scenario_count") or 0),
        "decision_row_count": len(decisions),
        "action_count": len(action_rows),
        "ledger_row_count": len(selected_ledger),
        "contract_checks": checks,
        "violations": violations,
        "blocks_canary_by_certification": status != "PASS",
        "canary_authorised_by_this_artifact": False,
        "post_amendment_executor_invoked": False,
        "credential_loading_invoked": False,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        "decision_log_path": str(decision_path),
        "execution_ledger_path": str(ledger_path),
        "scenario_corpus_path": str(cfg.output_root / settings["scenario_corpus_file"]),
    }
    write_json(cfg.output_root / settings["certification_file"], result)
    return result
