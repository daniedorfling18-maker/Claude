# SuperBru CLV-vs-close experiment

Generated locally on 2026-07-03 using:

```powershell
python scripts/run_superbru_clv_vs_close_experiment.py
```

## Verdict

True CLV-vs-close is not yet computable from the current repo snapshot set.

The SuperBru engine needs, for each pick:

- an earlier pick-time bookmaker quote;
- a later near-close bookmaker quote;
- the same selected outcome mapped across both quotes.

The repo currently has only one market-history snapshot for the current SuperBru card, so the experiment can tell us the instrumentation gap decisively, but it cannot yet tell us whether the picks beat the close.

## Data coverage

| Check | Value |
| --- | ---: |
| Pick rows checked | 24 |
| Pick sources | 13 locked-engine rows + 11 upload-card rows |
| Market-matched rows | 20 |
| Market-matched unique matches | 13 |
| Prediction proxy rows | 20 |
| Prediction proxy unique matches | 13 |
| Market-history snapshots | 1 |
| Usable CLV rows | 0 |
| Smartbet/Oddspedia grid overlap rows | 0 |

## Available market-edge proxy

Because true CLV is unavailable, the script computes a weaker proxy:

> selected outcome model probability minus de-vigged bookmaker h2h consensus probability at the available market snapshot.

This proxy is not sufficient for promotion, but it is still useful as a sanity check.

| Metric | Value |
| --- | ---: |
| Mean model edge vs market | +0.001612 |
| Median model edge vs market | +0.000753 |
| Positive unique rows | 9 |
| Negative unique rows | 4 |
| Positive rate | 69.23% |

Interpretation: the SuperBru model is broadly market-aligned, with only a very thin positive outcome-probability difference versus bookmaker consensus. That is not enough to claim a proprietary betting edge. The possible SuperBru edge, if any, is more likely in exact-score contest/game-theory mechanics than in raw 1X2 market prediction.

## Strongest positive proxy rows

| Match | Pick | Outcome | Market p | Model p | Edge | Hours from market snapshot to kickoff |
| --- | ---: | --- | ---: | ---: | ---: | ---: |
| Australia vs Egypt | 0-1 | away | 0.385738 | 0.391044 | +0.005306 | 79.743 |
| Mexico vs Ecuador | 1-0 | home | 0.424462 | 0.428156 | +0.003694 | 14.743 |
| Switzerland vs Algeria | 2-0 | home | 0.463059 | 0.466741 | +0.003682 | 64.743 |
| Belgium vs Senegal | 2-0 | home | 0.431362 | 0.434915 | +0.003553 | 33.743 |
| Ivory Coast vs Norway | 1-2 | away | 0.448341 | 0.451369 | +0.003027 | 6.743 |

## Strongest negative proxy rows

| Match | Pick | Outcome | Market p | Model p | Edge | Hours from market snapshot to kickoff |
| --- | ---: | --- | ---: | ---: | ---: | ---: |
| United States vs Bosnia & Herzegovina | 2-0 | home | 0.695822 | 0.695278 | -0.000544 | 37.743 |
| Spain vs Austria | 2-0 | home | 0.721164 | 0.720722 | -0.000441 | 56.743 |
| Canada vs Morocco | 0-2 | away | 0.537618 | 0.537361 | -0.000258 | 102.743 |
| England vs DR Congo | 2-0 | home | 0.750356 | 0.750274 | -0.000082 | 29.743 |
| France vs Sweden | 2-0 | home | 0.740885 | 0.740939 | +0.000054 | 10.743 |

## Promotion rule

Do not promote the SuperBru engine based on this result alone.

Promotion should require positive forward evidence:

- repeated odds snapshots saved from pick time to close;
- positive CLV by pick family;
- no degradation after exact-score/game-theory adjustments;
- no one-family concentration;
- dashboard visibility of CLV by match, pick source, and family.

## Implementation artifact

The reusable runner is:

```text
scripts/run_superbru_clv_vs_close_experiment.py
```

Generated local outputs are written to:

```text
outputs/superbru_clv_experiment/
```
