param(
    [int]$Port = 8765,
    [double]$MaxMemoryPercent = 95
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$DashboardDir = Join-Path $RepoRoot "outputs\polymarket_dashboard"
$ServerScript = Join-Path $RepoRoot "scripts\serve_polymarket_dashboard.js"
$Node = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"

if (-not (Test-Path -LiteralPath $DashboardDir)) {
    throw "Dashboard directory was not found: $DashboardDir"
}
if (-not (Test-Path -LiteralPath (Join-Path $DashboardDir "index.html"))) {
    throw "Dashboard index.html was not found in: $DashboardDir"
}
if (-not (Test-Path -LiteralPath $Node)) {
    throw "Bundled Node.js was not found: $Node"
}

function Get-MemoryUsedPercent {
    $os = Get-CimInstance Win32_OperatingSystem
    return [math]::Round((($os.TotalVisibleMemorySize - $os.FreePhysicalMemory) / $os.TotalVisibleMemorySize) * 100, 1)
}

$memoryUsedPercent = Get-MemoryUsedPercent
if ($MaxMemoryPercent -gt 0 -and $memoryUsedPercent -ge $MaxMemoryPercent) {
    throw "Dashboard was not started because memory is $memoryUsedPercent%, at or above the $MaxMemoryPercent% guardrail."
}

$ip = "YOUR_COMPUTER_IP"
$ipconfig = ipconfig
$match = $ipconfig | Select-String -Pattern "IPv4 Address.*:\s*(192\.168\.[0-9.]+|10\.[0-9.]+|172\.(1[6-9]|2[0-9]|3[0-1])\.[0-9.]+)" | Select-Object -First 1
if ($match) {
    $ip = ($match.Matches[0].Groups[1].Value).Trim()
}

Write-Host ""
Write-Host "Polymarket dashboard server"
Write-Host "Computer: http://127.0.0.1:$Port/"
Write-Host "Phone:    http://$ip`:$Port/"
Write-Host "Memory:   $memoryUsedPercent% used (guardrail $MaxMemoryPercent%)"
Write-Host ""
Write-Host "Leave this window open while viewing the dashboard."
Write-Host "Press Ctrl+C to stop it."
Write-Host "For a background task: .\scripts\install_polymarket_dashboard_task.ps1 -StartNow"
Write-Host ""

& $Node $ServerScript $DashboardDir $Port "0.0.0.0"
