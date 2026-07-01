# Quantitative Finance and Algorithmic Trading Curriculum

This curriculum is designed for a coding-first AI/research workflow using freely accessible resources, open-source libraries, and reproducible Python notebooks.  The goal is to build from foundations toward live, risk-controlled, AI-assisted paper trading.

## Repository structure

```text
src/quant_lab/
  foundations.py              # Module 1 primitives: distributions, simulation, MC pricing, time series

notebooks/quant_lab/
  math_foundations.ipynb       # Module 1 worked notebook

tests/quant_lab/
  test_foundations.py          # Unit-style sanity checks for Module 1

docs/
  QUANT_CURRICULUM.md          # This curriculum
```

Future phases should add:

```text
src/quant_lab/data.py          # OHLCV ingestion and cleaning
src/quant_lab/pricing.py       # Black-Scholes, Greeks, binomial trees
src/quant_lab/risk.py          # VaR, CVaR, stress tests
src/quant_lab/backtest.py      # Portfolio accounting, costs, slippage
src/quant_lab/features.py      # Technical/factor/alternative-data features
src/quant_lab/models.py        # ML models and walk-forward validation
src/quant_lab/rl.py            # Trading environments and RL agents
```

## Module 1 — Mathematical and statistical foundations

Learning objectives:

- Understand probability distributions used in finance.
- Simulate random walks and geometric Brownian motion.
- Price simple European options using Monte Carlo.
- Diagnose time-series properties such as autocorrelation and stationarity.

Free resources:

- MIT OCW 18.05 Introduction to Probability and Statistics: `https://ocw.mit.edu/courses/18-05-introduction-to-probability-and-statistics-spring-2014/`
- QuantEcon Python lectures: `https://python.quantecon.org/`
- statsmodels time-series documentation: `https://www.statsmodels.org/stable/tsa.html`
- NumPy random sampling documentation: `https://numpy.org/doc/stable/reference/random/index.html`

Projects:

1. Implement distribution samplers and verify sample moments.
2. Simulate random walks and GBM paths; compare empirical terminal moments with theory.
3. Price European calls/puts by Monte Carlo and compare with Black-Scholes.
4. Run autocorrelation and stationarity diagnostics on simulated series.

Deliverable:

- `notebooks/quant_lab/math_foundations.ipynb`

## Module 2 — Financial markets and instruments

Learning objectives:

- Understand equities, ETFs, indices, FX, futures, and options.
- Learn OHLCV data conventions, corporate actions, liquidity, and survivorship bias.
- Build clean data ingestion and validation functions.

Free resources:

- Investopedia market/instrument primers: `https://www.investopedia.com/markets-4689752`
- Yahoo Finance via yfinance: `https://github.com/ranaroussi/yfinance`
- Alpha Vantage documentation: `https://www.alphavantage.co/documentation/`
- pandas time-series documentation: `https://pandas.pydata.org/docs/user_guide/timeseries.html`

Projects:

1. Download daily OHLCV data for SPY, QQQ, GLD, TLT, and major FX proxies.
2. Clean missing values, splits/dividends, and non-trading days.
3. Compute liquidity and volatility summaries.
4. Build a data-quality report that flags stale, missing, or suspicious data.

## Module 3 — Derivatives pricing and risk management

Learning objectives:

- Price European options with Black-Scholes and binomial trees.
- Compute Greeks and understand sensitivity/risk.
- Estimate VaR/CVaR with historical and parametric methods.

Free resources:

- MIT OCW 15.401 Finance Theory I: `https://ocw.mit.edu/courses/15-401-finance-theory-i-fall-2008/`
- MIT OCW 15.450 Analytics of Finance: `https://ocw.mit.edu/courses/15-450-analytics-of-finance-fall-2010/`
- Option pricing reference by Macroption: `https://www.macroption.com/black-scholes-formula/`
- scipy statistics documentation: `https://docs.scipy.org/doc/scipy/reference/stats.html`

Projects:

1. Implement Black-Scholes call/put pricing and Greeks.
2. Implement a Cox-Ross-Rubinstein binomial tree.
3. Estimate portfolio VaR/CVaR using historical returns and normal assumptions.
4. Stress test a multi-asset portfolio under rate/equity/volatility shocks.

## Module 4 — Classical quantitative trading strategies

Learning objectives:

- Build mean-reversion, pairs, momentum, and factor strategies.
- Backtest with transaction costs, slippage, and realistic position sizing.
- Compare Sharpe, Sortino, drawdown, turnover, and hit-rate.

