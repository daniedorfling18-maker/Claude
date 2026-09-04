# Experiment registry

Created 2026-07-12 in response to the external audit (§4 and Priority 6):
every additional research lane raises the probability that chance alone
produces an apparently positive result. This is the authoritative research
surface. A lane not registered here cannot be promoted, funded, or cited as
edge evidence, regardless of its observed numbers.

## Research-surface freeze (registered 2026-07-12)

Exactly three primary hypotheses were permitted at registration; the
2026-09-04 amendment below raises that to five.

1. Sharp-anchor maker carry.
2. Persistent dutch-book consistency opportunities.
3. Structural-bias / smart-flow cohorts with positive CLV.
4. (H5) Variance risk premium on crypto options.
5. (H6) Perpetual funding carry.

No fourth primary may be inferred from an existing module or attractive
dashboard result. Adding or replacing a primary requires a dated owner-approved
amendment written before the new evaluation window begins, including an
economic mechanism, independent unit, sample floor, cost model, multiple-test
correction, stopping rule, and promotion/abandonment rule.

For H2 and H3, every observation before the merge commit containing this freeze
is diagnostic history only. It cannot be back-applied to the gates below. The
first eligible out-of-sample observation must have a timestamp strictly after
that merge commit. This prevents retroactive hypothesis registration.

### Amendment 2026-09-04 — two risk-premium primaries added (H5, H6)

This amendment is written BEFORE any H5 or H6 evaluation window begins and
takes effect on the merge commit that lands it. It does not assert that it has
been approved; the merge is the approval, and no agent may write, cite, or
imply otherwise.

**Anti-retroactivity, on the same terms already applied to H2 and H3.** Every
H5 and H6 observation before this amendment's merge commit is diagnostic
history only and cannot be back-applied to the gates below. The first eligible
out-of-sample observation must carry a timestamp strictly after that merge
commit.

**Why two premia rather than one hypothesis.** Both are compensation for
bearing risk rather than payment for being right, and in both the quantity is
observable before entry — a live option chain and a funding rate published
8-hourly in advance. That is the deliberate contrast with H1-H3, each of which
required an edge to be estimated, and each of whose estimators drifted toward
the favourable answer.

**Multiple-testing.** H5 and H6 are two members of one family and are corrected
together under the Benjamini-Hochberg FDR at 10% policy already registered
under "Multiple-testing and evidence policy", across the complete tested family
including null and negative cells. Reporting only the surviving premium is
prohibited.

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
- Funding reconciliation (owner-signed 2026-07-18, Route A;
  `docs/OWNER_AMENDMENT_SHARP_LINKING_EVALUATOR.md`): this sharp-anchor
  requirement is CONFIRMED as binding for funding. The sharp-linking
  evaluator anticipated above is registered (WO-105) and is a funding
  precondition. Generic (non-sharp-anchored) reward carry is NOT fundable
  under H1; testing it would require a separate, freshly pre-registered
  hypothesis (Route B, not adopted). The WO-93-revert record's contrary
  "generic reward carry may be funded" statement is SUPERSEDED by this
  owner decision. The evaluator is now BUILT and landed
  (`src/polymarket_predictive_engine/sharp_linking_evaluator.py`, WO-105),
  publishing `sharp_linking_qualification.json` and feeding the WO-99 gate as
  the `sharp_linking_qualified` precondition. The reconciliation flag is
  therefore `FUNDING_GOVERNANCE_RECONCILED = True` (flipped in the same commit
  that landed the passing evaluator, per the amendment). This does NOT open
  funding: the evaluator is itself fail-closed and holds the ticket
  not_eligible until §1 (exact-token sharp anchor) and §2 (exact-market Tier-0
  sufficiency) both pass on real data — which cannot happen until Tier-0
  markout coverage matures on the VPS. Tighten-only: this adds a funding
  precondition and loosens nothing.
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
- Status: primary hypothesis registered; WO-98 implements the dedicated exact
  post-registration evaluator. Only `outputs/h2_dutch/h2_evaluation.json` may
  state the registered H2 verdict; legacy gross scanner artifacts remain
  diagnostic.

### Exact prospective evaluator amendment (owner-approved 2026-07-16; WO-98)

The H2 registration boundary is `2026-07-12T13:38:47Z`; the fixed calendar
stop is `2026-09-10T13:38:47Z`. Existing gross scanner rows are diagnostic and
ungradable because they do not preserve exact per-leg fees, the registered
cost reserve, complete clear scans, or one shared observation clock. The exact
ledger therefore begins with WO-98, while retaining the original registration
boundary and rejecting every pre-boundary or future-dated row.

