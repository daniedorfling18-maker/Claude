from __future__ import annotations

from hashlib import sha256
from typing import Any

from .config import EngineConfig, load_config
from .utils import git_commit_hash, now_utc, write_csv, write_json

GOVERNANCE_DOCUMENT_NAMES = (
    "POLYMARKET_ACTUARIAL_MODEL_GOVERNANCE.md",
    "POLYMARKET_DATA_QUALITY_STANDARD.md",
    "POLYMARKET_MODEL_VALIDATION_STANDARD.md",
    "POLYMARKET_RISK_CONTROL_STANDARD.md",
    "POLYMARKET_LIVE_TRADING_APPROVAL_CHECKLIST.md",
    "POLYMARKET_DOCKER_SAFETY_AUDIT.md",
)


def _governance_document_manifest(cfg: EngineConfig) -> list[dict[str, Any]]:
    """Verify version-controlled governance documents without mutating source."""

    docs_dir = cfg.path.resolve().parent / "docs"
    manifest: list[dict[str, Any]] = []
    for name in GOVERNANCE_DOCUMENT_NAMES:
        source = docs_dir / name
        if not source.is_file():
            raise FileNotFoundError(f"Required governance document is missing: {source}")
        content = source.read_bytes()
        manifest.append(
            {
                "name": name,
                "source": f"docs/{name}",
                "byte_length": len(content),
                "sha256": sha256(content).hexdigest(),
            }
        )
    return manifest


def governance_report(cfg: EngineConfig) -> dict[str, Any]:
    document_manifest = _governance_document_manifest(cfg)
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
    generated_at = now_utc()
    run = [
        {
            "run_id": generated_at,
            "model_version": "pm-calibrated-v1",
            "git_commit_hash": git_commit_hash(),
            "status": "governance_report_generated",
        }
    ]
    write_csv(out / "assumption_register.csv", assumptions)
    write_csv(out / "model_risk_register.csv", risks)
    write_csv(out / "model_run_log.csv", run)
    payload = {
        "status": "ok",
        "docs_created": [],
        "docs_verified": sorted(GOVERNANCE_DOCUMENT_NAMES),
        "document_manifest": document_manifest,
        "source_docs_mutated": False,
        "live_trading_approved": False,
        "generated_at": generated_at,
        "generated_at_utc": generated_at,
    }
    write_json(out / "governance_report_summary.json", payload)
    return payload


def main(config_path: str):
    return governance_report(load_config(config_path))
