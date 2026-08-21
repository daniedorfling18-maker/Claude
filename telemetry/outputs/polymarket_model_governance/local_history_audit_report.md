# Polymarket Local History Audit

Generated: 2026-08-20T03:12:37Z

## Paper decision: BLOCK

no approved trade_signals rows; sports_other has no shadow evidence yet

## Warnings

- aggregate shadow P&L is negative across all experimental cohorts; diagnostic warning, not a cross-family paper blocker

## Core counts

- Features: 42585
- Labels: 2692
- Predictions: 17420
- Approved signals: 0
- Rejected signals: 17420
- Shadow positions: 227
- Shadow fills: 453

## Shadow P&L

- Cost basis: 2270.00
- Realised P&L: -160.84
- Unrealised P&L: -10.00
- Total P&L: -170.84
- ROI: -0.0753

## Top rejection reasons

- 6072: alpha lower-bound edge below configured minimum; cohort_quarantined
- 2360: alpha lower-bound edge below configured minimum; liquidity_below_alpha_trade_limit; cohort_quarantined
- 1968: alpha lower-bound edge below configured minimum; price_below_alpha_trade_limit; cohort_quarantined
- 1327: alpha lower-bound edge below configured minimum; spread_above_alpha_trade_limit; relative_spread_above_alpha_trade_limit; cohort_quarantined
- 1027: alpha lower-bound edge below configured minimum
- 732: alpha lower-bound edge below configured minimum; relative_spread_above_alpha_trade_limit; cohort_quarantined
- 679: alpha lower-bound edge below configured minimum; spread_above_alpha_trade_limit; relative_spread_above_alpha_trade_limit; liquidity_below_alpha_trade_limit; cohort_quarantined
- 582: alpha lower-bound edge below configured minimum; spread_above_alpha_trade_limit; cohort_quarantined
- 427: alpha lower-bound edge below configured minimum; liquidity_below_alpha_trade_limit
- 345: alpha lower-bound edge below configured minimum; price_below_alpha_trade_limit
- 320: alpha lower-bound edge below configured minimum; price_below_alpha_trade_limit; relative_spread_above_alpha_trade_limit; cohort_quarantined
- 294: alpha lower-bound edge below configured minimum; price_below_alpha_trade_limit; liquidity_below_alpha_trade_limit; cohort_quarantined
- 194: alpha lower-bound edge below configured minimum; spread_above_alpha_trade_limit; liquidity_below_alpha_trade_limit; cohort_quarantined
- 161: alpha lower-bound edge below configured minimum; relative_spread_above_alpha_trade_limit
- 142: alpha lower-bound edge below configured minimum; relative_spread_above_alpha_trade_limit; liquidity_below_alpha_trade_limit; cohort_quarantined

## Top alpha rows

- tennis | wta-lepchen-ibragim-2026-07-13-set-handicap-away-1pt5 | Lepchenko | edge_lower_bound=0.4802794565536074 | candidate=False
- tennis | atp-krumich-sil-2026-07-12-set-2-total-8pt5 | Over | edge_lower_bound=0.4735273538357472 | candidate=False
- tennis | atp-krumich-sil-2026-07-12-set-2-total-10pt5 | Over | edge_lower_bound=0.4735273538357472 | candidate=False
- tennis | atp-krumich-sil-2026-07-12-set-2-total-9pt5 | Over | edge_lower_bound=0.4731919436391222 | candidate=False
- tennis | atp-krumich-sil-2026-07-12-match-total-21pt5 | Over | edge_lower_bound=0.47106260232697394 | candidate=False
- tennis | atp-krumich-sil-2026-07-12-completed-match | No | edge_lower_bound=0.4696086361603575 | candidate=False
- tennis | wta-lepchen-ibragim-2026-07-13-completed-match | No | edge_lower_bound=0.46621345137673076 | candidate=False
- tennis | wta-lepchen-ibragim-2026-07-13-match-total-23pt5 | Over | edge_lower_bound=0.46621345137673076 | candidate=True
- tennis | atp-sinner-zverev-2026-07-12-set-3-total-9pt5 | Over | edge_lower_bound=0.46408230383574717 | candidate=False
- tennis | wta-lepchen-ibragim-2026-07-13-set-2-total-9pt5 | Under | edge_lower_bound=0.46408230383574717 | candidate=False

