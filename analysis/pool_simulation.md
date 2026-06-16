# Pool-simulation backtest — do the rank strategies beat the field?

A points backtest cannot evaluate `contrarian`/`exact_chase`, because those trade
expected Superbru points for pool **rank**. `scripts/pool_simulation.py` is a Monte
Carlo that answers the rank question directly.

## Method
- Each WC fixture's market scoreline distribution is rebuilt from the
  `football-data-backtest` per-fixture `lambda_home`/`lambda_away`.
- A synthetic field of `N` players each picks one scoreline sampled from the
  **synthetic public-pick model**, sharpened by `--field-concentration` so the
  field concentrates on popular scorelines (2-0/2-1/1-0) like a real pool.
- Each strategy mode (`raw_ev`/`conservative`/`exact_chase`/`contrarian`/
  `risk_adjusted`) plays as one extra entrant.
- Everyone is scored with real Superbru rules vs the **actual** result, over many
  simulated pools. `--pool-size` shortens the tournament; `--deficit` starts the
  strategy entrant behind the field (models chasing).

Run:
```
python -m superbru_score_engine football-data-backtest --config config.yaml --out-dir outputs/bt-wc
python scripts/pool_simulation.py --results-csv outputs/bt-wc/football_data_backtest_results.csv --players 29
```

## Findings (192 WC games, field of 29, β=8)
| scenario | best by p_win | takeaway |
|---|---|---|
| full pool, starting even | **raw_ev** (p_win 0.98) | raw_ev wins on points *and* rank; `conservative` strictly worst; `contrarian`/`exact_chase` do not help |
| 12 games left, 4 pts behind | **exact_chase** (p_win 0.009 vs raw_ev 0.002) | variance buys a ~4.5x better shot at 1st, at the cost of worse *average* finish |
| 12 games left, 8 pts behind | none (p_win ~0) | too far behind to recover against the field |

## Verdict
- **Keep `raw_ev` as the default** — it is best on points and on rank whenever you
  are even or the pool is long. This validates the default, and confirms the
  alternative strategies are *not* generally better.
- The strategy modes are **situational**: only a variance tilt (`exact_chase`)
  measurably raises the probability of an *outright win* when **behind in a short
  remaining slate**, and only by trading away typical position. `contrarian` and
  `conservative` did not help in this simulation.

## Caveats
- The field is a **SYNTHETIC** estimate of public behaviour, not real pool picks.
- `--field-concentration` is an assumption about how sharply the field clusters on
  popular scorelines; results shift with it.
- The WC workbook backtest is **1X2 only** (no historical WC totals) and uses
  closing/unknown-timing odds, so it is not identical to early pre-match picking.
