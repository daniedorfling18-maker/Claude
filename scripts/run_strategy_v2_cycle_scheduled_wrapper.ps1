param(
  [double]$MaxMemoryPercent = 95
)

$ErrorActionPreference = "Continue"

$repoRoot = "C:\Users\DanieDörfling\Documents\Codex\2026-06-25\daniedorfling18-maker-claude-https-github-com\repo"
$logPath = Join-Path $repoRoot "work\strategy_v2_scheduled_task.log"
$latestStatusPath = Join-Path $repoRoot "work\strategy_v2_cycle_latest_status.json"

New-Item -ItemType Directory -Force (Join-Path $repoRoot "work") | Out-Null

"=== Strategy V2 scheduled run started $(Get-Date -Format o) ===" | Out-File $logPath -Append -Encoding UTF8

function Get-MemoryUsedPercent {
  $os = Get-CimInstance Win32_OperatingSystem
  return [math]::Round((($os.TotalVisibleMemorySize - $os.FreePhysicalMemory) / $os.TotalVisibleMemorySize) * 100, 1)
}

$memoryUsedPercent = Get-MemoryUsedPercent
if ($MaxMemoryPercent -gt 0 -and $memoryUsedPercent -ge $MaxMemoryPercent) {
  $status = [PSCustomObject]@{
    status = "skipped_high_memory"
    started_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    ended_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    memory_used_percent = $memoryUsedPercent
    max_memory_percent = $MaxMemoryPercent
    reason = "Strategy V2 scheduled wrapper skipped before invoking the cycle because local memory was at or above the guardrail."
  }
  $status | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $latestStatusPath -Encoding UTF8
  "=== Strategy V2 scheduled run skipped for high memory: $memoryUsedPercent% / $MaxMemoryPercent% $(Get-Date -Format o) ===" | Out-File $logPath -Append -Encoding UTF8
  exit 0
}

try {
  Set-Location $repoRoot
  $env:PYTHONPATH = Join-Path $repoRoot "src"
  & "$repoRoot\scripts\run_polymarket_strategy_v2_cycle.ps1" *>> $logPath
  "=== Strategy V2 scheduled run completed $(Get-Date -Format o) ===" | Out-File $logPath -Append -Encoding UTF8
  exit 0
}
catch {
  "=== Strategy V2 scheduled run failed $(Get-Date -Format o) ===" | Out-File $logPath -Append -Encoding UTF8
  $_ | Out-String | Out-File $logPath -Append -Encoding UTF8
  exit 1
}
