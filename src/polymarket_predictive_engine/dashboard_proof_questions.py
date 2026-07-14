from __future__ import annotations

import argparse
import json
import re
from typing import Any, Mapping

from .config import EngineConfig, load_config
from .utils import now_utc, read_json, safe_float, write_json

OVERLAY_START = "<!-- proof-questions-overlay:start -->"
OVERLAY_END = "<!-- proof-questions-overlay:end -->"
PROOF_SECTION = (
    '  <section class="primary"><h2>Today\'s decisions</h2><div id="decisionSummary"></div></section>\n'
    '  <section><h2>The $100/month verdict</h2><div id="profitVerdict"></div></section>\n'
    '  <section><h2>Maker lane (WO-36)</h2><div id="makerLane"></div></section>\n'
    '  <section><h2>Four proof questions</h2><div id="proofQuestions"></div></section>'
)

# Sections that stay expanded in focus mode; everything else collapses into a
# click-to-open <details> block (content preserved for audits, out of the way
# for decisions). Matched on lower-cased <h2> text.
FOCUS_KEEP_TITLES = (
    "today's decisions",
    "the $100/month verdict",
    "maker lane (wo-36)",
    "four proof questions",
)


def _int(value: Any, default: int = 0) -> int:
    parsed = safe_float(value)
    return default if parsed is None else int(parsed)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _status_from_questions(questions: list[dict[str, Any]]) -> str:
    severities = {str(row.get("severity") or "unknown") for row in questions}
    if "bad" in severities:
        return "bad"
    if "warn" in severities or "unknown" in severities:
        return "warn"
    return "good"


