# Sharp-odds anchor (independent alpha signal)

The mispricing-alpha overlay can only beat the Polymarket price if its "fundamental" probability
comes from a **sharper** source than the market. De-vigged bookmaker *consensus* ≈ the Polymarket
price, so it adds no edge. This pipeline brings a genuinely sharp book (Pinnacle / Betfair Exchange)
into that slot.

## Flow

```
fetch-sharp-odds   The Odds API -> inputs/polymarket/sharp_odds.csv   (market_slug,outcome,decimal_odds)
build-sharp-anchor de-vig + join -> outputs/polymarket_training/sharp_fundamental_probabilities.csv
train-mispricing-alpha / validate   does the overlay now beat the market OOS?
```

`refresh-sharp-anchor` runs the first two in one step (for the live loop).

## 1. Get an odds key

Pinnacle and Betfair have no free public API; **The Odds API** aggregates both. Get a key at
the-odds-api.com and set it in the environment (never commit it):

```bash
export THE_ODDS_API_KEY=your_key
```

Tune `sharp_odds_fetch` in the config: `sports` (e.g. `soccer_fifa_world_cup`), `regions`
(`eu,uk` — Pinnacle is in `eu`, Betfair Exchange in `uk`/`eu`), and `bookmaker_priority`
(Pinnacle first, Betfair fallback). The Odds API charges usage credits per request, so keep the
sport list tight; `x-requests-remaining` is reported in the fetch summary.

## 2. Run it

```bash
polymarket-engine fetch-sharp-odds   --config polymarket_predictive_config.example.yaml
polymarket-engine build-sharp-anchor --config polymarket_predictive_config.example.yaml
polymarket-engine train-mispricing-alpha --config ...
polymarket-engine validate --config ...     # the verdict
```

The de-vig is the favourite-longshot-aware power method by default (`sharp_anchor.devig_method`),
which removes the bookmaker overround within each mutually-exclusive market so the fair
probabilities sum to 1.

## 3. The one tricky part: the token join

The fetch keys odds by `market_slug` (normalised `home vs away`) and `outcome` (team / `Draw`).
To land in the fundamental slot these must resolve to a **Polymarket `token_id`**. Two ways:

- **Direct** — add a `token_id` column to `sharp_odds.csv` (most reliable; you map once).
- **Map** — `sharp_anchor.token_map_path` (default the bot's `market_snapshot.csv`) joins
  `(market_slug, outcome) -> token_id` by normalised key. This only works when the Polymarket
  snapshot carries comparable team-name outcomes; cross-venue name differences will miss.

The `build-sharp-anchor` summary reports `skipped_no_token` so you can see join coverage. Start with
direct `token_id`s for the markets you care about, then broaden via the map.

## Manual fallback safety

If the API provider is unavailable, `sharp_odds_fetch.fallback_input_paths` can feed a manually
validated CSV into the same contract. Those rows are intentionally strict:

- required columns: `market_slug,outcome,decimal_odds,bookmaker,anchor_timestamp_utc`;
- optional but recommended: `token_id` and `anchor_source`;
- stale fallback rows are rejected by default after `fallback_max_age_hours` (24 hours in the example
  config);
- rejected rows are written to `outputs/polymarket_model_governance/sharp_odds_fallback_rejections.csv`;
- `build-sharp-anchor` refuses to de-vig a partial market if the priced outcomes look incomplete.

That last point matters for futures/outrights. A World Cup winner file with only France and Spain
would be mathematically unsafe: de-vigging only those two teams would inflate their probabilities as
if Brazil, Argentina, England, and every other runner did not exist. The engine now treats that as
missing anchor data rather than manufactured edge.

## Crypto markets (Deribit options)

For "Will BTC/ETH be above $K by DATE?" markets the fair value is *mechanically* computable - no
labelling lag, no name-matching - so it is the cleanest anchor to validate. The risk-neutral
probability `P(S_T > K)` equals the negative slope of the option call-price curve
(`-dC/dK`, Breeden-Litzenberger), and Deribit quotes a dense public chain (no API key needed).

```bash
# targets: token_id,currency,strike,expiry  (see inputs/polymarket/crypto_targets.example.csv)
polymarket-engine build-crypto-fundamental --config polymarket_predictive_config.example.yaml
```

It fetches Deribit's BTC/ETH option chain, builds a `P(S_T > K)` survival curve per expiry (snapping
to the nearest available expiry), and interpolates the probability at each target strike - writing
the same fundamental contract (`crypto_fundamental_probabilities.csv`, already in
`fundamental_probability_paths`). Each row carries `in_curve_range`: a strike outside the quoted
range is flat-extrapolated to the tail and **overstates** deep-OTM probability, so discount those.
A live run with spot ≈ \$60.7k and a 2-month expiry gives, e.g., `P(>70k)=0.17`, `P(>90k)=0.007`,
`P(>100k)=0.002` - a sensible decreasing curve straight from the option market.

## Safety

Everything here is offline/transform + a read-only odds GET. It produces a *fundamental probability*
that the overlay haircuts (`fundamental_probability_haircut`) and cross-checks
(`require_fundamental_cross_check_for_trading`) before it can ever influence a (still paper/dry-run)
trade. No order path is touched.
