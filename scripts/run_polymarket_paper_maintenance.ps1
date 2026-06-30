param(
    [string]$ConfigPath = "polymarket_predictive_config.example.yaml",
    [string]$TaskName = "Polymarket Paper Maintenance",
    [double]$MaxMemoryPercent = 95,
    [int]$StepTimeoutSeconds = 90,
    [switch]$Force,
    [switch]$SkipIdleDashboardRefresh
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$WorkRoot = Join-Path $RepoRoot "work"
$StatusPath = Join-Path $WorkRoot "polymarket_paper_maintenance_latest_status.json"
$TaskStatusPath = Join-Path $WorkRoot "polymarket_paper_maintenance_task_status.json"
$DashboardDataPath = Join-Path $RepoRoot "outputs\polymarket_dashboard\dashboard_data.json"

New-Item -ItemType Directory -Force $WorkRoot | Out-Null
Set-Location $RepoRoot
$env:PYTHONPATH = Join-Path $RepoRoot "src"

function Write-MaintenanceStatus {
    param([PSCustomObject]$Status)
    Write-JsonNoBom -Path $StatusPath -Value $Status -Depth 12
    Update-DashboardMaintenanceStatus -Status $Status
    Update-TaskStatus
}

function Write-JsonNoBom {
    param(
        [string]$Path,
        $Value,
        [int]$Depth = 12
    )
    $json = $Value | ConvertTo-Json -Depth $Depth
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $json, $encoding)
}

function Update-DashboardMaintenanceStatus {
    param([PSCustomObject]$Status)
    if (-not (Test-Path -LiteralPath $DashboardDataPath)) {
        return
    }
    try {
        $dashboard = Get-Content -LiteralPath $DashboardDataPath -Raw | ConvertFrom-Json
        $dashboard | Add-Member -NotePropertyName "paper_maintenance" -NotePropertyValue $Status -Force
        Write-JsonNoBom -Path $DashboardDataPath -Value $dashboard -Depth 100
    } catch {
        # Dashboard status is best-effort. The canonical maintenance status file
        # above is still written even if the static dashboard JSON is temporarily
        # locked, missing, or unreadable.
    }
}

function Update-DashboardTaskStatus {
    param([PSCustomObject]$Status)
    if (-not (Test-Path -LiteralPath $DashboardDataPath)) {
        return
    }
    try {
        $dashboard = Get-Content -LiteralPath $DashboardDataPath -Raw | ConvertFrom-Json
        $dashboard | Add-Member -NotePropertyName "paper_maintenance_task" -NotePropertyValue $Status -Force
        Write-JsonNoBom -Path $DashboardDataPath -Value $dashboard -Depth 100
    } catch {
        # Best-effort only; the task status file is the source of truth.
    }
}

