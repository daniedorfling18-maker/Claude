<#
.SYNOPSIS
  Submit SuperBru World Cup picks automatically from your own machine, in a VISIBLE Chrome.

.DESCRIPTION
  GitHub's scheduled Auto Pick runs headless on a datacenter IP, which SuperBru's login/anti-bot
  blocks (runs exit 2 on submit, or fire outside the narrow window and silently do nothing) - so
  picks never actually post and you fill them in by hand.

  This wrapper runs the SAME match-scoped auto-pick locally with --headed (a real, visible Chrome on
  your residential IP) and a wide window, so login + submission succeed. Schedule it every ~30 min
  during match hours (see docs/SUPERBRU_AUTO_SUBMIT_LOCAL.md) and it submits every unlocked match in
  the next few hours, idempotently - no manual entry.

  One-time setup (credentials) is documented in docs/SUPERBRU_AUTO_SUBMIT_LOCAL.md.

.PARAMETER WindowMinutes
  Submit any unlocked match kicking off within this many minutes (default 180). Wide so cron-style
  timing jitter can never miss a kickoff.
#>
param(
  [int]$WindowMinutes = 180,
  [string]$Config = "config.yaml"
)

$ErrorActionPreference = "Stop"
# Repo root = parent of this script's folder.
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$Email    = if ($env:SUPERBRU_USERNAME) { $env:SUPERBRU_USERNAME } else { $env:SUPERBRU_EMAIL }
$Password = $env:SUPERBRU_PASSWORD
$PoolUrl  = if ($env:SUPERBRU_POOL_URL) { $env:SUPERBRU_POOL_URL } else { "https://www.superbru.com/worldcup_predictor/pool_view.php?t=1296&p=13236623&view=matches" }
$LoginUrl = if ($env:SUPERBRU_LOGIN_URL) { $env:SUPERBRU_LOGIN_URL } else { "https://www.superbru.com/login" }

if (-not $Email -or -not $Password) {
  throw "Set SUPERBRU_USERNAME (or SUPERBRU_EMAIL) and SUPERBRU_PASSWORD first. See docs/SUPERBRU_AUTO_SUBMIT_LOCAL.md"
}

$LogDir = Join-Path $RepoRoot "outputs\pregame_checks\auto_pick_local"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir ("run_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")

Write-Host "[$(Get-Date -Format o)] SuperBru auto-pick (LOCAL, headed) window=$WindowMinutes min -> $LogFile"

# --headed => visible Chrome (NOT --headless). No --require-submission so a benign partial pass on a
# wide window doesn't mark the scheduled task failed; the printed summary shows what submitted.
python scripts\auto_pick_match_scoped_smart_odds.py `
  --email "$Email" `
  --password "$Password" `
  --login-url "$LoginUrl" `
  --pool-url "$PoolUrl" `
  --config "$Config" `
  --window-minutes $WindowMinutes `
  --pick-card-csv outputs\final_locked_picks\superbru_final_card.csv `
  --odds-lookup-window-minutes 90 `
  --headed `
  --out-dir "$LogDir" 2>&1 | Tee-Object -FilePath $LogFile

$code = $LASTEXITCODE
Write-Host "[$(Get-Date -Format o)] auto-pick exit code: $code (0 = ran; see the JSON summary above for 'submitted')"
exit $code
