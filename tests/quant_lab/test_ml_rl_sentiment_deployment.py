from __future__ import annotations

import numpy as np
import pandas as pd

from quant_lab.deployment import promote_only_after_evidence
from quant_lab.ml import (
    chronological_split,
    classification_report,
    fit_logistic_classifier,
    threshold_by_expected_value,
    walk_forward_splits,
)
from quant_lab.rl import SimpleTradingEnv, train_tabular_q_learning
from quant_lab.sentiment import aggregate_sentiment, lexicon_sentiment, score_headlines


def test_chronological_and_walk_forward_splits_are_ordered():
    frame = pd.DataFrame({"x": range(20)}, index=pd.date_range("2026-01-01", periods=20))

    train, test = chronological_split(frame, train_fraction=0.6)
    splits = walk_forward_splits(frame, train_size=8, test_size=4, step_size=4)

    assert train.index.max() < test.index.min()
    assert len(splits) == 3
    assert all(left.index.max() < right.index.min() for left, right in splits)


def test_logistic_classifier_learns_simple_directional_boundary():
    x = np.linspace(-3, 3, 80)
    features = pd.DataFrame({"x": x, "x2": x * x * 0.01})
    target = pd.Series((x > 0).astype(int), index=features.index)

    train, test = chronological_split(pd.concat([features, target.rename("target")], axis=1), train_fraction=0.75)
    model = fit_logistic_classifier(train[["x", "x2"]], train["target"], iterations=1_500, learning_rate=0.2)
    probabilities = model.predict_proba(test[["x", "x2"]])
    report = classification_report(test["target"], probabilities, threshold=0.5)
    ev_mask = threshold_by_expected_value(probabilities, avg_win=0.06, avg_loss=-0.03, min_ev=0.0)

    assert report.accuracy >= 0.9
    assert probabilities.iloc[-1] > probabilities.iloc[0]
    assert ev_mask.any()


def test_simple_trading_env_and_q_learning_return_policy_table():
    prices = pd.Series(np.linspace(100, 110, 40) + np.sin(np.linspace(0, 8, 40)))

    env = SimpleTradingEnv(prices)
    state = env.reset()
    next_state, reward, done = env.step(2)
    q = train_tabular_q_learning(prices, episodes=5, seed=42)

    assert 0 <= state <= 8
    assert 0 <= next_state <= 8
    assert isinstance(reward, float)
    assert done is False
    assert q.shape == (9, 3)


def test_sentiment_scoring_and_aggregation():
    rows = [
        {"ticker": "ABC", "timestamp": "2026-01-01T10:00:00Z", "headline": "ABC beats estimates and shares surge"},
        {"ticker": "ABC", "timestamp": "2026-01-01T11:00:00Z", "headline": "ABC warns of weak growth risk"},
    ]

    scored = score_headlines(rows)
    aggregated = aggregate_sentiment(scored)

    assert lexicon_sentiment("bullish profit surge").score > 0
    assert lexicon_sentiment("bearish loss warning").score < 0
    assert len(aggregated) == 1
    assert aggregated["sentiment_count"].iloc[0] == 2


def test_promotion_rule_fails_closed_until_forward_evidence_is_positive():
    blocked = promote_only_after_evidence(validation_roi=0.04, validation_trades=40, validation_drawdown=-0.04, forward_paper_roi=-0.01)
    approved = promote_only_after_evidence(validation_roi=0.04, validation_trades=40, validation_drawdown=-0.04, forward_paper_roi=0.02)

    assert blocked.approved is False
    assert approved.approved is True
