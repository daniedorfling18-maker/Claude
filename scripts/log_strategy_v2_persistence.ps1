param(
  [string]$ConfigPath = "polymarket_predictive_config.example.yaml"
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot

.\scripts\run_polymarket_strategy_v2_anchored_edge.ps1 -ConfigPath $ConfigPath | Out-Null

$logPath = ".\outputs\polymarket_strategy_v2\anchored_edge_persistence_log.csv"

$rows = Import-Csv .\outputs\polymarket_strategy_v2\anchored_edge_candidates.csv |
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
  $rows | Export-Csv $logPath -NoTypeInformation -Append
} else {
  $rows | Export-Csv $logPath -NoTypeInformation
}

Import-Csv $logPath |
  Select-Object logged_at_utc, family, market_slug, outcome, status, blockers, executable_price, anchor_fair_probability, risk_adjusted_anchor_edge, spread, liquidity |
  Format-Table -AutoSize
