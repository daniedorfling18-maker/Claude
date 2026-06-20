param(
  [string]$RepoRoot = (Get-Location).Path,
  [string]$TaskName = "Daily Superbru Local Automation",
  [string]$At = "07:00",
  [switch]$CreateGitHubIssue,
  [switch]$CommitAndPushOutputs,
  [switch]$ManualOnMissing,
  [switch]$NotifyOnFirstState
)

$ErrorActionPreference = "Stop"

$runner = Join-Path $RepoRoot "scripts\run_daily_superbru_local.ps1"
if (!(Test-Path $runner)) {
  throw "Runner not found: $runner"
}

$argsList = @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-File", "`"$runner`"",
  "-RepoRoot", "`"$RepoRoot`""
)

if ($CreateGitHubIssue) { $argsList += "-CreateGitHubIssue" }
if ($CommitAndPushOutputs) { $argsList += "-CommitAndPushOutputs" }
if ($ManualOnMissing) { $argsList += "-ManualOnMissing" }
if ($NotifyOnFirstState) { $argsList += "-NotifyOnFirstState" }

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument ($argsList -join " ") -WorkingDirectory $RepoRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -ExecutionTimeLimit (New-TimeSpan -Hours 2)

$user = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -Principal $principal `
  -Force | Out-Null

Write-Host "Installed scheduled task: $TaskName"
Write-Host "Runs daily at: $At"
Write-Host "Runner: $runner"
Write-Host "Mode: run only when $user is logged on, so Chrome/Cloudflare can work."
Write-Host "To test now: Start-ScheduledTask -TaskName `"$TaskName`""
