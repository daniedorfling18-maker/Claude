param(
    [string]$Config = "polymarket_predictive_config.example.yaml",
    [int]$MaxAssets = 20,
    [int]$DashboardPort = 8765,
    [double]$MaxMemoryPercentToStart = 99.0,
    [int]$WebsocketSeconds = 5,
    [double]$PredictionCycleSeconds = 30.0,
    [double]$DiscoveryCycleSeconds = 600.0,
    [int]$WindowsProbeTimeoutSeconds = 45,
    [switch]$ForceRestart,
    [switch]$SkipDashboard,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($PSScriptRoot)) {
    throw "Do not paste this script body into PowerShell. Run it as a file from the repo root: powershell -ExecutionPolicy Bypass -File .\scripts\start_polymarket_local_live.ps1 -ForceRestart"
}

$RepoRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$WorkDir = Join-Path $RepoRoot "work"
$DashboardDir = Join-Path $RepoRoot "outputs\polymarket_dashboard"
$DashboardScript = Join-Path $RepoRoot "scripts\serve_polymarket_dashboard.js"
$BotScript = Join-Path $RepoRoot "scripts\run_polymarket_local_live_loop.py"
$Node = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
$OutLog = Join-Path $WorkDir "local_live_loop.out.log"
$ErrLog = Join-Path $WorkDir "local_live_loop.err.log"
$DashboardOutLog = Join-Path $WorkDir "polymarket_dashboard.out.log"
$DashboardErrLog = Join-Path $WorkDir "polymarket_dashboard.err.log"
$ProcessScanTimedOut = $false

function Invoke-WithTimeout {
    param(
        [scriptblock]$ScriptBlock,
        [object[]]$ArgumentList = @(),
        [int]$TimeoutSeconds = 45,
        [object]$Fallback = $null,
        [string]$Description = "Windows probe"
    )

    $job = Start-Job -ScriptBlock $ScriptBlock -ArgumentList $ArgumentList
    try {
        $completed = Wait-Job -Job $job -Timeout $TimeoutSeconds
        if ($null -ne $completed -and $job.State -eq "Completed") {
            return Receive-Job -Job $job
        }
        Write-Warning "$Description timed out after $TimeoutSeconds seconds."
        return $Fallback
    } finally {
        Stop-Job -Job $job -ErrorAction SilentlyContinue | Out-Null
        Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
    }
}

