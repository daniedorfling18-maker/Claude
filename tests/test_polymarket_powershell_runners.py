from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _script_text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_strategy_v2_cycle_pins_repo_source_before_python_invocations():
    text = _script_text("scripts/run_polymarket_strategy_v2_cycle.ps1")

    pythonpath_index = text.index("$env:PYTHONPATH")
    first_python_module_index = text.index('"-m", "polymarket_predictive_engine.cli"')

    assert "(Resolve-Path .\\src).Path" in text
    assert pythonpath_index < first_python_module_index


def test_strategy_v2_cycle_refreshes_independent_anchors_before_scoring():
    text = _script_text("scripts/run_polymarket_strategy_v2_cycle.ps1")

    assert "[int]$IndependentAnchorMaxAgeMinutes = 60" in text
    sharp_refresh_index = text.index("refresh-sharp-anchor")
    crypto_targets_index = text.index("build-crypto-targets")
    crypto_refresh_index = text.index("build-crypto-fundamental")
    anchored_edge_index = text.index("run_polymarket_strategy_v2_anchored_edge.ps1")

    assert sharp_refresh_index < anchored_edge_index
    assert crypto_targets_index < crypto_refresh_index
    assert crypto_refresh_index < anchored_edge_index
    assert "crypto_targets = Read-JsonIfExists" in text
    assert "independent_anchor_refresh = $independentAnchorRefresh" in text


def test_strategy_v2_cycle_bounds_python_steps():
    text = _script_text("scripts/run_polymarket_strategy_v2_cycle.ps1")

    assert "[int]$StepTimeoutSeconds = 180" in text
    assert "Start-Process" in text
    assert "$process.WaitForExit($StepTimeoutSeconds * 1000)" in text
    assert "timed out after $StepTimeoutSeconds seconds" in text
    assert "$exitCode = [int]$process.ExitCode" in text


def test_strategy_v2_cycle_skips_before_heavy_work_when_memory_is_high():
    text = _script_text("scripts/run_polymarket_strategy_v2_cycle.ps1")

    assert "[double]$MaxMemoryPercent = 99" in text
    assert "[double]$MaintenanceMaxMemoryPercent = 99.5" in text
    guard_index = text.index("skipped_high_memory")
    shadow_start_index = text.index("$shadowRefreshProcess.Start()")

    assert "Get-MemoryUsedPercent" in text
    assert "Invoke-LowMemoryMaintenance" in text
    assert '"paper-trade"' in text
    assert "$memoryUsedPercent -ge $MaxMemoryPercent" in text
    assert guard_index < shadow_start_index


def test_strategy_v2_cycle_checks_memory_between_python_steps():
    text = _script_text("scripts/run_polymarket_strategy_v2_cycle.ps1")

    assert "function Assert-MemoryBelowGuard" in text
    assert "stopped_high_memory" in text
    assert 'Assert-MemoryBelowGuard -Phase "before_$Name"' in text
    assert 'Assert-MemoryBelowGuard -Phase "after_shadow_refresh"' in text
    invoke_step_index = text.index("function Invoke-PythonStep")
    assert text.index('Assert-MemoryBelowGuard -Phase "before_$Name"', invoke_step_index) < text.index(
        "Start-Process", invoke_step_index
    )


def test_strategy_v2_cycle_renders_dashboard_after_latest_status_is_written():
    text = _script_text("scripts/run_polymarket_strategy_v2_cycle.ps1")

    status_write_index = text.index("strategy_v2_cycle_latest_status.json")
    dashboard_render_index = text.index("render_polymarket_dashboard.py")

    assert status_write_index < dashboard_render_index


def test_strategy_v2_cycle_builds_forward_evidence_after_persistence_log():
    text = _script_text("scripts/run_polymarket_strategy_v2_cycle.ps1")

    persistence_index = text.index("anchored_edge_persistence_log.csv")
    evidence_index = text.index("strategy-v2-evidence")
    status_payload_index = text.index("strategy_v2_forward_evidence = $strategyV2ForwardEvidence")

    assert persistence_index < evidence_index
    assert evidence_index < status_payload_index


