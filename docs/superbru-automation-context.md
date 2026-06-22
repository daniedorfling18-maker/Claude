# SuperBru Automation Context

This document records the current production automation setup for the World Cup 2026 SuperBru engine.

## Current production split

The automation is split into two separate responsibilities:

1. **Refresh Locked Superbru Card** keeps the committed card fresh.
2. **Auto Pick (Scheduled)** submits the latest locked-card scoreline to SuperBru before kickoff.

This separation is intentional. The refresh workflow prepares the card; the auto-pick workflow submits from the card.

## Source of truth

The submitted scoreline comes from:

```text
outputs/final_locked_picks/superbru_final_card.csv
```

Auto Pick should not invent a pick. If the upcoming match is missing from this file, it should not submit.

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

- skips market-odds refresh by default;
- skips expensive final simulation by default;
- market odds are refreshed only when manually requested with `refresh_market_odds=true`.

This workflow does not submit picks to SuperBru.

## Auto Pick Scheduled Submitter

Workflow:

```text
.github/workflows/auto_pick.yml
```

Active runner:

```text
scripts/auto_pick_match_scoped.py
```

`scripts/auto_pick_match_scoped_smart_odds.py` is now a thin backward-compatible shim
that delegates to the base runner. The smart-odds quota guard it used to own is built
into the base runner and on by default (`--smart-odds`). The base runner additionally
adds live pre-kickoff recompute, leaderboard pool-position intelligence, and Oddspedia
correct-score grid blending.

Schedule:

- fires roughly 25 minutes before each configured kickoff;
- `window_minutes` defaults to 40 in the workflow to absorb GitHub's best-effort cron delay.

## Smart Odds API spending

The base runner spends Odds API credits only when action is needed.

For each queued match:

1. Read the locked-card score from `superbru_final_card.csv`.
2. Read the score currently visible in SuperBru's score inputs.
3. If the visible score already matches the locked-card score **and no leaderboard
   pool-position override is active** for this run:
   - mark the match as `already_picked`;
   - skip the one-match odds snapshot;
   - skip submit.
4. Otherwise:
   - fetch a one-match odds snapshot (unless `--skip-match-odds`);
   - recompute the pick live from fresh odds, applying pool-position intelligence and the
     Oddspedia correct-score blend;
   - submit the recomputed pick (falling back to the committed card pick if the recompute
     is unavailable).

Why the override caveat: when the leaderboard scrape shows you leading with a tight gap
or chasing within range, the live recompute may need to override the committed card pick
(defensive or private-chase). In that state the runner always pulls odds and recomputes;
the `already_picked` shortcut only applies when pool standing is inactive, where the
recompute resolves to the same engine strategy pick the card already encodes.

Expected behaviour:

```text
Primary run:  spends credits to recompute and submit the best pick before kickoff.
Backup run:   spends zero credits if the primary already saved that pick and no
              pool-position override applies.
```

## Manual dispatch controls

Auto Pick manual input:

```text
fetch_match_odds=true
```

This is the normal smart-spend path. It only fetches one-match odds when the visible SuperBru score does not already match the locked card.

Use:

```text
fetch_match_odds=false
```

only when you want a no-odds submit attempt from the locked card.

Refresh manual input:

```text
refresh_market_odds=true
```

spends The Odds API credits during card refresh. Leave it false unless intentionally refreshing market odds.

## Expected Auto Pick statuses

```text
already_picked      SuperBru already shows the card pick and no override active; no odds, no submit.
submitted           Pick (live recompute or card fallback) was submitted.
dry_run             Run checked but did not submit.
no_pick_available   No fresh recompute and no committed card pick to fall back to.
locked_skipped      SuperBru inputs were locked.
no_inputs_skipped   Score inputs were not found.
not_in_window       Match was not inside the submission window.
submit_failed       Submit attempt failed after queueing.
```

Per-match the summary also records `pick_source` (`live_odds_recompute`,
`committed_card_fallback`, or `already_picked_no_spend`), `pick_strategy`
(`leading_comfortable`, `defensive_leader`, `private_chase`, `raw_ev_far_behind`,
`engine_<mode>`, …), and `pick_changed_vs_card`.

## Important artifacts

```text
outputs/pregame_checks/auto_pick/
outputs/pregame_checks/auto_pick/match_odds/
outputs/pregame_checks/auto_pick/submit_diagnostics/
outputs/daily_robust_card/
outputs/final_locked_picks/
```

The Auto Pick summary mode is:

```json
"mode": "match_scoped_locked_card_auto_pick"
```

## Final operational rule

The automation should be treated as follows:

```text
Refresh Locked Superbru Card = prepares the card.
Auto Pick = submits from the card.
Odds API = used only when the pick needs action or when a manual refresh explicitly asks for market odds.
```
