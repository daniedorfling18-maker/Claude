param(
  [string]$ConfigPath = "polymarket_predictive_config.example.yaml",
  [int]$WebsocketSeconds = 90,
  [int]$StepTimeoutSeconds = 180,
  [double]$MaxMemoryPercent = 99.0,
  [double]$TargetedLiquidityMemoryPercent = 95.0,
  [double]$DashboardOnlyMaxMemoryPercent = 99.5,
  [switch]$SkipDashboardOnHighMemory
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot
New-Item -ItemType Directory -Force .\work | Out-Null

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile = Join-Path $repoRoot "work\shadow_research_cycle_$stamp.log"
$statusFile = Join-Path $repoRoot "work\shadow_research_cycle_latest_status.json"

function Write-LogLine {
  param([string]$Message)
  $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
  $line | Tee-Object -FilePath $logFile -Append
}

function Get-MemorySnapshot {
  try {
    $os = Get-CimInstance Win32_OperatingSystem
    $totalKb = [double]$os.TotalVisibleMemorySize
    $freeKb = [double]$os.FreePhysicalMemory
    if ($totalKb -le 0) { return $null }
    $usedPercent = [math]::Round((1.0 - ($freeKb / $totalKb)) * 100.0, 1)
    return [pscustomobject]@{
      used_percent = $usedPercent
      free_gb = [math]::Round($freeKb / 1MB, 2)
      total_gb = [math]::Round($totalKb / 1MB, 2)
    }
  } catch {
    return $null
  }
}

$script:StoppedHighMemory = $false
$script:StoppedHighMemoryPhase = ""
$script:StoppedHighMemorySnapshot = $null
$script:FailedStep = ""
$script:FailedErrorType = ""

function Stop-ForHighMemory {
  param(
    [string]$Phase,
    [object]$MemorySnapshot
  )
  $script:StoppedHighMemory = $true
  $script:StoppedHighMemoryPhase = $Phase
  $script:StoppedHighMemorySnapshot = $MemorySnapshot
  $message = "Shadow research cycle stopped at $Phase because local memory was $($MemorySnapshot.used_percent)% at or above the $MaxMemoryPercent% guardrail."
  Write-LogLine $message
  throw $message
}

function Assert-MemoryBelowGuard {
  param([string]$Phase)
  $snapshot = Get-MemorySnapshot
  if ($snapshot) {
    Write-LogLine "Memory at $($Phase): $($snapshot.used_percent)% used, $($snapshot.free_gb) GB free"
    if ($snapshot.used_percent -ge $MaxMemoryPercent) {
      Stop-ForHighMemory -Phase $Phase -MemorySnapshot $snapshot
    }
  }
}

function Invoke-Step {
  param(
    [string]$Name,
    [string[]]$Arguments,
    [string]$OutFile,
    [switch]$SkipMemoryGuard
  )
  if (-not $SkipMemoryGuard) {
    Assert-MemoryBelowGuard -Phase "before_$Name"
  }
  Write-LogLine "=== $Name ==="
  $safeName = $Name -replace "[^A-Za-z0-9_.-]", "_"
  $stdoutPath = Join-Path $repoRoot "work\.$stamp.$safeName.stdout.tmp"
  $stderrPath = Join-Path $repoRoot "work\.$stamp.$safeName.stderr.tmp"
  Remove-Item $stdoutPath, $stderrPath -ErrorAction SilentlyContinue
  $process = Start-Process `
    -FilePath "python" `
    -ArgumentList $Arguments `
    -WorkingDirectory $repoRoot `
    -NoNewWindow `
    -PassThru `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath
  if (-not $process.WaitForExit($StepTimeoutSeconds * 1000)) {
    try { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue } catch {}
    $message = "$Name timed out after $StepTimeoutSeconds seconds"
    $script:FailedStep = $Name
    $script:FailedErrorType = "step_timeout"
    $message | Tee-Object -FilePath $OutFile | Tee-Object -FilePath $logFile -Append
    $endedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $timeoutStatus = [ordered]@{
      status = "error"
      started_at_utc = $startedAt
      ended_at_utc = $endedAt
      stamp = $stamp
      phase = $Name
      error = $message
      error_type = "step_timeout"
      runner_process_id = $PID
      log_file = $logFile
      paper_trading_invoked = $false
      live_trading_invoked = $false
    }
    $timeoutStatus | ConvertTo-Json -Depth 8 | Set-Content $statusFile -Encoding UTF8
    throw $message
  }
  $lines = @()
  if (Test-Path $stdoutPath) {
    $lines += Get-Content $stdoutPath
  }
  if (Test-Path $stderrPath) {
    $lines += Get-Content $stderrPath
  }
  $lines | Tee-Object -FilePath $OutFile | Tee-Object -FilePath $logFile -Append
  Remove-Item $stdoutPath, $stderrPath -ErrorAction SilentlyContinue
  $process.Refresh()
  $exitCode = [int]$process.ExitCode
  if ($exitCode -ne 0) {
    throw "$Name failed with exit code $exitCode"
  }
  if (-not $SkipMemoryGuard) {
    Assert-MemoryBelowGuard -Phase "after_$Name"
  }
}

$startedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$status = [ordered]@{
  status = "running"
  started_at_utc = $startedAt
  stamp = $stamp
  runner_process_id = $PID
  log_file = $logFile
  paper_trading_invoked = $false
  live_trading_invoked = $false
}
$status | ConvertTo-Json -Depth 8 | Set-Content $statusFile -Encoding UTF8

Write-LogLine "Starting shadow-only Polymarket research cycle"
Write-LogLine "Repo: $repoRoot"
Write-LogLine "Config: $ConfigPath"
Write-LogLine "Websocket seconds: $WebsocketSeconds"
Write-LogLine "Step timeout seconds: $StepTimeoutSeconds"
Write-LogLine "Memory guard: heavy cycle max $MaxMemoryPercent%; dashboard-only max $DashboardOnlyMaxMemoryPercent%"
Write-LogLine "Liquidity discovery mode threshold: targeted evidence at or above $TargetedLiquidityMemoryPercent% memory"

$env:PYTHONPATH = (Resolve-Path .\src).Path
$memory = Get-MemorySnapshot
if ($memory) {
  Write-LogLine "Memory before heavy cycle: $($memory.used_percent)% used, $($memory.free_gb) GB free"
}

if ($memory -and $memory.used_percent -ge $MaxMemoryPercent) {
  $endedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  $reason = "Shadow research cycle skipped before websocket/model work because local memory was $($memory.used_percent)% at or above the $MaxMemoryPercent% guardrail."
  $dashboardStatus = "pending_dashboard_refresh"
  $dashboardReason = "Heavy research was skipped on the memory guard; dashboard-only refresh has not completed yet."

  $status = [ordered]@{
    status = "skipped_high_memory"
    started_at_utc = $startedAt
    ended_at_utc = $endedAt
    stamp = $stamp
    reason = $reason
    memory_used_percent = $memory.used_percent
    memory_free_gb = $memory.free_gb
    memory_total_gb = $memory.total_gb
    max_memory_percent = $MaxMemoryPercent
    dashboard_status = $dashboardStatus
    dashboard_reason = $dashboardReason
    runner_process_id = $PID
    log_file = $logFile
    paper_trading_invoked = $false
    live_trading_invoked = $false
  }
  $status | ConvertTo-Json -Depth 8 | Set-Content $statusFile -Encoding UTF8

  if ($SkipDashboardOnHighMemory) {
    $dashboardStatus = "skipped_by_flag"
    $dashboardReason = "Dashboard-only refresh was disabled for this run."
  } elseif ($memory.used_percent -ge $DashboardOnlyMaxMemoryPercent) {
    $dashboardStatus = "skipped_critical_memory"
    $dashboardReason = "Dashboard-only refresh skipped because memory was at or above the $DashboardOnlyMaxMemoryPercent% critical dashboard guardrail."
  } else {
    try {
      Invoke-Step "render-dashboard" @(".\scripts\render_polymarket_dashboard.py", "--config", $ConfigPath) ".\work\render_dashboard_high_memory_$stamp.json" -SkipMemoryGuard
      $dashboardStatus = "refreshed_dashboard_only"
      $dashboardReason = "Heavy research was skipped, but the static dashboard was refreshed so oversight stays current."
    } catch {
      $dashboardStatus = "dashboard_refresh_failed"
      $dashboardReason = $_.Exception.Message
      Write-LogLine "Dashboard-only refresh failed: $dashboardReason"
    }
  }

  $status["dashboard_status"] = $dashboardStatus
  $status["dashboard_reason"] = $dashboardReason
  $status | ConvertTo-Json -Depth 8 | Set-Content $statusFile -Encoding UTF8
  Write-LogLine $reason
  Write-LogLine "Dashboard status on skip: $dashboardStatus"
  exit 0
}

# Hard safety guard: this scheduled research cycle must never run the local bot loop.
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
  Where-Object { $_.CommandLine -like "*run_polymarket_local_live_loop.py*" } |
  ForEach-Object {
    Write-LogLine "Stopping local live loop process id $($_.ProcessId) before research cycle"
    Stop-Process -Id $_.ProcessId -Force
  }

try {
  $liquidityMode = "full"
  $liquidityDiscoveryStatus = "not_run"
  $liquidityDiscoveryError = ""
  $liquidityArgs = @(".\scripts\run_polymarket_liquidity_discovery.py", "--config", $ConfigPath)
  $liquidityMemory = Get-MemorySnapshot
  if ($liquidityMemory -and $liquidityMemory.used_percent -ge $TargetedLiquidityMemoryPercent) {
    $liquidityMode = "targeted-evidence"
    $liquidityArgs += @("--mode", "targeted-evidence")
  }
  Write-LogLine "Liquidity discovery mode: $liquidityMode"
  try {
    Invoke-Step "liquidity-discovery" $liquidityArgs ".\work\liquidity_discovery_shadow_research_$stamp.json"
    $liquidityDiscoveryStatus = "ok"
  } catch {
    if ($script:StoppedHighMemory) {
      throw
    }
    $liquidityDiscoveryStatus = "degraded_non_blocking"
    $liquidityDiscoveryError = $_.Exception.Message
    $script:FailedStep = ""
    $script:FailedErrorType = ""
    Write-LogLine "WARNING: liquidity discovery did not complete ($liquidityDiscoveryError); continuing with latest available evidence so discovery is not a cycle bottleneck."
    $degradedStatus = [ordered]@{
      status = "running_degraded"
      started_at_utc = $startedAt
      stamp = $stamp
      phase = "after_liquidity-discovery"
      liquidity_discovery_status = $liquidityDiscoveryStatus
      liquidity_discovery_error = $liquidityDiscoveryError
      liquidity_discovery_mode = $liquidityMode
      runner_process_id = $PID
      log_file = $logFile
      paper_trading_invoked = $false
      live_trading_invoked = $false
    }
    $degradedStatus | ConvertTo-Json -Depth 8 | Set-Content $statusFile -Encoding UTF8
  }
  Invoke-Step "collect-websocket" @("-m", "polymarket_predictive_engine.cli", "collect-websocket", "--config", $ConfigPath, "--websocket-seconds", "$WebsocketSeconds") ".\work\collect_shadow_research_$stamp.json"
  Invoke-Step "normalize-websocket" @("-m", "polymarket_predictive_engine.cli", "normalize-websocket", "--config", $ConfigPath) ".\work\normalize_shadow_research_$stamp.json"
  Invoke-Step "build-features-v2" @("-m", "polymarket_predictive_engine.cli", "build-features-v2", "--config", $ConfigPath, "--source", "websocket", "--allow-unlabelled-research-features") ".\work\features_shadow_research_$stamp.json"
  Invoke-Step "predict" @("-m", "polymarket_predictive_engine.cli", "predict", "--config", $ConfigPath, "--source", "websocket") ".\work\predict_shadow_research_$stamp.json"
  Invoke-Step "score-mispricing-alpha" @("-m", "polymarket_predictive_engine.cli", "score-mispricing-alpha", "--config", $ConfigPath) ".\work\alpha_shadow_research_$stamp.json"
  Invoke-Step "generate-signals-dry" @("-m", "polymarket_predictive_engine.cli", "generate-signals", "--config", $ConfigPath) ".\work\signals_shadow_research_$stamp.json"
  Invoke-Step "alpha-candidate-shadow-evidence" @(".\scripts\run_alpha_candidate_shadow_evidence.py", $ConfigPath) ".\work\alpha_candidate_shadow_research_$stamp.json"
  Invoke-Step "local-history-audit" @(".\scripts\audit_polymarket_local_history.py", $ConfigPath) ".\work\local_history_audit_$stamp.json"
  Invoke-Step "profit-sprint" @("-m", "polymarket_predictive_engine.cli", "profit-sprint", "--config", $ConfigPath) ".\work\profit_sprint_shadow_research_$stamp.json"
  Invoke-Step "price-action-microstructure" @("-m", "polymarket_predictive_engine.cli", "price-action-microstructure", "--config", $ConfigPath) ".\work\price_action_microstructure_shadow_research_$stamp.json"
  Invoke-Step "price-action-scout" @("-m", "polymarket_predictive_engine.cli", "price-action-scout", "--config", $ConfigPath) ".\work\price_action_scout_shadow_research_$stamp.json"
  Invoke-Step "refresh-governance" @("-m", "polymarket_predictive_engine.refresh_governance", "--config", $ConfigPath) ".\work\governance_refresh_shadow_research_$stamp.json"

  Copy-Item ".\work\local_history_audit_$stamp.json" ".\work\local_history_audit_latest.json" -Force
  $audit = Get-Content ".\work\local_history_audit_$stamp.json" -Raw | ConvertFrom-Json
  $governanceRefresh = Get-Content ".\work\governance_refresh_shadow_research_$stamp.json" -Raw | ConvertFrom-Json
  $liquiditySummaryPath = ".\outputs\polymarket_model_governance\liquidity_discovery_summary.json"
  $liquidity = $null
  if (Test-Path $liquiditySummaryPath) {
    $liquidity = Get-Content $liquiditySummaryPath -Raw | ConvertFrom-Json
  }
  $endedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  $status = [ordered]@{
    status = "ok"
    started_at_utc = $startedAt
    ended_at_utc = $endedAt
    stamp = $stamp
    paper_allowed = $audit.paper_decision.paper_allowed
    paper_reason = $audit.paper_decision.reason
    approved_signals = $audit.approved_signals
    rejected_signals = $audit.rejected_signals
    shadow_positions = $audit.shadow_positions
    shadow_total_pnl_usdc = $audit.shadow_total_pnl_usdc
    shadow_roi = $audit.shadow_roi
    liquidity_tradable_tokens = if ($liquidity) { $liquidity.tradable_tokens } else { $null }
    liquidity_fast_feedback_tradable_tokens = if ($liquidity) { $liquidity.fast_feedback_tradable_tokens } else { $null }
    liquidity_discovery_mode = $liquidityMode
    liquidity_discovery_status = $liquidityDiscoveryStatus
    liquidity_discovery_error = $liquidityDiscoveryError
    log_file = $logFile
    audit_file = ".\work\local_history_audit_$stamp.json"
    report_file = ".\outputs\polymarket_model_governance\local_history_audit_report.md"
    liquidity_report_file = ".\outputs\polymarket_model_governance\liquidity_discovery_summary.json"
    profit_sprint_file = ".\work\profit_sprint_shadow_research_$stamp.json"
    price_action_microstructure_file = ".\work\price_action_microstructure_shadow_research_$stamp.json"
    price_action_scout_file = ".\work\price_action_scout_shadow_research_$stamp.json"
    governance_refresh_file = ".\work\governance_refresh_shadow_research_$stamp.json"
    price_action_model_decision = $governanceRefresh.price_action_model_decision
    price_action_model_promotion_ready = $governanceRefresh.price_action_model_promotion_ready
    price_action_feedback_state = $governanceRefresh.price_action_feedback_state
    research_focus_status = $governanceRefresh.research_focus_status
    approved_for_paper_trading = $governanceRefresh.approved_for_paper_trading
    dashboard_status = $governanceRefresh.dashboard.status
    runner_process_id = $PID
    paper_trading_invoked = $false
    live_trading_invoked = $false
  }
  $status | ConvertTo-Json -Depth 8 | Set-Content $statusFile -Encoding UTF8
  $finalDashboardStatus = "not_attempted_after_final_status"
  $finalDashboardReason = "Final dashboard refresh after writing the completed cycle status was not attempted."
  $finalDashboardMemory = Get-MemorySnapshot
  if ($finalDashboardMemory -and $finalDashboardMemory.used_percent -ge $DashboardOnlyMaxMemoryPercent) {
    $finalDashboardStatus = "skipped_critical_memory_after_final_status"
    $finalDashboardReason = "Final dashboard refresh skipped because memory was at or above the $DashboardOnlyMaxMemoryPercent% critical dashboard guardrail."
    Write-LogLine $finalDashboardReason
  } else {
    try {
      Invoke-Step "render-dashboard-final-status" @(".\scripts\render_polymarket_dashboard.py", "--config", $ConfigPath) ".\work\render_dashboard_final_status_$stamp.json" -SkipMemoryGuard
      $finalDashboardStatus = "refreshed_after_final_status"
      $finalDashboardReason = "Dashboard refreshed after the final cycle status was written, so the cockpit does not display a stale running state."
    } catch {
      $finalDashboardStatus = "dashboard_refresh_failed_after_final_status"
      $finalDashboardReason = $_.Exception.Message
      Write-LogLine "Dashboard refresh after final status failed: $finalDashboardReason"
    }
  }
  $status["dashboard_status"] = $finalDashboardStatus
  $status["dashboard_reason"] = $finalDashboardReason
  $status | ConvertTo-Json -Depth 8 | Set-Content $statusFile -Encoding UTF8
  Write-LogLine "Cycle completed. Paper allowed: $($audit.paper_decision.paper_allowed). Reason: $($audit.paper_decision.reason)"
  exit 0
} catch {
  $endedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  if ($script:StoppedHighMemory) {
    $memory = $script:StoppedHighMemorySnapshot
    $dashboardStatus = "pending_dashboard_refresh_after_memory_stop"
    $dashboardReason = "Heavy research stopped on the memory guard; dashboard-only refresh has not completed yet."
    $status = [ordered]@{
      status = "stopped_high_memory"
      started_at_utc = $startedAt
      ended_at_utc = $endedAt
      stamp = $stamp
      phase = $script:StoppedHighMemoryPhase
      reason = $_.Exception.Message
      memory_used_percent = if ($memory) { $memory.used_percent } else { $null }
      memory_free_gb = if ($memory) { $memory.free_gb } else { $null }
      memory_total_gb = if ($memory) { $memory.total_gb } else { $null }
      max_memory_percent = $MaxMemoryPercent
      dashboard_status = $dashboardStatus
      dashboard_reason = $dashboardReason
      runner_process_id = $PID
      log_file = $logFile
      paper_trading_invoked = $false
      live_trading_invoked = $false
    }
    $status | ConvertTo-Json -Depth 8 | Set-Content $statusFile -Encoding UTF8

    if ($SkipDashboardOnHighMemory) {
      $dashboardStatus = "skipped_by_flag"
      $dashboardReason = "Dashboard-only refresh was disabled for this run."
    } elseif ($memory -and $memory.used_percent -lt $DashboardOnlyMaxMemoryPercent) {
      try {
        Invoke-Step "render-dashboard-after-memory-stop" @(".\scripts\render_polymarket_dashboard.py", "--config", $ConfigPath) ".\work\render_dashboard_after_memory_stop_$stamp.json" -SkipMemoryGuard
        $dashboardStatus = "refreshed_dashboard_only_after_memory_stop"
        $dashboardReason = "Heavy research stopped on the memory guard, but the static dashboard was refreshed so oversight stays current."
      } catch {
        $dashboardStatus = "dashboard_refresh_failed_after_memory_stop"
        $dashboardReason = $_.Exception.Message
        Write-LogLine "Dashboard refresh after memory stop failed: $dashboardReason"
      }
    } elseif ($memory -and $memory.used_percent -ge $DashboardOnlyMaxMemoryPercent) {
      $dashboardStatus = "skipped_critical_memory"
      $dashboardReason = "Dashboard-only refresh skipped because memory was at or above the $DashboardOnlyMaxMemoryPercent% critical dashboard guardrail."
    }
    $status["dashboard_status"] = $dashboardStatus
    $status["dashboard_reason"] = $dashboardReason
    $status | ConvertTo-Json -Depth 8 | Set-Content $statusFile -Encoding UTF8
    Write-LogLine "Stopped cleanly due to memory guard at phase $script:StoppedHighMemoryPhase"
    exit 0
  }
  $status = [ordered]@{
    status = "error"
    started_at_utc = $startedAt
    ended_at_utc = $endedAt
    stamp = $stamp
    phase = $script:FailedStep
    error = $_.Exception.Message
    error_type = $script:FailedErrorType
    runner_process_id = $PID
    log_file = $logFile
    paper_trading_invoked = $false
    live_trading_invoked = $false
  }
  $status | ConvertTo-Json -Depth 8 | Set-Content $statusFile -Encoding UTF8
  Write-LogLine "ERROR: $($_.Exception.Message)"
  exit 1
}

