# Polymarket research guide

This page replaces the June 2026 local shadow-research snapshot. It contains no
current gate values or local launcher instructions.

Read in this order:

1. `AGENTS.md` — VPS-only operating and governance rules;
2. `docs/POLYMARKET_CURRENT_STATE.md` and `docs/OPERATING_STATE.md` — where
   point-in-time state comes from;
3. `docs/EXPERIMENT_REGISTRY.md` — the exact H1-H3 research freeze;
4. `docs/POLYMARKET_CODEX_WORK_ORDERS.md` — accepted work and authorization;
5. `docs/REPOSITORY_LINE_AUDIT_2026-07-21.md` — unresolved static findings.

Research must use observation-time inputs, chronological/out-of-sample
validation and explicit data-dependency contracts. Historical, modeled,
reconstructed, shadow, paper and live-real-money evidence classes cannot be
relabelled upward. Backtests and pipeline inventories are diagnostic unless a
registered prospective control says otherwise.

All engines, collectors, models and tests run only in the VPS environment. The
single production stack is paper/scan-gated. Funding is CLOSED, WO-67 is BLOCKED
and no autonomous live-order path is approved.
