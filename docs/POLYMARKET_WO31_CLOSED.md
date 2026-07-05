# WO-31 Closure Status

Status: **closed / landed**
Date closed: **2026-07-05**

## Work order

**WO-31 — Per-sport anchor coverage reconciliation and auto-trim signal**

## Landed implementation

WO-31 is implemented and deployed through PR #65 and the subsequent successful VPS deploy run.

Implemented artifacts:

- `src/polymarket_predictive_engine/sharp_anchor_coverage.py`
- `outputs/polymarket_model_governance/sharp_anchor_coverage.json`
- `outputs/polymarket_model_governance/sharp_anchor_coverage_history.csv`
- `outputs/polymarket_model_governance/proof_questions.json`
- `outputs/polymarket_dashboard/dashboard_data.json` key: `proof_questions`

Governance wiring:

- `refresh-governance` now rebuilds sharp-anchor coverage.
- The dashboard now includes the four proof questions:
  1. Sharp-anchor rows mapped?
  2. Dutch-arb persistent opportunities?
  3. Focus-view CLV positive with enough samples?
  4. Audited paper P&L positive after governed probes?

Safety posture:

- No stake increase.
- No live execution path.
- No paper/live gate loosening.
- No automatic config trimming.
- `no_mappable_market` remains a recommendation string only.
- `paper_trading_invoked=false` and `live_trading_invoked=false` are preserved on the new artifacts.
- Crypto up/down remains frozen as a diagnostic-only family.

## Verification

CI passed on PR #65 after fixes.

The manual `Deploy Polymarket VPS Paper` workflow run completed successfully, including:

- deploy secrets validation
- SSH preparation and authorization
- environment patch delivery
- VPS pull/rebuild/verify step
- governance/dashboard refresh
- dashboard verification checks

This file is a status addendum because the long-form `docs/POLYMARKET_CODEX_WORK_ORDERS.md` still contains historical WO-31 text. The implementation status is closed/landed as of this addendum.
