param(
    [string]$TaskName = "Polymarket Dashboard Server",
    [int]$Port = 8765,
    [double]$MaxMemoryPercent = 99,
    [switch]$StartNow
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$Runner = Join-Path $RepoRoot "scripts\run_polymarket_dashboard_server.ps1"
$WorkRoot = Join-Path $RepoRoot "work"
$Launcher = Join-Path $WorkRoot "run_polymarket_dashboard_server_hidden.vbs"

New-Item -ItemType Directory -Force $WorkRoot | Out-Null

if (-not (Test-Path -LiteralPath $Runner)) {
    throw "Dashboard server runner not found: $Runner"
}

$QuotedRunner = '"' + $Runner + '"'
$Arguments = "-NoProfile -ExecutionPolicy Bypass -File $QuotedRunner -Port $Port -MaxMemoryPercent $MaxMemoryPercent"
$EscapedArguments = $Arguments.Replace('"', '""')
Set-Content -LiteralPath $Launcher -Encoding Unicode -Value @"
Set shell = CreateObject("WScript.Shell")
shell.Run "powershell.exe $EscapedArguments", 0, False
"@
$Action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument ('"' + $Launcher + '"') -WorkingDirectory $RepoRoot
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Days 7)
$User = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description "Serve the local Polymarket dashboard on port $Port." -Force | Out-Null

if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
}

Write-Host "Installed scheduled task: $TaskName"
Write-Host "Dashboard URL: http://127.0.0.1:$Port/"
Write-Host "Phone URL: use scripts\polymarket_goal_status.py for the current LAN URL."
Write-Host "Hidden launcher: $Launcher"
Write-Host "Status file: $RepoRoot\work\polymarket_dashboard_server_status.json"
Write-Host "Manual start: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Stop: Stop-ScheduledTask -TaskName '$TaskName'"
Write-Host "Uninstall: Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
