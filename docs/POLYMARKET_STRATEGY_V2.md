# Strategy V2: historical anchored-edge proposal

Strategy V2 was the June 2026 shadow-only transition away from internally
modeling market midpoint toward independently anchored executable edge. It is
retained as design provenance, not as the current work queue or an active
launcher.

Its durable constraints are now governed elsewhere:

- only the three H1-H3 hypotheses in `docs/EXPERIMENT_REGISTRY.md` may consume
  promotion-oriented research;
- exact-token identity, point-in-time sharp anchors, executable bid/ask, depth,
  spread, fees, markouts and adverse selection are required;
- diagnostics cannot become gates or be relabeled as prospective proof; and
- missing, stale or mismatched evidence fails closed.

Use `docs/POLYMARKET_CODEX_WORK_ORDERS.md` for accepted implementation history,
the generated operating state for current values and
`docs/REPOSITORY_LINE_AUDIT_2026-07-21.md` for unresolved correctness risks.
Funding is CLOSED and WO-67 is BLOCKED.
