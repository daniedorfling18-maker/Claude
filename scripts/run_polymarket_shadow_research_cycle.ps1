param(
  [string]$ConfigPath = "polymarket_predictive_config.example.yaml",
  [int]$WebsocketSeconds = 90
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot
New-Item -ItemType Directory -Force .\work | Out-Null

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile = Join-Path $repoRoot "work\shadow_research_cycle_$stamp.log"
$statusFile = Join-Path $repoRoot "work\shadow_research_cycle_latest_status.json"

function Write-LogLine {
  param([string]$Message)
  $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
  $line | Tee-Object -FilePath $logFile -Append
}

function Invoke-Step {
  param(
    [string]$Name,
    [string[]]$Arguments,
    [string]$OutFile
  )
  Write-LogLine "=== $Name ==="
  & python @Arguments 2>&1 | Tee-Object -FilePath $OutFile | Tee-Object -FilePath $logFile -Append
  if ($LASTEXITCODE -ne 0) {
    throw "$Name failed with exit code $LASTEXITCODE"
  }
}

$startedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$status = [ordered]@{
  status = "running"
  started_at_utc = $startedAt
  stamp = $stamp
  log_file = $logFile
  paper_trading_invoked = $false
  live_trading_invoked = $false
}
$status | ConvertTo-Json -Depth 8 | Set-Content $statusFile -Encoding UTF8

Write-LogLine "Starting shadow-only Polymarket research cycle"
Write-LogLine "Repo: $repoRoot"
Write-LogLine "Config: $ConfigPath"
Write-LogLine "Websocket seconds: $WebsocketSeconds"

# Hard safety guard: this scheduled research cycle must never run the local bot loop.
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
  Where-Object { $_.CommandLine -like "*run_polymarket_local_live_loop.py*" } |
  ForEach-Object {
    Write-LogLine "Stopping local live loop process id $($_.ProcessId) before research cycle"
    Stop-Process -Id $_.ProcessId -Force
  }

$env:PYTHONPATH = (Resolve-Path .\src).Path

try {
  Invoke-Step "liquidity-discovery" @(".\scripts\run_polymarket_liquidity_discovery.py", "--config", $ConfigPath) ".\work\liquidity_discovery_shadow_research_$stamp.json"
  Invoke-Step "collect-websocket" @("-m", "polymarket_predictive_engine.cli", "collect-websocket", "--config", $ConfigPath, "--websocket-seconds", "$WebsocketSeconds") ".\work\collect_shadow_research_$stamp.json"
  Invoke-Step "normalize-websocket" @("-m", "polymarket_predictive_engine.cli", "normalize-websocket", "--config", $ConfigPath) ".\work\normalize_shadow_research_$stamp.json"
  Invoke-Step "build-features-v2" @("-m", "polymarket_predictive_engine.cli", "build-features-v2", "--config", $ConfigPath, "--source", "websocket", "--allow-unlabelled-research-features") ".\work\features_shadow_research_$stamp.json"
  Invoke-Step "predict" @("-m", "polymarket_predictive_engine.cli", "predict", "--config", $ConfigPath, "--source", "websocket") ".\work\predict_shadow_research_$stamp.json"
  Invoke-Step "score-mispricing-alpha" @("-m", "polymarket_predictive_engine.cli", "score-mispricing-alpha", "--config", $ConfigPath) ".\work\alpha_shadow_research_$stamp.json"
  Invoke-Step "generate-signals-dry" @("-m", "polymarket_predictive_engine.cli", "generate-signals", "--config", $ConfigPath) ".\work\signals_shadow_research_$stamp.json"
  Invoke-Step "alpha-candidate-shadow-evidence" @(".\scripts\run_alpha_candidate_shadow_evidence.py", $ConfigPath) ".\work\alpha_candidate_shadow_research_$stamp.json"
  Invoke-Step "local-history-audit" @(".\scripts\audit_polymarket_local_history.py", $ConfigPath) ".\work\local_history_audit_$stamp.json"

  Copy-Item ".\work\local_history_audit_$stamp.json" ".\work\local_history_audit_latest.json" -Force
  $audit = Get-Content ".\work\local_history_audit_$stamp.json" -Raw | ConvertFrom-Json
  $liquiditySummaryPath = ".\outputs\polymarket_model_governance\liquidity_discovery_summary.json"
  $liquidity = $null
  if (Test-Path $liquiditySummaryPath) {
    $liquidity = Get-Content $liquiditySummaryPath -Raw | ConvertFrom-Json
  }
  $endedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  $status = [ordered]@{
    status = "ok"
    started_at_utc = $startedAt
    ended_at_utc = $endedAt
    stamp = $stamp
    paper_allowed = $audit.paper_decision.paper_allowed
    paper_reason = $audit.paper_decision.reason
    approved_signals = $audit.approved_signals
    rejected_signals = $audit.rejected_signals
    shadow_positions = $audit.shadow_positions
    shadow_total_pnl_usdc = $audit.shadow_total_pnl_usdc
    shadow_roi = $audit.shadow_roi
    liquidity_tradable_tokens = if ($liquidity) { $liquidity.tradable_tokens } else { $null }
    liquidity_fast_feedback_tradable_tokens = if ($liquidity) { $liquidity.fast_feedback_tradable_tokens } else { $null }
    log_file = $logFile
    audit_file = ".\work\local_history_audit_$stamp.json"
    report_file = ".\outputs\polymarket_model_governance\local_history_audit_report.md"
    liquidity_report_file = ".\outputs\polymarket_model_governance\liquidity_discovery_summary.json"
    paper_trading_invoked = $false
    live_trading_invoked = $false
  }
  $status | ConvertTo-Json -Depth 8 | Set-Content $statusFile -Encoding UTF8
  Write-LogLine "Cycle completed. Paper allowed: $($audit.paper_decision.paper_allowed). Reason: $($audit.paper_decision.reason)"
  exit 0
} catch {
  $endedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  $status = [ordered]@{
    status = "error"
    started_at_utc = $startedAt
    ended_at_utc = $endedAt
    stamp = $stamp
    error = $_.Exception.Message
    log_file = $logFile
    paper_trading_invoked = $false
    live_trading_invoked = $false
  }
  $status | ConvertTo-Json -Depth 8 | Set-Content $statusFile -Encoding UTF8
  Write-LogLine "ERROR: $($_.Exception.Message)"
  exit 1
}

