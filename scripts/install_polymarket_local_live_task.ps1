param(
    [string]$TaskName = "PolymarketLocalPaperBot",
    [string]$Config = "polymarket_predictive_config.example.yaml",
    [int]$MaxAssets = 20,
    [int]$DashboardPort = 8765,
    [switch]$Uninstall,
    [switch]$RunNow
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($PSScriptRoot)) {
    throw "Run this installer as a file: powershell -ExecutionPolicy Bypass -File .\scripts\install_polymarket_local_live_task.ps1"
}

$RepoRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$Launcher = Join-Path $RepoRoot "scripts\start_polymarket_local_live.ps1"

if (-not (Test-Path -LiteralPath $Launcher)) {
    throw "Launcher not found: $Launcher"
}

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed scheduled task '$TaskName' if it existed."
    exit 0
}

$argument = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$Launcher`"",
    "-Config", "`"$Config`"",
    "-MaxAssets", "$MaxAssets",
    "-DashboardPort", "$DashboardPort",
    "-ForceRestart"
) -join " "

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argument -WorkingDirectory $RepoRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Days 0)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Starts the local Polymarket paper bot and dashboard at Windows logon." `
    -Force | Out-Null

Write-Host "Installed scheduled task '$TaskName'."
Write-Host "Repo:      $RepoRoot"
Write-Host "Dashboard: http://127.0.0.1:$DashboardPort/"
Write-Host "Agent UI:  http://127.0.0.1:$DashboardPort/agent_status.html"

if ($RunNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Started scheduled task '$TaskName'."
}
