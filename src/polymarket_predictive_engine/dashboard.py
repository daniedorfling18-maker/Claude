from __future__ import annotations

from collections import Counter
import ctypes
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from .config import EngineConfig
from .discovery_policy import partition_active_queries, primary_hypothesis_targets
from .utils import now_utc, parse_timestamp, read_csv_rows, read_json, safe_float, serialize_value, write_json
from .worldcup_validation import is_worldcup_winner_market


DASHBOARD_PAYLOAD_LIST_LIMIT = 50

# The price-action model summary is re-scored by the 6-hourly VPS governance refresh plus the
# live loop's optimize pass (every ~10 iterations), so a healthy gap can approach six hours.
# The old 900s threshold flagged "Model scoring is stale" between perfectly healthy refreshes
# (observed 2026-07-08); 6h cadence + 2h grace means red now signals a genuinely missed refresh.
MODEL_SCORING_STALE_AFTER_SECONDS = 8 * 3600


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
    .card.tight { min-height:92px; }
    .label { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:0.08em; }
    .value { margin-top:8px; font-size:clamp(18px,2.2vw,26px); font-weight:750; letter-spacing:-0.03em; overflow-wrap:anywhere; }
    .value.good { color:var(--good); } .value.bad { color:var(--bad); } .value.warn { color:var(--warn); }
    section { padding:16px; margin-top:16px; overflow:hidden; }
    section.primary { border-color:rgba(70,211,154,0.35); background:linear-gradient(180deg,rgba(70,211,154,0.09),rgba(255,255,255,0.025)); }
    .decisionHero { display:grid; grid-template-columns:1.1fr 2fr; gap:14px; align-items:stretch; margin-bottom:14px; }
    .decisionStatus { border:1px solid rgba(255,255,255,0.12); border-radius:16px; padding:16px; background:rgba(0,0,0,0.22); min-width:0; }
    .decisionStatus .statusLabel { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:0.08em; }
    .decisionStatus .statusValue { margin-top:8px; font-size:clamp(24px,4vw,38px); line-height:1; font-weight:850; letter-spacing:-0.04em; overflow-wrap:anywhere; }
    .decisionStatus.good { border-color:rgba(70,211,154,0.5); box-shadow:0 0 0 1px rgba(70,211,154,0.12) inset; }
    .decisionStatus.good .statusValue { color:var(--good); }
    .decisionStatus.warn { border-color:rgba(255,209,102,0.45); box-shadow:0 0 0 1px rgba(255,209,102,0.10) inset; }
    .decisionStatus.warn .statusValue { color:var(--warn); }
    .decisionStatus.bad { border-color:rgba(255,107,122,0.5); box-shadow:0 0 0 1px rgba(255,107,122,0.12) inset; }
    .decisionStatus.bad .statusValue { color:var(--bad); }
    .decisionText { border:1px solid rgba(255,255,255,0.08); border-radius:16px; padding:16px; background:rgba(255,255,255,0.035); color:#d8e5f8; min-width:0; }
    .decisionText .headline { font-size:18px; font-weight:760; margin-bottom:8px; }
    .decisionText .body { color:#c8d8ef; overflow-wrap:anywhere; }
    .decisionGrid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; margin:12px 0; }
    .decisionTile { border:1px solid rgba(255,255,255,0.08); background:rgba(255,255,255,0.035); border-radius:12px; padding:11px; min-width:0; }
    .decisionTile .tileLabel { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:0.08em; margin-bottom:5px; }
    .decisionTile .tileValue { color:#d8e5f8; font-weight:720; overflow-wrap:anywhere; }
    .sectionLead { color:#c8d8ef; margin:-3px 0 12px; }
    .alertGrid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; margin-top:12px; }
    .alert { border:1px solid rgba(255,255,255,0.09); border-left:4px solid var(--warn); border-radius:14px; padding:12px; background:rgba(0,0,0,0.18); }
    .alert.good { border-left-color:var(--good); }
    .alert.bad { border-left-color:var(--bad); }
    .alert .title { font-weight:760; margin-bottom:4px; }
    .alert .body { color:#c8d8ef; overflow-wrap:anywhere; }
    details.legacy { margin-top:16px; border:1px solid var(--line); border-radius:18px; background:rgba(255,255,255,0.03); padding:12px 16px 16px; }
    details.legacy > summary { cursor:pointer; color:#d8e5f8; font-weight:760; }
    details.legacy > summary span { display:block; color:var(--muted); font-weight:450; margin-top:4px; }
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
    .actionList { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
    .actionItem { border:1px solid rgba(255,255,255,0.09); border-radius:14px; padding:12px; background:rgba(255,255,255,0.035); min-width:0; }
    .actionItem .rank { display:inline-flex; width:24px; height:24px; border-radius:999px; align-items:center; justify-content:center; margin-right:8px; background:rgba(255,255,255,0.08); color:#c8d8ef; font-weight:800; }
    .actionItem .title { font-weight:780; color:#ecf3ff; margin-bottom:6px; }
    .actionItem .meta { color:var(--muted); margin-top:7px; font-size:12px; overflow-wrap:anywhere; }
    .badge { display:inline-flex; align-items:center; border:1px solid rgba(255,255,255,0.12); border-radius:999px; padding:3px 8px; font-size:12px; margin:0 6px 6px 0; color:#d8e5f8; background:rgba(255,255,255,0.04); }
    .badge.good { border-color:rgba(70,211,154,0.35); color:var(--good); }
    .badge.warn { border-color:rgba(255,209,102,0.35); color:var(--warn); }
    .badge.bad { border-color:rgba(255,107,122,0.35); color:var(--bad); }
    details.expand summary { cursor:pointer; color:#d8e5f8; }
    details.expand summary::marker { color:var(--muted); }
    .fullText { margin-top:8px; padding:8px; border-radius:10px; background:rgba(0,0,0,0.22); color:#c8d8ef; white-space:pre-wrap; overflow-wrap:anywhere; }
    .error { color:var(--bad); padding:16px; border:1px solid rgba(255,107,122,0.35); border-radius:14px; background:rgba(255,107,122,0.08); }
    @media (max-width: 980px) { .grid, .two, .facts, .alertGrid, .decisionHero, .decisionGrid, .actionList { grid-template-columns:1fr; } header { flex-direction:column; } table { min-width:640px; } }
  </style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>Polymarket Paper Trading</h1>
      <div class="sub">Decision cockpit - trade/no-trade, blocker, next evidence target, model gate, and paper P&L - auto-refreshes every 5 seconds</div>
    </div>
    <div class="pill"><span id="statusDot" class="dot"></span><span id="statusText">Loading...</span></div>
  </header>
  <div id="error"></div>
  <div class="grid" id="cards"></div>
  <section class="primary"><h2>Canonical operating state</h2><div id="operatingState"></div></section>
  <section class="primary"><h2>Executor live-ops control plane</h2><div id="executorOps"></div></section>
  <section class="primary"><h2>Decision cockpit</h2><div id="decisionCockpit"></div></section>
  <section><h2>Action board</h2><div id="actionBoard"></div></section>
  <section><h2>Evidence funnel</h2><div id="evidenceFunnel"></div></section>
  <section><h2>Sharp sports edge funnel</h2><div id="sharpSportsFunnel"></div></section>
  <section><h2>H2 Dutch consistency (exact)</h2><div id="dutchArbWatch"></div></section>
  <section><h2>Longshot-bias shadow lane</h2><div id="longshotBias"></div></section>
  <section><h2>Performance factsheet</h2><div id="performanceFactsheet"></div></section>
  <div class="two">
    <section><h2>$100/month price-change route</h2><div id="goalPlan"></div></section>
    <section><h2>Model & evidence health</h2><div id="modelHealth"></div></section>
  </div>
  <details class="legacy"><summary>Decision evidence drill-down<span>Decision-useful supporting detail. Open this when you want to audit why the cockpit says trade, learn, wait, or stop.</span></summary>
  <div class="two">
    <section><h2>Oversight cockpit</h2><div id="oversightCockpit"></div></section>
    <section><h2>Live evidence cycle</h2><div id="cycle"></div></section>
  </div>
  <section><h2>Trading signal audit</h2><div id="tradeSignalAudit"></div></section>
  <section><h2>Closing-line value (CLV)</h2><div id="closingLineValue"></div></section>
  <section><h2>Smart-flow CLV</h2><div id="smartFlowClv"></div></section>
  <section><h2>Edge attribution</h2><div id="edgeAttribution"></div></section>
  <section><h2>World Cup validation layer</h2><div id="worldcupValidation"></div></section>
  <section><h2>Actual paper P&L</h2><div id="actualTarget"></div></section>
  <section><h2>Portfolio risk</h2><div id="portfolioRisk"></div></section>
  </details>
  <details class="legacy"><summary>Deep diagnostics and rejected-candidate audit<span>Raw telemetry is still here for debugging, but the decision cockpit above is the default trading view.</span></summary>
  <section><h2>Why no trade?</h2><div id="tradeDiagnostics"></div></section>
  <section><h2>Strategy V2 anchored edge</h2><div id="strategyV2"></div></section>
  <section><h2>Fast price-action scout</h2><div id="priceActionScout"></div></section>
  <section><h2>Microstructure edge lab</h2><div id="microstructureLab"></div></section>
  <section><h2>Algo replay lab</h2><div id="algoReplay"></div></section>
  <section><h2>Algo sweep lab</h2><div id="algoSweep"></div></section>
  <section><h2>Price-action prediction model</h2><div id="priceActionModel"></div></section>
  <section><h2>Quant research stack</h2><div id="quantResearch"></div></section>
  <section><h2>Price-action feedback loop</h2><div id="priceActionFeedback"></div></section>
  <div class="two">
    <section><h2>Promotion readiness</h2><div id="promotionReadiness"></div></section>
    <section><h2>Edge promotion watchlist</h2><div id="promotionWatchlist"></div></section>
  </div>
  <section><h2>Independent model anchors</h2><div id="independentFundamentals"></div></section>
  <section><h2>Liquidity discovery</h2><div id="liquidityDiscovery"></div></section>
  <section><h2>Open positions</h2><div id="positions"></div></section>
  <section><h2>Recent fills</h2><div id="fills"></div></section>
  <section><h2>Approved signals currently queued/scored</h2><div id="signals"></div></section>
  </details>
  <details class="legacy"><summary>Slow / legacy evidence panels<span>Kept for audit trail, but no longer the first thing to watch for live paper-trading decisions.</span></summary>
  <div class="two">
    <section><h2>Settlement watch</h2><div id="settlementWatch"></div></section>
    <section><h2>Shadow edge watchlist</h2><div id="shadowPromotionWatchlist"></div></section>
  </div>
  <section><h2>Signal cohort validation</h2><div id="cohorts"></div></section>
  <section><h2>Open shadow positions</h2><div id="shadowPositions"></div></section>
  <section><h2>Recent shadow fills</h2><div id="shadowFills"></div></section>
  </details>
  <details class="legacy"><summary>Historical research panels<span>Useful for provenance, not the live cockpit. Open only when auditing old rule evidence.</span></summary>
  <section><h2>Historical edge search</h2><div id="edgeSearch"></div></section>
  <section><h2>Historical rule live shadow scan</h2><div id="promotedRuleShadow"></div></section>
  </details>
</main>
<script>
const fmtUsd = (v) => v === null || v === undefined || v === "" || Number.isNaN(Number(v)) ? "-" : "$" + Number(v).toFixed(2);
const fmtNum = (v, d=4) => v === null || v === undefined || v === "" || Number.isNaN(Number(v)) ? "-" : Number(v).toFixed(d);
const ageSeconds = (v) => {
  const t = Date.parse(v || "");
  return Number.isNaN(t) ? null : Math.max(0, (Date.now() - t) / 1000);
};
const fmtAge = (seconds) => {
  if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) return "-";
  const s = Number(seconds);
  if (s < 90) return Math.round(s) + "s";
  if (s < 7200) return Math.round(s / 60) + "m";
  return fmtNum(s / 3600, 1) + "h";
};
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
  const preview = text.slice(0, Math.max(24, Math.floor(limit * 0.58))) + " ... " + text.slice(-Math.max(16, Math.floor(limit * 0.26)));
  return `<details class="expand"><summary title="${escapeHtml(text)}">${escapeHtml(preview)}</summary><div class="fullText">${escapeHtml(text)}</div></details>`;
};
const joinText = (v) => longText(Array.isArray(v) ? v.join(", ") : v);
const short = longText;
const joinList = joinText;
const marketLabel = (row) => longText(row?.market_name || row?.question || row?.market_slug || row?.market_id || row?.token_id);
const plain = (v) => escapeHtml(asText(v));
const asNumber = (v, fallback=0) => Number.isFinite(Number(v)) ? Number(v) : fallback;
const limitRows = (rows, max=6) => Array.isArray(rows) ? rows.slice(0, max) : [];
function card(label, value, cls="") { return `<div class="card"><div class="label">${escapeHtml(label)}</div><div class="value ${cls}">${value}</div></div>`; }
function decisionCards(rows) {
  if (!Array.isArray(rows) || !rows.length) return "";
  return rows.map(row => `
    <div class="card">
      <div class="label">${escapeHtml(row.label || "Decision metric")}</div>
      <div class="value ${escapeHtml(row.severity || "")}">${longText(row.value ?? "-", 120)}</div>
      <div class="muted">${longText(row.decision_use || row.why_it_matters || "", 170)}</div>
    </div>`).join("");
}
function table(rows, columns) {
  if (!rows || !rows.length) return `<div class="muted">No rows yet.</div>`;
  return `<div class="tableWrap"><table><thead><tr>${columns.map(c=>`<th>${escapeHtml(c[0])}</th>`).join("")}</tr></thead><tbody>${rows.map(row=>`<tr>${columns.map(c=>`<td>${c[2] ? c[2](row[c[1]], row) : plain(row[c[1]])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}
function titledTable(title, rows, columns, max=6) {
  const subset = limitRows(rows, max);
  if (!subset.length) return "";
  return `<div style="height:12px"></div><h3>${escapeHtml(title)}</h3>` + table(subset, columns);
}
function facts(rows) {
  return `<div class="facts">${rows.map(row => `<div class="fact"><div class="label">${escapeHtml(row[0])}</div><div class="factValue">${row[2] ? row[2](row[1]) : longText(row[1])}</div></div>`).join("")}</div>`;
}
function alertBox(title, body, cls="warn") {
  return `<div class="alert ${cls}"><div class="title">${escapeHtml(title)}</div><div class="body">${body}</div></div>`;
}
function badge(text, cls="") {
  return `<span class="badge ${escapeHtml(cls)}">${escapeHtml(text)}</span>`;
}
function actionList(rows) {
  if (!rows || !rows.length) return `<div class="muted">No current action list.</div>`;
  return `<div class="actionList">${rows.map((row, idx) => `
    <div class="actionItem">
      <div class="title"><span class="rank">${idx + 1}</span>${escapeHtml(row.title || row.action || "Action")}</div>
      <div>${longText(row.body || row.why || row.reason || "-", 280)}</div>
      <div class="meta">${escapeHtml(row.owner || "bot")} · ${escapeHtml(row.success_metric || row.measure || "measure outcome evidence")}</div>
    </div>`).join("")}</div>`;
}
const ageClass = (seconds, warn=300, bad=900) => seconds === null || seconds === undefined ? "warn" : Number(seconds) > bad ? "bad" : Number(seconds) > warn ? "warn" : "good";
async function load() {
  try {
    const res = await fetch("dashboard_data.json?ts=" + Date.now());
    if (!res.ok) throw new Error("dashboard_data.json returned " + res.status);
    const data = await res.json();
    document.getElementById("error").innerHTML = "";
    const paper = data.forward_paper_cycle?.paper || data.forward_paper_cycle || {};
    const tradingAccount = data.paper_trading_account || {};
    const broker = tradingAccount.broker || data.paper_broker_summary || data.forward_paper_cycle?.broker || paper.broker || {};
    const target = data.actual_profit_target || data.forward_paper_cycle?.actual_profit_target || {};
    const monthly = data.forward_paper_cycle?.monthly_profit_target || {};
    const diag = data.trade_diagnostics || {};
    const strategyV2 = data.strategy_v2 || {};
    const roundTrip = strategyV2.round_trip_evidence || {};
    const roundTripPnl = roundTrip.realized_pnl_usdc ?? roundTrip.total_mark_pnl_usdc;
    const priceScout = data.price_action_scout || {};
    const microstructure = data.price_action_microstructure || {};
    const algoReplay = data.algo_replay || {};
    const algoReplaySummaries = Array.isArray(algoReplay.summaries) ? algoReplay.summaries : [];
    const bestReplay = algoReplay.best_strategy || {};
    const algoSweep = data.algo_sweep || {};
    const priceActionModel = data.price_action_model || {};
    const quantResearch = data.quant_research_status || {};
    const alphaBridge = data.mispricing_alpha_bridge || {};
    const closingLine = data.closing_line_value || {};
    const smartFlowClv = data.h3_smart_flow || {};
    const legacySmartFlowClv = data.smart_flow_clv_legacy || {};
    const h2Dutch = data.h2_dutch || {};
    const dutchArb = data.dutch_arb || {};
    const longshotBias = data.longshot_bias || data.forward_paper_cycle?.longshot_bias || {};
    const performanceFactsheet = data.performance_factsheet || {};
    const operatingState = data.operating_state || {};
    const executorStatus = data.executor_status || operatingState.executor_status || {};
    const degradedWatchdog = data.degraded_state_watchdog || operatingState.degraded_state_watchdog || {};
    const deployAcceptance = data.deploy_acceptance || operatingState.deploy_acceptance || {};
    const edgeAttribution = data.edge_attribution || {};
    const riskState = data.risk_state || {};
    const portfolioRisk = riskState.portfolio_risk || {};
    const priceActionPaper = data.price_action_paper_signals || {};
    const currentHistScan = priceActionPaper.current_historical_analogue_scan || {};
    const historicalBreadthScan = priceActionPaper.historical_breadth_scan || {};
    const researchFocus = data.research_focus || {};
    const currentPositiveAnalogues = researchFocus.price_action_current_positive_analogues || {};
    const sideMissingAnalogues = researchFocus.price_action_side_missing_analogues || {};
    const paperConfirmationBlockers = researchFocus.price_action_paper_confirmation_blockers || {};
    const priceActionFeedback = data.price_action_feedback || {};
    const goalPlan = data.paper_profit_goal_plan || {};
    const priceActionGoal = goalPlan.price_action_goal_state || {};
    const paperRoundTripSummary = data.paper_round_trip_summary || {};
    const quoteAuditCohorts = (Array.isArray(paperRoundTripSummary.baseline_quote_audit_by_cohort) && paperRoundTripSummary.baseline_quote_audit_by_cohort.length)
      ? paperRoundTripSummary.baseline_quote_audit_by_cohort
      : (paperRoundTripSummary.quote_audit_by_cohort || []);
    const tradeSignalAudit = data.trade_signal_audit || {};
    const decisionSummary = data.decision_useful_summary || {};
    const evidenceFunnel = data.evidence_funnel || {};
    const sharpSportsFunnel = data.sharp_sports_funnel || {};
    const deploymentHealth = data.deployment_health || {};
    const probeExitWatch = data.paper_probe_exit_watch || {};
    const paperMaintenance = data.paper_maintenance || {};
    const paperMaintenanceTask = data.paper_maintenance_task || {};
    const shadowResearch = data.shadow_research_cycle || {};
    const priceScoutPnl = priceScout.realized_pnl_usdc ?? priceScout.total_mark_pnl_usdc;
    const freshness = data.evidence_freshness || {};
    const oversight = data.oversight_status || {};
    const rawLive = data.local_live_heartbeat || data.heartbeat || {};
    const liveStatus = freshness.live_loop_status || oversight.live_loop_status || "unknown";
    const legacyLiveActive = liveStatus === "live";
    const live = legacyLiveActive ? rawLive : {};
    const scanner = data.scanner_heartbeat || {};
    const discovery = live.discovery || {};
    const resourceGuard = live.resource_guard || {};
    const currentScan = discovery.scan || {};
    const lastScan = discovery.last_scan || {};
    const fastUpdown = discovery.last_fast_updown || discovery.fast_updown || {};
    const websocket = live.websocket || data.websocket_summary || {};
    const websocketFeatures = live.websocket_features || data.websocket_feature_summary || {};
    const ingest = live.ingest || {};
    const legacyFullCycle = freshness.legacy_full_cycle || {};
    const status = target.status || data.forward_paper_cycle?.status || "unknown";
    const dashboardAge = ageSeconds(data.generated_at_utc);
    const researchStatus = oversight.shadow_research_status || shadowResearch.effective_status || shadowResearch.status || "unknown";
    const researchAge = oversight.shadow_research_age_seconds ?? shadowResearch.age_seconds;
    const runPosture = legacyLiveActive ? (freshness.strategy_v2_runtime_posture || liveStatus) : (researchStatus === "ok" || researchStatus === "completed" || researchStatus === "success" ? "active_shadow_research" : researchStatus);
    const oversightState = oversight.status || "unknown";
    const researchGood = ["running", "ok", "completed", "success"].includes(researchStatus);
    const good = oversightState === "ok" || (oversightState === "unknown" && researchGood);
    const guarded = legacyLiveActive && (runPosture === "memory_paused" || runPosture === "guard_paused");
    const dashboardWarnAfterSeconds = Number(data.dashboard_warn_after_seconds || 900);
    const dashboardStaleAfterSeconds = Number(data.dashboard_stale_after_seconds || 1800);
    const dashboardStale = dashboardAge !== null && dashboardAge > dashboardStaleAfterSeconds;
    const bad = dashboardStale || oversightState === "bad" || researchStatus === "stale";
    const liveAge = freshness.live_heartbeat_age_seconds ?? ageSeconds(live.generated_at_utc);
    const modelAge = ageSeconds(priceActionModel.generated_at_utc);
    const signalAge = ageSeconds(priceActionPaper.generated_at_utc || priceActionPaper.signal_generated_at_utc);
    const validationGap = priceActionModel.validation_gap || {};
    const validationGapActive = oversight.validation_gap_active ?? (validationGap.state === "needs_positive_validation_examples" || (Number(priceActionModel.train_positive_targets || 0) > 0 && Number(priceActionModel.validation_positive_targets || 0) <= 0));
    const modelStale = modelAge !== null && modelAge > Number(oversight.model_stale_after_seconds || 28800);
    const approvedSignals = Number(oversight.approved_signal_count ?? data.forward_paper_cycle?.signals_approved ?? priceActionPaper.signals ?? diag.approved_signals_count ?? 0);
    const selectedQueries = currentScan.scan_plan?.selected_queries || lastScan.scan_plan?.selected_queries || scanner.scan?.scan_plan?.selected_queries || scanner.scan?.queries || [];
    const injectedQueries = currentScan.scan_plan?.injected_research_focus_queries || lastScan.scan_plan?.injected_research_focus_queries || scanner.scan?.scan_plan?.injected_research_focus_queries || [];
    const activeDiscoveryQueries = values => (Array.isArray(values) ? values : []).filter(value => {
      const normalized = String(value || "").toLowerCase().split("_").join(" ").split("/").join(" ").split("-").join(" ");
      const compact = normalized.split(" ").join("");
      return !compact.includes("updown") && !compact.includes("upordown");
    });
    const adaptiveQueries = activeDiscoveryQueries(data.research_focus?.collection_queries || data.liquidity_discovery?.settings?.adaptive_collection_queries || priceActionFeedback.collection_queries || priceActionGoal.collection_queries || validationGap.collection_queries || []);
    const displayedQueries = selectedQueries.length ? selectedQueries : adaptiveQueries;
    const brokerNeedsWork = Boolean(priceActionPaper.broker_refresh_needed || priceActionPaper.pending_broker_signals || priceActionPaper.pending_broker_confirmation_signals || priceActionPaper.pending_broker_exit_probes || approvedSignals > 0);
    const brokerRefreshLabel = priceActionPaper.broker_refresh_needed ? `${priceActionPaper.pending_broker_signals ?? 0} pending` : brokerNeedsWork ? "fresh" : "idle";
    const strategyGuardReason = (!oversight.strategy_v2_memory_pause_obsolete && legacyLiveActive) ? (freshness.strategy_v2_runtime_reason || strategyV2.runtime_reason || "") : "";
    const maintenanceFresh = Number(paperMaintenance.age_seconds ?? 999999) <= 900;
    const maintenanceInstalled = !["not_installed_or_unknown", "disabled", "not_started", ""].includes(String(paperMaintenanceTask.status || "").toLowerCase());
    const showMaintenance = maintenanceFresh && (maintenanceInstalled || paperMaintenance.status === "running" || paperMaintenance.status === "ok");
    const oversightAlerts = Array.isArray(oversight.alerts) && oversight.alerts.length
      ? oversight.alerts.map(item => alertBox(item.title || "Oversight alert", longText(item.body || "-", 260), item.severity || "warn"))
      : [];
    if (dashboardStale) oversightAlerts.push(alertBox("Dashboard snapshot is stale", `The displayed file is ${fmtAge(dashboardAge)} old. Refresh the dashboard generator before trusting signal counts.`, "bad"));
    if (String(deployAcceptance.status || "").toUpperCase() === "FAIL") oversightAlerts.push(alertBox("Latest deployment failed acceptance", `One or more real-data component contracts failed. Target ${longText(deployAcceptance.target_deploy_sha || "unknown", 20)} remains reversible to ${longText(deployAcceptance.rollback_ref || "unknown", 20)}; inspect the operating-state row before trusting this release.`, "bad"));
    if (String(degradedWatchdog.status || "") === "incident") oversightAlerts.push(alertBox("Persistent degraded-state incident", `${asNumber(degradedWatchdog.active_incident_count)} active incident(s). Fail-closed states remain unchanged; inspect the watchdog panel.`, "bad"));
    if (String(executorStatus.status || "").toUpperCase() === "ALERT") oversightAlerts.push(alertBox("Executor live-ops alert", `${asNumber(executorStatus.owner_alert_count)} registered executor alert(s) are active. Inspect the executor control plane; this dashboard cannot cancel or trade.`, "bad"));
    if (["needs_attention", "container_unversioned"].includes(String(deploymentHealth.status || ""))) oversightAlerts.push(alertBox("Deployment health needs attention", longText(deploymentHealth.summary || "Deployment metadata is incomplete or stale.", 260), "warn"));
    if (!oversightAlerts.length && modelStale) oversightAlerts.push(alertBox("Model scoring is stale", `Price-action model summary is ${fmtAge(modelAge)} old. Re-score/retrain before promoting any new paper signals.`, "bad"));
    if (!oversightAlerts.length && validationGapActive) oversightAlerts.push(alertBox("Model needs positive validation examples", `Train positives: ${plain(priceActionModel.train_positive_targets)}; validation positives: ${plain(priceActionModel.validation_positive_targets)}. Collect next: ${joinText(validationGap.collection_queries || [])}.`, "warn"));
    if (!oversightAlerts.length && approvedSignals <= 0) oversightAlerts.push(alertBox("No approved paper signals", longText(diag.main_blocker || priceActionPaper.decision || "Current gates are blocking paper entries.", 220), "warn"));
    if (!oversightAlerts.length && guarded) oversightAlerts.push(alertBox("Collection paused by memory guard", longText(freshness.strategy_v2_runtime_reason || strategyV2.runtime_reason || "The local guard paused heavier collection because RAM was too high.", 220), "warn"));
    if (!oversightAlerts.length) oversightAlerts.push(alertBox("Oversight clear", "Dashboard, shadow research, model freshness, and paper signal gates are not reporting urgent blockers.", "good"));
    document.getElementById("statusDot").className = "dot " + (bad ? "bad" : good ? "good" : guarded ? "warn" : "");
    document.getElementById("statusText").textContent = (dashboardStale ? "dashboard stale" : `research ${researchStatus}`) + " - " + status + " - snapshot age " + fmtAge(dashboardAge) + " - updated " + (data.generated_at_utc || "-");
    const rawPnl = Number(target.actual_pnl_since_baseline_usdc || goalPlan.raw_account_pnl_since_baseline_usdc || 0);
    const pnl = Number(goalPlan.decision_pnl_usdc ?? rawPnl ?? 0);
    const rawRunRate = target.raw_account_monthly_run_rate_usdc ?? target.monthly_run_rate_usdc;
    const decisionRunRate = goalPlan.decision_monthly_run_rate_usdc ?? target.decision_monthly_run_rate_usdc ?? target.monthly_run_rate_usdc;
    const auditedPnl = goalPlan.audited_pnl_since_baseline_usdc;
    const proofPnl = goalPlan.proof_verified_pnl_since_baseline_usdc ?? target.proof_verified_pnl_since_baseline_usdc;
    const decisionPnlSource = goalPlan.decision_pnl_source || target.decision_pnl_source || (proofPnl !== undefined && proofPnl !== null ? "proof_verified_entry_exit_quotes" : "audited_quote_consistent_exits");
    const pnlAuditState = goalPlan.pnl_audit_state || "unknown";
    const profitProofStatus = goalPlan.profit_target_proof_status || target.profit_target_proof_status || "unknown";
    const profitProofBlockers = goalPlan.profit_target_proof_blockers || target.profit_target_proof_blockers || [];
    const auditedRoundTrips = goalPlan.audited_round_trips_since_baseline ?? target.audited_round_trips_since_baseline ?? "-";
    const proofRoundTrips = goalPlan.proof_verified_round_trips_since_baseline ?? target.proof_verified_round_trips_since_baseline;
    const proofEntryMissing = goalPlan.proof_entry_snapshot_missing_round_trips ?? target.proof_entry_snapshot_missing_round_trips;
    const proofExitMissing = goalPlan.proof_exit_snapshot_missing_round_trips ?? target.proof_exit_snapshot_missing_round_trips;
    const evidenceRoundTrips = proofRoundTrips ?? auditedRoundTrips;
    const evidenceTripLabel = proofRoundTrips !== undefined && proofRoundTrips !== null ? "proof round trips" : "audited exits";
    const minAuditedRoundTrips = goalPlan.minimum_audited_round_trips_for_on_pace ?? target.minimum_audited_round_trips_for_on_pace ?? "-";
    const verifiedEvidenceReady = Boolean(goalPlan.verified_evidence_ready || target.verified_evidence_ready);
    const baselineQuoteConflicts = goalPlan.quote_conflict_round_trips ?? target.quote_conflict_round_trips ?? "-";
    const baselineQuoteUnverified = goalPlan.quote_unverified_round_trips ?? target.quote_unverified_round_trips ?? "-";
    const allTimeQuoteConflicts = goalPlan.all_time_quote_conflict_round_trips ?? target.all_time_quote_conflict_round_trips ?? "-";
    const allTimeQuoteUnverified = goalPlan.all_time_quote_unverified_round_trips ?? target.all_time_quote_unverified_round_trips ?? "-";
    const promotionPolicy = data.cohort_promotion_readiness || {};
    const minimumFilledOrders = Number(promotionPolicy.minimum_filled_orders || data.signal_cohort_pnl?.minimum_filled_orders || 5);
    const minimumEvidenceHours = 72;
    const fmtSignedUsd = (v) => {
      const n = Number(v);
      if (v === null || v === undefined || v === "" || Number.isNaN(n)) return "-";
      return (n > 0 ? "+" : "") + fmtUsd(n);
    };
    const evidenceCount = row => {
      for (const key of ["paper_audited_round_trips", "paper_audit_round_trips", "filled_orders", "buy_fills", "closed_trades", "settled_fills", "evidence_fills", "paper_buy_fills", "paper_sell_fills", "shadow_fills", "shadow_sell_fills", "validation_trades", "total_trades", "orders", "fills"]) {
        if (row && row[key] !== undefined && row[key] !== "") return asNumber(row[key], null);
      }
      return null;
    };
    const evidenceHours = row => {
      for (const key of ["evidence_hours", "elapsed_hours", "paper_elapsed_hours", "paper_audited_elapsed_hours", "shadow_elapsed_hours", "evidence_elapsed_hours", "hours_since_first_evidence", "hours_observed", "age_hours"]) {
        if (row && row[key] !== undefined && row[key] !== "") return asNumber(row[key], null);
      }
      const start = row ? (row.first_evidence_at_utc || row.first_fill_at_utc || row.first_timestamp || row.first_seen_at_utc) : "";
      const end = row ? (row.generated_at_utc || data.generated_at_utc) : data.generated_at_utc;
      const startAge = ageSeconds(start);
      const endAge = ageSeconds(end);
      if (startAge !== null && endAge !== null) return Math.max(0, (startAge - endAge) / 3600);
      const pnl = asNumber(row?.forward_paper_pnl_usdc ?? row?.forward_shadow_pnl_usdc ?? row?.realized_pnl_usdc ?? row?.total_mark_pnl_usdc ?? row?.total_pnl_usdc ?? row?.shadow_total_pnl_usdc, null);
      const monthly = asNumber(row?.monthly_run_rate_usdc ?? row?.shadow_monthly_run_rate_usdc, null);
      return pnl !== null && monthly !== null && Math.abs(monthly) > 0 ? Math.abs(pnl) * 720 / Math.abs(monthly) : null;
    };
    const evidenceReadyForAnnualising = row => {
      const count = evidenceCount(row);
      const hours = evidenceHours(row);
      return count !== null && count >= minimumFilledOrders && hours !== null && hours >= minimumEvidenceHours;
    };
    const fmtEvidenceRunRate = (value, row) => {
      const count = evidenceCount(row);
      const hours = evidenceHours(row);
      const weakCount = count === null || count < minimumFilledOrders;
      const weakHours = hours === null || hours < minimumEvidenceHours;
      if (weakCount || weakHours) {
        const countText = count === null ? "?" : fmtNum(count, 0);
        const hourText = hours === null ? "?" : fmtNum(hours, 1);
        return `n/a (${countText} fills, ${hourText}h)`;
      }
      return fmtUsd(value);
    };
    const fmtClvBeatRate = (value, row) => {
      const finalPositions = asNumber(row?.final_positions ?? closingLine.final_line_positions, 0);
      return finalPositions > 0 ? fmtNum(Number(value) * 100, 1) + "%" : "n/a";
    };
    const finalLineDisplay = () => {
      const finalCount = asNumber(closingLine.final_line_positions, 0);
      const scored = asNumber(closingLine.positions_scored, 0);
      return finalCount > 0 ? finalCount : `n/a (${scored} provisional lines await market close)`;
    };
    const algoBestDisplay = () => {
      if (!bestReplay.strategy) return "No replay artifact yet";
      if (asNumber(bestReplay.fills, 0) <= 0) {
        const losing = algoReplaySummaries
          .filter(row => asNumber(row.fills, 0) > 0 || asNumber(row.unrealised_mark_to_bid_pnl_usdc, 0) < 0)
          .map(row => `${row.strategy}: ${fmtUsd(row.unrealised_mark_to_bid_pnl_usdc)}`)
          .join("; ");
        return losing ? `no strategy beat doing nothing (${losing})` : "no strategy beat doing nothing";
      }
      return `${bestReplay.strategy}: ${fmtUsd(bestReplay.unrealised_mark_to_bid_pnl_usdc)} on ${fmtUsd(bestReplay.total_cost_usdc)}`;
    };
    const routeEvidenceRows = () => [
      ...(Array.isArray(priceActionGoal.top_cohorts) ? priceActionGoal.top_cohorts : []),
      ...(Array.isArray(priceActionFeedback.forward_paper_preview) ? priceActionFeedback.forward_paper_preview : []),
      ...(Array.isArray(priceActionFeedback.forward_shadow_preview) ? priceActionFeedback.forward_shadow_preview : []),
      ...(Array.isArray(priceActionFeedback.paper_confirmation_preview) ? priceActionFeedback.paper_confirmation_preview : []),
    ];
    const routePnl = (row, route) => asNumber(route === "paper" ? row?.forward_paper_pnl_usdc : row?.forward_shadow_pnl_usdc, null);
    const routeBestRow = route => routeEvidenceRows()
      .filter(row => routePnl(row, route) !== null && routePnl(row, route) > 0 && String(row?.forward_edge_blocker || "") === "")
      .sort((a,b) => (asNumber(b.monthly_run_rate_usdc, -Infinity) - asNumber(a.monthly_run_rate_usdc, -Infinity)) || (routePnl(b, route) - routePnl(a, route)))[0] || null;
    const fmtDurationHours = hours => {
      if (hours === null || hours === undefined || Number.isNaN(Number(hours))) return "?h";
      const value = Number(hours);
      if (value < 1) return `${Math.max(1, Math.round(value * 60))}m`;
      if (value < 24) return `${fmtNum(value, 1)}h`;
      return `${fmtNum(value / 24, 1)}d`;
    };
    const routeEvidenceDisplay = route => {
      const row = routeBestRow(route);
      if (!row) return `best ${route} cohort: no positive evidence yet`;
      const count = evidenceCount(row);
      const hours = evidenceHours(row);
      const pnl = routePnl(row, route);
      const countText = count === null ? "?" : fmtNum(count, 0);
      const noun = count === 1 ? "round trip" : "round trips";
      const base = `best ${route} cohort: ${fmtSignedUsd(pnl)} on ${countText} ${noun} over ${fmtDurationHours(hours)}`;
      return evidenceReadyForAnnualising(row)
        ? `${base} (${fmtUsd(row.monthly_run_rate_usdc)}/month evidence-qualified)`
        : `${base} (n too small to annualise)`;
    };
    const bestEdgeRouteSummary = () => `${routeEvidenceDisplay("shadow")} / ${routeEvidenceDisplay("paper")}`;
    const auditedRawSuffix = () => pnlAuditState === "raw_pnl_contains_quote_conflicts" && auditedPnl !== undefined && auditedPnl !== null
      ? ` (raw; audited ${fmtUsd(auditedPnl)})`
      : "";
    const paperRejections = asNumber(priceActionPaper.rejections, asNumber(data.forward_paper_cycle?.signals_rejected, asNumber(diag.rejected_signals_count, 0)));
    const brokerWorkPending = Boolean(priceActionPaper.broker_refresh_needed || priceActionPaper.pending_broker_signals || priceActionPaper.pending_broker_confirmation_signals || priceActionPaper.pending_broker_exit_probes);
    const trustedEdgeMissingFresh = tradeSignalAudit.verdict === "trusted_edge_missing_fresh_candidate"
      || String(priceActionPaper.decision || "").includes("trusted_shadow_edge_waiting_for_fresh_executable_candidate");
    const rawTradeDecision = dashboardStale
      ? "WAIT: REFRESH"
      : approvedSignals > 0
        ? (brokerWorkPending ? "RUN PAPER BROKER" : "PAPER READY")
        : "DO NOT TRADE YET";
    const tradeDecision = decisionSummary.trade_decision || rawTradeDecision;
    const tradeDecisionClass = decisionSummary.decision_class || (dashboardStale ? "bad" : approvedSignals > 0 ? "good" : "warn");
    const rawBlockerText = dashboardStale
      ? "The dashboard snapshot is too old to trust. Refresh the dashboard before reading the signal state."
      : approvedSignals > 0
        ? (brokerWorkPending ? "Approved signal rows exist and need the paper broker refresh to execute/record them." : "Approved signal rows exist. Monitor fills, exits, spread, and realised P&L.")
        : trustedEdgeMissingFresh
          ? "Trusted shadow repricing evidence exists, but there is no fresh open executable paper-confirmation candidate yet. Collect executable bid/ask rows across the registered H1/H2/H3 hypotheses rather than force a stale or closed trade."
          : (priceActionPaper.decision || diag.main_blocker || goalPlan.main_gap || "Current evidence gates are blocking entries.");
    const blockerText = decisionSummary.primary_blocker || rawBlockerText;
    const nextAction = decisionSummary.next_action || tradeSignalAudit.next_action || goalPlan.recommended_action || priceActionFeedback.next_action || diag.recommended_action || "Keep collecting live bid/ask evidence until a validated cohort produces executable positive paper signals.";
    const rawCollectQueries = activeDiscoveryQueries(tradeSignalAudit.missing_confirmation_queries?.length
      ? tradeSignalAudit.missing_confirmation_queries
      : displayedQueries.length
        ? displayedQueries
        : adaptiveQueries);
    const collectQueries = Array.isArray(decisionSummary.collect_now) && decisionSummary.collect_now.length
      ? activeDiscoveryQueries(decisionSummary.collect_now)
      : rawCollectQueries;
    const transferReason = priceActionModel.cohort_transfer?.reason || validationGap.reason || "";
    const rawUnlockCondition = approvedSignals > 0
      ? (brokerWorkPending ? "Paper broker refresh must run and record the fill/exit evidence." : "Keep this cohort under forward paper supervision.")
      : trustedEdgeMissingFresh
        ? "Find an open H1/H2/H3 candidate with executable bid and ask, acceptable spread, and matching trusted confirmation evidence."
      : validationGapActive
        ? (validationGap.reason || "The model still needs positive validation examples before promotion.")
        : transferReason
          ? transferReason
          : (priceActionGoal.state === "trusted_shadow_needs_paper_confirmation"
              ? "Trusted shadow cohorts need fresh executable paper-confirmation probes before promotion."
              : blockerText);
    const unlockCondition = decisionSummary.unlock_condition || rawUnlockCondition;
    const recentLossExits = asNumber(tradeSignalAudit.recent_loss_exit_count);
    const recentExitCount = asNumber(tradeSignalAudit.recent_exit_count);
    const riskLesson = recentExitCount > 0
      ? `${recentLossExits}/${recentExitCount} recent exits lost; recent realised P&L ${fmtUsd(tradeSignalAudit.recent_realised_pnl_usdc)}.`
      : "No recent closed paper exits in the current audit window.";
    const visibleAlerts = oversightAlerts.slice(0, 3).join("");
    const decisionTargets = tradeSignalAudit.paper_confirmation_targets || priceActionGoal.paper_confirmation_targets || [];
    const nearMissRows = (diag.current_near_miss_candidates && diag.current_near_miss_candidates.length)
      ? diag.current_near_miss_candidates
      : (diag.current_shadow_candidates || []);
    const summaryDecisionCards = decisionCards(decisionSummary.decision_cards || []);
    document.getElementById("cards").innerHTML = summaryDecisionCards || [
      card("Can paper trade now?", tradeDecision, tradeDecisionClass),
      card("Why not / why now?", longText(blockerText, 120), tradeDecisionClass),
      card("Unlock condition", longText(unlockCondition, 120), approvedSignals > 0 ? "good" : "warn"),
      card("Collect now", joinText(collectQueries), collectQueries.length ? "good" : "warn"),
      card("$100/month state", `${fmtUsd(pnl)} audited / ${fmtUsd(decisionRunRate)} run-rate / ${profitProofStatus}`, verifiedEvidenceReady ? "good" : "warn"),
      card("Best edge route", bestEdgeRouteSummary(), "warn")
    ].join("");
    const operatingRows = Array.isArray(operatingState.rows) ? operatingState.rows : [];
    const executorDeadMan = executorStatus.dead_man || {};
    const executorFreshness = executorStatus.freshness_slo || {};
    const executorKill = executorStatus.kill_criteria_scoreboard || {};
    const executorReconciliation = executorStatus.executor_reconciliation || {};
    const executorAlerts = Array.isArray(executorStatus.owner_alerts) ? executorStatus.owner_alerts : [];
    const executorAbsent = String(executorStatus.status || "ABSENT").toUpperCase() === "ABSENT";
    const executorStateClass = String(executorStatus.status || "").toUpperCase() === "ALERT" ? "bad" : executorAbsent ? "warn" : "good";
    const executorExposure = executorAbsent
      ? "ABSENT"
      : `${fmtUsd(executorStatus.exposure_usd)} / ${fmtUsd(executorStatus.stage_cap_usd)} (${plain(executorStatus.exposure_vs_stage_cap_state || "UNKNOWN")})`;
    document.getElementById("executorOps").innerHTML = `
      <div class="sectionLead">Independent, read-only monitoring of the future executor ledger and heartbeat. Until that ledger exists, every executor runtime value is intentionally ABSENT.</div>
      ${facts([
        ["Monitor state", executorStatus.status || "ABSENT", v=>badge(v, executorStateClass)],
        ["Mode", executorStatus.mode || "ABSENT"],
        ["Open orders", executorAbsent ? "ABSENT" : (executorStatus.open_orders ?? "UNKNOWN")],
        ["Exposure / stage cap", executorExposure, v=>v],
        ["Last action age", executorAbsent ? "ABSENT" : fmtAge(executorStatus.last_action_age_seconds), v=>v],
        ["Freshness SLO", executorAbsent ? "ABSENT" : `${executorFreshness.state || "UNKNOWN"} / ${fmtAge(executorFreshness.heartbeat_age_seconds)}`, v=>v],
        ["Dead-man", executorAbsent ? "ABSENT" : `${executorDeadMan.state || "UNKNOWN"} / ${fmtAge(executorDeadMan.countdown_seconds)} remaining`, v=>v],
        ["Kill criteria", executorAbsent ? "ABSENT" : (executorKill.status || "UNKNOWN")],
        ["Executor reconciliation", executorAbsent ? "ABSENT" : `${executorReconciliation.status || "UNKNOWN"} / ${fmtUsd(executorReconciliation.unexplained_discrepancy_usd)}`, v=>v],
        ["Owner notification", executorStatus.notify ? "NEW ALERT READY" : (executorAlerts.length ? "active / deduplicated" : "none")]
      ])}
      ${executorAlerts.length ? `<div class="alertGrid">${executorAlerts.map(item => alertBox(item.title || item.id || "Executor alert", longText(item.reason || "-", 260), item.severity === "critical" ? "bad" : "warn")).join("")}</div>` : `<div class="muted">No active executor live-ops alerts.</div>`}
      ${titledTable("Kill-criteria scoreboard", executorKill.criteria || [], [
        ["Criterion","criterion", v=>longText(v, 180)],
        ["Triggered","triggered"],
        ["Observed","observed", v=>longText(v, 140)],
        ["Threshold","threshold", v=>longText(v, 140)]
      ], 8)}
    `;
    document.getElementById("operatingState").innerHTML = `
      <div class="sectionLead">Generated from effective config and point-in-time artifacts. UNKNOWN means the evidence source is absent; no value is guessed.</div>
      ${operatingRows.length ? table(operatingRows, [
        ["Question","question", v=>longText(v, 150)],
        ["State","state", v=>longText(v, 160)],
        ["Evidence","evidence", v=>longText(v, 260)],
        ["Source","source", v=>longText(v, 160)],
        ["Verified","verified_at_utc"]
      ]) : `<div class="muted">Generated operating state is missing. Treat every current authorisation claim as UNKNOWN.</div>`}
      ${titledTable("Operating SLOs (reporting only)", operatingState.slo?.rows || [], [
        ["Metric","metric", v=>longText(v, 150)],
        ["State","state"],
        ["Target","target"],
        ["Measured","measured"],
        ["Unit","unit"],
        ["Observed","observed_at_utc"]
      ], 7)}
      ${titledTable("Degraded-state watchdog registrations", degradedWatchdog.evaluations || [], [
        ["Registration","registration_id", v=>longText(v, 180)],
        ["State","state"],
        ["Observed","observed_state", v=>longText(v || "-", 140)],
        ["Consecutive","consecutive_degraded_observations"],
        ["Maximum","max_consecutive_degraded_observations"],
        ["Artifact","artifact", v=>longText(v, 180)]
      ], 6)}
      ${titledTable("Active degraded-state incidents", degradedWatchdog.active_incidents || [], [
        ["Registration","registration_id", v=>longText(v, 170)],
        ["Entity","entity", v=>longText(v, 150)],
        ["Reason","reason", v=>longText(v, 260)],
        ["Detected","detected_at_utc"],
        ["Source","source_artifact", v=>longText(v, 180)]
      ], 5)}
      ${titledTable("WO-67 autonomous-execution preconditions", operatingState.wo67_preconditions || [], [
        ["ID","id"],
        ["Precondition","title", v=>longText(v, 180)],
        ["State","state"],
        ["Evidence","evidence", v=>longText(v, 220)]
      ], 5)}
    `;
    document.getElementById("decisionCockpit").innerHTML = `
      <div class="decisionHero">
        <div class="decisionStatus ${tradeDecisionClass}">
          <div class="statusLabel">Paper trading decision</div>
          <div class="statusValue">${escapeHtml(tradeDecision)}</div>
        </div>
        <div class="decisionText">
          <div class="headline">${escapeHtml(decisionSummary.headline || (approvedSignals > 0 ? "Actionable signal queue exists" : "The bot is correctly refusing weak trades"))}</div>
          <div class="body">${longText(blockerText, 420)}</div>
        </div>
      </div>
      <div class="decisionGrid">
        <div class="decisionTile"><div class="tileLabel">Unlock condition</div><div class="tileValue">${longText(unlockCondition, 220)}</div></div>
        <div class="decisionTile"><div class="tileLabel">Next evidence cycle</div><div class="tileValue">${longText(nextAction, 220)}</div></div>
        <div class="decisionTile"><div class="tileLabel">Collect now</div><div class="tileValue">${joinText(collectQueries)}</div></div>
        <div class="decisionTile"><div class="tileLabel">Risk/P&L lesson</div><div class="tileValue">${longText(riskLesson, 180)}</div></div>
      </div>
      ${titledTable("Operator questions", decisionSummary.decision_questions || [], [
        ["Question","question", v=>longText(v, 130)],
        ["Answer","answer", v=>longText(v, 220)],
        ["Why it matters","decision_use", v=>longText(v, 240)]
      ], 6)}
      ${visibleAlerts ? `<div class="alertGrid">${visibleAlerts}</div>` : ""}
      ${titledTable("Closest confirmation targets", decisionTargets, [
        ["Query","recommended_collection_query"],
        ["Cohort","cohort", v=>longText(v, 170)],
        ["Run-rate","monthly_run_rate_usdc", fmtEvidenceRunRate],
        ["Shadow P&L","forward_shadow_pnl_usdc", fmtUsd],
        ["Candidate rows","current_candidate_rows"],
        ["Executable rows","current_executable_rows"],
        ["Missing fresh","missing_fresh_candidate"]
      ], 5)}
      ${titledTable("Closest rejected candidates to learn from", nearMissRows, [
        ["Market","market_slug", v=>longText(v, 130)],
        ["Outcome","outcome"],
        ["Cohort","signal_cohort", v=>longText(v, 140)],
        ["Lower-bound","edge_lower_bound", v=>fmtNum(v,4)],
        ["Spread","spread", v=>fmtNum(v,4)],
        ["Liquidity","liquidity", v=>fmtNum(v,2)],
        ["Why blocked","near_miss_learning_reason", (v,row)=>longText(v || row.shadow_candidate_reason || row.rejection_reason, 180)]
      ], 5)}
    `;
    document.getElementById("actionBoard").innerHTML = `
      <div class="sectionLead">${longText(decisionSummary.operator_plain_english || "This panel shows only the actions that can move the bot toward a governed paper-trading edge.", 360)}</div>
      <div>${(decisionSummary.state_badges || []).map(item => badge(item.label || item, item.severity || "")).join("")}</div>
      ${actionList(decisionSummary.priority_actions || [])}
      <details class="expand"><summary>Evidence lane drill-down</summary>
        ${titledTable("Evidence lanes that matter now", decisionSummary.evidence_lanes || [], [
          ["Lane","lane"],
          ["State","state", v=>longText(v, 120)],
          ["Decision use","decision_use", v=>longText(v, 180)],
          ["Key metric","key_metric", v=>longText(v, 140)],
          ["Blocker / next","blocker_or_next", v=>longText(v, 220)]
        ], 8)}
        ${titledTable("Filtered from the primary view", decisionSummary.audit_only_panels || [], [
          ["Panel","panel"],
          ["Why not primary","reason", v=>longText(v, 240)]
        ], 8)}
      </details>
    `;
    document.getElementById("evidenceFunnel").innerHTML = `
      <div class="sectionLead">Road-to-paper view: discovery -> shadow evidence -> CLV/attribution -> family calibration -> sweep confirmation -> paper gate. Dashes mean the source artifact is missing, not approved.</div>
      ${facts([
        ["Liquidity targets discovered", evidenceFunnel.liquidity_targets_discovered],
        ["Alpha shadow candidates", evidenceFunnel.alpha_shadow_candidates],
        ["Shadow positions", evidenceFunnel.shadow_positions],
        ["Closed with CLV final lines", evidenceFunnel.closed_with_clv_final_lines],
        ["Attributed positions", evidenceFunnel.attributed_positions],
        ["Cohorts by attribution class", evidenceFunnel.cohorts_by_attribution_class, v=>longText(v, 220)],
        ["Positive CLV cohorts", evidenceFunnel.positive_clv_cohorts, joinText],
        ["Families beating market", evidenceFunnel.model_beats_market_families, joinText],
        ["Collection gap", evidenceFunnel.collection_gap, v=>longText(v, 220)],
        ["Sweep decision", evidenceFunnel.sweep_decision, v=>longText(v, 220)],
        ["Paper gate", evidenceFunnel.paper_gate_status, v=>longText(v, 180)],
        ["History rows", evidenceFunnel.evidence_history_rows]
      ])}
      ${titledTable("Missing pre-close quote positions", evidenceFunnel.missing_pre_close_positions || [], [
        ["Position","shadow_position_id", v=>longText(v, 120)],
        ["Family","family", v=>longText(v, 120)],
        ["Close time","close_time"],
        ["Reason","reason", v=>longText(v, 160)]
      ], 5)}
      ${titledTable("Latest evidence history", evidenceFunnel.recent_history || [], [
        ["Recorded","recorded_at_utc"],
        ["Source","source"],
        ["Rows / positions","positions_scored_attributed"],
        ["Final CLV rows","final_line_positions"],
        ["Positive cohorts","positive_cohorts", v=>longText(v, 180)],
        ["Summary","decision_or_class_summary", v=>longText(v, 220)]
      ], 5)}
    `;
    const sharpSportRows = Array.isArray(sharpSportsFunnel.families) ? sharpSportsFunnel.families : [];
    document.getElementById("sharpSportsFunnel").innerHTML = `
      <div class="sectionLead">Bookmaker-anchor route for sports: collection query -> liquid Polymarket rows -> sharp no-vig anchors -> current scored overlap -> CLV/paper proof. Reporting only; it does not approve trades.</div>
      ${facts([
        ["Status", sharpSportsFunnel.status || "-"],
        ["Next bottleneck", sharpSportsFunnel.next_bottleneck || "-", v=>longText(v, 260)],
        ["Active collection families", sharpSportsFunnel.active_collection_families || [], joinText],
        ["Anchor rows", sharpSportsFunnel.total_anchor_rows],
        ["Tradable rows", sharpSportsFunnel.total_tradable_liquidity_rows],
        ["Scored anchor hits", sharpSportsFunnel.total_scored_anchor_hits],
        ["Shadow candidates", sharpSportsFunnel.total_shadow_candidates],
        ["Final CLV rows", sharpSportsFunnel.total_final_clv_rows],
        ["Decision use", sharpSportsFunnel.decision_use || "-", v=>longText(v, 260)],
        ["Dry-run only", (sharpSportsFunnel.live_trading_invoked || sharpSportsFunnel.paper_trading_invoked) ? "unexpected invocation flag" : "yes - no orders placed"]
      ])}
      ${titledTable("Sharp sports family bottlenecks", sharpSportRows, [
        ["Family","family"],
        ["Query active","query_active"],
        ["Tradable","tradable_liquidity_rows"],
        ["Anchor rows","anchor_rows"],
        ["PM eligible","polymarket_eligible_rows"],
        ["Current join","anchor_current_joined_rows"],
        ["Ambiguous","anchor_ambiguous_rows"],
        ["Stale","anchor_stale_rows"],
        ["Actionable","anchor_actionable_rows"],
        ["Scored hits","scored_anchor_hits"],
        ["Shadow","shadow_candidates"],
        ["CLV final","final_clv_rows"],
        ["State","state", v=>longText(v, 150)],
        ["Next","next_action", v=>longText(v, 230)]
      ], 8)}
    `;
    const dutchBest = dutchArb.best_opportunity || {};
    const dutchStats = dutchArb.scan_stats_latest_poll || {};
    const dutchPersistent = Array.isArray(dutchArb.persistent_alerts) ? dutchArb.persistent_alerts : [];
    const dutchTop = Array.isArray(dutchArb.top_opportunities) ? dutchArb.top_opportunities : [];
    const fmtPct = v => v === null || v === undefined || v === "" || Number.isNaN(Number(v)) ? "-" : fmtNum(Number(v) * 100, 2) + "%";
    const h2Support = h2Dutch.support || {};
    const h2Checks = h2Support.support_checks || {};
    const h2Episodes = Array.isArray(h2Dutch.episode_preview) ? h2Dutch.episode_preview : [];
    const exactH2 = Object.keys(h2Dutch).length
      ? `<div class="sectionLead">Registered H2 authority: exact complete common-size plans after canonical per-leg fees and the fixed 0.002 USDC/basket reserve. This is shadow research only.</div>` + facts([
          ["Registered status", h2Dutch.status || "-"],
          ["Exact artifact generated", h2Dutch.generated_at_utc || "-"],
          ["Latest exact scan", h2Dutch.latest_exact_observation_at_utc || "none"],
          ["Formal evaluation started", h2Dutch.formal_evaluation_started ? "yes" : "no - stopping sample remains sealed"],
          ["Stop rule", h2Dutch.stop_reason || `first 100 episodes or ${h2Dutch.registered_stop_at_utc || "registered deadline"}`],
          ["Independent episodes", `${h2Dutch.independent_episodes ?? 0} / ${h2Dutch.critical_settings?.stop_episodes ?? 100}`],
          ["Persistent 3-scan episodes", `${h2Dutch.persistent_episodes_current ?? 0} / ${h2Dutch.critical_settings?.minimum_persistent_episodes ?? 10}`],
          ["Calendar span", `${h2Support.calendar_span_days ?? 0} / ${h2Dutch.critical_settings?.minimum_calendar_span_days ?? 14} days`],
          ["Aggregate exact net profit", h2Support.aggregate_net_profit_usdc, fmtUsd],
          ["Mean annualised net ROC", h2Support.mean_annualised_net_return_on_capital, fmtPct],
          ["Event-clustered 90% interval", h2Support.event_clustered_ci_low_90 === null || h2Support.event_clustered_ci_low_90 === undefined ? "not evaluated" : `[${fmtPct(h2Support.event_clustered_ci_low_90)}, ${fmtPct(h2Support.event_clustered_ci_high_90)}]`],
          ["Max positive-profit event share", h2Support.maximum_positive_event_profit_share, fmtPct],
          ["Support checks", h2Checks, v=>longText(v, 300)],
          ["FDR", h2Dutch.fdr_status || "not applicable"],
          ["Frozen evidence anchored", h2Dutch.evidence_anchor_pass ? "yes" : (h2Dutch.evidence_anchor_reason || "not yet")],
          ["Exact exclusions / coverage", h2Dutch.coverage || {}, v=>longText(v, 320)],
          ["Next condition", h2Dutch.next_unmet_condition || "-", v=>longText(v, 320)],
          ["No order authority", (h2Dutch.live_trading_invoked || h2Dutch.paper_trading_invoked) ? "unexpected invocation flag" : "confirmed - shadow research only"]
        ]) + titledTable("Current exact H2 episodes", h2Episodes, [
          ["Start","episode_start_at_utc"],
          ["Event","event_title", v=>longText(v, 180)],
          ["Legs","leg_count"],
          ["Ask sum","ask_sum", v=>fmtNum(v, 4)],
          ["Fees/set","entry_taker_fees_per_set", v=>fmtNum(v, 4)],
          ["Net profit","net_profit_usdc", fmtUsd],
          ["Annualised net ROC","annualised_net_return_on_capital", fmtPct],
          ["3-scan persistent","persistent_three_scans"]
        ], 8)
      : `<div class="sectionLead">No exact H2 artifact exists yet. The registered H2 verdict is unavailable; legacy gross rows cannot substitute.</div>`;
    const dutchCaveat = "Observed ask baskets (gross only). This live watch does not include the exact registered H2 costs or episode verdict.";
    const grossDiagnostic = Object.keys(dutchArb).length
      ? `<details class="expand"><summary>Dutch-book arb watch - live gross diagnostic only</summary><div class="sectionLead">${dutchCaveat}</div>` + facts([
          ["Status", dutchArb.status || "-"],
          ["Last scan", dutchArb.generated_at_utc || dutchArb.last_scan_at_utc || "-"],
          ["Events discovered", dutchStats.discovered ?? dutchArb.events_scanned ?? "-"],
          ["Events priced complete", dutchArb.events_priced_complete_latest_poll ?? dutchArb.events_priced_complete ?? "-"],
          ["Complete arbs now", dutchArb.complete_arbs_latest_poll ?? "-"],
          ["Above alert total", dutchArb.alerts_total ?? "-"],
          ["Persistent above alert", dutchArb.persistent_alert_count ?? dutchPersistent.length],
          ["Best basket", dutchBest.event || "none", v=>longText(v, 180)],
          ["Best ask sum", dutchBest.ask_sum, v=>fmtNum(v, 4)],
          ["Best annualised ROI", dutchArb.best_annualised_return_on_capital ?? dutchBest.annualised_return_on_capital, fmtPct],
          ["Dry-run only", (dutchArb.live_trading_invoked || dutchArb.paper_trading_invoked) ? "unexpected invocation flag" : "yes - no orders placed"],
          ["Caveat", dutchCaveat, v=>longText(v, 260)]
        ]) + (dutchPersistent.length ? `<div class="alertGrid">${dutchPersistent.map(row => alertBox(row.title || "Persistent Dutch-book arb", `${longText(row.event, 160)} persisted ${plain(row.consecutive_scans_above_alert)} scans above ${fmtPct(row.alert_annualised)} alert.`, "good")).join("")}</div>` : "")
        + titledTable("Top Dutch-book baskets", dutchTop, [
          ["Event","event", v=>longText(v, 180)],
          ["Outcomes","outcomes"],
          ["Ask sum","ask_sum", v=>fmtNum(v, 4)],
          ["Lock/set","lock_per_set", v=>fmtNum(v, 4)],
          ["Annualised ROI","annualised_return_on_capital", fmtPct],
          ["Capital","capital_usdc", fmtUsd],
          ["Profit","profit_usdc", fmtUsd],
          ["Days","days_to_resolution"]
        ], 5) + `</details>`
      : `<details class="expand"><summary>Dutch-book arb watch - live gross diagnostic only</summary><div class="sectionLead">No gross Dutch-book scan yet. The VPS loop will run a bounded dry-run pass when due.</div></details>`;
    document.getElementById("dutchArbWatch").innerHTML = exactH2 + grossDiagnostic;
    const longshotPreview = Array.isArray(longshotBias.candidate_preview) ? longshotBias.candidate_preview : [];
    const longshotCandidates = longshotBias.candidates ?? longshotPreview.length ?? "-";
    document.getElementById("longshotBias").innerHTML = Object.keys(longshotBias).length
      ? `<div class="sectionLead">Slow-market longshot-bias research only: buy cheap NO / avoid overpriced tails, then validate with CLV before any paper promotion.</div>` + facts([
          ["Status", longshotBias.status || "-"],
          ["Generated", longshotBias.generated_at_utc || "-"],
          ["Source", longshotBias.source || "longshot_bias_summary"],
          ["Markets scanned", longshotBias.markets_scanned ?? "-"],
          ["Source rows", longshotBias.source_rows ?? "-"],
          ["Candidates", longshotCandidates],
          ["Candidate cohorts", longshotBias.candidate_cohorts || {}, v=>longText(v, 220)],
          ["Shadow emit", longshotBias.emit_shadow_positions === undefined ? "-" : longshotBias.emit_shadow_positions],
          ["Shadow opened", longshotBias.shadow_update?.opened_this_cycle ?? "-"],
          ["Decision use", longshotBias.decision_use || "shadow_clv_learning_target_not_paper_trade_authorisation", v=>longText(v, 260)],
          ["Dry-run only", (longshotBias.live_trading_invoked || longshotBias.paper_trading_invoked) ? "unexpected invocation flag" : "yes - no paper/live orders placed"]
        ]) + titledTable("Longshot NO candidates", longshotPreview, [
          ["Market","question", (v,row)=>marketLabel(row)],
          ["Family","family", v=>longText(v, 120)],
          ["Outcome","outcome"],
          ["Ask","ask", v=>fmtNum(v, 4)],
          ["Bid","bid", v=>fmtNum(v, 4)],
          ["Spread","spread", v=>fmtNum(v, 4)],
          ["Liquidity","liquidity", v=>fmtNum(v, 2)],
          ["Cohort","signal_cohort", v=>longText(v, 180)]
        ], 6)
      : `<div class="sectionLead">No longshot-bias scan artifact yet. This lane should publish a disabled/skipped/ok state each paper cycle so absence is visible, not silent.</div>`;
    document.getElementById("modelHealth").innerHTML = facts([
      ["Model gate", priceActionModel.decision || decisionSummary.model_state, v=>longText(v, 220)],
      ["Model age", fmtAge(modelAge)],
      ["Validation gap", validationGapActive ? validationGap.reason || "Needs positive validation examples." : "No active positive-validation gap.", v=>longText(v, 260)],
      ["Cohort transfer", priceActionModel.cohort_transfer?.reason || "No active transfer blocker reported.", v=>longText(v, 260)],
      ["Paper bridge", priceActionPaper.decision, v=>longText(v, 220)],
      ["Sharp-anchor alpha bridge", alphaBridge.key_metric || alphaBridge.status || "-", v=>longText(v, 220)],
      ["Sharp-anchor next", alphaBridge.blocker_or_next || "-", v=>longText(v, 260)],
      ["Algo replay best", algoBestDisplay(), v=>longText(v, 220)],
      ["Current analogue scan", `${currentHistScan.current_rows ?? 0} rows / ${currentHistScan.positive_matches ?? 0} positive matches`, v=>longText(v, 180)],
      ["Analogue blocker", currentHistScan.state || "-", v=>longText(v, 220)],
      ["World Cup layer", (data.worldcup_validation_status || {}).status, v=>longText(v, 180)],
      ["Collect next", collectQueries, joinText],
      ["Learning verdict", decisionSummary.learning_verdict || "-", v=>longText(v, 260)]
    ]);
    document.getElementById("oversightCockpit").innerHTML = facts([
      ["Dashboard snapshot", `${fmtAge(dashboardAge)} old / ${data.generated_at_utc || "-"}`],
      ["Deployment health", `${deploymentHealth.status || "-"} / ${deploymentHealth.dashboard_code_version || "unknown"}`, v=>longText(v, 220)],
      ["Expected deploy SHA", deploymentHealth.expected_deploy_sha || "-", v=>longText(v, 180)],
      ["Odds API key", deploymentHealth.the_odds_api_key_present ? "present" : "missing"],
      ["Deploy blockers", deploymentHealth.blockers || [], joinText],
      ["Shadow research", `${researchStatus} / ${fmtAge(researchAge)} old`],
      ["Active lane", legacyLiveActive ? "legacy live loop" : "shadow research + websocket"],
      ["Model freshness", `${priceActionModel.status || "unknown"} / ${fmtAge(modelAge)} old`],
      ["Model decision", priceActionModel.decision, v=>longText(v, 220)],
      ["Validation gap", validationGapActive ? validationGap.reason || "Needs positive validation examples." : "No active positive-validation gap.", v=>longText(v, 260)],
      ["Collect next", validationGap.collection_queries || priceActionGoal.collection_queries || priceActionFeedback.collection_queries, joinText],
      ["Paper signals", `${approvedSignals} approved / ${priceActionPaper.rejections ?? 0} rejected`],
      ["Live historical scan", `${currentHistScan.current_rows ?? 0} current rows / ${currentHistScan.positive_matches ?? 0} positive`, v=>longText(v, 180)],
      ["Main blocker", diag.main_blocker || priceActionPaper.decision || goalPlan.main_gap, v=>longText(v, 260)]
    ]) + `<div class="alertGrid">${oversightAlerts.join("")}</div>`;
    document.getElementById("actualTarget").innerHTML = facts([
      ["Status", target.status],
      ["Target / month", target.target_monthly_profit_usdc, fmtUsd],
      ["Verified P&L for goal", pnl, fmtUsd],
      ["Verified run-rate", decisionRunRate, fmtUsd],
      ["Decision P&L source", decisionPnlSource, v=>longText(v, 180)],
      ["Raw ledger P&L (audit-only)", rawPnl, v=>fmtUsd(v) + auditedRawSuffix()],
      ["P&L audit state", pnlAuditState, v=>longText(v, 180)],
      ["Proof status", profitProofStatus, v=>longText(v, 180)],
      ["Proof round trips", `${evidenceRoundTrips} / ${minAuditedRoundTrips}`],
      ["Entry proof misses", proofEntryMissing ?? "-"],
      ["Exit proof misses", proofExitMissing ?? "-"],
      ["Proof blockers", profitProofBlockers, joinText],
      ["Baseline quote conflicts", baselineQuoteConflicts],
      ["Baseline unverified quote rows", baselineQuoteUnverified],
      ["All-time quote conflicts", allTimeQuoteConflicts],
      ["All-time unverified quote rows", allTimeQuoteUnverified],
      ["Tracking hours", target.elapsed_hours, v=>fmtNum(v,2)],
      ["Raw monthly run-rate (audit-only)", rawRunRate, v=>fmtUsd(v) + auditedRawSuffix()],
      ["Baseline equity", target.baseline?.baseline_equity_usdc, fmtUsd],
      ["Proof baseline generation", target.profit_target_baseline_generation ?? target.baseline?.baseline_generation ?? "-"],
      ["Proof reset at", target.profit_target_baseline_reset_at_utc || target.baseline?.reset_at_utc || "-"],
      ["Proof reset reason", target.profit_target_baseline_reset_reason || target.baseline?.reset_reason || "-", v=>longText(v, 220)],
      ["History preserved", target.historical_profit_data_preserved || target.baseline?.historical_data_preserved ? "yes" : "no"]
    ]) + titledTable("Quote-audit blockers by cohort", quoteAuditCohorts, [
      ["Cohort","signal_cohort", v=>longText(v, 180)],
      ["Trips","round_trips"],
      ["Audited","quote_consistent_round_trips"],
      ["Conflicts","quote_conflict_round_trips"],
      ["Unverified","quote_unverified_round_trips"],
      ["Entry miss","entry_snapshot_missing_round_trips"],
      ["Exit miss","exit_snapshot_missing_round_trips"],
      ["Proof trips","proof_verified_round_trips"],
      ["Raw P&L","raw_pnl_usdc", fmtUsd],
      ["Audited P&L","audited_pnl_usdc", fmtUsd],
      ["Proof P&L","proof_verified_pnl_usdc", fmtUsd],
      ["Excluded P&L","excluded_from_audit_pnl_usdc", fmtUsd],
      ["Top blocker","top_blocker_status", v=>longText(v, 120)],
      ["Action","recommended_action", v=>longText(v, 220)]
    ], 8) + titledTable("Quote-audit status counts", paperRoundTripSummary.quote_audit_status_counts || [], [
      ["Status","quote_consistency_status", v=>longText(v, 140)],
      ["Trips","round_trips"],
      ["P&L","pnl_usdc", fmtUsd],
      ["Reason","sample_reason", v=>longText(v, 260)]
    ], 6);
    document.getElementById("goalPlan").innerHTML = `<div class="sectionLead">This is the route to the $100/month target using tradable price changes, not waiting for market settlement.</div>` + facts([
      ["Route state", priceActionGoal.state || goalPlan.status],
      ["Needs settlement?", priceActionGoal.settlement_required_for_this_milestone ? "yes" : "no - this milestone uses bid/ask repricing and paper exits"],
      ["Verified P&L for goal", goalPlan.decision_pnl_usdc, fmtUsd],
      ["Raw ledger P&L", goalPlan.raw_account_pnl_since_baseline_usdc, v=>fmtUsd(v) + auditedRawSuffix()],
      ["P&L audit state", goalPlan.pnl_audit_state, v=>longText(v, 180)],
      ["Proof status", profitProofStatus, v=>longText(v, 180)],
      ["Proof round trips", `${evidenceRoundTrips} / ${minAuditedRoundTrips}`],
      ["Decision P&L source", decisionPnlSource, v=>longText(v, 180)],
      ["Proof blockers", profitProofBlockers, joinText],
      ["Baseline quote conflicts", baselineQuoteConflicts],
      ["All-time quote conflicts", allTimeQuoteConflicts],
      ["Best repricing evidence", routeEvidenceDisplay("shadow"), v=>longText(v, 220)],
      ["Forward paper evidence", routeEvidenceDisplay("paper"), v=>longText(v, 220)],
      ["Required/day from here", goalPlan.required_daily_from_here_usdc, fmtUsd],
      ["Goal gap", goalPlan.main_gap, v=>longText(v, 220)],
      ["Recommended action", goalPlan.recommended_action, v=>longText(v, 260)],
      ["Collect next", priceActionGoal.collection_queries || collectQueries, joinText]
    ]) + titledTable("Top price-change cohorts for the goal", priceActionGoal.top_cohorts || [], [
      ["Cohort","cohort", v=>longText(v, 180)],
      ["Source","source"],
      ["Action","action", v=>longText(v, 160)],
      ["Evidence","evidence_type", v=>longText(v, 160)],
      ["Run-rate","monthly_run_rate_usdc", fmtEvidenceRunRate],
      ["Paper P&L","forward_paper_pnl_usdc", fmtUsd],
      ["Shadow P&L","forward_shadow_pnl_usdc", fmtUsd],
      ["Trusted","trusted_for_goal"],
      ["Blocker","forward_edge_blocker", v=>longText(v, 160)]
    ], 5);
    const recentLossPreview = (tradeSignalAudit.recent_exit_preview || []).filter(row =>
      String(row.loss_making_exit || "").toLowerCase() === "true" || asNumber(row.realised_pnl_usdc, 0) < 0
    );
    document.getElementById("tradeSignalAudit").innerHTML = `<div class="sectionLead">Only evidence that changes the trade/no-trade decision is shown here. Open diagnostics below for raw files and provenance.</div>` + facts([
      ["Verdict", tradeSignalAudit.verdict || tradeDecision],
      ["Next action", tradeSignalAudit.next_action || nextAction, v=>longText(v, 260)],
      ["Approved / rejected", `${approvedSignals} / ${paperRejections}`],
      ["Missing target candidates", tradeSignalAudit.missing_confirmation_target_count],
      ["Missing queries", tradeSignalAudit.missing_confirmation_queries || collectQueries, joinText],
      ["Recent realised P&L", tradeSignalAudit.recent_realised_pnl_usdc, fmtUsd],
      ["Recent losses", `${recentLossExits}/${recentExitCount}`],
      ["Trading principles", tradeSignalAudit.trading_principles, joinText]
    ]) + titledTable("Trusted confirmation targets vs current candidates", tradeSignalAudit.paper_confirmation_targets || [], [
      ["Query","recommended_collection_query"],
      ["Cohort","cohort", v=>longText(v, 180)],
      ["Shadow P&L","forward_shadow_pnl_usdc", fmtUsd],
      ["Shadow ROI","forward_shadow_roi", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Run-rate","monthly_run_rate_usdc", fmtEvidenceRunRate],
      ["Candidate rows","current_candidate_rows"],
      ["Executable rows","current_executable_rows"],
      ["Missing fresh","missing_fresh_candidate"]
    ], 6) + titledTable("Current paper signal trade thesis", tradeSignalAudit.current_signal_theses || [], [
      ["Market","market_slug", v=>longText(v, 150)],
      ["Cohort","signal_cohort", v=>longText(v, 140)],
      ["Ask entry","entry_ask", v=>fmtNum(v,4)],
      ["Bid exit now","current_exit_bid", v=>fmtNum(v,4)],
      ["Spread","spread", v=>fmtNum(v,4)],
      ["Spread cost","estimated_spread_cost_usdc", fmtUsd],
      ["Break-even bid","break_even_exit_bid", v=>fmtNum(v,4)],
      ["TP bid needed","required_take_profit_bid", v=>fmtNum(v,4)],
      ["Gap to TP","current_bid_gap_to_profit", v=>fmtNum(v,4)],
      ["Stop bid","stop_loss_bid", v=>fmtNum(v,4)],
      ["Horizon","max_hold_minutes_before_exit", v=>fmtNum(v,1) + "m"]
    ], 4) + titledTable("Top reasons current candidates are rejected", tradeSignalAudit.rejection_reason_counts || [], [
      ["Count","count"],
      ["Reason","reason", v=>longText(v, 220)]
    ], 5) + titledTable("Recent loss exits and P&L diagnosis", recentLossPreview, [
      ["Market","market_slug", v=>longText(v, 150)],
      ["Cohort","signal_cohort", v=>longText(v, 140)],
      ["Entry","entry_price", v=>fmtNum(v,4)],
      ["Exit","exit_price", v=>fmtNum(v,4)],
      ["Break-even","break_even_exit_bid", v=>fmtNum(v,4)],
      ["P&L","realised_pnl_usdc", fmtUsd],
      ["Reason","exit_reason"],
      ["Loss","loss_making_exit"],
      ["Diagnosis","diagnosis", v=>longText(v, 240)]
    ], 4);
    const clvCohorts = Array.isArray(closingLine.cohorts) ? closingLine.cohorts : [];
    const positiveClv = Array.isArray(closingLine.positive_clv_cohorts) ? closingLine.positive_clv_cohorts : [];
    document.getElementById("closingLineValue").innerHTML = Object.keys(closingLine).length
      ? `<div class="sectionLead">CLV checks whether the market moved toward our entry after we bought. It is settlement-independent forward evidence, but it does not authorise paper or live trading by itself.</div>` + facts([
          ["Status", closingLine.status || "-"],
          ["Generated", closingLine.generated_at_utc || "-"],
          ["Positions scored", closingLine.positions_scored],
          ["Final close lines", finalLineDisplay()],
          ["Mean final CLV (all cohorts)", closingLine.mean_final_clv, v=>fmtNum(v, 4)],
          ["Focus mean final CLV (ex-updown)", (closingLine.focus_view||{}).focus_mean_final_clv, v=>fmtNum(v, 4)],
          ["Focus final positions", (closingLine.focus_view||{}).focus_final_positions],
          ["Beat close rate", closingLine.beat_close_rate, fmtClvBeatRate],
          ["Positive CLV cohorts", positiveClv.length ? positiveClv : "none yet", joinText],
          ["Focus positive cohorts", ((closingLine.focus_view||{}).focus_positive_cohorts||[]).length ? (closingLine.focus_view||{}).focus_positive_cohorts : "none yet", joinText],
          ["Governance note", closingLine.governance_note || "Diagnostic only; no trading gate changed.", v=>longText(v, 260)]
        ]) + (positiveClv.length ? `<div class="alertGrid">${alertBox("Positive CLV cohorts found", joinText(positiveClv), "good")}</div>` : "")
        + titledTable("CLV by cohort", clvCohorts, [
          ["Cohort","signal_cohort", v=>longText(v, 180)],
          ["Positions","positions"],
          ["Final","final_positions"],
          ["Mean final CLV","mean_final_clv", v=>fmtNum(v, 4)],
          ["CI low","final_clv_ci_low", v=>fmtNum(v, 4)],
          ["CI high","final_clv_ci_high", v=>fmtNum(v, 4)],
          ["Beat close","final_beat_close_rate", fmtClvBeatRate],
          ["Evidence","clv_evidence", v=>longText(v, 150)]
        ], 8)
      : `<div class="sectionLead">No CLV evidence yet. The next governance refresh should build closing_line_value.json once shadow positions and bid/ask features exist.</div>`;
    const h3Cohorts = Array.isArray(smartFlowClv.cohorts) ? smartFlowClv.cohorts : [];
    const h3Coverage = smartFlowClv.coverage || {};
    document.getElementById("smartFlowClv").innerHTML = Object.keys(smartFlowClv).length
      ? `<div class="sectionLead">Exact registered H3 test: observed public BUY versus the executable bid at close, net of entry/exit taker fees and fixed costs. It never uses midpoint or resolution labels and cannot authorise paper/live trading.</div>` + facts([
          ["Status", smartFlowClv.status || "-"],
          ["Generated", smartFlowClv.generated_at_utc || "-"],
          ["Formal evaluation started", smartFlowClv.formal_evaluation_started === true ? "yes" : "no - validation remains sealed"],
          ["Final executable fills", `${smartFlowClv.final_fills ?? 0} / ${smartFlowClv.critical_settings?.stop_fills ?? 100}`],
          ["Fills remaining", smartFlowClv.final_fills_remaining],
          ["Stop", `${smartFlowClv.stop_reason || "not_reached"} · deadline ${smartFlowClv.stop_deadline_utc || "-"}`],
          ["Decision", smartFlowClv.decision || "collect_more"],
          ["Passing cohorts", smartFlowClv.passing_cohorts || [], joinText],
          ["Coverage", h3Coverage, v=>longText(JSON.stringify(v), 300)],
          ["Legacy diagnostic", Object.keys(legacySmartFlowClv).length ? `${legacySmartFlowClv.fills_scored ?? 0} midpoint-era rows retained for provenance only` : "none"],
          ["Governance note", smartFlowClv.governance_note || "Shadow research only; no trading gate changed.", v=>longText(v, 260)]
        ]) + titledTable("Exact H3 untouched-validation cohorts", h3Cohorts, [
          ["Cohort","cohort", v=>longText(v, 150)],
          ["Type","cohort_type"],
          ["Fills","validation_fills"],
          ["Markets","validation_markets"],
          ["Mean net CLV","mean_net_clv", v=>fmtNum(v, 5)],
          ["90% CI low","cluster_bootstrap_ci_low_90", v=>fmtNum(v, 5)],
          ["FDR pass","bh_fdr_pass"],
          ["Concentration","maximum_positive_family_share", fmtPct],
          ["Gate","support_gate_pass"],
          ["Action","recommended_action", v=>longText(v, 160)]
        ], 8)
      : `<div class="sectionLead">No exact H3 artifact yet. The next daily training harvest must collect wallet-bearing trades and publish h3_evaluation.json; legacy midpoint output is not a substitute.</div>`;
    const correlatedExposure = Array.isArray(portfolioRisk.exposure_by_correlation_key) ? portfolioRisk.exposure_by_correlation_key : [];
    const categoryExposure = Array.isArray(portfolioRisk.exposure_by_category) ? portfolioRisk.exposure_by_category : [];
    document.getElementById("portfolioRisk").innerHTML = Object.keys(portfolioRisk).length
      ? `<div class="sectionLead">Report-only portfolio downside and concentration view. It does not authorise trades or change stake caps; it tells us when profitable evidence is becoming too concentrated.</div>` + facts([
          ["Generated", portfolioRisk.generated_at_utc],
          ["Open positions", portfolioRisk.open_positions],
          ["Marked positions", `${portfolioRisk.marked_positions ?? 0} marked / ${portfolioRisk.unmarked_positions ?? 0} unmarked`],
          ["Total cost", portfolioRisk.total_cost_usdc, fmtUsd],
          ["VaR 95", portfolioRisk.var_95_usdc, fmtUsd],
          ["CVaR 95", portfolioRisk.cvar_95_usdc, fmtUsd],
          ["Worst position return", portfolioRisk.worst_position_return_pct == null ? "-" : fmtNum(portfolioRisk.worst_position_return_pct, 2) + "%"],
          ["Decision use", portfolioRisk.decision_use, v=>longText(v, 180)]
        ]) + titledTable("Top correlated exposure", correlatedExposure, [
          ["Correlation key","key", v=>longText(v, 180)],
          ["Cost basis","cost_basis_usdc", fmtUsd]
        ], 10) + titledTable("Exposure by category", categoryExposure, [
          ["Category","key", v=>longText(v, 140)],
          ["Cost basis","cost_basis_usdc", fmtUsd]
        ], 10)
      : `<div class="sectionLead">No portfolio risk snapshot yet. The next portfolio snapshot should write risk_state.json with VaR/CVaR and correlated exposure.</div>`;
    const attributionCohorts = Array.isArray(edgeAttribution.cohorts) ? edgeAttribution.cohorts : [];
    document.getElementById("edgeAttribution").innerHTML = Object.keys(edgeAttribution).length
      ? `<div class="sectionLead">Explains closed shadow P&L using the exact identity: exit minus entry fill equals settlement surprise plus line movement minus execution cost. Diagnostic only; it does not authorise trading.</div>` + facts([
          ["Status", edgeAttribution.status || "-"],
          ["Generated", edgeAttribution.generated_at_utc || "-"],
          ["Closed seen", edgeAttribution.closed_positions_seen],
          ["Attributed positions", edgeAttribution.attributed_positions],
          ["Skipped closed", edgeAttribution.skipped_unattributable_closed],
          ["Minimum/cohort", edgeAttribution.minimum_positions_per_cohort],
          ["Identity note", edgeAttribution.identity_note, v=>longText(v, 260)],
          ["Governance note", edgeAttribution.governance_note || "Diagnostic only; no trading gate changed.", v=>longText(v, 260)]
        ]) + titledTable("Attribution by cohort", attributionCohorts, [
          ["Cohort","signal_cohort", v=>longText(v, 180)],
          ["Positions","attributed_positions"],
          ["Total P&L","total_pnl_usdc", fmtUsd],
          ["Execution cost","execution_cost_usdc", fmtUsd],
          ["Line movement","line_movement_usdc", fmtUsd],
          ["Settlement surprise","settlement_surprise_usdc", fmtUsd],
          ["Class","attribution_class", v=>longText(v, 170)],
          ["Action","recommended_action", v=>longText(v, 180)]
        ], 10)
      : `<div class="sectionLead">No edge attribution evidence yet. The next governance refresh should build edge_attribution.json from closed shadow positions and CLV.</div>`;
    const cycleRows = [
      ["Active lane", legacyLiveActive ? "legacy live loop" : "shadow research + websocket"],
      ["Research status", `${researchStatus} / ${fmtAge(researchAge)} old`],
      ["Research started", shadowResearch.started_at_utc || "-"],
      ["Research ended", shadowResearch.ended_at_utc || "-"],
      ["Live source", legacyLiveActive ? (live.live_source || live.source || "websocket") : "websocket"],
      ["WS messages", websocket.new_messages],
      ["Latest WS features", websocketFeatures.returned_feature_rows || websocketFeatures.new_feature_rows || websocketFeatures.feature_rows],
      ["Retained feature rows", websocketFeatures.retained_feature_rows],
      ["Feature retention", websocketFeatures.feature_retention_hours ? `${websocketFeatures.feature_retention_hours}h / max ${websocketFeatures.max_feature_rows || "-"}` : "-"],
      ["Target assets", websocket.target_assets],
      ["Target source", websocket.target_source, v=>longText(v, 180)],
      ["Target families", websocket.target_family_counts, v=>longText(v, 220)],
      ["Proof blocker targets", websocket.target_paper_confirmation_blocker_counts, v=>longText(v, 180)],
      ["Historical breadth targets", websocket.target_historical_breadth_counts, v=>longText(v, 180)],
      ["Feedback targets", websocket.target_feedback_broaden_counts, v=>longText(v, 180)],
      ["Features", data.forward_paper_cycle?.features],
      ["Predictions", data.forward_paper_cycle?.predictions],
      ["Approved", data.forward_paper_cycle?.signals_approved],
      ["Rejected", data.forward_paper_cycle?.signals_rejected],
      ["Signal CSV rows", priceActionPaper.signal_file_rows],
      ["Signal summary rows", priceActionPaper.summary_signals],
      ["Signal mismatch", priceActionPaper.summary_signal_mismatch],
      ["Broker refresh", brokerRefreshLabel],
      ["Main reason", broker.entry_pause_reason || Object.keys(broker.broker_rejection_reasons || {}).join(", "), longText]
    ];
    if (legacyLiveActive) {
      cycleRows.splice(1, 0,
        ["Live tick", live.iteration],
        ["WS window", live.websocket_seconds == null ? "-" : live.websocket_seconds + "s"],
        ["Prediction cycle", live.prediction_cycle_seconds == null ? "-" : live.prediction_cycle_seconds + "s"],
        ["Assets", live.asset_count == null ? "-" : live.asset_count],
        ["Effective max assets", live.effective_max_assets == null ? "-" : live.effective_max_assets],
        ["Resource guard", resourceGuard.reason || "-"],
        ["Memory", resourceGuard.memory_percent == null ? "-" : fmtNum(resourceGuard.memory_percent, 1) + "%"],
        ["Full cycle", legacyFullCycle.display_status || live.full_prediction_cycle?.status],
        ["Discovery", discovery.status],
        ["Last scan", discovery.last_status],
        ["Next discovery", discovery.next_due_in_seconds == null ? "-" : Math.round(Number(discovery.next_due_in_seconds)) + "s"],
        ["Discovery #", discovery.discovery_iteration],
        ["Fast 5m", fastUpdown.tokens == null ? (fastUpdown.status || "-") : (fastUpdown.tokens + " tokens")],
        ["Fast assets", fastUpdown.assets, joinText]
      );
    }
    if (strategyGuardReason) {
      cycleRows.push(
        ["Strategy V2 guard", strategyGuardReason, v=>longText(v, 180)],
        ["Strategy V2 memory", freshness.strategy_v2_memory_percent == null ? "-" : fmtNum(freshness.strategy_v2_memory_percent, 1) + "% / " + fmtNum(freshness.strategy_v2_max_memory_percent, 1) + "%"]
      );
    }
    if (showMaintenance) {
      cycleRows.push(
        ["Paper maintenance", paperMaintenance.status],
        ["Maintenance task", paperMaintenanceTask.status],
        ["Task state", paperMaintenanceTask.task_state],
        ["Task interval", paperMaintenanceTask.interval_minutes == null ? "-" : paperMaintenanceTask.interval_minutes + "m"],
        ["Task next run", paperMaintenanceTask.next_run_time],
        ["Maintenance age", paperMaintenance.age_seconds == null ? "-" : fmtNum(paperMaintenance.age_seconds, 0) + "s"],
        ["Maintenance memory", paperMaintenance.memory_used_percent == null ? "-" : fmtNum(paperMaintenance.memory_used_percent, 1) + "% / " + fmtNum(paperMaintenance.max_memory_percent, 1) + "%"],
        ["Maintenance next exit", paperMaintenance.next_exit_due_utc],
        ["Maintenance reason", paperMaintenance.reason || joinText(paperMaintenance.work_reasons), v=>longText(v, 180)]
      );
    }
    document.getElementById("cycle").innerHTML = facts(cycleRows);
    document.getElementById("tradeDiagnostics").innerHTML = facts([
      ["Main blocker", diag.main_blocker, v=>longText(v, 220)],
      ["Recommended action", diag.recommended_action, v=>longText(v, 220)],
      ["Predictions", diag.prediction_count],
      ["Approved signals", diag.approved_signals_count],
      ["Rejected signals", diag.rejected_signals_count],
      ["Near-miss learning", diag.near_miss_candidates_seen],
      ["Near-miss opened", diag.near_miss_opened_this_cycle],
      ["Near-miss open", diag.near_miss_open_positions],
      ["Alpha-learning candidates", diag.alpha_candidate_learning_candidates_seen],
      ["Alpha-learning openable", diag.alpha_candidate_learning_openable_candidates_seen],
      ["Alpha-learning opened", diag.alpha_candidate_learning_opened_this_cycle],
      ["Alpha-learning open", diag.alpha_candidate_learning_open_positions],
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
    ]) + `<div style="height:12px"></div>` + table(diag.current_alpha_learning_candidates || [], [
      ["Market","market_slug"],
      ["Outcome","outcome"],
      ["Cohort","signal_cohort"],
      ["Lower-bound","edge_lower_bound", v=>fmtNum(v,4)],
      ["Price","executable_price", v=>fmtNum(v,4)],
      ["Spread","spread", v=>fmtNum(v,4)],
      ["Liquidity","liquidity", v=>fmtNum(v,2)],
      ["Why blocked","rejection_reason", v=>longText(v || "awaiting shadow evidence / cohort promotion", 180)]
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
      ["Status", strategyV2.status],
      ["Decision", strategyV2.decision],
      ["Recommended action", strategyV2.recommended_action, v=>longText(v, 240)],
      ["Runtime", strategyV2.runtime_reason, v=>longText(v, 180)],
      ["Report generated", strategyV2.generated_at_utc],
      ["Cycle status", strategyV2.cycle_status?.status],
      ["Cycle paper broker", strategyV2.paper_broker_status],
      ["Cycle buy fills", strategyV2.paper_broker_buy_fills],
      ["Cycle exit fills", strategyV2.paper_broker_exit_fills],
      ["Cycle broker rejects", strategyV2.paper_broker_rejections],
      ["Rows scored", strategyV2.rows_scored],
      ["Anchor rows loaded", strategyV2.anchor_rows_loaded],
      ["Alpha-validated anchors", strategyV2.alpha_validated_anchor_rows],
      ["World Cup subset", strategyV2.worldcup_validated_anchor_rows],
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
      ["Run-rate","monthly_run_rate_usdc", fmtEvidenceRunRate],
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
    document.getElementById("priceActionScout").innerHTML = facts([
      ["Decision", priceScout.decision],
      ["Ledger entries", priceScout.ledger_entries],
      ["Policy eligible", priceScout.policy_eligible_entries],
      ["Policy excluded", priceScout.policy_excluded_entries],
      ["Deduped evidence", priceScout.deduped_evidence_entries],
      ["Duplicate entries", priceScout.duplicate_policy_eligible_entries],
      ["New entries", priceScout.new_entries],
      ["Observed candidates", priceScout.observed_candidates],
      ["Closed round trips", priceScout.closed_trades],
      ["Open marks", priceScout.open_trades],
      ["Take-profit exits", priceScout.take_profit_exits],
      ["Stop-loss exits", priceScout.stop_loss_exits],
      ["Realized P&L", priceScout.realized_pnl_usdc, fmtUsd],
      ["Marked P&L", priceScout.total_mark_pnl_usdc, fmtUsd],
      ["Marked ROI", priceScout.mark_roi, v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Review candidates", priceScout.price_action_review_candidates],
      ["Paper-confirm blocker scout targets", priceScout.paper_confirmation_blocker_targets],
      ["Current analogue scout targets", priceScout.current_positive_analogue_targets],
      ["Label-headroom scout targets", priceScout.label_headroom_research_targets],
      ["Current analogue eligible", (currentPositiveAnalogues.targets || []).length],
      ["Current analogue blocked/headroom", (currentPositiveAnalogues.blocked_targets || []).length],
      ["Side-missing analogue targets", (sideMissingAnalogues.targets || []).length],
      ["Side-missing collect", sideMissingAnalogues.collection_queries, v=>longText(v, 180)],
      ["Model label hurdle", currentPositiveAnalogues.model_label_minimum_return, v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Paper signal decision", priceActionPaper.decision],
      ["Paper signals", priceActionPaper.signals],
      ["Paper rejections", priceActionPaper.rejections],
      ["Approved PA cohorts", priceActionPaper.approved_price_action_cohorts],
      ["Approved microstructure cohorts", priceActionPaper.approved_microstructure_cohorts],
      ["Paper-confirm backlog", priceActionPaper.paper_confirmation_candidates],
      ["Paper-confirm current matches", priceActionPaper.paper_confirmation_current_candidates],
      ["Paper-confirm executable signals", priceActionPaper.paper_confirmation_signals],
      ["Paper-confirm fresh matches", priceActionPaper.paper_confirmation_current_fresh_matches],
      ["Paper-confirm hist gate", priceActionPaper.paper_confirmation_current_historical_state],
      ["Paper-confirm hist blocked", priceActionPaper.paper_confirmation_current_historical_blocked],
      ["Paper-confirm top blocker", priceActionPaper.paper_confirmation_current_top_blocker],
      ["Paper-confirm top family", priceActionPaper.paper_confirmation_current_top_blocked_family],
      ["Paper-confirm hist why", priceActionPaper.paper_confirmation_current_historical_blockers, v=>longText(v, 180)],
      ["Paper-confirm family why", priceActionPaper.paper_confirmation_current_historical_blocked_by_family, v=>longText(v, 180)],
      ["Proof blocker target state", paperConfirmationBlockers.state, v=>longText(v, 160)],
      ["Proof blocker collect", paperConfirmationBlockers.collection_queries, v=>longText(v, 180)],
      ["Proof blocker in-band", paperConfirmationBlockers.in_band_targets],
      ["Proof blocker entry-band wait", paperConfirmationBlockers.entry_band_wait_targets],
      ["All-current analogue state", currentHistScan.state, v=>longText(v, 180)],
      ["All-current rows", currentHistScan.current_rows],
      ["All-current positive", currentHistScan.positive_matches],
      ["All-current blocked", currentHistScan.blocked],
      ["All-current blocked why", currentHistScan.blocked_by_state, v=>longText(v, 220)],
      ["All-current positive families", currentHistScan.positive_by_family, v=>longText(v, 180)],
      ["All-current next", currentHistScan.next_action, v=>longText(v, 240)],
      ["Historical breadth state", historicalBreadthScan.state, v=>longText(v, 180)],
      ["Historical events", historicalBreadthScan.total_events],
      ["Historical val ROI", historicalBreadthScan.validation_roi, v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Historical val + rows", historicalBreadthScan.validation_positive_rows],
      ["Robust hist buckets", historicalBreadthScan.robust_positive_buckets],
      ["Near-positive hist buckets", historicalBreadthScan.near_positive_buckets],
      ["Hist collect target", historicalBreadthScan.recommended_collection_queries, v=>longText(v, 180)],
      ["Historical next", historicalBreadthScan.next_action, v=>longText(v, 220)],
      ["Low-price evidence", priceActionPaper.low_price_tick_probe_evidence_state],
      ["Low-price val +", priceActionPaper.low_price_tick_validation_positive_rows],
      ["Low-price val ROI", priceActionPaper.low_price_tick_validation_roi, v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Low-price gate", priceActionPaper.low_price_tick_gate_reason],
      ["Low-price tick candidates", priceActionPaper.low_price_tick_probe_candidates],
      ["Low-price tick signals", priceActionPaper.low_price_tick_probe_signals],
      ["Broker refresh needed", priceActionPaper.broker_refresh_needed],
      ["Pending broker signals", priceActionPaper.pending_broker_signals],
      ["Pending broker confirmations", priceActionPaper.pending_broker_confirmation_signals],
      ["Signal generated", priceActionPaper.signal_generated_at_utc],
      ["Broker generated", priceActionPaper.broker_generated_at_utc],
      ["Broker freshness reason", priceActionPaper.broker_refresh_reason, v=>longText(v, 220)],
      ["Microstructure current rows", priceActionPaper.source_microstructure_current_rows]
    ]) + `<div style="height:12px"></div><h3>Current historical analogue matches</h3>` + table(currentHistScan.positive_preview || [], [
      ["Family","family"],
      ["Market","market_slug", v=>longText(v, 140)],
      ["Outcome","outcome"],
      ["Bid","latest_bid", v=>fmtNum(v,4)],
      ["Ask","latest_ask", v=>fmtNum(v,4)],
      ["Spread","latest_spread", v=>fmtNum(v,4)],
      ["Val rows","historical_analogue_validation_rows"],
      ["Val +","historical_analogue_positive_rows"],
      ["Val ROI","historical_analogue_validation_roi", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Robust gap","robust_validation_roi_gap", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Collect","recommended_collection_query"],
      ["Action","forward_shadow_action", v=>longText(v, 180)],
      ["Auth","trade_authorisation", v=>longText(v, 160)]
    ]) + `<div style="height:12px"></div><h3>Current positive analogue label-headroom gate</h3>` + table(currentPositiveAnalogues.targets || [], [
      ["Use","decision_use", v=>longText(v, 160)],
      ["Market","market_slug", v=>longText(v, 140)],
      ["Ask","latest_ask", v=>fmtNum(v,4)],
      ["Max return","max_possible_return", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Required","model_label_minimum_return", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Collect","recommended_collection_query"]
    ]) + `<div style="height:12px"></div>` + table(currentPositiveAnalogues.blocked_targets || [], [
      ["Blocked","block_reason", v=>longText(v, 220)],
      ["Market","market_slug", v=>longText(v, 140)],
      ["Ask","latest_ask", v=>fmtNum(v,4)],
      ["Max return","max_possible_return", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Required","model_label_minimum_return", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Collect","recommended_collection_query"]
    ]) + `<div style="height:12px"></div><h3>Side-missing positive analogue context targets</h3>` + table(sideMissingAnalogues.targets || [], [
      ["Use","decision_use", v=>longText(v, 180)],
      ["Family","family"],
      ["Market","market_slug", v=>longText(v, 140)],
      ["Ask","latest_ask", v=>fmtNum(v,4)],
      ["Side-free val rows","side_agnostic_validation_rows"],
      ["Side-free +","side_agnostic_positive_rows"],
      ["Side-free ROI","side_agnostic_validation_roi", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Collect","recommended_collection_query"],
      ["Auth","trade_authorisation", v=>longText(v, 160)]
    ]) + `<div style="height:12px"></div><h3>Current analogue blockers to learn from</h3>` + table(currentHistScan.blocked_preview || [], [
      ["Gate","historical_analogue_gate", v=>longText(v, 160)],
      ["Family","family"],
      ["Market","market_slug", v=>longText(v, 140)],
      ["Outcome","outcome"],
      ["Bid","latest_bid", v=>fmtNum(v,4)],
      ["Ask","latest_ask", v=>fmtNum(v,4)],
      ["Spread","latest_spread", v=>fmtNum(v,4)],
      ["Val rows","historical_analogue_validation_rows"],
      ["Val +","historical_analogue_positive_rows"],
      ["Val ROI","historical_analogue_validation_roi", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Bucket","historical_analogue_key", v=>longText(v, 220)]
    ]) + `<div style="height:12px"></div><h3>Prioritized proof blocker targets</h3>` + table(paperConfirmationBlockers.targets || [], [
      ["Use","decision_use", v=>longText(v, 180)],
      ["Collect","recommended_collection_query"],
      ["Gate","historical_analogue_gate", v=>longText(v, 160)],
      ["Cohort","signal_cohort", v=>longText(v, 180)],
      ["Family","family"],
      ["Market","market_slug", v=>longText(v, 140)],
      ["Outcome","outcome"],
      ["Ask","latest_ask", v=>fmtNum(v,4)],
      ["Spread","latest_spread", v=>fmtNum(v,4)],
      ["Val rows","historical_analogue_validation_rows"],
      ["Val +","historical_analogue_positive_rows"],
      ["Val ROI","historical_analogue_validation_roi", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Next","next_action", v=>longText(v, 220)]
    ]) + `<div style="height:12px"></div><h3>Paper-confirmation analogue blockers</h3>` + table((priceActionPaper.paper_confirmation_current_historical_analogue || {}).blocked_preview || [], [
      ["Gate","historical_analogue_gate", v=>longText(v, 160)],
      ["Cohort","signal_cohort", v=>longText(v, 180)],
      ["Market","market_slug", v=>longText(v, 140)],
      ["Outcome","outcome"],
      ["Bid","latest_bid", v=>fmtNum(v,4)],
      ["Ask","latest_ask", v=>fmtNum(v,4)],
      ["Spread","latest_spread", v=>fmtNum(v,4)],
      ["Val rows","historical_analogue_validation_rows"],
      ["Val +","historical_analogue_positive_rows"],
      ["Val ROI","historical_analogue_validation_roi", v=>fmtNum(Number(v) * 100, 2) + "%"]
    ]) + `<div style="height:12px"></div><h3>Historical breadth scan</h3>` + table(historicalBreadthScan.specs || [], [
      ["Spec","spec_id", v=>longText(v, 160)],
      ["Tested buckets","tested_buckets"],
      ["Val + buckets","positive_validation_buckets"],
      ["Robust buckets","robust_positive_buckets"]
    ]) + `<div style="height:12px"></div>` + table(historicalBreadthScan.top_robust_buckets || [], [
      ["Spec","spec_id", v=>longText(v, 120)],
      ["Bucket","key", v=>longText(v, 220)],
      ["Train rows","train_rows"],
      ["Train ROI","train_roi", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Val rows","validation_rows"],
      ["Val ROI","validation_roi", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Val win","validation_win_rate", v=>fmtNum(Number(v) * 100, 1) + "%"]
    ]) + `<div style="height:12px"></div><h3>Near-positive historical buckets to collect</h3>` + table(historicalBreadthScan.top_near_positive_buckets || [], [
      ["Spec","spec_id", v=>longText(v, 120)],
      ["Bucket","key", v=>longText(v, 220)],
      ["Collect","recommended_collection_query"],
      ["Blockers","blockers", v=>longText(v, 220)],
      ["Train rows","train_rows"],
      ["Train ROI","train_roi", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Val rows","validation_rows"],
      ["Val ROI","validation_roi", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Concentration","validation_top_positive_token_pnl_share", v=>fmtNum(Number(v) * 100, 1) + "%"]
    ]) + `<div style="height:12px"></div><h3>Paper-confirmation probe exit watch</h3>` + facts([
      ["Status", probeExitWatch.status],
      ["Open probes", probeExitWatch.open_confirmation_probes],
      ["Fixed-horizon due", probeExitWatch.fixed_horizon_due_count],
      ["Next due", probeExitWatch.next_due_minutes == null ? "-" : fmtNum(probeExitWatch.next_due_minutes, 1) + "m"],
      ["Next due UTC", probeExitWatch.next_due_utc],
      ["Missing horizon", probeExitWatch.missing_horizon_count]
    ]) + `<div style="height:12px"></div>` + table(probeExitWatch.preview || [], [
      ["Cohort","signal_cohort", v=>longText(v, 160)],
      ["Market","market_slug", v=>longText(v, 140)],
      ["Outcome","outcome"],
      ["Age","age_minutes", v=>fmtNum(v, 1) + "m"],
      ["Horizon","max_hold_minutes_before_exit", v=>fmtNum(v, 1) + "m"],
      ["Due in","due_in_minutes", v=>fmtNum(v, 1) + "m"],
      ["Due","fixed_horizon_due"],
      ["Cost","cost_basis_usdc", fmtUsd]
    ]) + `<div style="height:12px"></div><h3>Price-action scout by cohort</h3>` + table(priceScout.cohorts || [], [
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
    ]) + `<div style="height:12px"></div><h3>Price-action scout candidate marks</h3>` + table(priceScout.top_candidates || [], [
      ["Source","source"],
      ["Cohort","signal_cohort"],
      ["Market","market_slug", v=>longText(v, 120)],
      ["Outcome","outcome"],
      ["Entry","entry_price", v=>fmtNum(v,4)],
      ["Latest bid","latest_bid", v=>fmtNum(v,4)],
      ["Exit","exit_price", v=>fmtNum(v,4)],
      ["Status","round_trip_status", v=>longText(v, 120)],
      ["Realized P&L","realized_pnl_usdc", fmtUsd],
      ["MTM P&L","mark_pnl_usdc", fmtUsd],
      ["Reason","candidate_reason", v=>longText(v, 140)]
    ]) + `<div style="height:12px"></div><h3>Price-action paper signals</h3>` + table(priceActionPaper.top_signals || [], [
      ["Market","market_slug", v=>longText(v, 120)],
      ["Outcome","outcome"],
      ["Ask","executable_price", v=>fmtNum(v,4)],
      ["Bid","price_action_latest_bid", v=>fmtNum(v,4)],
      ["Edge","edge", v=>fmtNum(v,4)],
      ["Max stake","max_stake_usdc", fmtUsd],
      ["Cohort","signal_cohort", v=>longText(v, 140)]
    ]) + `<div style="height:12px"></div><h3>Price-action paper rejections</h3>` + table(priceActionPaper.top_rejections || [], [
      ["Market","market_slug", v=>longText(v, 120)],
      ["Outcome","outcome"],
      ["Cohort","signal_cohort", v=>longText(v, 140)],
      ["Status","round_trip_status"],
      ["Reason","rejection_reason", v=>longText(v, 160)]
    ]);
    document.getElementById("microstructureLab").innerHTML = facts([
      ["Decision", microstructure.decision],
      ["Source rows", microstructure.source_rows],
      ["Tokens", microstructure.tokens],
      ["Trade events", microstructure.trade_events],
      ["Rules tested", microstructure.rule_rows],
      ["Validation-pass rules", microstructure.validation_pass_rules],
      ["Family-pass rules", microstructure.family_validation_pass_rules],
      ["Exit-policy pass rules", microstructure.exit_policy_validation_pass_rules],
      ["Current candidates", microstructure.current_candidates],
      ["Top rule", microstructure.top_rule?.rule_id, v=>longText(v, 180)],
      ["Top validation ROI", microstructure.top_rule?.validation_roi, v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Top validation P&L", microstructure.top_rule?.validation_pnl_usdc, fmtUsd]
    ]) + `<div style="height:12px"></div><h3>Microstructure rule leaderboard</h3>` + table(microstructure.top_rules || [], [
      ["Rule","rule_id", v=>longText(v, 180)],
      ["Family","rule_family"],
      ["Val trades","validation_trades"],
      ["Val ROI","validation_roi", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Val P&L","validation_pnl_usdc", fmtUsd],
      ["Win rate","validation_win_rate", v=>fmtNum(Number(v) * 100, 1) + "%"],
      ["Pass","validation_pass"],
      ["Status","status", v=>longText(v, 140)]
    ]) + `<div style="height:12px"></div><h3>Family microstructure leaderboard</h3>` + table(microstructure.top_family_rules || [], [
      ["Market family","market_family"],
      ["Rule","rule_id", v=>longText(v, 180)],
      ["Cohort","signal_cohort", v=>longText(v, 180)],
      ["Val trades","validation_trades"],
      ["Val ROI","validation_roi", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Val P&L","validation_pnl_usdc", fmtUsd],
      ["Pass","validation_pass"],
      ["Status","status", v=>longText(v, 140)]
    ]) + `<div style="height:12px"></div><h3>Exit-policy leaderboard</h3>` + table(microstructure.top_exit_policy_rules || [], [
      ["Exit","exit_policy_id"],
      ["Market family","market_family"],
      ["Rule","rule_id", v=>longText(v, 150)],
      ["TP","take_profit_return", v=>fmtNum(Number(v) * 100, 1) + "%"],
      ["SL","stop_loss_return", v=>fmtNum(Number(v) * 100, 1) + "%"],
      ["Horizon","max_forward_observations"],
      ["Val trades","validation_trades"],
      ["Val ROI","validation_roi", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Val P&L","validation_pnl_usdc", fmtUsd],
      ["Pass","validation_pass"]
    ]) + `<div style="height:12px"></div><h3>Current microstructure shadow candidates</h3>` + table(microstructure.current_candidates_preview || [], [
      ["Exit","exit_policy_id"],
      ["Rule","rule_id", v=>longText(v, 160)],
      ["Market","market_slug", v=>longText(v, 120)],
      ["Outcome","outcome"],
      ["Bid","latest_bid", v=>fmtNum(v,4)],
      ["Ask","latest_ask", v=>fmtNum(v,4)],
      ["Bid move","bid_move_abs", v=>fmtNum(v,4)],
      ["Val ROI","validation_roi", v=>fmtNum(Number(v) * 100, 2) + "%"]
    ]);
    document.getElementById("algoReplay").innerHTML = `<div class="sectionLead">Offline, shadow-only replay of event-driven strategies against recorded websocket quotes. Negative mark-to-bid P&L means do not promote the strategy.</div>` + facts([
      ["Strategies replayed", algoReplay.strategy_count],
      ["Best strategy", bestReplay.strategy || "-"],
      ["Best replay P&L", bestReplay.unrealised_mark_to_bid_pnl_usdc, fmtUsd],
      ["Best replay cost", bestReplay.total_cost_usdc, fmtUsd],
      ["Best fills", bestReplay.fills],
      ["Best intents", bestReplay.intents_emitted],
      ["Paper invoked", bestReplay.paper_trading_invoked],
      ["Live invoked", bestReplay.live_trading_invoked]
    ]) + titledTable("Replay strategy scorecard", algoReplaySummaries, [
      ["Strategy","strategy", v=>longText(v, 150)],
      ["Status","status"],
      ["Events","events_processed"],
      ["Intents","intents_emitted"],
      ["Fills","fills"],
      ["Cost","total_cost_usdc", fmtUsd],
      ["Mark-to-bid P&L","unrealised_mark_to_bid_pnl_usdc", fmtUsd],
      ["Resting","resting_orders_at_end"],
      ["Paper/live","paper_trading_invoked", (v,row)=>`${v ? "paper" : "no paper"} / ${row.live_trading_invoked ? "live" : "no live"}`]
    ], 8);
    const sweepCombos = Array.isArray(algoSweep.combos) ? algoSweep.combos : [];
    const sweepByStrategy = algoSweep.by_strategy || {};
    const sweepByStrategyRows = Object.values(sweepByStrategy || {}).map(row => {
      const selected = row.selected || {};
      return {...row, selected_params: selected.params || "-", selected_train_pnl_usdc: selected.train_pnl_usdc, selected_validation_pnl_usdc: selected.validation_pnl_usdc};
    });
    const sweepSelected = algoSweep.selected || {};
    const sweepSelectedParams = sweepSelected.params !== undefined
      ? sweepSelected.params
      : sweepSelected.tight_spread_maximum !== undefined
        ? `spread<=${fmtNum(sweepSelected.tight_spread_maximum, 4)}, imbalance>=${fmtNum(sweepSelected.minimum_book_imbalance, 4)}`
        : "-";
    document.getElementById("algoSweep").innerHTML = Object.keys(algoSweep).length
      ? `<div class="sectionLead">Train-only parameter sweep over recorded websocket history, then one out-of-sample validation score. A validated result is shadow research only.</div>` + facts([
          ["Decision", algoSweep.decision || "-"],
          ["Default strategy", algoSweep.strategy || "-"],
          ["Selected strategy", sweepSelected.strategy || "-"],
          ["Events", algoSweep.events_total],
          ["Train / validation", `${algoSweep.train_events ?? "-"} / ${algoSweep.validation_events ?? "-"}`],
          ["Combos tested", algoSweep.combos_tested],
          ["Train candidates", algoSweep.train_candidates],
          ["Selected params", sweepSelectedParams, v=>longText(v, 180)],
          ["Selected train P&L", sweepSelected.train_pnl_usdc, fmtUsd],
          ["Selected validation P&L", sweepSelected.validation_pnl_usdc, fmtUsd],
          ["Governance note", algoSweep.governance_note || "Shadow research only; no trading gate changed.", v=>longText(v, 260)]
        ]) + titledTable("Best by strategy", sweepByStrategyRows, [
          ["Strategy","strategy", v=>longText(v, 150)],
          ["Combos","combos_tested"],
          ["Candidates","train_candidates"],
          ["Decision","decision", v=>longText(v, 150)],
          ["Selected params","selected_params", v=>longText(v, 180)],
          ["Train P&L","selected_train_pnl_usdc", fmtUsd],
          ["Validation P&L","selected_validation_pnl_usdc", fmtUsd]
        ], 8) + titledTable("Sweep combinations", sweepCombos, [
          ["Strategy","strategy", v=>longText(v, 140)],
          ["Params","params", v=>longText(v, 170)],
          ["Spread max","tight_spread_maximum", v=>fmtNum(v, 4)],
          ["Min imbalance","minimum_book_imbalance", v=>fmtNum(v, 4)],
          ["Train fills","train_fills"],
          ["Train P&L","train_pnl_usdc", fmtUsd],
          ["Validation fills","validation_fills"],
          ["Validation P&L","validation_pnl_usdc", fmtUsd],
          ["Candidate","train_candidate"],
          ["Selected","selected"],
          ["Status","status"]
        ], 10)
      : `<div class="sectionLead">No sweep run yet. The next governance refresh should build algo_sweep_summary.json once websocket features exist.</div>`;
    document.getElementById("priceActionModel").innerHTML = facts([
      ["Decision", priceActionModel.decision],
      ["Promotion ready", priceActionModel.promotion_ready],
      ["Objective", priceActionModel.trading_objective, v=>longText(v, 220)],
      ["Label", priceActionModel.label_definition, v=>longText(v, 260)],
      ["Training events", priceActionModel.training_events],
      ["Train rows", priceActionModel.train_rows],
      ["Validation rows", priceActionModel.validation_rows],
      ["Train positive targets", priceActionModel.train_positive_targets],
      ["Validation positive targets", priceActionModel.validation_positive_targets],
      ["Validation gap", priceActionModel.validation_gap?.reason, v=>longText(v, 220)],
      ["Gap collection", priceActionModel.validation_gap?.collection_queries, joinText],
      ["Cohort transfer", priceActionModel.cohort_transfer?.reason, v=>longText(v, 240)],
      ["Family transfer overlap", priceActionModel.cohort_transfer?.family?.overlap_positive_cohorts, joinText],
      ["Validation-only positives", priceActionModel.cohort_transfer?.family?.validation_only_positive_cohorts, joinText],
      ["Training sources", priceActionModel.training_event_sources, v=>longText(v, 260)],
      ["Chosen probability threshold", priceActionModel.chosen_probability_threshold, v=>fmtNum(v, 3)],
      ["Validation threshold", priceActionModel.validation_probability_threshold, v=>fmtNum(v, 3)],
      ["Validation threshold policy", priceActionModel.validation_threshold_policy?.threshold_source, v=>longText(v, 160)],
      ["Current threshold policy", priceActionModel.current_threshold_policy?.threshold_source, v=>longText(v, 160)],
      ["Selected validation trades", priceActionModel.validation_selected?.selected_trades],
      ["Selected validation ROI", priceActionModel.validation_selected?.selected_roi, v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Selected validation win rate", priceActionModel.validation_selected?.selected_win_rate, v=>fmtNum(Number(v) * 100, 1) + "%"],
      ["Win/payoff gate", priceActionModel.validation_win_rate_gate],
      ["Win/payoff gate reason", priceActionModel.validation_win_rate_gate_reason, v=>longText(v, 180)],
      ["Validation rank lesson", priceActionModel.validation_rank_diagnostics?.state, v=>longText(v, 180)],
      ["Rank next action", priceActionModel.validation_rank_diagnostics?.next_action, v=>longText(v, 220)],
      ["Best positive rank", priceActionModel.validation_rank_diagnostics?.best_positive_rank],
      ["Missed positive targets", priceActionModel.validation_rank_diagnostics?.missed_positive_targets],
      ["Selected profit factor", priceActionModel.validation_selected?.selected_profit_factor, v=>fmtNum(v, 2)],
      ["Selected payoff ratio", priceActionModel.validation_selected?.selected_payoff_ratio, v=>fmtNum(v, 2)],
      ["Selected expectancy/trade", priceActionModel.validation_selected?.selected_expectancy_usdc_per_trade, fmtUsd],
      ["Buy-all baseline ROI", priceActionModel.validation_buy_all_baseline?.roi, v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Edge over baseline", priceActionModel.validation_edge_over_buy_all_roi, v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Selected ROI CI95", priceActionModel.validation_selected_roi_ci95, joinText],
      ["Selected max drawdown", priceActionModel.validation_selected_risk?.max_drawdown, v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Selected VaR 95", priceActionModel.validation_selected_risk?.historical_var_95, v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Selected CVaR 95", priceActionModel.validation_selected_risk?.conditional_var_95, v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Quant promotion rule", priceActionModel.quant_promotion_decision?.reason, v=>longText(v, 180)],
      ["Model blockers", priceActionModel.blockers, joinText],
      ["Validation blockers", priceActionModel.validation_blockers, joinText],
      ["Current rows scored", priceActionModel.current_rows_scored],
      ["Current model candidates", priceActionModel.current_model_candidates]
    ]) + `<div style="height:12px"></div><h3>Current ML shadow candidates</h3>` + table(priceActionModel.current_candidates_preview || [], [
      ["Market","market_slug", v=>longText(v, 130)],
      ["Family","family", v=>longText(v, 120)],
      ["Outcome","outcome"],
      ["Bid","latest_bid", v=>fmtNum(v,4)],
      ["Ask","latest_ask", v=>fmtNum(v,4)],
      ["Chase","chase_pressure", v=>fmtNum(v, 4)],
      ["Low price","low_price_convexity", v=>fmtNum(v, 4)],
      ["Pred prob","predicted_reprice_probability", v=>fmtNum(Number(v) * 100, 1) + "%"],
      ["Exp ROI","predicted_expected_roi", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Shadow","shadow_only"]
    ]) + `<div style="height:12px"></div><h3>Training threshold grid</h3>` + table(priceActionModel.train_threshold_selection?.threshold_grid || [], [
      ["Threshold","threshold", v=>fmtNum(v,3)],
      ["Trades","selected_trades"],
      ["ROI","selected_roi", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["P&L","selected_pnl_usdc", fmtUsd],
      ["Win rate","selected_win_rate", v=>fmtNum(Number(v) * 100, 1) + "%"],
      ["Profit factor","selected_profit_factor", v=>fmtNum(v, 2)],
      ["Payoff","selected_payoff_ratio", v=>fmtNum(v, 2)],
      ["Exp/trade","selected_expectancy_usdc_per_trade", fmtUsd],
      ["Win/payoff","train_win_rate_gate_reason", v=>longText(v, 120)],
      ["Pass","train_threshold_pass"]
    ]) + `<div style="height:12px"></div><h3>Validation rank lesson</h3>` + table(priceActionModel.validation_rank_diagnostics?.top_selected_examples || [], [
      ["Rank","rank"],
      ["Market","market_slug", v=>longText(v, 130)],
      ["Family","family", v=>longText(v, 120)],
      ["Prob","predicted_reprice_probability", v=>fmtNum(Number(v) * 100, 1) + "%"],
      ["Chase","chase_pressure", v=>fmtNum(v, 4)],
      ["Low price","low_price_convexity", v=>fmtNum(v, 4)],
      ["Target","target"],
      ["ROI","roi", v=>fmtNum(Number(v) * 100, 2) + "%"]
    ]) + `<div style="height:12px"></div><h3>Missed positive repricing examples</h3>` + table(priceActionModel.validation_rank_diagnostics?.missed_positive_examples || [], [
      ["Rank","rank"],
      ["Market","market_slug", v=>longText(v, 130)],
      ["Family","family", v=>longText(v, 120)],
      ["Prob","predicted_reprice_probability", v=>fmtNum(Number(v) * 100, 1) + "%"],
      ["Chase","chase_pressure", v=>fmtNum(v, 4)],
      ["Low price","low_price_convexity", v=>fmtNum(v, 4)],
      ["ROI","roi", v=>fmtNum(Number(v) * 100, 2) + "%"]
    ]) + `<div style="height:12px"></div><h3>Validation family rank summary</h3>` + table(priceActionModel.validation_rank_diagnostics?.family_summary || [], [
      ["Family","family", v=>longText(v, 140)],
      ["Rows","rows"],
      ["Positives","positive_targets"],
      ["Selected","selected"],
      ["Sel +","selected_positive_targets"],
      ["Sel ROI","selected_roi", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Best + rank","best_positive_rank"]
    ]) + `<div style="height:12px"></div><h3>Cohort transfer check</h3>` + table(priceActionModel.cohort_transfer?.family?.top_cohorts || [], [
      ["Family","cohort", v=>longText(v, 150)],
      ["Train rows","train_rows"],
      ["Train tradable +","train_positive_targets"],
      ["Train ROI","train_roi", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Val rows","validation_rows"],
      ["Val tradable +","validation_positive_targets"],
      ["Val ROI","validation_roi", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Transfers","positive_target_transfer"]
    ]);
    document.getElementById("quantResearch").innerHTML = facts([
      ["Implementation", `${quantResearch.implemented_modules ?? 0}/${quantResearch.total_modules ?? 8} modules`],
      ["Complete", quantResearch.implementation_complete],
      ["Trading posture", quantResearch.trading_posture, v=>longText(v, 220)],
      ["Bot integrations", quantResearch.bot_integrations, joinText],
      ["Non-negotiables", quantResearch.non_negotiables, joinText],
      ["Current model decision", quantResearch.price_action_model?.decision, v=>longText(v, 180)],
      ["Current blockers", quantResearch.price_action_model?.blockers, joinText],
      ["Collect next", quantResearch.price_action_feedback?.collection_queries, joinText]
    ]) + `<div style="height:12px"></div><h3>Curriculum modules</h3>` + table(quantResearch.curriculum_modules || [], [
      ["#","module"],
      ["Name","name", v=>longText(v, 180)],
      ["Code","code_module", v=>longText(v, 140)],
      ["Implemented","implemented"]
    ]);
    document.getElementById("priceActionFeedback").innerHTML = facts([
      ["Learning state", priceActionFeedback.learning_state],
      ["Next action", priceActionFeedback.next_action, v=>longText(v, 220)],
      ["Target / month", priceActionFeedback.target_monthly_profit_usdc, fmtUsd],
      ["Best positive evidence", routeEvidenceDisplay("shadow"), v=>longText(v, 220)],
      ["Monthly goal gap", priceActionFeedback.monthly_goal_gap_usdc, fmtUsd],
      ["Promotion candidates", priceActionFeedback.promotion_candidates],
      ["Positive collect candidates", priceActionFeedback.positive_collect_candidates],
      ["Suppressed candidates", priceActionFeedback.suppressed_candidates],
      ["Forward paper cohorts", priceActionFeedback.forward_paper_cohorts],
      ["Forward paper positive", priceActionFeedback.forward_paper_positive_cohorts],
      ["Forward paper evidence", routeEvidenceDisplay("paper"), v=>longText(v, 220)],
      ["Forward paper gap", priceActionFeedback.forward_paper_goal_gap_usdc, fmtUsd],
      ["Forward shadow cohorts", priceActionFeedback.forward_shadow_cohorts],
      ["Forward shadow positive", priceActionFeedback.forward_shadow_positive_cohorts],
      ["Paper-confirmation backlog", priceActionFeedback.paper_confirmation_candidates],
      ["Collect next", priceActionFeedback.collection_queries, joinText],
      ["Suppress", priceActionFeedback.suppressed_queries, joinText]
    ]) + `<div style="height:12px"></div><h3>Forward paper P&L by signal cohort</h3>` + table(priceActionFeedback.forward_paper_preview || [], [
      ["Cohort","cohort", v=>longText(v, 180)],
      ["Action","action", v=>longText(v, 160)],
      ["Buys","buy_fills"],
      ["Closed","closed_trades"],
      ["Open","open_trades"],
      ["P&L","forward_paper_pnl_usdc", fmtUsd],
      ["ROI","forward_paper_roi", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Run-rate","monthly_run_rate_usdc", fmtEvidenceRunRate],
      ["Trusted","trusted_for_goal"],
      ["Blocker","forward_edge_blocker", v=>longText(v, 160)],
      ["Source","promotion_evidence_source"],
      ["Reason","reason", v=>longText(v, 200)]
    ]) + `<div style="height:12px"></div><h3>Forward shadow P&L requiring paper confirmation</h3>` + table(priceActionFeedback.forward_shadow_preview || [], [
      ["Cohort","cohort", v=>longText(v, 180)],
      ["Action","action", v=>longText(v, 160)],
      ["Shadow buys","shadow_fills"],
      ["Shadow closed","shadow_sell_fills"],
      ["P&L","forward_shadow_pnl_usdc", fmtUsd],
      ["ROI","forward_shadow_roi", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Run-rate","monthly_run_rate_usdc", fmtEvidenceRunRate],
      ["Trusted","trusted_for_goal"],
      ["Blocker","forward_edge_blocker", v=>longText(v, 160)],
      ["Reason","reason", v=>longText(v, 200)]
    ]) + `<div style="height:12px"></div><h3>Paper-confirmation backlog from trusted shadow edge</h3>` + table(priceActionFeedback.paper_confirmation_preview || [], [
      ["Cohort","cohort", v=>longText(v, 180)],
      ["Query","recommended_collection_query"],
      ["Shadow P&L","forward_shadow_pnl_usdc", fmtUsd],
      ["Shadow ROI","forward_shadow_roi", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Shadow run-rate","monthly_run_rate_usdc", fmtEvidenceRunRate],
      ["Needed","reason", v=>longText(v, 220)]
    ]) + `<div style="height:12px"></div><h3>Price-action feedback cohort leaderboard</h3>` + table(priceActionFeedback.top_cohorts || [], [
      ["Source","source"],
      ["Cohort","cohort", v=>longText(v, 180)],
      ["Action","action", v=>longText(v, 160)],
      ["Query","recommended_collection_query"],
      ["Closed","closed_trades"],
      ["Val trades","validation_trades"],
      ["Realized P&L","realized_pnl_usdc", fmtUsd],
      ["Realized ROI","realized_roi", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["MTM P&L","total_mark_pnl_usdc", fmtUsd],
      ["Run-rate","monthly_run_rate_usdc", fmtEvidenceRunRate],
      ["Reason","reason", v=>longText(v, 200)]
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
      ["Run-rate","monthly_run_rate_usdc", fmtEvidenceRunRate],
      ["Fills","buy_fills"],
      ["Settled","settled_fills"],
      ["Reason","promotion_reason"]
    ]);
    document.getElementById("shadowPromotionWatchlist").innerHTML = table(data.shadow_signal_cohort_pnl?.promotion_watchlist || [], [
      ["Cohort","signal_cohort"],
      ["Score","promotion_ready_score", (v,row)=>`${v ?? 0}/${row.promotion_ready_checks ?? "?"}`],
      ["Shadow P&L","shadow_total_pnl_usdc", fmtUsd],
      ["ROI","shadow_roi", v=>fmtNum(Number(v) * 100, 2) + "%"],
      ["Run-rate","shadow_monthly_run_rate_usdc", fmtEvidenceRunRate],
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
    const fetchHealth = data.sharp_fetch_health || {};
    const fetchHealthKind = fetchHealth.health === "ok" ? "good" : (fetchHealth.health === "attention" ? "bad" : "warn");
    const fetchHealthBadge = Object.keys(fetchHealth).length
      ? `<div class="alertGrid">${alertBox(
          "Sharp odds fetch health: " + (fetchHealth.health || "unknown"),
          "status=" + escapeHtml(String(fetchHealth.status ?? "-"))
          + " · provider=" + escapeHtml(String(fetchHealth.provider_status ?? "-"))
          + " · rows=" + escapeHtml(String(fetchHealth.rows ?? "-"))
          + " · Odds API requests left=" + escapeHtml(String(fetchHealth.requests_remaining ?? "unknown"))
          + " · last fetch " + escapeHtml(String(fetchHealth.generated_at_utc || "not yet run")),
          fetchHealthKind)}</div>`
      : "";
    document.getElementById("independentFundamentals").innerHTML = fetchHealthBadge + table([
      { anchor: "Sharp odds fetch", ...(anchors.sharp_odds_fetch || {}) },
      { anchor: "Sharp de-vig anchor", ...(anchors.sharp_anchor || {}) },
      { anchor: "Sharp anchor reconciliation", ...((anchors.sharp_anchor_coverage || {}).anchor_funnel || {}) },
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
    ]) + `<div style="height:12px"></div><h3>Sharp anchor coverage by sport</h3>` +
    table((anchors.sharp_anchor || {}).coverage_by_sport_market || [], [
      ["Sport","sport", v=>longText(v, 120)],
      ["Market","market_key"],
      ["Rows in","rows_in"],
      ["Priced","priced_rows"],
      ["Mapped","fundamental_rows"],
      ["No token","skipped_no_token"],
      ["Public search joins","h2h_public_search_token_joins"],
      ["Token-map joins","token_map_joins"],
      ["Winner joins","worldcup_winner_token_joins"],
      ["Incomplete rows","incomplete_market_rows"]
    ]) + `<div style="height:12px"></div><h3>Anchor reconciliation by source / sport / market</h3>` +
    table((anchors.sharp_anchor_coverage || {}).source_sport_markets || [], [
      ["Source","source", v=>longText(v, 100)],
      ["Sport","sport", v=>longText(v, 130)],
      ["Market","market_key"],
      ["Fetched","source_rows_fetched"],
      ["Normalised","source_rows_normalized"],
      ["Audited","mapping_audit_rows"],
      ["PM eligible","polymarket_rows_eligible"],
      ["Mapped","rows_mapped"],
      ["Current join","rows_joined"],
      ["Map rate","mapping_rate", fmtPct],
      ["Join rate","join_rate", fmtPct],
      ["Ambiguous","ambiguous_rows"],
      ["Stale","stale_rows"],
      ["Missing timestamp","missing_anchor_timestamp_rows"],
      ["Ask divergence","mean_executable_buy_divergence", v=>v == null ? "-" : fmtNum(v, 4)],
      ["Zero join","zero_current_join"],
      ["Actionable","actionable_rows"],
      ["Conserved","denominator_conservation_ok"],
      ["Accounting","accounting_status", v=>longText(v, 130)]
    ]);
    const performanceRows = Array.isArray(performanceFactsheet.series) ? performanceFactsheet.series : [];
    document.getElementById("performanceFactsheet").innerHTML =
      `<div class="muted">Reporting only. Paper, shadow, and modeled rows are simulated; only a sample-qualified live-real-money row can ever be externally presentable.</div>` +
      `<div style="height:12px"></div>` +
      table(performanceRows, [
        ["Series","label", v=>longText(v, 140)],
        ["Evidence","evidence_class"],
        ["Days","n_days"],
        ["Cumulative","cumulative_return", v=>v === null || v === undefined ? "n/a" : fmtNum(Number(v) * 100, 2) + "%"],
        ["Sharpe (90% CI)","annualised_sharpe", (v,row)=>v === null || v === undefined
          ? `n/a (${escapeHtml(row.annualised_metrics_reason || "insufficient daily data")})`
          : `${fmtNum(v, 2)} [${fmtNum(row.bootstrap_sharpe_90_ci?.low, 2)}, ${fmtNum(row.bootstrap_sharpe_90_ci?.high, 2)}]`],
        ["Sortino","annualised_sortino", v=>fmtNum(v, 2)],
        ["Max DD","max_drawdown_depth", (v,row)=>`${fmtNum(Number(v) * 100, 2)}% / ${plain(row.max_drawdown_duration_days)}d`],
        ["Profit factor","profit_factor", v=>fmtNum(v, 2)],
        ["External ready","external_presentation_ready", v=>v ? "yes" : "no"]
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
    const staleOpenPositions = Array.isArray(broker.stale_open_positions) ? broker.stale_open_positions : [];
    const staleOpenWarning = staleOpenPositions.length
      ? alertBox("Stale open paper positions", `${staleOpenPositions.length} position(s) are past market close without fresh executable quotes or clean resolution evidence. Do not trust raw equity until they settle or receive fresh quotes.`, "bad")
        + table(staleOpenPositions, [
          ["Market","question", (v,row)=>marketLabel(row)],
          ["Outcome","outcome"],
          ["Cost","cost_basis_usdc", fmtUsd],
          ["Close","close_time"],
          ["Hours past close","hours_past_close", v=>fmtNum(v,2)],
          ["Quote state","quote_state"]
        ])
        + `<div style="height:12px"></div>`
      : "";
    document.getElementById("positions").innerHTML = staleOpenWarning + table(data.positions, [
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


def _decode_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _paper_probe_exit_watch(positions: list[dict[str, Any]], orders: list[dict[str, Any]]) -> dict[str, Any]:
    """Track open paper-confirmation probes against their fixed learning horizon."""
    latest_buy_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for order in orders:
        if str(order.get("side") or "") != "BUY_YES":
            continue
        key = (str(order.get("market_id") or ""), str(order.get("token_id") or ""))
        if not key[0] and not key[1]:
            continue
        current = latest_buy_by_key.get(key)
        current_time = parse_timestamp(current.get("created_at")) if current else None
        order_time = parse_timestamp(order.get("created_at"))
        if current is None or (order_time or datetime.min.replace(tzinfo=timezone.utc)) >= (
            current_time or datetime.min.replace(tzinfo=timezone.utc)
        ):
            latest_buy_by_key[key] = order

    now = datetime.now(timezone.utc)
    probes: list[dict[str, Any]] = []
    for position in positions:
        key = (str(position.get("market_id") or ""), str(position.get("token_id") or ""))
        order = latest_buy_by_key.get(key)
        if not order:
            continue
        signal = _decode_json_object(order.get("source_signal_json"))
        if str(signal.get("price_action_entry_source") or "") != "paper_confirmation_candidate":
            continue
        opened_at = parse_timestamp(position.get("updated_at")) or parse_timestamp(order.get("created_at"))
        if opened_at is None:
            age_minutes = None
        else:
            age_minutes = max(0.0, (now - opened_at.astimezone(timezone.utc)).total_seconds() / 60.0)
        max_hold = safe_float(signal.get("max_hold_minutes_before_exit"))
        due_in = None
        due = False
        if max_hold is not None and max_hold > 0 and age_minutes is not None:
            due_in = max(0.0, float(max_hold) - age_minutes)
            due = due_in <= 0.0
        probes.append(
            {
                "market_id": key[0],
                "token_id": key[1],
                "market_slug": signal.get("market_slug", ""),
                "question": signal.get("question", ""),
                "outcome": signal.get("outcome", ""),
                "signal_cohort": signal.get("signal_cohort", ""),
                "opened_at_utc": position.get("updated_at") or order.get("created_at"),
                "age_minutes": None if age_minutes is None else round(age_minutes, 2),
                "max_hold_minutes_before_exit": max_hold,
                "due_in_minutes": None if due_in is None else round(due_in, 2),
                "fixed_horizon_due": due,
                "cost_basis_usdc": safe_float(position.get("cost_basis_usdc")),
                "quantity": safe_float(position.get("quantity")),
            }
        )

    due_count = sum(1 for probe in probes if probe.get("fixed_horizon_due"))
    due_in_values = [
        float(probe["due_in_minutes"])
        for probe in probes
        if probe.get("due_in_minutes") is not None
    ]
    next_due = 0.0 if due_count else min(due_in_values) if due_in_values else None
    missing_horizon = sum(1 for probe in probes if probe.get("max_hold_minutes_before_exit") is None)
    probes.sort(
        key=lambda probe: (
            0 if probe.get("fixed_horizon_due") else 1,
            float(probe.get("due_in_minutes")) if probe.get("due_in_minutes") is not None else 999999.0,
        )
    )
    return {
        "status": "no_open_confirmation_probes" if not probes else "fixed_horizon_due" if due_count else "monitoring",
        "open_confirmation_probes": len(probes),
        "fixed_horizon_due_count": due_count,
        "missing_horizon_count": missing_horizon,
        "next_due_minutes": None if next_due is None else round(next_due, 2),
        "next_due_utc": (
            (now if next_due == 0 else now + timedelta(minutes=float(next_due))).strftime("%Y-%m-%dT%H:%M:%SZ")
            if next_due is not None
            else ""
        ),
        "preview": probes[:10],
    }


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


def _top_counter_label(counts: Any) -> str:
    if not isinstance(counts, dict) or not counts:
        return ""
    def _count(item: tuple[Any, Any]) -> float:
        return safe_float(item[1]) or 0.0

    key, count = max(counts.items(), key=_count)
    return f"{key} ({int(safe_float(count) or 0)})"


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


def _mispricing_alpha_bridge_status(
    *,
    alpha_summary: dict[str, Any],
    independent_anchor_status: dict[str, Any],
    predictions: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
) -> dict[str, Any]:
    """Summarise the sharp/fundamental anchor path into a decision-useful bridge status."""
    alpha_summary = alpha_summary if isinstance(alpha_summary, dict) else {}
    independent_anchor_status = independent_anchor_status if isinstance(independent_anchor_status, dict) else {}
    sharp_fetch = independent_anchor_status.get("sharp_odds_fetch") if isinstance(independent_anchor_status.get("sharp_odds_fetch"), dict) else {}
    sharp_anchor = independent_anchor_status.get("sharp_anchor") if isinstance(independent_anchor_status.get("sharp_anchor"), dict) else {}
    fundamental_rows = [
        row
        for row in predictions
        if str(row.get("fundamental_probability") or "").strip()
        or str(row.get("haircut_fundamental_probability") or "").strip()
    ]
    trade_candidates = [row for row in fundamental_rows if _truthy(row.get("alpha_trade_candidate"))]
    shadow_candidates = [row for row in fundamental_rows if _truthy(row.get("shadow_trade_candidate"))]
    near_miss_candidates = [row for row in fundamental_rows if _truthy(row.get("near_miss_learning_candidate"))]
    blocker_counter: Counter[str] = Counter()
    for row in fundamental_rows:
        if _truthy(row.get("alpha_trade_candidate")):
            continue
        reason = str(row.get("validation_layer_reason") or row.get("shadow_candidate_reason") or "").strip()
        if reason:
            for part in reason.split(";"):
                cleaned = part.strip()
                if cleaned:
                    blocker_counter[cleaned] += 1
    rejected_token_ids = {
        str(row.get("token_id") or ""): row
        for row in fundamental_rows
        if str(row.get("token_id") or "")
    }
    for row in rejected:
        token_id = str(row.get("token_id") or "")
        if token_id and token_id in rejected_token_ids:
            reason = str(row.get("rejection_reason") or "").strip()
            if reason:
                blocker_counter[reason] += 1

    loaded = int(safe_float(alpha_summary.get("fundamental_probabilities_loaded")) or 0)
    hits = int(safe_float(alpha_summary.get("fundamental_probability_hits")) or len(fundamental_rows))
    scored = int(safe_float(alpha_summary.get("scored")) or 0)
    summary_trade_candidates = int(safe_float(alpha_summary.get("trade_candidates")) or len(trade_candidates))
    summary_shadow_candidates = int(safe_float(alpha_summary.get("shadow_trade_candidates")) or len(shadow_candidates))
    if summary_trade_candidates > 0:
        status = "trade_candidates_waiting_for_governed_risk_path"
        blocker_or_next = "Run only through paper readiness, cohort, risk, spread, and liquidity gates; do not bypass governance."
    elif summary_shadow_candidates > 0 or shadow_candidates:
        status = "shadow_candidates_collecting_forward_evidence"
        blocker_or_next = "Collect forward paper/CLV evidence for the sharp-backed shadow candidates before promotion."
    elif fundamental_rows:
        status = "fundamental_rows_scored_but_blocked"
        blocker_or_next = blocker_counter.most_common(1)[0][0] if blocker_counter else "No alpha trade candidate after haircut, penalties, and microstructure filters."
    elif loaded > 0:
        status = "no_current_prediction_overlap"
        blocker_or_next = "Sharp anchor rows exist, but the current prediction universe has no matching token rows; broaden/refresh market discovery."
    elif str(sharp_anchor.get("status") or "") in {"built", "ok"}:
        status = "anchor_built_without_scored_hits"
        blocker_or_next = "Sharp anchor built, but no fundamental probabilities reached current scoring."
    else:
        status = "missing_or_unusable_sharp_anchor"
        blocker_or_next = independent_anchor_status.get("main_blocker") or "Fetch/build sharp-anchor probabilities before expecting anchor-led signals."

    top_rows = sorted(
        fundamental_rows,
        key=lambda row: (
            safe_float(row.get("edge_lower_bound")) or -999.0,
            safe_float(row.get("fundamental_edge_after_haircut")) or -999.0,
            safe_float(row.get("alpha_raw_edge")) or -999.0,
        ),
        reverse=True,
    )
    return {
        "status": status,
        "generated_at_utc": alpha_summary.get("generated_at_utc"),
        "decision_use": "Measures whether sharp bookmaker probabilities are transferring into current executable Polymarket candidates.",
        "key_metric": (
            f"{hits}/{loaded} current fundamental hits; "
            f"{summary_trade_candidates} trade candidates; {summary_shadow_candidates} shadow candidates"
        ),
        "blocker_or_next": blocker_or_next,
        "sharp_fetch_status": sharp_fetch.get("status"),
        "sharp_fetch_rows": sharp_fetch.get("rows"),
        "sharp_anchor_status": sharp_anchor.get("status"),
        "sharp_anchor_rows": sharp_anchor.get("fundamental_rows"),
        "alpha_predictions": alpha_summary.get("predictions"),
        "alpha_scored": scored,
        "fundamental_probabilities_loaded": loaded,
        "fundamental_probability_hits": hits,
        "fundamental_rows_in_predictions": len(fundamental_rows),
        "alpha_trade_candidates": summary_trade_candidates,
        "shadow_trade_candidates": summary_shadow_candidates,
        "near_miss_candidates": len(near_miss_candidates),
        "bookmaker_cross_check_failures": alpha_summary.get("bookmaker_cross_check_failures", 0),
        "microstructure_filter_failures": alpha_summary.get("microstructure_filter_failures", 0),
        "top_blockers": [{"reason": reason, "count": count} for reason, count in blocker_counter.most_common(6)],
        "top_fundamental_rows": top_rows[:8],
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }


_SHARP_SPORT_FAMILIES: tuple[dict[str, Any], ...] = (
    {
        "family": "worldcup_soccer",
        "query": "world cup",
        "family_prefixes": ("worldcup", "world cup", "fifa", "soccer"),
        "anchor_sports": ("soccer_fifa_world_cup",),
    },
    {
        "family": "tennis_match",
        "query": "tennis",
        "family_prefixes": ("tennis", "tennis_match", "tennis_tennis"),
        "anchor_sports": ("tennis_atp", "tennis_wta"),
    },
    {
        "family": "basketball_nba_match",
        "query": "nba",
        "family_prefixes": ("basketball_nba", "basketball"),
        "anchor_sports": ("basketball_nba",),
    },
    {
        "family": "baseball_mlb_match",
        "query": "mlb",
        "family_prefixes": ("baseball_mlb", "baseball"),
        "anchor_sports": ("baseball_mlb",),
    },
    {
        "family": "mma_match",
        "query": "mma",
        "family_prefixes": ("mma",),
        "anchor_sports": ("mma_mixed_martial_arts",),
    },
)


def _query_active_for_family(research_focus: dict[str, Any], query: str) -> bool:
    key = str(query or "").strip().lower()
    if not key:
        return False
    guard = research_focus.get("collection_query_guard")
    guard = guard if isinstance(guard, dict) else {}
    query_lists = (
        research_focus.get("collection_queries"),
        research_focus.get("raw_collection_queries"),
        guard.get("guarded_collection_queries"),
        guard.get("broad_fill_queries"),
        guard.get("broad_base_queries"),
    )
    for values in query_lists:
        if not isinstance(values, list):
            continue
        if any(str(item or "").strip().lower() == key for item in values):
            return True
    return False


def _row_matches_prefix(row: dict[str, Any], prefixes: tuple[str, ...]) -> bool:
    text = " ".join(
        str(row.get(key) or "").strip().lower()
        for key in ("family", "category", "signal_cohort", "market_slug", "question", "sport")
    )
    return any(
        part == prefix or part.startswith(prefix + "_") or f"|{prefix}" in part or prefix in text
        for prefix in prefixes
        for part in text.replace("|", " ").split()
    )


def _sport_anchor_rows(coverage_rows: list[dict[str, Any]], anchor_sports: tuple[str, ...]) -> dict[str, int]:
    totals = {
        "anchor_rows": 0,
        "anchor_rows_in": 0,
        "anchor_priced_rows": 0,
        "anchor_no_token_rows": 0,
        "anchor_current_joined_rows": 0,
        "anchor_actionable_rows": 0,
        "anchor_ambiguous_rows": 0,
        "anchor_stale_rows": 0,
        "polymarket_eligible_rows": 0,
    }
    sports = {sport.lower() for sport in anchor_sports}
    for row in coverage_rows:
        sport = str(row.get("sport") or "").strip().lower()
        if sport not in sports:
            continue
        totals["anchor_rows"] += int(safe_float(row.get("rows_mapped") or row.get("fundamental_rows")) or 0)
        totals["anchor_rows_in"] += int(safe_float(row.get("source_rows_normalized") or row.get("rows_in")) or 0)
        totals["anchor_priced_rows"] += int(safe_float(row.get("priced_rows")) or 0)
        totals["anchor_no_token_rows"] += int(safe_float(row.get("skipped_no_token")) or 0)
        totals["anchor_current_joined_rows"] += int(safe_float(row.get("rows_joined")) or 0)
        totals["anchor_actionable_rows"] += int(safe_float(row.get("actionable_rows")) or 0)
        totals["anchor_ambiguous_rows"] += int(safe_float(row.get("ambiguous_rows")) or 0)
        totals["anchor_stale_rows"] += int(safe_float(row.get("stale_rows")) or 0)
        totals["polymarket_eligible_rows"] += int(safe_float(row.get("polymarket_rows_eligible")) or 0)
    return totals


def _sharp_sports_funnel_payload(
    *,
    research_focus: dict[str, Any],
    liquidity_discovery: dict[str, Any],
    independent_anchor_status: dict[str, Any],
    alpha_bridge: dict[str, Any],
    predictions: list[dict[str, Any]],
    closing_line_value: dict[str, Any],
) -> dict[str, Any]:
    """Summarise the highest-prior sports edge route from collection to proof.

    This is reporting-only. It helps the operator see whether sharp bookmaker
    anchors are blocked at collection, liquidity, mapping, scoring, or CLV
    evidence. It never authorises paper or live trading.
    """

    research_focus = research_focus if isinstance(research_focus, dict) else {}
    liquidity_discovery = liquidity_discovery if isinstance(liquidity_discovery, dict) else {}
    independent_anchor_status = independent_anchor_status if isinstance(independent_anchor_status, dict) else {}
    closing_line_value = closing_line_value if isinstance(closing_line_value, dict) else {}
    sharp_anchor = independent_anchor_status.get("sharp_anchor")
    sharp_anchor = sharp_anchor if isinstance(sharp_anchor, dict) else {}
    sharp_coverage = independent_anchor_status.get("sharp_anchor_coverage")
    sharp_coverage = sharp_coverage if isinstance(sharp_coverage, dict) else {}
    coverage_rows = sharp_coverage.get("source_sport_markets") or sharp_anchor.get("coverage_by_sport_market")
    coverage_rows = coverage_rows if isinstance(coverage_rows, list) else []
    liquidity_rows = liquidity_discovery.get("family_summary")
    liquidity_rows = liquidity_rows if isinstance(liquidity_rows, list) else []
    clv_rows = closing_line_value.get("cohorts")
    clv_rows = clv_rows if isinstance(clv_rows, list) else []

    family_rows: list[dict[str, Any]] = []
    for item in _SHARP_SPORT_FAMILIES:
        family = str(item["family"])
        query = str(item["query"])
        prefixes = tuple(str(value) for value in item["family_prefixes"])
        anchor_sports = tuple(str(value) for value in item["anchor_sports"])
        query_active = _query_active_for_family(research_focus, query)
        matched_liquidity = [row for row in liquidity_rows if isinstance(row, dict) and _row_matches_prefix(row, prefixes)]
        liquidity_tokens = sum(int(safe_float(row.get("tokens")) or 0) for row in matched_liquidity)
        tradable_rows = sum(int(safe_float(row.get("tradable_tokens")) or 0) for row in matched_liquidity)
        anchor_totals = _sport_anchor_rows([row for row in coverage_rows if isinstance(row, dict)], anchor_sports)
        matched_predictions = [
            row
            for row in predictions
            if _row_matches_prefix(row, prefixes)
            and (
                str(row.get("fundamental_probability") or "").strip()
                or str(row.get("haircut_fundamental_probability") or "").strip()
            )
        ]
        trade_candidates = sum(1 for row in matched_predictions if _truthy(row.get("alpha_trade_candidate")))
        shadow_candidates = sum(1 for row in matched_predictions if _truthy(row.get("shadow_trade_candidate")))
        matched_clv = [
            row for row in clv_rows if isinstance(row, dict) and _row_matches_prefix(row, prefixes)
        ]
        final_clv_rows = sum(int(safe_float(row.get("final_positions")) or 0) for row in matched_clv)
        positive_clv_rows = sum(
            1
            for row in matched_clv
            if str(row.get("clv_evidence") or "").lower().startswith("positive")
            or (safe_float(row.get("mean_final_clv")) or 0.0) > 0
        )
        if not query_active:
            state = "needs_collection_query"
            next_action = f"Keep {query!r} in the guarded collection queue until liquid rows appear."
        elif tradable_rows <= 0:
            state = "needs_liquid_polymarket_rows"
            next_action = "Refresh liquidity discovery/websocket targets for this family."
        elif anchor_totals["anchor_rows"] <= 0:
            state = "needs_sharp_bookmaker_anchor"
            next_action = "Fetch/build no-vig bookmaker anchors and map them to current Polymarket tokens."
        elif not matched_predictions:
            state = "needs_scored_anchor_overlap"
            next_action = "Join the sharp anchor rows to the current prediction universe."
        elif trade_candidates <= 0 and shadow_candidates <= 0:
            state = "blocked_by_alpha_filters"
            next_action = alpha_bridge.get("blocker_or_next") or "Inspect alpha haircut, spread, and liquidity blockers."
        elif final_clv_rows <= 0:
            state = "needs_forward_clv_evidence"
            next_action = "Collect bid/ask lines after the sharp-backed shadow candidates to measure CLV."
        elif positive_clv_rows <= 0:
            state = "needs_positive_clv"
            next_action = "Keep the family shadow-only until final-line CLV becomes positive."
        else:
            state = "positive_clv_watch"
            next_action = "Review paper gates only after cohort-level forward evidence remains positive."
        family_rows.append(
            {
                "family": family,
                "query": query,
                "query_active": query_active,
                "liquidity_tokens": liquidity_tokens,
                "tradable_liquidity_rows": tradable_rows,
                **anchor_totals,
                "scored_anchor_hits": len(matched_predictions),
                "trade_candidates": trade_candidates,
                "shadow_candidates": shadow_candidates,
                "final_clv_rows": final_clv_rows,
                "positive_clv_rows": positive_clv_rows,
                "state": state,
                "next_action": next_action,
                "decision_use": "reporting_only_not_trade_authorisation",
            }
        )

    active = [row["family"] for row in family_rows if row["query_active"]]
    next_bottleneck = next((row["next_action"] for row in family_rows if row["state"] != "positive_clv_watch"), "")
    total_shadow = sum(int(row["shadow_candidates"]) for row in family_rows)
    total_trade = sum(int(row["trade_candidates"]) for row in family_rows)
    total_clv = sum(int(row["final_clv_rows"]) for row in family_rows)
    if total_trade > 0:
        status = "governed_trade_candidates_exist"
    elif total_shadow > 0:
        status = "sharp_shadow_candidates_need_forward_evidence"
    elif total_clv > 0:
        status = "clv_evidence_collected"
    elif any(row["anchor_rows"] > 0 for row in family_rows):
        status = "anchors_need_current_overlap"
    elif any(row["tradable_liquidity_rows"] > 0 for row in family_rows):
        status = "liquidity_waiting_for_sharp_anchors"
    elif active:
        status = "collecting_sharp_sports_liquidity"
    else:
        status = "sharp_sports_collection_not_active"

    return {
        "status": status,
        "generated_at_utc": now_utc(),
        "decision_use": "Shows the sharp sports edge route bottleneck; reporting only, no paper/live authorisation.",
        "families": family_rows,
        "active_collection_families": active,
        "next_bottleneck": next_bottleneck or "No active bottleneck reported.",
        "total_anchor_rows": sum(int(row["anchor_rows"]) for row in family_rows),
        "total_tradable_liquidity_rows": sum(int(row["tradable_liquidity_rows"]) for row in family_rows),
        "total_scored_anchor_hits": sum(int(row["scored_anchor_hits"]) for row in family_rows),
        "total_trade_candidates": total_trade,
        "total_shadow_candidates": total_shadow,
        "total_final_clv_rows": total_clv,
        "anchor_accounting": sharp_coverage.get("anchor_funnel", {}),
        "anchor_source_sport_markets": sharp_coverage.get("source_sport_markets", []),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }


def _dashboard_entry_price_band(cfg: EngineConfig) -> tuple[float, float]:
    risk = cfg.raw.get("risk") if isinstance(cfg.raw.get("risk"), dict) else {}
    minimum_entry_price = safe_float(risk.get("minimum_entry_price"))
    maximum_entry_price = safe_float(risk.get("maximum_entry_price"))
    return (
        float(minimum_entry_price) if minimum_entry_price is not None else 0.05,
        float(maximum_entry_price) if maximum_entry_price is not None else 0.90,
    )


def _dashboard_time_to_close_hours(row: dict[str, Any]) -> float | None:
    for key in ("close_time", "end_time", "endDate", "endDateIso"):
        parsed = parse_timestamp(row.get(key))
        if parsed is not None:
            return (parsed.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds() / 3600.0
    for key in ("time_to_close_hours", "hours_to_close"):
        value = safe_float(row.get(key))
        if value is not None:
            return value
    return None


def _alpha_learning_openable_dashboard_candidate(
    row: dict[str, Any],
    minimum_entry_price: float,
    maximum_entry_price: float,
) -> bool:
    time_to_close = _dashboard_time_to_close_hours(row)
    if time_to_close is not None and time_to_close < 0:
        return False
    executable_price = safe_float(row.get("executable_price"))
    if executable_price is None or not minimum_entry_price <= executable_price <= maximum_entry_price:
        return False
    if safe_float(row.get("best_ask")) is None and safe_float(row.get("executable_price")) is None:
        return False
    return True


def _trade_diagnostics(
    cfg: EngineConfig,
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
    alpha_learning_candidates = [
        row
        for row in predictions
        if _truthy(row.get("alpha_trade_candidate"))
        and _truthy(row.get("validation_layer_pass"))
        and _truthy(row.get("microstructure_filter_pass"))
        and _truthy(row.get("bookmaker_cross_check_pass", True))
    ]
    alpha_learning_candidates.sort(
        key=lambda row: (
            safe_float(row.get("edge_lower_bound")) or 0.0,
            safe_float(row.get("alpha_score")) or 0.0,
        ),
        reverse=True,
    )
    minimum_entry_price, maximum_entry_price = _dashboard_entry_price_band(cfg)
    openable_alpha_learning_candidates = [
        row
        for row in alpha_learning_candidates
        if _alpha_learning_openable_dashboard_candidate(row, minimum_entry_price, maximum_entry_price)
    ]
    openable_alpha_learning_candidates.sort(
        key=lambda row: (
            safe_float(row.get("edge_lower_bound")) or 0.0,
            safe_float(row.get("alpha_score")) or 0.0,
        ),
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
        "alpha_candidate_learning_candidates_seen": shadow_summary.get(
            "alpha_candidate_learning_candidates_seen",
            len(alpha_learning_candidates),
        ),
        "alpha_candidate_learning_openable_candidates_seen": len(openable_alpha_learning_candidates),
        "alpha_candidate_learning_opened_this_cycle": shadow_summary.get("alpha_candidate_learning_opened_this_cycle"),
        "alpha_candidate_learning_open_positions": shadow_summary.get("alpha_candidate_learning_open_positions"),
        "alpha_candidate_learning_entry_price_band": {
            "minimum_entry_price": minimum_entry_price,
            "maximum_entry_price": maximum_entry_price,
        },
        "shadow_candidates_seen": shadow_summary.get("shadow_candidates_seen"),
        "shadow_opened_this_cycle": shadow_summary.get("opened_this_cycle"),
        "shadow_open_positions": shadow_summary.get("open_positions"),
        "quarantined_cohort_count": len(quarantined),
        "top_rejection_reasons": top_reasons,
        "current_near_miss_candidates": near_miss_candidates[:12],
        "current_alpha_learning_candidates": openable_alpha_learning_candidates[:12],
        "current_shadow_candidates": shadow_candidates[:12],
        "quarantined_cohorts": quarantined[:12],
    }


def _dash(value: Any) -> Any:
    if value is None or value == "" or value == [] or value == {}:
        return "-"
    return value


def _first_present_number(payload: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in {None, ""}:
            return value
    return "-"


def _count_by_field(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts = Counter(str(row.get(field) or "unknown") for row in rows if isinstance(row, dict))
    return dict(sorted(counts.items()))


def _evidence_funnel_payload(
    *,
    liquidity_discovery: dict[str, Any],
    predictions: list[dict[str, Any]],
    shadow_position_rows: list[dict[str, Any]],
    closing_line_value: dict[str, Any],
    edge_attribution: dict[str, Any],
    family_calibration: dict[str, Any],
    collection_coverage: dict[str, Any],
    algo_sweep: dict[str, Any],
    promotion_gate: dict[str, Any],
    evidence_history_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    alpha_shadow_candidates = sum(1 for row in predictions if _truthy(row.get("shadow_trade_candidate")))
    open_shadow = sum(1 for row in shadow_position_rows if str(row.get("status") or "open").lower() == "open")
    closed_shadow = sum(1 for row in shadow_position_rows if str(row.get("status") or "").lower() == "closed")
    attribution_cohorts = edge_attribution.get("cohorts", []) if isinstance(edge_attribution, dict) else []
    attribution_cohorts = attribution_cohorts if isinstance(attribution_cohorts, list) else []
    positive_clv = closing_line_value.get("positive_clv_cohorts", []) if isinstance(closing_line_value, dict) else []
    positive_clv = positive_clv if isinstance(positive_clv, list) else []
    calibration_families = family_calibration.get("model_beats_market_families", []) if isinstance(family_calibration, dict) else []
    calibration_families = calibration_families if isinstance(calibration_families, list) else []
    missing_pre_close = (
        collection_coverage.get("missing_pre_close_positions", []) if isinstance(collection_coverage, dict) else []
    )
    missing_pre_close = missing_pre_close if isinstance(missing_pre_close, list) else []
    missing_count = collection_coverage.get("positions_missing_pre_close_quote") if isinstance(collection_coverage, dict) else None
    covered_count = collection_coverage.get("positions_with_pre_close_quote") if isinstance(collection_coverage, dict) else None
    if missing_count not in {None, ""} and covered_count not in {None, ""}:
        collection_gap: Any = f"{missing_count} missing / {covered_count} covered"
    else:
        collection_gap = "-"
    if isinstance(promotion_gate, dict) and promotion_gate:
        approved = bool(promotion_gate.get("approved_for_paper_trading"))
        gate_status = "approved_for_paper" if approved else "blocked_for_paper"
        blockers = promotion_gate.get("paper_blockers", [])
        if blockers:
            gate_status += ": " + "; ".join(str(item) for item in blockers[:3])
    else:
        gate_status = "-"

    return {
        "liquidity_targets_discovered": _first_present_number(
            liquidity_discovery,
            [
                "target_assets",
                "targets_discovered",
                "discovered_targets",
                "candidate_assets",
                "tokens",
                "markets",
                "rows",
            ],
        ),
        "alpha_shadow_candidates": alpha_shadow_candidates if predictions else "-",
        "shadow_positions": f"{open_shadow} open / {closed_shadow} closed" if shadow_position_rows else "-",
        "closed_with_clv_final_lines": _dash(closing_line_value.get("final_line_positions") if isinstance(closing_line_value, dict) else None),
        "attributed_positions": _dash(edge_attribution.get("attributed_positions") if isinstance(edge_attribution, dict) else None),
        "cohorts_by_attribution_class": _dash(_count_by_field(attribution_cohorts, "attribution_class")),
        "positive_clv_cohorts": _dash(positive_clv),
        "model_beats_market_families": _dash(calibration_families),
        "collection_gap": collection_gap,
        "missing_pre_close_positions": missing_pre_close[:25],
        "sweep_decision": _dash(algo_sweep.get("decision") if isinstance(algo_sweep, dict) else None),
        "paper_gate_status": gate_status,
        "evidence_history_rows": len(evidence_history_rows) if evidence_history_rows else "-",
        "recent_history": list(reversed(evidence_history_rows[-10:])),
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


def _sharp_fetch_health(independent_anchor_status: dict[str, Any]) -> dict[str, Any]:
    """Compact, top-line health badge for the sharp-odds fetch.

    During an unattended run the biggest silent failure is a dead Odds API key or an
    exhausted request budget: the fetch keeps 'succeeding' with zero rows and no anchor
    ever forms. This promotes the key liveness signals to one glanceable object so that
    failure mode surfaces on day one instead of at the end of the evidence window.
    """
    status_map = independent_anchor_status if isinstance(independent_anchor_status, dict) else {}
    fetch = status_map.get("sharp_odds_fetch") if isinstance(status_map.get("sharp_odds_fetch"), dict) else {}
    status = str(fetch.get("status") or "unknown")
    provider_status = str(fetch.get("provider_status") or "unknown")
    rows = int(safe_float(fetch.get("rows")) or 0)
    remaining = fetch.get("requests_remaining")
    remaining_num = safe_float(remaining)
    # Healthy = the provider was actually reached and either produced rows or is between
    # scheduled fetches with a live key; unhealthy = missing key, hard error, or quota gone.
    if status in {"missing_api_key", "error"} or provider_status in {"missing_api_key"}:
        health = "attention"
    elif remaining_num is not None and remaining_num <= 0:
        health = "attention"
    elif status in {"fetched", "partial", "fallback_loaded", "skipped_budget"} or provider_status == "attempted":
        # skipped_budget is the healthy between-fetches state now that skip summaries
        # chain and carry the last real quota reading forward.
        health = "ok"
    else:
        health = "unknown"
    return {
        "health": health,
        "status": status,
        "provider_status": provider_status,
        "rows": rows,
        "markets": int(safe_float(fetch.get("markets")) or 0),
        "requests_used": fetch.get("requests_used"),
        "requests_remaining": remaining if remaining not in (None, "") else "unknown",
        "generated_at_utc": fetch.get("generated_at_utc"),
        "note": "Dead key / exhausted Odds API quota shows as health=attention. Diagnostic only; no gate changed.",
    }


def _independent_anchor_status(governance: Path) -> dict[str, Any]:
    """Expose the latest independent-anchor summaries even when the heartbeat is settlement-only."""
    sharp_fetch = read_json(governance / "sharp_odds_fetch_summary.json", default={}) or {}
    sharp_anchor = read_json(governance / "sharp_anchor_summary.json", default={}) or {}
    sharp_anchor_coverage = read_json(governance / "sharp_anchor_coverage.json", default={}) or {}
    crypto_targets = read_json(governance / "crypto_targets_summary.json", default={}) or {}
    crypto = read_json(governance / "crypto_fundamental_summary.json", default={}) or {}
    sharp_anchor_payload = sharp_anchor if isinstance(sharp_anchor, dict) else {}
    sharp_anchor_payload.setdefault("coverage_by_sport_market", [])
    components = {
        "sharp_odds_fetch": sharp_fetch if isinstance(sharp_fetch, dict) else {},
        "sharp_anchor": sharp_anchor_payload,
        "sharp_anchor_coverage": sharp_anchor_coverage if isinstance(sharp_anchor_coverage, dict) else {},
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
        elif rows <= 0 and status and name != "sharp_anchor_coverage":
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


def _local_process_exists(pid_value: Any) -> bool | None:
    try:
        pid = int(float(str(pid_value)))
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    if sys.platform.startswith("win"):
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if handle:
                kernel32.CloseHandle(handle)
                return True
            error = ctypes.get_last_error()
            if error == 5:  # ERROR_ACCESS_DENIED: process exists but cannot be queried.
                return True
            if error in {0, 87}:  # ERROR_INVALID_PARAMETER usually means the PID is gone.
                return False
        except Exception:
            pass
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except Exception:
            return None
        if result.returncode != 0:
            return None
        return f'"{pid}"' in result.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return None
    return True


def _strategy_v2_status(cfg: EngineConfig, legacy_full_cycle: dict[str, Any] | None = None) -> dict[str, Any]:
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
    cycle_paper_trade = cycle_status.get("paper_trade_refresh") if isinstance(cycle_status.get("paper_trade_refresh"), dict) else {}
    cycle_broker = cycle_paper_trade.get("broker") if isinstance(cycle_paper_trade.get("broker"), dict) else {}

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
    legacy_live_driver = (
        isinstance(legacy_full_cycle, dict)
        and str(legacy_full_cycle.get("effective_status") or "").lower() == "live"
    )
    missing_strategy_artifacts = not report and not candidates and cycle_state in {"", "missing"}
    runtime_posture = "collecting_shadow_evidence"
    runtime_reason = "Strategy V2 cycle is ready to collect shadow evidence."
    if missing_strategy_artifacts and legacy_live_driver:
        runtime_posture = "not_running_in_this_deployment"
        runtime_reason = "Strategy V2 is not running in this deployment."
    elif cycle_state == "skipped_high_memory":
        runtime_posture = "memory_paused"
        runtime_reason = str(cycle_status.get("reason") or "Strategy V2 paused by the local memory guard.")
    elif cycle_state in {"error", "failed"}:
        runtime_posture = "cycle_error"
        runtime_reason = str(cycle_status.get("reason") or cycle_status.get("error") or "Strategy V2 cycle reported an error.")
    elif cycle_state in {"", "missing"}:
        runtime_posture = "not_started"
        runtime_reason = "Strategy V2 cycle status is missing."
    status = report.get("status") or ("missing" if not candidates else "ok")
    decision = report.get("decision") or "missing_report"
    recommended_action = report.get("recommended_action") or "Run Strategy V2 anchored-edge scanner."
    if missing_strategy_artifacts and legacy_live_driver:
        status = "not running in this deployment"
        decision = "not running in this deployment"
        recommended_action = "No Strategy V2 action needed while the VPS deployment is driven by the legacy live loop."

    return {
        "status": status,
        "generated_at_utc": report.get("generated_at_utc"),
        "decision": decision,
        "recommended_action": recommended_action,
        "cycle_status": cycle_status,
        "paper_trade_refresh": cycle_paper_trade,
        "paper_broker_status": cycle_broker.get("status"),
        "paper_broker_buy_fills": cycle_broker.get("buy_orders_filled", cycle_broker.get("orders_filled")),
        "paper_broker_exit_fills": cycle_broker.get("exit_orders_filled"),
        "paper_broker_rejections": cycle_broker.get("orders_rejected"),
        "runtime_posture": runtime_posture,
        "runtime_reason": runtime_reason,
        "memory_used_percent": cycle_status.get("memory_used_percent"),
        "max_memory_percent": cycle_status.get("max_memory_percent"),
        "rows_scored": report.get("rows_scored") if report.get("rows_scored") is not None else len(candidates),
        "anchor_rows_loaded": report.get("anchor_rows_loaded"),
        "alpha_validated_anchor_rows": report.get("alpha_validated_anchor_rows", report.get("worldcup_validated_anchor_rows")),
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


def _price_action_scout_status(cfg: EngineConfig) -> dict[str, Any]:
    """Expose fast shadow-only price-action scout evidence."""
    root = cfg.output_root / "polymarket_price_action"
    summary = read_json(root / "price_action_scout_summary.json", default={}) or {}
    if not isinstance(summary, dict):
        summary = {}
    cohorts = read_csv_rows(root / "price_action_scout_cohort_evidence.csv")
    candidates = read_csv_rows(root / "price_action_scout_round_trip_evidence.csv")
    entries = read_csv_rows(root / "price_action_scout_entries.csv")
    return {
        "status": summary.get("status") or ("missing" if not entries and not candidates else "computed"),
        "generated_at_utc": summary.get("generated_at_utc"),
        "decision": summary.get("decision") or "collect_more_price_action_scout_evidence",
        "new_entries": summary.get("new_entries"),
        "ledger_entries": summary.get("ledger_entries", len(entries)),
        "policy_eligible_entries": summary.get("policy_eligible_entries"),
        "policy_excluded_entries": summary.get("policy_excluded_entries"),
        "deduped_evidence_entries": summary.get("deduped_evidence_entries"),
        "duplicate_policy_eligible_entries": summary.get("duplicate_policy_eligible_entries"),
        "round_trip_candidates": summary.get("round_trip_candidates", len(candidates)),
        "watchlist_rows": summary.get("watchlist_rows"),
        "profit_sprint_target_rows": summary.get("profit_sprint_target_rows"),
        "paper_confirmation_blocker_targets": summary.get("paper_confirmation_blocker_targets"),
        "current_positive_analogue_targets": summary.get("current_positive_analogue_targets"),
        "label_headroom_research_targets": summary.get("label_headroom_research_targets"),
        "observed_candidates": summary.get("observed_candidates"),
        "closed_trades": summary.get("closed_trades"),
        "open_trades": summary.get("open_trades"),
        "take_profit_exits": summary.get("take_profit_exits"),
        "stop_loss_exits": summary.get("stop_loss_exits"),
        "price_action_review_candidates": summary.get("price_action_review_candidates"),
        "realized_pnl_usdc": summary.get("realized_pnl_usdc"),
        "realized_roi": summary.get("realized_roi"),
        "total_mark_pnl_usdc": summary.get("total_mark_pnl_usdc"),
        "mark_roi": summary.get("mark_roi"),
        "warnings": summary.get("warnings", {}),
        "cohorts": cohorts[:25],
        "top_candidates": _sorted_by_numeric(candidates, "mark_pnl_usdc")[:25],
        "entry_file": str(root / "price_action_scout_entries.csv"),
        "candidate_file": str(root / "price_action_scout_round_trip_evidence.csv"),
        "shadow_only": True,
    }


def _price_action_microstructure_status(cfg: EngineConfig) -> dict[str, Any]:
    """Expose the websocket microstructure rule lab."""
    root = cfg.output_root / "polymarket_price_action"
    summary = read_json(root / "microstructure_summary.json", default={}) or {}
    if not isinstance(summary, dict):
        summary = {}
    rules = read_csv_rows(root / "microstructure_rule_evidence.csv")
    family_rules = read_csv_rows(root / "microstructure_family_rule_evidence.csv")
    exit_policy_rules = read_csv_rows(root / "microstructure_exit_policy_evidence.csv")
    current = read_csv_rows(root / "microstructure_current_candidates.csv")
    events = read_csv_rows(root / "microstructure_trade_events.csv", limit=1)
    return {
        "status": summary.get("status") or ("missing" if not rules and not current and not events else "computed"),
        "generated_at_utc": summary.get("generated_at_utc"),
        "decision": summary.get("decision") or "collect_more_websocket_microstructure_evidence",
        "source_rows": summary.get("source_rows"),
        "tokens": summary.get("tokens"),
        "trade_events": summary.get("trade_events"),
        "rule_rows": summary.get("rule_rows", len(rules)),
        "validation_pass_rules": summary.get("validation_pass_rules"),
        "family_rule_rows": summary.get("family_rule_rows", len(family_rules)),
        "family_validation_pass_rules": summary.get("family_validation_pass_rules"),
        "exit_policy_rows": summary.get("exit_policy_rows", len(exit_policy_rules)),
        "exit_policy_validation_pass_rules": summary.get("exit_policy_validation_pass_rules"),
        "current_candidates": summary.get("current_candidates", len(current)),
        "top_rule": summary.get("top_rule", rules[0] if rules else {}),
        "top_family_rule": summary.get("top_family_rule", family_rules[0] if family_rules else {}),
        "top_exit_policy_rule": summary.get("top_exit_policy_rule", exit_policy_rules[0] if exit_policy_rules else {}),
        "top_rules": summary.get("top_rules", rules[:15]),
        "top_family_rules": summary.get("top_family_rules", family_rules[:15]),
        "top_exit_policy_rules": summary.get("top_exit_policy_rules", exit_policy_rules[:15]),
        "current_candidates_preview": summary.get("current_candidates_preview", current[:15]),
        "trade_events_file": str(root / "microstructure_trade_events.csv"),
        "rule_evidence_file": str(root / "microstructure_rule_evidence.csv"),
        "family_rule_evidence_file": str(root / "microstructure_family_rule_evidence.csv"),
        "exit_policy_evidence_file": str(root / "microstructure_exit_policy_evidence.csv"),
        "current_candidates_file": str(root / "microstructure_current_candidates.csv"),
        "shadow_only": True,
    }


def _price_action_model_status(cfg: EngineConfig) -> dict[str, Any]:
    """Expose the strict future-bid repricing prediction model."""
    root = cfg.output_root / "polymarket_price_action"
    summary = read_json(root / "price_action_model_summary.json", default={}) or {}
    if not isinstance(summary, dict):
        summary = {}
    current = read_csv_rows(root / "price_action_model_current_candidates.csv")
    validation = read_csv_rows(root / "price_action_model_validation_predictions.csv", limit=25)
    return {
        "status": summary.get("status") or ("missing" if not current and not validation else "trained"),
        "generated_at_utc": summary.get("generated_at_utc"),
        "decision": summary.get("decision") or "train_price_action_model_from_bid_ask_repricing_events",
        "promotion_ready": summary.get("promotion_ready", False),
        "trading_objective": summary.get("trading_objective"),
        "label_definition": summary.get("label_definition"),
        "training_events": summary.get("training_events"),
        "train_rows": summary.get("train_rows"),
        "validation_rows": summary.get("validation_rows"),
        "train_positive_targets": summary.get("train_positive_targets"),
        "validation_positive_targets": summary.get("validation_positive_targets"),
        "validation_gap": summary.get("validation_gap", {}),
        "cohort_transfer": summary.get("cohort_transfer", {}),
        "training_event_sources": summary.get("training_event_sources", {}),
        "validation_positive_rate": summary.get("validation_positive_rate"),
        "validation_brier": summary.get("validation_brier"),
        "validation_log_loss": summary.get("validation_log_loss"),
        "chosen_probability_threshold": summary.get("chosen_probability_threshold"),
        "validation_probability_threshold": summary.get("validation_probability_threshold"),
        "validation_threshold_policy": summary.get("validation_threshold_policy", {}),
        "current_probability_threshold": summary.get("current_probability_threshold"),
        "current_threshold_policy": summary.get("current_threshold_policy", {}),
        "train_threshold_selection": summary.get("train_threshold_selection", {}),
        "validation_selected": summary.get("validation_selected", {}),
        "validation_rank_diagnostics": summary.get("validation_rank_diagnostics", {}),
        "validation_selected_risk": summary.get("validation_selected_risk", {}),
        "validation_win_rate_gate": summary.get("validation_win_rate_gate"),
        "validation_win_rate_gate_reason": summary.get("validation_win_rate_gate_reason"),
        "quant_promotion_decision": summary.get("quant_promotion_decision", {}),
        "maximum_selected_validation_drawdown": summary.get("maximum_selected_validation_drawdown"),
        "validation_blockers": summary.get("validation_blockers", []),
        "blockers": summary.get("blockers", []),
        "validation_buy_all_baseline": summary.get("validation_buy_all_baseline", {}),
        "validation_selected_roi_ci95": summary.get("validation_selected_roi_ci95", []),
        "validation_edge_over_buy_all_roi": summary.get("validation_edge_over_buy_all_roi"),
        "roi_expectancy_from_train": summary.get("roi_expectancy_from_train", {}),
        "minimum_expected_roi_to_trade": summary.get("minimum_expected_roi_to_trade"),
        "current_rows_scored": summary.get("current_rows_scored"),
        "current_model_candidates": summary.get("current_model_candidates", len(current)),
        "current_candidates_preview": summary.get("current_candidates_preview", current[:15]),
        "validation_preview": validation,
        "validation_predictions_file": str(root / "price_action_model_validation_predictions.csv"),
        "current_candidates_file": str(root / "price_action_model_current_candidates.csv"),
        "shadow_only": True,
    }


def _price_action_paper_signal_status(cfg: EngineConfig) -> dict[str, Any]:
    """Expose the settlement-independent price-action paper signal bridge."""
    root = cfg.output_root / "polymarket_price_action"
    summary = read_json(root / "price_action_paper_signal_summary.json", default={}) or {}
    if not isinstance(summary, dict):
        summary = {}
    signals = read_csv_rows(root / "price_action_paper_signals.csv")
    rejections = read_csv_rows(root / "price_action_paper_rejections.csv")
    summary_signals = safe_float(summary.get("signals"))
    summary_rejections = safe_float(summary.get("rejections"))
    signal_count = len(signals)
    rejection_count = len(rejections)
    row_confirmation_signal_count = sum(
        1
        for signal in signals
        if signal.get("price_action_evidence_status") == "trusted_shadow_requires_broker_paper_confirmation"
        or signal.get("price_action_entry_source") == "paper_confirmation_candidate"
    )
    summary_confirmation_signals = int(safe_float(summary.get("paper_confirmation_signals")) or 0)
    row_low_price_tick_signal_count = sum(
        1
        for signal in signals
        if signal.get("price_action_evidence_status") == "low_price_tick_requires_broker_paper_confirmation"
        or signal.get("price_action_entry_source") == "low_price_tick_probe"
    )
    confirmation_signal_count = row_confirmation_signal_count
    if signal_count and summary_confirmation_signals:
        confirmation_signal_count = max(row_confirmation_signal_count, min(signal_count, summary_confirmation_signals))
    summary_signal_mismatch = summary_signals is not None and int(summary_signals) != signal_count
    summary_rejection_mismatch = summary_rejections is not None and int(summary_rejections) != rejection_count
    low_price_tick_evidence = summary.get("low_price_tick_probe_evidence")
    if not isinstance(low_price_tick_evidence, dict):
        low_price_tick_evidence = {}
    paper_confirmation_current_historical = summary.get("paper_confirmation_current_historical_analogue")
    if not isinstance(paper_confirmation_current_historical, dict):
        paper_confirmation_current_historical = {}
    paper_confirmation_blockers = (
        paper_confirmation_current_historical.get("blocked_by_state")
        if isinstance(paper_confirmation_current_historical.get("blocked_by_state"), dict)
        else {}
    )
    paper_confirmation_blocked_by_family = (
        paper_confirmation_current_historical.get("blocked_by_family")
        if isinstance(paper_confirmation_current_historical.get("blocked_by_family"), dict)
        else {}
    )
    paper_confirmation_state = paper_confirmation_current_historical.get("state")
    if not paper_confirmation_state:
        approved_current = int(safe_float(paper_confirmation_current_historical.get("approved")) or 0)
        blocked_current = int(safe_float(paper_confirmation_current_historical.get("blocked")) or 0)
        fresh_current = int(safe_float(paper_confirmation_current_historical.get("fresh_matches")) or 0)
        if approved_current > 0:
            paper_confirmation_state = "historical_analogue_current_candidates_ready"
        elif blocked_current > 0:
            paper_confirmation_state = "current_candidates_blocked_by_bridge_gates"
        elif fresh_current > 0:
            paper_confirmation_state = "fresh_matches_waiting_for_bridge_gate"
        else:
            paper_confirmation_state = "waiting_for_fresh_executable_confirmation_match"
    paper_confirmation_top_blocker = _top_counter_label(paper_confirmation_blockers)
    paper_confirmation_top_blocked_family = _top_counter_label(paper_confirmation_blocked_by_family)
    current_historical_analogue_scan = summary.get("current_historical_analogue_scan")
    if not isinstance(current_historical_analogue_scan, dict):
        current_historical_analogue_scan = {}
    historical_breadth_scan = summary.get("historical_breadth_scan")
    if not isinstance(historical_breadth_scan, dict):
        historical_breadth_scan = {}
    return {
        "status": summary.get("status") or ("missing" if not signals and not rejections else "computed"),
        "generated_at_utc": summary.get("generated_at_utc"),
        "decision": summary.get("decision") or "no_price_action_paper_signals_until_positive_cohort_evidence",
        "signals": signal_count,
        "rejections": rejection_count,
        "signal_file_rows": signal_count,
        "rejection_file_rows": rejection_count,
        "summary_signals": summary.get("signals"),
        "summary_rejections": summary.get("rejections"),
        "summary_signal_mismatch": summary_signal_mismatch,
        "summary_rejection_mismatch": summary_rejection_mismatch,
        "approved_price_action_cohorts": summary.get("approved_price_action_cohorts"),
        "approved_microstructure_cohorts": summary.get("approved_microstructure_cohorts"),
        "paper_confirmation_candidates": summary.get("paper_confirmation_candidates"),
        "paper_confirmation_current_candidates": summary.get("paper_confirmation_current_candidates"),
        "paper_confirmation_current_historical_analogue": paper_confirmation_current_historical,
        "paper_confirmation_current_historical_state": paper_confirmation_state,
        "paper_confirmation_current_fresh_matches": paper_confirmation_current_historical.get("fresh_matches"),
        "paper_confirmation_current_historical_blocked": paper_confirmation_current_historical.get("blocked"),
        "paper_confirmation_current_historical_blockers": paper_confirmation_blockers,
        "paper_confirmation_current_historical_blocked_by_family": paper_confirmation_blocked_by_family,
        "paper_confirmation_current_top_blocker": paper_confirmation_top_blocker,
        "paper_confirmation_current_top_blocked_family": paper_confirmation_top_blocked_family,
        "current_historical_analogue_scan": current_historical_analogue_scan,
        "current_historical_analogue_state": current_historical_analogue_scan.get("state"),
        "current_historical_analogue_rows": current_historical_analogue_scan.get("current_rows"),
        "current_historical_analogue_positive_matches": current_historical_analogue_scan.get("positive_matches"),
        "current_historical_analogue_blocked": current_historical_analogue_scan.get("blocked"),
        "current_historical_analogue_blockers": current_historical_analogue_scan.get("blocked_by_state"),
        "current_historical_analogue_positive_by_family": current_historical_analogue_scan.get("positive_by_family"),
        "historical_breadth_scan": historical_breadth_scan,
        "historical_breadth_state": historical_breadth_scan.get("state"),
        "historical_breadth_events": historical_breadth_scan.get("total_events"),
        "historical_breadth_validation_roi": historical_breadth_scan.get("validation_roi"),
        "historical_breadth_robust_positive_buckets": historical_breadth_scan.get("robust_positive_buckets"),
        "historical_breadth_near_positive_buckets": historical_breadth_scan.get("near_positive_buckets"),
        "historical_breadth_recommended_collection_queries": historical_breadth_scan.get("recommended_collection_queries"),
        "paper_confirmation_signals": confirmation_signal_count,
        "summary_paper_confirmation_signals": summary.get("paper_confirmation_signals"),
        "low_price_tick_probe_evidence": low_price_tick_evidence,
        "low_price_tick_probe_evidence_state": low_price_tick_evidence.get("state"),
        "low_price_tick_validation_positive_rows": low_price_tick_evidence.get("validation_positive_rows"),
        "low_price_tick_validation_roi": low_price_tick_evidence.get("validation_roi"),
        "low_price_tick_gate_reason": low_price_tick_evidence.get("gate_reason"),
        "low_price_tick_probe_candidates": summary.get("low_price_tick_probe_candidates"),
        "low_price_tick_probe_signals": max(
            row_low_price_tick_signal_count,
            int(safe_float(summary.get("low_price_tick_probe_signals")) or 0),
        ),
        "source_round_trip_rows": summary.get("source_round_trip_rows"),
        "source_microstructure_current_rows": summary.get("source_microstructure_current_rows"),
        "signal_file": str(root / "price_action_paper_signals.csv"),
        "rejection_file": str(root / "price_action_paper_rejections.csv"),
        "top_signals": signals[:25],
        "top_rejections": rejections[:25],
        "paper_only": True,
    }


def _payload_time(payload: Any) -> datetime | None:
    if not isinstance(payload, dict):
        return None
    for key in (
        "generated_at_utc",
        "updated_at_utc",
        "started_at_utc",
        "ended_at_utc",
        "timestamp_utc",
        "timestamp",
        "created_at_utc",
    ):
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


def _paper_trading_account_payload(source: str, broker_summary: dict[str, Any], forward: dict[str, Any]) -> dict[str, Any]:
    """Expose one canonical paper-trading account for dashboard cards and API consumers.

    The raw forward cycle can be older than the standalone paper broker ledger. Keeping the
    raw cycle is useful for diagnostics, but the dashboard needs a single source of truth for
    account equity, cash, exposure, and fills.
    """
    cycle_broker = forward.get("broker") if isinstance(forward, dict) and isinstance(forward.get("broker"), dict) else {}
    mismatch_fields: list[str] = []
    if broker_summary and cycle_broker:
        for field in ("equity", "cash", "total_exposure", "buy_orders_filled", "exit_orders_filled", "orders_filled"):
            current = safe_float(broker_summary.get(field))
            cycle = safe_float(cycle_broker.get(field))
            if current is None and cycle is None:
                continue
            if current is None or cycle is None or abs(current - cycle) > 0.005:
                mismatch_fields.append(field)
    return {
        "source": source,
        "generated_at_utc": broker_summary.get("generated_at_utc") if isinstance(broker_summary, dict) else "",
        "broker": broker_summary,
        "forward_cycle_broker_generated_at_utc": cycle_broker.get("generated_at_utc", "") if cycle_broker else "",
        "forward_cycle_broker_mismatch": bool(mismatch_fields),
        "forward_cycle_broker_mismatch_fields": mismatch_fields,
        "canonical_for_dashboard": True,
    }


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


def _legacy_full_cycle_status(heartbeat: dict[str, Any], live_loop_status: str) -> dict[str, Any]:
    """Display-safe status for the old local live-loop full-cycle heartbeat.

    The current safe lane is the shadow-research cycle. The old live-loop heartbeat is useful
    as audit context, but a stale file that still says ``running`` must not look like an active
    trading process in the dashboard.
    """
    if not isinstance(heartbeat, dict) or not heartbeat:
        return {
            "raw_status": "",
            "effective_status": "not_started",
            "display_status": "not started",
            "age_seconds": None,
            "reason": "No legacy local live-loop heartbeat was found; dashboard is using the shadow-research lane.",
        }

    full_cycle = heartbeat.get("full_prediction_cycle") if isinstance(heartbeat.get("full_prediction_cycle"), dict) else {}
    raw_status = str(full_cycle.get("status") or heartbeat.get("status") or "unknown").strip() or "unknown"
    age = _age_seconds(heartbeat)
    if live_loop_status == "stale":
        return {
            "raw_status": raw_status,
            "effective_status": "stale",
            "display_status": f"stale legacy heartbeat (raw: {raw_status})",
            "age_seconds": age,
            "reason": (
                "This is an old local live-loop status file. It is kept for audit history, "
                "but the current active lane is shadow research; do not treat its raw "
                f"'{raw_status}' value as a running bot."
            ),
        }
    if live_loop_status == "live":
        return {
            "raw_status": raw_status,
            "effective_status": "live",
            "display_status": raw_status,
            "age_seconds": age,
            "reason": "Legacy local live-loop heartbeat is fresh.",
        }
    if live_loop_status == "down":
        display_status = f"down (raw: {raw_status})"
    else:
        display_status = f"{live_loop_status or 'unknown'} (raw: {raw_status})"
    return {
        "raw_status": raw_status,
        "effective_status": live_loop_status or "unknown",
        "display_status": display_status,
        "age_seconds": age,
        "reason": "Legacy local live-loop heartbeat is not the current safe research lane.",
    }


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


def _shadow_research_cycle_status(cfg: EngineConfig) -> dict[str, Any]:
    path = cfg.path.parent / "work" / "shadow_research_cycle_latest_status.json"
    payload = _read_json_lenient(path, default={}) or {}
    if not isinstance(payload, dict) or not payload:
        return {
            "status": "not_started",
            "effective_status": "not_started",
            "status_file": str(path),
            "age_seconds": None,
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
            "reason": "Shadow research cycle has not written a status file yet.",
        }
    age = _age_seconds(payload)
    # The scheduled shadow-research lane is intentionally slower than a live loop,
    # but a multi-hour "running" file is stale observability, not an active worker.
    stale_after_seconds = safe_float(
        (cfg.raw.get("shadow_research_cycle", {}) or {}).get("status_stale_after_seconds")
    )
    if stale_after_seconds is None:
        stale_after_seconds = 45 * 60
    status = str(payload.get("status") or "unknown")
    effective_status = status
    reason = str(payload.get("reason") or "")
    runner_process_active = None
    if age is not None and age > stale_after_seconds:
        effective_status = "stale"
        reason = (
            f"Shadow research status is {round(age, 1)} seconds old; "
            "treat this as stale even if the file still says running."
        )
    elif status in {"running", "started", "in_progress"} and payload.get("runner_process_id") is not None:
        runner_process_active = _local_process_exists(payload.get("runner_process_id"))
        if runner_process_active is False:
            effective_status = "interrupted"
            reason = (
                f"Shadow research status says {status}, but runner process "
                f"{payload.get('runner_process_id')} is no longer active; treat the cycle as interrupted."
            )
    return {
        **payload,
        "status_file": str(path),
        "age_seconds": age,
        "stale_after_seconds": stale_after_seconds,
        "effective_status": effective_status,
        "reason": reason,
        "runner_process_active": runner_process_active,
        "paper_trading_invoked": bool(payload.get("paper_trading_invoked", False)),
        "live_trading_invoked": bool(payload.get("live_trading_invoked", False)),
    }


def _dashboard_oversight_status(
    *,
    live_loop_status: str,
    heartbeat: dict[str, Any],
    shadow_research: dict[str, Any],
    governance_refresh: dict[str, Any],
    price_action_model: dict[str, Any],
    price_action_paper_signals: dict[str, Any],
    goal_plan: dict[str, Any],
    trade_diagnostics: dict[str, Any],
    evidence_freshness: dict[str, Any],
    strategy_v2: dict[str, Any],
    dutch_arb: dict[str, Any],
) -> dict[str, Any]:
    """Single oversight summary for stale/live/model gate visibility."""
    live_age = _age_seconds(heartbeat)
    shadow_status = str(shadow_research.get("effective_status") or shadow_research.get("status") or "unknown")
    shadow_age = safe_float(shadow_research.get("age_seconds"))
    governance_refresh_status = str(governance_refresh.get("status") or "missing")
    governance_refresh_stage = str(
        governance_refresh.get("failed_stage")
        or governance_refresh.get("current_stage")
        or "unknown"
    )
    governance_refresh_age = _age_seconds(governance_refresh)
    model_age = _age_seconds(price_action_model)
    signal_age = _age_seconds(price_action_paper_signals)
    validation_gap = price_action_model.get("validation_gap", {})
    if not isinstance(validation_gap, dict):
        validation_gap = {}
    cohort_transfer = price_action_model.get("cohort_transfer", {})
    if not isinstance(cohort_transfer, dict):
        cohort_transfer = {}
    train_positive = safe_float(price_action_model.get("train_positive_targets")) or 0.0
    validation_positive = safe_float(price_action_model.get("validation_positive_targets")) or 0.0
    validation_gap_active = bool(
        validation_gap.get("state") == "needs_positive_validation_examples"
        or (train_positive > 0 and validation_positive <= 0)
    )
    approved_signals = safe_float(price_action_paper_signals.get("signals"))
    if approved_signals is None:
        approved_signals = safe_float(trade_diagnostics.get("approved_signals_count")) or 0.0

    alerts: list[dict[str, Any]] = []

    def add_alert(severity: str, title: str, body: str) -> None:
        alerts.append({"severity": severity, "title": title, "body": body})

    legacy_full_cycle = (
        evidence_freshness.get("legacy_full_cycle") if isinstance(evidence_freshness.get("legacy_full_cycle"), dict) else {}
    )
    legacy_live_driver = str(legacy_full_cycle.get("effective_status") or "").lower() == "live"

    if shadow_status in {"not_started", "missing"}:
        if legacy_live_driver:
            add_alert(
                "info",
                "Driver: legacy live loop (VPS deployment); shadow-cycle status file not expected.",
                str(legacy_full_cycle.get("reason") or "Legacy live loop heartbeat is fresh."),
            )
        else:
            add_alert(
                "warn",
                "Shadow research cycle has not started",
                str(shadow_research.get("reason") or "The current safe research lane has not produced a status file."),
            )
    elif shadow_status == "stale":
        add_alert(
            "bad",
            "Shadow research cycle is stale",
            str(shadow_research.get("reason") or "The current safe research lane has not refreshed recently."),
        )
    elif shadow_status == "interrupted":
        add_alert(
            "bad",
            "Shadow research cycle was interrupted",
            str(
                shadow_research.get("reason")
                or "The current safe research lane claimed to be running, but the local runner process is no longer active."
            ),
        )
    elif shadow_status in {"error", "failed"}:
        add_alert(
            "bad",
            "Shadow research cycle failed",
            str(shadow_research.get("reason") or shadow_research.get("error") or "The current safe research lane reported failure."),
        )
    elif shadow_status in {"skipped_high_memory", "stopped_high_memory", "memory_paused"}:
        add_alert(
            "warn",
            "Shadow research paused by memory guard",
            str(
                shadow_research.get("reason")
                or "The current safe research lane skipped heavier websocket/model work because local memory was above the configured guardrail."
            ),
        )
    elif shadow_status in {"running", "ok", "completed", "success"}:
        pass
    elif live_loop_status != "live":
        add_alert(
            "warn",
            "Legacy local live loop is not running",
            (
                "This is expected unless the audit has approved the old local live-loop lane. "
                f"Legacy heartbeat status is {live_loop_status or 'unknown'}; age is {round(live_age, 1) if live_age is not None else 'unknown'} seconds."
            ),
        )
    liquidity_status = str(shadow_research.get("liquidity_discovery_status") or "")
    if liquidity_status in {"degraded_non_blocking", "warning", "failed", "timeout"}:
        add_alert(
            "warn",
            "Liquidity discovery degraded, cycle continued",
            str(
                shadow_research.get("liquidity_discovery_error")
                or "The cycle continued with the latest available liquidity evidence instead of blocking the learning loop."
            ),
        )
    if governance_refresh_status == "failed":
        add_alert(
            "bad",
            "Governance refresh failed",
            (
                f"Stage {governance_refresh_stage} failed: "
                f"{governance_refresh.get('error_type') or 'error'}: {governance_refresh.get('error') or 'no detail'}"
            ),
        )
    if model_age is None:
        add_alert("warn", "Model freshness unknown", "Price-action model summary has no usable timestamp.")
    elif model_age > MODEL_SCORING_STALE_AFTER_SECONDS:
        refresh_context = ""
        if governance_refresh_status == "running":
            refresh_context = (
                f" Active refresh stage: {governance_refresh_stage}; "
                f"elapsed {round(safe_float(governance_refresh.get('elapsed_seconds')) or 0.0, 1)} seconds."
            )
        elif governance_refresh_status == "failed":
            refresh_context = f" Last refresh failed at stage {governance_refresh_stage}."
        add_alert(
            "bad",
            "Model scoring is stale",
            (
                f"Price-action model summary is {round(model_age, 1)} seconds old; "
                f"re-score before trusting new promotions.{refresh_context}"
            ),
        )
    if validation_gap_active:
        add_alert(
            "warn",
            "Model needs positive validation examples",
            str(
                validation_gap.get("reason")
                or "Train positives exist, but validation still has no positive executable bid repricing examples."
            ),
        )
    if cohort_transfer.get("state") == "positive_targets_do_not_transfer":
        add_alert(
            "warn",
            "Model edge does not transfer across cohorts",
            str(
                cohort_transfer.get("reason")
                or "Train and validation positives are appearing in different market families, so promotion remains blocked."
            ),
        )
    if approved_signals <= 0:
        add_alert(
            "warn",
            "No approved paper signals",
            str(
                trade_diagnostics.get("main_blocker")
                or price_action_paper_signals.get("decision")
                or goal_plan.get("main_gap")
                or "Current gates are blocking paper entries."
            ),
        )
    if evidence_freshness.get("broker_refresh_needed"):
        add_alert("warn", "Broker refresh pending", "Paper broker is behind the latest approved signal or exit-probe file.")
    stale_open_count = int(safe_float(evidence_freshness.get("stale_open_position_count")) or 0)
    if stale_open_count > 0:
        add_alert(
            "bad",
            "Stale open paper positions",
            f"{stale_open_count} open paper position(s) are past market close without fresh quotes or clean resolution evidence.",
        )
    posture = str(evidence_freshness.get("strategy_v2_runtime_posture") or strategy_v2.get("runtime_posture") or "")
    strategy_cycle_status = strategy_v2.get("cycle_status") if isinstance(strategy_v2.get("cycle_status"), dict) else {}
    strategy_pause_time = parse_timestamp(
        strategy_cycle_status.get("ended_at_utc")
        or strategy_cycle_status.get("generated_at_utc")
        or strategy_cycle_status.get("updated_at_utc")
        or strategy_cycle_status.get("started_at_utc")
    )
    shadow_refresh_time = parse_timestamp(
        shadow_research.get("ended_at_utc")
        or shadow_research.get("generated_at_utc")
        or shadow_research.get("updated_at_utc")
        or shadow_research.get("started_at_utc")
    )
    memory_pause_obsolete = bool(
        posture in {"memory_paused", "guard_paused"}
        and shadow_status in {"running", "ok", "completed", "success"}
        and strategy_pause_time is not None
        and shadow_refresh_time is not None
        and shadow_refresh_time > strategy_pause_time
    )
    if posture in {"memory_paused", "guard_paused"} and not memory_pause_obsolete:
        add_alert(
            "warn",
            "Collection paused by memory guard",
            str(
                evidence_freshness.get("strategy_v2_runtime_reason")
                or strategy_v2.get("runtime_reason")
                or "The local guard paused heavier collection because RAM was high."
            ),
        )
    persistent_arbs = dutch_arb.get("persistent_alerts") if isinstance(dutch_arb, dict) else []
    if isinstance(persistent_arbs, list) and persistent_arbs:
        best = persistent_arbs[0] if isinstance(persistent_arbs[0], dict) else {}
        add_alert(
            "info",
            "Dutch-book arb basket persisted",
            (
                f"{len(persistent_arbs)} dry-run basket(s) persisted above the alert threshold for 3+ scans. "
                f"Best visible basket: {best.get('event') or best.get('event_id') or 'unknown'}."
            ),
        )

    status = "ok"
    if any(alert.get("severity") == "bad" for alert in alerts):
        status = "bad"
    elif any(alert.get("severity") == "warn" for alert in alerts):
        status = "warn"
    return {
        "status": status,
        "alerts": alerts,
        "live_loop_status": live_loop_status,
        "live_heartbeat_age_seconds": live_age,
        "shadow_research_status": shadow_status,
        "shadow_research_age_seconds": shadow_age,
        "shadow_research_reason": shadow_research.get("reason"),
        "governance_refresh_status": governance_refresh_status,
        "governance_refresh_stage": governance_refresh_stage,
        "governance_refresh_age_seconds": governance_refresh_age,
        "governance_refresh_elapsed_seconds": safe_float(governance_refresh.get("elapsed_seconds")),
        "model_status": price_action_model.get("status"),
        "model_decision": price_action_model.get("decision"),
        "model_age_seconds": model_age,
        "model_stale_after_seconds": MODEL_SCORING_STALE_AFTER_SECONDS,
        "signal_age_seconds": signal_age,
        "approved_signal_count": int(approved_signals),
        "validation_gap_active": validation_gap_active,
        "validation_gap_collection_queries": validation_gap.get("collection_queries", []),
        "cohort_transfer_state": cohort_transfer.get("state"),
        "strategy_v2_memory_pause_obsolete": memory_pause_obsolete,
        "stale_open_position_count": stale_open_count,
        "stale_open_positions": evidence_freshness.get("stale_open_positions", []),
        "goal_gap": goal_plan.get("main_gap"),
        "main_trade_blocker": trade_diagnostics.get("main_blocker"),
    }


def _first_non_empty_list(*values: Any) -> list[Any]:
    for value in values:
        if isinstance(value, list) and value:
            return value
    return []


def _compact_list(values: Any, *, limit: int = 5) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in out:
            continue
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _usd_text(value: Any) -> str:
    number = safe_float(value)
    return "-" if number is None else f"${number:.2f}"


def _signed_usd_text(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return "-"
    return f"+${number:.2f}" if number > 0 else f"${number:.2f}"


def _route_evidence_count(row: dict[str, Any], route: str) -> int | None:
    keys = (
        (
            "paper_audited_round_trips",
            "paper_audit_round_trips",
            "closed_trades",
            "paper_sell_fills",
            "paper_buy_fills",
            "buy_fills",
        )
        if route == "paper"
        else (
            "shadow_sell_fills",
            "closed_trades",
            "shadow_fills",
            "buy_fills",
        )
    )
    for key in keys:
        value = safe_float(row.get(key))
        if value is not None:
            return int(value)
    return None


def _route_pnl(row: dict[str, Any], route: str) -> float | None:
    key = "forward_paper_pnl_usdc" if route == "paper" else "forward_shadow_pnl_usdc"
    value = safe_float(row.get(key))
    if value is not None:
        return float(value)
    fallback = safe_float(row.get("realized_pnl_usdc") or row.get("total_mark_pnl_usdc"))
    return None if fallback is None else float(fallback)


def _route_evidence_hours(row: dict[str, Any], route: str) -> float | None:
    keys = (
        "evidence_hours",
        "elapsed_hours",
        "paper_elapsed_hours",
        "paper_audited_elapsed_hours",
        "shadow_elapsed_hours",
        "evidence_elapsed_hours",
        "hours_since_first_evidence",
        "hours_observed",
        "age_hours",
    )
    for key in keys:
        value = safe_float(row.get(key))
        if value is not None:
            return float(value)
    pnl = _route_pnl(row, route)
    monthly = safe_float(row.get("monthly_run_rate_usdc") or row.get("shadow_monthly_run_rate_usdc"))
    if pnl is None or monthly is None or monthly == 0:
        return None
    return abs(float(pnl)) * 720.0 / abs(float(monthly))


def _duration_hours_text(hours: float | None) -> str:
    if hours is None:
        return "?h"
    if hours < 1:
        return f"{max(1, round(hours * 60))}m"
    if hours < 24:
        return f"{hours:.1f}h"
    return f"{hours / 24.0:.1f}d"


def _best_route_row(price_action_goal: dict[str, Any], price_action_feedback: dict[str, Any], route: str) -> dict[str, Any] | None:
    rows: list[dict[str, Any]] = []
    for key in ("top_cohorts",):
        value = price_action_goal.get(key)
        if isinstance(value, list):
            rows.extend(row for row in value if isinstance(row, dict))
    for key in ("forward_paper_preview", "forward_shadow_preview", "paper_confirmation_preview"):
        value = price_action_feedback.get(key)
        if isinstance(value, list):
            rows.extend(row for row in value if isinstance(row, dict))

    candidates = [
        row
        for row in rows
        if (_route_pnl(row, route) or 0.0) > 0
        and str(row.get("forward_edge_blocker") or "") == ""
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda row: (
            safe_float(row.get("monthly_run_rate_usdc") or row.get("shadow_monthly_run_rate_usdc")) or -999999.0,
            _route_pnl(row, route) or -999999.0,
        ),
        reverse=True,
    )
    return candidates[0]


def _route_evidence_text(
    price_action_goal: dict[str, Any],
    price_action_feedback: dict[str, Any],
    route: str,
    *,
    minimum_filled_orders: int = 5,
    minimum_evidence_hours: float = 72.0,
) -> str:
    row = _best_route_row(price_action_goal, price_action_feedback, route)
    if row is None:
        return f"best {route} cohort: no positive evidence yet"
    count = _route_evidence_count(row, route)
    hours = _route_evidence_hours(row, route)
    pnl = _route_pnl(row, route)
    count_text = "?" if count is None else str(count)
    noun = "round trip" if count == 1 else "round trips"
    base = f"best {route} cohort: {_signed_usd_text(pnl)} on {count_text} {noun} over {_duration_hours_text(hours)}"
    monthly = safe_float(row.get("monthly_run_rate_usdc") or row.get("shadow_monthly_run_rate_usdc"))
    if (
        count is not None
        and count >= minimum_filled_orders
        and hours is not None
        and hours >= minimum_evidence_hours
        and monthly is not None
    ):
        return f"{base} ({_usd_text(monthly)}/month evidence-qualified)"
    return f"{base} (n too small to annualise)"


def _best_edge_route_text(price_action_goal: dict[str, Any], price_action_feedback: dict[str, Any]) -> str:
    return (
        f"{_route_evidence_text(price_action_goal, price_action_feedback, 'shadow')} / "
        f"{_route_evidence_text(price_action_goal, price_action_feedback, 'paper')}"
    )


def _decision_useful_summary(
    *,
    actual_target: dict[str, Any],
    broker_summary: dict[str, Any],
    shadow_research: dict[str, Any],
    price_action_model: dict[str, Any],
    price_action_paper_signals: dict[str, Any],
    goal_plan: dict[str, Any],
    trade_signal_audit: dict[str, Any],
    trade_diagnostics: dict[str, Any],
    oversight_status: dict[str, Any],
    evidence_freshness: dict[str, Any],
    price_action_feedback: dict[str, Any],
    research_focus: dict[str, Any],
    websocket_summary: dict[str, Any],
    websocket_feature_summary: dict[str, Any],
    worldcup_validation: dict[str, Any],
    alpha_bridge: dict[str, Any],
    sharp_sports_funnel: dict[str, Any],
) -> dict[str, Any]:
    """Build the small, operator-facing dashboard contract.

    The raw dashboard payload is intentionally rich for audits. This summary is
    deliberately narrow: it only exposes facts that change the next trading,
    learning, or safety decision.
    """

    approved_signals = int(
        safe_float(price_action_paper_signals.get("signals"))
        if safe_float(price_action_paper_signals.get("signals")) is not None
        else safe_float(trade_diagnostics.get("approved_signals_count")) or 0
    )
    rejected_signals = int(
        safe_float(price_action_paper_signals.get("rejections"))
        if safe_float(price_action_paper_signals.get("rejections")) is not None
        else safe_float(trade_diagnostics.get("rejected_signals_count")) or 0
    )
    broker_work_pending = bool(
        price_action_paper_signals.get("broker_refresh_needed")
        or safe_float(price_action_paper_signals.get("pending_broker_signals"))
        or safe_float(price_action_paper_signals.get("pending_broker_confirmation_signals"))
        or safe_float(price_action_paper_signals.get("pending_broker_exit_probes"))
    )
    paper_confirmation_mode = bool(
        price_action_paper_signals.get("paper_only")
        or safe_float(price_action_paper_signals.get("paper_confirmation_signals"))
        or safe_float(price_action_paper_signals.get("pending_broker_confirmation_signals"))
    )
    shadow_status = str(oversight_status.get("shadow_research_status") or shadow_research.get("effective_status") or "")
    model_age = _age_seconds(price_action_model)
    model_stale = bool(model_age is not None and model_age > MODEL_SCORING_STALE_AFTER_SECONDS)
    validation_gap = price_action_model.get("validation_gap") if isinstance(price_action_model.get("validation_gap"), dict) else {}
    cohort_transfer = price_action_model.get("cohort_transfer") if isinstance(price_action_model.get("cohort_transfer"), dict) else {}
    validation_gap_active = bool(oversight_status.get("validation_gap_active"))
    transfer_blocked = cohort_transfer.get("state") == "positive_targets_do_not_transfer"
    price_action_goal = goal_plan.get("price_action_goal_state") if isinstance(goal_plan.get("price_action_goal_state"), dict) else {}
    confirmation_target_rows = (
        trade_signal_audit.get("paper_confirmation_targets")
        if isinstance(trade_signal_audit.get("paper_confirmation_targets"), list)
        else []
    )
    confirmation_executable_rows = sum(
        int(safe_float(row.get("current_executable_rows")) or 0)
        for row in confirmation_target_rows
        if isinstance(row, dict)
    )
    confirmation_missing_targets = sum(
        1 for row in confirmation_target_rows if isinstance(row, dict) and row.get("missing_fresh_candidate")
    )
    current_candidates_blocked = bool(
        trade_signal_audit.get("verdict") == "trusted_edge_current_candidates_blocked_by_gate"
    )
    train_threshold_selection = (
        price_action_model.get("train_threshold_selection")
        if isinstance(price_action_model.get("train_threshold_selection"), dict)
        else {}
    )
    chosen_train_metrics = (
        train_threshold_selection.get("chosen_train_metrics")
        if isinstance(train_threshold_selection.get("chosen_train_metrics"), dict)
        else {}
    )
    validation_selected_for_decision = (
        price_action_model.get("validation_selected")
        if isinstance(price_action_model.get("validation_selected"), dict)
        else {}
    )
    validation_rank_for_decision = (
        price_action_model.get("validation_rank_diagnostics")
        if isinstance(price_action_model.get("validation_rank_diagnostics"), dict)
        else {}
    )
    validation_blockers_for_decision = (
        price_action_model.get("validation_blockers")
        if isinstance(price_action_model.get("validation_blockers"), list)
        else []
    )
    selected_validation_trades_for_decision = int(
        safe_float(validation_selected_for_decision.get("selected_trades")) or 0
    )
    model_threshold_blocked = bool(
        str(price_action_model.get("status") or "") == "trained"
        and selected_validation_trades_for_decision <= 0
        and (
            chosen_train_metrics.get("train_threshold_pass") is False
            or any(
                "no probability threshold cleared the training split trade gates" in str(item)
                for item in validation_blockers_for_decision
            )
        )
    )
    trusted_missing_fresh = bool(
        trade_signal_audit.get("verdict") == "trusted_edge_missing_fresh_candidate"
        or (
            str(price_action_paper_signals.get("decision") or "").find("trusted_shadow_edge_waiting_for_fresh_executable_candidate") >= 0
            and confirmation_executable_rows <= 0
            and confirmation_missing_targets > 0
        )
    )
    proposed_collect_now = _first_non_empty_list(
        research_focus.get("collection_queries"),
        trade_signal_audit.get("missing_confirmation_queries"),
        price_action_goal.get("collection_queries"),
        price_action_feedback.get("collection_queries"),
        validation_gap.get("collection_queries"),
    )
    active_collect_now, frozen_collect_exclusions = partition_active_queries(proposed_collect_now)
    if not active_collect_now:
        active_collect_now = list(primary_hypothesis_targets({}, scan_sequence=1).values())
    collect_now = _compact_list(active_collect_now, limit=6)

    target_monthly = safe_float(actual_target.get("target_monthly_profit_usdc")) or safe_float(
        goal_plan.get("target_monthly_profit_usdc")
    ) or 100.0
    run_rate = safe_float(actual_target.get("monthly_run_rate_usdc"))
    actual_pnl = safe_float(actual_target.get("actual_pnl_since_baseline_usdc")) or 0.0
    decision_pnl = safe_float(goal_plan.get("decision_pnl_usdc"))
    if decision_pnl is None:
        decision_pnl = actual_pnl
    decision_run_rate = safe_float(goal_plan.get("decision_monthly_run_rate_usdc"))
    if decision_run_rate is None:
        decision_run_rate = run_rate
    audited_pnl = safe_float(goal_plan.get("audited_pnl_since_baseline_usdc"))
    proof_pnl = safe_float(goal_plan.get("proof_verified_pnl_since_baseline_usdc"))
    proof_round_trip_value = safe_float(goal_plan.get("proof_verified_round_trips_since_baseline"))
    proof_round_trips = int(proof_round_trip_value) if proof_round_trip_value is not None else None
    proof_entry_missing = safe_float(goal_plan.get("proof_entry_snapshot_missing_round_trips"))
    proof_exit_missing = safe_float(goal_plan.get("proof_exit_snapshot_missing_round_trips"))
    decision_pnl_source = str(
        goal_plan.get("decision_pnl_source")
        or ("proof_verified_entry_exit_quotes" if proof_pnl is not None else "audited_quote_consistent_exits")
    )
    pnl_audit_state = str(goal_plan.get("pnl_audit_state") or "unknown")
    profit_target_proof_status = str(goal_plan.get("profit_target_proof_status") or "unknown")
    profit_target_proof_blockers = _compact_list(goal_plan.get("profit_target_proof_blockers"), limit=4)
    verified_evidence_ready = bool(goal_plan.get("verified_evidence_ready"))
    audited_round_trips = int(safe_float(goal_plan.get("audited_round_trips_since_baseline")) or 0)
    evidence_round_trips = proof_round_trips if proof_round_trips is not None else audited_round_trips
    evidence_round_trip_label = "proof round trips" if proof_round_trips is not None else "audited round trips"
    minimum_audited_round_trips = int(safe_float(goal_plan.get("minimum_audited_round_trips_for_on_pace")) or 5)
    best_repricing_run_rate = safe_float(price_action_goal.get("best_repricing_monthly_run_rate_usdc"))
    forward_paper_run_rate = safe_float(price_action_goal.get("best_forward_paper_monthly_run_rate_usdc"))
    edge_route_text = _best_edge_route_text(price_action_goal, price_action_feedback)
    paper_route_row = _best_route_row(price_action_goal, price_action_feedback, "paper")
    paper_route_qualified = bool(
        paper_route_row
        and (_route_evidence_count(paper_route_row, "paper") or 0) >= 5
        and (_route_evidence_hours(paper_route_row, "paper") or 0.0) >= 72.0
    )
    recent_loss_exits = int(safe_float(trade_signal_audit.get("recent_loss_exit_count")) or 0)
    recent_exits = int(safe_float(trade_signal_audit.get("recent_exit_count")) or 0)
    missing_targets = int(safe_float(trade_signal_audit.get("missing_confirmation_target_count")) or confirmation_missing_targets)

    if shadow_status == "stale":
        trade_decision = "WAIT: RESEARCH STALE"
        decision_class = "bad"
        headline = "Do not trust stale research state"
        primary_blocker = str(
            shadow_research.get("reason")
            or oversight_status.get("shadow_research_reason")
            or "The current research cycle is stale."
        )
        next_action = "Refresh the shadow research/governance cycle before acting on any signal counts."
        unlock_condition = "A fresh shadow-research cycle must complete or be actively running with current model and signal files."
    elif shadow_status in {"error", "failed"}:
        trade_decision = "WAIT: CYCLE FAILED"
        decision_class = "bad"
        headline = "The latest research cycle failed before producing trustworthy signals"
        primary_blocker = str(
            shadow_research.get("error")
            or shadow_research.get("reason")
            or oversight_status.get("shadow_research_reason")
            or "The current safe research lane reported failure."
        )
        next_action = "Fix or rerun the failed shadow research cycle after memory is below the guardrail; do not trade from partial outputs."
        unlock_condition = "The shadow cycle must complete and refresh model, signal, and dashboard artifacts after the failing phase."
    elif shadow_status == "interrupted":
        trade_decision = "WAIT: CYCLE INTERRUPTED"
        decision_class = "bad"
        headline = "The research cycle stopped without a clean final status"
        primary_blocker = str(
            shadow_research.get("reason")
            or oversight_status.get("shadow_research_reason")
            or "The latest research cycle appears to have stopped before publishing a clean result."
        )
        next_action = "Let the next scheduled shadow cycle restart after memory is below the guardrail; do not trade from partial outputs."
        unlock_condition = "A fresh shadow-research cycle must complete and publish model, signal, and dashboard artifacts."
    elif shadow_status in {"skipped_high_memory", "stopped_high_memory", "memory_paused"}:
        trade_decision = "WAIT: MEMORY GUARD"
        decision_class = "warn"
        headline = "The local resource guard stopped the research cycle"
        primary_blocker = str(
            shadow_research.get("reason")
            or "The cycle skipped or stopped because RAM was above the configured guardrail."
        )
        next_action = "Wait for RAM to drop, then allow the next shadow cycle to refresh evidence."
        unlock_condition = "A fresh cycle must complete under the memory guard without weakening trading gates."
    elif shadow_status in {"running", "started", "in_progress"} and model_stale:
        trade_decision = "WAIT: CYCLE RUNNING"
        decision_class = "warn"
        headline = "A fresh research cycle is already updating the model"
        primary_blocker = (
            f"The previous price-action model summary is {round(model_age, 1) if model_age is not None else 'unknown'} seconds old, "
            "but the current shadow cycle is running and should refresh model, signal, and dashboard artifacts."
        )
        next_action = "Let the running shadow cycle finish; do not start a competing governance refresh."
        unlock_condition = "The running cycle must complete and publish fresh model, signal, and dashboard artifacts."
    elif model_stale and str(oversight_status.get("governance_refresh_status") or "") == "running":
        refresh_stage = str(oversight_status.get("governance_refresh_stage") or "unknown")
        refresh_elapsed = safe_float(oversight_status.get("governance_refresh_elapsed_seconds")) or 0.0
        trade_decision = "WAIT: GOVERNANCE REFRESH RUNNING"
        decision_class = "warn"
        headline = "The single-owner governance refresh is rebuilding model evidence"
        primary_blocker = (
            f"The previous price-action model is stale; the active refresh is at {refresh_stage} "
            f"after {round(refresh_elapsed, 1)} seconds."
        )
        next_action = "Let the locked refresh finish; do not start a competing refresh."
        unlock_condition = "The active refresh must publish a fresh model timestamp while governance gates remain unchanged."
    elif model_stale and str(oversight_status.get("governance_refresh_status") or "") == "failed":
        refresh_stage = str(oversight_status.get("governance_refresh_stage") or "unknown")
        trade_decision = "WAIT: GOVERNANCE REFRESH FAILED"
        decision_class = "bad"
        headline = "The governed model refresh failed"
        primary_blocker = f"The latest governance refresh failed at stage {refresh_stage}; the previous model is stale."
        next_action = "Fix the named failing stage and rerun one locked governance refresh."
        unlock_condition = "A single governance refresh must complete the model-critical stages and publish a fresh model timestamp."
    elif model_stale:
        trade_decision = "WAIT: MODEL STALE"
        decision_class = "bad"
        headline = "The model score is too old for trading decisions"
        primary_blocker = f"Price-action model summary is {round(model_age, 1) if model_age is not None else 'unknown'} seconds old."
        next_action = "Refresh governance so the price-action model is rescored before any paper promotion."
        unlock_condition = "Fresh model timestamp plus unchanged governance gates."
    elif approved_signals > 0:
        trade_decision = (
            "RUN PAPER CONFIRMATION BROKER"
            if paper_confirmation_mode and broker_work_pending
            else "PAPER CONFIRMATION READY"
            if paper_confirmation_mode
            else "RUN PAPER BROKER"
            if broker_work_pending
            else "PAPER READY"
        )
        decision_class = "good"
        headline = "Paper-confirmation probes are ready" if paper_confirmation_mode else "Approved governed paper signals exist"
        primary_blocker = (
            "Paper-confirmation signals are waiting for the broker refresh; this is not live approval or sizing promotion."
            if paper_confirmation_mode and broker_work_pending
            else "Paper-confirmation signals are ready; monitor them as evidence, not proof."
            if paper_confirmation_mode
            else "Approved signals are waiting for the broker refresh."
            if broker_work_pending
            else "Approved signals have reached the paper lane; monitor fills, exits, and realised P&L."
        )
        next_action = (
            "Run/allow the paper broker refresh and verify confirmation fills are recorded."
            if paper_confirmation_mode and broker_work_pending
            else "Monitor confirmation fills, exits, and cohort P&L before promotion."
            if paper_confirmation_mode
            else "Run/allow the paper broker refresh and verify fills are recorded."
            if broker_work_pending
            else "Monitor forward paper P&L by signal cohort and keep stake capped until evidence remains positive."
        )
        unlock_condition = "Forward paper fills must produce positive cohort evidence before any sizing increase."
    elif model_threshold_blocked:
        trade_decision = "LEARN: MODEL SELECTS NO TRADES"
        decision_class = "warn"
        headline = "The trained ML model has positives, but no validated tradable selection"
        threshold_source = str(chosen_train_metrics.get("threshold_source") or "unknown threshold policy")
        best_positive_rank = validation_rank_for_decision.get("best_positive_rank")
        blocker = (
            validation_blockers_for_decision[0]
            if validation_blockers_for_decision
            else "No probability/rank threshold produced an evidence-qualified validation trade set."
        )
        primary_blocker = (
            f"{blocker}; selected validation trades={selected_validation_trades_for_decision}; "
            f"train positives={price_action_model.get('train_positive_targets', '-')}; "
            f"validation positives={price_action_model.get('validation_positive_targets', '-')}; "
            f"best positive validation rank={best_positive_rank if best_positive_rank is not None else 'n/a'}; "
            f"threshold source={threshold_source}."
        )
        next_action = str(
            validation_rank_for_decision.get("next_action")
            or "Use the missed-positive/ranked-candidate diagnostics to add anti-chase features and collect more matching bid/ask rows."
        )
        unlock_condition = (
            "A train-derived probability/rank threshold must select at least the configured minimum validation trades "
            "and pass positive P&L, ROI, win/payoff, CI, and drawdown gates."
        )
    elif current_candidates_blocked:
        trade_decision = "WAIT: CANDIDATES BLOCKED"
        decision_class = "warn"
        headline = "Fresh candidates exist, but none passes the governed signal thesis"
        primary_blocker = (
            "Fresh executable candidates exist for trusted confirmation cohorts, but they are still blocked by model, "
            "cohort-transfer, spread, risk, or recent-loss evidence gates."
        )
        next_action = str(
            trade_signal_audit.get("next_action")
            or "Use rejection reasons and recent exits to tighten the signal thesis before allowing any paper entry."
        )
        unlock_condition = "At least one fresh candidate must move from rejected/blocked into a governed paper signal with entry ask, exit bid, and risk math."
    elif trusted_missing_fresh:
        trade_decision = "COLLECT FRESH CANDIDATE"
        decision_class = "warn"
        headline = "Trusted shadow edge exists, but no fresh executable candidate"
        primary_blocker = (
            "The current gap is not settlement. It is missing a fresh open market row with executable bid/ask, acceptable spread, "
            "and the same trusted paper-confirmation cohort."
        )
        next_action = trade_signal_audit.get("next_action") or (
            "Collect fresh websocket/liquidity candidates for " + (", ".join(collect_now) if collect_now else "the trusted cohorts") + "."
        )
        unlock_condition = "A fresh non-excluded candidate must appear with executable ask entry and bid exit math that passes the paper bridge."
    elif validation_gap_active or transfer_blocked:
        trade_decision = "LEARN: MODEL BLOCKED"
        decision_class = "warn"
        headline = "The model has not proven a repeatable edge"
        primary_blocker = str(
            cohort_transfer.get("reason")
            or validation_gap.get("reason")
            or price_action_model.get("decision")
            or "The model needs forward validation evidence before paper trading."
        )
        next_action = (
            "Collect same-family bid/ask repricing examples and keep suppressed cohorts out of the broker."
        )
        unlock_condition = "Positive validation examples must transfer within the same family/signal cohort, after bid/ask costs."
    else:
        trade_decision = "DO NOT TRADE YET"
        decision_class = "warn"
        headline = "No governed paper entry is currently justified"
        primary_blocker = str(
            trade_diagnostics.get("main_blocker")
            or price_action_paper_signals.get("decision")
            or goal_plan.get("main_gap")
            or "Current gates are blocking paper entries."
        )
        next_action = str(
            trade_signal_audit.get("next_action")
            or goal_plan.get("recommended_action")
            or price_action_feedback.get("next_action")
            or trade_diagnostics.get("recommended_action")
            or "Keep collecting live bid/ask evidence until a validated cohort produces executable positive paper signals."
        )
        unlock_condition = "The next paper entry must pass model, cohort, liquidity, spread, and paper-bridge evidence gates."

    if not collect_now:
        collect_now = _compact_list(price_action_feedback.get("suppressed_queries"), limit=4)

    learning_verdict = primary_blocker
    if recent_exits:
        learning_verdict = (
            f"{recent_loss_exits}/{recent_exits} recent exits lost; use that as feedback before widening any cohort."
        )
    elif trusted_missing_fresh and collect_now:
        learning_verdict = "The current useful learning target is fresh executable bid/ask data for " + ", ".join(collect_now) + "."

    state_badges = [
        {"label": trade_decision, "severity": decision_class},
        {"label": f"{approved_signals} approved / {rejected_signals} rejected", "severity": "good" if approved_signals else "warn"},
        {"label": f"audited P&L {_usd_text(decision_pnl)}", "severity": "good" if decision_pnl >= 0 else "bad"},
        {
            "label": f"audited run-rate {_usd_text(decision_run_rate)} / {_usd_text(target_monthly)}",
            "severity": "good" if decision_run_rate is not None and decision_run_rate >= target_monthly and verified_evidence_ready else "warn",
        },
        {
            "label": f"proof {profit_target_proof_status}",
            "severity": "good" if verified_evidence_ready else "warn",
        },
    ]
    if collect_now:
        state_badges.append({"label": "collect: " + ", ".join(collect_now[:3]), "severity": "good"})

    priority_owner = "paper confirmation broker" if paper_confirmation_mode and approved_signals > 0 else "paper broker" if approved_signals > 0 else "research cycle"
    priority_actions = [
        {
            "title": next_action,
            "body": primary_blocker,
            "owner": priority_owner,
            "success_metric": unlock_condition,
        }
    ]
    if trusted_missing_fresh:
        priority_actions.append(
            {
                "title": "Find fresh executable confirmation rows",
                "body": "Search/collect live markets for the trusted confirmation queries; ignore stale closed rows and excluded 5-minute crypto cohorts.",
                "owner": "liquidity discovery + websocket",
                "success_metric": f"missing targets falls from {missing_targets} to 0 and paper-confirmation signals become > 0",
            }
        )
    if recent_exits:
        priority_actions.append(
            {
                "title": "Use recent losses as model feedback",
                "body": f"{recent_loss_exits} of {recent_exits} recent exits lost. Treat fixed-horizon losses as failed signals, not hidden variance.",
                "owner": "price-action model",
                "success_metric": "new cohort evidence improves after bid/ask spread and fixed-horizon exits",
            }
        )
    if approved_signals <= 0:
        priority_actions.append(
            {
                "title": "Keep paper/live gates closed",
                "body": "Do not loosen thresholds to create activity. The current task is evidence collection, not forced turnover.",
                "owner": "governance",
                "success_metric": "paper promotion only after positive forward cohort evidence",
            }
        )

    selected_model = validation_selected_for_decision
    rank_diagnostics = validation_rank_for_decision
    selected_roi = safe_float(selected_model.get("selected_roi"))
    selected_profit_factor = safe_float(selected_model.get("selected_profit_factor"))
    selected_trades = safe_float(selected_model.get("selected_trades"))
    current_historical_scan = (
        price_action_paper_signals.get("current_historical_analogue_scan")
        if isinstance(price_action_paper_signals.get("current_historical_analogue_scan"), dict)
        else {}
    )
    model_key_metric = (
        (
            f"selected 0 validation trades; train +{price_action_model.get('train_positive_targets', '-')}; "
            f"validation +{price_action_model.get('validation_positive_targets', '-')}; "
            f"best + rank {rank_diagnostics.get('best_positive_rank', 'n/a')}"
        )
        if model_threshold_blocked
        else
        f"selected {int(selected_trades or 0)}; ROI {selected_roi * 100:.2f}%; PF {selected_profit_factor:.2f}"
        if selected_roi is not None and selected_profit_factor is not None
        else (
            f"train +{price_action_model.get('train_positive_targets', '-')}; "
            f"validation +{price_action_model.get('validation_positive_targets', '-')}"
        )
    )
    evidence_lanes = [
        {
            "lane": "Paper trade gate",
            "state": trade_decision,
            "decision_use": "Determines whether the broker may take paper risk now.",
            "key_metric": f"{approved_signals} approved / {rejected_signals} rejected",
            "blocker_or_next": primary_blocker,
        },
        {
            "lane": "Price-action ML",
            "state": price_action_model.get("decision") or price_action_model.get("status") or "unknown",
            "decision_use": "Predicts whether buying at ask can later sell/mark at bid profitably.",
            "key_metric": model_key_metric,
            "blocker_or_next": rank_diagnostics.get("next_action")
            or price_action_model.get("validation_win_rate_gate_reason")
            or cohort_transfer.get("reason")
            or validation_gap.get("reason")
            or "No active model blocker reported.",
        },
        {
            "lane": "Forward paper P&L",
            "state": pnl_audit_state,
            "decision_use": "Measures entry-and-exit quote-supported paper progress toward the $100/month target; raw ledger P&L is diagnostic only.",
            "key_metric": f"verified {_usd_text(decision_pnl)}; run-rate {_usd_text(decision_run_rate)}",
            "blocker_or_next": (
                f"proof {profit_target_proof_status}; {evidence_round_trip_label} {evidence_round_trips}/{minimum_audited_round_trips}"
                + (f"; blockers {', '.join(profit_target_proof_blockers)}" if profit_target_proof_blockers else "")
            ),
        },
        {
            "lane": "Live bid/ask feed",
            "state": shadow_status or "unknown",
            "decision_use": "Feeds current executable prices and ask-to-future-bid repricing labels across liquid event families.",
            "key_metric": (
                f"{websocket_summary.get('new_messages', '-')} messages / "
                f"{websocket_feature_summary.get('feature_rows', '-')} features / "
                f"{len((websocket_summary.get('target_family_counts') or {}))} target families"
            ),
            "blocker_or_next": "Collect " + ", ".join(collect_now) if collect_now else "No priority collection query is active.",
        },
        {
            "lane": "Current historical analogue scan",
            "state": current_historical_scan.get("state") or "unknown",
            "decision_use": "Checks all current live bid/ask rows against historical buy-at-ask / sell-at-bid validation buckets.",
            "key_metric": (
                f"{current_historical_scan.get('current_rows', 0)} rows; "
                f"{current_historical_scan.get('positive_matches', 0)} positive matches"
            ),
            "blocker_or_next": current_historical_scan.get("next_action")
            or "Collect more current bid/ask variation across liquid families.",
        },
        {
            "lane": "Sharp-anchor alpha bridge",
            "state": alpha_bridge.get("status") or "unknown",
            "decision_use": alpha_bridge.get("decision_use")
            or "Measures whether independent bookmaker probabilities transfer into current executable candidates.",
            "key_metric": alpha_bridge.get("key_metric") or "-",
            "blocker_or_next": alpha_bridge.get("blocker_or_next") or "No sharp-anchor bridge status available.",
        },
        {
            "lane": "Sharp sports edge funnel",
            "state": sharp_sports_funnel.get("status") or "unknown",
            "decision_use": sharp_sports_funnel.get("decision_use")
            or "Shows whether sharp sports edge is blocked at collection, liquidity, anchors, scoring, or CLV.",
            "key_metric": (
                f"{sharp_sports_funnel.get('total_tradable_liquidity_rows', 0)} tradable; "
                f"{sharp_sports_funnel.get('total_anchor_rows', 0)} anchor rows; "
                f"{sharp_sports_funnel.get('total_scored_anchor_hits', 0)} scored hits; "
                f"{sharp_sports_funnel.get('total_final_clv_rows', 0)} final CLV"
            ),
            "blocker_or_next": sharp_sports_funnel.get("next_bottleneck")
            or "No sharp sports funnel status available.",
        },
        {
            "lane": "World Cup validation",
            "state": worldcup_validation.get("status") or "unknown",
            "decision_use": "Adds bookmaker/fundamental cross-checks where World Cup rows exist.",
            "key_metric": f"{worldcup_validation.get('approved_signals', 0)} approved World Cup signals",
            "blocker_or_next": worldcup_validation.get("main_blocker") or "No World Cup blocker reported.",
        },
        {
            "lane": "Best shadow repricing route",
            "state": price_action_goal.get("state") or goal_plan.get("status") or "unknown",
            "decision_use": "Shows the actual positive repricing evidence before any monthly extrapolation.",
            "key_metric": edge_route_text,
            "blocker_or_next": goal_plan.get("main_gap") or goal_plan.get("recommended_action") or "-",
        },
    ]

    audit_only_panels = [
        {
            "panel": "Legacy local live-loop heartbeat",
            "reason": "Historical status only; the current active lane is shadow research + websocket.",
        },
        {
            "panel": "Historical edge search",
            "reason": "Useful for provenance, but in-sample/historical rule tables do not authorise paper trades.",
        },
        {
            "panel": "Promotion readiness tables",
            "reason": "Audit evidence only unless the current cohort also has fresh governed paper signals.",
        },
        {
            "panel": "Raw liquidity discovery",
            "reason": "Shows opportunity coverage; decision impact comes only through current executable candidates and cohort evidence.",
        },
        {
            "panel": "Slow settlement watch",
            "reason": "Helpful for final outcome validation, but the current paper bridge is price-action bid/ask evidence.",
        },
    ]

    can_trade_now = decision_class == "good" and approved_signals > 0
    collect_text = ", ".join(collect_now) if collect_now else "No priority collection target is active."
    raw_pnl_text = f"raw ledger {_usd_text(actual_pnl)}" if audited_pnl is not None or proof_pnl is not None else ""
    profit_target_text = (
        f"{_usd_text(decision_pnl)} verified; {_usd_text(decision_run_rate)} verified monthly run-rate vs {_usd_text(target_monthly)} target"
        + f"; proof {profit_target_proof_status}; {evidence_round_trip_label} {evidence_round_trips}/{minimum_audited_round_trips}"
        + (f" ({raw_pnl_text})" if raw_pnl_text else "")
    )
    decision_questions = [
        {
            "question": "Can the bot paper trade now?",
            "answer": (
                "Yes - paper-confirmation probes exist; keep them evidence-only and monitor fills."
                if paper_confirmation_mode and can_trade_now
                else "Yes - governed paper signals exist; keep stake capped and monitor fills."
                if can_trade_now
                else f"No - {trade_decision}: {primary_blocker}"
            ),
            "decision_use": "This is the only top-line authorisation question for the broker lane.",
        },
        {
            "question": "What unlocks the next trade?",
            "answer": unlock_condition,
            "decision_use": "Prevents forcing trades by making the evidence requirement explicit.",
        },
        {
            "question": "What should the bot collect next?",
            "answer": collect_text,
            "decision_use": "Turns research focus into a concrete websocket/liquidity collection target.",
        },
        {
            "question": "Are we on pace for $100/month?",
            "answer": profit_target_text,
            "decision_use": "Measures quote-consistent paper evidence against the affordability objective, not raw ledger artefacts or historical backtest optimism.",
        },
        {
            "question": "Which edge route is being tested?",
            "answer": edge_route_text,
            "decision_use": "Separates promising shadow repricing from confirmed forward paper profit.",
        },
        {
            "question": "What did the last losses teach?",
            "answer": learning_verdict,
            "decision_use": "Keeps the model learning from failed exits instead of widening weak cohorts.",
        },
    ]
    decision_cards = [
        {
            "label": "Can paper trade now?",
            "value": (
                "Yes - paper-confirmation probes exist"
                if paper_confirmation_mode and can_trade_now
                else "Yes - governed paper signals exist"
                if can_trade_now
                else f"No - {trade_decision}"
            ),
            "severity": "good" if can_trade_now else decision_class,
            "decision_use": "Broker action is allowed only from approved paper or paper-confirmation signals.",
        },
        {
            "label": "Why / why not?",
            "value": primary_blocker,
            "severity": decision_class,
            "decision_use": "The main blocker must be removed before trusting new trades.",
        },
        {
            "label": "Unlock condition",
            "value": unlock_condition,
            "severity": "good" if can_trade_now else "warn",
            "decision_use": "The next measurable condition that changes the bot state.",
        },
        {
            "label": "Collect now",
            "value": collect_text,
            "severity": "good" if collect_now else "warn",
            "decision_use": "The next data collection target for the learning loop.",
        },
        {
            "label": "$100/month state",
            "value": profit_target_text,
            "severity": "good" if decision_run_rate is not None and decision_run_rate >= target_monthly and verified_evidence_ready else "warn",
            "decision_use": "Shows whether audited, quote-consistent paper trading is actually paying for the capital goal.",
        },
        {
            "label": "Edge route",
            "value": edge_route_text,
            "severity": "good" if paper_route_qualified else "warn",
            "decision_use": "Shadow edge is research; forward paper P&L is proof.",
        },
    ]

    return {
        "trade_decision": trade_decision,
        "decision_class": decision_class,
        "headline": headline,
        "operator_plain_english": (
            "Use this cockpit like a quant control panel: trade only if governed paper signals exist; "
            "otherwise collect the specific evidence named here and let losing cohorts stay suppressed."
        ),
        "primary_blocker": primary_blocker,
        "next_action": next_action,
        "unlock_condition": unlock_condition,
        "collect_now": collect_now,
        "frozen_updown_collection_queries_excluded": frozen_collect_exclusions,
        "learning_verdict": learning_verdict,
        "approved_signals": approved_signals,
        "rejected_signals": rejected_signals,
        "actual_pnl_since_baseline_usdc": actual_pnl,
        "audited_pnl_since_baseline_usdc": audited_pnl,
        "proof_verified_pnl_since_baseline_usdc": proof_pnl,
        "decision_pnl_usdc": decision_pnl,
        "decision_pnl_source": decision_pnl_source,
        "monthly_run_rate_usdc": run_rate,
        "decision_monthly_run_rate_usdc": decision_run_rate,
        "pnl_audit_state": pnl_audit_state,
        "profit_target_proof_status": profit_target_proof_status,
        "profit_target_proof_blockers": profit_target_proof_blockers,
        "verified_evidence_ready": verified_evidence_ready,
        "audited_round_trips_since_baseline": audited_round_trips,
        "proof_verified_round_trips_since_baseline": proof_round_trips,
        "proof_entry_snapshot_missing_round_trips": proof_entry_missing,
        "proof_exit_snapshot_missing_round_trips": proof_exit_missing,
        "minimum_audited_round_trips_for_on_pace": minimum_audited_round_trips,
        "target_monthly_profit_usdc": target_monthly,
        "best_repricing_monthly_run_rate_usdc": best_repricing_run_rate,
        "best_forward_paper_monthly_run_rate_usdc": forward_paper_run_rate,
        "model_threshold_blocked": model_threshold_blocked,
        "model_selected_validation_trades": selected_validation_trades_for_decision,
        "model_train_threshold_pass": chosen_train_metrics.get("train_threshold_pass"),
        "model_train_threshold_source": chosen_train_metrics.get("threshold_source"),
        "model_best_positive_validation_rank": validation_rank_for_decision.get("best_positive_rank"),
        "model_validation_blockers": _compact_list(validation_blockers_for_decision, limit=6),
        "state_badges": state_badges,
        "decision_cards": decision_cards,
        "decision_questions": decision_questions,
        "priority_actions": priority_actions[:4],
        "evidence_lanes": evidence_lanes,
        "audit_only_panels": audit_only_panels,
        "model_state": price_action_model.get("decision") or price_action_model.get("status"),
        "shadow_research_status": shadow_status,
    }


def _paper_maintenance_status(cfg: EngineConfig) -> dict[str, Any]:
    path = cfg.path.parent / "work" / "polymarket_paper_maintenance_latest_status.json"
    payload = _read_json_lenient(path, default={}) or {}
    if not isinstance(payload, dict) or not payload:
        return {
            "status": "not_started",
            "status_file": str(path),
            "age_seconds": None,
            "reason": "Lightweight paper maintenance has not written a status file yet.",
        }
    return {
        **payload,
        "status_file": str(path),
        "age_seconds": _age_seconds(payload),
        "paper_trade_refresh_status": (
            payload.get("paper_trade_refresh", {}).get("status")
            if isinstance(payload.get("paper_trade_refresh"), dict)
            else ""
        ),
    }


def _paper_maintenance_task_status(cfg: EngineConfig) -> dict[str, Any]:
    path = cfg.path.parent / "work" / "polymarket_paper_maintenance_task_status.json"
    payload = _read_json_lenient(path, default={}) or {}
    if not isinstance(payload, dict) or not payload:
        return {
            "status": "not_installed_or_unknown",
            "status_file": str(path),
            "reason": "No paper-maintenance scheduled-task status file has been written.",
        }
    return {
        **payload,
        "status_file": str(path),
        "age_seconds": _age_seconds(payload),
    }


def _algo_replay_status(cfg: EngineConfig) -> dict[str, Any]:
    root = cfg.output_root / "polymarket_algo"
    summaries: list[dict[str, Any]] = []
    if root.exists():
        for path in sorted(root.glob("replay_*_summary.json")):
            payload = read_json(path, default={}) or {}
            if not isinstance(payload, dict) or not payload:
                continue
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
            pnl = safe_float(payload.get("unrealised_mark_to_bid_pnl_usdc"))
            summaries.append(
                {
                    "strategy": payload.get("strategy") or path.stem.replace("replay_", "").replace("_summary", ""),
                    "status": payload.get("status"),
                    "generated_at_utc": payload.get("generated_at_utc") or mtime,
                    "events_processed": payload.get("events_processed"),
                    "intents_emitted": payload.get("intents_emitted"),
                    "fills": payload.get("fills"),
                    "cancels": payload.get("cancels"),
                    "expiries": payload.get("expiries"),
                    "resting_orders_at_end": payload.get("resting_orders_at_end"),
                    "total_cost_usdc": payload.get("total_cost_usdc"),
                    "unrealised_mark_to_bid_pnl_usdc": payload.get("unrealised_mark_to_bid_pnl_usdc"),
                    "paper_trading_invoked": bool(payload.get("paper_trading_invoked", False)),
                    "live_trading_invoked": bool(payload.get("live_trading_invoked", False)),
                    "artifact_path": str(path),
                    "_sort_pnl": pnl if pnl is not None else -999999.0,
                }
            )
    summaries.sort(
        key=lambda row: safe_float(row.get("_sort_pnl")) if safe_float(row.get("_sort_pnl")) is not None else -999999.0,
        reverse=True,
    )
    for row in summaries:
        row.pop("_sort_pnl", None)
    return {
        "status": "ok" if summaries else "missing",
        "strategy_count": len(summaries),
        "best_strategy": summaries[0] if summaries else {},
        "summaries": summaries,
        "paper_trading_invoked": any(bool(row.get("paper_trading_invoked")) for row in summaries),
        "live_trading_invoked": any(bool(row.get("live_trading_invoked")) for row in summaries),
    }


def _scoreboard_status(broker: dict[str, Any], target: dict[str, Any]) -> str:
    broker_equity = safe_float(broker.get("equity"))
    current = target.get("current") if isinstance(target, dict) else {}
    target_equity = safe_float(current.get("equity_usdc") if isinstance(current, dict) else None)
    if broker_equity is None or target_equity is None:
        return "missing_evidence"
    return "aligned" if abs(broker_equity - target_equity) <= 0.005 else "drift_detected"


def _broker_signal_freshness(
    price_action_signals: dict[str, Any],
    broker: dict[str, Any],
    paper_probe_exit_watch: dict[str, Any] | None = None,
    open_positions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Report whether current price-action paper signals have reached the broker.

    The broker summary can be perfectly valid but older than the latest signal
    file. When that happens, the dashboard must show pending broker work instead
    of implying the latest paper probes were already processed.
    """
    signal_count = int(safe_float(price_action_signals.get("signals")) or 0)
    confirmation_count = int(safe_float(price_action_signals.get("paper_confirmation_signals")) or 0)
    signal_rows = price_action_signals.get("top_signals") if isinstance(price_action_signals.get("top_signals"), list) else []
    position_rows = open_positions if isinstance(open_positions, list) else []
    open_position_keys = {
        (
            str(position.get("market_id") or "").strip(),
            str(position.get("token_id") or "").strip(),
            str(position.get("side") or "BUY_YES").strip() or "BUY_YES",
        )
        for position in position_rows
        if str(position.get("status") or "open").strip().lower() == "open"
        and (safe_float(position.get("quantity")) or 0.0) > 0
    }
    pending_signal_rows: list[dict[str, Any]] = []
    if signal_rows and len(signal_rows) >= signal_count:
        for signal in signal_rows[:signal_count]:
            key = (
                str(signal.get("market_id") or signal.get("market_slug") or "").strip(),
                str(signal.get("token_id") or "").strip(),
                str(signal.get("side") or "BUY_YES").strip() or "BUY_YES",
            )
            if key not in open_position_keys:
                pending_signal_rows.append(signal)
    signal_time = parse_timestamp(price_action_signals.get("generated_at_utc"))
    broker_time = parse_timestamp(broker.get("generated_at_utc"))
    timestamp_stale = bool(signal_count > 0 and (broker_time is None or (signal_time is not None and signal_time > broker_time)))
    if timestamp_stale and signal_rows and len(signal_rows) >= signal_count:
        stale = bool(pending_signal_rows)
    else:
        stale = timestamp_stale
    exit_watch = paper_probe_exit_watch if isinstance(paper_probe_exit_watch, dict) else {}
    due_exit_count = int(safe_float(exit_watch.get("fixed_horizon_due_count")) or 0)
    open_exit_probes = int(safe_float(exit_watch.get("open_confirmation_probes")) or 0)
    exit_refresh_needed = due_exit_count > 0
    stale_seconds = (
        max(0.0, (signal_time - broker_time).total_seconds())
        if stale and signal_time is not None and broker_time is not None
        else None
    )
    pending_signal_count = len(pending_signal_rows) if signal_rows and len(signal_rows) >= signal_count else signal_count
    pending_confirmation_count = (
        sum(
            1
            for signal in pending_signal_rows
            if signal.get("price_action_evidence_status") == "trusted_shadow_requires_broker_paper_confirmation"
            or signal.get("price_action_entry_source") == "paper_confirmation_candidate"
        )
        if signal_rows and len(signal_rows) >= signal_count
        else confirmation_count
    )
    if stale:
        reason = (
            "Price-action paper signals were generated after the latest broker run."
            if broker_time is not None
            else "Price-action paper signals exist but no broker run timestamp is available."
        )
    elif timestamp_stale and signal_count > 0 and signal_rows and len(signal_rows) >= signal_count:
        reason = "Latest signal file is newer than the broker run, but every current signal already has an open paper position."
    elif exit_refresh_needed:
        reason = "One or more open paper-confirmation probes reached their fixed exit horizon."
    else:
        reason = "Latest broker run is at least as fresh as the paper signal file."
    return {
        "broker_refresh_needed": stale or exit_refresh_needed,
        "broker_signal_refresh_needed": stale,
        "broker_exit_refresh_needed": exit_refresh_needed,
        "pending_broker_signals": pending_signal_count if stale else 0,
        "pending_broker_confirmation_signals": pending_confirmation_count if stale else 0,
        "pending_broker_exit_probes": due_exit_count,
        "open_broker_exit_probes": open_exit_probes,
        "broker_matched_open_signal_positions": max(0, signal_count - pending_signal_count)
        if signal_rows and len(signal_rows) >= signal_count
        else None,
        "next_broker_exit_due_utc": exit_watch.get("next_due_utc", ""),
        "next_broker_exit_due_minutes": exit_watch.get("next_due_minutes"),
        "signal_generated_at_utc": price_action_signals.get("generated_at_utc"),
        "broker_generated_at_utc": broker.get("generated_at_utc"),
        "broker_signal_stale_seconds": stale_seconds,
        "broker_refresh_reason": reason,
    }


def _sha_matches(expected: str, actual: str) -> bool:
    expected = expected.strip()
    actual = actual.strip()
    if not expected or not actual or expected == "unknown" or actual == "unknown":
        return False
    return expected.startswith(actual) or actual.startswith(expected)


def _local_git_head() -> str:
    repo_root = Path(__file__).resolve().parents[2]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception:
        return ""
    return result.stdout.strip()


def _deployment_health() -> dict[str, Any]:
    expected_sha = os.getenv("PM_VPS_DEPLOYED_SHA", "").strip()
    image_build_sha = os.getenv("PM_IMAGE_BUILD_SHA", "").strip()
    local_git_head = _local_git_head()
    runtime_sha = image_build_sha if image_build_sha and image_build_sha != "unknown" else local_git_head
    in_container = Path("/.dockerenv").exists() or bool(os.getenv("KUBERNETES_SERVICE_HOST"))
    odds_key_present = bool(os.getenv("THE_ODDS_API_KEY", "").strip())
    version_match = _sha_matches(expected_sha, runtime_sha) if expected_sha and runtime_sha else None
    blockers: list[str] = []

    if expected_sha:
        if not runtime_sha:
            blockers.append("runtime_sha_missing")
        elif not version_match:
            blockers.append("deployed_sha_mismatch")
    elif in_container:
        blockers.append("pm_vps_deployed_sha_missing")

    if not odds_key_present:
        blockers.append("the_odds_api_key_missing")

    if expected_sha and not blockers:
        status = "ok"
        summary = "VPS deployment SHA, dashboard code, and odds API key are aligned."
    elif expected_sha:
        status = "needs_attention"
        summary = "VPS deployment metadata is present, but one or more runtime checks need attention."
    elif in_container:
        status = "container_unversioned"
        summary = "Running inside a container without PM_VPS_DEPLOYED_SHA, so stale-code checks cannot verify the deployed commit."
    else:
        status = "local_development"
        summary = "Local development render; deployment SHA checks are not expected."

    return {
        "status": status,
        "summary": summary,
        "blockers": blockers,
        "expected_deploy_sha": expected_sha,
        "image_build_sha": image_build_sha,
        "local_git_head": local_git_head,
        "dashboard_code_version": runtime_sha or "unknown",
        "version_match": version_match,
        "the_odds_api_key_present": odds_key_present,
        "runtime": "container" if in_container else "local",
        "manual_deploy_workflow": "deploy-polymarket-vps-paper.yml",
        "dashboard_markers": {
            "proof_status": True,
            "evidence_funnel": True,
            "profit_target_proof_status": True,
        },
    }


def _trim_dashboard_payload_value(
    value: Any,
    *,
    path: tuple[str, ...],
    limits: list[dict[str, Any]],
    max_list_items: int = DASHBOARD_PAYLOAD_LIST_LIMIT,
) -> Any:
    """Return a dashboard-sized copy while leaving source artifacts complete."""
    if isinstance(value, list):
        original_items = len(value)
        dashboard_items = min(original_items, max_list_items)
        if original_items > max_list_items:
            limits.append(
                {
                    "path": ".".join(path) or "$",
                    "original_items": original_items,
                    "dashboard_items": dashboard_items,
                }
            )
        return [
            _trim_dashboard_payload_value(
                item,
                path=path + ("[]",),
                limits=limits,
                max_list_items=max_list_items,
            )
            for item in value[:dashboard_items]
        ]
    if isinstance(value, dict):
        return {
            str(key): _trim_dashboard_payload_value(
                nested,
                path=path + (str(key),),
                limits=limits,
                max_list_items=max_list_items,
            )
            for key, nested in value.items()
        }
    return value


def _write_dashboard_data(path: Path, payload: dict[str, Any]) -> Path:
    limits: list[dict[str, Any]] = []
    trimmed = _trim_dashboard_payload_value(payload, path=(), limits=limits)
    if isinstance(trimmed, dict):
        trimmed["dashboard_payload_limits"] = {
            "max_list_items": DASHBOARD_PAYLOAD_LIST_LIMIT,
            "truncated_list_count": len(limits),
            "truncated_lists": limits[:100],
            "source_artifacts_complete": True,
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temp_path.open("w", encoding="utf-8") as f:
        json.dump(trimmed, f, sort_keys=True, separators=(",", ":"), default=serialize_value)
    temp_path.replace(path)
    return path


def render_dashboard(cfg: EngineConfig, latest_report: dict[str, Any] | None = None) -> dict[str, Any]:
    out = cfg.output_root / "polymarket_dashboard"
    out.mkdir(parents=True, exist_ok=True)
    governance = cfg.governance_root
    predictions_root = cfg.output_root / "polymarket_predictions"
    portfolio_root = cfg.output_root / "polymarket_portfolio"
    shadow_root = cfg.output_root / "polymarket_shadow"

    paper_trade_refresh = read_json(governance / "paper_trade_refresh.json", default={}) or {}
    if not isinstance(paper_trade_refresh, dict):
        paper_trade_refresh = {}
    forward = latest_report or read_json(governance / "forward_paper_cycle.json", default={}) or {}
    scanner_heartbeat = read_json(governance / "live_paper_loop_heartbeat.json", default={}) or {}
    local_live_heartbeat = read_json(governance / "local_live_loop_heartbeat.json", default={}) or {}
    heartbeat = local_live_heartbeat or scanner_heartbeat
    paper_summary = read_json(portfolio_root / "paper_trading_summary.json", default={}) or {}
    target_source, actual_target = _freshest_payload(
        [
            ("latest_report", forward.get("actual_profit_target") if isinstance(forward, dict) else {}),
            ("paper_trade_refresh", paper_trade_refresh.get("actual_profit_target") if isinstance(paper_trade_refresh, dict) else {}),
            ("paper_profit_target_tracker", read_json(governance / "paper_profit_target_tracker.json", default={}) or {}),
        ]
    )
    broker_source, broker_summary = _freshest_payload(
        [
            ("latest_report", forward.get("broker") if isinstance(forward, dict) else {}),
            ("paper_trade_refresh", paper_trade_refresh.get("broker") if isinstance(paper_trade_refresh, dict) else {}),
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
    shadow_position_rows = read_csv_rows(shadow_root / "shadow_positions.csv")
    shadow_positions = _enrich_shadow_rows(_open_positions(shadow_position_rows))
    shadow_fills = _last(read_csv_rows(shadow_root / "shadow_fills.csv"), 50)
    orders = read_csv_rows(portfolio_root / "paper_orders.csv")
    paper_probe_exit_watch = _paper_probe_exit_watch(positions, orders)
    predictions = read_csv_rows(predictions_root / "predictions.csv")
    signals = read_csv_rows(predictions_root / "trade_signals.csv")
    price_action_signals = read_csv_rows(cfg.output_root / "polymarket_price_action" / "price_action_paper_signals.csv")
    if price_action_signals:
        signals = signals + price_action_signals
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
    mispricing_alpha_summary = read_json(governance / "mispricing_alpha_live_summary.json", default={}) or {}
    if not isinstance(mispricing_alpha_summary, dict):
        mispricing_alpha_summary = {}
    independent_anchor_status = _independent_anchor_status(governance)
    alpha_bridge = _mispricing_alpha_bridge_status(
        alpha_summary=mispricing_alpha_summary,
        independent_anchor_status=independent_anchor_status,
        predictions=predictions,
        rejected=rejected,
    )
    live_loop_status = _live_loop_status(heartbeat if isinstance(heartbeat, dict) else {}, cfg)
    legacy_full_cycle_status = _legacy_full_cycle_status(heartbeat if isinstance(heartbeat, dict) else {}, live_loop_status)
    strategy_v2_status = _strategy_v2_status(cfg, legacy_full_cycle_status)
    strategy_v2_runtime = _strategy_v2_runtime_freshness(strategy_v2_status, live_loop_status)
    price_action_scout_status = _price_action_scout_status(cfg)
    price_action_microstructure_status = _price_action_microstructure_status(cfg)
    price_action_model_status = _price_action_model_status(cfg)
    price_action_paper_signal_status = _price_action_paper_signal_status(cfg)
    governance_refresh_status = read_json(governance / "governance_refresh_status.json", default={}) or {}
    if not isinstance(governance_refresh_status, dict):
        governance_refresh_status = {}
    broker_signal_freshness = _broker_signal_freshness(
        price_action_paper_signal_status,
        broker_summary,
        paper_probe_exit_watch,
        positions,
    )
    price_action_paper_signal_status = {**price_action_paper_signal_status, **broker_signal_freshness}
    price_action_feedback = read_json(governance / "price_action_feedback.json", default={}) or {}
    if not isinstance(price_action_feedback, dict):
        price_action_feedback = {}
    paper_round_trip_summary = read_json(cfg.output_root / "polymarket_price_action" / "paper_broker_round_trip_summary.json", default={}) or {}
    if not isinstance(paper_round_trip_summary, dict):
        paper_round_trip_summary = {}
    algo_replay = _algo_replay_status(cfg)
    algo_sweep = read_json(cfg.output_root / "polymarket_algo" / "algo_sweep_summary.json", default={}) or {}
    if not isinstance(algo_sweep, dict):
        algo_sweep = {}
    quant_research_status = read_json(governance / "quant_research_status.json", default={}) or {}
    if not isinstance(quant_research_status, dict):
        quant_research_status = {}
    closing_line_value = read_json(governance / "closing_line_value.json", default={}) or {}
    if not isinstance(closing_line_value, dict):
        closing_line_value = {}
    smart_flow_clv_legacy = read_json(governance / "smart_flow_clv.json", default={}) or {}
    if not isinstance(smart_flow_clv_legacy, dict):
        smart_flow_clv_legacy = {}
    h3_smart_flow = read_json(cfg.output_root / "h3_smart_flow" / "h3_evaluation.json", default={}) or {}
    if not isinstance(h3_smart_flow, dict):
        h3_smart_flow = {}
    if h3_smart_flow:
        h3_smart_flow = {
            **h3_smart_flow,
            "cohorts": read_csv_rows(cfg.output_root / "h3_smart_flow" / "h3_cohorts.csv"),
        }
    h2_dutch = read_json(cfg.output_root / "h2_dutch" / "h2_evaluation.json", default={}) or {}
    if not isinstance(h2_dutch, dict):
        h2_dutch = {}
    if h2_dutch:
        h2_dutch = {
            **h2_dutch,
            "episode_preview": read_csv_rows(
                cfg.output_root / "h2_dutch" / "h2_episode_state.csv"
            )[-20:],
        }
    dutch_arb = read_json(cfg.output_root / "polymarket_arbitrage" / "dutch_arb_monitor_summary.json", default={}) or {}
    if not isinstance(dutch_arb, dict):
        dutch_arb = {}
    if not dutch_arb and isinstance(heartbeat, dict) and isinstance(heartbeat.get("dutch_arb"), dict):
        dutch_arb = {
            **heartbeat["dutch_arb"],
            "source": "live_loop_heartbeat",
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        }
    longshot_bias = read_json(cfg.output_root / "polymarket_longshot_bias" / "longshot_bias_summary.json", default={}) or {}
    if not isinstance(longshot_bias, dict):
        longshot_bias = {}
    if not longshot_bias and isinstance(forward, dict) and isinstance(forward.get("longshot_bias"), dict):
        longshot_bias = {
            **forward["longshot_bias"],
            "source": "forward_paper_cycle",
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        }
    performance_factsheet = read_json(cfg.output_root / "performance" / "performance_factsheet.json", default={}) or {}
    if not isinstance(performance_factsheet, dict):
        performance_factsheet = {}
    operating_state = read_json(cfg.output_root / "performance" / "operating_state.json", default={}) or {}
    if not isinstance(operating_state, dict):
        operating_state = {}
    executor_status = read_json(cfg.output_root / "execution" / "executor_status.json", default={}) or {}
    if not isinstance(executor_status, dict):
        executor_status = {}
    degraded_state_watchdog = read_json(
        cfg.output_root / "ops_scheduler" / "degraded_state_watchdog.json", default={}
    ) or {}
    if not isinstance(degraded_state_watchdog, dict):
        degraded_state_watchdog = {}
    deploy_acceptance = read_json(
        cfg.output_root / "ops_scheduler" / "deploy_acceptance.json", default={}
    ) or {}
    if not isinstance(deploy_acceptance, dict):
        deploy_acceptance = {}
    edge_attribution = read_json(governance / "edge_attribution.json", default={}) or {}
    if not isinstance(edge_attribution, dict):
        edge_attribution = {}
    family_calibration = read_json(governance / "family_calibration_scorecard.json", default={}) or {}
    if not isinstance(family_calibration, dict):
        family_calibration = {}
    collection_coverage = read_json(governance / "collection_coverage.json", default={}) or {}
    if not isinstance(collection_coverage, dict):
        collection_coverage = {}
    evidence_history_status = read_json(governance / "evidence_history_status.json", default={}) or {}
    if not isinstance(evidence_history_status, dict):
        evidence_history_status = {}
    evidence_history_rows = read_csv_rows(governance / "evidence_history.csv")
    promotion_gate = read_json(governance / "promotion_gate.json", default={}) or {}
    if not isinstance(promotion_gate, dict):
        promotion_gate = {}
    risk_state = read_json(portfolio_root / "risk_state.json", default={}) or {}
    if not isinstance(risk_state, dict):
        risk_state = {}
    websocket_summary = read_json(cfg.output_root / "polymarket_websocket" / "websocket_summary.json", default={}) or {}
    if not isinstance(websocket_summary, dict):
        websocket_summary = {}
    websocket_feature_summary = read_json(governance / "websocket_feature_summary.json", default={}) or {}
    if not isinstance(websocket_feature_summary, dict):
        websocket_feature_summary = {}
    research_focus = read_json(governance / "research_focus.json", default={}) or {}
    if not isinstance(research_focus, dict):
        research_focus = {}
    goal_plan = read_json(governance / "paper_profit_goal_plan.json", default={}) or {}
    if not isinstance(goal_plan, dict):
        goal_plan = {}
    trade_signal_audit = read_json(governance / "trade_signal_audit.json", default={}) or {}
    if not isinstance(trade_signal_audit, dict):
        trade_signal_audit = {}
    paper_maintenance = _paper_maintenance_status(cfg)
    paper_maintenance_task = _paper_maintenance_task_status(cfg)
    shadow_research_cycle = _shadow_research_cycle_status(cfg)
    deployment_health = _deployment_health()
    evidence_freshness = {
        "broker_source": broker_source,
        "broker_generated_at_utc": broker_summary.get("generated_at_utc"),
        "target_source": target_source,
        "target_generated_at_utc": actual_target.get("generated_at_utc"),
        "live_loop_status": live_loop_status,
        "live_heartbeat_age_seconds": _age_seconds(heartbeat),
        "legacy_full_cycle": legacy_full_cycle_status,
        "scoreboard_status": _scoreboard_status(broker_summary, actual_target),
        "broker_refresh_needed": broker_signal_freshness.get("broker_refresh_needed"),
        "broker_signal_refresh_needed": broker_signal_freshness.get("broker_signal_refresh_needed"),
        "broker_exit_refresh_needed": broker_signal_freshness.get("broker_exit_refresh_needed"),
        "pending_broker_signals": broker_signal_freshness.get("pending_broker_signals"),
        "pending_broker_confirmation_signals": broker_signal_freshness.get("pending_broker_confirmation_signals"),
        "pending_broker_exit_probes": broker_signal_freshness.get("pending_broker_exit_probes"),
        "next_broker_exit_due_utc": broker_signal_freshness.get("next_broker_exit_due_utc"),
        "next_broker_exit_due_minutes": broker_signal_freshness.get("next_broker_exit_due_minutes"),
        "stale_open_position_count": broker_summary.get("stale_open_position_count"),
        "stale_open_positions": broker_summary.get("stale_open_positions", []),
        **strategy_v2_runtime,
    }
    trade_diagnostics = _trade_diagnostics(
        cfg,
        predictions=predictions,
        approved_signals=signals,
        rejected=rejected,
        near_miss_candidates=near_miss_candidates,
        shadow_summary=shadow_summary,
    )
    oversight_status = _dashboard_oversight_status(
        live_loop_status=live_loop_status,
        heartbeat=heartbeat if isinstance(heartbeat, dict) else {},
        shadow_research=shadow_research_cycle,
        governance_refresh=governance_refresh_status,
        price_action_model=price_action_model_status,
        price_action_paper_signals=price_action_paper_signal_status,
        goal_plan=goal_plan,
        trade_diagnostics=trade_diagnostics,
        evidence_freshness=evidence_freshness,
        strategy_v2=strategy_v2_status,
        dutch_arb=dutch_arb,
    )
    worldcup_validation_status = _worldcup_validation_status(
        predictions=predictions,
        approved_signals=signals,
        rejected=rejected,
    )
    evidence_funnel = _evidence_funnel_payload(
        liquidity_discovery=liquidity_discovery if isinstance(liquidity_discovery, dict) else {},
        predictions=predictions,
        shadow_position_rows=shadow_position_rows,
        closing_line_value=closing_line_value,
        edge_attribution=edge_attribution,
        family_calibration=family_calibration,
        collection_coverage=collection_coverage,
        algo_sweep=algo_sweep,
        promotion_gate=promotion_gate,
        evidence_history_rows=evidence_history_rows,
    )
    sharp_sports_funnel = _sharp_sports_funnel_payload(
        research_focus=research_focus,
        liquidity_discovery=liquidity_discovery if isinstance(liquidity_discovery, dict) else {},
        independent_anchor_status=independent_anchor_status,
        alpha_bridge=alpha_bridge,
        predictions=predictions,
        closing_line_value=closing_line_value,
    )
    decision_useful_summary = _decision_useful_summary(
        actual_target=actual_target,
        broker_summary=broker_summary,
        shadow_research=shadow_research_cycle,
        price_action_model=price_action_model_status,
        price_action_paper_signals=price_action_paper_signal_status,
        goal_plan=goal_plan,
        trade_signal_audit=trade_signal_audit,
        trade_diagnostics=trade_diagnostics,
        oversight_status=oversight_status,
        evidence_freshness=evidence_freshness,
        price_action_feedback=price_action_feedback,
        research_focus=research_focus,
        websocket_summary=websocket_summary,
        websocket_feature_summary=websocket_feature_summary,
        worldcup_validation=worldcup_validation_status,
        alpha_bridge=alpha_bridge,
        sharp_sports_funnel=sharp_sports_funnel,
    )

    payload = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "deployment_health": deployment_health,
        "governance_refresh_status": governance_refresh_status,
        "decision_useful_summary": decision_useful_summary,
        "forward_paper_cycle": forward,
        "paper_trade_refresh": paper_trade_refresh,
        "paper_broker_summary": broker_summary,
        "paper_trading_account": _paper_trading_account_payload(broker_source, broker_summary, forward),
        "heartbeat": heartbeat,
        "local_live_heartbeat": local_live_heartbeat,
        "scanner_heartbeat": scanner_heartbeat,
        "actual_profit_target": actual_target,
        "evidence_freshness": evidence_freshness,
        "oversight_status": oversight_status,
        "signal_cohort_pnl": signal_cohort_pnl,
        "cohort_promotion_readiness": _cohort_promotion_readiness(cfg, signal_cohort_pnl),
        "shadow_signal_cohort_pnl": shadow_summary,
        "shadow_settlement_watch": _shadow_settlement_watch(shadow_positions, shadow_summary),
        "independent_anchor_status": independent_anchor_status,
        "sharp_fetch_health": _sharp_fetch_health(independent_anchor_status),
        "strategy_v2": strategy_v2_status,
        "price_action_scout": price_action_scout_status,
        "price_action_microstructure": price_action_microstructure_status,
        "price_action_model": price_action_model_status,
        "price_action_paper_signals": price_action_paper_signal_status,
        "price_action_feedback": price_action_feedback,
        "paper_round_trip_summary": paper_round_trip_summary,
        "algo_replay": algo_replay,
        "algo_sweep": algo_sweep,
        "quant_research_status": quant_research_status,
        "mispricing_alpha_summary": mispricing_alpha_summary,
        "mispricing_alpha_bridge": alpha_bridge,
        "closing_line_value": closing_line_value,
        "h3_smart_flow": h3_smart_flow,
        "smart_flow_clv_legacy": smart_flow_clv_legacy,
        "h2_dutch": h2_dutch,
        "dutch_arb": dutch_arb,
        "longshot_bias": longshot_bias,
        "performance_factsheet": performance_factsheet,
        "operating_state": operating_state,
        "executor_status": executor_status,
        "degraded_state_watchdog": degraded_state_watchdog,
        "deploy_acceptance": deploy_acceptance,
        "edge_attribution": edge_attribution,
        "family_calibration": family_calibration,
        "collection_coverage": collection_coverage,
        "evidence_history_status": evidence_history_status,
        "evidence_history": evidence_history_rows[-50:],
        "promotion_gate": promotion_gate,
        "evidence_funnel": evidence_funnel,
        "sharp_sports_funnel": sharp_sports_funnel,
        "risk_state": risk_state,
        "websocket_summary": websocket_summary,
        "websocket_feature_summary": websocket_feature_summary,
        "research_focus": research_focus,
        "paper_profit_goal_plan": goal_plan,
        "trade_signal_audit": trade_signal_audit,
        "paper_probe_exit_watch": paper_probe_exit_watch,
        "paper_maintenance": paper_maintenance,
        "paper_maintenance_task": paper_maintenance_task,
        "shadow_research_cycle": shadow_research_cycle,
        "edge_strategy_search": edge_strategy_search,
        "promoted_rule_shadow": promoted_rule_shadow,
        "liquidity_discovery": liquidity_discovery,
        "positions": positions,
        "recent_fills": fills,
        "shadow_positions": shadow_positions,
        "shadow_fills": shadow_fills,
        "approved_signals": signals[:50],
        "rejection_counts": _rejection_counts(rejected),
        "worldcup_validation_status": worldcup_validation_status,
        "trade_diagnostics": trade_diagnostics,
    }
    # Attach the four proof questions to the payload and HTML BEFORE the single compact
    # write, so EVERY render path (VPS live loop, paper cycle, CLI) exposes them while the
    # payload stays single-line and capped. Previously the overlay only ran inside
    # refresh_governance (and re-wrote the file pretty-printed); the live loop's plain
    # render then clobbered it, which is exactly what the scheduled proof-health check
    # flags. The overlay module imports config/utils only, so there is no import cycle.
    from .dashboard_proof_questions import build_proof_questions, _inject_html_overlay

    proof_questions = build_proof_questions(cfg, dashboard_data=payload)
    payload["proof_questions"] = proof_questions
    # The pre-registered $100/month verdict recomputes on every render path for the
    # same clobber-proofing reason as proof_questions above: whichever process
    # renders last must carry the current verdict, not erase it.
    from .profit_verdict import build_profit_verdict

    payload["profit_verdict"] = build_profit_verdict(cfg)
    # Maker-lane tiles (WO-36): read the latest study + live-test scoreboard
    # artifacts if the VPS jobs have produced them; absent files render as an
    # inert "not run yet" panel. Reporting only.
    payload["maker_lane"] = {
        "study": read_json(cfg.output_root / "maker_carry" / "maker_carry_study.json", default={}) or {},
        "live_test": read_json(cfg.output_root / "maker_carry" / "maker_live_test.json", default={}) or {},
        "decision_policy": read_json(cfg.output_root / "maker_carry" / "decision_policy.json", default={}) or {},
        "requote_alerts": read_json(cfg.output_root / "maker_carry" / "requote_alerts.json", default={}) or {},
        "fill_replay": read_json(cfg.output_root / "maker_carry" / "maker_fill_replay.json", default={}) or {},
        # WO-99 stage-ticket eligibility, so the eligible/not_eligible signal is
        # visible in the dashboard data even on VPSes with no ntfy topic
        # configured (the push channel is otherwise the only surfacing path).
        "stage_ticket_eligibility": read_json(
            cfg.output_root / "maker_carry" / "stage_ticket_eligibility.json", default={}
        ) or {},
    }
    html_out = _inject_html_overlay(HTML)
    _write_dashboard_data(out / "dashboard_data.json", payload)
    (out / "index.html").write_text(html_out, encoding="utf-8")
    # The governance proof_questions.json artifact is written here rather than in a separate
    # post-render step, so it always matches the served dashboard on every render path and
    # refresh_governance no longer needs a second apply pass (which re-wrote the payload
    # pretty-printed, breaking the compact/capped invariant above).
    write_json(cfg.governance_root / "proof_questions.json", proof_questions)
    return {
        "status": "ok",
        "dashboard_dir": str(out),
        "dashboard_file": str(out / "index.html"),
        "dashboard_data": str(out / "dashboard_data.json"),
        "proof_questions_status": proof_questions.get("status"),
        "proof_questions_html_status": "overlay_written",
    }
