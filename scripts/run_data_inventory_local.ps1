param(
  [string]$RepoRoot = (Get-Location).Path,
  [string]$ChromeDebugPort = "9222",
  [string]$ChromeProfileDir = ".chrome-oddspedia-profile",
  [string]$SeedUrl = "https://www.superbru.com/worldcup_predictor/pool_view.php?t=1296&p=13236623&g=32&view=matches",
  [string]$SuperbruPoolUrl = "https://www.superbru.com/worldcup_predictor/pool_view.php?t=1296&p=13236623&g=32&view=matches",
  [int]$MaxOddspediaMatches = 0,
  [switch]$TryOddspediaAltUrls,
  [switch]$WriteRawState
)

$ErrorActionPreference = "Stop"
Set-Location $RepoRoot

function Test-CdpPort {
  param([string]$Port)
  try {
    Invoke-RestMethod "http://127.0.0.1:$Port/json/version" -TimeoutSec 2 | Out-Null
    return $true
  } catch {
    return $false
  }
}

function Start-ChromeDebug {
  param([string]$Port, [string]$ProfileDir, [string]$Url)
  $chrome = "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
  if (!(Test-Path $chrome)) { $chrome = "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe" }
  if (!(Test-Path $chrome)) { throw "Could not find chrome.exe" }

  Start-Process -FilePath $chrome -ArgumentList @(
    "--remote-debugging-port=$Port",
    "--user-data-dir=$RepoRoot\$ProfileDir",
    "--no-first-run",
    "--no-default-browser-check",
    $Url
  )
}

function Ensure-ChromeCdp {
  if (-not (Test-CdpPort $ChromeDebugPort)) {
    Write-Host "Starting Chrome with remote debugging on port $ChromeDebugPort..."
    Start-ChromeDebug -Port $ChromeDebugPort -ProfileDir $ChromeProfileDir -Url $SeedUrl
    Start-Sleep -Seconds 5
  }
  if (-not (Test-CdpPort $ChromeDebugPort)) {
    throw "Chrome CDP port $ChromeDebugPort is not available. Close Chrome, then rerun this script after the browser page is accessible in Chrome."
  }
}

mkdir outputs\data_inventory -Force | Out-Null
mkdir outputs\data_inventory\raw_diagnostics -Force | Out-Null

Ensure-ChromeCdp

$oddArgs = @(
  "scripts\audit_oddspedia_available_data_cdp_session.py",
  "--cdp-url", "http://127.0.0.1:$ChromeDebugPort",
  "--urls-csv", "inputs\oddspedia_match_urls.csv",
  "--out-dir", "outputs\data_inventory",
  "--diagnostics-dir", "outputs\data_inventory\raw_diagnostics\oddspedia",
  "--settle-ms", "8000",
  "--timeout-ms", "90000"
)
if ($MaxOddspediaMatches -gt 0) { $oddArgs += @("--max-matches", "$MaxOddspediaMatches") }
if ($TryOddspediaAltUrls) { $oddArgs += "--try-alt-urls" }
if ($WriteRawState) { $oddArgs += "--write-raw-state" }

Write-Host "Auditing Oddspedia available data..."
python @oddArgs

Write-Host "Filtering Oddspedia high-value market paths..."
python scripts\filter_oddspedia_high_value_market_paths.py `
  --markets-csv outputs\data_inventory\oddspedia_available_markets.csv `
  --network-csv outputs\data_inventory\oddspedia_network_inventory.csv `
  --out-csv outputs\data_inventory\oddspedia_high_value_market_paths.csv `
  --out-json outputs\data_inventory\oddspedia_high_value_market_paths_summary.json

$superbruArgs = @(
  "scripts\audit_superbru_available_data_cdp_session.py",
  "--cdp-url", "http://127.0.0.1:$ChromeDebugPort",
  "--pool-url", $SuperbruPoolUrl,
  "--fixtures-csv", "outputs\final_locked_picks\superbru_final_card.csv",
  "--out-dir", "outputs\data_inventory",
  "--diagnostics-dir", "outputs\data_inventory\raw_diagnostics\superbru",
  "--settle-ms", "9000",
  "--timeout-ms", "90000"
)
if ($WriteRawState) { $superbruArgs += "--write-raw-state" }

Write-Host "Auditing Superbru available data..."
python @superbruArgs

Write-Host "Building combined data coverage summary..."
python scripts\build_data_inventory_summary.py `
  --inventory-dir outputs\data_inventory `
  --out-json outputs\data_inventory\data_coverage_summary.json

Write-Host "Data inventory complete. Review outputs\data_inventory\data_coverage_summary.json"
