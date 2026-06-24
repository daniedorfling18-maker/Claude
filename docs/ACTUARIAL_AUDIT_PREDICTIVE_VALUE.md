# Actuarial Audit — Predictive Value of the Model and Process

Audit date: 2026-06-24. Scope: the SuperBru/World Cup score engine, the
Polymarket predictive engine, and the surrounding validation/governance process.
Method: review of the modelling code and of the committed validation artefacts
(out-of-sample league calibration, live World Cup results, betting/Polymarket
forecast-skill reports). All figures below are taken from artefacts already in
the repo; none are asserted from memory.

## 1. Headline opinion

| Question | Determination |
|---|---|
| Does the model generate an independent signal? | **No.** The predictor *is* the market price (de-vigged odds for the score engine; order-book midpoint for the Polymarket engine). |
| Is there a statistically significant edge over the market baseline out of sample? | **No.** OOS edge over the naive/market baseline is indistinguishable from zero. |
| Expected betting ROI at liquid prices? | **≈ −vig (−3% to −5%)** by construction; positive only on genuinely soft prices, which is unproven. |
| Is the Polymarket ML engine validated? | **No — zero resolved labels.** It has never been trained or scored on real data; its own gates refuse below 100 labels. |
| Is the process/governance sound? | **Yes — institutional-grade (A−).** OOS splits, bootstrap CIs, leakage controls, fail-closed live gates, honest caveats. |

Bottom line: **the machinery is excellent; the alpha is unproven and most likely
zero.** The model is a well-calibrated *mirror* of the market. Do not deploy
betting capital expecting model edge. The one avenue with a genuine structural
edge mechanism (the SuperBru pool, funded by other players) shows a positive but
not-yet-credible signal.

## 2. The models are market-anchored, not independent

- Polymarket engine: the trained calibrator's predictor is `implied_probability`,
  which falls back to `midpoint` — i.e. the market price itself
  (`models/calibration_v2.py`, `joined_feature_label_rows`). It bucket-calibrates
  the market price against realised outcomes. By construction this can only
  correct *systematic miscalibration* (e.g. favourite–longshot bias); it cannot
  produce an average edge over the market.
- Score engine: probabilities are de-vigged bookmaker odds (Dixon–Coles shaping).
  In the live World Cup file every `model_pick` equals the market favourite and
  every `fav_prob` equals the de-vigged market probability
  (`wc_predictive_power_per_match.csv`).
- The richer WebSocket microstructure features (depths, imbalance, last trade)
  are now collected and join into `features_v2.csv`, but the calibrator still
  consumes only the midpoint. **No microstructure signal is used by the model yet.**

## 3. Out-of-sample edge is statistically zero

Source: `outputs/calibration/{extended-train,extended-validation}/football_data_league_summary.json`
(football-data.co.uk closing-odds backtest, 7 divisions).

| Split | Matches | Mode | Edge vs naive (pts/match) | p-value | Significant |
|---|---|---|---|---|---|
| Train (21/22–23/24) | 7,246 | h2h | **+0.042** | 0.000 | Yes |
| Train | 7,246 | h2h_totals | +0.010 | 0.14 | No |
| **Validation (24/25, OOS)** | 2,370 | h2h | **+0.013** | **0.39** | **No** |
| Validation (OOS) | 2,370 | h2h_totals | +0.002 | 0.90 | No |

The in-sample edge (+0.042 pts/match, p=0) **collapses out of sample** to +0.013
(p=0.39). That is the classic signature of overfitting: the apparent skill does
not generalise. Calibration quality is market-level in both splits (Brier ≈ 0.571,
log-loss ≈ 0.96, 1X2 accuracy ≈ 54.7%). Net: no demonstrable forecasting edge
over the de-vigged market.

## 4. Live World Cup result (n=40) is positive but not credible

Source: `wc_predictive_power_summary.json`, `superbru_realised_submitted_summary.json`.

- SuperBru pool, realised: **0.875 pts/match** (35 pts over 40), 52.5% outcome hit,
  12.5% exact.
- Edge vs best naive template (2-0 = 0.7625): **+0.1125 pts/match**, 95% CI
  **[−0.21, +0.44]** — straddles zero.
- Round 1 = 0.69, Round 2 = 1.16: a swing consistent with small-sample noise.

Credibility (limited-fluctuation): to confirm a +0.11 pts/match edge at p<0.05
with points SD ≈ 0.95 requires roughly n ≈ (1.96·0.95/0.11)² ≈ **~290 matches**.
At 40, the experience carries little credibility; the result is encouraging but
not bankable.

## 5. Betting / Polymarket ROI is ~0 to −vig

Source: `betting_market_realised_roi` in `wc_predictive_power_summary.json`
(price-taker, assumed 5% vig).

| Market set | n | Vigged ROI | 95% CI | Brier skill vs uniform |
|---|---|---|---|---|
| polymarket_prekickoff | 10 | +25.1% | [−19.6%, +66.3%] | 0.40 |
| all_markets_combined | 17 | +16.7% | [−18.7%, +49.1%] | 0.38 |
| market_history_devig | 7 | +4.7% | [−48%, +52%] | 0.33 |

Every CI straddles zero. The "Brier skill 0.38–0.40" measures the **market's**
forecast skill versus a coin-flip, not the model's edge versus the market. The
repo's own caveat states it plainly: *betting at the price the probability came
from is 0-EV by construction; a realistic vigged line is ~ −5%.* Positive ROI is
only attainable on genuinely soft prices (obtainable price > sharp fair), which
the 17-match sample cannot establish.