def test_strategy_v2_cycle_refreshes_websocket_after_forward_evidence():
    text = _script_text("scripts/run_polymarket_strategy_v2_cycle.ps1")

    assert "[int]$PostEvidenceWebsocketSeconds = 20" in text
    evidence_index = text.index("strategy-v2-evidence")
    websocket_index = text.index("strategy-v2-websocket-refresh")
    normalize_index = text.index("strategy-v2-normalize-websocket")
    status_payload_index = text.index("strategy_v2_websocket_refresh = $strategyV2WebsocketRefresh")

    assert evidence_index < websocket_index
    assert websocket_index < normalize_index
    assert normalize_index < status_payload_index
    assert "websocket_feature_summary.json" in text


def test_strategy_v2_cycle_builds_round_trip_after_websocket_normalisation():
    text = _script_text("scripts/run_polymarket_strategy_v2_cycle.ps1")

    normalize_index = text.index("strategy-v2-normalize-websocket")
    microstructure_index = text.index("price-action-microstructure")
    round_trip_index = text.index("strategy-v2-round-trip")
    microstructure_status_index = text.index("price_action_microstructure = $priceActionMicrostructure")
    status_payload_index = text.index("strategy_v2_round_trip_evidence = $strategyV2RoundTripEvidence")

    assert normalize_index < microstructure_index
    assert microstructure_index < round_trip_index
    assert microstructure_index < microstructure_status_index
    assert round_trip_index < status_payload_index
    assert "microstructure_summary.json" in text
    assert "strategy_v2_round_trip_evidence.json" in text


def test_strategy_v2_cycle_builds_price_action_scout_after_round_trip():
    text = _script_text("scripts/run_polymarket_strategy_v2_cycle.ps1")

    round_trip_index = text.index("strategy-v2-round-trip")
    scout_index = text.index("price-action-scout")
    status_payload_index = text.index("price_action_scout = $priceActionScout")

    assert round_trip_index < scout_index
    assert scout_index < status_payload_index
    assert "price_action_scout_summary.json" in text


def test_strategy_v2_cycle_builds_price_action_paper_signals_after_scout():
    text = _script_text("scripts/run_polymarket_strategy_v2_cycle.ps1")

    scout_index = text.index("price-action-scout")
    feedback_index = text.index("price-action-feedback")
    paper_signal_index = text.index("price-action-paper-signals")
    status_payload_index = text.index("price_action_paper_signals = $priceActionPaperSignals")

    assert scout_index < feedback_index
    assert feedback_index < paper_signal_index
    assert paper_signal_index < status_payload_index
    assert "price_action_feedback_before_paper_signals = $priceActionFeedbackBeforePaperSignals" in text
    assert "price_action_paper_signal_summary.json" in text


def test_strategy_v2_cycle_runs_paper_broker_after_price_action_paper_signals():
    text = _script_text("scripts/run_polymarket_strategy_v2_cycle.ps1")

    paper_signal_index = text.index("price-action-paper-signals")
    paper_trade_index = text.index('"paper-trade"', paper_signal_index)
    final_feedback_index = text.rindex("price-action-feedback")
    status_payload_index = text.index("paper_trade_refresh = $paperTradeRefresh")

    assert paper_signal_index < paper_trade_index
    assert paper_trade_index < final_feedback_index
    assert paper_trade_index < status_payload_index
    assert "paper_trade_refresh.json" in text
    assert "paper_trade_refresh = $paperTradeRefresh" in text


def test_strategy_v2_cycle_refreshes_price_action_feedback_after_paper_broker():
    text = _script_text("scripts/run_polymarket_strategy_v2_cycle.ps1")

    paper_trade_index = text.index('"paper-trade"')
    final_feedback_index = text.rindex("price-action-feedback")
    status_payload_index = text.index("price_action_feedback = $priceActionFeedback")

    assert paper_trade_index < final_feedback_index
    assert final_feedback_index < status_payload_index
    assert "price_action_feedback.json" in text


