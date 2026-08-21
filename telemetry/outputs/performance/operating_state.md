# Generated operating state

Generated at: `2026-08-21T01:55:06Z`  
Source: point-in-time config and artifacts. Missing evidence is `UNKNOWN`; it is never guessed.

| Question | State | Evidence | Source | Verified at |
|---|---|---|---|---|
| Research mode | **ACTIVE_SHADOW_PAPER_GATED** | trading.mode=paper; paper.enabled=True; live.enabled=False | `effective config` | UNKNOWN |
| Mechanical paper readiness (not authorisation) | **READY** | mechanical_readiness=ready; capability_only=true; governed_authorisation_is_separate=true | `effective config + paper_trade_readiness.json` | UNKNOWN |
| Governed paper authorisation | **NOT_GRANTED** | no approved trade_signals rows; sports_other has no shadow evidence yet; verification_age_seconds=81749.0; maximum=86400 | `local_history_audit_summary.json` | 2026-08-20T03:12:37Z |
| Paper-simulation activity by registered lane | **RECORDED_FILLS=49** | paper_simulation_only=true; registered_experiment_lanes={"mispricing_alpha_overlay": 3, "paper_exit_alpha_edge_deteriorated": 1, "paper_exit_fixed_horizon": 7, "paper_exit_hard_stop_loss": 4, "paper_exit_stop_loss": 4, "paper_exit_take_profit": 4, "price_action_round_trip:label_headroom_research": 1, "price_action_round_trip:low_price_tick_probe": 1, "price_action_round_trip:paper_confirmation_candidate": 17, "price_action_round_trip:paper_confirmation_current_candidate": 7}; does_not_imply_live_or_governed_authorisation=true | `paper_fills.csv + paper_orders.csv` | UNKNOWN |
| Human live-test authorisation | **OPERATOR_DOMAIN_MONITORED_READ_ONLY** | human execution is outside system control; system monitoring is read-only | `maker_live_test config` | UNKNOWN |
| Live-wallet monitoring (legacy primary view) | **ACTIVE_READ_ONLY:ok** | primary=single_project_account; custody=A1; attribution=non_overlapping_mode_time_windows | `config + maker_live_test.json` | 2026-08-21T01:41:31Z |
| Operator wallet monitoring | **ACTIVE_READ_ONLY:ok** | configured=True; role=human_operator_window; custody=A1 | `maker_live_test.wallet_address + maker_live_test.json` | 2026-08-21T01:41:31Z |
| Executor-era monitoring on the A1 single project wallet | **A1_SINGLE_PROJECT_WALLET:EXECUTOR_WINDOW_INACTIVE:ok** | configured_from=maker_live_test.wallet_address; legacy_executor_wallet_field_must_remain_empty=True; role=executor_mode_time_window; concurrent_human_window_prohibited=true | `custody Amendment A1 + maker_live_test.wallet_address + execution ledger mode/time` | 2026-08-21T01:41:31Z |
| A1 excess-balance sweep advisory | **NOT_REQUIRED** | do not initiate an A1 sweep from this artifact | `execution/a1_sweep_advisory.json` | 2026-08-21T01:55:06Z |
| Live orders submitted by the system | **UNKNOWN** | no execution ledger; live_config_enabled=False | `UNKNOWN` | UNKNOWN |
| Future executor mode | **ABSENT** | status=ABSENT; ledger_rows=0 | `execution/executor_status.json` | 2026-08-21T01:55:00Z |
| Future executor open orders | **ABSENT** | ledger_rows=0 | `execution/executor_status.json` | 2026-08-21T01:55:00Z |
| Future executor exposure vs stage cap | **ABSENT** | cap_source=decision_policy.sizing.binding_capital_usd | `execution/executor_status.json + maker_carry/decision_policy.json` | 2026-08-21T01:55:00Z |
| Future executor last action age | **ABSENT** | last_action_at_utc=UNKNOWN | `execution/executor_status.json` | 2026-08-21T01:55:00Z |
| Independent executor dead-man monitor | **ABSENT** | heartbeat_age_seconds=None; countdown_seconds=None; threshold_seconds=1800.0 | `execution/executor_heartbeat.json read by vps_ops_scheduler` | 2026-08-21T01:55:00Z |
| Future executor freshness SLO | **ABSENT** | heartbeat_age_seconds=None; slo_seconds=600.0 | `execution/executor_status.json` | 2026-08-21T01:55:00Z |
| Future executor kill-criteria scoreboard | **ABSENT** | triggered=none | `maker_carry/decision_policy.json via executor monitor` | 2026-08-21T01:55:00Z |
| Autonomous execution authorisation | **BLOCKED** | P1=not_met, P2=not_met, P3=not_met, P4=UNKNOWN, P5=met | `WO-67 preconditions` | UNKNOWN |
| Persistent degraded-state incidents | **INCIDENT** | active_incidents=1; new_incidents=0 | `ops_scheduler/degraded_state_watchdog.json` | 2026-08-21T01:55:03Z |
| Latest post-deploy acceptance | **PASS** | target=f160b9caf3816d6f467bdb0811cff3ffead186d9; failed_checks=none; rollback_ref=f528adb8937ffc0aac036d4971eb3fd20bd587c0 | `ops_scheduler/deploy_acceptance.json` | 2026-08-15T12:19:15Z |
| Latest deployed SHA | **036d09be26a0d0f7d9cba5a049e468b688decd70** | telemetry=036d09be26a0d0f7d9cba5a049e468b688decd70; expected=036d09be26a0d0f7d9cba5a049e468b688decd70; image=036d09be26a0d0f7d9cba5a049e468b688decd70; aligned=True | `outputs/performance/vps_telemetry_manifest.json + PM_VPS_DEPLOYED_SHA/PM_IMAGE_BUILD_SHA` | 2026-08-21T01:30:09Z |
| Source vs deployed SHA | **ALIGNED** | source=036d09be26a0d0f7d9cba5a049e468b688decd70; checkout=036d09be26a0d0f7d9cba5a049e468b688decd70; deployed=036d09be26a0d0f7d9cba5a049e468b688decd70; divergence_started_at_utc=UNKNOWN; divergence_age_seconds=0.0 | `outputs/performance/vps_telemetry_manifest.json` | 2026-08-21T01:30:09Z |
| Latest verified evidence timestamp | **2026-08-21T01:55:03Z** | degraded_state_watchdog | `artifact generated_at_utc` | 2026-08-21T01:55:03Z |