function Update-TaskStatus {
    try {
        $existing = Read-JsonIfExists $TaskStatusPath
        if (-not ($existing -is [PSCustomObject])) {
            $existing = $null
        }
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        $taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
        $status = [PSCustomObject]@{
            status = if ($task) { "installed" } else { "not_installed_or_unknown" }
            task_name = $TaskName
            task_state = if ($task) { [string]$task.State } else { "" }
            interval_minutes = if ($existing) { $existing.interval_minutes } else { $null }
            max_memory_percent = if ($existing -and $null -ne $existing.max_memory_percent) { $existing.max_memory_percent } else { $MaxMemoryPercent }
            config_path = if ($existing -and $existing.config_path) { $existing.config_path } else { $ConfigPath }
            trigger_start_time = if ($existing) { $existing.trigger_start_time } else { "" }
            trigger_start_source = if ($existing) { $existing.trigger_start_source } else { "" }
            start_at_next_exit_due_requested = if ($existing -and $null -ne $existing.start_at_next_exit_due_requested) { $existing.start_at_next_exit_due_requested } else { $null }
            next_run_time = if ($taskInfo) { $taskInfo.NextRunTime.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ") } else { "" }
            last_run_time = if ($taskInfo) { $taskInfo.LastRunTime.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ") } else { "" }
            last_task_result = if ($taskInfo) { [int]$taskInfo.LastTaskResult } else { $null }
            runner = $PSCommandPath
            generated_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
            updated_by_runner = $true
        }
    } catch {
        $status = [PSCustomObject]@{
            status = "not_installed_or_unknown"
            task_name = $TaskName
            reason = $_.Exception.Message
            generated_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
            updated_by_runner = $true
        }
    }
    Write-JsonNoBom -Path $TaskStatusPath -Value $status -Depth 12
    Update-DashboardTaskStatus -Status $status
}

function Read-JsonIfExists {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
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

function Convert-ToBool {
    param($Value)
    if ($Value -is [bool]) {
        return $Value
    }
    $text = [string]$Value
    return $text.Trim().ToLowerInvariant() -in @("1", "true", "yes", "y", "on")
}

function Convert-ToInt {
    param($Value)
    try {
        if ($null -eq $Value -or [string]$Value -eq "") {
            return 0
        }
        return [int][double]$Value
    } catch {
        return 0
    }
}

function Convert-ToUtcDate {
    param($Value)
    $text = ([string]$Value).Trim()
    if (-not $text) {
        return $null
    }
    try {
        return ([datetime]::Parse($text, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::AssumeUniversal)).ToUniversalTime()
    } catch {
        return $null
    }
}

function Invoke-PythonStep {
    param(
        [string]$Name,
        [string[]]$Arguments,
        [int]$TimeoutSeconds
    )
    $safeName = $Name -replace "[^A-Za-z0-9_.-]", "_"
    $stdoutPath = Join-Path $WorkRoot "polymarket_paper_maintenance_$safeName.stdout.log"
    $stderrPath = Join-Path $WorkRoot "polymarket_paper_maintenance_$safeName.stderr.log"
    Remove-Item $stdoutPath, $stderrPath -ErrorAction SilentlyContinue

    try {
        $process = Start-Process `
            -FilePath "python" `
            -ArgumentList $Arguments `
            -WorkingDirectory $RepoRoot `
            -PassThru `
            -WindowStyle Hidden `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath

        if (-not $process.WaitForExit([math]::Max(10, $TimeoutSeconds) * 1000)) {
            try { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue } catch {}
            return [PSCustomObject]@{
                status = "timed_out"
                name = $Name
                stdout_log = $stdoutPath
                stderr_log = $stderrPath
                reason = "$Name timed out after $TimeoutSeconds seconds."
            }
        }
        $process.Refresh()
        if ([int]$process.ExitCode -ne 0) {
            return [PSCustomObject]@{
                status = "error"
                name = $Name
                exit_code = [int]$process.ExitCode
                stdout_log = $stdoutPath
                stderr_log = $stderrPath
                reason = "$Name failed."
            }
        }
        return [PSCustomObject]@{
            status = "ran"
            name = $Name
            stdout_log = $stdoutPath
            stderr_log = $stderrPath
        }
    } catch {
        return [PSCustomObject]@{
            status = "error"
            name = $Name
            stdout_log = $stdoutPath
            stderr_log = $stderrPath
            reason = $_.Exception.Message
        }
    }
}

$startedAt = (Get-Date).ToUniversalTime()
$memoryUsedPercent = Get-MemoryUsedPercent
if ($MaxMemoryPercent -gt 0 -and $memoryUsedPercent -ge $MaxMemoryPercent) {
    Write-MaintenanceStatus ([PSCustomObject]@{
        status = "skipped_high_memory"
        generated_at_utc = $startedAt.ToString("yyyy-MM-ddTHH:mm:ssZ")
        memory_used_percent = $memoryUsedPercent
        max_memory_percent = $MaxMemoryPercent
        reason = "Paper broker/dashboard maintenance was skipped because local memory is at or above the guardrail."
    })
    exit 0
}

$dashboard = Read-JsonIfExists $DashboardDataPath
$freshness = if ($dashboard -and $dashboard.evidence_freshness) { $dashboard.evidence_freshness } else { $null }
$probeWatch = if ($dashboard -and $dashboard.paper_probe_exit_watch) { $dashboard.paper_probe_exit_watch } else { $null }

$brokerRefreshNeeded = Convert-ToBool $(if ($freshness) { $freshness.broker_refresh_needed } else { $false })
$pendingSignals = Convert-ToInt $(if ($freshness) { $freshness.pending_broker_signals } else { 0 })
$pendingExitProbes = Convert-ToInt $(if ($freshness) { $freshness.pending_broker_exit_probes } else { 0 })
$openProbes = Convert-ToInt $(if ($probeWatch) { $probeWatch.open_confirmation_probes } else { 0 })
$nextExitDueRaw = if ($freshness -and $freshness.next_broker_exit_due_utc) {
    $freshness.next_broker_exit_due_utc
} elseif ($probeWatch -and $probeWatch.next_due_utc) {
    $probeWatch.next_due_utc
} else {
    ""
}
$nextExitDueUtc = Convert-ToUtcDate $nextExitDueRaw
$exitDueByClock = $false
if ($nextExitDueUtc -ne $null -and $openProbes -gt 0) {
    $exitDueByClock = $nextExitDueUtc -le $startedAt
}

$workReasons = @()
if ($Force) { $workReasons += "force" }
if ($brokerRefreshNeeded) { $workReasons += "dashboard_broker_refresh_needed" }
if ($pendingSignals -gt 0) { $workReasons += "pending_paper_signals" }
if ($pendingExitProbes -gt 0) { $workReasons += "pending_exit_probes" }
if ($exitDueByClock) { $workReasons += "exit_due_by_clock" }

if ($workReasons.Count -gt 0) {
    $paperTrade = Invoke-PythonStep `
        -Name "paper-trade" `
        -Arguments @("-m", "polymarket_predictive_engine.cli", "paper-trade", "--config", $ConfigPath) `
        -TimeoutSeconds $StepTimeoutSeconds
    $paperRefresh = Read-JsonIfExists (Join-Path $RepoRoot "outputs\polymarket_model_governance\paper_trade_refresh.json")
    $status = if ($paperTrade.status -eq "ran") { "ran_broker_maintenance" } else { $paperTrade.status }
    Write-MaintenanceStatus ([PSCustomObject]@{
        status = $status
        generated_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        memory_used_percent = $memoryUsedPercent
        max_memory_percent = $MaxMemoryPercent
        work_reasons = $workReasons
        paper_trade_step = $paperTrade
        paper_trade_refresh = $paperRefresh
    })
    exit 0
}

if (-not $SkipIdleDashboardRefresh) {
    $render = Invoke-PythonStep `
        -Name "render-dashboard" `
        -Arguments @("scripts\render_polymarket_dashboard.py", "--config", $ConfigPath) `
        -TimeoutSeconds ([math]::Min($StepTimeoutSeconds, 45))
    Write-MaintenanceStatus ([PSCustomObject]@{
        status = if ($render.status -eq "ran") { "refreshed_dashboard_idle" } else { $render.status }
        generated_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        memory_used_percent = $memoryUsedPercent
        max_memory_percent = $MaxMemoryPercent
        work_reasons = @()
        next_exit_due_utc = $nextExitDueRaw
        open_confirmation_probes = $openProbes
        render_step = $render
    })
    exit 0
}

Write-MaintenanceStatus ([PSCustomObject]@{
    status = "skipped_no_work"
    generated_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    memory_used_percent = $memoryUsedPercent
    max_memory_percent = $MaxMemoryPercent
    next_exit_due_utc = $nextExitDueRaw
    open_confirmation_probes = $openProbes
})