## Closing-line value (CLV)

- positions_scored=109, final_line_positions=90, mean_final_clv=0.0084, positive_clv_cohorts=none
- baseball_mlb_match — n_final=2, mean_final_clv=-0.0100, CI=[-0.0350, 0.0150], evidence=insufficient_clv_evidence
- crypto — n_final=21, mean_final_clv=0.0940, CI=[-0.0834, 0.2855], evidence=insufficient_clv_evidence
- crypto_btc_updown_event — n_final=3, mean_final_clv=-0.0998, CI=[-0.4795, 0.5295], evidence=insufficient_clv_evidence
- crypto_eth_updown_event — n_final=2, mean_final_clv=-0.4645, CI=[-0.4695, -0.4595], evidence=insufficient_clv_evidence
- crypto_xrp_updown_event — n_final=3, mean_final_clv=-0.0198, CI=[-0.4095, 0.5995], evidence=insufficient_clv_evidence
- esports_match — n_final=3, mean_final_clv=0.3663, CI=[0.0000, 0.5895], evidence=insufficient_clv_evidence
- exploratory_crypto_updown_live_model|crypto_btc_updown_daily|outcome=down — n_final=1, mean_final_clv=-0.2395, CI=[n/a, n/a], evidence=insufficient_clv_evidence
- exploratory_crypto_updown_live_model|crypto_btc_updown_daily|outcome=up — n_final=2, mean_final_clv=0.2895, CI=[0.0895, 0.4895], evidence=insufficient_clv_evidence
- exploratory_crypto_updown_live_model|crypto_eth_updown_15m|outcome=up — n_final=1, mean_final_clv=-0.0050, CI=[n/a, n/a], evidence=insufficient_clv_evidence
- exploratory_crypto_updown_live_model|crypto_eth_updown_daily|outcome=up — n_final=3, mean_final_clv=0.4027, CI=[0.2190, 0.5795], evidence=insufficient_clv_evidence
- exploratory_crypto_updown_live_model|crypto_sol_updown_15m|outcome=up — n_final=1, mean_final_clv=0.4818, CI=[n/a, n/a], evidence=insufficient_clv_evidence
- exploratory_crypto_updown_live_model|crypto_xrp_updown_daily|outcome=up — n_final=1, mean_final_clv=0.1195, CI=[n/a, n/a], evidence=insufficient_clv_evidence
- near_miss_learning|baseball_mlb_match — n_final=4, mean_final_clv=0.0550, CI=[-0.0400, 0.1825], evidence=insufficient_clv_evidence
- near_miss_learning|crypto — n_final=4, mean_final_clv=-0.0841, CI=[-0.3098, 0.3210], evidence=insufficient_clv_evidence
- near_miss_learning|crypto_btc_updown_event — n_final=1, mean_final_clv=-0.2575, CI=[n/a, n/a], evidence=insufficient_clv_evidence
- near_miss_learning|crypto_eth_updown_event — n_final=1, mean_final_clv=-0.1775, CI=[n/a, n/a], evidence=insufficient_clv_evidence
- near_miss_learning|exploratory_historical_rule|crypto_sol_updown_event|outcome=down — n_final=1, mean_final_clv=0.1750, CI=[n/a, n/a], evidence=insufficient_clv_evidence
- near_miss_learning|geopolitics — n_final=1, mean_final_clv=-0.0035, CI=[n/a, n/a], evidence=insufficient_clv_evidence
- near_miss_learning|macro_rates — n_final=2, mean_final_clv=0.0235, CI=[0.0130, 0.0340], evidence=insufficient_clv_evidence
- near_miss_learning|tennis_tennis_winner — n_final=1, mean_final_clv=-0.2350, CI=[n/a, n/a], evidence=insufficient_clv_evidence
- near_miss_learning|unknown — n_final=2, mean_final_clv=-0.4818, CI=[-0.4950, -0.4685], evidence=insufficient_clv_evidence
- near_miss_learning|worldcup — n_final=5, mean_final_clv=0.2054, CI=[-0.2037, 0.6090], evidence=insufficient_clv_evidence
- soccer_match — n_final=3, mean_final_clv=-0.0752, CI=[-0.3995, 0.5145], evidence=insufficient_clv_evidence
- structural|longshot_no|ai_model_leader — n_final=2, mean_final_clv=0.0703, CI=[0.0351, 0.1055], evidence=insufficient_clv_evidence
- structural|longshot_no|crypto_btc_special — n_final=1, mean_final_clv=0.0177, CI=[n/a, n/a], evidence=insufficient_clv_evidence
- structural|longshot_no|crypto_eth_special — n_final=2, mean_final_clv=-0.0005, CI=[-0.0005, -0.0005], evidence=insufficient_clv_evidence
- structural|longshot_no|crypto_sol_special — n_final=2, mean_final_clv=-0.0007, CI=[-0.0010, -0.0005], evidence=insufficient_clv_evidence
- unknown — n_final=7, mean_final_clv=-0.1129, CI=[-0.4174, 0.2296], evidence=insufficient_clv_evidence
- worldcup — n_final=7, mean_final_clv=-0.2891, CI=[-0.4561, -0.0142], evidence=insufficient_clv_evidence
- worldcup_2026_winner_fundamental — n_final=1, mean_final_clv=0.2375, CI=[n/a, n/a], evidence=insufficient_clv_evidence

