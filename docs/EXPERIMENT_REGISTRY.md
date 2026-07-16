# Experiment registry

Created 2026-07-12 in response to the external audit (§4 and Priority 6):
every additional research lane raises the probability that chance alone
produces an apparently positive result. This is the authoritative research
surface. A lane not registered here cannot be promoted, funded, or cited as
edge evidence, regardless of its observed numbers.

## Research-surface freeze (registered 2026-07-12)

Exactly three primary hypotheses are permitted:

1. Sharp-anchor maker carry.
2. Persistent dutch-book consistency opportunities.
3. Structural-bias / smart-flow cohorts with positive CLV.

No fourth primary may be inferred from an existing module or attractive
dashboard result. Adding or replacing a primary requires a dated owner-approved
amendment written before the new evaluation window begins, including an
economic mechanism, independent unit, sample floor, cost model, multiple-test
correction, stopping rule, and promotion/abandonment rule.

For H2 and H3, every observation before the merge commit containing this freeze
is diagnostic history only. It cannot be back-applied to the gates below. The
first eligible out-of-sample observation must have a timestamp strictly after
that merge commit. This prevents retroactive hypothesis registration.

## H1 — PRIMARY: Sharp-anchor maker carry

- Economic mechanism: resting liquidity earns a published reward-pot share
  and potentially captures spread, while an independent sharp probability
  identifies quotes whose apparent carry is least likely to be erased by
  adverse selection. Edge is realised reward plus spread minus markout, fees,
  gas, and all investor costs—not the reward headline alone.
- Universe: rewarded markets passing the registered yield-first scan and all
  existing resolution, price-band, thin-book, and payout-floor exclusions.
  Anchor-qualified claims additionally require a fresh, unambiguous bookmaker
  join with a current executable Polymarket bid and ask.
- Independent unit: one published-v2 portfolio observation per UTC day.
- Primary metric: trusted net carry per day at the registered capital cap.
- Frozen gates: M-A, M-B, and M-C in `maker_carry_study.py`, registered at
  2026-07-09T13:00:00Z. No metric in this registry replaces or enriches what
  those gates read.
- Registration boundary: M-A/M-B/M-C test the already registered reward-carry
  component and remain binding. They are necessary but not sufficient evidence
  for the newly named sharp-anchor-qualified hypothesis. Pre-freeze anchor
  observations are diagnostic and cannot prove that qualification; a future
  evaluator must be registered before it links anchor state to maker markout.
- Decision/stopping rule: the frozen WO-50 policy table and dated decision
  process in `live_test_decision_policy.py`.
- Abandonment action: `maker_lane_not_supported_program_review`.
- Status: active registered study; only forward, distinct-UTC-day evidence is
  eligible.
- Validation protocol (registered 2026-07-14): the study's net carry is a
  simulation UPPER BOUND until validated by a three-tier ladder, each tier
  gating the next. Tier 0 — fill-replay against recorded book/prints
  (last-in-queue), free, runs on history; produces confirmed-fill ratio,
  realized markout, and a reported (never auto-applied, tighten-only)
  simulation-to-reality haircut (WO-83). Tier 1 — reward-receipt test:
  rest minimum size on a CALM wide-band rewarded market across one reward
  epoch, compare paid reward to predicted share (isolates the reward half
  at low adverse-selection exposure). Tier 2 — real fill markout via the
  P2 $100 human stage (the only true test of the adverse-selection half).
  A Tier-0 result showing the fill model is wildly optimistic can retire
  the lane with zero capital.
