param(
  [string]$ConfigPath = "polymarket_predictive_config.example.yaml"
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot

New-Item -ItemType Directory -Force .\outputs\polymarket_strategy_v2 | Out-Null
New-Item -ItemType Directory -Force .\work | Out-Null

$started = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

# Refresh the underlying Polymarket market/prediction snapshot before rescoring Strategy V2.
$predictionSnapshotPath = ".\outputs\polymarket_predictions\predictions.csv"
$predictionSnapshotBeforeUtc = $null
if (Test-Path $predictionSnapshotPath) {
  $predictionSnapshotBeforeUtc = (Get-Item $predictionSnapshotPath).LastWriteTimeUtc.ToString("yyyy-MM-ddTHH:mm:ssZ")
}

$shadowRefreshScript = Join-Path $repoRoot "scripts\run_polymarket_shadow_research_cycle.ps1"
$shadowRefreshArgs = @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-File", $shadowRefreshScript,
  "-ConfigPath", $ConfigPath,
  "-WebsocketSeconds", "30"
)

& powershell.exe @shadowRefreshArgs | Out-Null
if ($LASTEXITCODE -ne 0) {
  throw "Shadow research refresh failed with exit code $LASTEXITCODE"
}

$predictionSnapshotAfterUtc = $null
if (Test-Path $predictionSnapshotPath) {
  $predictionSnapshotAfterUtc = (Get-Item $predictionSnapshotPath).LastWriteTimeUtc.ToString("yyyy-MM-ddTHH:mm:ssZ")
}


# Refresh the underlying market/prediction snapshot before rescoring Strategy V2.

python -m polymarket_predictive_engine.cli refresh-governance --config $ConfigPath | Out-Null
python -m polymarket_predictive_engine.cli validation-report --config $ConfigPath | Out-Null

.\scripts\run_polymarket_opportunity_audit.ps1 -ConfigPath $ConfigPath | Out-Null
.\scripts\run_polymarket_strategy_v2_anchored_edge.ps1 -ConfigPath $ConfigPath | Out-Null

$logPath = ".\outputs\polymarket_strategy_v2\anchored_edge_persistence_log.csv"

$anchoredRows = Import-Csv .\outputs\polymarket_strategy_v2\anchored_edge_candidates.csv |
  Where-Object { $_.anchor_fair_probability -ne "" } |
  Select-Object `
    @{Name="logged_at_utc"; Expression={(Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")}},
    family,
    market_slug,
    outcome,
    status,
    blockers,
    anchor_fair_probability,
    executable_price,
    anchor_raw_edge,
    spread,
    spread_penalty,
    liquidity,
    liquidity_penalty,
    uncertainty_penalty,
    risk_adjusted_anchor_edge

if (Test-Path $logPath) {
  $anchoredRows | Export-Csv $logPath -NoTypeInformation -Append
} else {
  $anchoredRows | Export-Csv $logPath -NoTypeInformation
}

$shadowCandidates = $anchoredRows | Where-Object { $_.status -eq "shadow_candidate" }
$rejectedAnchored = $anchoredRows | Where-Object { $_.status -eq "rejected" }

$status = [PSCustomObject]@{
  status = "ok"
  started_at_utc = $started
  ended_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  prediction_snapshot_before_utc = $predictionSnapshotBeforeUtc
  prediction_snapshot_after_utc = $predictionSnapshotAfterUtc
  anchored_rows = @($anchoredRows).Count
  shadow_candidates = @($shadowCandidates).Count
  rejected_anchored_rows = @($rejectedAnchored).Count
  active_shadow_candidates = @($shadowCandidates | Select-Object family, market_slug, outcome, executable_price, anchor_fair_probability, risk_adjusted_anchor_edge)
  persistence_log = $logPath
}

$status |
  ConvertTo-Json -Depth 8 |
  Set-Content .\work\strategy_v2_cycle_latest_status.json -Encoding UTF8

"`n=== STRATEGY V2 CYCLE COMPLETE ==="
$status | Format-List

"`n=== ACTIVE SHADOW CANDIDATES ==="
$shadowCandidates |
  Select-Object family, market_slug, outcome, executable_price, anchor_fair_probability, risk_adjusted_anchor_edge, spread, liquidity |
  Format-List