## Edge attribution

- closed_positions_seen=226, attributed_positions=109, skipped_unattributable_closed=117
- ai_model_leader — positions=6, total_pnl=-3.956002, line_movement=-1.320517, execution_cost=1.141039, class=model_direction_not_confirmed
- baseball_mlb_match — positions=2, total_pnl=-1.396135, line_movement=-0.231884, execution_cost=0.219807, class=insufficient_attribution_evidence
- crypto — positions=21, total_pnl=-3.845278, line_movement=-245.102363, execution_cost=-262.537366, class=model_direction_not_confirmed
- crypto_btc_updown_event — positions=3, total_pnl=-4.988476, line_movement=-8.355934, execution_cost=0.353407, class=insufficient_attribution_evidence
- crypto_eth_updown_event — positions=2, total_pnl=-1.859389, line_movement=-19.763414, execution_cost=0.215079, class=insufficient_attribution_evidence
- crypto_xrp_updown_event — positions=3, total_pnl=4.640244, line_movement=-4.533354, execution_cost=0.446951, class=insufficient_attribution_evidence
- esports_match — positions=3, total_pnl=-9.777203, line_movement=25.202041, execution_cost=0.426033, class=insufficient_attribution_evidence
- exploratory_crypto_updown_live_model|crypto_btc_updown_daily|outcome=down — positions=1, total_pnl=2.916667, line_movement=-9.770833, execution_cost=0.208333, class=insufficient_attribution_evidence
- exploratory_crypto_updown_live_model|crypto_btc_updown_daily|outcome=up — positions=2, total_pnl=-2.684458, line_movement=11.130299, execution_cost=0.156986, class=insufficient_attribution_evidence
- exploratory_crypto_updown_live_model|crypto_eth_updown_15m|outcome=up — positions=1, total_pnl=45.555556, line_movement=0.0, execution_cost=0.277778, class=insufficient_attribution_evidence

## Algo sweep lab

- decision=no_sweep_candidate_reached_minimum_train_fills, strategy=tight_spread_join_bid_shadow, combos_tested=9, train_candidates=0