- Funding prerequisite amendment (owner-approved 2026-07-16; WO-93): the
  WO-50 action table may indicate either `fund_100*` action only for its exact
  named current-portfolio market, and only when both tests below pass on
  artifacts timestamped strictly after the pre-observation registration
  boundary `2026-07-16T14:03:26Z`. Until then the policy's binding capital is
  exactly zero.
  1. **Sharp-qualified H1:** the market outcome token has at least one fresh
     exact-token `joined` anchor (maximum age 6h), all fresh anchors agree
     within 0.03 probability, a fresh executable Polymarket bid/ask exists
     (maximum age 5m), and the consensus sharp fair lies inside the proposed
     maker bid/ask quote band. Historical-market substitution and aggregate
     anchor counts are prohibited.
  2. **Tier-0 sufficiency:** the replay is no older than 30m, postdates and
     identifies the exact current portfolio version, uses the official book
     as its primary source, and its exact market row has at least 30
     last-in-queue evaluable opportunities, at least 10 confirmed fills, at
     least 80% coverage plus 10 observed markouts at each of 5/15/60m, and a
     numeric market-level simulation-to-reality adverse-selection haircut no
     greater than 1.0. Archive or aggregate evidence cannot substitute.
  These values are tighten-only: age, disagreement, and haircut maxima may
  only decrease; evidence and coverage minima may only increase. Missing,
  stale, future-dated, ambiguous, malformed, or insufficient evidence fails
  closed. This amendment changes no order path and does not waive the
  registered Tier-1/Tier-2 human-stage requirements.

## H2 — PRIMARY: Persistent dutch-book consistency opportunities

- Economic mechanism: a complete mutually exclusive/exhaustive basket whose
  executable asks sum below one can lock a payoff, but only if every leg is
  simultaneously buyable at a common size and the deviation persists long
  enough to survive stale-quote and queue effects.
- Universe: complete negRisk/event-group baskets observed by
  `dutch_arb_monitor.py`; incomplete, unpriceable, ambiguous, or oversized
  baskets are excluded before measurement.
- Independent unit: an event/opportunity episode, clustered by event and UTC
  day; an episode must clear for at least one full scan before a later episode
  can count independently.
- Primary metric: common-size executable basket profit divided by capital,
  net of venue fees, registered slippage/adverse cost, and capital holding
  time. Headline pre-cost ask-sum deviation is not the primary metric.
- Sample floor: at least 30 independent event-day episodes over at least 14
  calendar days, including at least 10 episodes persistent for three
  consecutive 15-minute scans.
- Support gate: aggregate net profit positive; event-clustered bootstrap 90%
  lower bound above zero; no single event supplies more than 35% of positive
  profit; and every counted episode has a complete common-size execution plan.
  If partitions are inspected, Benjamini-Hochberg FDR at 10% applies across all
  inspected partitions.
- Stopping rule: evaluate at 100 independent episodes or 60 calendar days,
  whichever comes first. If the support gate is not met, return the lane to
  diagnostic status; no threshold tuning on that window.
- Status: primary hypothesis registered; current scanner artifacts remain
  diagnostic until a dedicated post-registration OOS evaluator implements
  this exact contract.

## H3 — PRIMARY: Structural-bias / smart-flow cohorts with positive CLV

- Economic mechanism: persistent venue bias or demonstrably informed public
  flow may move the later executable line; edge exists only when a
  pre-specified cohort's entry repeatedly beats the later bid/close after
  costs, not because a wallet has a compelling historical P&L headline.
- Universe: point-in-time public fills and structural cohorts with immutable
  cohort assignment at observation time. Wallet/cohort identity, entry price,
  and later line must all come from stored timestamps.
- Independent unit: the first eligible fill per wallet × token × UTC day,
  clustered by market so repeated fills/ticks do not manufacture sample size.
- Primary metric: final executable CLV (later bid minus observed buy price),
  net of the registered cost stack.
- Evaluation protocol: cohort discovery uses only the first 60% of the new
  chronological window; the final 40% is untouched validation. Wallets or
  cohort definitions selected on validation are invalid.
- Sample floor: at least 30 independent final-line fills across at least 10
  markets overall and at least 20 independent final-line fills for any claimed
  cohort.
- Support gate: positive mean net final CLV with market-clustered bootstrap 90%
  lower bound above zero, Benjamini-Hochberg FDR at 10% across every tested
  wallet/structural cohort, and no market family supplying more than 35% of
  positive CLV. Passing creates a shadow research candidate only; normal
  forward paper, risk, and capital gates still bind.
- Stopping rule: evaluate at 100 independent final-line fills or 90 calendar
  days. If no cohort passes untouched validation, suppress following and do
  not redefine cohorts on the same window.
- Status: primary hypothesis registered; existing `smart_flow_clv.py`,
  calibration-bias, and wallet-intelligence outputs are diagnostic until a
  post-registration evaluator implements the clustering, OOS split, costs,
  concentration, and FDR contract above.

### Exact prospective evaluator amendment (owner-approved 2026-07-16; WO-96)