## Operating SLOs (reporting only)

A breach alerts the operator; it never changes a gate, size, broker, or order path.

| Metric | State | Target | Measured | Unit | Source | Observed at |
|---|---|---:|---:|---|---|---|
| Quote-sheet age | **OK** | 93600.0 | 807.59 | seconds | `outputs/maker_carry/maker_quote_sheet.md` | 2026-08-21T01:41:38Z |
| Governance-refresh duration | **OK** | 2400.0 | 118.308 | seconds | `outputs/ops_scheduler/status.json` | 2026-08-20T19:58:49.308350+00:00 |
| Consecutive scheduler overrun/missed cycles | **BREACH** | 0.0 | 1.0 | cycles | `outputs/ops_scheduler/status.json` | 2026-08-21T01:55:01.288477+00:00 |
| Websocket observation gap | **OK** | 300.0 | 53.0 | seconds | `outputs/polymarket_websocket/websocket_messages_latest.json` | 2026-08-21T01:54:13Z |
| Dashboard staleness | **OK** | 300.0 | 14.0 | seconds | `outputs/polymarket_dashboard/dashboard_data.json` | 2026-08-21T01:54:52Z |
| Wallet-reconciliation age | **OK** | 93600.0 | 81772.0 | seconds | `outputs/performance/wallet_reconciliation.json` | 2026-08-20T03:12:14Z |
| Ledger-anchor age | **OK** | 129600.0 | 81631.0 | seconds | `outputs/performance/ledger_anchor_head.json` | 2026-08-20T03:14:35Z |
| Ledger-anchor publication age | **OK** | 7200.0 | 1500.0 | seconds | `outputs/performance/anchor_push_status.json` | 2026-08-21T01:30:06Z |
| Telemetry publication age | **OK** | 7200.0 | 1492.0 | seconds | `outputs/performance/telemetry_push_status.json` | 2026-08-21T01:30:14Z |

## Degraded-state watchdog (reporting only)

Persistent semantic-health incidents alert the owner; they never override a fail-closed or risk state.

| Registration | Entity | State | Reason | Source |
|---|---|---|---|---|
| operating_state_slo_breach | operating_state_slo | **INCIDENT** | SLO rows breached, unknown, or missing: scheduler_overrun_cycles | `performance/operating_state.json` |

## WO-67 autonomous-execution preconditions

WO-67 remains blocked unless every precondition is independently `met`. This report never authorises execution.

| ID | Precondition | State | Evidence | Source |
|---|---|---|---|---|
| P1 | Maker gates M-A/M-B/M-C pass | **not_met** | {"M_A_carry_evidence": "pending", "M_B_adverse_realism": "pending", "M_C_payout_floor": "pass_by_construction"} | `maker_carry_study.json` |
| P2 | Human live-test Stage 1 complete | **not_met** | ladder_stage_permitted=0; consecutive_live_ok_days=0; Stage 1 requires >=7 positive real days with fills inside the registered 2x-model bound | `decision_policy.json` |
| P3 | Dated owner amendment authorises scoped live path | **not_met** | AGENTS.md has no signed Owner amendments section; the WO-67 draft authorises nothing | `AGENTS.md Owner amendments` |
| P4 | Independent merge control enforced | **UNKNOWN** | independent_merge_gate.json has not been produced in this checkout; run scripts/audit_github_merge_gate.py to evaluate P4 | `independent_merge_gate.json` |
| P5 | Scoped key-custody design approved | **met** | custody design and Amendment A1 both carry exact dated APPROVED status lines | `docs/KEY_CUSTODY_DESIGN_WO67_P5.md` |

## Missing inputs

- `independent_merge_gate`
- `live_execution_ledger`

`paper_trading_invoked=false`; `live_trading_invoked=false`.
