param(
    [string]$TaskName = "Polymarket Paper Maintenance",
    [int]$IntervalMinutes = 1,
    [double]$MaxMemoryPercent = 95,
    [string]$ConfigPath = "polymarket_predictive_config.example.yaml",
    [switch]$StartNow
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$Runner = Join-Path $RepoRoot "scripts\run_polymarket_paper_maintenance.ps1"

if (-not (Test-Path -LiteralPath $Runner)) {
    throw "Paper maintenance runner not found: $Runner"
}

$IntervalMinutes = [math]::Max(1, $IntervalMinutes)
$QuotedRunner = '"' + $Runner + '"'
$QuotedConfig = '"' + $ConfigPath + '"'
$Arguments = "-NoProfile -ExecutionPolicy Bypass -File $QuotedRunner -ConfigPath $QuotedConfig -MaxMemoryPercent $MaxMemoryPercent"
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $Arguments -WorkingDirectory $RepoRoot
$Trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
$User = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description "Run lightweight local Polymarket paper broker/dashboard maintenance every $IntervalMinutes minute(s)." `
    -Force | Out-Null

if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
}

Write-Host "Installed scheduled task: $TaskName"
Write-Host "Interval: every $IntervalMinutes minute(s)"
Write-Host "Status file: $RepoRoot\work\polymarket_paper_maintenance_latest_status.json"
Write-Host "Dashboard URL: http://127.0.0.1:8765/"
Write-Host "Manual start: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Stop: Stop-ScheduledTask -TaskName '$TaskName'"
Write-Host "Uninstall: Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