The H3 registration boundary is `2026-07-12T13:38:47Z`. Eligible observations
are post-boundary public BUY fills in the frozen 0.05–0.90 entry band. The first
fill per wallet × token × UTC day is the independent row, ordered by normalized
fill timestamp then immutable trade ID. A final line is the last token-level
executable bid after entry and at/before close, observed within 60 minutes of
close; missing/future/mismatched/stale quote data is ungraded, never imputed.

The stopping sample is the first 100 graded independent fills or all eligible
graded fills entered during the first 90 calendar days, whichever stop occurs
first. Formal evaluation does not begin early. At stop, the sample freezes:
the first floor(60%) is discovery and the final remainder is untouched
validation. Fixed structural cohorts are entry-price bands 0.05–<0.20,
0.20–0.80, >0.80–0.90; point-in-time leaderboard ranks 1–10, 11–50, 51–100;
and positively observed top-holder membership. Intelligence snapshots must be
at/before the fill and at most 36 hours old. Wallet candidates require at least
five discovery fills in at least two markets. No candidate is selected from
validation.

Net CLV per share is final bid minus entry price minus canonical WO-94 taker
fees at both prices, minus 0.005 fixed exit cost and 0.005 adverse-selection
cost. At stop all fixed and discovery-selected cohorts, including null and
negative cells, enter complete-family BH-FDR at 10%. The existing sample,
clustered-bootstrap, concentration, shadow-only pass, and suppression gates
above remain unchanged. `smart_flow_clv.py` remains legacy diagnostic history;
only the WO-96 exact artifact may state the H3 registered verdict.

## Multiple-testing and evidence policy

- Each primary has one primary metric. Secondary cuts are descriptive.
- H2/H3 partition scans use BH-FDR at 10% across the complete tested family,
  including null/negative cells; reporting only the best cell is prohibited.
- Historical, modeled, reconstructed, shadow, paper, and live-real-money
  evidence classes remain separate. None may be relabelled upward.
- A promising diagnostic can become a primary only through a future
  pre-observation amendment and a fresh OOS window.

### Prospective taker-fee correction (owner-approved 2026-07-16; WO-94)

All prospective taker signal scores and paper fills after the WO-94 merge use
the venue's category- and price-aware V2 taker fee. Alpha entry edge is net of
one taker fee; price-action expected round-trip edge is net of entry and exit
taker fees. Exact market fee metadata is authoritative, with documented
category rates as the missing-metadata fallback and a non-zero conservative
fallback for malformed/unknown inputs. This is a cost correction, not a new
hypothesis or a threshold amendment. Historical fills remain immutable and the
registered legacy verdict engine retains its frozen cost constants to avoid a
mid-study rule change; its results must continue to be labelled under that
registered assumption.

## Mandatory legacy adjudication (not a fourth primary)

The registered taker `$100/month` CLV verdict engine must run to its existing
terminal decision/extension dates because changing a stopping rule mid-study
would be data snooping. It is a legacy adjudication obligation, not permission
for new taker-model breadth. Its result can terminate or reject that legacy
program; it cannot create a fourth primary hypothesis under this freeze.

Semantics clarification (decided by the owner 2026-07-14, WO-87): the
quantity this engine has always graded is the last tradeable price at or
before the market close — empirically a near-settled price for 51/61
finals — so its gate metric IS unit mean net settlement return per dollar,
not closing-line edge. The binding metric, thresholds, alpha, and floors
are unchanged (a mid-study swap would be snooping); all labels are renamed
to say what the metric is, and a separately-registered diagnostic — true
pre-event CLV against the last in-band [0.05, 0.95] price at or before
close_time − 6h — is reported alongside. The diagnostic feeds no gate in
this study; every verdict rendering carries the settlement-return caveat.

## Diagnostic / parked surface

- Implication networks, generic calibration bias, drift term structure,
  reconstructed CLV, hourly adverse selection, and unregistered model sweeps
  are diagnostics only.
- Crypto up/down remains frozen after negative evidence; unfreezing requires a
  new owner-approved registry amendment and fresh OOS window.
- Politics, awards, long-dated macro, and every family not named in H1–H3 are
  parked. Passive low-cost collection may continue, but no promotion-oriented
  modelling or agent time is allocated without amendment.
