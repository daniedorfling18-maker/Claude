param(
  [string]$ConfigPath = "polymarket_predictive_config.example.yaml",
  [int]$StepTimeoutSeconds = 180,
  [int]$IndependentAnchorMaxAgeMinutes = 60,
  [int]$PostEvidenceWebsocketSeconds = 20,
  [double]$MaxMemoryPercent = 94,
  [double]$MaintenanceMaxMemoryPercent = 98.5
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot
$env:PYTHONPATH = (Resolve-Path .\src).Path

New-Item -ItemType Directory -Force .\outputs\polymarket_strategy_v2 | Out-Null
New-Item -ItemType Directory -Force .\work | Out-Null

$started = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

# Refresh the underlying Polymarket market/prediction snapshot before rescoring Strategy V2.
$predictionSnapshotPath = ".\outputs\polymarket_predictions\predictions.csv"
$predictionSnapshotBeforeUtc = $null
if (Test-Path $predictionSnapshotPath) {
  $predictionSnapshotBeforeUtc = (Get-Item $predictionSnapshotPath).LastWriteTimeUtc.ToString("yyyy-MM-ddTHH:mm:ssZ")
}

$shadowRefreshScript = Join-Path $repoRoot "scripts\run_polymarket_shadow_research_cycle.ps1"
$shadowStdoutPath = Join-Path $repoRoot "work\strategy_v2_shadow_refresh_stdout.log"
$shadowStderrPath = Join-Path $repoRoot "work\strategy_v2_shadow_refresh_stderr.log"
Remove-Item $shadowStdoutPath, $shadowStderrPath -ErrorAction SilentlyContinue

function Quote-ForPowerShellCommand {
  param([string]$Value)
  "'" + ($Value -replace "'", "''") + "'"
}

function Test-FreshFile {
  param(
    [string]$Path,
    [int]$MaxAgeMinutes
  )
  if ($MaxAgeMinutes -le 0) {
    return $false
  }
  if (-not (Test-Path $Path)) {
    return $false
  }
  $ageMinutes = ((Get-Date).ToUniversalTime() - (Get-Item $Path).LastWriteTimeUtc).TotalMinutes
  return ($ageMinutes -lt $MaxAgeMinutes)
}

function Read-JsonIfExists {
  param([string]$Path)
  if (-not (Test-Path $Path)) {
    return $null
  }
  try {
    return Get-Content $Path -Raw | ConvertFrom-Json
  } catch {
    return [PSCustomObject]@{
      status = "unreadable"
      path = $Path
      error = $_.Exception.Message
    }
  }
}

function Get-MemoryUsedPercent {
  $os = Get-CimInstance Win32_OperatingSystem
  return [math]::Round((($os.TotalVisibleMemorySize - $os.FreePhysicalMemory) / $os.TotalVisibleMemorySize) * 100, 1)
}

function Invoke-LowMemoryMaintenance {
  param([double]$MemoryUsedPercent)
  if ($MaintenanceMaxMemoryPercent -le 0 -or $MemoryUsedPercent -ge $MaintenanceMaxMemoryPercent) {
    return [PSCustomObject]@{
      status = "skipped_high_memory"
      memory_used_percent = $MemoryUsedPercent
      maintenance_max_memory_percent = $MaintenanceMaxMemoryPercent
      reason = "Paper broker/dashboard maintenance was skipped because memory was above the maintenance guardrail."
    }
  }

  $stdoutPath = Join-Path $repoRoot "work\strategy_v2_low_memory_paper_trade.stdout.log"
  $stderrPath = Join-Path $repoRoot "work\strategy_v2_low_memory_paper_trade.stderr.log"
  Remove-Item $stdoutPath, $stderrPath -ErrorAction SilentlyContinue
  try {
    $process = Start-Process `
      -FilePath "python" `
      -ArgumentList @("-m", "polymarket_predictive_engine.cli", "paper-trade", "--config", $ConfigPath) `
      -WorkingDirectory $repoRoot `
      -PassThru `
      -WindowStyle Hidden `
      -RedirectStandardOutput $stdoutPath `
      -RedirectStandardError $stderrPath

    $maintenanceTimeoutMs = [int]([math]::Max(30, [math]::Min($StepTimeoutSeconds, 90)) * 1000)
    if (-not $process.WaitForExit($maintenanceTimeoutMs)) {
      try { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue } catch {}
      return [PSCustomObject]@{
        status = "timed_out"
        memory_used_percent = $MemoryUsedPercent
        stdout_log = $stdoutPath
        stderr_log = $stderrPath
        reason = "Low-memory paper broker/dashboard maintenance timed out."
      }
    }
    $process.Refresh()
    if ([int]$process.ExitCode -ne 0) {
      return [PSCustomObject]@{
        status = "error"
        exit_code = [int]$process.ExitCode
        memory_used_percent = $MemoryUsedPercent
        stdout_log = $stdoutPath
        stderr_log = $stderrPath
        reason = "Low-memory paper broker/dashboard maintenance failed."
      }
    }
    return [PSCustomObject]@{
      status = "ran"
      memory_used_percent = $MemoryUsedPercent
      stdout_log = $stdoutPath
      stderr_log = $stderrPath
      paper_trade_refresh = Read-JsonIfExists ".\outputs\polymarket_model_governance\paper_trade_refresh.json"
    }
  } catch {
    return [PSCustomObject]@{
      status = "error"
      memory_used_percent = $MemoryUsedPercent
      stdout_log = $stdoutPath
      stderr_log = $stderrPath
      reason = $_.Exception.Message
    }
  }
}

function Stop-StrategyCycleForHighMemory {
  param(
    [string]$Phase,
    [double]$MemoryUsedPercent
  )
  $statusName = if ($Phase -eq "before_shadow_refresh") { "skipped_high_memory" } else { "stopped_high_memory" }
  $reason = if ($Phase -eq "before_shadow_refresh") {
    "Strategy V2 cycle skipped before starting heavy work because local memory was at or above the guardrail."
  } else {
    "Strategy V2 cycle stopped before launching the next step because local memory was at or above the guardrail."
  }
  $maintenance = Invoke-LowMemoryMaintenance -MemoryUsedPercent $MemoryUsedPercent
  $status = [PSCustomObject]@{
    status = $statusName
    started_at_utc = $started
    ended_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    phase = $Phase
    memory_used_percent = $MemoryUsedPercent
    max_memory_percent = $MaxMemoryPercent
    reason = $reason
    anchored_rows = $null
    shadow_candidates = $null
    rejected_anchored_rows = $null
    active_shadow_candidates = @()
    paper_trading_invoked = ($maintenance.status -eq "ran")
    live_trading_invoked = $false
    low_memory_maintenance = $maintenance
  }
  $status |
    ConvertTo-Json -Depth 8 |
    Set-Content .\work\strategy_v2_cycle_latest_status.json -Encoding UTF8
  "`n=== STRATEGY V2 CYCLE STOPPED: HIGH MEMORY ==="
  $status | Format-List
  exit 0
}

function Assert-MemoryBelowGuard {
  param([string]$Phase)
  if ($MaxMemoryPercent -le 0) {
    return
  }
  $memoryUsedPercent = Get-MemoryUsedPercent
  if ($memoryUsedPercent -ge $MaxMemoryPercent) {
    Stop-StrategyCycleForHighMemory -Phase $Phase -MemoryUsedPercent $memoryUsedPercent
  }
}

function Invoke-PythonStep {
  param(
    [string]$Name,
    [string[]]$Arguments
  )
  Assert-MemoryBelowGuard -Phase "before_$Name"
  $safeName = $Name -replace "[^A-Za-z0-9_.-]", "_"
  $stdoutPath = Join-Path $repoRoot "work\strategy_v2_$safeName.stdout.log"
  $stderrPath = Join-Path $repoRoot "work\strategy_v2_$safeName.stderr.log"
  Remove-Item $stdoutPath, $stderrPath -ErrorAction SilentlyContinue

  $process = Start-Process `
    -FilePath "python" `
    -ArgumentList $Arguments `
    -WorkingDirectory $repoRoot `
    -PassThru `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath

  if (-not $process.WaitForExit($StepTimeoutSeconds * 1000)) {
    try { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue } catch {}
    throw "$Name timed out after $StepTimeoutSeconds seconds"
  }

  $process.Refresh()
  $exitCode = [int]$process.ExitCode
  if ($exitCode -ne 0) {
    $output = ""
    if (Test-Path $stdoutPath) {
      $output += (Get-Content $stdoutPath -Raw)
    }
    if (Test-Path $stderrPath) {
      $output += (Get-Content $stderrPath -Raw)
    }
    throw "$Name failed with exit code $exitCode. Output: $output"
  }
}

Assert-MemoryBelowGuard -Phase "before_shadow_refresh"

$shadowCommand = "& $(Quote-ForPowerShellCommand $shadowRefreshScript) -ConfigPath $(Quote-ForPowerShellCommand $ConfigPath) -WebsocketSeconds 30 *> $(Quote-ForPowerShellCommand $shadowStdoutPath)"
$shadowCommandArg = '"' + ($shadowCommand -replace '"', '\"') + '"'

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$psi.Arguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command $shadowCommandArg"
$psi.WorkingDirectory = $repoRoot
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden

$shadowRefreshProcess = New-Object System.Diagnostics.Process
$shadowRefreshProcess.StartInfo = $psi

$startedProcess = $shadowRefreshProcess.Start()
if (-not $startedProcess) {
  throw "Failed to start hidden shadow refresh process"
}

$shadowRefreshProcess.WaitForExit()

if ($shadowRefreshProcess.ExitCode -ne 0) {
  $shadowOutput = ""
  if (Test-Path $shadowStdoutPath) {
    $shadowOutput = Get-Content $shadowStdoutPath -Raw
  }
  throw "Shadow research refresh failed with exit code $($shadowRefreshProcess.ExitCode). Output: $shadowOutput"
}

$shadowStatusPath = Join-Path $repoRoot "work\shadow_research_cycle_latest_status.json"
if (-not (Test-Path $shadowStatusPath)) {
  throw "Shadow research refresh did not write $shadowStatusPath"
}

$shadowStatus = Get-Content $shadowStatusPath -Raw | ConvertFrom-Json

if ($shadowStatus.status -ne "ok") {
  throw "Shadow research refresh status was $($shadowStatus.status)"
}

if ([datetime]$shadowStatus.started_at_utc -lt [datetime]$started) {
  throw "Shadow research refresh status is stale: $($shadowStatus.started_at_utc) before $started"
}

Assert-MemoryBelowGuard -Phase "after_shadow_refresh"

$predictionSnapshotAfterUtc = $null
if (Test-Path $predictionSnapshotPath) {
  $predictionSnapshotAfterUtc = (Get-Item $predictionSnapshotPath).LastWriteTimeUtc.ToString("yyyy-MM-ddTHH:mm:ssZ")
}

$independentAnchorRefresh = [ordered]@{
  max_age_minutes = $IndependentAnchorMaxAgeMinutes
  sharp_anchor_step = "skipped_fresh"
  crypto_targets_step = "skipped_fresh"
  crypto_fundamental_step = "skipped_fresh"
}

$sharpFundamentalPath = ".\outputs\polymarket_training\sharp_fundamental_probabilities.csv"
if (-not (Test-FreshFile $sharpFundamentalPath $IndependentAnchorMaxAgeMinutes)) {
  Invoke-PythonStep "refresh-sharp-anchor" @("-m", "polymarket_predictive_engine.cli", "refresh-sharp-anchor", "--config", $ConfigPath)
  $independentAnchorRefresh.sharp_anchor_step = "refreshed"
}

$independentAnchorRefresh.sharp_odds_fetch = Read-JsonIfExists ".\outputs\polymarket_model_governance\sharp_odds_fetch_summary.json"
$independentAnchorRefresh.sharp_anchor = Read-JsonIfExists ".\outputs\polymarket_model_governance\sharp_anchor_summary.json"

$cryptoTargetsPath = ".\inputs\polymarket\crypto_targets.csv"
if (-not (Test-FreshFile $cryptoTargetsPath $IndependentAnchorMaxAgeMinutes)) {
  Invoke-PythonStep "build-crypto-targets" @("-m", "polymarket_predictive_engine.cli", "build-crypto-targets", "--config", $ConfigPath)
  $independentAnchorRefresh.crypto_targets_step = "refreshed"
}
$independentAnchorRefresh.crypto_targets = Read-JsonIfExists ".\outputs\polymarket_model_governance\crypto_targets_summary.json"

$cryptoFundamentalPath = ".\outputs\polymarket_training\crypto_fundamental_probabilities.csv"
if (-not (Test-FreshFile $cryptoFundamentalPath $IndependentAnchorMaxAgeMinutes)) {
  Invoke-PythonStep "build-crypto-fundamental" @("-m", "polymarket_predictive_engine.cli", "build-crypto-fundamental", "--config", $ConfigPath)
  $independentAnchorRefresh.crypto_fundamental_step = "refreshed"
}
$independentAnchorRefresh.crypto_fundamental = Read-JsonIfExists ".\outputs\polymarket_model_governance\crypto_fundamental_summary.json"


# Refresh the governance/pipeline state before rescoring Strategy V2.

Invoke-PythonStep "refresh-governance" @("-m", "polymarket_predictive_engine.cli", "refresh-governance", "--config", $ConfigPath)
Invoke-PythonStep "validation-report" @("-m", "polymarket_predictive_engine.cli", "validation-report", "--config", $ConfigPath)

.\scripts\run_polymarket_opportunity_audit.ps1 -ConfigPath $ConfigPath | Out-Null
.\scripts\run_polymarket_strategy_v2_anchored_edge.ps1 -ConfigPath $ConfigPath | Out-Null

$logPath = ".\outputs\polymarket_strategy_v2\anchored_edge_persistence_log.csv"

$anchoredRows = Import-Csv .\outputs\polymarket_strategy_v2\anchored_edge_candidates.csv |
  Where-Object { $_.anchor_fair_probability -ne "" } |
  Select-Object `
    @{Name="logged_at_utc"; Expression={(Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")}},
    family,
    market_slug,
    outcome,
    token_id,
    status,
    blockers,
    anchor_fair_probability,
    executable_price,
    anchor_raw_edge,
    spread,
    spread_penalty,
    liquidity,
    liquidity_penalty,
    uncertainty_penalty,
    risk_adjusted_anchor_edge

if (Test-Path $logPath) {
  $anchoredRows | Export-Csv $logPath -NoTypeInformation -Append
} else {
  $anchoredRows | Export-Csv $logPath -NoTypeInformation
}

Invoke-PythonStep "strategy-v2-evidence" @("-m", "polymarket_predictive_engine.cli", "strategy-v2-evidence", "--config", $ConfigPath)
$strategyV2ForwardEvidence = Read-JsonIfExists ".\outputs\polymarket_strategy_v2\strategy_v2_forward_evidence.json"
Invoke-PythonStep "strategy-v2-websocket-refresh" @("-m", "polymarket_predictive_engine.cli", "collect-websocket", "--config", $ConfigPath, "--websocket-seconds", "$PostEvidenceWebsocketSeconds")
$strategyV2WebsocketRefresh = Read-JsonIfExists ".\outputs\polymarket_websocket\websocket_summary.json"
Invoke-PythonStep "strategy-v2-normalize-websocket" @("-m", "polymarket_predictive_engine.cli", "normalize-websocket", "--config", $ConfigPath)
$strategyV2WebsocketFeatures = Read-JsonIfExists ".\outputs\polymarket_model_governance\websocket_feature_summary.json"
Invoke-PythonStep "price-action-microstructure" @("-m", "polymarket_predictive_engine.cli", "price-action-microstructure", "--config", $ConfigPath)
$priceActionMicrostructure = Read-JsonIfExists ".\outputs\polymarket_price_action\microstructure_summary.json"
Invoke-PythonStep "strategy-v2-round-trip" @("-m", "polymarket_predictive_engine.cli", "strategy-v2-round-trip", "--config", $ConfigPath)
$strategyV2RoundTripEvidence = Read-JsonIfExists ".\outputs\polymarket_strategy_v2\strategy_v2_round_trip_evidence.json"
Invoke-PythonStep "price-action-scout" @("-m", "polymarket_predictive_engine.cli", "price-action-scout", "--config", $ConfigPath)
$priceActionScout = Read-JsonIfExists ".\outputs\polymarket_price_action\price_action_scout_summary.json"
Invoke-PythonStep "price-action-feedback" @("-m", "polymarket_predictive_engine.cli", "price-action-feedback", "--config", $ConfigPath)
$priceActionFeedbackBeforePaperSignals = Read-JsonIfExists ".\outputs\polymarket_model_governance\price_action_feedback.json"
Invoke-PythonStep "price-action-paper-signals" @("-m", "polymarket_predictive_engine.cli", "price-action-paper-signals", "--config", $ConfigPath)
$priceActionPaperSignals = Read-JsonIfExists ".\outputs\polymarket_price_action\price_action_paper_signal_summary.json"
Invoke-PythonStep "paper-trade" @("-m", "polymarket_predictive_engine.cli", "paper-trade", "--config", $ConfigPath)
$paperTradeRefresh = Read-JsonIfExists ".\outputs\polymarket_model_governance\paper_trade_refresh.json"
Invoke-PythonStep "price-action-feedback" @("-m", "polymarket_predictive_engine.cli", "price-action-feedback", "--config", $ConfigPath)
$priceActionFeedback = Read-JsonIfExists ".\outputs\polymarket_model_governance\price_action_feedback.json"

$shadowCandidates = $anchoredRows | Where-Object { $_.status -eq "shadow_candidate" }
$rejectedAnchored = $anchoredRows | Where-Object { $_.status -eq "rejected" }

$status = [PSCustomObject]@{
  status = "ok"
  started_at_utc = $started
  ended_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  prediction_snapshot_before_utc = $predictionSnapshotBeforeUtc
  prediction_snapshot_after_utc = $predictionSnapshotAfterUtc
  anchored_rows = @($anchoredRows).Count
  shadow_candidates = @($shadowCandidates).Count
  rejected_anchored_rows = @($rejectedAnchored).Count
  active_shadow_candidates = @($shadowCandidates | Select-Object family, market_slug, outcome, executable_price, anchor_fair_probability, risk_adjusted_anchor_edge)
  persistence_log = $logPath
  strategy_v2_forward_evidence = $strategyV2ForwardEvidence
  strategy_v2_websocket_refresh = $strategyV2WebsocketRefresh
  strategy_v2_websocket_features = $strategyV2WebsocketFeatures
  price_action_microstructure = $priceActionMicrostructure
  strategy_v2_round_trip_evidence = $strategyV2RoundTripEvidence
  price_action_scout = $priceActionScout
  price_action_feedback_before_paper_signals = $priceActionFeedbackBeforePaperSignals
  price_action_paper_signals = $priceActionPaperSignals
  paper_trade_refresh = $paperTradeRefresh
  price_action_feedback = $priceActionFeedback
  independent_anchor_refresh = $independentAnchorRefresh
}

$status |
  ConvertTo-Json -Depth 8 |
  Set-Content .\work\strategy_v2_cycle_latest_status.json -Encoding UTF8

Invoke-PythonStep "render-dashboard" @(".\scripts\render_polymarket_dashboard.py", "--config", $ConfigPath)

"`n=== STRATEGY V2 CYCLE COMPLETE ==="
$status | Format-List

"`n=== ACTIVE SHADOW CANDIDATES ==="
$shadowCandidates |
  Select-Object family, market_slug, outcome, executable_price, anchor_fair_probability, risk_adjusted_anchor_edge, spread, liquidity |
  Format-List







