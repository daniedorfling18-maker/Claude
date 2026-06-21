# Superbru Backtesting

This documents the backtesting layer: how past picks are scored against results, and how
pre-match signals are archived so they can be evaluated once results arrive.

## How it works

The backtest pipeline has two complementary parts:

**Reactive scoring** (`build_superbru_backtest_from_results.py`) — joins completed match results
to locked picks and scores them with Superbru-style points. This runs automatically as step 6 of
the daily Oddspedia pipeline.

**Signal archiving** (`build_oddspedia_signal_archive.py`) — snapshots each day's Oddspedia
signals, EV recommendations, and independence classifications into a rolling CSV. When results
arrive in later rounds, this archive can be joined against results to evaluate whether the
pre-match signals (OU gap, independence class, EV ranking) predicted actual performance.

## Key output files

```text
outputs/backtesting/superbru_pick_backtest.csv          — picks scored against completed results
outputs/backtesting/backtest_summary.json               — summary metrics (hit rates, total points)
outputs/backtesting/signal_archive_rolling.csv          — all signals archived by day (appended)
outputs/backtesting/snapshots/signal_archive_YYYY-MM-DD.csv  — point-in-time snapshot per run
```

## Scoring rules

```text
Exact score:                         3.0 pts
Correct result + correct goal diff:  1.5 pts
Correct result only:                 1.0 pt
Wrong result:                        0.0 pts
```

Configurable via `--exact-points`, `--margin-points`, `--result-points`.

## Reactive scoring

Runs automatically as step 6 of:

```bash
python scripts/run_oddspedia_pipeline.py
```

Or manually:

```bash
python scripts/build_superbru_backtest_from_results.py \
  --results-csv outputs/superbru_pool/superbru_match_results_auto.csv \
  --picks-csv outputs/final_locked_picks/superbru_final_card.csv \
  --oddspedia-comparison-csv outputs/oddspedia_pick_validation/oddspedia_pick_comparison.csv
```

The script joins on `match_id` (slugified `home_team-away_team`). If results are from a different
round than the locked picks, `completed_matches_with_picks` in the summary JSON will be 0 — this
is expected and not an error. Results are sourced from `outputs/superbru_pool/superbru_match_results_auto.csv`.

## Signal archiving

Runs automatically as step 10 of the daily pipeline. Re-running on the same date is idempotent:
today's rows are replaced, not duplicated.

Or manually:

```bash
python scripts/build_oddspedia_signal_archive.py
```

The rolling archive accumulates one row per match per day. The columns include:

| Column | What it captures |
|--------|-----------------|
| `archive_date` | Date of pipeline run |
| `locked_pick` | Pick at archive time |
| `locked_pick_ev` | Expected Superbru points for locked pick |
| `best_ev_scoreline` | Highest-EV alternative from Oddspedia |
| `ev_gap_vs_locked` | EV gap: positive means we could switch to a better score |
| `oddspedia_top1_score` | Highest-probability CS score from grid |
| `grid_over_2_5_pct` | Grid's over-2.5 probability (vs `market_p_over_2_5`) |
| `grid_btts_pct` | Grid's BTTS probability (vs `market_p_btts_yes`) |
| `ou25_diff_pct` | Market minus grid on OU2.5 |
| `independence_class` | `market_aligned` / `mildly_independent` / `strongly_independent` |
| `signal_consistency` | Whether OU and BTTS divergences point the same direction |
| `pick_follows` | Whether locked pick follows grid or market when they disagree |
| `actual_score` | Filled in retrospectively after match |
| `backtest_points` | Filled in retrospectively after match |

## Using the archive for retrospective analysis

Once results arrive, join `signal_archive_rolling.csv` against results manually or via the
backtest script to answer questions like:

- Do `strongly_independent` matches produce better EV outcomes than `market_aligned` ones?
- When the OU2.5 gap is large (grid underestimates goals vs market), do we lose points by
  following the grid's preferred low-scoring scorelines?
- Is the `best_ev_scoreline` systematically better than `locked_pick` over multiple rounds?

## Evidence classes

Treat archived signals by recency and method:

- **Daily archive snapshot** — strongest, captured pre-match by the scheduled pipeline.
- **Reactive results join** — scored only for matches where results overlap the picks CSV.
- **Recovered historical grids** — weaker than live snapshots; useful for reference only.

## Backtest summary fields

The `backtest_summary.json` reports:

```json
{
  "result_rows_available": 8,
  "pick_rows_available": 40,
  "completed_matches_with_picks": 0,
  "total_points_estimate": ...,
  "average_points_estimate": ...,
  "exact_hit_rate": ...,
  "outcome_hit_rate": ...,
  "margin_hit_rate": ...,
  "oddspedia_modal_total_points_estimate": ...,
  "points_delta_vs_oddspedia_modal": ...
}
```

`oddspedia_modal_total_points_estimate` shows how many points the Oddspedia top-probability
pick would have scored — useful for comparing our locked picks against the "obvious" alternative.
