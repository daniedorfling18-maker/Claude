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
scripts/auto_pick_match_scoped_smart_odds.py
```

Supporting alias files:

```text
scripts/team_name_aliases.py
scripts/submit_superbru_pick_cdp_aliases.py
```

Schedule:

- runs 20 minutes before each configured kickoff;
- runs again 10 minutes before each configured kickoff as a backup;
- uses a default `window_minutes` value of 25 to tolerate GitHub cron delay.

## Team-name aliases

Team-name matching is alias-aware across the card lookup, one-match Odds API event lookup, and in-browser SuperBru row/subtab matching.

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

The active smart runner patches the base team normalizer to use `canonical_team_key`, so a canonical locked-card row such as `United States` can match a SuperBru row or tab labelled `USA`.

The alias-aware submitter wrapper replaces the browser JavaScript row and subtab matchers so the in-browser submit step accepts canonical names plus all known aliases.

## Smart Odds API spending

The smart runner is designed to spend Odds API credits only when action is needed.

For each queued match:

1. Read the locked-card score from `superbru_final_card.csv`.
2. Read the score currently visible in SuperBru's score inputs.
3. If the visible score already matches the locked-card score:
   - mark the match as `already_picked`;
   - skip the one-match odds snapshot;
   - skip submit.
4. If the visible score is blank or different:
   - fetch a one-match odds snapshot, unless explicitly disabled;
   - submit the locked-card scoreline.

Expected behaviour:

```text
20-minute run: spends credits only if the pick needs to be submitted or changed.
10-minute backup: spends zero credits if the 20-minute run already saved the pick.
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
already_picked      SuperBru already shows the locked-card score; no odds and no submit.
submitted           Locked-card score was submitted.
dry_run             Run checked but did not submit.
pick_card_missing   Match was in the window but missing from the locked card.
locked_skipped      SuperBru inputs were locked.
no_inputs_skipped   Score inputs were not found.
not_in_window       Match was not inside the submission window.
submit_failed       Submit attempt failed after queueing.
```

## Important artifacts

```text
outputs/pregame_checks/auto_pick/
outputs/pregame_checks/auto_pick/match_odds/
outputs/pregame_checks/auto_pick/submit_diagnostics/
outputs/daily_robust_card/
outputs/final_locked_picks/
```

The Auto Pick summary mode should be:

```json
"mode": "match_scoped_locked_card_auto_pick_smart_odds_aliases"
```

## Final operational rule

The automation should be treated as follows:

```text
Refresh Locked Superbru Card = prepares the card.
Auto Pick = submits from the card.
Odds API = used only when the pick needs action or when a manual refresh explicitly asks for market odds.
Team aliases = canonical card names and SuperBru/Odds short labels must resolve to the same team key.
```
