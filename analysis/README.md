# Independent model & pool-differentiation analysis (2026-06-16)

## TL;DR
- A free, **independent** (results-only) model **cannot beat the closing market** —
  tested rigorously on 84,198 club matches. So it was repurposed as a
  **pool-differentiation** tool to climb rank, not a market-beater.
- The best differentiation play is **1-1 draws in the tightest games**: near-zero
  expected-points cost, high differentiation (the field under-picks draws), and it
  hedges the draw-clusters that have been costing points.

## 1. Can a free independent model beat the market? No.
Walk-forward Elo built from football-data results only (no odds input), 84,198
matches, held out on 2022/23-2024/25 (23,280 matches). Tool:
`scripts/independent_vs_market.py`.

| probabilities | log-loss | Brier |
|---|---|---|
| naive (base rates) | 1.0740 | 0.6498 |
| **market (closing, de-vigged)** | **0.9933** | **0.5933** |
| Elo model (independent) | 1.0153 | 0.6082 |

The Elo model **beats naive** (real signal) but **loses to the market in all 22
leagues**. Betting its value picks at closing odds returns **-7.7% to -9.7%**, and
ROI gets *worse* as the edge filter tightens — the model's "edges" are its own
errors. **Verdict:** free public data can't beat the closing line, and xG wouldn't
change it (the market already prices strength *and* xG).

## 2. Differentiation overlay
Independent national-team Elo (49,477 international results, neutral-venue aware,
friendlies down-weighted) vs the de-vigged WC odds, flagging where the independent
view diverges from the field. Tool: `scripts/differentiation_overlay.py`
→ `analysis/differentiation_report.csv`.

## 3. EV-cost contrarian selection (the key tool)
Rebuilds the market scoreline distribution from the engine's own
`lambda_home`/`lambda_away` (a self-check reproduces the engine's expected-points
**exactly**), computes the Superbru EV of every scoreline, and ranks fixtures by
the EV cost of going contrarian. Tool: `scripts/ev_contrarian.py`
→ `analysis/ev_contrarian_report.csv`.

- **Cheapest contrarian = the 1-1 draw in tight games** (EV cost 0.003-0.11):
  Cape Verde-Saudi (0.003), DR Congo-Uzbekistan (0.043), Turkey-USA (0.057),
  Egypt-Iran (0.082), Paraguay-Australia (0.095), Ghana-Panama (0.099),
  Algeria-Austria (0.103), Colombia-Portugal (0.106).
- **Model-backed underdog** picks cost more (0.045-0.25). Cheapest non-host:
  Cape Verde -> Saudi (0.045). Cape Verde-Saudi is cheap in *both* lenses — the
  market treats it as a coin-flip.

## How to use it (you're chasing rank from behind)
- Spend contrarian bullets where the **EV cost is smallest** (top of the draw
  list). Play the **consensus** everywhere else to protect position.
- Draws differentiate (most of the field fades them) **and** hedge the
  draw-clusters that have been zeroing favourite-pickers.

## How to run (from the project root, venv active, with data present)
```
python scripts/independent_vs_market.py work/football-data-leagues
python scripts/differentiation_overlay.py <wc_odds_snapshot.json>
python scripts/ev_contrarian.py <predictions.json>
```
These tools run inside the full engine project (they import `superbru_score_engine`
and read `work/`, `outputs/`, `work/international_results.csv`). They are committed
here as the source of record; they won't run from this config-only repo alone.

## Caveats (don't skip)
- **Contrarian = lower expected points, lower field-correlation.** Use it to gain
  rank when behind; play consensus when protecting a lead.
- **Host bug:** WC games are treated as neutral, so hosts (USA/Canada/Mexico) are
  under-rated by the Elo — their "fade" picks are artifacts (flagged in the
  report). The EV-cost *draw* picks are unaffected (computed from the host-correct
  market distribution).
- The independent model is **sub-market on accuracy** (see §1) — these are
  variance plays, not edges.
