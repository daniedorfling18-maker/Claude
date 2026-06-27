param(
    [string]$Config = "polymarket_predictive_config.example.yaml",
    [int]$MaxAssets = 50,
    [int]$DashboardPort = 8765,
    [double]$MaxMemoryPercentToStart = 90.0,
    [int]$WebsocketSeconds = 5,
    [double]$PredictionCycleSeconds = 15.0,
    [double]$DiscoveryCycleSeconds = 300.0,
    [switch]$ForceRestart,
    [switch]$SkipDashboard,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$WorkDir = Join-Path $RepoRoot "work"
$DashboardDir = Join-Path $RepoRoot "outputs\polymarket_dashboard"
$DashboardScript = Join-Path $RepoRoot "scripts\serve_polymarket_dashboard.js"
$Node = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
$OutLog = Join-Path $WorkDir "local_live_loop.out.log"
$ErrLog = Join-Path $WorkDir "local_live_loop.err.log"
$DashboardOutLog = Join-Path $WorkDir "polymarket_dashboard.out.log"
$DashboardErrLog = Join-Path $WorkDir "polymarket_dashboard.err.log"

function Get-MemoryPercentUsed {
    $os = Get-CimInstance Win32_OperatingSystem
    return [math]::Round((1.0 - ($os.FreePhysicalMemory / $os.TotalVisibleMemorySize)) * 100.0, 1)
}

function Get-LocalIpHint {
    $ip = "YOUR_COMPUTER_IP"
    $ipconfig = ipconfig
    $match = $ipconfig | Select-String -Pattern "IPv4 Address.*:\s*(192\.168\.[0-9.]+|10\.[0-9.]+|172\.(1[6-9]|2[0-9]|3[0-1])\.[0-9.]+)" | Select-Object -First 1
    if ($match) {
        $ip = ($match.Matches[0].Groups[1].Value).Trim()
    }
    return $ip
}

function Get-RepoLocalLiveProcesses {
    $repoText = [string]$RepoRoot
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
        Where-Object {
            $_.CommandLine -like "*run_polymarket_local_live_loop.py*" -and
            $_.CommandLine -like "*$repoText*"
        }
}

function Test-PortListening {
    param([int]$Port)
    $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    return $null -ne $connection
}

New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null

$memoryPercent = Get-MemoryPercentUsed
$localIp = Get-LocalIpHint
$existingBots = @(Get-RepoLocalLiveProcesses)

Write-Host ""
Write-Host "Polymarket local paper bot launcher"
Write-Host "Repo:      $RepoRoot"
Write-Host "Memory:    $memoryPercent% used"
Write-Host "Dashboard: http://127.0.0.1:$DashboardPort/"
Write-Host "Phone:     http://$localIp`:$DashboardPort/"
Write-Host ""

if ($existingBots.Count -gt 0) {
    if (-not $ForceRestart) {
        Write-Host "A repo-owned local Polymarket bot is already running:"
        $existingBots | ForEach-Object { Write-Host "  PID $($_.ProcessId): $($_.CommandLine)" }
        Write-Host ""
        Write-Host "Not starting a duplicate. Use -ForceRestart only if you intentionally want to replace it."
        exit 0
    }
    foreach ($bot in $existingBots) {
        Write-Host "Stopping existing local bot PID $($bot.ProcessId)"
        if (-not $DryRun) {
            Stop-Process -Id $bot.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
}

if ($memoryPercent -gt $MaxMemoryPercentToStart) {
    Write-Host "Not starting the live loop: memory is above the safety threshold ($memoryPercent% > $MaxMemoryPercentToStart%)."
    Write-Host "Close extra Codex/browser apps or stop Docker, then rerun this script."
    exit 2
}

if (-not $SkipDashboard) {
    $dashboardListening = Test-PortListening -Port $DashboardPort
    if ($dashboardListening) {
        Write-Host "Dashboard server is already listening on port $DashboardPort."
    } else {
        if (-not (Test-Path -LiteralPath $DashboardDir)) {
            throw "Dashboard directory was not found: $DashboardDir"
        }
        if (-not (Test-Path -LiteralPath $Node)) {
            throw "Bundled Node.js was not found: $Node"
        }
        Write-Host "Starting dashboard server on port $DashboardPort."
        if (-not $DryRun) {
            Start-Process -FilePath $Node `
                -ArgumentList @($DashboardScript, $DashboardDir, "$DashboardPort", "0.0.0.0") `
                -WorkingDirectory $RepoRoot `
                -RedirectStandardOutput $DashboardOutLog `
                -RedirectStandardError $DashboardErrLog `
                -WindowStyle Hidden | Out-Null
        }
    }
}

$env:PYTHONPATH = Join-Path $RepoRoot "src"
$argsList = @(
    "-u",
    "scripts\run_polymarket_local_live_loop.py",
    "--config",
    $Config,
    "--websocket-seconds",
    "$WebsocketSeconds",
    "--prediction-cycle-seconds",
    "$PredictionCycleSeconds",
    "--discovery-cycle-seconds",
    "$DiscoveryCycleSeconds",
    "--max-assets",
    "$MaxAssets",
    "--paper-source",
    "websocket"
)

Write-Host "Starting local websocket paper loop with max-assets=$MaxAssets."
Write-Host "Logs:"
Write-Host "  $OutLog"
Write-Host "  $ErrLog"

if ($DryRun) {
    Write-Host "Dry run only; no process was started."
    exit 0
}

$process = Start-Process -FilePath "python" `
    -ArgumentList $argsList `
    -WorkingDirectory $RepoRoot `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -WindowStyle Hidden `
    -PassThru

Write-Host ""
Write-Host "Started local Polymarket paper bot PID $($process.Id)."
Write-Host "Open dashboard: http://127.0.0.1:$DashboardPort/"
Write-Host "Mobile on same Wi-Fi: http://$localIp`:$DashboardPort/"
