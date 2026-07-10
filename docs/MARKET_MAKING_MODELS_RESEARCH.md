# Market-making models: research survey and application map

Researched 2026-07-10 for the WO-36 maker lane. Two bodies of knowledge share
the name "market maker model": the institutional/academic theory of liquidity
provision (Part I) and the ICT/Smart-Money retail charting framework (Part II).
Part III maps both onto our Polymarket system and states what changes.

---

## Part I — Institutional and academic market making

### 1. What a market maker earns and pays

A maker quotes bid and ask around fair value and earns the spread on turnover.
The classical decomposition (Huang & Stoll 1997) splits the quoted spread into:

1. **Order-processing cost** - infrastructure, capital, exchange fees;
2. **Inventory cost** - compensation for holding unwanted directional risk
   between fills;
3. **Adverse selection** - the component lost to counterparties who know more.

Maker P&L per fill is measured as **realized spread minus markout**: you earn
(fill price vs mid) and lose (mid drift after the fill). Markout at a fixed
horizon (+1/+5/+30 min) against informed flow is the industry-standard measure
of adverse selection — exactly what our study's markout charge implements.

### 2. Inventory models (Stoll 1978; Amihud-Mendelson 1980; Ho-Stoll 1981)

The dealer is risk-averse; holding inventory q exposes them to price variance.
Consequence: the dealer's **reservation price** shifts against their inventory
(long inventory -> shade both quotes down to attract buyers), and quotes are
asymmetric around mid whenever q != 0. Ho-Stoll formalized the dealer's
dynamic programming problem over a horizon T.

### 3. Information models (Glosten-Milgrom 1985; Kyle 1985; PIN/VPIN)