def build_proof_questions(
    cfg: EngineConfig,
    *,
    dashboard_data: Mapping[str, Any] | None = None,
    sharp_anchor_coverage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the four-question proof contract for the dashboard.

    This is display/reporting only. It consolidates proof streams into the questions that decide
    whether the bot is actually closer to the $100/month target. It never changes gates, stake caps,
    paper mode, or live execution settings.
    """

    data = dict(dashboard_data or read_json(cfg.output_root / "polymarket_dashboard" / "dashboard_data.json", default={}) or {})
    coverage = dict(sharp_anchor_coverage or read_json(cfg.governance_root / "sharp_anchor_coverage.json", default={}) or {})
    sharp = _dict(data.get("sharp_sports_funnel"))
    alpha_bridge = _dict(data.get("mispricing_alpha_bridge"))
    dutch = _dict(data.get("dutch_arb"))
    closing_line = _dict(data.get("closing_line_value"))
    focus_view = _dict(closing_line.get("focus_view"))
    decision = _dict(data.get("decision_useful_summary"))
    goal_plan = _dict(data.get("paper_profit_goal_plan"))
    target = _dict(data.get("actual_profit_target"))

    mapped_rows = _int(coverage.get("total_rows_mapped"), _int(sharp.get("total_anchor_rows")))
    fetched_rows = _int(coverage.get("total_rows_fetched"))
    scored_hits = _int(sharp.get("total_scored_anchor_hits"), _int(alpha_bridge.get("fundamental_probability_hits")))
    flagged_zero_join = _int(coverage.get("flagged_no_mappable_market_count"))
    if mapped_rows > 0 and scored_hits > 0:
        sharp_severity = "good"
        sharp_answer = f"Yes: {mapped_rows} sharp-anchor row(s) mapped and {scored_hits} reached current scoring."
    elif mapped_rows > 0:
        sharp_severity = "warn"
        sharp_answer = f"Partly: {mapped_rows} row(s) mapped, but current scoring has {scored_hits} overlap hits. Refresh/broaden discovery."
    elif fetched_rows > 0:
        sharp_severity = "bad" if flagged_zero_join else "warn"
        sharp_answer = f"No: {fetched_rows} fetched row(s) produced zero mapped token rows. Flagged zero-join markets: {flagged_zero_join}."
    else:
        sharp_severity = "warn"
        sharp_answer = "No fetched sharp-anchor rows yet; confirm Odds API health and the fetch cadence before judging edge."

    persistent_arbs = _list(dutch.get("persistent_alerts"))
    persistent_count = _int(dutch.get("persistent_alert_count"), len(persistent_arbs))
    complete_arbs = _int(dutch.get("complete_arbs_latest_poll"), _int(dutch.get("complete_arbs")))
    best_ask_sum = dutch.get("best_opportunity", {}).get("ask_sum") if isinstance(dutch.get("best_opportunity"), dict) else dutch.get("best_ask_sum")
    if persistent_count > 0:
        dutch_severity = "good"
        dutch_answer = f"Yes: {persistent_count} Dutch-book basket(s) persisted above the alert threshold for 3+ scans."
    elif complete_arbs > 0:
        dutch_severity = "warn"
        dutch_answer = f"Maybe: {complete_arbs} complete basket(s) appeared, but none are persistent yet. Best ask sum: {best_ask_sum or '-'}."
    elif dutch:
        dutch_severity = "warn"
        dutch_answer = (
            "None found: zero complete/persistent baskets. That is the expected state of an "
            "efficient book (not an operational fault); the scanner keeps watching dry-run."
        )
    else:
        dutch_severity = "warn"
        dutch_answer = "No Dutch-book monitor artifact yet; deploy/governance should build it before this proof stream can answer."

    clv_settings = cfg.raw.get("closing_line_value", {}) if isinstance(cfg.raw.get("closing_line_value"), dict) else {}
    min_final = max(1, int(safe_float(clv_settings.get("minimum_final_samples")) or 12))
    focus_final = _int(focus_view.get("focus_final_positions"))
    focus_mean = safe_float(focus_view.get("focus_mean_final_clv"))
    focus_positive = _list(focus_view.get("focus_positive_cohorts"))
    if focus_final >= min_final and focus_mean is not None and focus_mean > 0 and focus_positive:
        clv_severity = "good"
        clv_answer = f"Yes: focus CLV is positive ({focus_mean:.4f}) across {focus_final}/{min_final}+ final lines."
    elif focus_final >= min_final and focus_mean is not None:
        clv_severity = "bad" if focus_mean <= 0 else "warn"
        clv_answer = f"No: focus CLV has enough final lines ({focus_final}) but mean final CLV is {focus_mean:.4f}."
    else:
        clv_severity = "warn"
        clv_answer = f"Not enough yet: focus final lines {focus_final}/{min_final}; keep collecting non-up/down CLV."

    decision_pnl = safe_float(decision.get("decision_pnl_usdc"))
    if decision_pnl is None:
        decision_pnl = safe_float(goal_plan.get("decision_pnl_usdc"))
    if decision_pnl is None:
        decision_pnl = safe_float(target.get("actual_pnl_since_baseline_usdc"))
    evidence_trips = _int(
        decision.get("proof_verified_round_trips_since_baseline"),
        _int(goal_plan.get("proof_verified_round_trips_since_baseline"), _int(goal_plan.get("audited_round_trips_since_baseline"))),
    )
    min_trips = _int(
        decision.get("minimum_audited_round_trips_for_on_pace"),
        _int(goal_plan.get("minimum_audited_round_trips_for_on_pace"), 5),
    )
    proof_status = str(decision.get("profit_target_proof_status") or goal_plan.get("profit_target_proof_status") or "unknown")
    if decision_pnl is not None and decision_pnl > 0 and evidence_trips >= min_trips:
        paper_severity = "good"
        paper_answer = f"Yes: quote-supported decision P&L is ${decision_pnl:.2f} across {evidence_trips}/{min_trips}+ proof/audited round trips."
    elif decision_pnl is not None and decision_pnl > 0:
        paper_severity = "warn"
        paper_answer = f"Positive but under-sampled: ${decision_pnl:.2f} across {evidence_trips}/{min_trips} proof/audited round trips."
    elif decision_pnl is not None:
        paper_severity = "bad" if evidence_trips >= min_trips else "warn"
        paper_answer = f"No: quote-supported decision P&L is ${decision_pnl:.2f}; proof status={proof_status}; trips {evidence_trips}/{min_trips}."
    else:
        paper_severity = "warn"
        paper_answer = f"No quote-supported paper P&L artifact yet; proof status={proof_status}."

    questions = [
        {
            "question": "Sharp-anchor rows mapped?",
            "answer": sharp_answer,
            "severity": sharp_severity,
            "key_metric": f"fetched={fetched_rows}; mapped={mapped_rows}; scored_hits={scored_hits}; zero_join_flags={flagged_zero_join}",
            "decision_use": "Confirms the highest-prior bookmaker edge is reaching Polymarket tokens and current candidates.",
        },
        {
            "question": "Dutch-arb persistent opportunities?",
            "answer": dutch_answer,
            "severity": dutch_severity,
            "key_metric": f"persistent={persistent_count}; complete_now={complete_arbs}; best_ask_sum={best_ask_sum or '-'}",
            "decision_use": "Mechanical edge candidate; still dry-run only until execution/fill reality is reviewed.",
        },
        {
            "question": "Focus-view CLV positive with enough samples?",
            "answer": clv_answer,
            "severity": clv_severity,
            "key_metric": f"focus_final={focus_final}/{min_final}; focus_mean_final_clv={focus_mean}; positive={len(focus_positive)}",
            "decision_use": "Excludes frozen crypto up/down diagnostics and measures whether focus cohorts beat later market lines.",
        },
        {
            "question": "Audited paper P&L positive after governed probes?",
            "answer": paper_answer,
            "severity": paper_severity,
            "key_metric": f"decision_pnl={decision_pnl}; evidence_trips={evidence_trips}/{min_trips}; proof_status={proof_status}",
            "decision_use": "Only quote-supported paper P&L can move the $100 goal; raw ledger run-rate is not proof.",
        },
    ]
    return {
        "status": _status_from_questions(questions),
        "generated_at_utc": now_utc(),
        "questions": questions,
        "summary": "; ".join(f"{row['question']} {row['severity']}" for row in questions),
        "decision_use": "one_glance_profit_proof_contract_not_trade_authorisation",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }


def _overlay_script() -> str:
    focus_keep_js = json.dumps(list(FOCUS_KEEP_TITLES))
    return f"""
{OVERLAY_START}
<script>
(() => {{
  const escProof = (value) => String(value ?? "-").replace(/[&<>\"']/g, ch => ({{"&":"&amp;","<":"&lt;",">":"&gt;","\\\"":"&quot;","'":"&#39;"}}[ch]));
  const proofBadge = (text, cls) => `<span class="badge ${{escProof(cls || '')}}">${{escProof(text)}}</span>`;
  const proofTable = (rows) => {{
    if (!Array.isArray(rows) || !rows.length) return '<div class="muted">No proof questions generated yet.</div>';
    return `<div class="sectionLead">Four proof questions that decide whether the system is truly moving toward the $100 goal. Reporting only; no stake, gate, paper, or live setting is changed.</div>`
      + `<div>${{proofBadge('overall=' + (window.__proofQuestionsStatus || 'unknown'), window.__proofQuestionsStatus === 'good' ? 'good' : window.__proofQuestionsStatus === 'bad' ? 'bad' : 'warn')}}</div>`
      + `<div class="tableWrap"><table><thead><tr><th>Question</th><th>Answer</th><th>Metric</th><th>Use</th></tr></thead><tbody>`
      + rows.map(row => `<tr><td>${{proofBadge(row.question || 'Proof question', row.severity || 'warn')}}</td><td>${{escProof(row.answer || '-')}}</td><td class="mono">${{escProof(row.key_metric || '-')}}</td><td>${{escProof(row.decision_use || '-')}}</td></tr>`).join('')
      + `</tbody></table></div>`;
  }};
  const verdictPanel = (verdict) => {{
    if (!verdict || !verdict.verdict) return '<div class="muted">Verdict engine has not produced output yet.</div>';
    const state = String(verdict.verdict);
    const cls = state.startsWith('yes') ? 'good' : state.startsWith('no') ? 'bad' : 'warn';
    const gates = verdict.gates || {{}};
    const gateRow = (key) => {{
      const gate = gates[key] || {{}};
      const gcls = gate.state === 'pass' ? 'good' : gate.state === 'fail' ? 'bad' : 'warn';
      return `<tr><td>${{proofBadge(key, gcls)}}</td><td>${{escProof(gate.state || '-')}}</td><td>${{escProof(gate.reason || '-')}}</td></tr>`;
    }};
    return `<div class="sectionLead">Pre-registered ${{escProof(verdict.registered_at_utc || '')}}: the conclusive answer to "can this bot generate $${{escProof(verdict.target_monthly_profit_usdc || 100)}}/month?", computed from evidence only. Reporting only; no gate or stake is changed.</div>`
      + `<div>${{proofBadge(state, cls)}}</div>`
      + `<div class="tableWrap"><table><thead><tr><th>Gate</th><th>State</th><th>Why</th></tr></thead><tbody>`
      + ['A_edge_exists', 'B_edge_survives_costs', 'C_scale_feasible'].map(gateRow).join('')
      + `</tbody></table></div>`
      + `<div class="muted">${{escProof(verdict.next_evidence || '')}}</div>`;
  }};
  const makerPanel = (maker) => {{
    const study = (maker || {{}}).study || {{}};
    const live = (maker || {{}}).live_test || {{}};
    const policy = (maker || {{}}).decision_policy || {{}};
    const requote = (maker || {{}}).requote_alerts || {{}};
    const replay = (maker || {{}}).fill_replay || {{}};
    const requoteState = String(requote.alert_state || 'not_run');
    const requoteCls = requoteState === 'quotes_ok' ? 'good' : (requoteState === 'requote_advised' || requoteState === 'not_run' ? 'warn' : 'bad');
    const requoteBanner = `<div>${{proofBadge('WO-66: ' + requoteState, requoteCls)}} <strong>${{escProof(requote.headline || 'Read-only quote alert has not run yet.')}}</strong></div>`
      + `<div class="muted">${{escProof(requote.markets_checked ?? 0)}} ticket(s) checked · ${{escProof(requote.markets_requiring_action ?? 0)}} requiring action · human action only, no cancel/order path.</div>`;
    const action = String(policy.indicated_action || 'policy_not_run_yet');
    const actionCls = action.startsWith('fund_') ? 'good' : (action.startsWith('stop_') || action.includes('not_supported') ? 'bad' : 'warn');
    const policyLine = `<div>${{proofBadge('policy: ' + action, actionCls)}} ${{proofBadge('ladder stage ' + (policy.ladder_stage_permitted ?? '-'), 'warn')}} ${{proofBadge('kill ' + ((policy.kill_criteria_status || {{}}).status || '-'), ((policy.kill_criteria_status || {{}}).status === 'clear') ? 'good' : 'bad')}}</div>`
      + `<div class="muted">${{escProof(policy.action_reason || policy.policy_note || 'Registered decision policy has not produced output yet.')}}</div>`;
    if (!study.status) return requoteBanner + policyLine + '<div class="muted">Maker-carry study has not run yet.</div>';
    const gates = study.maker_gates || {{}};
    const gateA = gates.M_A_carry_evidence || {{}};
    const gateB = gates.M_B_adverse_realism || {{}};
    const replayState = String(replay.coverage_status || replay.status || 'not_run');
    const replayCls = replayState === 'covered' ? 'good' : (replayState === 'insufficient_coverage' ? 'bad' : 'warn');
    const replayCoverage = replay.coverage || {{}};
    const haircut = replay.simulation_to_reality_haircut;
    const haircutText = (haircut === null || haircut === undefined) ? '-' : String(haircut);
    const mv = String(gates.maker_verdict || 'insufficient_evidence');
    const mvCls = mv.startsWith('evidence_supported') ? 'good' : 'warn';
    let html = `<div class="sectionLead">Zero-fee quoting for daily liquidity rewards - measurement only, the system never places orders. Gates pre-registered ${{escProof(gates.registered_at_utc || '')}}.</div>`
      + requoteBanner
      + policyLine
      + `<div>${{proofBadge(mv, mvCls)}} ${{proofBadge('M-A runs ' + (gateA.runs_at_or_above_target || 0) + '/' + (gateA.required_runs || 7), gateA.state === 'pass' ? 'good' : 'warn')}} ${{proofBadge('M-B markout ' + (gateB.state || 'pending'), gateB.state === 'pass' ? 'good' : 'warn')}} ${{proofBadge('Tier-0 ' + replayState, replayCls)}}</div>`
      + `<div class="mono">Tier-0 confirmed-fill ratio ${{escProof(replay.confirmed_fill_ratio ?? '-')}} Â· replay windows ${{escProof(replayCoverage.windows_covered ?? 0)}}/${{escProof(replayCoverage.windows_simulated ?? 0)}} Â· simulation-to-reality haircut ${{escProof(haircutText)}}</div>`
      + `<div class="muted">Haircut is reported next to M-B only. It is never auto-applied; any tightening requires a dated amendment and can only make the gate harder.</div>`
      + `<div class="mono">est net carry $${{escProof(study.portfolio_net_carry_usd_per_day ?? '-')}} /day (~$${{escProof(study.portfolio_net_carry_usd_per_month ?? '-')}} /month, UPPER BOUND) on $${{escProof(study.portfolio_capital_usd ?? '-')}} across ${{escProof(study.portfolio_markets ?? 0)}} market(s)</div>`;
    if (live.status === 'awaiting_wallet_address') {{
      html += `<div class="muted">Live-test scoreboard idle: no wallet configured (maker_live_test.wallet_address).</div>`;
    }} else if (live.status === 'ok') {{
      const sb = String(live.scoreboard || 'no_activity_yet');
      const sbCls = sb === 'winning_so_far' ? 'good' : (sb === 'no_activity_yet' ? 'warn' : 'bad');
      html += `<div>${{proofBadge('live test: ' + sb, sbCls)}}</div>`
        + `<div class="tableWrap"><table><thead><tr><th>(a) rewards</th><th>(b) inventory PnL</th><th>(c) fills 24h vs model</th><th>net score</th></tr></thead><tbody>`
        + `<tr><td class="mono">$${{escProof(live.rewards_usd_total ?? 0)}} total / $${{escProof(live.rewards_usd_last_24h ?? 0)}} 24h</td>`
        + `<td class="mono">$${{escProof(live.inventory_pnl_usd ?? 0)}} (value $${{escProof(live.inventory_value_usd ?? 0)}})</td>`
        + `<td class="mono">${{escProof(live.fills_last_24h ?? 0)}} vs ${{escProof(live.modelled_fills_per_day ?? '-')}}/day${{live.fill_alert ? ' - STOP' : ''}}</td>`
        + `<td class="mono">$${{escProof(live.net_score_usd ?? 0)}}</td></tr>`
        + `</tbody></table></div>`;
    }}
    return html;
  }};
  const summaryPanel = (payload) => {{
    const verdict = payload.profit_verdict || {{}};
    const gateA = ((verdict.gates || {{}}).A_edge_exists) || {{}};
    const study = ((payload.maker_lane || {{}}).study) || {{}};
    const live = ((payload.maker_lane || {{}}).live_test) || {{}};
    const makerPolicy = ((payload.maker_lane || {{}}).decision_policy) || {{}};
    const makerRequote = ((payload.maker_lane || {{}}).requote_alerts) || {{}};
    const makerReplay = ((payload.maker_lane || {{}}).fill_replay) || {{}};
    const mGates = study.maker_gates || {{}};
    const mA = mGates.M_A_carry_evidence || {{}};
    const alerts = ((payload.oversight_status || {{}}).alerts || []).length;
    const vState = String(verdict.verdict || 'no data');
    const vCls = vState.startsWith('yes') ? 'good' : vState.startsWith('no_') ? 'bad' : 'warn';
    const rows = [
      ['Taker verdict', `${{vState}} - Gate A units ${{gateA.independent_market_units ?? 0}}/${{gateA.minimum_final_samples ?? 12}} (${{gateA.settled_finals_total ?? 0}} finals settled)`, vCls],
      ['Maker lane', study.status
        ? `alert ${{makerRequote.alert_state || 'not_run'}} - Tier-0 ${{makerReplay.coverage_status || makerReplay.status || 'not_run'}} - policy ${{makerPolicy.indicated_action || 'not_run'}} - est $${{study.portfolio_net_carry_usd_per_day ?? '-'}} /day (upper bound) - gate M-A day ${{mA.runs_at_or_above_target ?? 0}}/${{mA.required_runs ?? 7}}`
        : 'study has NOT run on this box yet - force it or wait for the daily harvest',
        ['pull_quotes_now', 'STOP'].includes(String(makerRequote.alert_state || '')) ? 'bad' : (study.status ? 'good' : 'bad')],
      ['Live money test', live.status === 'ok' ? `${{live.scoreboard}} - rewards $${{live.rewards_usd_total ?? 0}} / inventory PnL $${{live.inventory_pnl_usd ?? 0}}`
        : 'not funded (planned post-World Cup)', live.status === 'ok' ? (String(live.scoreboard).startsWith('winning') ? 'good' : 'warn') : 'warn'],
      ['System health', alerts === 0 ? 'no oversight alerts' : `${{alerts}} oversight alert(s) - open Oversight cockpit below`, alerts === 0 ? 'good' : 'bad'],
    ];
    return `<div class="sectionLead">The four numbers that drive decisions. Everything below "Four proof questions" is collapsed drill-down - click any grey heading to expand.</div>`
      + `<div class="tableWrap"><table><tbody>`
      + rows.map(([k, v, cls]) => `<tr><td>${{proofBadge(k, cls)}}</td><td>${{escProof(v)}}</td></tr>`).join('')
      + `</tbody></table></div>`
      + `<div class="muted">Decision calendar: interim taker read Jul 12-13 - maker gate earliest Jul 16 - WC final (verdict + pot regime change) Jul 19 - funding decision post-WC.</div>`;
  }};
  const applyFocusMode = () => {{
    if (window.__focusModeApplied) return;
    window.__focusModeApplied = true;
    const keep = new Set({focus_keep_js});
    const main = document.querySelector('main') || document.body;
    const style = document.createElement('style');
    style.textContent = 'details.focusWrap>summary{{cursor:pointer;opacity:0.65;font-size:0.9em;padding:4px 0;}}details.focusWrap[open]>summary{{opacity:1;}}';
    document.head.appendChild(style);
    const collapse = (el, label) => {{
      if (!el || el.querySelector(':scope > details.focusWrap')) return;
      const det = document.createElement('details');
      det.className = 'focusWrap';
      const sum = document.createElement('summary');
      sum.textContent = label;
      det.appendChild(sum);
      const h2 = el.querySelector(':scope > h2');
      Array.from(el.childNodes).filter((n) => n !== h2).forEach((n) => det.appendChild(n));
      el.appendChild(det);
    }};
    const cards = document.getElementById('cards');
    if (cards && cards.parentNode) {{
      const wrap = document.createElement('section');
      cards.parentNode.insertBefore(wrap, cards);
      wrap.appendChild(cards);
      collapse(wrap, 'legacy status cards (click to expand)');
    }}
    const keepEls = [];
    Array.from(main.querySelectorAll('section')).forEach((sec) => {{
      const h2 = sec.querySelector(':scope > h2');
      const title = (h2 ? h2.textContent : '').trim().toLowerCase();
      if (keep.has(title)) {{ keepEls.push([title, sec]); return; }}
      if (sec.closest('details.focusWrap') || sec.querySelector(':scope > details.focusWrap > summary')) return;
      collapse(sec, (h2 ? h2.textContent.trim() : 'detail') + ' (click to expand)');
      if (h2) h2.style.display = 'none';
    }});
    const headerEl = main.querySelector('header');
    const anchor = headerEl ? headerEl.nextSibling : main.firstChild;
    Array.from(keep).reverse().forEach((title) => {{
      const hit = keepEls.find(([t]) => t === title);
      if (hit) main.insertBefore(hit[1], anchor);
    }});
  }};
  async function refreshProofQuestions() {{
    const el = document.getElementById('proofQuestions');
    if (!el) return;
    try {{
      const res = await fetch('dashboard_data.json?proof_ts=' + Date.now());
      if (!res.ok) throw new Error('dashboard_data.json returned ' + res.status);
      const payload = await res.json();
      const proof = payload.proof_questions || {{}};
      window.__proofQuestionsStatus = proof.status || 'unknown';
      el.innerHTML = proofTable(proof.questions || []);
      const verdictEl = document.getElementById('profitVerdict');
      if (verdictEl) verdictEl.innerHTML = verdictPanel(payload.profit_verdict);
      const makerEl = document.getElementById('makerLane');
      if (makerEl) makerEl.innerHTML = makerPanel(payload.maker_lane);
      const summaryEl = document.getElementById('decisionSummary');
      if (summaryEl) summaryEl.innerHTML = summaryPanel(payload);
      applyFocusMode();
    }} catch (err) {{
      el.innerHTML = `<div class="error">Unable to load proof questions: ${{escProof(err.message || err)}}</div>`;
    }}
  }}
  refreshProofQuestions();
  setInterval(refreshProofQuestions, 5000);
}})();
</script>
{OVERLAY_END}
""".strip()


def _insert_proof_section(html: str) -> str:
    if 'id="proofQuestions"' in html:
        return html
    evidence_match = re.search(
        r'(\s*<section><h2>Evidence funnel</h2><div id="evidenceFunnel"></div></section>)',
        html,
    )
    if evidence_match:
        return html[: evidence_match.end()] + "\n" + PROOF_SECTION + html[evidence_match.end() :]
    sharp_match = re.search(r'(\s*<section><h2>Sharp sports edge funnel</h2>)', html)
    if sharp_match:
        return html[: sharp_match.start()] + PROOF_SECTION + "\n" + html[sharp_match.start() :]
    if "</body>" in html:
        return html.replace("</body>", PROOF_SECTION + "\n</body>", 1)
    return html + "\n" + PROOF_SECTION


def _inject_html_overlay(html: str) -> str:
    html = re.sub(
        re.escape(OVERLAY_START) + r".*?" + re.escape(OVERLAY_END),
        "",
        html,
        flags=re.S,
    )
    html = _insert_proof_section(html)
    script = _overlay_script()
    if "</body>" in html:
        return html.replace("</body>", script + "\n</body>", 1)
    return html + "\n" + script + "\n"


def apply_dashboard_proof_questions(
    cfg: EngineConfig,
    *,
    sharp_anchor_coverage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Standalone/CLI repair pass that overlays proof questions onto an existing dashboard.

    The primary path is `dashboard.render_dashboard`, which builds the questions into its
    own compact payload write; this function exists for retrofitting an already-rendered
    dashboard (e.g. manual diagnostics). It must preserve the dashboard's compact
    single-line payload format rather than re-writing it pretty-printed.
    """
    dashboard_root = cfg.output_root / "polymarket_dashboard"
    data_path = dashboard_root / "dashboard_data.json"
    html_path = dashboard_root / "index.html"
    data = read_json(data_path, default={}) or {}
    data = data if isinstance(data, dict) else {}
    proof = build_proof_questions(cfg, dashboard_data=data, sharp_anchor_coverage=sharp_anchor_coverage)
    data["proof_questions"] = proof
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_text(
        json.dumps(data, sort_keys=True, separators=(",", ":"), default=str),
        encoding="utf-8",
    )
    write_json(cfg.governance_root / "proof_questions.json", proof)

    html_status = "missing_html"
    if html_path.exists():
        html = html_path.read_text(encoding="utf-8")
        html_path.write_text(_inject_html_overlay(html), encoding="utf-8")
        html_status = "overlay_written"

    return {
        "status": "ok",
        "proof_status": proof.get("status"),
        "dashboard_data_path": str(data_path),
        "dashboard_html_path": str(html_path),
        "html_status": html_status,
        "questions": len(proof.get("questions", [])),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Add four proof questions to the generated dashboard artifacts.")
    parser.add_argument("--config", default="polymarket_predictive_config.example.yaml")
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    print(json.dumps(apply_dashboard_proof_questions(cfg), indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
