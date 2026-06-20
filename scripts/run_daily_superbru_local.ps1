param(
  [string]$RepoRoot = (Get-Location).Path,
  [string]$ChromeDebugPort = "9222",
  [string]$ChromeProfileDir = ".chrome-oddspedia-profile",
  [string]$SeedUrl = "https://www.superbru.com/worldcup_predictor/pool_view.php?t=1296&p=13236623&g=32&view=matches",
  [string]$SuperbruPoolUrl = "https://www.superbru.com/worldcup_predictor/pool_view.php?t=1296&p=13236623&g=32&view=matches",
  [string]$SnapshotId = "",
  [switch]$SkipFinalSimulation,
  [switch]$SkipOddspediaScrape,
  [switch]$SkipResultsBackfill,
  [switch]$UseOddspediaResultsBackfill,
  [switch]$SkipBacktest,
  [switch]$SkipSuperbruPoolScrape,
  [switch]$ManualOnMissing,
  [switch]$NotifyOnFirstState,
  [switch]$CreateGitHubIssue,
  [switch]$CommitAndPushOutputs
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

function Get-CsvRowCount {
  param([string]$Path)
  if (!(Test-Path $Path)) { return 0 }
  return (Import-Csv $Path | Measure-Object).Count
}

function Invoke-GitHubIssueIfAvailable {
  param([string]$BodyFile, [string]$Title)
  $gh = Get-Command gh -ErrorAction SilentlyContinue
  if ($null -eq $gh) {
    Write-Warning "GitHub CLI 'gh' not found. Notification report written to $BodyFile"
    return
  }
  try {
    gh issue create --title $Title --body-file $BodyFile | Out-Host
  } catch {
    Write-Warning "Could not create GitHub issue. Report written to $BodyFile. Error: $_"
  }
}

function Show-LocalNotification {
  param([string]$Message, [string]$ReportPath)
  try {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($Message, "Superbru Daily Action Items") | Out-Null
  } catch {
    Write-Host $Message
  }
  Write-Host "Report: $ReportPath"
}

mkdir outputs\daily_robust_card -Force | Out-Null
mkdir outputs\daily_notifications -Force | Out-Null
mkdir outputs\backtesting -Force | Out-Null
mkdir outputs\superbru_pool -Force | Out-Null

if (Test-Path outputs\daily_robust_card\daily_robust_superbru_card.csv) {
  Copy-Item outputs\daily_robust_card\daily_robust_superbru_card.csv outputs\daily_robust_card\previous_superbru_card.csv -Force
  Write-Host "Previous robust card captured."
} else {
  Write-Host "No previous robust card found. First run will only notify if -NotifyOnFirstState is used."
}

if (-not $SkipSuperbruPoolScrape) {
  Ensure-ChromeCdp
  Write-Host "Refreshing Superbru pool leaderboard via Chrome CDP..."
  python scripts\scrape_superbru_pool_cdp_session.py `
    --cdp-url "http://127.0.0.1:$ChromeDebugPort" `
    --pool-url $SuperbruPoolUrl `
    --out-dir outputs\superbru_pool `
    --leaderboard-out inputs\pool_leaderboard_auto.csv `
    --settle-ms 12000 `
    --timeout-ms 90000

  if (Test-Path inputs\pool_leaderboard_auto.csv) {
    $leaderboardRows = Get-CsvRowCount -Path inputs\pool_leaderboard_auto.csv
    if ($leaderboardRows -gt 0) {
      Copy-Item inputs\pool_leaderboard_auto.csv inputs\pool_leaderboard.csv -Force
      Write-Host "Superbru pool leaderboard refreshed: $leaderboardRows rows copied to inputs\pool_leaderboard.csv."
    } else {
      Write-Warning "Superbru pool scrape produced 0 leaderboard rows. Keeping existing inputs\pool_leaderboard.csv."
    }
  }
}

if (-not $SkipResultsBackfill) {
  Ensure-ChromeCdp
  Write-Host "Backfilling Superbru prior match results via Chrome CDP..."
  python scripts\scrape_superbru_results_cdp_session.py `
    --cdp-url "http://127.0.0.1:$ChromeDebugPort" `
    --pool-url $SuperbruPoolUrl `
    --out-csv outputs\superbru_pool\superbru_match_results_auto.csv `
    --out-summary-json outputs\superbru_pool\superbru_match_results_summary.json `
    --diagnostics-dir outputs\superbru_pool\results_diagnostics `
    --settle-ms 9000 `
    --timeout-ms 90000

  $superbruResultRows = Get-CsvRowCount -Path outputs\superbru_pool\superbru_match_results_auto.csv
  Write-Host "Superbru match-results backfill rows: $superbruResultRows"
}

$pipelineArgs = @()
if ($SnapshotId -ne "") { $pipelineArgs += @("--snapshot-id", $SnapshotId) }
if ($SkipFinalSimulation) { $pipelineArgs += "--skip-final-simulation" }

Write-Host "Running daily robust pipeline..."
$env:PYTHONPATH = "src"
python scripts\run_daily_robust_pipeline.py @pipelineArgs

if (-not $SkipOddspediaScrape) {
  Ensure-ChromeCdp

  $manualFlag = @()
  if ($ManualOnMissing) { $manualFlag += "--manual-on-missing" }

  Write-Host "Refreshing Oddspedia SmartBet grid via Chrome CDP..."
  python scripts\scrape_oddspedia_cdp_session.py `
    --cdp-url "http://127.0.0.1:$ChromeDebugPort" `
    --urls-csv inputs\oddspedia_match_urls.csv `
    --out-csv inputs\smartbet_grids\oddspedia_probability_grids_auto.csv `
    --out-summary-csv inputs\smartbet_grids\oddspedia_probability_summary_auto.csv `
    --out-json outputs\oddspedia_probability_extract\oddspedia_cdp_probability_extract_summary.json `
    --diagnostics-dir outputs\oddspedia_probability_extract\cdp_diagnostics `
    --settle-ms 12000 `
    --post-click-ms 6000 `
    --timeout-ms 90000 `
    @manualFlag
}

if ($UseOddspediaResultsBackfill -and (-not $SkipResultsBackfill)) {
  if (Test-Path inputs\oddspedia_match_urls.csv) {
    Ensure-ChromeCdp
    Write-Host "Backfilling Oddspedia final results via Chrome CDP as secondary/fallback source..."
    python scripts\scrape_oddspedia_results_cdp_session.py `
      --cdp-url "http://127.0.0.1:$ChromeDebugPort" `
      --urls-csv inputs\oddspedia_match_urls.csv `
      --out-csv outputs\backtesting\oddspedia_results_backfill.csv `
      --out-json outputs\backtesting\oddspedia_results_backfill_summary.json `
      --diagnostics-dir outputs\backtesting\oddspedia_results_diagnostics `
      --settle-ms 9000 `
      --timeout-ms 90000
  } else {
    Write-Warning "inputs\oddspedia_match_urls.csv missing. Oddspedia results backfill skipped."
  }
}