- **Glosten-Milgrom**: even a risk-neutral, competitive dealer must quote a
  positive spread because some arrivals are informed. Each fill is Bayesian
  evidence; quotes are regret-free conditional expectations ("the ask is the
  expected value given someone lifted it"). Spread widens with the informed
  fraction of flow.
- **Kyle**: an informed trader with private information trades gradually to
  hide inside noise flow; price impact is linear with depth parameter lambda
  (Kyle's lambda). Liquidity is the inverse of lambda.
- **Easley-O'Hara PIN -> VPIN** (Easley, Lopez de Prado, O'Hara): estimate the
  probability/intensity of informed (toxic) flow from signed volume imbalance
  in volume-time; makers should widen or pull when toxicity spikes (VPIN rose
  ahead of the 2010 Flash Crash in their study).

### 4. The canonical algorithmic framework: Avellaneda-Stoikov (2008)

Stochastic-control formulation that modern quoting engines descend from.
With mid price s, inventory q, risk aversion gamma, volatility sigma, horizon
T-t, and an exponential fill-intensity model lambda(delta) = A*exp(-k*delta)
(arrival rate falls as you quote further from mid):

- **Reservation price**: r = s - q * gamma * sigma^2 * (T - t)
  (inventory shades your private "fair" price; long inventory -> quote lower).
- **Optimal total spread**: gamma * sigma^2 * (T - t) + (2/gamma) * ln(1 + gamma/k)
  (volatility/time term + fill-probability term).
- Quotes are placed symmetrically around r, NOT around mid — this is the
  inventory-skew mechanism ([Optiver describes running exactly this shape at
  scale](https://medium.com/@navnoorbawa/optivers-3-5b-market-making-engine-avellaneda-stoikov-inventory-optimization-at-scale-a28fede5a85a)).

Key extensions: Gueant-Lehalle-Fernandez-Tapia (closed forms under inventory
limits), Cartea-Jaimungal (alpha signals, model ambiguity, jumps),
Guilbaud-Pham (mixing limit and market orders), and a live RL literature
([PLOS One 2022 RL-tuned A-S](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0277042);
[2025 RL market-making survey work](https://arxiv.org/pdf/2507.18680);
[FlowHFT imitation-learning policies 2025](https://arxiv.org/pdf/2505.05784)).
Practitioner references: [Hummingbot's A-S guide](https://hummingbot.org/blog/guide-to-the-avellaneda--stoikov-strategy/),
[worked implementation notes](https://www.quantbeckman.com/p/can-you-manage-inventoryor-is-it).

### 5. Queueing and order-book microstructure

At fixed price grids the maker's edge depends on **queue position** (earlier in
queue = better fills, less pick-off); LOB dynamics are modeled as queueing
systems (Cont-Stoikov-Talreja) and self-exciting **Hawkes processes** (order
flow clusters — the empirical basis for our observation that jumps cluster and
for regime-switched pick-off pricing). Our fill-replay WO-40 uses last-in-queue
priority as the conservative bound.

### 6. Institutional practice (Citadel Securities, Virtu, Optiver, Jane Street)

- Revenue = spread capture + exchange/maker rebates + internalization (PFOF in
  equities) - adverse selection - hedging costs.
- Inventory is hedged cross-instrument within seconds-to-minutes; hard
  inventory limits with kill switches (our `cancelAll()` rule).
- The economics are extremely high-Sharpe at scale: Virtu's IPO filing
  famously disclosed 1 losing day in 1,238 trading days — the P&L is a
  high-frequency annuity from the spread, not directional bets.
- Quotes are pulled around scheduled announcements (our event-risk rule is the
  standard practice, not an invention).

### 7. Prediction-market specifics (our venue)

- **Bounded binary payoffs**: price lives in [0,1] and settles at exactly 0 or
  1. There is no underlying to hedge with — unhedged inventory at resolution
  is a pure directional bet. "T - t" in A-S is REAL here (markets expire), so
  the volatility term gamma*sigma^2*(T-t) shrinks toward expiry but JUMP risk
  (news, goals, announcements) dominates diffusion risk. A
  [2025 arXiv paper adapts Avellaneda-Stoikov to prediction markets](https://arxiv.org/pdf/2510.15205)
  ("Toward Black-Scholes for Prediction Markets"), with reservation quotes in
  terms of inventory, risk aversion, time-to-expiry, and belief variance.
- **AMM lineage**: older prediction venues used Hanson's LMSR (logarithmic
  market scoring rule) — an automated maker with bounded loss. Polymarket is
  a CLOB, so human/algo makers replace the AMM, but LMSR intuition (liquidity
  parameter = max subsidy) survives in how reward pots subsidize quoting.
- **Venue economics change the objective function.** On Polymarket a maker
  earns: (a) daily liquidity rewards (quadratic score by distance-from-mid,
  per-minute sampling, min(Q_bid,Q_ask) two-sidedness, in-game multiplier);
  (b) maker rebates = 15-25% of taker fees on YOUR fills, paid daily; (c) 4%
  APY holding rewards on position value; (d) spread capture; MINUS adverse
  selection and terminal inventory risk. Note (b) means getting filled is
  partially compensated — pure reward-farming without fills misses income,
  and our current study (rewards - adverse only) is conservative on both (b)
  and (c).
- **Empirical microstructure of Polymarket exists**:
  [an arXiv study of fill-side non-retail behavioral tiers on Polymarket](https://arxiv.org/pdf/2605.11640)
  documents distinct informed-flow signatures — supporting wallet-tiered
  markouts (WO-37 + flow toxicity) as the right adverse-selection refinement.
- **Mechanics that matter**: no native stop orders on the CLOB; sports books
  auto-cancel at game start; tick regime shifts at 0.96/0.04; single-sided
  reward scoring only inside mid [0.10, 0.90]; matching restarts enter 2-min
  post-only.

---

## Part II — The ICT "Market Maker Model" (MMXM)

### What it claims

Retail framework popularized by Michael Huddleston ("Inner Circle Trader").
Price is narrated as an algorithm ("IPDA") moving between liquidity pools in a
V / inverted-V curve with four phases: (1) original consolidation, (2)
"engineering" liquidity (a trend that builds stop clusters behind swing
highs/lows), (3) "smart-money reversal" at a higher-timeframe premium/discount
array, (4) a liquidity hunt sweeping the engineered stops back to the origin.
Buy-side variant (MMBM) declines then reverses up; sell-side (MMSM) mirrors.
([Framework description](https://michaeljhuddleston.org/notes/ict-market-maker-model-mmxm-trade-with-smart-money-not-against-it/);
[SMC overview](https://tradingwyckoff.com/en/smart-money-concepts/).)

### Scientific status — honest evaluation

- **No peer-reviewed validation.** There is no published, independently
  verified performance record of ICT/MMXM as a methodology, and critics note
  the framework's elements (flexible PD arrays, post-hoc phase labeling) make
  it largely unfalsifiable — after any reversal, an MMXM can be drawn
  ([critical analysis of SMC backtest limitations](https://medium.com/@SentientTradingSociety/dumb-money-concepts-and-stat-test-limitations-110dcd4b67cf)).
- **The causal narrative is unsupported.** Institutional makers do not
  coordinate to "engineer" retail stops; failed breakouts and sweeps are
  well explained by ordinary mechanics — thin books at extremes, stop
  cascades, momentum, mean reversion after liquidity demand shocks
  (Grossman-Miller). Deliberate stop-hunting as coordinated institutional
  strategy lacks order-flow evidence; where individual actors do ignite
  momentum deliberately, it is prosecuted manipulation, not a business model.
- **BUT parts of the phenomenology are real.** Stop-loss orders demonstrably
  cluster at round numbers and recent extremes, and trigger price cascades —
  Osler's Journal of Finance / JIMF work on FX stop orders documented both
  the clustering and the predictable cascades. Liquidity-vacuum overshoots
  followed by reversion (the "V") are a real, studied pattern. Kyle-model
  informed traders genuinely conceal intent. So MMXM is best read as a
  narrative wrapper around real microstructure phenomena, with an
  evidence-free intentionality story on top.

### Applicability to Polymarket: mostly none, one testable residue

1. **There are no stop orders on Polymarket's CLOB** — no stop-loss pools
   exist, so the fuel for "liquidity hunts" in the ICT sense is absent.
2. Prices are bounded and settle at truth; the terminal anchor (settlement)
   dominates any curve narrative.
3. Books are thin and event-jump driven; "consolidation -> engineered trend"
   phases are indistinguishable from news flow.
4. **The testable residue**: "sweep then revert" reduces to an empirical
   question our infrastructure already tests — is there systematic mean
   reversion after large one-bar moves / depth-clearing prints? That is a
   special case of the WO-43 drift scan plus markout ledger, and it will be
   judged under constraints 6-7 (planted-truth estimator tests, BH-FDR) —
   not by narrative. If reversion-after-sweep is real on this venue, the scan
   flags it; if not, no amount of curve-drawing makes it tradeable.

**Verdict:** build with Part I; file Part II as folklore whose one falsifiable
claim is already inside our pre-registered testing program.

---

## Part III — Application map to our system

| Theory | Where it lives in our stack | Status |
|---|---|---|
| Glosten-Milgrom adverse selection | Markout charge on band-crossing prints (+5min, queue-share weighted) | SHIPPED (maker study) |
| Spread decomposition / realized-spread-minus-markout | Study's net carry = rewards - worst-case pick-off | SHIPPED (conservative: ignores rebate + holding income) |
| A-S fill intensity lambda(delta) | Band-crossing prints/day at quote distance | SHIPPED (empirical, not parametric) |
| A-S optimal delta | Per-market distance sweep (0.25/0.5/0.75 x band) maximizing net | SHIPPED (grid, not closed-form) |
| A-S reservation-price inventory skew | Quote-sheet standing rule (skew/reduce, never add, when inventoried) | Rule added 2026-07-10; parametric skew = future WO if gates pass |
| VPIN / toxicity conditioning | WO-37 wallet tiers -> markout by wallet cohort | QUEUED (Codex) |
| Queue-position value | WO-40 last-in-queue fill replay (now upgradeable to official /orderbook-history) | QUEUED (Codex) |
| Hawkes jump clustering | Dual-window pick-off charge; regime-switch model filed as refinement | PARTIAL |
| Event-risk quote pulling | Quote-sheet rule 1 + event keyword flags + venue auto-cancel at game start | SHIPPED |
| Terminal binary risk (no hedge at expiry) | Sheet rules: minimum size, GTD expiry, stay inside mid [0.10,0.90] | Rule added 2026-07-10 |
| Prediction-market A-S (2025 paper) | Candidate framework for post-gates sizing (belief variance from our 1-min series) | READING LIST |
| MMXM sweep-reversion residue | WO-43 drift scan + markout ledger under FDR discipline | QUEUED (Codex) |

### Concrete rule changes adopted from this research

Added to the daily quote sheet's standing rules (research-grounded, zero new
machinery): (a) **inventory skew** — once filled on one side, requote to
reduce, never to add (Ho-Stoll/A-S reservation-price logic, Optiver practice);
(b) **band discipline** — quote only while mid is inside [0.10, 0.90], where
the venue scores single-sided liquidity and tick/gamma risk is lowest.

### Sources

Academic: Stoll 1978; Amihud-Mendelson 1980; Ho-Stoll 1981; Glosten-Milgrom
1985; Kyle 1985; Huang-Stoll 1997; Easley-O'Hara PIN; Easley-Lopez de
Prado-O'Hara VPIN; Avellaneda-Stoikov 2008; Gueant-Lehalle-Fernandez-Tapia;
Cartea-Jaimungal; Grossman-Miller 1988; Osler (FX stop-loss clustering and
cascades); Hanson LMSR. Web: links inline above.
