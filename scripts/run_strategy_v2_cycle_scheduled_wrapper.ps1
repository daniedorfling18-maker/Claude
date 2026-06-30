param(
  [double]$MaxMemoryPercent = 95
)

$ErrorActionPreference = "Continue"

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$workRoot = Join-Path $repoRoot "work"
$logPath = Join-Path $workRoot "strategy_v2_scheduled_task.log"
$latestStatusPath = Join-Path $workRoot "strategy_v2_cycle_latest_status.json"
$runId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ") + "_pid$PID"
$runLogPath = Join-Path $workRoot "strategy_v2_scheduled_task_$runId.log"

New-Item -ItemType Directory -Force $workRoot | Out-Null

function Write-StrategyLog {
  param([string]$Message)
  $Message | Out-File $runLogPath -Append -Encoding UTF8
  try {
    $Message | Out-File $logPath -Append -Encoding UTF8 -ErrorAction Stop
  } catch {
    # The shared log is best-effort only. A per-run log is always used so log
    # contention never blocks the trading research cycle.
  }
}

function Write-StrategyStatus {
  param([PSCustomObject]$Status)
  $Status | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $latestStatusPath -Encoding UTF8
}

function Get-MemoryUsedPercent {
  $os = Get-CimInstance Win32_OperatingSystem
  return [math]::Round((($os.TotalVisibleMemorySize - $os.FreePhysicalMemory) / $os.TotalVisibleMemorySize) * 100, 1)
}

$mutex = New-Object System.Threading.Mutex($false, "Global\PolymarketStrategyV2Cycle")
$lockTaken = $false

try {
  $lockTaken = $mutex.WaitOne(0)
  if (-not $lockTaken) {
    $status = [PSCustomObject]@{
      status = "skipped_already_running"
      started_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
      ended_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
      reason = "Strategy V2 scheduled wrapper skipped because another cycle is already running."
      run_log = $runLogPath
    }
    Write-StrategyStatus $status
    Write-StrategyLog "=== Strategy V2 scheduled run skipped: already running $(Get-Date -Format o) ==="
    exit 0
  }

  Write-StrategyLog "=== Strategy V2 scheduled run started $(Get-Date -Format o) ==="

  $memoryUsedPercent = Get-MemoryUsedPercent
  if ($MaxMemoryPercent -gt 0 -and $memoryUsedPercent -ge $MaxMemoryPercent) {
    $status = [PSCustomObject]@{
      status = "skipped_high_memory"
      started_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
      ended_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
      memory_used_percent = $memoryUsedPercent
      max_memory_percent = $MaxMemoryPercent
      reason = "Strategy V2 scheduled wrapper skipped before invoking the cycle because local memory was at or above the guardrail."
      run_log = $runLogPath
    }
    Write-StrategyStatus $status
    Write-StrategyLog "=== Strategy V2 scheduled run skipped for high memory: $memoryUsedPercent% / $MaxMemoryPercent% $(Get-Date -Format o) ==="
    exit 0
  }

  Set-Location $repoRoot
  $env:PYTHONPATH = Join-Path $repoRoot "src"
  & "$repoRoot\scripts\run_polymarket_strategy_v2_cycle.ps1" *>> $runLogPath
  $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
  if ($exitCode -ne 0) {
    throw "Strategy V2 cycle exited with code $exitCode"
  }
  Write-StrategyLog "=== Strategy V2 scheduled run completed $(Get-Date -Format o) ==="
  exit 0
} catch {
  Write-StrategyLog "=== Strategy V2 scheduled run failed $(Get-Date -Format o) ==="
  $_ | Out-String | Out-File $runLogPath -Append -Encoding UTF8
  try {
    $_ | Out-String | Out-File $logPath -Append -Encoding UTF8 -ErrorAction Stop
  } catch {}
  exit 1
} finally {
  if ($lockTaken) {
    try { $mutex.ReleaseMutex() | Out-Null } catch {}
  }
  $mutex.Dispose()
}