## 6. The Polymarket ML engine is unproven

- No `labels.csv`, `historical_resolutions.csv`, `market_resolutions.csv` or
  `historical_price_snapshots.csv` exist — **zero resolved markets**.
- `train_calibration_model` refuses below `minimum_training_rows` (100);
  `validate_model` hard-codes `approved_for_live_trading = False` and warns below
  100 validation rows. So the engine cannot self-approve and has produced no
  validated prediction at all.
- Interpretation: it is a *framework* for measuring edge, not evidence of edge.

## 7. The process is the strong asset (grade: A−)

- Honest, sophisticated validation: temporal OOS split, 10,000-sample bootstrap
  CIs, p-values, RPS/CRPS/Brier/log-loss, naive-template and uniform baselines,
  explicit "0-EV by construction" caveats.
- Leakage control in depth: layered forbidden-field guards in the WebSocket
  normaliser, `features_v2`, and the model feature selector; point-in-time labels;
  BOM-safe config loading. Verified clean across the 241-test suite.
- Fail-closed governance: kill switch, dual opt-in for live trading, human
  approval file, paper-only defaults.

The discipline here is materially better than the edge it has so far found —
which is the correct order to build in.

## 8. Recommendations

1. **Do not commit betting capital on the expectation of model alpha.** Best
   estimate of edge over a liquid line is ≈ 0 to −vig. Keep live trading gated
   off (as it is).
2. **Treat the SuperBru pool as the only live edge candidate.** It pays from other
   players, so beating naive templates is real edge with no market edge required.
   The current +0.11 pts/match is unconfirmed — re-assess at n ≥ ~300 before sizing up.
3. **To create *provable* edge, change three things:**
   - Feed the WebSocket microstructure features into the calibrator (move beyond
     univariate midpoint recalibration) and prove OOS Brier/log-loss skill **vs the
     market**, not vs uniform.
   - Measure ROI at the **obtainable** price against a **sharp anchor** (soft-price
     capture), never at the line's own price.
   - Accumulate ≥100–300 resolved labels and require a positive, significant OOS
     skill before promoting paper → live.
4. **Reserve/risk:** until a significant OOS edge exists, any staking is a
   negative-to-zero-expectation variance game; size for survival (flat, small)
   and judge purely on realised pool finish, not on backtested betting ROI.

## 9. Evidence index

- `outputs/calibration/extended-train|extended-validation/football_data_league_summary.json`
- `outputs/backtesting/wc_predictive_power/wc_predictive_power_summary.json` and `…_per_match.csv`
- `outputs/backtesting/superbru_realised_submitted_summary.json`
- `outputs/backtesting/live_signal_backtest_summary.json` (oddspedia gate: not promoted)
- `src/polymarket_predictive_engine/models/calibration_v2.py`, `validation.py`

## 10. Empirical OOS test on real resolved markets (2026-06-24)

The recommendations in §8 were executed: a corpus of resolved Polymarket markets was
pulled (`scripts/pull_polymarket_resolved_history.py`), a multivariate model was trained
on point-in-time features anchored on the market price, and it was scored **out of sample
against the market itself** with a temporal split by market (`models/skill_model.py`).

Corpus: **157 resolved markets** with CLOB price history (**30 World Cup**, the rest the
liquid "other" set), **314 clean token labels**, 49,466 midpoint snapshots, thinned to
3,764 point-in-time rows. Split by market: train 110 markets / 2,636 rows, test 47 markets
/ 1,128 rows. Features were price-derived (momentum 5m–24h, rolling mean/volatility,
time-to-close, logit-midpoint, text); order-book microstructure is not backfillable and so
is absent here (see caveat).

Result — the model does **not** beat the market out of sample; it is marginally worse:

| Metric (OOS, n=1,128) | Market | Model | Skill vs market |
|---|---|---|---|
| Brier | 0.1876 | 0.1946 | **−3.7%** |
| Log loss | 0.650 | 0.721 | −10.9% |
| Mean Brier gain vs market | — | — | **−0.0070, 95% CI [−0.019, +0.004]** |
| Uncertain region (n=390) Brier | 0.237 | 0.248 | −4.6% |

`beats_market_significantly = false` — the CI for the model's gain over the market straddles
(and centres slightly below) zero. **Obtainable-price ROI** (trade model-vs-price
disagreements, settle at resolution, 1% fee) is not significant at any threshold: ROI
−15% / −1.7% / +9.5% at edge cut 0.03 / 0.05 / 0.08, every CI crossing zero.

Promotion gate: **314 labels ≥ the 300 live target (label gate passes), but skill is not
significant, so the gate stays closed** for both paper and live — exactly the intended
fail-closed behaviour.

**Conclusion.** On real resolved Polymarket markets, point-in-time price-trajectory features
add no out-of-sample forecasting or trading edge over the market midpoint — the midpoint is
an efficient forecast and the model is, if anything, slightly worse. This **empirically
confirms** the "market mirror" thesis of §2 with a proper OOS-vs-market test, not vs uniform.

**Caveat (open question).** This tests only the features that can be *backfilled* from REST
price history. Order-book microstructure (depth, imbalance, top-of-book size) is WebSocket-
live-only and could not be reconstructed historically; the calibrator already consumes those
columns when present, so the microstructure hypothesis can be tested forward once live
capture accumulates — but nothing obtainable from history shows an edge today.