Each live scan uses one UTC timestamp and records every completely priced
3--80-leg negRisk event, including non-opportunities needed to prove a clear.
Every leg must have a unique token, a positive displayed best-ask size, and a
canonical WO-94 fee schedule. The common executable size is the minimum
displayed top-ask size. Per completed basket, capital is the ask sum plus all
entry taker fees (with the venue's five-decimal per-order rounding) plus a
fixed `0.002` USDC slippage/adverse-selection reserve;
net profit is one USDC minus that all-in capital. The primary metric is net
return on capital annualised by the positive event-resolution holding time.
Missing, incomplete, ambiguous, stale, non-finite, already-ended, or otherwise
unpriceable scans are excluded and cannot prove a clear.

An episode starts at the first qualifying complete scan whose all-in net profit
is positive. Its first qualifying execution plan and economics are frozen. A
later episode for the same event requires at least one intervening complete
non-qualifying scan; an absent or malformed scan never proves a clear. At most
one episode per event x UTC day is independent. Persistence requires three
qualifying scans in the same uncleared episode with both adjacent gaps between
10 and 20 minutes; a missing/late scan breaks the persistence run but does not
create a new episode.

The formal sample is the first 100 independent episodes in chronological order
or all eligible episode starts no later than the fixed 60-day stop, whichever
occurs first. Evaluation stays sealed before either stop. Support requires at
least 30 episodes, a first-to-last episode-day span of at least 14 calendar
days, at least 10 three-scan-persistent episodes, complete common-size plans
for every episode, positive aggregate net profit, an event-clustered bootstrap
90% lower bound above zero for mean annualised net return on capital, and no
event contributing more than 35% of positive net profit. The bootstrap uses
1,000 deterministic iterations with seed `20260716`. No partitions are
registered for H2, so FDR is explicitly not applicable; adding a partition
would require a new prospective registration and complete-family BH-FDR at
10%. A pass creates a shadow research candidate only. Failure suppresses H2;
neither outcome can invoke paper/live trading, alter a gate, or authorize
capital.

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
are post-boundary public BUY fills with immutable trade ID and positive size in
the frozen 0.05–0.90 entry band. The first
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

### Resolved-corpus diagnostic amendment (WO-101)

WO-101 resolves the historical trainer's point-in-time leakage defect without
changing the prospective H3 test above. Resolution states are preserved in a
versioned append-only observation ledger. A terminal label is available only
at the latest of market close, reported resolution time, and the first time a
registered producer actually observed that resolution. A later API response
cannot backdate label availability.

Model features come only from timestamped two-sided CLOB quotes captured by
the public websocket/order-book producer; the venue's single-price historical
`{t,p}` response cannot substitute for a bid or ask. Features and terminal
labels are written to separate artifacts.

The diagnostic split is whole-market and chronological. Validation contains
at least 10 distinct canonical markets and the latest 30% of markets, whichever
is larger, while retaining at least one earlier market. A training market is
retained only if its terminal label became available strictly before the
earliest validation feature minus a 24-hour embargo. Markets with overlapping
label intervals are purged. Input rows are limited to exact quotes no more
than seven days before close and are thinned to one deterministic quote per
token-hour. Configuration is tighten-only.

This model is H3 structural-bias feature-discovery substrate, not a fourth
primary and not registered H3 evidence. Its execution diagnostic may buy only
at the recorded ask and must apply the canonical category/price-aware taker
fee. No result from this retrospective terminal-resolution corpus can replace
the WO-96 prospective fill-to-final-bid CLV verdict, alter a promotion gate,
or authorize paper/live capital. Funding remains closed and WO-67 blocked.

## H5 — PRIMARY: Variance risk premium on crypto options

- Economic mechanism: option-implied variance is a risk-neutral expectation
  carrying a premium for bearing variance risk, so it exceeds subsequently
  realised variance on average. A defined-risk short-variance position with a
  delta hedge harvests that spread. The premium is compensation for tail risk,
  not a forecast — it is paid precisely because the position loses in a
  volatility shock, and any version of this hypothesis that treats it as free
  money has misread the mechanism.
- Universe: BTC and ETH options on Deribit at 7-30 calendar days to expiry,
  restricted to strikes carrying a two-sided quote. An expiry with no two-sided
  quote at the protective strike is excluded before measurement, not after.
- Independent unit: one non-overlapping option cycle per underlying, the next
  cycle beginning at the previous expiry. **Daily observations are NOT
  independent**: realised variance is strongly autocorrelated, so overlapping
  windows inflate the effective sample. The cluster bootstrap resamples cycles.
- Primary metric: realised premium per cycle per unit of capital at risk, net
  of option spread crossed at entry and exit, delta-hedge transaction costs,
  financing on posted collateral, and the cost of the protective long leg.
  Implied-minus-realised variance before costs is NOT the primary metric.
- Sample floor: at least 60 independent cycles across both underlyings over at
  least 180 calendar days, and additionally `n >= 7.849 * sigma^2 / delta^2`.
  *Basis for 60:* the cluster bootstrap resamples cycles, and a percentile
  interval cannot place its 10th-percentile lower bound below the smallest
  order statistic, so `n` must satisfy `1/n <= 0.10` — that is, `n >= 10` — just
  to produce a non-degenerate bound. 60 is six times that minimum, so the bound
  rests on 6 order statistics rather than 1.
  `min_daily_observations: 20` (`polymarket_predictive_config.example.yaml:304`)
  is deliberately NOT the basis: its own registered block states "No gate,
  sizing rule, policy, broker, or order path reads these statistics", and this
  floor is read by a gate. *Basis for 180 days:* a shorter window can
  sit entirely inside one volatility regime, and this premium is earned in calm
  periods and repaid in shocks, so a calm-only window measures the wrong thing.
  *Basis for 7.849:* `(1.96 + 0.8416)^2`, the 5% two-sided and 80% power
  standard-normal quantiles. `sigma` is the measured per-cycle standard
  deviation. `delta` is the all-in round-trip cost **computed from the venue fee
  schedule registered in config under `h5_variance_risk_premium.cost_model`
  before the window opens**; until that key exists, no gate may read this
  hypothesis. An earlier revision called `delta` "the registered all-in
  round-trip cost" while no such registered value existed anywhere in config or
  code, which is A1's own derivation repeated.
- Cost model: the venue fee schedule must be captured from Deribit's own fee
  documentation or endpoint and registered in config before any gate reads.
  Until it is, a conservative fallback applies and **the gate cannot pass on the
  fallback alone**, mirroring the registered `CONSERVATIVE_UNKNOWN_RATE = 0.07`
  pattern at `src/polymarket_common/fees.py:25`. Costs are charged per cycle at
  the executable side of the book, never at the midpoint.
- Support gate: aggregate net premium positive; cycle-clustered bootstrap 90%
  lower bound above zero **after subtracting the summed per-channel bias bound
  published under the A11 disclosure below**; and no single underlying supplies
  more than 35% of positive net premium. *Basis:* mirrors the registered H2 support gate in this
  file — "event-clustered bootstrap 90% lower bound above zero" and "no single
  event supplies more than 35% of positive net profit" — with the cluster
  changed from event to option cycle. Cited by content rather than line number
  because intra-file line citations drift with every amendment.
- A11 bias-direction disclosure: **seven identified channels push the estimate
  the same way, toward a larger apparent premium, and none identified pushes the
  other way.** (i) Excluding illiquid expiries removes exactly the stressed
  periods where realised exceeds implied. (ii) A short measurement window is
  likely to be calm. (iii) Omitting delta-hedge slippage understates cost.
  (iv) Measuring only cycles the position survived is survivorship. (v) The
  sample floor is itself computed from `sigma` measured on the window under
  test, so a calm window lowers the required `n` and permits the terminal read
  sooner — the floor loosens precisely when the upward bias is largest.
  (vi) BTC and ETH are the two assets whose variance risk premium is the most
  documented in the class, so selecting them conditions the universe on the
  answer. (vii) The 90-day extension is a second look taken when the interim
  result sits near the gate.

  An earlier revision claimed one channel pushed the other way — that discrete
  hedging error inflates measured realised variance. **That was wrong and is
  withdrawn:** hedging error is a profit-and-loss effect on the position, while
  realised variance is measured from the return series independently of how the
  position is hedged.

  A11 requires an explicit argument that the effect exceeds the aggregate bias,
  and **that argument cannot be made from data that does not yet exist.** So the
  support gate below carries a bias bound rather than a claim: the estimator
  must publish a signed upper bound for each channel above, and the gate passes
  only if the 90% lower bound **net of the summed bias bound** still exceeds
  zero. A 90% lower bound alone is not responsive — it corrects for sampling
  variability, not for bias in the estimand, and is displaced upward by exactly
  the same amount as the point estimate.
- Multiple-test correction: H5 and H6 form one family of two and are corrected
  together under Benjamini-Hochberg FDR at 10% across the complete tested
  family, including null and negative cells. Reporting only the surviving
  premium is prohibited. Any partition inspected within this hypothesis joins
  the same family and is corrected with it, so inspecting more partitions
  raises the bar rather than lowering it.
- Stopping rule: evaluate at 120 independent cycles or 365 calendar days,
  whichever comes first, with one registered extension of at most 90 days
  available exactly once. If the support gate is not met at the terminal read,
  the lane returns to diagnostic status. No threshold tuning on that window.
- Promotion / abandonment: gate met -> promote to backtest (M4) and capacity
  measurement, not to capital. Gate not met at the terminal read -> abandon the
  lane and record the result; it may not be revived without a fresh
  pre-observation amendment and a new out-of-sample window.
- Status: registered by the merge of this amendment. No collection before that
  merge counts.

## H6 — PRIMARY: Perpetual funding carry

- Economic mechanism: a perpetual future is tethered to spot by a periodic
  funding payment between longs and shorts. When funding is persistently
  positive, a long-spot / short-perpetual position is delta-neutral and collects
  funding as carry. The rate is **published in advance**, so the carry is known
  at entry rather than estimated after; the risk borne is basis divergence and
  short-leg liquidation, and the premium is compensation for exactly that.
- Universe: BTC and ETH perpetuals on Binance, with the matching spot leg. A
  period in which either leg lacks a two-sided quote is excluded before
  measurement.
- Independent unit: one non-overlapping weekly carry period per underlying.
  **8-hourly funding observations are NOT independent** — funding is strongly
  autocorrelated — so the cluster bootstrap resamples weeks, not intervals.
- Primary metric: realised carry per week per unit of capital at risk, net of
  both legs' fees, borrow or margin financing, basis change over the period,
  and the collateral held idle against liquidation. Headline funding rate is NOT
  the primary metric.
- Sample floor: at least 60 independent weekly periods across both underlyings
  over at least 180 calendar days, and additionally
  `n >= 7.849 * sigma^2 / delta^2` on the same terms as H5. *Bases:* as H5.
- Cost model: Binance spot and futures fee schedules captured from the venue and
  registered in config before any gate reads, with the same conservative
  fallback rule and the same prohibition on passing a gate using the fallback
  alone.
- Support gate: aggregate net carry positive; week-clustered bootstrap 90% lower
  bound above zero **after subtracting the summed per-channel bias bound
  published under the A11 disclosure below**; no single underlying supplies more
  than 35% of positive net carry. *Basis:* mirrors the registered H2 support gate in this file, cited by
  content under H5 above, with the cluster changed from event to weekly period.
- A11 bias-direction disclosure: **six identified channels push toward a larger
  apparent carry and none identified pushes the other way.** (i) Measuring only
  periods the short leg survived is survivorship, and liquidation is precisely
  the tail being paid for. (ii) Ignoring idle collateral overstates return on
  capital. (iii) Assuming basis convergence at period end books an unrealised
  gain as carry. (iv) Entry conditioned on observed persistently positive
  funding selects periods on the signal and then measures realised carry over
  them. (v) The sample floor is computed from `sigma` on the window under test,
  so a calm window lowers the required `n`. (vi) The cost model covers fees and
  financing only, omitting venue and withdrawal risk, which is a genuine cost of
  holding the carry.

  An earlier revision substituted "may not rest on the point estimate" for what
  A11 actually requires. **That substitution is withdrawn** — it paraphrased the
  rule into a weaker form this design happened to satisfy, which is the failure
  A11 exists to prevent. As with H5, the argument that the effect exceeds the
  aggregate bias cannot be made before the data exists, so the support gate
  carries the same bias bound: the 90% lower bound must exceed zero **after
  subtracting the summed per-channel bias bound**.
- Multiple-test correction: H5 and H6 form one family of two and are corrected
  together under Benjamini-Hochberg FDR at 10% across the complete tested
  family, including null and negative cells. Reporting only the surviving
  premium is prohibited. Any partition inspected within this hypothesis joins
  the same family and is corrected with it, so inspecting more partitions
  raises the bar rather than lowering it.
- Stopping rule: evaluate at 120 independent weekly periods or 365 calendar
  days, whichever comes first, with one registered extension of at most 90 days
  available exactly once. If the support gate is not met at the terminal read,
  the lane returns to diagnostic status. No threshold tuning on that window.
- Promotion / abandonment: as H5 — gate met promotes to backtest and capacity
  measurement, not to capital; gate not met at the terminal read abandons the
  lane, revivable only by a fresh pre-observation amendment and a new window.
- Status: registered by the merge of this amendment. No collection before that
  merge counts.

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
