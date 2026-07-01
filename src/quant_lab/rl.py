from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class SimpleTradingEnv:
    prices: pd.Series
    cost_bps: float = 0.0
    max_steps: int | None = None

    def __post_init__(self) -> None:
        self.prices = pd.to_numeric(self.prices, errors="coerce").dropna().reset_index(drop=True)
        if len(self.prices) < 3:
            raise ValueError("at least three prices are required")
        self.returns = self.prices.pct_change().fillna(0.0)
        self.max_steps = self.max_steps or len(self.prices) - 1
        self.reset()

    def reset(self) -> int:
        self.index = 1
        self.position = 0
        self.done = False
        return self.state()

    def state(self) -> int:
        recent_return = float(self.returns.iloc[self.index])
        trend_bucket = 2 if recent_return > 0.002 else 0 if recent_return < -0.002 else 1
        return trend_bucket * 3 + (self.position + 1)

    def step(self, action: int) -> tuple[int, float, bool]:
        """Actions: 0 short, 1 flat, 2 long."""
        if self.done:
            return self.state(), 0.0, True
        target_position = action - 1
        turnover = abs(target_position - self.position)
        self.position = target_position
        self.index += 1
        reward = self.position * float(self.returns.iloc[self.index]) - turnover * self.cost_bps / 10_000.0
        self.done = self.index >= min(len(self.prices) - 1, int(self.max_steps))
        return self.state(), float(reward), self.done


def train_tabular_q_learning(
    prices: pd.Series,
    *,
    episodes: int = 100,
    alpha: float = 0.1,
    gamma: float = 0.95,
    epsilon: float = 0.1,
    seed: int = 1,
) -> np.ndarray:
    """Tiny Q-learning baseline for curriculum experiments."""
    rng = np.random.default_rng(seed)
    q = np.zeros((9, 3))
    for _ in range(episodes):
        env = SimpleTradingEnv(prices)
        state = env.reset()
        done = False
        while not done:
            action = int(rng.integers(0, 3)) if rng.random() < epsilon else int(np.argmax(q[state]))
            next_state, reward, done = env.step(action)
            q[state, action] += alpha * (reward + gamma * np.max(q[next_state]) - q[state, action])
            state = next_state
    return q