Free resources:

- QuantStart strategy articles: `https://www.quantstart.com/`
- Backtrader documentation: `https://www.backtrader.com/docu/`
- statsmodels cointegration documentation: `https://www.statsmodels.org/stable/generated/statsmodels.tsa.stattools.coint.html`
- Fama-French data library: `https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html`

Projects:

1. Test Engle-Granger cointegration across at least five ETF/equity pairs.
2. Backtest z-score spread entries/exits with costs.
3. Build time-series and cross-sectional momentum strategies.
4. Build simple factor long-short portfolios and rolling-window analytics.

## Module 5 — Machine learning for trading

Learning objectives:

- Engineer point-in-time features.
- Build supervised models for return prediction and regime classification.
- Validate with rolling/expanding windows and no lookahead.
- Evaluate both predictive metrics and trading metrics.

Free resources:

- Stanford CS229 Machine Learning: `https://cs229.stanford.edu/`
- scikit-learn user guide: `https://scikit-learn.org/stable/user_guide.html`
- mlfinlab concepts/documentation: `https://hudsonthames.org/mlfinlab/`
- XGBoost documentation: `https://xgboost.readthedocs.io/`

Projects:

1. Build momentum, volatility, volume, RSI, MACD, and Bollinger features.
2. Train logistic/linear baselines on next-period return labels.
3. Compare Random Forests, Gradient Boosting, and a small neural net.
4. Convert predictions into trading rules and compare equity curves/drawdowns.

## Module 6 — Deep reinforcement learning for trading

Learning objectives:

- Define trading environments with state, actions, rewards, and costs.
- Train simple tabular Q-learning before deep RL.
- Compare DQN/PPO/A2C-style agents against classical baselines.
- Understand instability, overfitting, and reward hacking.

Free resources:

- Sutton and Barto RL book: `http://incompleteideas.net/book/the-book-2nd.html`
- David Silver RL course: `https://www.davidsilver.uk/teaching/`
- OpenAI Spinning Up: `https://spinningup.openai.com/`
- Gymnasium documentation: `https://gymnasium.farama.org/`

Projects:

1. Build a single-asset buy/sell/hold environment with costs.
2. Train tabular Q-learning on discretized indicators.
3. Train a DQN or PPO-style policy on daily data.
4. Compare agents against momentum, mean reversion, and buy-and-hold.

## Module 7 — NLP, sentiment, and alternative data

Learning objectives:

- Ingest and clean financial text.
- Score sentiment using open pretrained models.
- Aggregate sentiment into ticker/time signals.
- Combine text and price signals without leakage.

Free resources:

- Hugging Face Transformers documentation: `https://huggingface.co/docs/transformers/index`
- FinBERT model family on Hugging Face: `https://huggingface.co/ProsusAI/finbert`
- SEC EDGAR data: `https://www.sec.gov/edgar`
- GDELT Project: `https://www.gdeltproject.org/`

Projects:

1. Score sample headlines with FinBERT.
2. Aggregate sentiment per ticker/day.
3. Test event-window returns after sentiment shocks.
4. Combine sentiment with momentum/volatility features in a walk-forward model.

## Module 8 — Backtesting, risk, and deployment

Learning objectives:

- Build or integrate a robust portfolio backtester.
- Model costs, slippage, position limits, and risk budgets.
- Compare all strategies under a common framework.
- Prepare paper-trading infrastructure, logs, dashboards, and kill switches.

Free resources:

- Backtrader documentation: `https://www.backtrader.com/docu/`
- vectorbt open-source docs: `https://vectorbt.dev/`
- pandas performance/visualization docs: `https://pandas.pydata.org/docs/`
- Open-source broker/paper APIs such as Alpaca docs: `https://alpaca.markets/docs/`

Projects:

1. Build a reusable strategy interface with `generate_signals`, `size_positions`, and `execute`.
2. Add cost/slippage models and portfolio risk constraints.
3. Run unified comparisons across classical, ML, RL, and sentiment strategies.
4. Build a paper-trading dashboard with P&L, drawdowns, exposure, and model health.

## Operating rules for every phase

- Use point-in-time features only.
- Include explicit transaction costs and slippage.
- Separate data, features, models, evaluation, execution, and risk.
- Validate with chronological splits or walk-forward tests.
- Prefer simple baselines before complex models.
- Treat weak evidence as no edge.
- Write tests or notebook sanity checks for every primitive.
