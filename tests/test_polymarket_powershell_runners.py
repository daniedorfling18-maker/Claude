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

    assert "[double]$MaxMemoryPercent = 94" in text
    guard_index = text.index("skipped_high_memory")
    shadow_start_index = text.index("$shadowRefreshProcess.Start()")

    assert "Get-MemoryUsedPercent" in text
    assert "$memoryUsedPercent -ge $MaxMemoryPercent" in text
    assert guard_index < shadow_start_index


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
    round_trip_index = text.index("strategy-v2-round-trip")
    status_payload_index = text.index("strategy_v2_round_trip_evidence = $strategyV2RoundTripEvidence")

    assert normalize_index < round_trip_index
    assert round_trip_index < status_payload_index
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
    paper_signal_index = text.index("price-action-paper-signals")
    status_payload_index = text.index("price_action_paper_signals = $priceActionPaperSignals")

    assert scout_index < paper_signal_index
    assert paper_signal_index < status_payload_index
    assert "price_action_paper_signal_summary.json" in text


def test_strategy_v2_scheduled_wrapper_pins_repo_source():
    text = _script_text("scripts/run_strategy_v2_cycle_scheduled_wrapper.ps1")

    assert "(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot \"..\"))" in text
    assert "C:\\Users" not in text
    assert '$env:PYTHONPATH = Join-Path $repoRoot "src"' in text
    assert "strategy_v2_scheduled_task_$runId.log" in text


def test_strategy_v2_scheduled_wrapper_skips_before_cycle_when_memory_is_high():
    text = _script_text("scripts/run_strategy_v2_cycle_scheduled_wrapper.ps1")

    assert "[double]$MaxMemoryPercent = 95" in text
    assert "Get-MemoryUsedPercent" in text
    assert "$memoryUsedPercent -ge $MaxMemoryPercent" in text
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


def test_dashboard_server_runner_refuses_to_start_when_memory_is_high():
    text = _script_text("scripts/run_polymarket_dashboard_server.ps1")

    assert "[double]$MaxMemoryPercent = 95" in text
    assert "Get-MemoryUsedPercent" in text
    assert "$memoryUsedPercent -ge $MaxMemoryPercent" in text
    assert "skipped_high_memory" in text
    assert "polymarket_dashboard_server_status.json" in text


def test_dashboard_task_installer_runs_wrapper_with_working_directory():
    text = _script_text("scripts/install_polymarket_dashboard_task.ps1")

    assert "run_polymarket_dashboard_server.ps1" in text
    assert "New-ScheduledTaskTrigger -AtLogOn" in text
    assert "-WorkingDirectory $RepoRoot" in text
    assert "-MultipleInstances IgnoreNew" in text
    assert "Start-ScheduledTask -TaskName $TaskName" in text


def test_manual_dashboard_launcher_uses_same_memory_guard():
    text = _script_text("scripts/start_polymarket_dashboard.ps1")

    assert "[double]$MaxMemoryPercent = 95" in text
    assert "Get-MemoryUsedPercent" in text
    assert "$memoryUsedPercent -ge $MaxMemoryPercent" in text
    assert "install_polymarket_dashboard_task.ps1 -StartNow" in text
