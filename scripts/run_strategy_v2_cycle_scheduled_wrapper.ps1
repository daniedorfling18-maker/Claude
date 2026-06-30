$ErrorActionPreference = "Continue"

$repoRoot = "C:\Users\DanieDörfling\Documents\Codex\2026-06-25\daniedorfling18-maker-claude-https-github-com\repo"
$logPath = Join-Path $repoRoot "work\strategy_v2_scheduled_task.log"

New-Item -ItemType Directory -Force (Join-Path $repoRoot "work") | Out-Null

"=== Strategy V2 scheduled run started $(Get-Date -Format o) ===" | Out-File $logPath -Append -Encoding UTF8

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
