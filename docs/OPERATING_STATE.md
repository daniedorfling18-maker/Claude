# Operating State — the canonical answers

Manual v1, updated 2026-07-12. WO-68 will replace this with a generated
artifact; until then THIS file wins over any conflicting prose in README,
AGENTS.md, or older state documents (2026-07-12 external audit finding:
the front-door docs had drifted three weeks behind the system).

| Question | State |
|---|---|
| Research mode | ACTIVE — shadow/paper-gated research running on the VPS |
| Mechanical paper capability | Present (paper broker, typed ledger, reconciliation) |
| Governed paper authorisation | NOT granted — `paper_allowed = false`; promotion gates unpassed |
| Paper activity | Paper-bridge loop runs; no governed promotions; paper ledger flat since the 2026-06-25 reset |
| Human live-test authorisation | Operator's own domain, outside system control. R240 pipe test in progress (SA access verified 2026-07-12). The system's role is READ-ONLY monitoring plus an advisory quote sheet |
| Live-wallet monitoring | ACTIVE, read-only (`maker_live_test` scoreboard + WO-62 three-way NAV reconciliation). The configured address is a public identifier; no keys exist anywhere in this system |
| Live orders submitted by the system | ZERO, ever. No live order path exists (foundational invariant) |
| Autonomous execution authorisation | BLOCKED — WO-67 preconditions P1–P5, including a dated owner amendment to AGENTS.md; a chat instruction never suffices |
| Latest deployed SHA | See `vps-telemetry` branch `manifest.json` → `deployed_git_rev` (refreshed every 30 min) |
| Latest verified evidence | Taker: 14 units, 6 beating close, p=0.788 → insufficient/trending NO. Maker: yield-first day 1 at target ($8.77/day modelled, 2026-07-11); M-A 1/7 published_v2 days |

Registered decision dates: earliest maker-gate pass 2026-07-17; final taker
read + policy date 2026-07-19/20; terminal resolution 2026-08-19/20
(amendment 7).
