param(
  [string]$ConfigPath = "polymarket_predictive_config.example.yaml",
  [int]$StepTimeoutSeconds = 180,
  [int]$IndependentAnchorMaxAgeMinutes = 60,
  [double]$MaxMemoryPercent = 94
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

function Invoke-PythonStep {
  param(
    [string]$Name,
    [string[]]$Arguments
  )
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

$memoryUsedPercent = Get-MemoryUsedPercent
if ($memoryUsedPercent -ge $MaxMemoryPercent) {
  $status = [PSCustomObject]@{
    status = "skipped_high_memory"
    started_at_utc = $started
    ended_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    memory_used_percent = $memoryUsedPercent
    max_memory_percent = $MaxMemoryPercent
    reason = "Strategy V2 cycle skipped before starting heavy work because local memory was at or above the guardrail."
    anchored_rows = $null
    shadow_candidates = $null
    rejected_anchored_rows = $null
    active_shadow_candidates = @()
    paper_trading_invoked = $false
    live_trading_invoked = $false
  }
  $status |
    ConvertTo-Json -Depth 8 |
    Set-Content .\work\strategy_v2_cycle_latest_status.json -Encoding UTF8
  "`n=== STRATEGY V2 CYCLE SKIPPED: HIGH MEMORY ==="
  $status | Format-List
  exit 0
}

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