if ((Test-Path inputs\smartbet_grids\oddspedia_probability_grids_auto.csv) -and (Test-Path inputs\smartbet_grids\oddspedia_probability_summary_auto.csv)) {
  Write-Host "Running Oddspedia overlay comparison..."
  python scripts\compare_locked_picks_to_oddspedia.py `
    --locked-picks-csv outputs\final_locked_picks\superbru_final_card.csv `
    --oddspedia-grid-csv inputs\smartbet_grids\oddspedia_probability_grids_auto.csv `
    --oddspedia-summary-csv inputs\smartbet_grids\oddspedia_probability_summary_auto.csv `
    --out-csv outputs\oddspedia_pick_validation\oddspedia_pick_comparison.csv `
    --out-json outputs\oddspedia_pick_validation\oddspedia_pick_comparison_summary.json
} else {
  Write-Warning "Oddspedia grid CSV missing. Overlay comparison skipped."
}

if (-not $SkipBacktest) {
  $resultsCsv = ""
  $superbruRows = Get-CsvRowCount -Path outputs\superbru_pool\superbru_match_results_auto.csv
  if ($superbruRows -gt 0) {
    $resultsCsv = "outputs\superbru_pool\superbru_match_results_auto.csv"
    Write-Host "Using Superbru match results for backtest: $resultsCsv"
  } elseif (Test-Path outputs\backtesting\oddspedia_results_backfill.csv) {
    $resultsCsv = "outputs\backtesting\oddspedia_results_backfill.csv"
    Write-Warning "Superbru results unavailable. Falling back to Oddspedia results: $resultsCsv"
  }

  if ($resultsCsv -ne "") {
    Write-Host "Building Superbru backtest from $resultsCsv..."
    python scripts\build_superbru_backtest_from_results.py `
      --results-csv $resultsCsv `
      --picks-csv outputs\final_locked_picks\superbru_final_card.csv `
      --oddspedia-comparison-csv outputs\oddspedia_pick_validation\oddspedia_pick_comparison.csv `
      --out-csv outputs\backtesting\superbru_pick_backtest.csv `
      --out-summary-json outputs\backtesting\backtest_summary.json
  } else {
    Write-Warning "No match results source available. Backtest skipped."
  }
}

