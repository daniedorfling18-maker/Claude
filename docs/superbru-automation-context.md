# SuperBru Automation Context

This document records the current production automation setup for the World Cup 2026 SuperBru engine.

## Current production split

The automation is split into two separate responsibilities:

1. **Refresh Locked Superbru Card** keeps the committed card fresh.
2. **Auto Pick (Scheduled)** submits the latest scoreline to SuperBru before kickoff.

This separation is intentional. The refresh workflow prepares the card; the auto-pick workflow submits from the card or from a live one-match recompute.

## Source of truth and fallback

The committed card is:

```text
outputs/final_locked_picks/superbru_final_card.csv
```

Auto Pick uses this card as the fallback/source-of-truth row set. When match odds are enabled, Auto Pick fetches only the imminent match's odds and recomputes a fresh pick before submission. If the fresh recompute is unavailable, it falls back to the committed card pick.

## Refresh Locked Superbru Card

Workflow:

```text
.github/workflows/refresh-locked-superbru-card.yml
```

Purpose:

- regenerate the locked-card outputs;
- validate that `superbru_final_card.csv` exists and has rows;
- upload refresh artifacts;
- commit refreshed output files.

Schedule:

```text
06:05 UTC / 08:05 SAST
14:05 UTC / 16:05 SAST
```

Default quota posture:

- skips market-odds refresh by default (runs with `--skip-market-odds-fetch`);
- skips expensive final simulation by default (runs with `--skip-final-simulation`);
- market odds are refreshed only by removing `--skip-market-odds-fetch` from the workflow command, or by running the pipeline locally.

This workflow does not submit picks to SuperBru.

## Auto Pick Scheduled Submitter

Workflow:

```text
.github/workflows/auto_pick.yml
```

Active entrypoint:

```text
scripts/auto_pick_match_scoped_smart_odds.py
```

Important: despite the legacy filename, this entrypoint currently delegates to the full live runner in `scripts/auto_pick_match_scoped.py` after patching alias-aware team matching and alias-aware browser submission.

Supporting alias files:

```text
scripts/team_name_aliases.py
scripts/submit_superbru_pick_cdp_aliases.py
```

Schedule:

- runs once per configured kickoff, roughly 25 minutes before kickoff;
- uses a default `window_minutes` value of 40 to tolerate GitHub cron delay;
- does not use the older 20/10 double-fire design.

## Odds API spending

The scheduled Auto Pick run uses a match-scoped odds pull, not a whole-tournament pull.

Default behaviour:

1. Log in to SuperBru.
2. Scan match tabs and queue only matches inside the pre-kickoff window.
3. Fetch a one-match odds snapshot for the queued match.
4. Recompute a fresh pick from that one-match snapshot and the engine config.
5. Apply pool-position intelligence if leaderboard scraping succeeds.
6. Submit the recomputed pick; if recompute fails, submit the committed card fallback pick.

Expected quota posture:

```text
Scheduled Auto Pick = about one match-scoped odds pull per kickoff.
Refresh Locked Superbru Card = zero Odds API pulls (cached odds via --skip-market-odds-fetch).
Local full pipeline without --skip-market-odds-fetch = intentionally spends refresh credits.
Auto Pick with no odds / recompute failure = submit committed card fallback pick only.
```

The workflow comments estimate match odds cost from the configured region and market scope. The base runner resolves unset `odds_regions` / `odds_markets` from `config.yaml`, defaulting to `eu` and `h2h,totals` when no config value exists.

## Team-name aliases

Team-name matching is alias-aware across the card lookup, one-match Odds API event lookup, orientation checks, and in-browser SuperBru row/subtab matching.

Examples that should match each other:

```text
United States / USA / US
South Korea / Korea Republic / KOR
Czechia / Czech Republic / CZE
Iran / IR Iran / IRI / IRN
DR Congo / Democratic Republic of the Congo / DRC / COD
Bosnia and Herzegovina / Bosnia / BIH / BHI
Curacao / Curaçao / CUR / CUW
Ivory Coast / Côte d'Ivoire / CIV
```

Alias implementation:

- `scripts/team_name_aliases.py` provides `canonical_team_key`.
- `scripts/auto_pick_match_scoped_smart_odds.py` patches the base runner's `norm_team` to `canonical_team_key`.
- `scripts/submit_superbru_pick_cdp_aliases.py` patches the browser JavaScript row and subtab matchers so SuperBru labels such as `USA` can match canonical card names such as `United States`.

## Schedule-only operation

Both workflows are schedule-only. CI forbids a `workflow_dispatch` trigger on the automated
workflows (see the workflow-validation step in `ci.yml`), so there are no manual run-time inputs
such as `fetch_match_odds` or `refresh_market_odds`. Each workflow's behaviour is fixed in its YAML:

- **Auto Pick** always performs a match-scoped odds pull and live recompute for any match inside
  the pre-kickoff window, and falls back to the committed card pick when the recompute is unavailable.
- **Refresh Locked Superbru Card** always runs
  `scripts/run_daily_robust_pipeline.py --skip-final-simulation --skip-market-odds-fetch`,
  rebuilding the card from committed cached odds and spending no Odds API credits.

To change posture, edit the workflow command or run the script locally — for example, drop
`--skip-market-odds-fetch` to refresh market odds during a rebuild, drop `--skip-final-simulation`
to run the final-leader simulation, or run `python scripts/run_daily_robust_pipeline.py` with no
skip flags for the full pipeline.

## Expected Auto Pick statuses

```text
submitted              Scoreline was submitted.
dry_run                Run checked but did not submit.
no_pick_available      Neither a live recompute nor committed-card fallback was available.
pick_card_missing      Match was in the window but missing from the locked card.
locked_skipped         SuperBru inputs were locked.
no_inputs_skipped      Score inputs were not found.
not_in_window          Match was not inside the submission window.
submit_failed          Submit attempt failed after queueing.
```

## Important artifacts

```text
outputs/pregame_checks/auto_pick/
outputs/pregame_checks/auto_pick/match_odds/
outputs/pregame_checks/auto_pick/submit_diagnostics/
outputs/daily_robust_card/
outputs/final_locked_picks/
```

The base Auto Pick summary mode remains:

```json
"mode": "match_scoped_locked_card_auto_pick"
```

The alias-aware entrypoint adds this marker to the printed result:

```json
"alias_aware_entrypoint": true
```

## Final operational rule

```text
Refresh Locked Superbru Card = prepares the card and normally spends zero Odds API credits.
Auto Pick = submits from a live one-match recompute, with committed-card fallback.
Odds API = one match-scoped pull per scheduled kickoff by default, not a whole tournament pull.
Team aliases = canonical card names and SuperBru/Odds short labels resolve to the same team key.
```
