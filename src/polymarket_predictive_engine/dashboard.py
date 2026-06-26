from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from .config import EngineConfig
from .utils import now_utc, parse_timestamp, read_csv_rows, read_json, safe_float, write_json


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
    const broker = data.forward_paper_cycle?.broker || paper.broker || {};
    const target = data.actual_profit_target || data.forward_paper_cycle?.actual_profit_target || {};
    const monthly = data.forward_paper_cycle?.monthly_profit_target || {};
    const diag = data.trade_diagnostics || {};
    const live = data.local_live_heartbeat || data.heartbeat || {};
    const scanner = data.scanner_heartbeat || {};
    const discovery = live.discovery || {};
    const currentScan = discovery.scan || {};
    const lastScan = discovery.last_scan || {};
    const fastUpdown = discovery.last_fast_updown || discovery.fast_updown || {};
    const websocket = live.websocket || {};
    const websocketFeatures = live.websocket_features || {};
    const ingest = live.ingest || {};
    const status = target.status || data.forward_paper_cycle?.status || "unknown";
    const good = status === "target_reached" || status === "on_pace";
    const bad = status === "not_on_pace" || status === "missing_equity";
    document.getElementById("statusDot").className = "dot " + (good ? "good" : bad ? "bad" : "");
    document.getElementById("statusText").textContent = status + " - live tick " + (live.iteration || "-") + " - updated " + (data.generated_at_utc || "-");
    const pnl = Number(target.actual_pnl_since_baseline_usdc || 0);
    document.getElementById("cards").innerHTML = [
      card("Equity", fmtUsd(broker.equity), Number(broker.equity) >= 1000 ? "good" : "bad"),
      card("Actual P&L since clean baseline", fmtUsd(pnl), pnl >= 0 ? "good" : "bad"),
      card("Monthly run-rate", target.monthly_run_rate_usdc == null ? "Collecting" : fmtUsd(target.monthly_run_rate_usdc), target.monthly_run_rate_usdc >= target.target_monthly_profit_usdc ? "good" : ""),
      card("Live WS messages", websocket.new_messages ?? "-", "good"),
      card("Live WS features", websocketFeatures.feature_rows ?? "-", "good"),
      card("Ledger snapshots", ingest.inserted_market_snapshots ?? "-", "good"),
      card("Scanning now", joinText(currentScan.scan_plan?.selected_queries || lastScan.scan_plan?.selected_queries || scanner.scan?.scan_plan?.selected_queries || scanner.scan?.queries), "warn"),
      card("Exposure", fmtUsd(broker.total_exposure)),
      card("Cash", fmtUsd(broker.cash)),
      card("Buy fills / cycle", broker.buy_orders_filled ?? broker.orders_filled ?? "0"),
      card("Exit fills / cycle", broker.exit_orders_filled ?? "0"),
      card("Signals approved", data.forward_paper_cycle?.signals_approved ?? "0"),
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
    const anchors = data.heartbeat?.independent_fundamentals || data.independent_anchor_status || {};
    document.getElementById("independentFundamentals").innerHTML = table([
      { anchor: "Sharp odds fetch", ...(anchors.sharp_odds_fetch || {}) },
      { anchor: "Sharp de-vig anchor", ...(anchors.sharp_anchor || {}) },
      { anchor: "Deribit crypto fundamental", ...(anchors.crypto_fundamental || {}) }
    ], [
      ["Anchor","anchor"],
      ["Status","status"],
      ["Rows","rows", (v,row)=>v ?? row.fundamental_rows ?? row.rows_in ?? "-"],
      ["Markets","markets"],
      ["Output","output_file", v=>longText(v)],
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


def _trade_diagnostics(
    *,
    predictions: list[dict[str, Any]],
    approved_signals: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    shadow_summary: dict[str, Any],
) -> dict[str, Any]:
    shadow_candidates = [row for row in predictions if _truthy(row.get("shadow_trade_candidate"))]
    shadow_candidates.sort(
        key=lambda row: safe_float(row.get("shadow_priority_score")) or 0.0,
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
        "shadow_candidates_seen": shadow_summary.get("shadow_candidates_seen"),
        "shadow_opened_this_cycle": shadow_summary.get("opened_this_cycle"),
        "shadow_open_positions": shadow_summary.get("open_positions"),
        "quarantined_cohort_count": len(quarantined),
        "top_rejection_reasons": top_reasons,
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
    crypto = read_json(governance / "crypto_fundamental_summary.json", default={}) or {}
    components = {
        "sharp_odds_fetch": sharp_fetch if isinstance(sharp_fetch, dict) else {},
        "sharp_anchor": sharp_anchor if isinstance(sharp_anchor, dict) else {},
        "crypto_fundamental": crypto if isinstance(crypto, dict) else {},
    }
    any_present = any(bool(component) for component in components.values())
    any_usable = any(
        str(component.get("status") or "").lower() in {"fetched", "built"}
        and int(safe_float(component.get("rows") or component.get("fundamental_rows")) or 0) > 0
        for component in components.values()
    )
    return {
        "status": "usable" if any_usable else "setup_needed" if any_present else "missing",
        **components,
    }


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
    actual_target = forward.get("actual_profit_target") or read_json(
        governance / "paper_profit_target_tracker.json", default={}
    ) or {}
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

    payload = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "forward_paper_cycle": forward,
        "heartbeat": heartbeat,
        "local_live_heartbeat": local_live_heartbeat,
        "scanner_heartbeat": scanner_heartbeat,
        "actual_profit_target": actual_target,
        "signal_cohort_pnl": signal_cohort_pnl,
        "cohort_promotion_readiness": _cohort_promotion_readiness(cfg, signal_cohort_pnl),
        "shadow_signal_cohort_pnl": shadow_summary,
        "shadow_settlement_watch": _shadow_settlement_watch(shadow_positions, shadow_summary),
        "independent_anchor_status": _independent_anchor_status(governance),
        "edge_strategy_search": edge_strategy_search,
        "promoted_rule_shadow": promoted_rule_shadow,
        "liquidity_discovery": liquidity_discovery,
        "positions": positions,
        "recent_fills": fills,
        "shadow_positions": shadow_positions,
        "shadow_fills": shadow_fills,
        "approved_signals": signals[:50],
        "rejection_counts": _rejection_counts(rejected),
        "trade_diagnostics": _trade_diagnostics(
            predictions=predictions,
            approved_signals=signals,
            rejected=rejected,
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
