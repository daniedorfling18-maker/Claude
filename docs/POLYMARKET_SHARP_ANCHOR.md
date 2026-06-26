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

## Safety

Everything here is offline/transform + a read-only odds GET. It produces a *fundamental probability*
that the overlay haircuts (`fundamental_probability_haircut`) and cross-checks
(`require_fundamental_cross_check_for_trading`) before it can ever influence a (still paper/dry-run)
trade. No order path is touched.
