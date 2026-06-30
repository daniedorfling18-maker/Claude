from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from .config import EngineConfig
from .utils import now_utc, parse_timestamp, read_csv_rows, read_json, safe_float, write_json
from .worldcup_validation import is_worldcup_winner_market


HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Polymarket Paper Trading Dashboard</title>
  <style>
    :root { color-scheme: dark; --bg:#08111f; --panel:#101c2f; --panel2:#13233a; --text:#ecf3ff; --muted:#8ea3bf; --good:#46d39a; --bad:#ff6b7a; --warn:#ffd166; --line:#203553; }
    * { box-sizing: border-box; }
    body { margin:0; font:14px/1.45 Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif; background:radial-gradient(circle at top left,#173154 0,#08111f 42rem); color:var(--text); }
    main { max-width:1280px; margin:0 auto; padding:clamp(16px,3vw,28px); }
    header { display:flex; justify-content:space-between; gap:16px; align-items:flex-start; margin-bottom:22px; }
    h1 { margin:0; font-size:28px; letter-spacing:-0.03em; }
    h2 { margin:0 0 12px; font-size:16px; }
    .sub { color:var(--muted); margin-top:6px; }
    .pill { display:inline-flex; align-items:center; gap:8px; border:1px solid var(--line); background:rgba(255,255,255,0.04); padding:8px 11px; border-radius:999px; white-space:nowrap; }
    .dot { width:9px; height:9px; border-radius:50%; background:var(--warn); box-shadow:0 0 18px currentColor; }
    .dot.good { background:var(--good); color:var(--good); }
    .dot.bad { background:var(--bad); color:var(--bad); }
    .grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin-bottom:16px; }
    .card, section { background:linear-gradient(180deg,rgba(255,255,255,0.055),rgba(255,255,255,0.025)); border:1px solid var(--line); border-radius:18px; box-shadow:0 18px 50px rgba(0,0,0,0.25); }
    .card { padding:16px; min-height:104px; min-width:0; }
    .label { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:0.08em; }
    .value { margin-top:8px; font-size:clamp(18px,2.2vw,26px); font-weight:750; letter-spacing:-0.03em; overflow-wrap:anywhere; }
    .value.good { color:var(--good); } .value.bad { color:var(--bad); } .value.warn { color:var(--warn); }
    section { padding:16px; margin-top:16px; overflow:hidden; }
    .tableWrap { width:100%; overflow-x:auto; -webkit-overflow-scrolling:touch; border-radius:12px; }
    table { width:100%; min-width:720px; border-collapse:collapse; }
    th, td { padding:10px 8px; border-bottom:1px solid rgba(255,255,255,0.08); text-align:left; vertical-align:top; }
    th { color:var(--muted); font-size:12px; font-weight:650; text-transform:uppercase; letter-spacing:0.06em; }
    td { color:#d8e5f8; overflow-wrap:anywhere; max-width:360px; }
    tr:hover td { background:rgba(255,255,255,0.035); }
    .two { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
    .mono { font-family:ui-monospace, SFMono-Regular, Consolas, monospace; font-size:12px; }
    .muted { color:var(--muted); }
    .facts { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }
    .fact { border:1px solid rgba(255,255,255,0.08); background:rgba(255,255,255,0.035); border-radius:12px; padding:10px; min-width:0; }
    .fact .label { margin-bottom:5px; }
    .factValue { color:#d8e5f8; font-weight:650; overflow-wrap:anywhere; }
    details.expand summary { cursor:pointer; color:#d8e5f8; }
    details.expand summary::marker { color:var(--muted); }
    .fullText { margin-top:8px; padding:8px; border-radius:10px; background:rgba(0,0,0,0.22); color:#c8d8ef; white-space:pre-wrap; overflow-wrap:anywhere; }
    .error { color:var(--bad); padding:16px; border:1px solid rgba(255,107,122,0.35); border-radius:14px; background:rgba(255,107,122,0.08); }
    @media (max-width: 980px) { .grid, .two, .facts { grid-template-columns:1fr; } header { flex-direction:column; } table { min-width:640px; } }
  </style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>Polymarket Paper Trading</h1>
      <div class="sub">Local paper bot dashboard - live websocket view - auto-refreshes every 5 seconds</div>
    </div>
    <div class="pill"><span id="statusDot" class="dot"></span><span id="statusText">Loading...</span></div>
  </header>
  <div id="error"></div>
  <div class="grid" id="cards"></div>
  <div class="two">
    <section><h2>Actual profit target</h2><div id="actualTarget"></div></section>
    <section><h2>Latest cycle</h2><div id="cycle"></div></section>
  </div>
  <section><h2>Why no trade?</h2><div id="tradeDiagnostics"></div></section>
  <section><h2>Strategy V2 anchored edge</h2><div id="strategyV2"></div></section>
  <div class="two">
    <section><h2>Promotion readiness</h2><div id="promotionReadiness"></div></section>
    <section><h2>Edge promotion watchlist</h2><div id="promotionWatchlist"></div></section>
  </div>
  <div class="two">
    <section><h2>Settlement watch</h2><div id="settlementWatch"></div></section>
    <section><h2>Shadow edge watchlist</h2><div id="shadowPromotionWatchlist"></div></section>
  </div>
  <section><h2>Signal cohort validation</h2><div id="cohorts"></div></section>
  <section><h2>Open shadow positions</h2><div id="shadowPositions"></div></section>
  <section><h2>Recent shadow fills</h2><div id="shadowFills"></div></section>
  <section><h2>World Cup validation layer</h2><div id="worldcupValidation"></div></section>
  <section><h2>Independent model anchors</h2><div id="independentFundamentals"></div></section>
  <section><h2>Historical edge search</h2><div id="edgeSearch"></div></section>
  <section><h2>Historical rule live shadow scan</h2><div id="promotedRuleShadow"></div></section>
  <section><h2>Liquidity discovery</h2><div id="liquidityDiscovery"></div></section>
  <section><h2>Open positions</h2><div id="positions"></div></section>
  <section><h2>Recent fills</h2><div id="fills"></div></section>
  <section><h2>Approved signals currently queued/scored</h2><div id="signals"></div></section>
</main>
<script>
const fmtUsd = (v) => v === null || v === undefined || v === "" || Number.isNaN(Number(v)) ? "-" : "$" + Number(v).toFixed(2);
const fmtNum = (v, d=4) => v === null || v === undefined || v === "" || Number.isNaN(Number(v)) ? "-" : Number(v).toFixed(d);
const escapeHtml = (v) => String(v ?? "-").replace(/[&<>"']/g, ch => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[ch]));
const asText = (v) => {
  if (v === null || v === undefined || v === "") return "-";
  if (Array.isArray(v)) return v.join(", ");
  if (typeof v === "object") return JSON.stringify(v, null, 2);
  return String(v);
};
const longText = (v, limit=96) => {
  const text = asText(v);
  if (!text || text === "-") return "-";
  if (text.length <= limit) return escapeHtml(text);
  const preview = text.slice(0, Math.max(24, Math.floor(limit * 0.58))) + " … " + text.slice(-Math.max(16, Math.floor(limit * 0.26)));
  return `<details class="expand"><summary title="${escapeHtml(text)}">${escapeHtml(preview)}</summary><div class="fullText">${escapeHtml(text)}</div></details>`;
};
const joinText = (v) => longText(Array.isArray(v) ? v.join(", ") : v);
const short = longText;
const joinList = joinText;
const marketLabel = (row) => longText(row?.market_name || row?.question || row?.market_slug || row?.market_id || row?.token_id);
const plain = (v) => escapeHtml(asText(v));
function card(label, value, cls="") { return `<div class="card"><div class="label">${escapeHtml(label)}</div><div class="value ${cls}">${value}</div></div>`; }
function table(rows, columns) {
  if (!rows || !rows.length) return `<div class="muted">No rows yet.</div>`;
  return `<div class="tableWrap"><table><thead><tr>${columns.map(c=>`<th>${escapeHtml(c[0])}</th>`).join("")}</tr></thead><tbody>${rows.map(row=>`<tr>${columns.map(c=>`<td>${c[2] ? c[2](row[c[1]], row) : plain(row[c[1]])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}
function facts(rows) {
  return `<div class="facts">${rows.map(row => `<div class="fact"><div class="label">${escapeHtml(row[0])}</div><div class="factValue">${row[2] ? row[2](row[1]) : longText(row[1])}</div></div>`).join("")}</div>`;
}
async function load() {
  try {
    const res = await fetch("dashboard_data.json?ts=" + Date.now());
    if (!res.ok) throw new Error("dashboard_data.json returned " + res.status);
    const data = await res.json();
    document.getElementById("error").innerHTML = "";
    const paper = data.forward_paper_cycle?.paper || data.forward_paper_cycle || {};
    const broker = data.paper_broker_summary || data.forward_paper_cycle?.broker || paper.broker || {};
    const target = data.actual_profit_target || data.forward_paper_cycle?.actual_profit_target || {};
    const monthly = data.forward_paper_cycle?.monthly_profit_target || {};
    const diag = data.trade_diagnostics || {};
    const strategyV2 = data.strategy_v2 || {};
    const roundTrip = strategyV2.round_trip_evidence || {};
    const roundTripPnl = roundTrip.realized_pnl_usdc ?? roundTrip.total_mark_pnl_usdc;
    const live = data.local_live_heartbeat || data.heartbeat || {};
    const freshness = data.evidence_freshness || {};
    const scanner = data.scanner_heartbeat || {};
    const discovery = live.discovery || {};
    const resourceGuard = live.resource_guard || {};
    const currentScan = discovery.scan || {};
    const lastScan = discovery.last_scan || {};
    const fastUpdown = discovery.last_fast_updown || discovery.fast_updown || {};
    const websocket = live.websocket || {};
    const websocketFeatures = live.websocket_features || {};
    const ingest = live.ingest || {};
    const status = target.status || data.forward_paper_cycle?.status || "unknown";
    const liveStatus = freshness.live_loop_status || "unknown";
    const runPosture = freshness.strategy_v2_runtime_posture || liveStatus;
    const good = liveStatus === "live";
    const guarded = runPosture === "memory_paused" || runPosture === "guard_paused";
    const bad = liveStatus === "stale" || liveStatus === "not_started" || liveStatus === "down";
    document.getElementById("statusDot").className = "dot " + (good ? "good" : guarded ? "warn" : bad ? "bad" : "");
    document.getElementById("statusText").textContent = runPosture + " - " + status + " - live tick " + (live.iteration || "-") + " - updated " + (data.generated_at_utc || "-");
    const pnl = Number(target.actual_pnl_since_baseline_usdc || 0);
    document.getElementById("cards").innerHTML = [
      card("Equity", fmtUsd(broker.equity), Number(broker.equity) >= 1000 ? "good" : "bad"),
      card("Actual P&L since clean baseline", fmtUsd(pnl), pnl >= 0 ? "good" : "bad"),
      card("Monthly run-rate", target.monthly_run_rate_usdc == null ? "Collecting" : fmtUsd(target.monthly_run_rate_usdc), target.monthly_run_rate_usdc >= target.target_monthly_profit_usdc ? "good" : ""),
      card("Live loop", liveStatus, good ? "good" : bad ? "bad" : "warn"),
      card("Run posture", runPosture, guarded ? "warn" : good ? "good" : bad ? "bad" : "warn"),
      card("Scoreboard", freshness.scoreboard_status || "unknown", freshness.scoreboard_status === "aligned" ? "good" : "warn"),
      card("Live WS messages", websocket.new_messages ?? "-", "good"),
      card("Live WS features", websocketFeatures.feature_rows ?? "-", "good"),
      card("Ledger snapshots", ingest.inserted_market_snapshots ?? "-", "good"),
      card("Scanning now", joinText(currentScan.scan_plan?.selected_queries || lastScan.scan_plan?.selected_queries || scanner.scan?.scan_plan?.selected_queries || scanner.scan?.queries), "warn"),
      card("Exposure", fmtUsd(broker.total_exposure)),
      card("Cash", fmtUsd(broker.cash)),
      card("Buy fills / cycle", broker.buy_orders_filled ?? broker.orders_filled ?? "0"),
      card("Exit fills / cycle", broker.exit_orders_filled ?? "0"),
      card("Signals approved", data.forward_paper_cycle?.signals_approved ?? "0"),
      card("Strategy V2 shadow", strategyV2.shadow_candidates ?? "0", Number(strategyV2.shadow_candidates || 0) > 0 ? "good" : "warn"),
      card("Strategy V2 anchors", `${strategyV2.anchored_rows ?? 0}/${strategyV2.rows_scored ?? 0}`, Number(strategyV2.anchored_rows || 0) > 0 ? "good" : "warn"),
      card("Round-trip P&L", fmtUsd(roundTripPnl), Number(roundTripPnl || 0) > 0 ? "good" : "warn"),
      card("Main trade blocker", longText(diag.main_blocker || "-", 120), Number(diag.approved_signals_count || 0) > 0 ? "good" : "warn"),
      card("Next settlement", data.shadow_settlement_watch?.next_settlement_minutes == null ? "Waiting" : fmtNum(data.shadow_settlement_watch.next_settlement_minutes, 0) + "m"),
      card("Shadow P&L", fmtUsd(data.shadow_settlement_watch?.shadow_total_pnl_usdc), Number(data.shadow_settlement_watch?.shadow_total_pnl_usdc || 0) > 0 ? "good" : "warn"),
      card("Expected lower-bound / cycle", fmtUsd(monthly.expected_lower_bound_profit_per_cycle_usdc), monthly.status === "on_pace" ? "good" : "warn")
    ].join("");
    document.getElementById("actualTarget").innerHTML = facts([
      ["Status", target.status],
      ["Target / month", target.target_monthly_profit_usdc, fmtUsd],
      ["Actual P&L", target.actual_pnl_since_baseline_usdc, fmtUsd],
      ["Tracking hours", target.elapsed_hours, v=>fmtNum(v,2)],
      ["Monthly run-rate", target.monthly_run_rate_usdc, fmtUsd],
      ["Baseline equity", target.baseline?.baseline_equity_usdc, fmtUsd]
    ]);
    document.getElementById("cycle").innerHTML = facts([
      ["Live tick", live.iteration],
      ["Live source", live.live_source || live.source || (websocket.new_messages != null ? "websocket" : "-")],
      ["WS window", live.websocket_seconds == null ? "-" : live.websocket_seconds + "s"],
      ["Prediction cycle", live.prediction_cycle_seconds == null ? "-" : live.prediction_cycle_seconds + "s"],
      ["Assets", live.asset_count == null ? "-" : live.asset_count],
      ["Effective max assets", live.effective_max_assets == null ? "-" : live.effective_max_assets],
      ["Resource guard", resourceGuard.reason || "-"],
      ["Memory", resourceGuard.memory_percent == null ? "-" : fmtNum(resourceGuard.memory_percent, 1) + "%"],
      ["Strategy V2 guard", freshness.strategy_v2_runtime_reason || strategyV2.runtime_reason || "-"],
      ["Strategy V2 memory", freshness.strategy_v2_memory_percent == null ? "-" : fmtNum(freshness.strategy_v2_memory_percent, 1) + "% / " + fmtNum(freshness.strategy_v2_max_memory_percent, 1) + "%"],
      ["WS messages", websocket.new_messages],
      ["WS features", websocketFeatures.feature_rows],
      ["Snapshots inserted", ingest.inserted_market_snapshots],
      ["Full cycle", live.full_prediction_cycle?.status],
      ["Discovery", discovery.status],
      ["Last scan", discovery.last_status],
      ["Next discovery", discovery.next_due_in_seconds == null ? "-" : Math.round(Number(discovery.next_due_in_seconds)) + "s"],
      ["Discovery #", discovery.discovery_iteration],
      ["Fast 5m", fastUpdown.tokens == null ? (fastUpdown.status || "-") : (fastUpdown.tokens + " tokens")],
      ["Fast assets", fastUpdown.assets, joinText],
      ["Scan mode", currentScan.scan_plan?.mode || lastScan.scan_plan?.mode || scanner.scan?.scan_plan?.mode],
      ["Queries", currentScan.scan_plan?.selected_queries || lastScan.scan_plan?.selected_queries || scanner.scan?.scan_plan?.selected_queries || scanner.scan?.queries, joinText],
      ["Priority queue", currentScan.scan_plan?.adaptive_priority?.priority_queries || lastScan.scan_plan?.adaptive_priority?.priority_queries || scanner.scan?.scan_plan?.adaptive_priority?.priority_queries || currentScan.scan_plan?.ordered_queries || lastScan.scan_plan?.ordered_queries || scanner.scan?.scan_plan?.ordered_queries, joinText],
      ["Tokens", live.discovery?.scan?.tokens || scanner.scan?.tokens],
      ["Features", data.forward_paper_cycle?.features],
      ["Predictions", data.forward_paper_cycle?.predictions],
      ["Approved", data.forward_paper_cycle?.signals_approved],
      ["Rejected", data.forward_paper_cycle?.signals_rejected],
      ["Broker rejects", monthly.broker_rejected_orders],
      ["Main reason", broker.entry_pause_reason || Object.keys(broker.broker_rejection_reasons || {}).join(", "), longText]
    ]);
    document.getElementById("tradeDiagnostics").innerHTML = facts([
      ["Main blocker", diag.main_blocker, v=>longText(v, 220)],
      ["Recommended action", diag.recommended_action, v=>longText(v, 220)],
      ["Predictions", diag.prediction_count],
      ["Approved signals", diag.approved_signals_count],
      ["Rejected signals", diag.rejected_signals_count],
      ["Near-miss learning", diag.near_miss_candidates_seen],
      ["Near-miss opened", diag.near_miss_opened_this_cycle],
      ["Near-miss open", diag.near_miss_open_positions],
      ["Shadow candidates", diag.shadow_candidates_seen],
      ["Opened this cycle", diag.shadow_opened_this_cycle],
      ["Quarantined cohorts", diag.quarantined_cohort_count]
    ]) + `<div style="height:12px"></div>` + table(diag.current_shadow_candidates || [], [
      ["Market","market_slug"],
      ["Outcome","outcome"],
      ["Cohort","signal_cohort"],
      ["Kind","crypto_model_contract_kind"],
      ["Crypto edge","crypto_model_edge_after_cost", v=>fmtNum(v,4)],
      ["Spread","spread", v=>fmtNum(v,4)],
      ["Liquidity","liquidity", v=>fmtNum(v,2)],
      ["Priority","shadow_priority_score", v=>fmtNum(v,4)],
      ["Reason","shadow_candidate_reason", longText]
    ]) + `<div style="height:12px"></div>` + table(diag.current_near_miss_candidates || [], [
      ["Market","market_slug"],
      ["Outcome","outcome"],
      ["Cohort","signal_cohort"],
      ["Raw edge","alpha_raw_edge", v=>fmtNum(v,4)],
      ["Lower-bound","edge_lower_bound", v=>fmtNum(v,4)],
      ["Penalty","alpha_total_penalty", v=>fmtNum(v,4)],
      ["Spread","spread", v=>fmtNum(v,4)],
      ["Liquidity","liquidity", v=>fmtNum(v,2)],
      ["Reason","near_miss_learning_reason", longText]
    ]) + `<div style="height:12px"></div>` + table(diag.top_rejection_reasons || [], [
      ["Count","count"],
      ["Rejected reason","reason", v=>longText(v, 180)]
    ]) + `<div style="height:12px"></div>` + table(diag.quarantined_cohorts || [], [
      ["Cohort","signal_cohort"],
      ["Closed","closed_positions"],
      ["P&L","closed_realised_pnl_usdc", fmtUsd],
      ["ROI","closed_roi", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Reason","quarantine_reason", v=>longText(v, 180)]
    ]);
    document.getElementById("strategyV2").innerHTML = facts([
      ["Decision", strategyV2.decision],
      ["Recommended action", strategyV2.recommended_action, v=>longText(v, 240)],
      ["Report generated", strategyV2.generated_at_utc],
      ["Cycle status", strategyV2.cycle_status?.status],
      ["Rows scored", strategyV2.rows_scored],
      ["Anchor rows loaded", strategyV2.anchor_rows_loaded],
      ["World Cup validated anchors", strategyV2.worldcup_validated_anchor_rows],
      ["Rows matched to anchors", strategyV2.anchored_rows],
      ["Shadow candidates", strategyV2.shadow_candidates],
      ["Watchlist rows", strategyV2.watchlist_candidates],
      ["Missing anchors", strategyV2.missing_anchor_rows],
      ["Persistence entries", strategyV2.persistence_entries],
      ["Forward evidence", strategyV2.forward_evidence?.decision],
      ["Forward MTM P&L", strategyV2.forward_evidence?.total_mark_pnl_usdc, fmtUsd],
      ["Forward review cohorts", strategyV2.forward_evidence?.paper_review_candidates],
      ["Round-trip evidence", roundTrip.decision],
      ["Closed round trips", roundTrip.closed_trades],
      ["Round-trip realized P&L", roundTrip.realized_pnl_usdc, fmtUsd],
      ["Round-trip MTM P&L", roundTrip.total_mark_pnl_usdc, fmtUsd],
      ["Main blocker", strategyV2.main_blocker, v=>longText(v, 180)]
    ]) + `<div style="height:12px"></div><h3>Current shadow candidates</h3>` + table(strategyV2.top_shadow_candidates || [], [
      ["Family","family"],
      ["Market","market_slug", v=>longText(v, 120)],
      ["Outcome","outcome"],
      ["Anchor","anchor_fair_probability", v=>fmtNum(v,4)],
      ["Price","executable_price", v=>fmtNum(v,4)],
      ["Edge after penalty","risk_adjusted_anchor_edge", v=>fmtNum(v,4)],
      ["Liquidity","liquidity", v=>fmtNum(v,2)],
      ["Spread","spread", v=>fmtNum(v,4)],
      ["Anchor source","anchor_source", v=>longText(v, 100)]
    ]) + `<div style="height:12px"></div><h3>Promotion progress</h3>` + table(strategyV2.promotion_progress || [], [
      ["Family","family"],
      ["Shadow log entries","shadow_entries"],
      ["Current shadows","current_shadow_candidates"],
      ["Need entries","remaining_shadow_entries_to_review"],
      ["Min settled","min_settled_for_review"],
      ["Best edge","best_risk_adjusted_anchor_edge", v=>fmtNum(v,4)],
      ["Status","status", v=>longText(v, 120)]
    ]) + `<div style="height:12px"></div><h3>Forward evidence by Strategy V2 cohort</h3>` + table(strategyV2.forward_evidence_cohorts || [], [
      ["Cohort","signal_cohort"],
      ["Candidates","candidates"],
      ["Current","current_shadow_candidates"],
      ["Resolved","resolved_candidates"],
      ["MTM P&L","total_mark_pnl_usdc", fmtUsd],
      ["MTM ROI","mark_roi", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Run-rate","monthly_run_rate_usdc", fmtUsd],
      ["Score","promotion_ready_score", (v,row)=>`${v ?? 0}/${row.promotion_ready_checks ?? "?"}`],
      ["Status","status", v=>longText(v, 140)],
      ["Reason","reason", v=>longText(v, 180)]
    ]) + `<div style="height:12px"></div><h3>Forward evidence candidate marks</h3>` + table(strategyV2.forward_evidence_top_candidates || [], [
      ["Cohort","signal_cohort"],
      ["Market","market_slug", v=>longText(v, 120)],
      ["Outcome","outcome"],
      ["Entry","entry_price", v=>fmtNum(v,4)],
      ["Latest","latest_price", v=>fmtNum(v,4)],
      ["P&L","mark_pnl_usdc", fmtUsd],
      ["ROI","mark_roi", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Resolved","resolved_evidence"],
      ["Settlement","settlement_status", v=>longText(v, 120)]
    ]) + `<div style="height:12px"></div><h3>Round-trip price-action evidence by cohort</h3>` + table(strategyV2.round_trip_evidence_cohorts || [], [
      ["Cohort","signal_cohort"],
      ["Candidates","candidates"],
      ["Closed","closed_trades"],
      ["Open","open_trades"],
      ["TP/SL","take_profit_exits", (v,row)=>`${v ?? 0}/${row.stop_loss_exits ?? 0}`],
      ["Win rate","win_rate", v=>fmtNum(Number(v) * 100, 1) + "%"],
      ["Realized P&L","realized_pnl_usdc", fmtUsd],
      ["Realized ROI","realized_roi", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["MTM P&L","total_mark_pnl_usdc", fmtUsd],
      ["Status","status", v=>longText(v, 140)],
      ["Reason","reason", v=>longText(v, 180)]
    ]) + `<div style="height:12px"></div><h3>Round-trip price-action candidate marks</h3>` + table(strategyV2.round_trip_evidence_top_candidates || [], [
      ["Cohort","signal_cohort"],
      ["Market","market_slug", v=>longText(v, 120)],
      ["Outcome","outcome"],
      ["Entry","entry_price", v=>fmtNum(v,4)],
      ["Latest bid","latest_bid", v=>fmtNum(v,4)],
      ["Exit","exit_price", v=>fmtNum(v,4)],
      ["Status","round_trip_status", v=>longText(v, 120)],
      ["Realized P&L","realized_pnl_usdc", fmtUsd],
      ["MTM P&L","mark_pnl_usdc", fmtUsd],
      ["Obs","observations"]
    ]) + `<div style="height:12px"></div><h3>Anchored near-misses</h3>` + table(strategyV2.top_anchored_rejections || [], [
      ["Family","family"],
      ["Market","market_slug", v=>longText(v, 120)],
      ["Outcome","outcome"],
      ["Anchor","anchor_fair_probability", v=>fmtNum(v,4)],
      ["Price","executable_price", v=>fmtNum(v,4)],
      ["Edge after penalty","risk_adjusted_anchor_edge", v=>fmtNum(v,4)],
      ["Blockers","blockers", v=>longText(v, 160)]
    ]) + `<div style="height:12px"></div><h3>Family coverage</h3>` + table(strategyV2.family_summary || [], [
      ["Family","family"],
      ["Rows","rows"],
      ["Anchored","anchored_rows"],
      ["Actionable","anchored_candidates"],
      ["Shadow","shadow_candidates"],
      ["Best edge","best_edge", v=>fmtNum(v,4)],
      ["Action","action", v=>longText(v, 120)]
    ]);
    document.getElementById("promotionReadiness").innerHTML = table(data.cohort_promotion_readiness?.cohorts || [], [
      ["Cohort","signal_cohort"],
      ["Promoted","promoted"],
      ["Fills","evidence_fills"],
      ["Need fills","fill_gap"],
      ["P&L","evidence_pnl_usdc", fmtUsd],
      ["Need P&L","pnl_gap_usdc", fmtUsd],
      ["Status","readiness_status"]
    ]);
    document.getElementById("promotionWatchlist").innerHTML = table(data.signal_cohort_pnl?.promotion_watchlist || [], [
      ["Cohort","signal_cohort"],
      ["Probation","probationary"],
      ["Probe cap","probationary_max_stake_usdc", fmtUsd],
      ["Score","promotion_ready_score", (v,row)=>`${v ?? 0}/${row.promotion_ready_checks ?? "?"}`],
      ["P&L","total_pnl_usdc", fmtUsd],
      ["ROI","roi", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Run-rate","monthly_run_rate_usdc", fmtUsd],
      ["Fills","buy_fills"],
      ["Settled","settled_fills"],
      ["Reason","promotion_reason"]
    ]);
    document.getElementById("shadowPromotionWatchlist").innerHTML = table(data.shadow_signal_cohort_pnl?.promotion_watchlist || [], [
      ["Cohort","signal_cohort"],
      ["Score","promotion_ready_score", (v,row)=>`${v ?? 0}/${row.promotion_ready_checks ?? "?"}`],
      ["Shadow P&L","shadow_total_pnl_usdc", fmtUsd],
      ["ROI","shadow_roi", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Run-rate","shadow_monthly_run_rate_usdc", fmtUsd],
      ["Fills","shadow_fills"],
      ["Settled","shadow_sell_fills"],
      ["Open","shadow_open_positions"]
    ]);
    document.getElementById("settlementWatch").innerHTML =
      `<div class="muted">Checks: ${data.shadow_settlement_watch?.settlement_checks ?? 0} - settled last cycle: ${data.shadow_settlement_watch?.settled_positions ?? 0}</div>` +
      table(data.shadow_settlement_watch?.next_open_settlements || [], [
        ["Close","close_time"],
        ["Minutes","minutes_until_close", v=>fmtNum(v,0)],
        ["Cohort","signal_cohort"],
        ["Market","market_slug"],
        ["Outcome","outcome"]
      ]);
    document.getElementById("cohorts").innerHTML = table(data.signal_cohort_pnl?.cohorts || [], [
      ["Cohort","signal_cohort"], ["Promoted","promoted"], ["Buy fills","buy_fills"], ["P&L","total_pnl_usdc", fmtUsd],
      ["ROI","roi", v=>fmtNum(Number(v) * 100, 2) + "%"], ["Evidence","promotion_evidence_source"],
      ["Shadow fills","shadow_fills"], ["Shadow P&L","shadow_total_pnl_usdc", fmtUsd],
      ["Open","shadow_open_positions"], ["Reason","promotion_reason"]
    ]);
    document.getElementById("shadowPositions").innerHTML = table(data.shadow_positions || [], [
      ["Scope","rule_scope"],
      ["Cohort","signal_cohort"],
      ["Market","question", (v,row)=>marketLabel(row)],
      ["Outcome","outcome"],
      ["Entry","entry_price", v=>fmtNum(v,4)],
      ["Mark","latest_mark_price", v=>fmtNum(v,4)],
      ["Stake","stake_usdc", fmtUsd],
      ["P&L","unrealised_pnl_usdc", fmtUsd],
      ["Return","return_pct", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Close","close_time"],
      ["Opened","opened_at"]
    ]);
    document.getElementById("shadowFills").innerHTML = table(data.shadow_fills || [], [
      ["Time","created_at"], ["Side","side"], ["Cohort","signal_cohort"],
      ["Market","question", (v,row)=>marketLabel(row)],
      ["Price","price", v=>fmtNum(v,4)], ["Notional","gross_notional_usdc", fmtUsd], ["Reason","reason"]
    ]);
    const wc = data.worldcup_validation_status || {};
    document.getElementById("worldcupValidation").innerHTML = facts([
      ["Status", wc.status],
      ["Winner rows", wc.worldcup_winner_rows],
      ["Fundamental coverage", wc.fundamental_coverage_pct == null ? "-" : fmtNum(wc.fundamental_coverage_pct, 1) + "%"],
      ["Bookmaker pass", wc.bookmaker_cross_check_pass],
      ["Bookmaker fail", wc.bookmaker_cross_check_fail],
      ["Microstructure pass", wc.microstructure_pass],
      ["Approved signals", wc.approved_signals],
      ["Main blocker", wc.main_blocker, v=>longText(v, 180)]
    ]) + `<div style="height:12px"></div>` + table(wc.top_rejection_reasons || [], [
      ["Count","count"],
      ["Rejected reason","reason", v=>longText(v, 220)]
    ]);
    const anchors = data.heartbeat?.independent_fundamentals || data.independent_anchor_status || {};
    document.getElementById("independentFundamentals").innerHTML = table([
      { anchor: "Sharp odds fetch", ...(anchors.sharp_odds_fetch || {}) },
      { anchor: "Sharp de-vig anchor", ...(anchors.sharp_anchor || {}) },
      { anchor: "Crypto target generator", ...(anchors.crypto_targets || {}) },
      { anchor: "Deribit crypto fundamental", ...(anchors.crypto_fundamental || {}) }
    ], [
      ["Anchor","anchor"],
      ["Status","status"],
      ["Rows","rows", (v,row)=>v ?? row.fundamental_rows ?? row.target_rows ?? row.rows_in ?? "-"],
      ["Markets","markets"],
      ["Rejected","fallback_rejected_rows", (v,row)=>v ?? row.skipped_incomplete_market_rows ?? "-"],
      ["Reject reasons","fallback_rejection_reasons", (v,row)=>longText(v || row.incomplete_market_samples || "", 180)],
      ["Output","output_file", (v,row)=>longText(v || row.output_path || row.fallback_rejections_path || "")],
      ["Blocker","blocker", v=>longText(v, 160)],
      ["Note","note"]
    ]);
    document.getElementById("edgeSearch").innerHTML = table(data.edge_strategy_search?.top_rules || [], [
      ["Rule","rule_value"], ["Promotable","promotable"], ["Holdout ROI","holdout_roi", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Dev ROI","dev_roi", v=>fmtNum(Number(v) * 100, 2) + "%"], ["Holdout rows","holdout_rows"],
      ["Markets","markets"], ["Reason","promotion_reason"]
    ]);
    document.getElementById("promotedRuleShadow").innerHTML = table(data.promoted_rule_shadow?.scans || [], [
      ["Scope","rule_scope"], ["Rule","rule_value"], ["Family","family"], ["Outcome","outcome"], ["Query","query"],
      ["Events","events"], ["Matching tokens","matching_tokens"], ["Candidates","candidates"], ["Rejected","rejected"]
    ]);
    const liquidity = data.liquidity_discovery || {};
    document.getElementById("liquidityDiscovery").innerHTML =
      `<div class="muted">Status: ${liquidity.status || "unknown"} - scanned ${liquidity.tokens_scanned ?? 0} tokens - ${liquidity.tradable_tokens ?? 0} currently pass liquidity filters</div>` +
      table(liquidity.model_target_queue || [], [
        ["Family","family"],
        ["Tradable","tradable_tokens"],
        ["Status","status"],
        ["Best rule","top_rule", v=>longText(v)],
        ["Holdout ROI","top_rule_holdout_roi", v=>fmtNum(Number(v) * 100, 2) + "%"],
        ["Evidence","top_rule_reason"],
        ["Recommendation","recommendation"]
      ]) +
      `<div style="height:12px"></div>` +
      table(liquidity.top_tradable || [], [
        ["Market","question", (v,row)=>marketLabel(row)],
        ["Family","family"],
        ["Outcome","outcome"],
        ["Bid","best_bid", v=>fmtNum(v,4)],
        ["Ask","best_ask", v=>fmtNum(v,4)],
        ["Spread","spread", v=>fmtNum(v,4)],
        ["Liquidity","liquidity", v=>fmtNum(v,2)],
        ["Close","close_time"]
      ]) +
      `<div style="height:12px"></div>` +
      table(liquidity.family_summary || [], [
        ["Family","family"],
        ["Tokens","tokens"],
        ["Tradable","tradable_tokens"],
        ["Max liquidity","max_liquidity", v=>fmtNum(v,2)],
        ["Min spread","min_spread", v=>fmtNum(v,4)]
      ]);
    document.getElementById("positions").innerHTML = table(data.positions, [
      ["Market","market_name", (v,row)=>marketLabel(row)], ["Avg entry","average_entry_price", v=>fmtNum(v,4)],
      ["Cost","cost_basis_usdc", fmtUsd], ["Qty","quantity", v=>fmtNum(v,2)], ["Status","status"], ["Updated","updated_at"]
    ]);
    document.getElementById("fills").innerHTML = table(data.recent_fills, [
      ["Time","created_at"], ["Side","side"], ["Market","market_name", (v,row)=>marketLabel(row)], ["Fill","fill_price", v=>fmtNum(v,4)],
      ["Qty","quantity", v=>fmtNum(v,2)], ["Notional","gross_notional_usdc", fmtUsd], ["Slippage","slippage_usdc", fmtUsd]
    ]);
    document.getElementById("signals").innerHTML = table(data.approved_signals, [
      ["Market","market_name", (v,row)=>marketLabel(row)], ["Price","executable_price", v=>fmtNum(v,4)], ["Edge","edge", v=>fmtNum(v,4)],
      ["Priority EV","expected_lower_bound_profit_usdc", fmtUsd], ["Stake","sizing_decision", fmtUsd], ["Confidence","confidence", v=>fmtNum(v,3)]
    ]);
  } catch (err) {
    document.getElementById("error").innerHTML = `<div class="error">Dashboard could not load: ${err.message}</div>`;
  }
}
load();
setInterval(load, 5000);
</script>
</body>
</html>
"""



def _slug_to_title(slug: str) -> str:
    slug = (slug or "").strip()
    if not slug:
        return ""
    slug = re.sub(r"-\d+$", "", slug)
    text = slug.replace("-", " ").strip()
    if not text:
        return ""
    text = text.title()
    return (
        text.replace("Fifa", "FIFA")
        .replace(" Usa ", " USA ")
        .replace(" Uefa ", " UEFA ")
        .replace(" Nba ", " NBA ")
        .replace(" Nfl ", " NFL ")
    )


def _market_label(row: dict[str, Any]) -> str:
    for key in ("market_name", "question"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    slug = str(row.get("market_slug") or "").strip()
    title = _slug_to_title(slug)
    if title:
        return title
    return str(row.get("market_id") or row.get("token_id") or "").strip()


def _lookup_keys(row: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for key in ("market_id", "token_id", "correlation_key"):
        value = str(row.get(key) or "").strip()
        if value:
            keys.append(value)
    return keys


def _build_market_lookup(*row_groups: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}
    for rows in row_groups:
        for row in rows:
            label = _market_label(row)
            slug = str(row.get("market_slug") or "").strip()
            question = str(row.get("question") or "").strip()
            if not label and not slug and not question:
                continue
            payload = {"market_name": label, "market_slug": slug, "question": question or label}
            for key in _lookup_keys(row):
                lookup.setdefault(key, payload)
    return lookup


def _enrich_market_names(rows: list[dict[str, Any]], lookup: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for row in rows:
        copy = dict(row)
        match = None
        for key in _lookup_keys(copy):
            if key in lookup:
                match = lookup[key]
                break
        if match:
            copy.setdefault("market_slug", match.get("market_slug", ""))
            if not str(copy.get("question") or "").strip():
                copy["question"] = match.get("question") or match.get("market_name") or ""
            copy["market_name"] = match.get("market_name") or _market_label(copy)
        else:
            copy["market_name"] = _market_label(copy)
        enriched.append(copy)
    return enriched


def _market_lookup_from_orders(order_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for order in order_rows:
        payload = order.get("source_signal_json") or "{}"
        if isinstance(payload, str):
            try:
                decoded = json.loads(payload)
            except (TypeError, ValueError, json.JSONDecodeError):
                decoded = {}
        else:
            decoded = payload if isinstance(payload, dict) else {}
        if isinstance(decoded, dict):
            merged = {**order, **decoded}
            rows.append(merged)
    return rows

def _enrich_shadow_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for row in rows:
        copy = dict(row)
        payload = copy.get("source_signal_json") or "{}"
        if isinstance(payload, str):
            try:
                decoded = json.loads(payload)
            except (TypeError, ValueError, json.JSONDecodeError):
                decoded = {}
        else:
            decoded = payload if isinstance(payload, dict) else {}
        if isinstance(decoded, dict):
            for key in ("close_time", "outcome", "rule_scope", "rule_value", "event_title"):
                if not str(copy.get(key) or "").strip() and str(decoded.get(key) or "").strip():
                    copy[key] = decoded.get(key)
            if not str(copy.get("question") or "").strip() and str(decoded.get("question") or "").strip():
                copy["question"] = decoded.get("question")
        enriched.append(copy)
    return enriched

def _last(rows: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    return rows[-n:] if len(rows) > n else rows


def _open_positions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if str(row.get("status", "")).lower() == "open"]


def _rejection_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(str(row.get("rejection_reason") or "unknown") for row in rows))


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _top_rejection_reasons(rejected: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    counts = Counter(str(row.get("rejection_reason") or "unknown") for row in rejected)
    return [{"reason": reason, "count": count} for reason, count in counts.most_common(limit)]


def _worldcup_validation_status(
    *,
    predictions: list[dict[str, Any]],
    approved_signals: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
) -> dict[str, Any]:
    worldcup_rows = [row for row in predictions if is_worldcup_winner_market(row)]
    worldcup_approved = [row for row in approved_signals if is_worldcup_winner_market(row)]
    worldcup_rejected = [row for row in rejected if is_worldcup_winner_market(row)]
    with_fundamental = [
        row
        for row in worldcup_rows
        if str(row.get("fundamental_probability") or "").strip()
        or str(row.get("haircut_fundamental_probability") or "").strip()
    ]
    bookmaker_pass = [row for row in worldcup_rows if _truthy(row.get("bookmaker_cross_check_pass"))]
    bookmaker_fail = [
        row
        for row in worldcup_rows
        if str(row.get("bookmaker_cross_check_pass") or "").strip().lower() == "false"
    ]
    micro_pass = [row for row in worldcup_rows if _truthy(row.get("microstructure_filter_pass"))]
    coverage = (len(with_fundamental) / len(worldcup_rows) * 100.0) if worldcup_rows else None
    if not worldcup_rows:
        status = "no_worldcup_winner_rows"
        main_blocker = "No World Cup winner rows are currently in the scored prediction set."
    elif not with_fundamental:
        status = "missing_bookmaker_fundamental"
        main_blocker = "World Cup rows exist, but none have bookmaker/fundamental probabilities attached."
    elif not bookmaker_pass:
        status = "no_cross_checked_edge"
        main_blocker = "Fundamental probabilities exist, but no row passes the bookmaker haircut cross-check."
    elif not worldcup_approved:
        status = "collecting_or_blocked_by_trade_gates"
        main_blocker = "Cross-checked World Cup rows exist, but none are approved by trading and promotion gates yet."
    else:
        status = "approved_signals_available"
        main_blocker = "Approved World Cup signals are available; monitor forward paper P&L by cohort."
    return {
        "status": status,
        "worldcup_winner_rows": len(worldcup_rows),
        "fundamental_rows": len(with_fundamental),
        "fundamental_coverage_pct": coverage,
        "bookmaker_cross_check_pass": len(bookmaker_pass),
        "bookmaker_cross_check_fail": len(bookmaker_fail),
        "microstructure_pass": len(micro_pass),
        "approved_signals": len(worldcup_approved),
        "rejected_signals": len(worldcup_rejected),
        "main_blocker": main_blocker,
        "top_rejection_reasons": _top_rejection_reasons(worldcup_rejected, limit=6),
    }


def _trade_diagnostics(
    *,
    predictions: list[dict[str, Any]],
    approved_signals: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    near_miss_candidates: list[dict[str, Any]],
    shadow_summary: dict[str, Any],
) -> dict[str, Any]:
    shadow_candidates = [row for row in predictions if _truthy(row.get("shadow_trade_candidate"))]
    shadow_candidates.sort(
        key=lambda row: safe_float(row.get("shadow_priority_score")) or 0.0,
        reverse=True,
    )
    near_miss_candidates = list(near_miss_candidates)
    near_miss_candidates.sort(
        key=lambda row: safe_float(row.get("near_miss_priority_score")) or 0.0,
        reverse=True,
    )
    quarantined = shadow_summary.get("quarantined_cohorts", []) if isinstance(shadow_summary, dict) else []
    if not isinstance(quarantined, list):
        quarantined = []
    top_reasons = _top_rejection_reasons(rejected)

    if approved_signals:
        main_blocker = "Approved paper signals are available for the broker."
        recommended_action = "Monitor fills and realised paper P&L."
    elif (safe_float(shadow_summary.get("shadow_candidates_seen")) or 0) > 0 and quarantined:
        main_blocker = "Fast-market shadow candidates exist, but current cohorts are quarantined or blocked by prior bad evidence."
        recommended_action = "Keep collecting shadow evidence and only allow tiny probes once a non-quarantined cohort qualifies."
    elif top_reasons:
        reason = str(top_reasons[0]["reason"])
        if "cohort_quarantined" in reason or "cohort_negative" in reason or "cohort_evidence" in reason:
            main_blocker = "Current candidates are blocked by negative or quarantined cohort evidence."
            recommended_action = "Do not probe this exact cohort until fresh shadow evidence clears the quarantine."
        elif "alpha lower-bound" in reason and "liquidity" in reason:
            main_blocker = "Most candidates fail both the lower-bound edge and liquidity gates."
            recommended_action = "Improve model selectivity or wait for cleaner liquidity before paper probing."
        elif "alpha lower-bound" in reason:
            main_blocker = "Model lower-bound edge is below the configured trading threshold."
            recommended_action = "Keep learning; do not force trades until edge survives uncertainty and cost penalties."
        elif "same-category validation" in reason:
            main_blocker = "The same-category validation gate lacks enough resolved labels."
            recommended_action = "Collect more resolved or live cohort evidence before promotion."
        else:
            main_blocker = f"Top rejection reason: {reason}"
            recommended_action = "Inspect top rejected signals and cohort evidence before changing gates."
    else:
        main_blocker = "No approved signals and no rejected-signal evidence is available yet."
        recommended_action = "Wait for the next full prediction cycle or inspect the live loop heartbeat."

    return {
        "main_blocker": main_blocker,
        "recommended_action": recommended_action,
        "approved_signals_count": len(approved_signals),
        "rejected_signals_count": len(rejected),
        "prediction_count": len(predictions),
        "near_miss_candidates_seen": len(near_miss_candidates),
        "near_miss_opened_this_cycle": shadow_summary.get("near_miss_opened_this_cycle"),
        "near_miss_open_positions": shadow_summary.get("near_miss_open_positions"),
        "shadow_candidates_seen": shadow_summary.get("shadow_candidates_seen"),
        "shadow_opened_this_cycle": shadow_summary.get("opened_this_cycle"),
        "shadow_open_positions": shadow_summary.get("open_positions"),
        "quarantined_cohort_count": len(quarantined),
        "top_rejection_reasons": top_reasons,
        "current_near_miss_candidates": near_miss_candidates[:12],
        "current_shadow_candidates": shadow_candidates[:12],
        "quarantined_cohorts": quarantined[:12],
    }


def _cohort_promotion_readiness(cfg: EngineConfig, signal_cohort_pnl: dict[str, Any]) -> dict[str, Any]:
    policy = cfg.raw.get("cohort_promotion", {}) or {}
    minimum_fills = int(policy.get("minimum_filled_orders", 5))
    minimum_pnl = float(policy.get("minimum_pnl_usdc", 0.0))
    cohorts = signal_cohort_pnl.get("cohorts", []) if isinstance(signal_cohort_pnl, dict) else []
    rows: list[dict[str, Any]] = []
    for cohort in cohorts if isinstance(cohorts, list) else []:
        if not isinstance(cohort, dict):
            continue
        fills = int(safe_float(cohort.get("buy_fills")) or 0)
        pnl = safe_float(cohort.get("total_pnl_usdc")) or 0.0
        promoted = str(cohort.get("promoted")).lower() == "true" or cohort.get("promoted") is True
        fill_gap = max(0, minimum_fills - fills)
        pnl_gap = max(0.0, minimum_pnl - pnl)
        if promoted:
            status = "promoted"
        elif fill_gap > 0:
            status = "needs_more_fills"
        elif pnl <= minimum_pnl:
            status = "needs_positive_pnl"
        else:
            status = "ready_for_promotion_review"
        rows.append(
            {
                "signal_cohort": cohort.get("signal_cohort", ""),
                "promoted": promoted,
                "evidence_fills": fills,
                "required_fills": minimum_fills,
                "fill_gap": fill_gap,
                "evidence_pnl_usdc": pnl,
                "required_pnl_usdc": minimum_pnl,
                "pnl_gap_usdc": pnl_gap,
                "readiness_status": status,
            }
        )
    rows.sort(
        key=lambda row: (
            row["promoted"],
            -int(row["fill_gap"]),
            float(row["evidence_pnl_usdc"]),
        ),
        reverse=True,
    )
    return {
        "minimum_filled_orders": minimum_fills,
        "minimum_pnl_usdc": minimum_pnl,
        "promoted_count": sum(1 for row in rows if row.get("promoted")),
        "cohorts": rows,
    }


def _shadow_settlement_watch(shadow_positions: list[dict[str, Any]], shadow_summary: dict[str, Any]) -> dict[str, Any]:
    now_dt = datetime.now(timezone.utc)
    upcoming: list[dict[str, Any]] = []
    total_pnl = 0.0
    for row in shadow_positions:
        total_pnl += safe_float(row.get("unrealised_pnl_usdc")) or 0.0
        close_time = str(row.get("close_time") or "").strip()
        parsed = parse_timestamp(close_time)
        if parsed is None:
            continue
        parsed = parsed.astimezone(timezone.utc)
        minutes = (parsed - now_dt).total_seconds() / 60.0
        upcoming.append(
            {
                "close_time": close_time,
                "minutes_until_close": minutes,
                "signal_cohort": row.get("signal_cohort", ""),
                "market_slug": row.get("market_slug", ""),
                "outcome": row.get("outcome", ""),
            }
        )
    upcoming.sort(key=lambda row: safe_float(row.get("minutes_until_close")) or 0.0)
    future = [row for row in upcoming if (safe_float(row.get("minutes_until_close")) or 0.0) >= 0]
    due = [row for row in upcoming if (safe_float(row.get("minutes_until_close")) or 0.0) < 0]
    return {
        "open_positions": len(shadow_positions),
        "positions_with_close_time": len(upcoming),
        "due_positions": len(due),
        "next_settlement_minutes": (safe_float(future[0].get("minutes_until_close")) if future else None),
        "next_open_settlements": (future or upcoming)[:12],
        "shadow_total_pnl_usdc": total_pnl,
        "settlement_checks": shadow_summary.get("settlement_checks"),
        "settled_positions": shadow_summary.get("settled_positions"),
        "settlement_reason_counts": shadow_summary.get("settlement_reason_counts", {}),
    }


def _independent_anchor_status(governance: Path) -> dict[str, Any]:
    """Expose the latest independent-anchor summaries even when the heartbeat is settlement-only."""
    sharp_fetch = read_json(governance / "sharp_odds_fetch_summary.json", default={}) or {}
    sharp_anchor = read_json(governance / "sharp_anchor_summary.json", default={}) or {}
    crypto_targets = read_json(governance / "crypto_targets_summary.json", default={}) or {}
    crypto = read_json(governance / "crypto_fundamental_summary.json", default={}) or {}
    components = {
        "sharp_odds_fetch": sharp_fetch if isinstance(sharp_fetch, dict) else {},
        "sharp_anchor": sharp_anchor if isinstance(sharp_anchor, dict) else {},
        "crypto_targets": crypto_targets if isinstance(crypto_targets, dict) else {},
        "crypto_fundamental": crypto if isinstance(crypto, dict) else {},
    }
    for name, component in components.items():
        if not component:
            continue
        rows = int(
            safe_float(
                component.get("rows")
                or component.get("fundamental_rows")
                or component.get("target_rows")
                or component.get("rows_in")
            )
            or 0
        )
        status = str(component.get("status") or "").lower()
        blocker = ""
        fallback_rejected = int(safe_float(component.get("fallback_rejected_rows")) or 0)
        incomplete_market_rows = int(safe_float(component.get("skipped_incomplete_market_rows")) or 0)
        if rows <= 0 and fallback_rejected:
            blocker = f"fallback odds rejected: {fallback_rejected} rows"
        elif rows <= 0 and incomplete_market_rows:
            blocker = f"incomplete odds markets rejected: {incomplete_market_rows} rows"
        elif status in {"error", "partial"}:
            blocker = f"{status}: {component.get('errors', '')}".strip()
        elif status in {"missing_api_key", "no_input", "no_targets", "no_terminal_targets"}:
            blocker = status
        elif rows <= 0 and status:
            blocker = "no usable rows"
        if blocker:
            component.setdefault("blocker", blocker)
    any_present = any(bool(component) for component in components.values())
    any_usable = any(
        str(component.get("status") or "").lower() in {"fetched", "built", "fallback_loaded"}
        and int(
            safe_float(
                component.get("rows")
                or component.get("fundamental_rows")
                or component.get("target_rows")
            )
            or 0
        ) > 0
        for component in components.values()
    )
    blockers = [
        f"{name}: {component.get('blocker')}"
        for name, component in components.items()
        if isinstance(component, dict) and component.get("blocker")
    ]
    return {
        "status": "usable" if any_usable else "setup_needed" if any_present else "missing",
        "main_blocker": blockers[0] if blockers else "",
        "blockers": blockers,
        **components,
    }


def _sorted_by_numeric(rows: list[dict[str, Any]], key: str, *, reverse: bool = True) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: safe_float(row.get(key)) if safe_float(row.get(key)) is not None else -999.0, reverse=reverse)


def _read_json_lenient(path: Path, default: Any = None) -> Any:
    payload = read_json(path, default=None)
    if payload is not None:
        return payload
    try:
        with Path(path).open("r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return default


def _strategy_v2_status(cfg: EngineConfig) -> dict[str, Any]:
    """Expose Strategy V2 anchored-edge research progress on the dashboard.

    This is observability only. Strategy V2 remains shadow-only until a separate
    governance/promotion review explicitly allows paper probation.
    """
    root = cfg.output_root / "polymarket_strategy_v2"
    report = read_json(root / "anchored_edge_report.json", default={}) or {}
    if not isinstance(report, dict):
        report = {}
    candidates = read_csv_rows(root / "anchored_edge_candidates.csv")
    persistence = read_csv_rows(root / "anchored_edge_persistence_log.csv")
    forward_evidence = read_json(root / "strategy_v2_forward_evidence.json", default={}) or {}
    if not isinstance(forward_evidence, dict):
        forward_evidence = {}
    forward_cohorts = read_csv_rows(root / "strategy_v2_cohort_forward_evidence.csv")
    forward_candidates = read_csv_rows(root / "strategy_v2_forward_evidence.csv")
    round_trip_evidence = read_json(root / "strategy_v2_round_trip_evidence.json", default={}) or {}
    if not isinstance(round_trip_evidence, dict):
        round_trip_evidence = {}
    round_trip_cohorts = read_csv_rows(root / "strategy_v2_round_trip_cohort_evidence.csv")
    round_trip_candidates = read_csv_rows(root / "strategy_v2_round_trip_evidence.csv")
    cycle_status = _read_json_lenient(cfg.path.parent / "work" / "strategy_v2_cycle_latest_status.json", default={}) or {}
    if not isinstance(cycle_status, dict):
        cycle_status = {}

    status_counts = report.get("status_counts") if isinstance(report.get("status_counts"), dict) else {}
    warnings = report.get("warnings") if isinstance(report.get("warnings"), dict) else {}
    family_summary = report.get("family_summary") if isinstance(report.get("family_summary"), list) else []
    top_anchored_rejections = report.get("top_anchored_rejections") if isinstance(report.get("top_anchored_rejections"), list) else []
    if not top_anchored_rejections:
        top_anchored_rejections = [
            row
            for row in candidates
            if str(row.get("status") or "") == "rejected" and safe_float(row.get("anchor_fair_probability")) is not None
        ]
        top_anchored_rejections = _sorted_by_numeric(top_anchored_rejections, "risk_adjusted_anchor_edge")[:25]

    shadow_candidates = [row for row in candidates if str(row.get("status") or "") == "shadow_candidate"]
    watchlist_candidates = [row for row in candidates if str(row.get("status") or "") == "watchlist"]
    anchored_rows = [row for row in candidates if safe_float(row.get("anchor_fair_probability")) is not None]
    top_shadow = _sorted_by_numeric(shadow_candidates, "risk_adjusted_anchor_edge")[:25]

    settings = report.get("settings") if isinstance(report.get("settings"), dict) else {}
    configured = cfg.raw.get("strategy_v2", {}) if isinstance(cfg.raw.get("strategy_v2"), dict) else {}
    promotion_min_candidates = safe_float(settings.get("promotion_min_candidates") or configured.get("promotion_min_candidates")) or 20.0
    promotion_min_settled = safe_float(settings.get("promotion_min_settled") or configured.get("promotion_min_settled")) or 10.0

    current_shadow_by_family = Counter(str(row.get("family") or "unknown") for row in shadow_candidates)
    shadow_log_by_family: dict[str, dict[str, Any]] = {}
    for row in persistence:
        if str(row.get("status") or "") != "shadow_candidate":
            continue
        family = str(row.get("family") or "unknown")
        bucket = shadow_log_by_family.setdefault(
            family,
            {
                "family": family,
                "shadow_entries": 0,
                "current_shadow_candidates": 0,
                "best_risk_adjusted_anchor_edge": None,
                "latest_logged_at_utc": "",
            },
        )
        bucket["shadow_entries"] += 1
        edge = safe_float(row.get("risk_adjusted_anchor_edge"))
        best = safe_float(bucket.get("best_risk_adjusted_anchor_edge"))
        if edge is not None and (best is None or edge > best):
            bucket["best_risk_adjusted_anchor_edge"] = edge
        logged_at = str(row.get("logged_at_utc") or "")
        if logged_at > str(bucket.get("latest_logged_at_utc") or ""):
            bucket["latest_logged_at_utc"] = logged_at

    for row in shadow_candidates:
        family = str(row.get("family") or "unknown")
        bucket = shadow_log_by_family.setdefault(
            family,
            {
                "family": family,
                "shadow_entries": 0,
                "current_shadow_candidates": 0,
                "best_risk_adjusted_anchor_edge": None,
                "latest_logged_at_utc": "",
            },
        )
        edge = safe_float(row.get("risk_adjusted_anchor_edge"))
        best = safe_float(bucket.get("best_risk_adjusted_anchor_edge"))
        if edge is not None and (best is None or edge > best):
            bucket["best_risk_adjusted_anchor_edge"] = edge

    promotion_progress = []
    for family, bucket in shadow_log_by_family.items():
        shadow_entries = int(bucket.get("shadow_entries") or 0)
        current_count = int(current_shadow_by_family.get(family, 0))
        remaining_entries = max(0, int(promotion_min_candidates) - shadow_entries)
        if remaining_entries > 0:
            status = "collect_more_shadow_entries"
        else:
            status = "needs_settled_pnl_and_anchor_methodology_review"
        promotion_progress.append(
            {
                **bucket,
                "current_shadow_candidates": current_count,
                "remaining_shadow_entries_to_review": remaining_entries,
                "min_shadow_entries_for_review": int(promotion_min_candidates),
                "min_settled_for_review": int(promotion_min_settled),
                "status": status,
            }
        )
    promotion_progress.sort(
        key=lambda row: (
            int(row.get("shadow_entries") or 0),
            safe_float(row.get("best_risk_adjusted_anchor_edge")) or -999.0,
        ),
        reverse=True,
    )

    top_blockers = report.get("top_blockers") if isinstance(report.get("top_blockers"), dict) else {}
    main_blocker = next(iter(top_blockers.keys()), "") if top_blockers else ""
    cycle_state = str(cycle_status.get("status") or "").lower()
    runtime_posture = "collecting_shadow_evidence"
    runtime_reason = "Strategy V2 cycle is ready to collect shadow evidence."
    if cycle_state == "skipped_high_memory":
        runtime_posture = "memory_paused"
        runtime_reason = str(cycle_status.get("reason") or "Strategy V2 paused by the local memory guard.")
    elif cycle_state in {"error", "failed"}:
        runtime_posture = "cycle_error"
        runtime_reason = str(cycle_status.get("reason") or cycle_status.get("error") or "Strategy V2 cycle reported an error.")
    elif cycle_state in {"", "missing"}:
        runtime_posture = "not_started"
        runtime_reason = "Strategy V2 cycle status is missing."
    return {
        "status": report.get("status") or ("missing" if not candidates else "ok"),
        "generated_at_utc": report.get("generated_at_utc"),
        "decision": report.get("decision") or "missing_report",
        "recommended_action": report.get("recommended_action") or "Run Strategy V2 anchored-edge scanner.",
        "cycle_status": cycle_status,
        "runtime_posture": runtime_posture,
        "runtime_reason": runtime_reason,
        "memory_used_percent": cycle_status.get("memory_used_percent"),
        "max_memory_percent": cycle_status.get("max_memory_percent"),
        "rows_scored": report.get("rows_scored") if report.get("rows_scored") is not None else len(candidates),
        "anchor_rows_loaded": report.get("anchor_rows_loaded"),
        "worldcup_validated_anchor_rows": report.get("worldcup_validated_anchor_rows"),
        "anchored_rows": report.get("anchored_rows") if report.get("anchored_rows") is not None else len(anchored_rows),
        "shadow_candidates": status_counts.get("shadow_candidate", len(shadow_candidates)),
        "watchlist_candidates": status_counts.get("watchlist", len(watchlist_candidates)),
        "missing_anchor_rows": warnings.get("missing_anchor_rows"),
        "main_blocker": main_blocker,
        "status_counts": status_counts,
        "top_blockers": top_blockers,
        "family_summary": family_summary,
        "top_shadow_candidates": top_shadow,
        "top_anchored_rejections": top_anchored_rejections[:25],
        "promotion_progress": promotion_progress[:25],
        "forward_evidence": forward_evidence,
        "forward_evidence_cohorts": forward_cohorts[:25],
        "forward_evidence_top_candidates": _sorted_by_numeric(forward_candidates, "mark_pnl_usdc")[:25],
        "round_trip_evidence": round_trip_evidence,
        "round_trip_evidence_cohorts": round_trip_cohorts[:25],
        "round_trip_evidence_top_candidates": _sorted_by_numeric(round_trip_candidates, "mark_pnl_usdc")[:25],
        "persistence_entries": len(persistence),
        "persistence_log": str(root / "anchored_edge_persistence_log.csv"),
        "shadow_only": True,
    }


def _payload_time(payload: Any) -> datetime | None:
    if not isinstance(payload, dict):
        return None
    for key in ("generated_at_utc", "timestamp_utc", "timestamp", "created_at_utc"):
        parsed = parse_timestamp(payload.get(key))
        if parsed is not None:
            return parsed.astimezone(timezone.utc)
    current = payload.get("current")
    if isinstance(current, dict):
        parsed = parse_timestamp(current.get("timestamp_utc"))
        if parsed is not None:
            return parsed.astimezone(timezone.utc)
    return None


def _freshest_payload(candidates: list[tuple[str, Any]]) -> tuple[str, dict[str, Any]]:
    usable = [(name, payload, _payload_time(payload)) for name, payload in candidates if isinstance(payload, dict) and payload]
    if not usable:
        return "", {}
    with_times = [item for item in usable if item[2] is not None]
    if with_times:
        name, payload, _ = max(with_times, key=lambda item: item[2] or datetime.min.replace(tzinfo=timezone.utc))
        return name, dict(payload)
    name, payload, _ = usable[0]
    return name, dict(payload)


def _age_seconds(payload: Any) -> float | None:
    parsed = _payload_time(payload)
    if parsed is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())


def _live_loop_status(heartbeat: dict[str, Any], cfg: EngineConfig) -> str:
    if not heartbeat:
        return "not_started"
    age = _age_seconds(heartbeat)
    live_settings = cfg.raw.get("local_live_loop", {}) or {}
    stale_after = safe_float(live_settings.get("heartbeat_stale_after_seconds"))
    if stale_after is None:
        ws_seconds = safe_float(heartbeat.get("websocket_seconds")) or 5.0
        prediction_seconds = safe_float(heartbeat.get("prediction_cycle_seconds")) or 15.0
        stale_after = max(90.0, ws_seconds * 6.0, prediction_seconds * 4.0)
    if age is not None and age > stale_after:
        return "stale"
    status = str(heartbeat.get("status") or "").lower()
    if status in {"ok", "running", "ran"}:
        return "live"
    if status in {"error", "failed"}:
        return "down"
    return "unknown"


def _strategy_v2_runtime_freshness(strategy_v2: dict[str, Any], live_loop_status: str) -> dict[str, Any]:
    """Summarise whether Strategy V2 is collecting evidence or safely paused.

    This is dashboard observability only. A memory pause is not a trading failure; it means the local
    guard did its job and avoided starting heavier model work while the laptop was under pressure.
    """
    posture = str(strategy_v2.get("runtime_posture") or "").strip()
    reason = str(strategy_v2.get("runtime_reason") or "").strip()
    if not posture:
        posture = "collecting_shadow_evidence" if live_loop_status == "live" else live_loop_status or "unknown"
    return {
        "strategy_v2_runtime_posture": posture,
        "strategy_v2_runtime_reason": reason,
        "strategy_v2_cycle_status": (strategy_v2.get("cycle_status") or {}).get("status")
        if isinstance(strategy_v2.get("cycle_status"), dict)
        else "",
        "strategy_v2_memory_percent": strategy_v2.get("memory_used_percent"),
        "strategy_v2_max_memory_percent": strategy_v2.get("max_memory_percent"),
    }


def _scoreboard_status(broker: dict[str, Any], target: dict[str, Any]) -> str:
    broker_equity = safe_float(broker.get("equity"))
    current = target.get("current") if isinstance(target, dict) else {}
    target_equity = safe_float(current.get("equity_usdc") if isinstance(current, dict) else None)
    if broker_equity is None or target_equity is None:
        return "missing_evidence"
    return "aligned" if abs(broker_equity - target_equity) <= 0.005 else "drift_detected"


def render_dashboard(cfg: EngineConfig, latest_report: dict[str, Any] | None = None) -> dict[str, Any]:
    out = cfg.output_root / "polymarket_dashboard"
    out.mkdir(parents=True, exist_ok=True)
    governance = cfg.governance_root
    predictions_root = cfg.output_root / "polymarket_predictions"
    portfolio_root = cfg.output_root / "polymarket_portfolio"
    shadow_root = cfg.output_root / "polymarket_shadow"

    forward = latest_report or read_json(governance / "forward_paper_cycle.json", default={}) or {}
    scanner_heartbeat = read_json(governance / "live_paper_loop_heartbeat.json", default={}) or {}
    local_live_heartbeat = read_json(governance / "local_live_loop_heartbeat.json", default={}) or {}
    heartbeat = local_live_heartbeat or scanner_heartbeat
    paper_summary = read_json(portfolio_root / "paper_trading_summary.json", default={}) or {}
    target_source, actual_target = _freshest_payload(
        [
            ("latest_report", forward.get("actual_profit_target") if isinstance(forward, dict) else {}),
            ("paper_profit_target_tracker", read_json(governance / "paper_profit_target_tracker.json", default={}) or {}),
        ]
    )
    broker_source, broker_summary = _freshest_payload(
        [
            ("latest_report", forward.get("broker") if isinstance(forward, dict) else {}),
            ("paper_trading_summary", paper_summary),
        ]
    )
    signal_cohort_pnl = read_json(governance / "signal_cohort_pnl.json", default={}) or {}
    shadow_summary = read_json(governance / "shadow_signal_cohort_pnl.json", default={}) or {}
    edge_strategy_search = read_json(governance / "edge_strategy_search_summary.json", default={}) or {}
    promoted_rule_shadow = read_json(governance / "promoted_rule_shadow_summary.json", default={}) or {}
    liquidity_discovery = read_json(governance / "liquidity_discovery_summary.json", default={}) or {}
    positions = _open_positions(read_csv_rows(portfolio_root / "positions.csv"))
    fills = _last(read_csv_rows(portfolio_root / "paper_fills.csv"), 50)
    shadow_positions = _enrich_shadow_rows(_open_positions(read_csv_rows(shadow_root / "shadow_positions.csv")))
    shadow_fills = _last(read_csv_rows(shadow_root / "shadow_fills.csv"), 50)
    orders = read_csv_rows(portfolio_root / "paper_orders.csv")
    predictions = read_csv_rows(predictions_root / "predictions.csv")
    signals = read_csv_rows(predictions_root / "trade_signals.csv")
    lookup = _build_market_lookup(signals, predictions, _market_lookup_from_orders(orders))
    positions = _enrich_market_names(positions, lookup)
    fills = _enrich_market_names(fills, lookup)
    signals = _enrich_market_names(signals, lookup)
    signals.sort(
        key=lambda row: safe_float(row.get("priority_score") or row.get("expected_lower_bound_profit_usdc")) or 0.0,
        reverse=True,
    )
    rejected = read_csv_rows(predictions_root / "rejected_signals.csv")
    near_miss_candidates = read_csv_rows(predictions_root / "near_miss_learning_candidates.csv")
    live_loop_status = _live_loop_status(heartbeat if isinstance(heartbeat, dict) else {}, cfg)
    strategy_v2_status = _strategy_v2_status(cfg)
    strategy_v2_runtime = _strategy_v2_runtime_freshness(strategy_v2_status, live_loop_status)

    payload = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "forward_paper_cycle": forward,
        "paper_broker_summary": broker_summary,
        "heartbeat": heartbeat,
        "local_live_heartbeat": local_live_heartbeat,
        "scanner_heartbeat": scanner_heartbeat,
        "actual_profit_target": actual_target,
        "evidence_freshness": {
            "broker_source": broker_source,
            "broker_generated_at_utc": broker_summary.get("generated_at_utc"),
            "target_source": target_source,
            "target_generated_at_utc": actual_target.get("generated_at_utc"),
            "live_loop_status": live_loop_status,
            "live_heartbeat_age_seconds": _age_seconds(heartbeat),
            "scoreboard_status": _scoreboard_status(broker_summary, actual_target),
            **strategy_v2_runtime,
        },
        "signal_cohort_pnl": signal_cohort_pnl,
        "cohort_promotion_readiness": _cohort_promotion_readiness(cfg, signal_cohort_pnl),
        "shadow_signal_cohort_pnl": shadow_summary,
        "shadow_settlement_watch": _shadow_settlement_watch(shadow_positions, shadow_summary),
        "independent_anchor_status": _independent_anchor_status(governance),
        "strategy_v2": strategy_v2_status,
        "edge_strategy_search": edge_strategy_search,
        "promoted_rule_shadow": promoted_rule_shadow,
        "liquidity_discovery": liquidity_discovery,
        "positions": positions,
        "recent_fills": fills,
        "shadow_positions": shadow_positions,
        "shadow_fills": shadow_fills,
        "approved_signals": signals[:50],
        "rejection_counts": _rejection_counts(rejected),
        "worldcup_validation_status": _worldcup_validation_status(
            predictions=predictions,
            approved_signals=signals,
            rejected=rejected,
        ),
        "trade_diagnostics": _trade_diagnostics(
            predictions=predictions,
            approved_signals=signals,
            rejected=rejected,
            near_miss_candidates=near_miss_candidates,
            shadow_summary=shadow_summary,
        ),
    }
    write_json(out / "dashboard_data.json", payload)
    (out / "index.html").write_text(HTML, encoding="utf-8")
    return {
        "status": "ok",
        "dashboard_dir": str(out),
        "dashboard_file": str(out / "index.html"),
        "dashboard_data": str(out / "dashboard_data.json"),
    }
