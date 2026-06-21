<#
.SYNOPSIS
    Run Superbru predictions from the most recent cached odds snapshot - spends
    no API quota and dodges the "Python was not found" Microsoft Store stub.

.DESCRIPTION
    Resolves a real Python interpreter, finds the newest *.json odds snapshot
    under -SnapshotDir, runs `superbru_score_engine predict` against it, writes
    to a timestamped folder, and prints the predictions.csv path.

.EXAMPLE
    .\scripts\predict_latest_cached.ps1
    .\scripts\predict_latest_cached.ps1 -Python "C:\path\to\python.exe"
    $env:SUPERBRU_PYTHON = "C:\...\python.exe"; .\scripts\predict_latest_cached.ps1
#>
param(
    [string]$Python      = $env:SUPERBRU_PYTHON,
    [string]$Config      = "config.yaml",
    [string]$SnapshotDir = "outputs\odds-snapshots",
    [string]$OutRoot     = "outputs\predictions"
)

$ErrorActionPreference = "Stop"

function Resolve-Python {
    param([string]$Preferred)
    if ($Preferred -and (Test-Path $Preferred)) { return (Resolve-Path $Preferred).Path }
    if ($Preferred) { return $Preferred }                       # e.g. "py" / name on PATH
    if (Test-Path ".\.venv\Scripts\python.exe") { return (Resolve-Path ".\.venv\Scripts\python.exe").Path }
    if (Get-Command py     -ErrorAction SilentlyContinue) { return "py" }
    if (Get-Command python -ErrorAction SilentlyContinue) { return "python" }
    throw "No Python found. Pass -Python <path> or set `$env:SUPERBRU_PYTHON."
}

$py = Resolve-Python -Preferred $Python
Write-Host "Python:   $py"

$snap = Get-ChildItem -Path $SnapshotDir -Recurse -Filter *.json -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $snap) { throw "No cached odds snapshot (*.json) found under '$SnapshotDir'. Fetch odds first." }
Write-Host "Snapshot: $($snap.FullName)"

$stamp  = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
$outDir = Join-Path $OutRoot $stamp

& $py -m superbru_score_engine predict --config $Config --odds-json $snap.FullName --out-dir $outDir
if ($LASTEXITCODE -ne 0) { throw "predict failed (exit $LASTEXITCODE)" }

Write-Host ""
Write-Host "Predictions: $(Join-Path $outDir 'predictions.csv')"