function Get-MemoryPercentUsed {
    $value = Invoke-WithTimeout `
        -TimeoutSeconds $WindowsProbeTimeoutSeconds `
        -Description "Memory check" `
        -Fallback $null `
        -ScriptBlock {
            $os = Get-CimInstance Win32_OperatingSystem
            [math]::Round((1.0 - ($os.FreePhysicalMemory / $os.TotalVisibleMemorySize)) * 100.0, 1)
        }
    if ($null -eq $value) {
        Write-Warning "Could not read memory pressure quickly; refusing to start the bot to protect the laptop."
        return 1000.0
    }
    return [double]$value
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
    $botScriptText = [string]$BotScript
    $rows = Invoke-WithTimeout `
        -TimeoutSeconds $WindowsProbeTimeoutSeconds `
        -Description "Local bot process scan" `
        -Fallback "__TIMEOUT__" `
        -ArgumentList @($botScriptText) `
        -ScriptBlock {
            param([string]$BotScriptText)
            Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
                Where-Object {
                    $cmd = [string]$_.CommandLine
                    # Match the new absolute-path launcher and legacy relative-path launches.
                    $cmd -like "*$BotScriptText*" -or $cmd -like "*run_polymarket_local_live_loop.py*"
                } |
                Select-Object ProcessId,Name,CommandLine
        }
    if ($rows -eq "__TIMEOUT__") {
        $script:ProcessScanTimedOut = $true
        return @()
    }
    return @($rows)
}

function Test-PortListening {
    param([int]$Port)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(800)) {
            return $false
        }
        try {
            $client.EndConnect($async)
            return $true
        } catch {
            return $false
        }
    } finally {
        $client.Close()
    }
}

function Get-LiquidityQueryScore {
    param(
        [string]$Query,
        [object]$FamilyRows
    )
    $familiesByQuery = @{
        "btc updown" = @("crypto_btc_updown_5m", "crypto_btc_updown_15m")
        "xrp updown" = @("crypto_xrp_updown_5m", "crypto_xrp_updown_event")
        "solana updown" = @("crypto_sol_updown_5m")
        "eth updown" = @("crypto_eth_updown_5m", "crypto_eth_updown_15m")
        "bitcoin" = @("crypto_btc_special", "crypto_btc_updown_5m", "crypto_btc_updown_15m")
        "ethereum" = @("crypto_eth_updown_5m", "crypto_eth_updown_15m")
        "solana" = @("crypto_sol_updown_5m", "crypto_special")
        "xrp" = @("crypto_xrp_special", "crypto_xrp_updown_5m")
        "world cup" = @("sports_other", "worldcup")
        "tennis" = @("tennis_itf_total", "tennis_atp_total", "tennis_wta_total", "unknown")
    }
    $queryKey = $Query.ToLowerInvariant()
    if (-not $familiesByQuery.ContainsKey($queryKey)) {
        return 0.0
    }
    $matched = @()
    foreach ($row in @($FamilyRows)) {
        $family = [string]$row.family
        if ($familiesByQuery[$queryKey] -contains $family) {
            $matched += $row
        }
    }
    if ($matched.Count -eq 0) {
        return -20.0
    }
    $tradable = 0.0
    $liquidity = 0.0
    foreach ($row in $matched) {
        $tradableValue = $row.tradable_tokens
        if ($null -eq $tradableValue) { $tradableValue = $row.tradable }
        if ($null -ne $tradableValue -and "$tradableValue" -ne "") { $tradable += [double]$tradableValue }
        $liqValue = $row.max_liquidity
        if ($null -ne $liqValue -and "$liqValue" -ne "") { $liquidity += [double]$liqValue }
    }
    if ($tradable -le 0) {
        return -40.0
    }
    return $tradable + [math]::Min(10.0, $liquidity / 10000.0)
}

function Set-DefaultResearchScanEnvironment {
    if ([string]::IsNullOrWhiteSpace($env:POLYMARKET_SCAN_QUERY_MODE)) {
        $env:POLYMARKET_SCAN_QUERY_MODE = "rotate"
    }
    if ([string]::IsNullOrWhiteSpace($env:POLYMARKET_MAX_SCAN_QUERIES)) {
        $env:POLYMARKET_MAX_SCAN_QUERIES = "1"
    }
    if (-not [string]::IsNullOrWhiteSpace($env:POLYMARKET_QUERIES)) {
        return
    }
    $defaultQueries = @("btc updown", "xrp updown", "solana updown", "eth updown", "bitcoin", "ethereum", "solana", "xrp", "world cup", "tennis")
    $liquidityPath = Join-Path $RepoRoot "outputs\polymarket_model_governance\liquidity_discovery_summary.json"
    if (-not (Test-Path -LiteralPath $liquidityPath)) {
        return
    }
    try {
        $payload = Get-Content -LiteralPath $liquidityPath -Raw | ConvertFrom-Json
        $familyRows = @($payload.family_summary)
        if ($familyRows.Count -eq 0) {
            return
        }
        $scoredQueries = @()
        foreach ($query in $defaultQueries) {
            $scoredQueries += [PSCustomObject]@{
                Query = $query
                Score = (Get-LiquidityQueryScore -Query $query -FamilyRows $familyRows)
            }
        }
        $ordered = $scoredQueries | Sort-Object -Property Query | Sort-Object -Property Score -Descending | Select-Object -ExpandProperty Query
        if ($ordered.Count -gt 0) {
            $env:POLYMARKET_QUERIES = ($ordered -join ",")
        }
    } catch {
        Write-Warning "Could not build liquidity-aware query order from $liquidityPath. Using configured query order. Error: $($_.Exception.Message)"
    }
}

New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
Set-DefaultResearchScanEnvironment

$memoryPercent = Get-MemoryPercentUsed
$localIp = Get-LocalIpHint
$existingBots = @(Get-RepoLocalLiveProcesses)

Write-Host ""
Write-Host "Polymarket local paper bot launcher"
Write-Host "Repo:      $RepoRoot"
Write-Host "Memory:    $memoryPercent% used"
Write-Host "Dashboard: http://127.0.0.1:$DashboardPort/"
Write-Host "Agent UI:  http://127.0.0.1:$DashboardPort/agent_status.html"
Write-Host "Phone:     http://$localIp`:$DashboardPort/"
if (-not [string]::IsNullOrWhiteSpace($env:POLYMARKET_SCAN_QUERY_MODE)) {
    Write-Host "Scan mode override: $env:POLYMARKET_SCAN_QUERY_MODE"
}
if (-not [string]::IsNullOrWhiteSpace($env:POLYMARKET_MAX_SCAN_QUERIES)) {
    Write-Host "Max scan queries override: $env:POLYMARKET_MAX_SCAN_QUERIES"
}
if (-not [string]::IsNullOrWhiteSpace($env:POLYMARKET_QUERIES)) {
    Write-Host "Liquidity-aware query order: $env:POLYMARKET_QUERIES"
}
Write-Host ""

if ($ProcessScanTimedOut) {
    Write-Host "Not starting the live loop: Windows could not confirm whether another local bot is already running."
    Write-Host "Close heavy apps or reboot, then rerun this script."
    exit 3
}

if ($existingBots.Count -gt 0) {
    if (-not $ForceRestart) {
        Write-Host "A local Polymarket bot is already running:"
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
    Start-Sleep -Seconds 2
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
            Write-Host "Dashboard directory was not found; rendering a bootstrap dashboard."
            $env:PYTHONPATH = Join-Path $RepoRoot "src"
            $bootstrap = @'
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.dashboard import render_dashboard
cfg = load_config("polymarket_predictive_config.example.yaml")
print(render_dashboard(cfg))
'@
            $bootstrap | python -
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
    $BotScript,
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
Write-Host "Agent status: http://127.0.0.1:$DashboardPort/agent_status.html"
Write-Host "Mobile on same Wi-Fi: http://$localIp`:$DashboardPort/"
