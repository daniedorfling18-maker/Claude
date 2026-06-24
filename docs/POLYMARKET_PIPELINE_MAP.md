# Polymarket Pipeline Map

Generated from static repository inspection.

| Service or Script | Category | Can place orders | Training suitable | Outputs |
|---|---:|---:|---:|---|
| polymarket-monitor | unknown | True | False | outputs/polymarket |
| polymarket-long-short | unknown | True | False | outputs/polymarket |
| pm-data-collector | unknown | False | False | outputs/polymarket |
| pm-fixed-monitor | unknown | True | False | outputs/polymarket |
| pm-fixed-converter | unknown | False | False | outputs/polymarket |
| pm-fixed-long-short | unknown | True | False | outputs/polymarket |
| pm-fixed-mm-eval | unknown | False | False | outputs/polymarket |
| pm-fixed-ml | unknown | False | False | outputs/polymarket |
| pm-wide-all-monitor | all | True | False | outputs/polymarket_wide/all |
| pm-wide-all-ml | all | False | False | outputs/polymarket_wide/all |
| pm-wide-worldcup-monitor | worldcup | True | False | outputs/polymarket_wide/worldcup |
| pm-wide-worldcup-ml | worldcup | False | False | outputs/polymarket_wide/worldcup |
| pm-wide-soccer-monitor | soccer | True | False | outputs/polymarket_wide/soccer |
| pm-wide-soccer-ml | soccer | False | False | outputs/polymarket_wide/soccer |
| pm-wide-sports-monitor | sports | True | False | outputs/polymarket_wide/sports |
| pm-wide-sports-ml | sports | False | False | outputs/polymarket_wide/sports |
| pm-wide-crypto-monitor | crypto | True | False | outputs/polymarket_wide/crypto |
| pm-wide-crypto-ml | crypto | False | False | outputs/polymarket_wide/crypto |
| pm-wide-bitcoin-monitor | bitcoin | True | False | outputs/polymarket_wide/bitcoin |
| pm-wide-bitcoin-ml | bitcoin | False | False | outputs/polymarket_wide/bitcoin |
| pm-wide-election-monitor | election | True | False | outputs/polymarket_wide/election |
| pm-wide-election-ml | election | False | False | outputs/polymarket_wide/election |
| pm-wide-trump-monitor | trump | True | False | outputs/polymarket_wide/trump |
| pm-wide-trump-ml | trump | False | False | outputs/polymarket_wide/trump |
| pm-wide-finance-monitor | finance | True | False | outputs/polymarket_wide/finance |
| pm-wide-finance-ml | finance | False | False | outputs/polymarket_wide/finance |
| pm-wide-fed-monitor | fed | True | False | outputs/polymarket_wide/fed |
| pm-wide-fed-ml | fed | False | False | outputs/polymarket_wide/fed |
| polymarket-agent | all | True | False | outputs/polymarket |
| build_polymarket_bookmaker_cross_check.py | election | False | False | outputs/polymarket_available_markets/strict_clean_candidate_edges.csv;outputs/polymarket_available_markets/bookmaker_cross_check.csv |
| build_polymarket_superbru_decision_board.py | election | True | False | outputs/latest/predictions_upcoming.csv;outputs/polymarket_available_markets/bookmaker_cross_check.csv;outputs/polymarket_available_markets/polymarket_superbru_decision_board.csv;outputs/polymarket_available_markets/manual_trade_tickets.csv |
| fill_polymarket_available_market_prices.py | sports | True | False | outputs/latest/predictions.csv |
| generate_polymarket_trade_intents.py | election | True | False | outputs/polymarket_available_markets/polymarket_superbru_decision_board.csv;outputs/polymarket_available_markets/auto_trade_intents.csv;outputs/polymarket_available_markets/auto_trade_intents.json |
| polymarket_long_short_engine.py | all | True | False | outputs/backtesting/prediction_log.csv;outputs/polymarket-wc-retrospective/polymarket_wc_1x2_summary.csv;outputs/polymarket/long_short_intents.csv |
| polymarket_market_making_eval.py | all | False | False | outputs/polymarket/market_snapshot.csv;outputs/polymarket/long_short_intents.csv;outputs/polymarket/mm_quote_state.csv;outputs/polymarket/mm_quote_evaluations.csv;outputs/polymarket/mm_quote_summary.csv |
| polymarket_mispricing_bot.py | all | True | False | outputs/polymarket |
| polymarket_ml_collector.py | all | False | True | outputs/polymarket |
| polymarket_wc_retrospective.py | all | True | False | outputs/polymarket-wc-retrospective |
| run_polymarket_available_markets_simulator.py | sports | False | False | outputs/latest/predictions.csv;outputs/polymarket_available_markets |
| run_polymarket_flat_stake_simulator.py | sports | False | False | outputs/latest/predictions.csv;outputs/polymarket_flat_stake |
| run_polymarket_live_trade_decision_loop.py | election | True | False | outputs/polymarket_available_markets/auto_trade_intents.csv;outputs/polymarket_available_markets/live_trade_decisions.csv;outputs/polymarket_available_markets/live_trade_decisions.json |
| run_polymarket_market_making_operation.py | election | False | False | outputs/polymarket/mm_quote_state.csv;outputs/polymarket/mm_quote_evaluations.csv;outputs/polymarket/mm_quote_summary.csv |
| run_polymarket_pipeline.py | worldcup | True | False | outputs/polymarket;outputs/polymarket;outputs/polymarket;outputs/polymarket |
