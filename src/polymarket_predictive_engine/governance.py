from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import EngineConfig, load_config
from .utils import git_commit_hash, now_utc, write_csv, write_json

DOCS = {
"POLYMARKET_ACTUARIAL_MODEL_GOVERNANCE.md": """# Polymarket Actuarial Model Governance\n\n## Intended purpose\nIdentify point-in-time positive expected value Polymarket opportunities for research, backtesting and paper trading.\n\n## Intended users\nQuant, actuarial, model-risk and trading-system reviewers.\n\n## Restrictions on use\nLive trading is prohibited unless data readiness, validation, paper-trading evidence, risk controls and human approval all pass.\n\n## Model risk\nThe model may be miscalibrated, overfit, affected by market microstructure, or invalidated by resolution ambiguity.\n\n## Live trading approval requirements\nDual opt-in, approval file, no kill switch, no data quality blockers, validation approval, and paper trading evidence are mandatory.\n""",
"POLYMARKET_DATA_QUALITY_STANDARD.md": """# Polymarket Data Quality Standard\n\nTraining must use raw point-in-time snapshots only. Latest snapshots are diagnostics only. Blocker issues fail closed.\n""",
"POLYMARKET_MODEL_VALIDATION_STANDARD.md": """# Polymarket Model Validation Standard\n\nValidation requires time splits, holdout testing, calibration, baseline comparison, drift checks and sample-size warnings.\n""",
"POLYMARKET_RISK_CONTROL_STANDARD.md": """# Polymarket Risk Control Standard\n\nRisk controls include bankroll, Kelly cap, exposure limits, spread, liquidity, confidence, resolution-risk and kill-switch gates.\n""",
"POLYMARKET_LIVE_TRADING_APPROVAL_CHECKLIST.md": """# Polymarket Live Trading Approval Checklist\n\n- Data readiness approved\n- Model validation approved\n- Paper trading evidence reviewed\n- Approval file created outside git\n- POLYMARKET_LIVE_TRADING=1 set\n- trading.mode: live set\n- Kill switch inactive\n- No blockers or duplicate-writer risks\n""",
"POLYMARKET_DOCKER_SAFETY_AUDIT.md": """# Polymarket Docker Safety Audit\n\nDocker services must be mapped before live use. Duplicate writers and conflicting signal paths must be resolved before capital is used.\n""",
}


def governance_report(cfg: EngineConfig) -> dict[str, Any]:
    docs_dir = Path("docs")
    docs_dir.mkdir(parents=True, exist_ok=True)
    for name, content in DOCS.items():
        (docs_dir / name).write_text(content, encoding="utf-8")
    out = cfg.governance_root
    assumptions = [
        {"assumption": "Raw snapshots are point-in-time", "status": "requires data quality confirmation"},
        {"assumption": "Latest joined snapshots are diagnostics only", "status": "enforced by design"},
        {"assumption": "Live trading disabled by default", "status": "enforced by config and environment gates"},
    ]
    risks = [
        {"risk": "Resolution ambiguity", "severity": "high", "mitigation": "resolution-risk filters and manual review"},
        {"risk": "Lookahead leakage", "severity": "blocker", "mitigation": "feature leakage rejection and point-in-time labels"},
        {"risk": "Duplicate writer or executor", "severity": "high", "mitigation": "pipeline inventory and duplicate writer checks"},
    ]
    run = [{"run_id": now_utc(), "model_version": "pm-calibrated-v1", "git_commit_hash": git_commit_hash(), "status": "governance_report_generated"}]
    write_csv(out / "assumption_register.csv", assumptions)
    write_csv(out / "model_risk_register.csv", risks)
    write_csv(out / "model_run_log.csv", run)
    payload = {"docs_created": sorted(DOCS), "live_trading_approved": False, "generated_at": now_utc()}
    write_json(out / "governance_report_summary.json", payload)
    return payload


def main(config_path: str):
    return governance_report(load_config(config_path))
