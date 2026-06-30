param(
    [int]$Port = 8765,
    [string]$HostName = "0.0.0.0",
    [double]$MaxMemoryPercent = 95
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$DashboardDir = Join-Path $RepoRoot "outputs\polymarket_dashboard"
$ServerScript = Join-Path $RepoRoot "scripts\serve_polymarket_dashboard.js"
$WorkDir = Join-Path $RepoRoot "work"
$StatusPath = Join-Path $WorkDir "polymarket_dashboard_server_status.json"
$StdoutLog = Join-Path $WorkDir "polymarket_dashboard_server.out.log"
$StderrLog = Join-Path $WorkDir "polymarket_dashboard_server.err.log"
$Node = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"

New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null

function Get-MemoryUsedPercent {
    $os = Get-CimInstance Win32_OperatingSystem
    return [math]::Round((($os.TotalVisibleMemorySize - $os.FreePhysicalMemory) / $os.TotalVisibleMemorySize) * 100, 1)
}

function Test-PortListening {
    param([int]$Port)
    try {
        return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    } catch {
        $netstat = netstat -ano | Select-String -Pattern (":$Port\s+")
        return [bool]$netstat
    }
}

function Write-DashboardStatus {
    param(
        [string]$Status,
        [hashtable]$Extra = @{}
    )
    $payload = [ordered]@{
        status = $Status
        updated_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        port = $Port
        host = $HostName
        dashboard_dir = "$DashboardDir"
        status_file = "$StatusPath"
        stdout_log = "$StdoutLog"
        stderr_log = "$StderrLog"
        memory_used_percent = Get-MemoryUsedPercent
        max_memory_percent = $MaxMemoryPercent
    }
    foreach ($key in $Extra.Keys) {
        $payload[$key] = $Extra[$key]
    }
    $payload | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $StatusPath -Encoding UTF8
}

try {
    if (Test-PortListening -Port $Port) {
        Write-DashboardStatus "already_running" @{
            url = "http://127.0.0.1:$Port/"
            reason = "A dashboard server is already listening on this port."
        }
        exit 0
    }

    $memoryUsedPercent = Get-MemoryUsedPercent
    if ($MaxMemoryPercent -gt 0 -and $memoryUsedPercent -ge $MaxMemoryPercent) {
        Write-DashboardStatus "skipped_high_memory" @{
            reason = "Dashboard server was not started because local memory was at or above the guardrail."
        }
        exit 0
    }

    if (-not (Test-Path -LiteralPath $DashboardDir)) {
        Write-DashboardStatus "missing_dashboard_dir" @{ reason = "Dashboard directory was not found." }
        throw "Dashboard directory was not found: $DashboardDir"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $DashboardDir "index.html"))) {
        Write-DashboardStatus "missing_dashboard_index" @{ reason = "Dashboard index.html was not found." }
        throw "Dashboard index.html was not found in: $DashboardDir"
    }
    if (-not (Test-Path -LiteralPath $ServerScript)) {
        Write-DashboardStatus "missing_server_script" @{ reason = "Dashboard server script was not found." }
        throw "Dashboard server script was not found: $ServerScript"
    }
    if (-not (Test-Path -LiteralPath $Node)) {
        Write-DashboardStatus "missing_node" @{ reason = "Bundled Node.js was not found." }
        throw "Bundled Node.js was not found: $Node"
    }

    Write-DashboardStatus "starting" @{
        url = "http://127.0.0.1:$Port/"
        lan_url_hint = "http://YOUR_COMPUTER_IP:$Port/"
    }

    "=== Dashboard server started $(Get-Date -Format o) ===" | Out-File $StdoutLog -Append -Encoding UTF8
    & $Node $ServerScript $DashboardDir $Port $HostName 1>> $StdoutLog 2>> $StderrLog
    $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
    Write-DashboardStatus "exited" @{ exit_code = $exitCode }
    exit $exitCode
} catch {
    Write-DashboardStatus "error" @{
        error = $_.Exception.Message
    }
    $_ | Out-String | Out-File $StderrLog -Append -Encoding UTF8
    exit 1
}
