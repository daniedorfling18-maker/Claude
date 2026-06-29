param(
  [string]$ConfigPath = "polymarket_predictive_config.example.yaml"
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot
$env:PYTHONPATH = (Resolve-Path .\src).Path

python .\scripts\run_polymarket_strategy_v2_anchored_edge.py $ConfigPath
