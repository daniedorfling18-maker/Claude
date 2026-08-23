# DRAFT — owner amendment registering H4: model-conditional crypto up/down repricing (paper-only evaluation)

> **STATUS: UNSIGNED TEMPLATE. NOT IN EFFECT.**
> This file authorizes nothing. The WO-95 research-surface freeze
> (registered 2026-07-12) remains fully binding: exactly three primary
> hypotheses are permitted and crypto up/down markets carry
> `frozen_crypto_updown_no_collection_priority`. Nothing below runs until
> the owner executes the steps in "How to execute". A chat instruction
> never substitutes for any of these steps. The system remains
> paper/dry-run throughout; this amendment adds NO order path and touches
> NO funding gate.

## Why this draft exists (measured provenance, 2026-08-15)

Source: `edge_attribution_positions.csv` at `origin/vps-telemetry @
0ae83704`; decision-tractability analysis of the same file (109 attributed
positions; details in the session ledger).

1. **Tractability bar.** At the measured shadow decision rate (2.4–5.0/day
   bounded) and per-share variance (sd 0.226), only an edge of roughly
   **0.04/share or larger** is testable inside one quarter. Smaller claimed
   edges require years of decisions and are therefore untestable here.
2. **One cohort clears the bar.** `exploratory_crypto_updown_live_model`
   (n=9 positions, 8 markets, 2026-07-01 to 2026-07-11): line movement
   **+0.2441/share** (95% lower bound **+0.0759**), total P&L +0.2102
   (95% lower bound +0.0335), settlement surprise −0.028, execution cost
   +0.0059. Eight of nine positions positive; median +0.120; trimmed mean
   (dropping best and worst) +0.179; mixed close reasons (settlement,
   stop-loss, take-profit). Realised P&L exceeded the model's own claimed
   edge (+0.0752).
3. **The claim is MODEL-CONDITIONAL, not market-conditional.** Across all
   20 up/down positions regardless of signal cohort, line movement is
   +0.035 mean with a 95% lower bound of **−0.131** and only 10/20
   positive. The market type alone carries nothing measurable. Whatever is
   here lives in the live model's own selections. n=9 is a diagnostic, not
   evidence — which is exactly the situation the registry's evidence policy
   anticipates: *"A promising diagnostic can become a primary only through
   a future pre-observation amendment and a fresh OOS window."*
4. **The operational fact.** The cohort's last position predates the WO-95
   freeze by one day. The freeze cut up/down collection priority on
   2026-07-12; the shadow flow stopped with it. The diagnostic was never
   falsified — it was defunded by a rule that predates the evidence.

## How to execute this amendment (when and if the owner chooses)

1. Fill in the date and name in the amendment text below.
2. Append the section titled "Owner amendment" to
   `docs/EXPERIMENT_REGISTRY.md` as a new `## H4` entry, dated BEFORE the
   evaluation window begins (the pre-observation requirement).
3. Open the PR from the owner GitHub account. An agent that did not author
   this draft audits; the owner merges. The merge commit timestamp is the
   start of the eligible OOS window — no observation before it counts.
4. Only after that merge may the narrow unfreeze in §U below be
   implemented, as its own one-WO PR, owner-merged.

---

## Owner amendment (template text)

**Dated owner amendment — [YYYY-MM-DD], [OWNER NAME].**

I register a fourth primary hypothesis, H4, for paper-only evaluation,
under every term below. Terms are tighten-only.

### H4 — Model-conditional crypto up/down repricing

- **Economic mechanism.** Polymarket's sub-daily crypto up/down markets
  settle against an external price feed. A model reading the underlying
  spot faster than the Polymarket book reprices can enter before the book
  adjusts. The testable signature — observed in the n=9 diagnostic and
  required going forward — is P&L carried by **line movement** (the book
  repricing toward the position after entry) with settlement surprise
  near zero. Mechanism is falsified if P&L arrives as settlement surprise
  rather than line movement, whatever its sign.
- **Independent unit.** One shadow position opened by the live model's own
  signal on one up/down market instance. Positions on the same asset and
  window (e.g. two entries in one 15m BTC candle) count as one unit.
- **Primary metric.** Mean **line_movement_per_share** across units, with
  its 95% confidence interval. P&L is reported but is NOT the decision
  metric: at these sample sizes settlement noise (sd ≈ 0.38/share)
  dominates P&L, and the mechanism claims repricing skill, not settlement
  luck.
- **Sample floor.** **n ≥ 250 units**, all timestamped strictly after the
  amendment's merge commit. The 9 diagnostic positions are history and
  never count. 250 derives from the measured tractability analysis: it
  detects a 0.04/share effect at the measured variance (95%/80%).
- **Cost model.** WO-94 V2 category- and price-aware taker fees on entry
  and exit; measured execution cost baseline 0.0059/share from the
  diagnostic; line movement reported both gross and net of the full cost
  stack. Any gas or settlement-claim cost itemised, never netted silently.
- **Multiple-test correction.** H4 is ONE pre-registered hypothesis: one
  model, one market family, one metric. No per-asset, per-interval, or
  per-hour sub-claims may be promoted; sub-cuts are descriptive and
  reported as the complete family (all assets × all intervals evaluated,
  including negative cells), never cherry-picked.
- **Stopping rule.** Evaluate at n=250 exactly (no peeking-based stops
  except the safety stop below).
  - **Promote to funding review** only if the 95% lower bound of net line
    movement ≥ **+0.04/share** AND the capacity gate (below) passes.
  - **Abandon** (action: `crypto_updown_lane_not_supported`) if the 95%
    lower bound < +0.04/share. No re-run without a fresh amendment.
  - **Safety stop:** if cumulative shadow P&L across the window falls
    below **−$50 notional-equivalent**, halt the evaluation and report;
    this can only stop, never extend.
- **Capacity gate.** Median deployable size per decision at the touch,
  measured from recorded book depth on the evaluated markets, must imply
  ≥ **$25/day** expected value at the measured edge; otherwise the edge is
  real and irrelevant, and the lane is abandoned on capacity grounds —
  reported as such, not as absence of edge.
- **Promotion boundary.** Passing H4 authorizes NOTHING beyond a funding
  review under the existing WO-50 policy and Tier-1/Tier-2 human stages.
  No order path, no sizing, no gate change. Paper/dry-run throughout.

### §U — Narrow unfreeze (implemented only after this amendment merges)

`frozen_crypto_updown_no_collection_priority` is lifted ONLY for:
(a) websocket/feature collection priority for up/down tokens the live
model signals on, and (b) shadow position opening by the existing
`exploratory_crypto_updown_live_model` path. The freeze stays in force for
discovery queries, the resolved calibration corpus (#445/#447 exclusions
stand for that surface), dashboards, and every other consumer. The
unfreeze is one WO, one PR, owner-merged, and reverts automatically to
frozen at n=250 or the safety stop, whichever comes first.

---

*Drafted 2026-08-15 by the orchestrating agent. This draft records
measured findings and a proposed evaluation design. It grants no
authorization, and no agent may treat its existence as approval.*