def test_strategy_v2_scheduled_wrapper_pins_repo_source():
    text = _script_text("scripts/run_strategy_v2_cycle_scheduled_wrapper.ps1")

    assert "(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot \"..\"))" in text
    assert "C:\\Users" not in text
    assert '$env:PYTHONPATH = Join-Path $repoRoot "src"' in text
    assert "strategy_v2_scheduled_task_$runId.log" in text


def test_strategy_v2_scheduled_wrapper_skips_before_cycle_when_memory_is_high():
    text = _script_text("scripts/run_strategy_v2_cycle_scheduled_wrapper.ps1")

    assert "[double]$MaxMemoryPercent = 99" in text
    assert "[double]$MaintenanceMaxMemoryPercent = 99.5" in text
    assert "Get-MemoryUsedPercent" in text
    assert "$memoryUsedPercent -ge $MaxMemoryPercent" in text
    assert "Invoke-LowMemoryMaintenance" in text
    assert '"paper-trade"' in text
    assert "low_memory_maintenance = $maintenance" in text
    assert "skipped_high_memory" in text
    assert "run_polymarket_strategy_v2_cycle.ps1" in text
    assert text.index("skipped_high_memory") < text.index("run_polymarket_strategy_v2_cycle.ps1")


def test_strategy_v2_scheduled_wrapper_prevents_overlapping_runs():
    text = _script_text("scripts/run_strategy_v2_cycle_scheduled_wrapper.ps1")

    assert "Global\\PolymarketStrategyV2Cycle" in text
    assert "$mutex.WaitOne(0)" in text
    assert "skipped_already_running" in text
    assert "ReleaseMutex" in text


def test_shadow_research_cycle_bounds_each_python_step():
    text = _script_text("scripts/run_polymarket_shadow_research_cycle.ps1")

    assert "[int]$StepTimeoutSeconds = 180" in text
    assert "Start-Process" in text
    assert "$process.WaitForExit($StepTimeoutSeconds * 1000)" in text
    assert "timed out after $StepTimeoutSeconds seconds" in text
    assert "$exitCode = [int]$process.ExitCode" in text


def test_shadow_research_cycle_checks_memory_between_python_steps():
    text = _script_text("scripts/run_polymarket_shadow_research_cycle.ps1")

    assert "function Assert-MemoryBelowGuard" in text
    assert "function Stop-ForHighMemory" in text
    assert "[switch]$SkipMemoryGuard" in text
    assert 'Assert-MemoryBelowGuard -Phase "before_$Name"' in text
    assert 'Assert-MemoryBelowGuard -Phase "after_$Name"' in text
    assert 'status = "stopped_high_memory"' in text
    assert "skipped_after_memory_stop" in text
    assert "refreshed_dashboard_only_after_memory_stop" in text
    assert "render-dashboard-after-memory-stop" in text
    invoke_step_index = text.index("function Invoke-Step")
    start_process_index = text.index("Start-Process", invoke_step_index)
    before_guard_index = text.index('Assert-MemoryBelowGuard -Phase "before_$Name"', invoke_step_index)
    after_guard_index = text.index('Assert-MemoryBelowGuard -Phase "after_$Name"', invoke_step_index)

    assert before_guard_index < start_process_index
    assert start_process_index < after_guard_index


def test_shadow_research_cycle_dashboard_refresh_bypasses_heavy_guard_only():
    text = _script_text("scripts/run_polymarket_shadow_research_cycle.ps1")

    assert 'Invoke-Step "render-dashboard"' in text
    assert 'Invoke-Step "render-dashboard-after-memory-stop"' in text
    assert "-SkipMemoryGuard" in text
    assert "$memory.used_percent -ge $DashboardOnlyMaxMemoryPercent" in text
    assert "$memory.used_percent -lt $DashboardOnlyMaxMemoryPercent" in text
    assert "$SkipDashboardOnHighMemory" in text


