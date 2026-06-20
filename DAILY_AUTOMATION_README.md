# Daily Superbru Automation

This is the production operating flow for the World Cup Superbru engine.

The goal is simple:

1. refresh market odds;
2. build the robust Superbru card;
3. refresh the Oddspedia SmartBet correct-score grid through a verified local Chrome session;
4. compare the locked picks against the Oddspedia grid;
5. notify only when there are new action items.

## Why this is local, not pure GitHub Actions

Oddspedia is Cloudflare-protected. GitHub Actions cannot reliably clear the Cloudflare browser challenge, so the Oddspedia grid capture must run locally through a normal Chrome profile.

GitHub Actions still runs the daily robust pipeline and can consume committed Oddspedia grid files, but the reliable SmartBet refresh step is local.

## One-time setup

Install dependencies:

```powershell
python -m pip install pandas numpy scipy pyyaml requests playwright tabulate
python -m playwright install chromium
```

Set the odds API key in your environment or GitHub secret:

```powershell
$env:THE_ODDS_API_KEY = "YOUR_REAL_KEY"
```

For GitHub issue notifications from the local task, install and authenticate GitHub CLI:

```powershell
gh auth login
```

## First verified Oddspedia run

Run this manually once so Chrome can clear Cloudflare and create a persistent profile:

```powershell
.\scripts\run_daily_superbru_local.ps1 `
  -ManualOnMissing `
  -NotifyOnFirstState `
  -CreateGitHubIssue `
  -CommitAndPushOutputs
```

If Chrome opens a Cloudflare check, complete it. Keep the Chrome profile folder `.chrome-oddspedia-profile` intact so future runs reuse the clearance cookie.

## Install the daily scheduled task

This installs a Windows Task Scheduler job that runs every day at 07:00 while you are logged in:

```powershell
.\scripts\install_daily_superbru_task.ps1 `
  -At "07:00" `
  -CreateGitHubIssue `
  -CommitAndPushOutputs
```

The task is installed as **Daily Superbru Local Automation**.

Test it immediately:

```powershell
Start-ScheduledTask -TaskName "Daily Superbru Local Automation"
```

Check task history in Task Scheduler if it does not appear to run.

## What the daily task does

The task runs:

```powershell
.\scripts\run_daily_superbru_local.ps1 -CreateGitHubIssue -CommitAndPushOutputs
```

It performs these steps:

1. captures the previous robust Superbru card;
2. runs `scripts/run_daily_robust_pipeline.py`;
3. starts or attaches to Chrome on CDP port 9222;
4. runs `scripts/scrape_oddspedia_cdp_session.py`;
5. writes:
   - `inputs/smartbet_grids/oddspedia_probability_grids_auto.csv`
   - `inputs/smartbet_grids/oddspedia_probability_summary_auto.csv`
6. runs `scripts/compare_locked_picks_to_oddspedia.py`;
7. runs `scripts/notify_score_changes.py`;
8. runs `scripts/notify_daily_superbru_action_items.py`;
9. creates a GitHub issue only if the action-item digest changed;
10. commits and pushes refreshed outputs when `-CommitAndPushOutputs` is used.

## Notification logic

A notification is created only when the combined daily action set changes.

The combined action set includes:

- robust-card score changes versus the previous card; and
- Oddspedia SmartBet review items where the locked score is materially weaker than a same-outcome alternative, or where Oddspedia creates a market-outcome conflict.

The state file is:

```text
outputs/daily_notifications/daily_superbru_action_state.json
```

If the same action set appears again tomorrow, the notifier suppresses the duplicate notification.

The daily report is written to:

```text
outputs/daily_notifications/daily_superbru_action_items.md
outputs/daily_notifications/daily_superbru_action_items.json
```

## Manual review command

To inspect review items manually:

```powershell
Import-Csv outputs\oddspedia_pick_validation\oddspedia_pick_comparison.csv |
  Where-Object {$_.action -ne "keep" -and $_.action -ne "no_grid"} |
  Sort-Object {[double]$_.probability_gap_vs_locked_pct} -Descending |
  Format-Table match_id,locked_pick,locked_pick_probability_pct,oddspedia_best_score,oddspedia_best_probability_pct,probability_gap_vs_locked_pct,action -AutoSize
```

## Important operating rule

Oddspedia SmartBet is a calibration and review layer. It is not an automatic switch engine.

A higher-probability Oddspedia modal score does not automatically replace the Superbru pick. Any change must still pass:

- Superbru expected-points logic;
- leader/chaser risk logic;
- robust-policy checks;
- manual judgement where the market and model conflict.

## Troubleshooting

### Chrome CDP port not available

Close all Chrome windows and kill background Chrome:

```powershell
taskkill /F /IM chrome.exe
```

Then rerun the daily task or manual command.

### Cloudflare blocks again

Run the manual mode:

```powershell
.\scripts\run_daily_superbru_local.ps1 -ManualOnMissing -CreateGitHubIssue -CommitAndPushOutputs
```

Complete the Cloudflare challenge in the opened Chrome window.

### Oddspedia grid count is low

Check:

```powershell
Get-Content outputs\oddspedia_probability_extract\oddspedia_cdp_probability_extract_summary.json
```

Expected:

```text
matches_with_grid = number of input URLs
correct_score_row_count = 19 x matches_with_grid
```

If not, inspect diagnostics:

```powershell
Get-ChildItem outputs\oddspedia_probability_extract\cdp_diagnostics
```
