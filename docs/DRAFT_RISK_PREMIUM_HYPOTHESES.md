# DRAFT — variance risk premium and perpetual funding carry

**Status: DRAFT, NOT REGISTERED, NOT ADMISSIBLE.** This is deliberately NOT in
`docs/EXPERIMENT_REGISTRY.md`. An earlier revision registered both lanes there
with their A11 failure noted; that was withdrawn, because it is exactly what S8
forbids: *"A draft that fails any rule returns to its drafter. Registering it
with the failure noted is the outcome this section exists to prevent."*

The same question was resolved the other way one day earlier, and that precedent
governs: on 2026-09-03 two maker-scaling estimators whose every identified bias
channel pushed the favourable way were refused registration and kept out of the
registry (`docs/POLYMARKET_QUANT_MODE_CHARTER.md`). Registering these two on
2026-09-04 under the same condition would have been the opposite ruling on
identical facts.

## What the lanes are

**Variance risk premium.** Option-implied variance carries a premium for bearing
variance risk, so it exceeds subsequently realised variance on average. A
defined-risk short-variance position with a delta hedge harvests the spread. It
is compensation for tail risk: the position loses in a volatility shock, which
is why it is paid.

**Perpetual funding carry.** A perpetual future is tethered to spot by a
periodic funding payment. When funding is persistently positive, long spot plus
short perpetual is delta-neutral and collects funding. The risk borne is basis
divergence and short-leg liquidation.

Existing instrument: `src/polymarket_predictive_engine/crypto_fundamental.py`
already extracts the risk-neutral density from the live Deribit chain by
Breeden-Litzenberger (`usd_call_curve`, `survival_probabilities_from_calls`,
`interpolate_survival`). Binance and Coinbase price clients exist. The realised
side, the funding collector and both estimators do not.

## Why this is not registerable yet — the defects an independent gate found

Fix every one of these before proposing registration. Each was verified, not
asserted.

1. **A11, and it is the blocking one.** Ten identified bias channels in the
   variance lane and nine in the carry lane all push toward a larger apparent
   premium, with none identified pushing the other way. A11 makes that
   inadmissible absent an explicit argument that the effect exceeds the
   aggregate bias, and that argument cannot be made before the data exists.
   **Registering the lane and deferring the argument to a gate condition is not
   a way round this.** Register when the argument can be made.

2. **The headline justification was false.** Both lanes were pitched as having a
   quantity "observable before entry", in contrast to the estimators that
   failed. The registered primary metric was *realised* premium per cycle, and
   realised variance is not observable at entry. For carry, funding publishes
   one 8-hour interval ahead while the independent unit is a week of 21
   intervals, and basis change over the period is in the metric. Either restate
   the metric so the claim is true, or drop the claim.

3. **The concentration criterion was unsatisfiable.** "No single underlying
   supplies more than 35% of positive net premium" over a two-underlying
   universe: two non-negative shares of a positive total sum to 1, so the larger
   is always at least 0.5. It fails for every possible split. Express the
   criterion at the cluster the basis actually cites, or widen the universe.

4. **The 120-unit stopping arm was unreachable.** 365 days gives 52 weekly
   periods per underlying, 104 across two, against 120 required. Only the
   calendar arm could ever bind, which interacts badly with the extension being
   a second look.

5. **The bias bound had no floor.** It required "a registered non-zero minimum"
   naming no number, no config key and no basis — A1's own rejected pattern. At
   all-zero bounds the gate reduced to exactly the `LB90 > 0` the same paragraph
   called non-responsive; at a negative bound it was looser still.

6. **A2 was absent.** Four threshold comparisons were introduced and none stated
   its missing, empty, unparseable or non-finite branch. A missing per-channel
   bias bound would naturally sum as zero, which is the favourable direction.

7. **Bias channels still omitted**, beyond those already listed: the capital-at-
   risk denominator is undefined for the variance lane (margin, max loss and
   notional differ by multiples, and narrowing it inflates the ratio);
   protective-strike distance, hedge frequency and the point chosen inside the
   7-30 day window are all researcher degrees of freedom chosen after seeing the
   chain; venue and counterparty risk is named as an omitted cost for the carry
   lane but not the variance lane, which carries the identical exposure; and
   percentile-bootstrap lower-bound coverage at small cluster counts sits above
   nominal, displacing the gate quantity upward.

8. **Consumers of the primary-hypothesis set were never enumerated.**
   `src/polymarket_predictive_engine/discovery_policy.py:10-14` holds a
   three-element `PRIMARY_HYPOTHESES` tuple; WO-95's registered coverage
   contract and WO-33 both require lanes to map to H1-H3; `dashboard.py` renders
   "the registered H1/H2/H3 hypotheses". Any future registration must say, for
   each, whether it changes or why it does not.

## What must exist before registration is proposed again

- A support evaluator that writes its own verdict artifact, so a gate reads
  engine-produced state rather than producer assertions.
- A bias-bound derivation method per channel, with a literal registered floor
  and computation by a component other than the one whose lane it gates.
- Fee schedules captured from Deribit and Binance and registered in config.
- Metric definitions that make whatever "observable before entry" claim survives
  actually true.

## What this document is not

It registers nothing, authorises nothing, and confers no evidence class. No
observation taken before a future registration merge could count toward any
gate defined here.