def test_shadow_research_cycle_refreshes_price_action_learning_and_governance():
    text = _script_text("scripts/run_polymarket_shadow_research_cycle.ps1")

    local_audit_index = text.index("local-history-audit")
    profit_sprint_index = text.index("profit-sprint")
    microstructure_index = text.index("price-action-microstructure")
    scout_index = text.index("price-action-scout")
    governance_refresh_index = text.index("refresh-governance")
    status_payload_index = text.index("price_action_model_decision = $governanceRefresh.price_action_model_decision")

    assert local_audit_index < profit_sprint_index
    assert profit_sprint_index < microstructure_index
    assert microstructure_index < scout_index
    assert scout_index < governance_refresh_index
    assert governance_refresh_index < status_payload_index
    assert "polymarket_predictive_engine.refresh_governance" in text
    assert "price_action_feedback_state = $governanceRefresh.price_action_feedback_state" in text
    assert "dashboard_status = $governanceRefresh.dashboard.status" in text


def test_dashboard_server_runner_refuses_to_start_when_memory_is_high():
    text = _script_text("scripts/run_polymarket_dashboard_server.ps1")

    assert "[double]$MaxMemoryPercent = 99" in text
    assert "Get-MemoryUsedPercent" in text
    assert "$memoryUsedPercent -ge $MaxMemoryPercent" in text
    assert "skipped_high_memory" in text
    assert "polymarket_dashboard_server_status.json" in text
    assert 'Write-DashboardStatus "running"' in text
    assert "Dashboard server process started and is serving the generated dashboard artifacts." in text


def test_dashboard_task_installer_runs_wrapper_with_working_directory():
    text = _script_text("scripts/install_polymarket_dashboard_task.ps1")

    assert "run_polymarket_dashboard_server.ps1" in text
    assert "New-ScheduledTaskTrigger -AtLogOn" in text
    assert "-WorkingDirectory $RepoRoot" in text
    assert "-MultipleInstances IgnoreNew" in text
    assert "Start-ScheduledTask -TaskName $TaskName" in text


def test_manual_dashboard_launcher_uses_same_memory_guard():
    text = _script_text("scripts/start_polymarket_dashboard.ps1")

    assert "[double]$MaxMemoryPercent = 99" in text
    assert "Get-MemoryUsedPercent" in text
    assert "$memoryUsedPercent -ge $MaxMemoryPercent" in text
    assert "install_polymarket_dashboard_task.ps1 -StartNow" in text


def test_paper_maintenance_runner_is_lightweight_and_due_exit_aware():
    text = _script_text("scripts/run_polymarket_paper_maintenance.ps1")

    assert "[double]$MaxMemoryPercent = 99" in text
    assert "$env:PYTHONPATH = Join-Path $RepoRoot \"src\"" in text
    assert "polymarket_paper_maintenance_latest_status.json" in text
    assert "next_broker_exit_due_utc" in text
    assert "exit_due_by_clock" in text
    assert "Update-DashboardMaintenanceStatus" in text
    assert 'Add-Member -NotePropertyName "paper_maintenance"' in text
    assert "Update-TaskStatus" in text
    assert 'Add-Member -NotePropertyName "paper_maintenance_task"' in text
    assert "polymarket_paper_maintenance_task_status.json" in text
    assert "Read-JsonIfExists $TaskStatusPath" in text
    assert "runner = $PSCommandPath" in text
    assert "Write-JsonNoBom" in text
    assert "UTF8Encoding($false)" in text
    assert '"paper-trade"' in text
    assert "render_polymarket_dashboard.py" in text
    assert "skipped_high_memory" in text
    assert "Start-Process" in text


def test_paper_maintenance_task_installer_runs_every_minute_without_overlap():
    text = _script_text("scripts/install_polymarket_paper_maintenance_task.ps1")

    assert "run_polymarket_paper_maintenance.ps1" in text
    assert "polymarket_paper_maintenance_task_status.json" in text
    assert "[switch]$StartAtNextExitDue" in text
    assert "next_broker_exit_due_utc" in text
    assert "dashboard_next_broker_exit_due_utc" in text
    assert "-TaskName $QuotedTaskName" in text
    assert "[int]$IntervalMinutes = 1" in text
    assert "-RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)" in text
    assert "-MultipleInstances IgnoreNew" in text
    assert "Start-ScheduledTask -TaskName $TaskName" in text
