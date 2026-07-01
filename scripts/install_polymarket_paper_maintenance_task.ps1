param(
    [string]$TaskName = "Polymarket Paper Maintenance",
    [int]$IntervalMinutes = 1,
    [double]$MaxMemoryPercent = 99,
    [string]$ConfigPath = "polymarket_predictive_config.example.yaml",
    [switch]$StartAtNextExitDue,
    [switch]$StartNow
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$Runner = Join-Path $RepoRoot "scripts\run_polymarket_paper_maintenance.ps1"
$WorkRoot = Join-Path $RepoRoot "work"
$StatusPath = Join-Path $WorkRoot "polymarket_paper_maintenance_task_status.json"
$DashboardDataPath = Join-Path $RepoRoot "outputs\polymarket_dashboard\dashboard_data.json"

New-Item -ItemType Directory -Force $WorkRoot | Out-Null

if (-not (Test-Path -LiteralPath $Runner)) {
    throw "Paper maintenance runner not found: $Runner"
}

function Read-JsonIfExists {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Convert-ToLocalDate {
    param($Value)
    $text = ([string]$Value).Trim()
    if (-not $text) {
        return $null
    }
    try {
        return ([datetime]::Parse($text, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::AssumeUniversal)).ToLocalTime()
    } catch {
        return $null
    }
}

$IntervalMinutes = [math]::Max(1, $IntervalMinutes)
$TriggerStart = (Get-Date).AddMinutes(1)
$TriggerStartSource = "default_next_minute"
if ($StartAtNextExitDue) {
    $DashboardData = Read-JsonIfExists $DashboardDataPath
    $NextExitDueRaw = if ($DashboardData -and $DashboardData.evidence_freshness) {
        $DashboardData.evidence_freshness.next_broker_exit_due_utc
    } else {
        ""
    }
    $NextExitDue = Convert-ToLocalDate $NextExitDueRaw
    if ($NextExitDue -and $NextExitDue -gt (Get-Date)) {
        $TriggerStart = $NextExitDue
        $TriggerStartSource = "dashboard_next_broker_exit_due_utc"
    } else {
        $TriggerStartSource = "next_exit_due_missing_or_past_default_next_minute"
    }
}
$QuotedRunner = '"' + $Runner + '"'
$QuotedConfig = '"' + $ConfigPath + '"'
$QuotedTaskName = '"' + $TaskName + '"'
$Arguments = "-NoProfile -ExecutionPolicy Bypass -File $QuotedRunner -ConfigPath $QuotedConfig -TaskName $QuotedTaskName -MaxMemoryPercent $MaxMemoryPercent"
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $Arguments -WorkingDirectory $RepoRoot
$Trigger = New-ScheduledTaskTrigger `
    -Once `
    -At $TriggerStart `
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

$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$TaskInfo = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
$GeneratedAtUtc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
@{
    status = if ($Task) { "installed" } else { "unknown" }
    task_name = $TaskName
    task_state = if ($Task) { [string]$Task.State } else { "" }
    interval_minutes = $IntervalMinutes
    max_memory_percent = $MaxMemoryPercent
    config_path = $ConfigPath
    runner = $Runner
    start_at_next_exit_due_requested = [bool]$StartAtNextExitDue
    trigger_start_time = $TriggerStart.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    trigger_start_source = $TriggerStartSource
    start_now_requested = [bool]$StartNow
    last_run_time = if ($TaskInfo) { $TaskInfo.LastRunTime.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ") } else { "" }
    next_run_time = if ($TaskInfo) { $TaskInfo.NextRunTime.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ") } else { "" }
    installed_at_utc = $GeneratedAtUtc
    generated_at_utc = $GeneratedAtUtc
} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $StatusPath -Encoding UTF8

Write-Host "Installed scheduled task: $TaskName"
Write-Host "Interval: every $IntervalMinutes minute(s)"
Write-Host "First trigger: $($TriggerStart.ToString('yyyy-MM-dd HH:mm:ss zzz')) ($TriggerStartSource)"
Write-Host "Status file: $RepoRoot\work\polymarket_paper_maintenance_latest_status.json"
Write-Host "Task status: $StatusPath"
Write-Host "Dashboard URL: http://127.0.0.1:8765/"
Write-Host "Manual start: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Stop: Stop-ScheduledTask -TaskName '$TaskName'"
Write-Host "Uninstall: Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
