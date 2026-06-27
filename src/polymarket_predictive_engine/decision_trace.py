from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import EngineConfig, live_trading_allowed
from .research_focus import build_research_focus
from .utils import now_utc, parse_timestamp, read_csv_rows, read_json, safe_float, write_json

TRACE_LIMIT = 30


def _read_json(path: Path) -> dict[str, Any]:
    payload = read_json(path, default={}) or {}
    return payload if isinstance(payload, dict) else {}


def _payload_timestamp(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("generated_at_utc", "timestamp_utc", "timestamp", "created_at_utc", "run_at_utc"):
        value = payload.get(key)
        if value:
            return str(value)
    current = payload.get("current")
    if isinstance(current, dict) and current.get("timestamp_utc"):
        return str(current.get("timestamp_utc"))
    return ""


def _age_seconds(value: Any) -> float | None:
    parsed = parse_timestamp(value)
    if parsed is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())


def _payload_age_seconds(payload: Any) -> float | None:
    return _age_seconds(_payload_timestamp(payload))


def _status_from_payload(payload: Any, *, stale_after_seconds: float = 180.0) -> str:
    if not isinstance(payload, dict) or not payload:
        return "missing"
    age = _payload_age_seconds(payload)
    if age is not None and age > stale_after_seconds:
        return "stale"
    status = str(payload.get("status") or "present").lower()
    if status in {"ok", "ran", "running", "live", "present", "approved", "paper_only"}:
        return "ok"
    if status in {"error", "failed", "blocked", "down"}:
        return status
    return status or "present"


def _safe_int(value: Any) -> int:
    return int(safe_float(value) or 0)


