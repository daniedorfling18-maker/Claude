# Polymarket API assimilation notes

Assimilated 2026-07-10 from https://docs.polymarket.com (llms-full.txt, 1.03MB,
32,068 lines). Conceptual/mechanics sections read in full; SDK method listings
and per-endpoint reference stubs cataloged programmatically (table at bottom).
Sections read lightly: perps account-management/authenticated-sessions detail,
prediction-market referral page, trading SDK how-to pages. Live-verified probes
are marked [VERIFIED LIVE].

## Platform architecture

- **Gamma API** `gamma-api.polymarket.com` - markets, events, tags, series,
  comments, sports (+/teams), public-search, profiles. Public.
- **Data API** `data-api.polymarket.com` - positions, closed-positions, trades,
  activity, value, oi, holders, leaderboard, combo positions/activity, builder
  analytics. Public.
- **CLOB API** `clob.polymarket.com` - books/prices/midpoints/spreads (+batch
  POST variants up to 500 tokens), prices-history, **orderbook-history**
  [VERIFIED LIVE 2026-07-10: 102k+ historical book snapshots with full bid/ask
  ladders for one WC token - historical order books EXIST via API], ohlc
  (documented in error-code reference; path probe 404'd - param/path uncertain),
  last-trade-price(+batch), tick-size, neg-risk, clob-markets/{condition_id}
  (fee + itode flags); trading endpoints under L2 auth.
- **Bridge API** `bridge.polymarket.com` - fun.xyz proxy. deposit/withdraw/
  quote/status/supported-assets. 15 chains (Ethereum min $7, most L2s $2,
  BTC/Tron $9). All inbound auto-wrapped to pUSD. Withdrawals instant/free but
  route pUSD->USDC through one Uniswap v3 pool (can exhaust; split large
  withdrawals; >$50k use third-party bridge). Wrong-token recovery:
  recovery.polymarket.com.
- **Relayer** `relayer-v2.polymarket.com` - gasless wallet deploy/approvals/CTF
  ops/transfers. RELAYER_API_KEY(+_ADDRESS) headers. /submit 25 req/min.
  States: STATE_NEW->EXECUTED->MINED->CONFIRMED (terminal: CONFIRMED, FAILED,
  INVALID).
- **RFQ system (Combos)** - WebSocket + REST for multi-leg quote auctions.
- **Perps** - separate perpetual futures exchange, /v1/* paths, own auth.
- **Polymarket US** exists separately: docs.polymarket.us.

## Authentication (CLOB)

- L1 = wallet EIP-712 (`ClobAuthDomain`, chainId 137, message "This message
  attests that I control the given wallet") -> POST /auth/api-key or GET
  /auth/derive-api-key. Headers POLY_ADDRESS/SIGNATURE/TIMESTAMP/NONCE.
- L2 = HMAC-SHA256 with (apiKey, secret, passphrase). 5 headers:
  POLY_ADDRESS/SIGNATURE/TIMESTAMP/API_KEY/PASSPHRASE.
- Signature types: 0 EOA, 1 POLY_PROXY (Magic), 2 GNOSIS_SAFE, 3 POLY_1271
  (deposit wallets - new API users; ERC-7739-wrapped sigs; maker=signer=deposit
  wallet; V2 orders only).
- Deposit wallets: ERC-1967 beacon proxies from factory
  0x00000000000Fb5C9ADea0298D729A0CB3823Cc07; relayer WALLET-CREATE (no user
  sig) + WALLET batches (65-byte EIP-712 over DepositWallet Batch).
- V2 order struct: salt, maker, signer, tokenId, makerAmount, takerAmount,
  side, signatureType, timestamp, metadata, **builder** (bytes32). EIP-712
  domain version "2" (Exchange v3 domain for Combos).

## Fees (canonical, from /trading/fees)

`fee = C x feeRate x p x (1-p)` per fill, taker-only, at match time. Makers
NEVER pay. Fee in USDC peaks at p=0.5, symmetric. Rounded to 5dp; min 0.00001.
Applies to markets deployed on/after activation; `feesEnabled` flag + params
via getClobMarketInfo (fd.r/fd.e/fd.to; mbf/tbf builder rates).

| Category | Taker rate | Maker rebate share |
|---|---|---|
| Crypto | 0.07 | 20% |
| **Sports** | **0.05** | 15% |
| Finance / Politics / Mentions / Tech | 0.04 | 25% |
| Economics / Culture / Weather / Other | 0.05 | 25% |
| Geopolitics | 0 (fee-free) | - |

Per-dollar taker cost = rate x (1-p). SYSTEM IMPACT: verdict amendment 6
raises sports taker_fee_rate 0.03 -> 0.05.

**Maker Rebates**: share of taker fees redistributed DAILY (pUSD, $1 min) to
makers whose resting liquidity got FILLED, fee-curve weighted
(your fee_equivalent / market total x pool), per-market pools.
**Taker Rebate Program** (live 2026-05-28): tiers on 30-day Weighted Volume
wV = size x (1-entry) x category weight (Sports 1.0, Politics/Fin/Mentions/
Tech 1.3, Econ/Culture/Weather/Other 1.7, Crypto 2.3, Geo 0). Bronze $2k->3%
... Obsidian $10M->50%; daily pUSD payouts; one-time level-up bonuses
($10..$25k); omnibus wallets ineligible.
**Holding Rewards**: 4.00% annualized on total position value in eligible
markets, sampled randomly once/hour, paid daily. Variable rate.

## Liquidity rewards (maker incentive program)

Quadratic score S(v,s) = ((v-s)/v)^2 x b (b = in-game multiplier). Two side
scores Q_one/Q_two aggregate bids on m + asks on m' (and vice versa), sampled
randomly EVERY MINUTE. Midpoint in [0.10,0.90]:
Q_min = max(min(Q1,Q2), max(Q1,Q2)/c), c=3.0 (single-sided scores at 1/3).
Outside that range: strictly min (double-sided required). Normalize per sample
across makers, sum over epoch (10,080 samples doc'd = 7d of minutes; payouts
daily 00:00 UTC, min $1). Spread measured vs size-cutoff-adjusted midpoint;
min_incentive_size / max_incentive_spread per market via Gamma/CLOB.
**WC 2026 incentive program Jun 11 - Jul 19** ($/game pre+live caps):
group $6,110 (marquee $10,725), R32 $13,650, R16 $18,200, QF $26,975,
SF $38,350, 3rd place $15,600, FINAL $52,000. ENDS JUL 19 - pot universe
shrinks after.

## Trading mechanics

- All orders are EIP-712-signed limit orders; market order = marketable limit.
  Types: GTC, GTD (+60s security buffer convention), FOK, FAK; post-only
  rejected if it would cross. Batch POST /orders up to 15.
- Taker delay: 250ms on selected crypto/finance up-down markets (`itode: true`
  via GET /clob-markets/{condition_id}); sports have configured game delays;
  pending-delay orders cannot be cancelled; order statuses live/matched/
  delayed/unmatched. Trade statuses MATCHED->MINED->CONFIRMED (RETRYING->FAILED).
- maxOrderSize = balance - sum(openOrderSize - filled).
- Tick sizes 0.1/0.01/0.001/0.0001; tick_size_change event when price >0.96 or
  <0.04. **WC advance/moneyline/spreads/totals markets use 0.0025 ticks.**
- SPORTS: resting orders auto-cancelled at official game start (early starts
  may not clear in time).
- Matching engine restarts: HTTP 425 on order endpoints, exponential backoff;
  after restart 2 MINUTES post-only mode (503 + code post_only_mode +
  Retry-After); cancel-only mode possible; ~2 days notice via Telegram
  t.me/polytradingapis + Discord #trading-apis.
- Displayed price = midpoint, unless spread > $0.10 -> last trade. Price
  discovery: complementary bids (0.60 YES + 0.40 NO) match by minting.
  No trading size limits.
- Error notes: banned address / closed-only mode errors exist; "not found"
  strings force 404; FOK fully-filled-or-killed; FAK needs >=1 match.

## Market structure & settlement

- Market = binary condition (conditionId, questionId, 2 ERC-1155 token IDs;
  `enableOrderBook` gates CLOB). Event = container; negRisk events are
  mutually-exclusive multi-outcome with No->all-other-Yes conversion via
  NegRiskAdapter; augmented negRisk = named + placeholder + Other outcomes
  (`negRiskAugmented`; only trade NAMED outcomes).
- CTF ops (via pUSD collateral adapters, gasless through relayer): split $1 ->
  1 YES + 1 NO; merge pair -> $1; redeem winners post-resolution (burns whole
  balance, indexSets [1,2]). Position IDs derivable (oracle=UMA adapter,
  parentCollectionId=0, indexSet 1/2) but exposed by Gamma.
- **pUSD**: ERC-20 wrapper over USDC.e (CollateralOnramp/Offramp enforce),
  6 decimals, Polygon; all trading collateral. Deposits auto-wrap.
- Resolution: UMA Optimistic Oracle; anyone proposes with ~$750 bond; 2h
  challenge window; dispute -> second proposal; second dispute -> DVM vote
  (~48h; debate 24-48h in UMA Discord). Outcomes: proposer/disputer wins, Too
  Early, or 50/50 (each token $0.50). Undisputed ~2h; disputed 4-6 days.
  Clarifications via bulletin board ("Additional context"). UmaCtfAdapter
  v3.0 0x157Ce2d672854c848c9b79C49a8Cc6cc89176a49.

## Key contracts (Polygon 137)

CTF Exchange 0xE111180000d2663C0091e4f400237545B87B996B; NegRisk CTF Exchange
0xe2222d279d744050d28e00520010520000310F59; NegRisk Adapter 0xd91E80cF2E7be2e1
62c6513ceD06f1dD0dA35296; ConditionalTokens 0x4D97DCd97eC945f40cF65F87097ACe5E
A0476045; pUSD 0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB; CtfCollateralAdapter
0xAdA100Db00Ca00073811820692005400218FcE1f (negRisk variant 0xadA2005600Dec949
baf300f4C6120000bDB6eAab); Onramp 0x93070a847efEf7F70739046A929D47a521F5B8ee;
Combos: Exchange v3 0xe3333700cA9d93003F00f0F71f8515005F6c00Aa, PositionManager
0x006F54F7f9A22e0000CC2AB60031000000ae9fEF, CombinatorialModule
0x30000034706C7d8e12009DAB006Be20000c031A8, AutoRedeemer 0xa1200000d0002264C9a
1698e001292D00E1b00af; Perps deposit 0xDCa4af75705dbB50f62437045afF9921947917d2.
CTF Exchange V2 audited by Quantstamp + Cantina (Mar 2026).

## WebSockets

| Channel | URL | Auth |
|---|---|---|
| market | wss://ws-subscriptions-clob.polymarket.com/ws/market | no |
| user | .../ws/user | L2 creds, subscribe by CONDITION ids |
| sports | wss://sports-api.polymarket.com/ws | no (server pings 5s, pong<10s) |
| RTDS | wss://ws-live-data.polymarket.com | optional gamma_auth |

market events: book, price_change (size 0 = level removed), tick_size_change,
last_trade_price, + custom_feature_enabled: best_bid_ask, new_market (carries
fee_schedule incl rebate_rate, game_start_time, sports_market_type),
market_resolved (winning_asset_id). PING every 10s. Dynamic sub/unsub.
user events: trade (status transitions), order (PLACEMENT/UPDATE/CANCELLATION).
RTDS topics: crypto_prices (Binance symbols btcusdt...), crypto_prices_chainlink
(btc/usd; sponsored key form for 15m crypto markets), equity_prices (Pyth:
stocks/ETFs/forex/XAU/XAG/WTI/CC/NGD; 2-min snapshot on subscribe;
is_carried_forward when closed; price-to-beat endpoint
polymarket.com/api/equity/price-to-beat/{slug}), comments (created/removed/
reaction_*; profile incl proxyWallet).

## Rate limits (Cloudflare, throttle-not-reject, sliding windows)

General 15k/10s. Gamma: general 4k/10s, /events 500, /markets 300, listing 900,
/comments 200, /tags 200, /public-search 350. Data API: 1k/10s general,
/trades 200, /positions 150, /closed-positions 150. CLOB: 9k/10s general;
/book+/price+/midpoint 1500; batch 500; /prices-history 1000; ledger 900;
POST /order burst 5k/10s sustained 120k/10min; /orders 2k & 21k/10min;
cancel-all 250/10s. Bridge 50/10s. Relayer /submit 25/min. PNL API 200/10s.
Servers eu-west-2; co-location via KYC/KYB form.

## Geographic restrictions (prediction markets)

- OFAC full block (frontend+API, no closing): IR, SY, CU, KP, UA-43/14/09.
- Close-only frontend+API: US, GB, FR, DE, IT, BE, PL, SK, AU, SG, TW, TH, BR,
  RU, BY + others (full list in docs). Close-only frontend-only: JP, NL,
  MT(sports).
- **South Africa: unrestricted.** Geoblock check: GET polymarket.com/api/geoblock.
- Perps (separate, stricter on CA): US, Canada, CU, IR, KP, SY, Crimea,
  Donetsk, Luhansk.

## Combos (RFQ) essentials

Multi-leg YES/NO positions; RFQ auction: request -> 400ms maker quote window
-> best quote -> 10s user acceptance -> optional Last Look (1s confirm;
requires ~$2.5k combo notional + form; >15% rejections/hr = quoting pause) ->
execution (MATCHED/MINED/RETRYING/CONFIRMED/FAILED). BUY uses notional sizing,
SELL uses shares (e6 base units; full share = 1,000,000). Maker fills BUY YES
by buying NO at 1-price (collateral) or selling YES (inventory). Combo
positions/activity via Data API (cursor pagination; closed econ =
realized_payout_usdc - total_cost_usdc). Combo markets catalog: GET combo
markets (public), position_ids[0]=YES/[1]=NO. RFQ errors incl
SUBMISSION_WINDOW_CLOSED, ALLOWANCE/BALANCE_VALIDATION_FAILED.

## Perps essentials

Separate hybrid exchange (offchain matching, Polygon custody, state-root
commitments). Early access via referral. 9 instruments: SP500/GOLD/WTIOIL/
NAS100/SILVER 20x, BTC/ETH/SOL 20x, SPCX 10x; pUSD collateral, min deposit
10 pUSD. Fees flat: maker -0.5bp REBATE, taker 2bp (launch period). Funding:
5s premium samples (1k notional book walk), 1h charge window, 8h formula /8,
interest leg 0.01%/8h, clamp +/-0.05%, scale 1.0 crypto / 0.5 other, cap
4%/hr, longs pay shorts when rich. Index 200ms from Pyth/Chainlink/Hyperliquid;
Mark = median(C1 150s-EMA book mid, C2 recent trades, C3 external marks), all
degrade to Index. Margin: IM tiers incremental, MM = IM/2; states Healthy/
Margin call (reduce-only)/Liquidation (IOC reduce-only, market-priced, no
protective spread; insurance fund absorbs below 2/3 MM; extra liquidation fee).
Self-trade prevention: CancelMaker, always on. 20ms taker delay. Auto-cancel
dead man's switch PATCH /v1/trade/auto-cancel (10 triggers/day). Account keyed
by EOA; proxy credentials (POLYMARKET-PROXY/-SECRET headers) for private
reads; signed ts+salt for trade actions. Rate limits: IP 1,000 weighted
tokens/min (book depth 10/100/500/1000 = 2/5/10/20; batch = 1+floor(n/20));
action 5,000/min, 1,000 open-order cap; WS 50 conns/IP, 100 subs/conn,
1k msgs/min. Candles 1m..1w (API adds 30m/6h/12h), max 1000. Public trades
max 100/req. Perps referral: 20% of referred trading fees, weekly, 15-use cap.

## Builder program

builderCode (bytes32, public, onchain in every attributed order; optional
X-Builder-Code header on bridge deposit/withdraw). Builder fees additive to
platform fees: taker max 100bps, maker max 50bps, 1bp granularity; changes:
1/7days cooldown + 3-day notice + one pending. Tiers: Unverified 100 relayer
txn/day, Verified 10k (email builder@polymarket.com), Partner unlimited.
Personal unlimited relayer txns via Relayer API key without routing for others.

## System-impacting findings (2026-07-10 read)

1. **Sports taker fee 0.05 not 0.03** -> verdict amendment 6 (tightening).
2. **orderbook-history endpoint exists** [VERIFIED LIVE] -> corrects prior
   "no historical books" claim; upgrades WO-40 replay data source.
3. **Maker rebates** (15% sports/20% crypto/25% rest of taker fees, daily) =
   uncounted maker-lane income; our carry study omits it (conservative).
4. **Holding rewards 4% APY** on position value = additional maker inventory
   carry, also uncounted (conservative).
5. **WC liquidity incentive program ends Jul 19** (SF $38,350/game, final
   $52,000) -> pot universe will shrink post-WC; daily history ledger will
   quantify.
6. Liquidity-reward scoring: per-minute sampling, single-sided allowed at /3
   in [0.10,0.90] mid, in-game multiplier b -> our min(Q1,Q2) share model is
   conservative in-range.
7. Sports resting orders auto-cancel at game start; WC side markets use
   0.0025 ticks -> quote-sheet mechanics.
8. Combos RFQ + perps + taker-rebate tiers exist (new products; not in scope
   for the $100 verdict but quizzable).

## Endpoint catalog (from API reference stubs)

| Method | Path | Name | Spec |
|---|---|---|---|
| GET | `/v1/info/bbo` | Get BBO | perps |
| GET | `/v1/info/book` | Get Book | perps |
| GET | `/v1/info/assets` | Get Collateral Assets | perps |
| GET | `/v1/info/exchange` | Get Exchange Info | perps |
| GET | `/v1/info/index` | Get Index | perps |
| GET | `/v1/info/instruments` | Get Instruments | perps |
| GET | `/v1/info/klines` | Get Klines | perps |
| GET | `/v1/info/time` | Get Server Time | perps |
| GET | `/v1/info/statistics` | Get Statistics | perps |
| GET | `/v1/info/tickers` | Get Tickers | perps |
| GET | `/v1/info/ping` | Test Connection | perps |
| POST | `/v1/account/referral` | Apply Referral Code | perps |
| DELETE | `/v1/trade/orders` | Cancel Orders | perps |
| DELETE | `/v1/trade/orders-coid` | Cancel Orders COID | perps |
| GET | `/v1/info/invite` | Check Invite Code | perps |
| GET | `/holders` | Get top holders for markets | data |
| POST | `/v1/account/invite` | Create Account Invite | perps |
| POST | `/v1/trade/orders` | Create Orders | perps |
| POST | `/v1/account/proxy` | Create Proxy | perps |
| GET | `/midpoint` | Get midpoint price | clob |
| GET | `/time` | Get server time | clob |
| DELETE | `/v1/account/proxy` | Delete Proxy | perps |
| GET | `/events/{id}` | Get event by id | gamma |
| GET | `/events/slug/{slug}` | Get event by slug | gamma |
| GET | `/events/{id}/tags` | Get event tags | gamma |
| GET | `/events` | List events | gamma |
| GET | `/events/keyset` | List events (keyset pagination) | gamma |
| GET | `/v1/account/limits` | Get Account Limits | perps |
| GET | `/v1/account/referral` | Get Account Referral | perps |
| GET | `/v1/account/rewards` | Get Account Rewards | perps |
| GET | `/v1/account/stats` | Get Account Stats | perps |
| GET | `/v1/account/auto-cancel` | Get Auto-Cancel Status | perps |
| GET | `/v1/account/balances` | Get Balances | perps |
| GET | `/v1/account/credentials` | Get Credentials | perps |
| GET | `/v1/account/deposits` | Get Deposits | perps |
| GET | `/v1/account/equity` | Get Equity | perps |
| GET | `/v1/info/fees` | Get Fees | perps |
| GET | `/v1/account/fills` | Get Fills | perps |
| GET | `/v1/account/funding` | Get Funding Charges | perps |
| GET | `/v1/info/funding` | Get Historical Funding | perps |
| GET | `/v1/account/config` | Get Instrument Config | perps |
| GET | `/v1/account/internal-transfers` | Get Internal Transfers | perps |
| GET | `/v1/info/limit-tiers` | Get Limit Tiers | perps |
| GET | `/v1/account/open-orders` | Get Open Orders | perps |
| GET | `/v1/account/orders` | Get Orders | perps |
| GET | `/v1/account/pnl` | Get PnL | perps |
| GET | `/v1/account/portfolio` | Get Portfolio | perps |
| GET | `/v1/info/portfolio` | Get Public Portfolio | perps |
| GET | `/v1/info/trades` | Get Recent Trades | perps |
| GET | `/v1/account/withdrawals` | Get Withdrawals | perps |
| POST | `/v1/account/internal-transfer` | Internal Transfer | perps |
| GET | `/fee-rate` | Get fee rate | clob |
| GET | `/fee-rate/{token_id}` | Get fee rate by path parameter | clob |
| GET | `/last-trade-price` | Get last trade price | clob |
| GET | `/last-trades-prices` | Get last trade prices (query parameters) | clob |
| POST | `/last-trades-prices` | Get last trade prices (request body) | clob |
| GET | `/price` | Get market price | clob |
| GET | `/prices` | Get market prices (query parameters) | clob |
| POST | `/prices` | Get market prices (request body) | clob |
| GET | `/midpoints` | Get midpoint prices (query parameters) | clob |
| POST | `/midpoints` | Get midpoint prices (request body) | clob |
| GET | `/book` | Get order book | clob |
| POST | `/books` | Get order books (request body) | clob |
| GET | `/spread` | Get spread | clob |
| POST | `/spreads` | Get spreads | clob |
| GET | `/tick-size` | Get tick size | clob |
| GET | `/tick-size/{token_id}` | Get tick size by path parameter | clob |
| POST | `/batch-prices-history` | Get batch prices history | clob |
| GET | `/clob-markets/{condition_id}` | Get CLOB market info | clob |
| GET | `/markets/{id}` | Get market by id | gamma |
| GET | `/markets/slug/{slug}` | Get market by slug | gamma |
| GET | `/markets-by-token/{token_id}` | Get market by token | clob |
| GET | `/markets/{id}/tags` | Get market tags by id | gamma |
| GET | `/prices-history` | Get prices history | clob |
| GET | `/markets` | List markets | gamma |
| GET | `/markets/keyset` | List markets (keyset pagination) | gamma |
| GET | `/live-volume` | Get live volume for an event | data |
| GET | `/oi` | Get open interest | data |
| PATCH | `/v1/trade/auto-cancel` | Set Auto-Cancel | perps |
| DELETE | `/cancel-all` | Cancel all orders | clob |
| DELETE | `/orders` | Cancel multiple orders | clob |
| DELETE | `/cancel-market-orders` | Cancel orders for a market | clob |
| DELETE | `/order` | Cancel single order | clob |
| GET | `/order-scoring` | Get order scoring status | clob |
| GET | `/data/order/{orderID}` | Get single order by ID | clob |
| GET | `/data/orders` | Get user orders | clob |
| POST | `/order` | Post a new order | clob |
| POST | `/orders` | Post multiple orders | clob |
| POST | `/heartbeats` | Send heartbeat | clob |
| PATCH | `/v1/trade/leverage` | Update Leverage | perps |
| POST | `/v1/account/withdraw` | Withdraw | perps |
| POST | `/deposit` | Create bridge addresses | bridge |
| POST | `/withdraw` | Create withdrawal addresses | bridge |
| POST | `/quote` | Get a quote | bridge |
| GET | `/supported-assets` | Get supported assets | bridge |
| GET | `/status/{address}` | Get transaction status | bridge |
| GET | `/v1/builders/leaderboard` | Get aggregated builder leaderboard | data |
| GET | `/v1/builders/volume` | Get daily builder volume time-series | data |
| GET | `/v1/rfq/combo-markets` | Get combo markets | combos-rfq |
| GET | `/comments/{id}` | Get comments by comment id | gamma |
| GET | `/comments/user_address/{user_address}` | Get comments by user address | gamma |
| GET | `/comments` | List comments | gamma |
| GET | `/closed-positions` | Get closed positions for a user | data |
| GET | `/positions` | Get current positions for a user | data |
| GET | `/v1/market-positions` | Get positions for a market | data |
| GET | `/value` | Get total value of a user's positions | data |
| GET | `/v1/leaderboard` | Get trader leaderboard rankings | data |
| GET | `/trades` | Get trades for a user or markets | data |
| GET | `/activity` | Get user activity | data |
| GET | `/v1/activity/combos` | Get user combo activity | data |
| GET | `/v1/positions/combos` | Get user combo positions | data |
| POST | `/v1/maker/quotes/cancel` | Cancel a quote | combos-rfq |
| POST | `/v1/maker/confirmations` | Confirm or decline last look | combos-rfq |
| POST | `/v1/maker/quotes` | Submit a quote | combos-rfq |
| GET | `/sampling-markets` | Get sampling markets | clob |
| GET | `/sampling-simplified-markets` | Get sampling simplified markets | clob |
| GET | `/simplified-markets` | Get simplified markets | clob |
| GET | `/v1/accounting/snapshot` | Download an accounting snapshot (ZIP of CSVs) | data |
| GET | `/traded` | Get total markets a user has traded | data |
| GET | `/public-profile` | Get public profile by wallet address | gamma |
| GET | `/rebates/current` | Get current rebated fees for a maker | clob |
| GET | `/relayer/api/keys` | Get all relayer API keys | relayer |
| GET | `/deployed` | Check if a wallet is deployed | relayer |
| GET | `/transaction` | Get a transaction by ID | relayer |
| GET | `/nonce` | Get current nonce for a user | relayer |
| GET | `/transactions` | Get recent transactions for a user | relayer |
| GET | `/relay-payload` | Get relayer address and nonce | relayer |
| POST | `/submit` | Submit a transaction | relayer |
| GET | `/rewards/markets/current` | Get current active rewards configurations | clob |
| GET | `/rewards/user` | Get earnings for user by date | clob |
| GET | `/rewards/markets/multi` | Get multiple markets with rewards | clob |
| GET | `/rewards/markets/{condition_id}` | Get raw rewards for a specific market | clob |
| GET | `/rewards/user/percentages` | Get reward percentages for user | clob |
| GET | `/rewards/user/total` | Get total earnings for user by date | clob |
| GET | `/rewards/user/markets` | Get user earnings and markets configuration | clob |
| GET | `/public-search` | Search markets, events, and profiles | gamma |
| GET | `/series/{id}` | Get series by id | gamma |
| GET | `/series` | List series | gamma |
| GET | `/sports` | Get sports metadata information | gamma |
| GET | `/sports/market-types` | Get valid sports market types | gamma |
| GET | `/teams` | List teams | gamma |
| GET | `/tags/{id}/related-tags` | Get related tags (relationships) by tag id | gamma |
| GET | `/tags/slug/{slug}/related-tags` | Get related tags (relationships) by tag slug | gamma |
| GET | `/tags/{id}` | Get tag by id | gamma |
| GET | `/tags/slug/{slug}` | Get tag by slug | gamma |
| GET | `/tags/{id}/related-tags/tags` | Get tags related to a tag id | gamma |
| GET | `/tags/slug/{slug}/related-tags/tags` | Get tags related to a tag slug | gamma |
| GET | `/tags` | List tags | gamma |
| GET | `/builder/trades` | Get builder trades | clob |
| GET | `/data/trades` | Get trades | clob |
