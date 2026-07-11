# System Map — stocks, flows, feedback loops, and failure surfaces

Filed 2026-07-11 from the full-system sweep. This is the reference picture
of what runs where, what feeds what, and which loops are actually flowing.
Update it when structure changes, not when numbers change.

## Actors and subsystems

```mermaid
flowchart TD
    subgraph External
        PM[Polymarket APIs<br/>Gamma / CLOB / data-api / websocket]
        ODDS[The Odds API<br/>sharp anchors]
        GH[GitHub<br/>repo + telemetry + archive]
        SB[SuperBru]
    end
    subgraph VPS
        LOOP[paper-live loop<br/>collector + models + governance<br/>~95% CPU, cpu_shares soft priority (host: 1 vCPU)]
        SCHED[ops scheduler<br/>13 daily/15-min lanes]
        DASH[dashboard :8765]
        WATCH[superbru watchdog]
        LEDGERS[(Ledgers<br/>features / prints / books /<br/>shadow / portfolio / histories)]
    end
    subgraph Decision layer
        GATES[Verdict gates A/B/C<br/>Maker gates M-A/B/C<br/>amendments 1-7]
        POLICY[decision_policy.json<br/>frozen WO-50 table]
        THESIS[Venture thesis<br/>pivot triggers]
    end
    subgraph Humans_and_agents
        USER[Operator - Danie]
        ORCH[Orchestrator - quant lead]
        CODEX[Builder - Codex]
    end

    PM --> LOOP --> LEDGERS
    PM --> SCHED --> LEDGERS
    ODDS --> LOOP
    SB --> WATCH
    LEDGERS --> GATES --> POLICY --> USER
    LEDGERS --> DASH
    LEDGERS -->|30-min telemetry push| GH --> ORCH
    ORCH -->|work orders + audits| CODEX -->|PRs| GH -->|git pull| VPS
    USER -->|deploys, decisions, $| VPS
    POLICY -.->|indicates only,<br/>never executes| USER
```

## Feedback loops (the heart of the audit)

| Loop | Type | State | Notes |
|---|---|---|---|
| **B1 Honesty**: measurement attacks estimates → estimates shrink to truth | Balancing | **Flowing, proven** | Killed the $958 mirage, the 0.03 fee, the legacy share model, and the orchestrator's own gate loophole |
| **B2 Ops detection**: telemetry/diagnostic → human fix | Balancing | Flowing, manual | Detection automated (disk, restarts, exit codes daily); correction is human. Acceptable at this scale |
| **R1 Build**: questions → WOs → more system → more audit load → more questions | Reinforcing | **Ran hot this week** | Governor is normative only (thesis do-not list, trigger-based audits). Twice outran carrying capacity (disk, CPU) |
| **R2 Value**: live results → credibility/content → audience/customers → resources → scale | Reinforcing | **ZERO FLOW** | Every node exists on paper; no live contact, no publishing, no conversations. The system currently runs only its cost loops |

**The single most important line in this document:** the machine's only
flowing reinforcing loop is the one that consumes resources (R1). The loop
that creates value (R2) is fully built and fully parked, blocked at one
node: first real-world contact (a deposit, a post, a conversation). Every
prior audit said this in different words; the map shows it structurally.

## Stocks and their guards

| Stock | Growth | Guard |
|---|---|---|
| Websocket features | continuous | retention window (~59k rows) + archive roller |
| Trade prints / official books | daily + backfill | per-market caps, completed-market stamps |
| Evidence (graded finals, gate days) | ~daily, event-driven | append-only final history; published_v2-only M-A counting |
| Disk | refills | telemetry disk%; manual prune (no auto-governor — accepted) |
| Founder attention | **depletes** | trigger-based audits; do-not list; decision calendar |

## Delays (where the system waits)

- Evidence gates: days-to-weeks by design (7 distinct days, 12 units).
- Settlement grading: ≤48h after market close (Gamma backfill + price-history fallback).
- Ops detection: ≤30 min (telemetry) / daily (diagnostic).
- **Human actions: unbounded.** Machine nodes all have cadences; the three
  human nodes (deposit, publish, converse) have prose dates only. This is
  the longest, least-governed delay in the system and sits directly on R2.

## Single points of failure and their mitigations

| SPOF | Blast radius | Mitigation |
|---|---|---|
| One VPS | all collection + runtime | Telemetry preserves decisions; WO-65 archives full ledgers; restore runbook |
| Gamma as settlement-truth source | grading pipeline | Price-history fallback live; WO-47 adds websocket resolution events (second source) |
| Collector-computer coupling (one process) | collection gaps on compute crash | Fail-closed restarts + cpu_shares soft priority (host is 1 vCPU; hard caps meaningless); revisit if restarts persist |
| Orchestrator context | continuity of judgment | Charter + WO docs + this map are the durable memory; sessions are disposable |
| Self-merge (author=reviewer=merger) | code defects reaching main | Acceptable at $0 live; **must add independent review before live capital** |
| SA→Polymarket access assumption | the entire live branch | UNVERIFIED. The $10 deposit test resolves it |

## Sweep verdict

Structure: sound. Balancing loops: proven under fire. Guards: in place and
recently tightened. The system's one structural pathology is not in any
component — it is that **R2 has never carried a single unit of flow**, and
every day R1 runs while R2 is parked, the ratio of cost to value worsens.
No further code changes this sweep; the map IS the finding.