def _counter_rows(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    counts = Counter(str(row.get(key) or "unknown") for row in rows)
    return [{"reason": reason, "count": count} for reason, count in counts.most_common(12)]


def _market_label(row: dict[str, Any]) -> str:
    for key in ("question", "market_name", "market_slug", "market_id", "token_id"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return "unknown"


def _trace_candidate(row: dict[str, Any], *, final_action: str) -> dict[str, Any]:
    return {
        "final_action": final_action,
        "market": _market_label(row),
        "market_id": row.get("market_id", ""),
        "token_id": row.get("token_id", ""),
        "category": row.get("category", ""),
        "signal_cohort": row.get("signal_cohort", ""),
        "cohort_promotion_status": row.get("cohort_promotion_status", ""),
        "cohort_evidence_source_cohort": row.get("cohort_evidence_source_cohort", ""),
        "edge": row.get("edge", ""),
        "edge_lower_bound": row.get("edge_lower_bound", ""),
        "gross_edge_before_slippage": row.get("gross_edge_before_slippage", ""),
        "alpha_trade_candidate": row.get("alpha_trade_candidate", ""),
        "shadow_trade_candidate": row.get("shadow_trade_candidate", ""),
        "bookmaker_cross_check_pass": row.get("bookmaker_cross_check_pass", ""),
        "microstructure_filter_pass": row.get("microstructure_filter_pass", ""),
        "liquidity": row.get("liquidity", ""),
        "spread": row.get("spread", ""),
        "risk_decision": row.get("risk_decision", ""),
        "sizing_decision": row.get("sizing_decision", ""),
        "rejection_reason": row.get("rejection_reason", ""),
        "approval_reason": row.get("approval_reason", ""),
    }


def _build_candidate_trace(approved: list[dict[str, Any]], rejected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    traced: list[dict[str, Any]] = []
    for row in approved[:TRACE_LIMIT]:
        traced.append(_trace_candidate(row, final_action="approved_for_paper_broker"))
    remaining = max(0, TRACE_LIMIT - len(traced))
    for row in rejected[:remaining]:
        traced.append(_trace_candidate(row, final_action="rejected_before_broker"))
    return traced


def _diagnose_no_trade(
    *,
    readiness: dict[str, Any],
    broker: dict[str, Any],
    predictions: list[dict[str, Any]],
    approved: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
) -> dict[str, Any]:
    if approved:
        return {
            "status": "signals_available",
            "main_blocker": "Approved paper signals exist; broker should process them unless duplicate, risk, or pause controls intervene.",
            "next_action": "Monitor broker fills, duplicate skips, and paper P&L.",
        }
    entry_pause = str(broker.get("entry_pause_reason") or "").strip()
    if entry_pause:
        return {
            "status": "entry_paused",
            "main_blocker": entry_pause,
            "next_action": "Monitor exits and wait for equity to recover above the clean forward P&L pause threshold.",
        }
    if not readiness.get("approved_for_paper_trading"):
        blockers = readiness.get("blockers") or []
        return {
            "status": "paper_readiness_blocked",
            "main_blocker": "; ".join(str(item) for item in blockers) or "paper readiness is not approved",
            "next_action": "Fix the readiness blocker before expecting paper entries.",
        }
    if not predictions:
        return {
            "status": "no_predictions",
            "main_blocker": "No prediction rows are available for the current cycle.",
            "next_action": "Run or wait for discovery plus prediction so the signal gate has a scored universe.",
        }
    if rejected:
        reason = str((_counter_rows(rejected, "rejection_reason") or [{"reason": "unknown"}])[0]["reason"])
        if "cohort_quarantined" in reason or "cohort_negative" in reason or "cohort evidence" in reason:
            next_action = "Keep collecting shadow evidence; do not force paper probes until the cohort clears quarantine or earns probationary/promoted status."
        elif "same-category validation" in reason:
            next_action = "Collect more clean resolved labels or cohort evidence for this category before enabling paper probes."
        elif "alpha lower-bound" in reason:
            next_action = "Wait for stronger lower-bound edge after costs, liquidity, uncertainty, and validation haircuts."
        else:
            next_action = "Inspect rejected_signals.csv and the candidate trace before changing gates."
        return {"status": "signals_rejected", "main_blocker": reason, "next_action": next_action}
    return {
        "status": "no_signal_evidence",
        "main_blocker": "No approved signals and no rejected-signal evidence are available.",
        "next_action": "Wait for the next prediction cycle or inspect the local-live heartbeat for stalled discovery/prediction.",
    }


def build_cycle_decision_trace(
    cfg: EngineConfig,
    *,
    cycle_report: dict[str, Any] | None = None,
    broker_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    predictions_root = cfg.output_root / "polymarket_predictions"
    governance = cfg.governance_root
    portfolio_root = cfg.output_root / "polymarket_portfolio"

    predictions = read_csv_rows(predictions_root / "predictions.csv")
    approved = read_csv_rows(predictions_root / "trade_signals.csv")
    rejected = read_csv_rows(predictions_root / "rejected_signals.csv")
    broker = broker_summary or _read_json(portfolio_root / "paper_trading_summary.json")
    readiness = _read_json(governance / "paper_trade_readiness.json")
    forward = cycle_report or _read_json(governance / "forward_paper_cycle.json")
    heartbeat = _read_json(governance / "local_live_loop_heartbeat.json")
    no_trade = _diagnose_no_trade(
        readiness=readiness,
        broker=broker,
        predictions=predictions,
        approved=approved,
        rejected=rejected,
    )

    trace = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "source": forward.get("source") or heartbeat.get("full_prediction_cycle", {}).get("source", ""),
        "live_trading": False,
        "pipeline_counts": {
            "websocket_messages": heartbeat.get("websocket", {}).get("new_messages"),
            "websocket_features": heartbeat.get("websocket_features", {}).get("feature_rows"),
            "ledger_snapshots_inserted": heartbeat.get("ingest", {}).get("inserted_market_snapshots"),
            "predictions": len(predictions),
            "approved_signals": len(approved),
            "rejected_signals": len(rejected),
            "broker_signals_processed": broker.get("signals_processed", 0),
            "buy_orders_filled": broker.get("buy_orders_filled", broker.get("orders_filled", 0)),
            "exit_orders_filled": broker.get("exit_orders_filled", 0),
        },
        "paper_readiness": {
            "approved": bool(readiness.get("approved_for_paper_trading")),
            "decision": readiness.get("decision", ""),
            "blockers": readiness.get("blockers", []),
            "resolved_labels": readiness.get("resolved_labels"),
            "model_source": readiness.get("model_source", ""),
        },
        "broker": {
            "status": broker.get("status", ""),
            "entry_pause_reason": broker.get("entry_pause_reason", ""),
            "broker_rejection_reasons": broker.get("broker_rejection_reasons", {}),
            "cash": broker.get("cash"),
            "equity": broker.get("equity"),
            "total_exposure": broker.get("total_exposure"),
        },
        "no_trade_diagnosis": no_trade,
        "top_rejection_reasons": _counter_rows(rejected, "rejection_reason"),
        "candidate_trace": _build_candidate_trace(approved, rejected),
    }
    write_json(governance / "cycle_decision_trace.json", trace)
    return trace


def _subsystem_freshness(cfg: EngineConfig) -> dict[str, Any]:
    governance = cfg.governance_root
    candidates = {
        "local_live_loop": governance / "local_live_loop_heartbeat.json",
        "background_discovery": governance / "local_live_loop_discovery_heartbeat.json",
        "forward_paper_cycle": governance / "forward_paper_cycle.json",
        "paper_broker": cfg.output_root / "polymarket_portfolio" / "paper_trading_summary.json",
        "paper_readiness": governance / "paper_trade_readiness.json",
        "cohort_pnl": governance / "signal_cohort_pnl.json",
        "shadow_cohort_pnl": governance / "shadow_signal_cohort_pnl.json",
        "liquidity_discovery": governance / "liquidity_discovery_summary.json",
        "sharp_odds_fetch": governance / "sharp_odds_fetch_summary.json",
        "sharp_anchor": governance / "sharp_anchor_summary.json",
        "crypto_fundamental": governance / "crypto_fundamental_summary.json",
        "supervisor": governance / "supervisor_status.json",
    }
    rows: list[dict[str, Any]] = []
    for name, path in candidates.items():
        payload = _read_json(path)
        rows.append(
            {
                "subsystem": name,
                "path": str(path),
                "status": _status_from_payload(payload),
                "generated_at_utc": _payload_timestamp(payload),
                "age_seconds": _payload_age_seconds(payload),
            }
        )
    payload = {"status": "ok", "generated_at_utc": now_utc(), "subsystems": rows}
    write_json(governance / "subsystem_freshness.json", payload)
    return payload


def _write_dashboard_agent_files(cfg: EngineConfig, status: dict[str, Any]) -> None:
    out = cfg.output_root / "polymarket_dashboard"
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "agent_status.json", status)
    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Polymarket Agent Status</title>
  <style>
    body { margin:0; font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif; background:#08111f; color:#ecf3ff; }
    main { max-width:1180px; margin:0 auto; padding:24px; }
    h1 { margin:0 0 6px; } .muted { color:#8ea3bf; }
    .card, section { border:1px solid #203553; border-radius:16px; background:rgba(255,255,255,.04); padding:14px; margin-top:14px; }
    .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; }
    .ok { color:#46d39a; } .bad { color:#ff6b7a; } .warn { color:#ffd166; }
    table { width:100%; border-collapse:collapse; margin-top:8px; }
    th,td { border-bottom:1px solid rgba(255,255,255,.08); padding:8px; text-align:left; vertical-align:top; }
    th { color:#8ea3bf; text-transform:uppercase; font-size:12px; }
    pre { white-space:pre-wrap; overflow-wrap:anywhere; }
  </style>
</head>
<body><main>
  <h1>Polymarket Agent Status</h1>
  <div class="muted">Auto-refreshes every 5 seconds. This is paper/shadow observability only.</div>
  <div id="app">Loading...</div>
</main>
<script>
const esc = v => String(v ?? "-").replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const cls = s => /ok|live|ran|approved|present/.test(String(s||"")) ? "ok" : /error|blocked|stale|missing|disabled/.test(String(s||"")) ? "bad" : "warn";
function table(rows, cols){ if(!rows || !rows.length) return '<div class="muted">No rows.</div>'; return '<table><thead><tr>'+cols.map(c=>'<th>'+esc(c[0])+'</th>').join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+cols.map(c=>'<td>'+esc(r[c[1]])+'</td>').join('')+'</tr>').join('')+'</tbody></table>'; }
async function load(){
  const data = await fetch('agent_status.json?ts=' + Date.now()).then(r => r.json());
  const focus = data.research_focus || {};
  document.getElementById('app').innerHTML = `
    <section><h2>Next best action</h2><div class="card"><b>${esc(data.next_best_action?.summary)}</b><p>${esc(data.next_best_action?.action)}</p></div></section>
    <section><h2>Research focus</h2><div class="card"><b>${esc(focus.summary || '-')}</b></div>${table(focus.watchlist || [], [['Cohort','cohort'],['Thesis','thesis'],['Status','status'],['Priority','priority_score'],['Fills','buy_fills'],['Settled','settled_fills'],['P&L','total_pnl_usdc'],['ROI','roi'],['Query','recommended_collection_query']])}</section>
    <section><h2>Agent lanes</h2><div class="grid">${(data.agents||[]).map(a=>`<div class="card"><b>${esc(a.agent)}</b><div class="${cls(a.status)}">${esc(a.status)}</div><p class="muted">${esc(a.role)}</p></div>`).join('')}</div></section>
    <section><h2>Pipeline counts</h2><pre>${esc(JSON.stringify(data.pipeline_counts || {}, null, 2))}</pre></section>
    <section><h2>Subsystem freshness</h2>${table(data.subsystem_freshness || [], [['Subsystem','subsystem'],['Status','status'],['Age sec','age_seconds'],['Generated','generated_at_utc']])}</section>
  `;
}
load(); setInterval(load, 5000);
</script></body></html>
"""
    (out / "agent_status.html").write_text(html, encoding="utf-8")


def build_agent_status(cfg: EngineConfig, trace: dict[str, Any] | None = None) -> dict[str, Any]:
    governance = cfg.governance_root
    trace = trace or _read_json(governance / "cycle_decision_trace.json")
    heartbeat = _read_json(governance / "local_live_loop_heartbeat.json")
    broker = _read_json(cfg.output_root / "polymarket_portfolio" / "paper_trading_summary.json")
    readiness = _read_json(governance / "paper_trade_readiness.json")
    cohort = _read_json(governance / "signal_cohort_pnl.json")
    shadow = _read_json(governance / "shadow_signal_cohort_pnl.json")
    live_allowed, live_reasons = live_trading_allowed(cfg)
    no_trade = trace.get("no_trade_diagnosis", {}) if isinstance(trace, dict) else {}
    counts = trace.get("pipeline_counts", {}) if isinstance(trace, dict) else {}
    freshness = _subsystem_freshness(cfg)
    try:
        research_focus = build_research_focus(cfg)
    except Exception as exc:  # noqa: BLE001 - keep status page alive
        research_focus = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

    agents = [
        {"agent": "supervisor", "status": _status_from_payload(_read_json(governance / "supervisor_status.json"), stale_after_seconds=3600), "role": "start/restart the local loop and dashboard, enforce one process, expose reboot state"},
        {"agent": "collector", "status": _status_from_payload(heartbeat, stale_after_seconds=180), "role": "collect CLOB websocket messages and update live top-of-book evidence", "last_messages": counts.get("websocket_messages"), "last_features": counts.get("websocket_features")},
        {"agent": "predictor", "status": "ok" if _safe_int(counts.get("predictions")) > 0 else "waiting", "role": "build point-in-time features and score the current universe", "predictions": counts.get("predictions")},
        {"agent": "signal_gate", "status": "approved_signals" if _safe_int(counts.get("approved_signals")) > 0 else "blocking_all_candidates", "role": "apply alpha, validation, cohort, and risk pre-checks before the broker sees anything", "approved": counts.get("approved_signals"), "rejected": counts.get("rejected_signals")},
        {"agent": "paper_broker", "status": broker.get("status", "missing"), "role": "paper-fill approved signals, close exits, settle positions, and export the paper ledger", "buy_fills": broker.get("buy_orders_filled", broker.get("orders_filled", 0)), "exit_fills": broker.get("exit_orders_filled", 0)},
        {"agent": "shadow_learning", "status": shadow.get("status", "missing"), "role": "maintain shadow positions and forward cohort evidence before promotion", "open_positions": shadow.get("open_positions"), "promoted_cohorts": cohort.get("promoted_cohorts", [])},
        {"agent": "live_executor", "status": "disabled" if not live_allowed else "approval_flags_set_but_executor_is_skeleton", "role": "real trading path; intentionally disabled/skeleton-only in this repo", "blockers": live_reasons},
    ]
    status = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "next_best_action": {"summary": no_trade.get("main_blocker", "No decision trace available yet."), "action": no_trade.get("next_action", "Run or wait for the next local-live paper cycle."), "diagnosis_status": no_trade.get("status", "unknown")},
        "research_focus": research_focus,
        "agents": agents,
        "readiness": readiness,
        "pipeline_counts": counts,
        "subsystem_freshness": freshness.get("subsystems", []),
    }
    write_json(governance / "agent_status.json", status)
    write_json(governance / "next_best_action.json", status["next_best_action"])
    _write_dashboard_agent_files(cfg, status)
    return status


def write_agent_runtime_bundle(
    cfg: EngineConfig,
    *,
    cycle_report: dict[str, Any] | None = None,
    broker_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    trace = build_cycle_decision_trace(cfg, cycle_report=cycle_report, broker_summary=broker_summary)
    agent_status = build_agent_status(cfg, trace)
    return {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "decision_trace": str(cfg.governance_root / "cycle_decision_trace.json"),
        "agent_status": str(cfg.governance_root / "agent_status.json"),
        "next_best_action": agent_status.get("next_best_action", {}),
        "research_focus": agent_status.get("research_focus", {}),
        "dashboard_agent_status": str(cfg.output_root / "polymarket_dashboard" / "agent_status.html"),
    }
