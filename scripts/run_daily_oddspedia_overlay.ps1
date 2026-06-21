param(
  [string]$RepoRoot = (Get-Location).Path,
  [string]$ChromeDebugPort = "9222",
  [string]$ChromeProfileDir = ".chrome-oddspedia-profile",
  [string]$SeedUrl = "https://oddspedia.com/football/world/world-cup",
  [string]$SeedUrlsCsv = "inputs\oddspedia_seed_urls.csv",
  [string]$UrlsCsv = "inputs\oddspedia_match_urls.csv",
  [string]$LockedPicksCsv = "outputs\final_locked_picks\superbru_final_card.csv",
  [switch]$HeadfulDiscovery,
  [switch]$ManualOnMissing,
  [switch]$SkipUrlDiscovery,
  [switch]$SkipOddspediaScrape,
  [switch]$SkipComparison
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

if (-not $SkipOddspediaScrape) {
  if (-not $SkipUrlDiscovery) {
    if (!(Test-Path $SeedUrlsCsv)) {
      throw "Seed URL CSV not found: $SeedUrlsCsv"
    }

    $discoverArgs = @(
      "scripts\discover_oddspedia_match_urls.py",
      "--fixtures-csv", $LockedPicksCsv,
      "--seed-urls-csv", $SeedUrlsCsv,
      "--out-csv", $UrlsCsv,
      "--out-json", "outputs\oddspedia_url_discovery\oddspedia_url_discovery.json",
      "--settle-ms", "8000",
      "--timeout-ms", "60000"
    )
    if ($HeadfulDiscovery) { $discoverArgs += "--headful" }

    python @discoverArgs
  }

  if (!(Test-Path $UrlsCsv)) {
    throw "Oddspedia match URL CSV not found: $UrlsCsv. Run URL discovery first or pass a valid -UrlsCsv."
  }

  if (-not (Test-CdpPort $ChromeDebugPort)) {
    Write-Host "Starting Chrome with remote debugging on port $ChromeDebugPort..."
    Start-ChromeDebug -Port $ChromeDebugPort -ProfileDir $ChromeProfileDir -Url $SeedUrl
    Start-Sleep -Seconds 5
  }

  if (-not (Test-CdpPort $ChromeDebugPort)) {
    throw "Chrome CDP port $ChromeDebugPort is not available. Close all Chrome windows, rerun this script, and clear Cloudflare if prompted."
  }

  $scrapeArgs = @(
    "scripts\scrape_oddspedia_cdp_session.py",
    "--cdp-url", "http://127.0.0.1:$ChromeDebugPort",
    "--urls-csv", $UrlsCsv,
    "--out-csv", "inputs\smartbet_grids\oddspedia_probability_grids_auto.csv",
    "--out-summary-csv", "inputs\smartbet_grids\oddspedia_probability_summary_auto.csv",
    "--out-json", "outputs\oddspedia_probability_extract\oddspedia_cdp_probability_extract_summary.json",
    "--diagnostics-dir", "outputs\oddspedia_probability_extract\cdp_diagnostics",
    "--settle-ms", "12000",
    "--post-click-ms", "6000",
    "--timeout-ms", "90000"
  )
  if ($ManualOnMissing) { $scrapeArgs += "--manual-on-missing" }

  python @scrapeArgs
}

if (-not $SkipComparison) {
  if (!(Test-Path $LockedPicksCsv)) {
    throw "Locked picks CSV not found: $LockedPicksCsv. Run the daily robust pipeline first."
  }
  if (!(Test-Path "inputs\smartbet_grids\oddspedia_probability_grids_auto.csv")) {
    throw "Oddspedia grid CSV missing. Run scrape step first or pass -SkipComparison."
  }

  $compareArgs = @(
    "scripts\compare_locked_picks_to_oddspedia.py",
    "--locked-picks-csv", $LockedPicksCsv,
    "--oddspedia-grid-csv", "inputs\smartbet_grids\oddspedia_probability_grids_auto.csv",
    "--oddspedia-summary-csv", "inputs\smartbet_grids\oddspedia_probability_summary_auto.csv",
    "--out-csv", "outputs\oddspedia_pick_validation\oddspedia_pick_comparison.csv",
    "--out-json", "outputs\oddspedia_pick_validation\oddspedia_pick_comparison_summary.json"
  )

  if (Test-Path "inputs\smartbet_grids\oddspedia_markets_summary_auto.csv") {
    $compareArgs += @("--markets-summary-csv", "inputs\smartbet_grids\oddspedia_markets_summary_auto.csv")
  }

  python @compareArgs
}

Write-Host "Oddspedia overlay process completed."
Write-Host "Discovered URLs: $UrlsCsv"
Write-Host "Discovery summary: outputs\oddspedia_url_discovery\oddspedia_url_discovery.json"
Write-Host "SmartBet summary: inputs\smartbet_grids\oddspedia_probability_summary_auto.csv"
Write-Host "Comparison: outputs\oddspedia_pick_validation\oddspedia_pick_comparison.csv"
