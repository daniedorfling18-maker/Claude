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

function Invoke-WarnOnlyStep {
  param([string]$Name, [scriptblock]$Step)
  Write-Host $Name
  try {
    & $Step
    if ($LASTEXITCODE -ne 0) { Write-Warning "$Name exited with code $LASTEXITCODE. Continuing daily run." }
  } catch {
    Write-Warning "$Name failed. Continuing daily run. Error: $_"
  }
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
mkdir outputs\oddspedia_probability_extract -Force | Out-Null
mkdir outputs\oddspedia_pick_validation -Force | Out-Null
mkdir inputs\smartbet_grids -Force | Out-Null

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

  Invoke-WarnOnlyStep "Refreshing visible Superbru pool picks via Chrome CDP..." {
    python scripts\scrape_superbru_pool_picks_cdp_session.py `
      --cdp-url "http://127.0.0.1:$ChromeDebugPort" `
      --pool-url $SuperbruPoolUrl `
      --fixtures-csv outputs\final_locked_picks\superbru_final_card.csv `
      --out-csv outputs\superbru_pool\superbru_pool_picks_auto.csv `
      --out-summary-json outputs\superbru_pool\superbru_pool_picks_summary.json `
      --diagnostics-dir outputs\superbru_pool\pick_diagnostics `
      --settle-ms 12000 `
      --timeout-ms 90000
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

# Archive the previous Oddspedia probability snapshot before refreshing.
Invoke-WarnOnlyStep "Archiving previous Oddspedia probability snapshot..." {
  python scripts\archive_oddspedia_snapshot.py --history-dir outputs\oddspedia_probability_extract\history
}

# Run the full 10-step Oddspedia pipeline (scrape → features → quality → compare →
# EV → backtest → independence → crowding → pool intelligence → signal archive).
# Uses curl_cffi TLS impersonation — no Chrome/CDP required for this step.
$pipelineSkipFlag = @()
if ($SkipOddspediaScrape) { $pipelineSkipFlag += "--skip-scrape" }

Write-Host "Running Oddspedia pipeline (10 steps)..."
Invoke-WarnOnlyStep "Oddspedia full pipeline..." {
  python scripts\run_oddspedia_pipeline.py `
    --locked-picks-csv outputs\final_locked_picks\superbru_final_card.csv `
    @pipelineSkipFlag
}

# Compare to previous snapshot (runs after pipeline so new grid is available).
Invoke-WarnOnlyStep "Comparing Oddspedia movement versus previous snapshot..." {
  python scripts\compare_oddspedia_movement.py `
    --history-dir outputs\oddspedia_probability_extract\history `
    --summary-csv inputs\smartbet_grids\oddspedia_probability_summary_auto.csv `
    --shape-csv inputs\smartbet_grids\oddspedia_score_shape_features.csv `
    --comparison-csv outputs\oddspedia_pick_validation\oddspedia_pick_comparison.csv `
    --ev-recommendations-csv outputs\oddspedia_pick_validation\oddspedia_ev_recommendations.csv `
    --out-csv outputs\oddspedia_pick_validation\oddspedia_movement_vs_previous.csv `
    --out-summary-json outputs\oddspedia_pick_validation\oddspedia_movement_summary.json
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
  --oddspedia-ev-recommendations-csv outputs\oddspedia_pick_validation\oddspedia_ev_recommendations.csv `
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
  git add -f inputs\pool_leaderboard.csv inputs\pool_leaderboard_auto.csv inputs\smartbet_grids outputs\superbru_pool outputs\market_odds outputs\market_odds_validation outputs\market_odds_history outputs\component_validation outputs\final_locked_picks outputs\daily_robust_card outputs\oddspedia_probability_extract outputs\oddspedia_pick_validation outputs\daily_notifications outputs\final_leader_decision_daily_robust outputs\backtesting ':(exclude)outputs/superbru_pool/results_diagnostics/superbru_clicked_state_*' ':(exclude)outputs/superbru_pool/results_diagnostics/superbru_result_page_*' ':(exclude)outputs/superbru_pool/pick_diagnostics/*' ':(exclude)outputs/oddspedia_probability_extract/cdp_diagnostics/*' ':(exclude)outputs/backtesting/oddspedia_results_diagnostics/*'
  git diff --cached --quiet
  if ($LASTEXITCODE -eq 0) {
    Write-Host "No output changes to commit."
  } else {
    git commit -m "Update daily Superbru local outputs"
    git push
  }
}

Write-Host "Daily Superbru local automation complete."
