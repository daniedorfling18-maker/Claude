$ErrorActionPreference = "Stop"

$repo = "C:\Users\DanieDörfling\Documents\Codex\superbru-engine-ah"
Set-Location $repo

$secretPath = "C:\Users\DanieDörfling\.superbru\the_odds_api_key.txt"

if (!(Test-Path $secretPath)) {
    throw "THE_ODDS_API_KEY file not found at $secretPath"
}

$keyText = Get-Content $secretPath -Raw

if ([string]::IsNullOrWhiteSpace($keyText)) {
    throw "THE_ODDS_API_KEY file exists but is empty."
}

$env:THE_ODDS_API_KEY = $keyText.Trim()

& "$repo\scripts\run_daily_superbru_local.ps1" -CreateGitHubIssue -CommitAndPushOutputs

exit $LASTEXITCODE

