from __future__ import annotations

import argparse
import json
import re
from typing import Any, Mapping

from .config import EngineConfig, load_config
from .utils import now_utc, read_json, safe_float, write_json

OVERLAY_START = "<!-- proof-questions-overlay:start -->"
OVERLAY_END = "<!-- proof-questions-overlay:end -->"
PROOF_SECTION = '  <section><h2>Four proof questions</h2><div id="proofQuestions"></div></section>'


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
