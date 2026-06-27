# Auto-submit SuperBru picks from your own machine (reliable)

## Why local

The scheduled GitHub Auto Pick is unreliable, which is why you keep filling picks in by hand:

- It runs **headless on a datacenter IP**, and SuperBru's login/anti-bot blocks it — a run that
  actually computed picks **exited code 2** (submission failed).
- GitHub cron timing **drifts**, so many runs fire outside the narrow pre-kickoff window and
  silently do nothing (`submitted: 0`, green).

Running the **same** auto-pick on your machine in a **visible Chrome on your home IP** clears both
problems. `scripts/run_superbru_autopick_local.ps1` does exactly that (it passes `--headed` and a
wide window). Schedule it and you stop entering picks manually.

## One-time setup (Windows)

1. **Install dependencies** (once):

   ```powershell
   cd C:\path\to\Claude
   pip install -e ".[scraper]"
   python -m playwright install chromium
   ```

2. **Store your credentials** in your user environment (persists; open a NEW terminal after):

   ```powershell
   setx SUPERBRU_USERNAME "you@email.com"
   setx SUPERBRU_PASSWORD "your-superbru-password"
   setx SUPERBRU_POOL_URL "https://www.superbru.com/worldcup_predictor/pool_view.php?t=1296&p=13236623&view=matches"
   setx THE_ODDS_API_KEY  "your-odds-api-key"
   ```

   (`SUPERBRU_POOL_URL` is your pool's *matches* view; the default in the script is the WC pool.)

3. **Test it once, watching the browser:**

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\run_superbru_autopick_local.ps1
   ```

   A Chrome window opens, logs in, and submits any unlocked match kicking off in the next 3 hours.
   Check the printed JSON for `"submitted"` > 0, and confirm the picks on SuperBru. The log is saved
   under `outputs\pregame_checks\auto_pick_local\`.

## Schedule it (Task Scheduler)

Run it every 30 minutes during match hours. This one task fires at 14:00 and repeats every 30 min
for 14 hours (≈ until 04:00), covering the WC slate:

```powershell
schtasks /Create /TN "SuperBru Auto Pick" /SC DAILY /ST 14:00 /RI 30 /DU 14:00 /RL LIMITED ^
  /TR "powershell -ExecutionPolicy Bypass -WindowStyle Minimized -File C:\path\to\Claude\scripts\run_superbru_autopick_local.ps1"
```

Notes:
- Replace `C:\path\to\Claude` with your repo path.
- A visible Chrome briefly appears each run (that's what dodges the bot block). Keep the schedule to
  match hours so it isn't popping up all day. `-WindowStyle Minimized` hides the PowerShell window,
  not Chrome.
- The task runs **only while you're logged in** (it needs a desktop session for the visible browser).
- Wide window + every-30-min = each kickoff gets ~6 submission attempts in the 3 h before it; cron
  jitter can't cause a miss, and re-submitting the same pick is harmless.

Check it's working: `outputs\pregame_checks\auto_pick_local\*.log` and your SuperBru picks.

## If fresh login still gets blocked (escalation)

If even the headed run can't log in (SuperBru challenges the Playwright login), switch to driving a
Chrome you've **already** logged into, via the persistent CDP profile the repo uses for scraping
(`run_daily_superbru_local.ps1` starts Chrome with `--remote-debugging-port=9222` and a persistent
`--user-data-dir`). That reuses your authenticated session and never re-logs-in. Ask and I'll wire
the submitter to attach to that CDP session instead of launching a fresh browser.

## The GitHub workflow is now backup only

`auto_pick.yml` still runs (window widened, and `--require-submission` makes it **fail loudly** when
it can't submit, so you get a notification instead of a silent miss). But treat it as a backup —
the local scheduled run above is what reliably posts your picks.
