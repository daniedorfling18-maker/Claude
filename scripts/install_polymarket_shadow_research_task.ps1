param(
  [string]$TaskName = "Polymarket Shadow Research Cycle",
  [int]$IntervalMinutes = 60,
  [int]$WebsocketSeconds = 90,
  [string]$ConfigPath = "polymarket_predictive_config.example.yaml"
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$runner = Join-Path $repoRoot "scripts\run_polymarket_shadow_research_cycle.ps1"
$config = Join-Path $repoRoot $ConfigPath

if (-not (Test-Path $runner)) {
  throw "Runner not found: $runner"
}
if (-not (Test-Path $config)) {
  throw "Config not found: $config"
}
if ($IntervalMinutes -lt 15) {
  throw "IntervalMinutes must be at least 15 to avoid hammering APIs. Requested: $IntervalMinutes"
}

$quotedRunner = '"' + $runner + '"'
$quotedConfig = '"' + $config + '"'
$arguments = "-NoProfile -ExecutionPolicy Bypass -File $quotedRunner -ConfigPath $quotedConfig -WebsocketSeconds $WebsocketSeconds"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
# Windows exposes RunLevel as Limited or Highest. Limited is the intended non-admin/least-privilege mode here.
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null

Write-Host "Installed scheduled task: $TaskName"
Write-Host "Runs every $IntervalMinutes minutes while you are logged in."
Write-Host "Runner: $runner"
Write-Host "Config: $config"
Write-Host "Manual start: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "View status: Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
Write-Host "Latest result: Get-Content '$repoRoot\work\shadow_research_cycle_latest_status.json' -Raw"
Write-Host "Disable: Disable-ScheduledTask -TaskName '$TaskName'"
Write-Host "Uninstall: Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