Write-Host "Checking robust-card score changes..."
python scripts\notify_score_changes.py `
  --current-card-csv outputs\daily_robust_card\daily_robust_superbru_card.csv `
  --previous-card-csv outputs\daily_robust_card\previous_superbru_card.csv `
  --audit-csv outputs\daily_robust_card\daily_switch_audit.csv `
  --out-md outputs\daily_robust_card\score_change_notification.md

$firstFlag = @()
if ($NotifyOnFirstState) { $firstFlag += "--notify-on-first-state" }

Write-Host "Building combined daily notification..."
python scripts\notify_daily_superbru_action_items.py `
  --score-change-json outputs\daily_robust_card\score_change_notification.json `
  --oddspedia-comparison-csv outputs\oddspedia_pick_validation\oddspedia_pick_comparison.csv `
  --out-md outputs\daily_notifications\daily_superbru_action_items.md `
  --out-json outputs\daily_notifications\daily_superbru_action_items.json `
  --state-json outputs\daily_notifications\daily_superbru_action_state.json `
  --update-state `
  @firstFlag

$notification = Get-Content outputs\daily_notifications\daily_superbru_action_items.json -Raw | ConvertFrom-Json
if ($notification.notify -eq $true) {
  $msg = "Superbru action items found: $($notification.action_count) total ($($notification.score_change_count) score changes, $($notification.oddspedia_review_count) Oddspedia reviews)."
  Show-LocalNotification -Message $msg -ReportPath "outputs\daily_notifications\daily_superbru_action_items.md"
  if ($CreateGitHubIssue) {
    $titleDate = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd")
    Invoke-GitHubIssueIfAvailable -Title "Superbru action items - $titleDate" -BodyFile "outputs\daily_notifications\daily_superbru_action_items.md"
  }
} else {
  Write-Host "No new Superbru action items. No notification created."
}

if ($CommitAndPushOutputs) {
  Write-Host "Committing daily outputs..."
  git add -f inputs\pool_leaderboard.csv inputs\pool_leaderboard_auto.csv inputs\smartbet_grids outputs\superbru_pool outputs\market_odds outputs\market_odds_validation outputs\market_odds_history outputs\component_validation outputs\final_locked_picks outputs\daily_robust_card outputs\oddspedia_pick_validation outputs\daily_notifications outputs\final_leader_decision_daily_robust outputs\backtesting
  git diff --cached --quiet
  if ($LASTEXITCODE -eq 0) {
    Write-Host "No output changes to commit."
  } else {
    git commit -m "Update daily Superbru local outputs"
    git push
  }
}

Write-Host "Daily Superbru local automation complete."

