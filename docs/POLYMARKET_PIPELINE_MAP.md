# Polymarket pipeline map: diagnostic only

This former generated inventory is retired as a safety or capability map.
`pipeline_inventory.py` infers capabilities from filename substrings and can
classify historical or deleted launchers as order-capable. It does not prove
what the deployed stack can execute and must not be used to authorize or assess
live trading.

The authoritative boundaries are:

- `AGENTS.md` for repository and runtime rules;
- `outputs/performance/operating_state.{md,json}` on the VPS for point-in-time
  generated state;
- `docs/OPERATING_STATE.md` for the generated-state contract; and
- `docs/POLYMARKET_VPS_DOCKER_RUNBOOK.md` for the single production stack.

Funding is CLOSED and WO-67 is BLOCKED. No approved autonomous live-order path
exists. Any regenerated pipeline inventory remains heuristic diagnostic output
until the generator uses explicit declared capabilities and has direct tests.
