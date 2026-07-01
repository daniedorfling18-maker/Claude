from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ClassificationReport:
    observations: int
    accuracy: float
    precision: float
    recall: float
    brier: float
    positive_rate: float

    def to_dict(self) -> dict[str, float | int]:
        return asdict(self)


@dataclass(frozen=True)
class LogisticModel:
    weights: np.ndarray
    mean: np.ndarray
    std: np.ndarray
    feature_names: list[str]

    def predict_proba(self, frame: pd.DataFrame) -> pd.Series:
        matrix = _prepare_matrix(frame[self.feature_names], self.mean, self.std)
        probability = _sigmoid(matrix @ self.weights)
        return pd.Series(probability, index=frame.index)


def chronological_split(frame: pd.DataFrame, *, train_fraction: float = 0.7) -> tuple[pd.DataFrame, pd.DataFrame]:
    if frame.empty:
        return frame.copy(), frame.copy()
    train_fraction = max(0.05, min(0.95, train_fraction))
    ordered = frame.sort_index()
    split = max(1, min(len(ordered) - 1, int(len(ordered) * train_fraction))) if len(ordered) > 1 else len(ordered)
    return ordered.iloc[:split].copy(), ordered.iloc[split:].copy()


def walk_forward_splits(
    frame: pd.DataFrame,
    *,
    train_size: int,
    test_size: int,
    step_size: int | None = None,
) -> list[tuple[pd.DataFrame, pd.DataFrame]]:
    ordered = frame.sort_index()
    step = step_size or test_size
    splits: list[tuple[pd.DataFrame, pd.DataFrame]] = []
    start = 0
    while start + train_size + test_size <= len(ordered):
        train = ordered.iloc[start : start + train_size]
        test = ordered.iloc[start + train_size : start + train_size + test_size]
        splits.append((train.copy(), test.copy()))
        start += step
    return splits


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(values, -40, 40)))


def _prepare_matrix(frame: pd.DataFrame, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    values = frame.astype(float).to_numpy()
    standardized = (values - mean) / std
    return np.column_stack([np.ones(len(standardized)), standardized])


def fit_logistic_classifier(
    features: pd.DataFrame,
    target: pd.Series,
    *,
    l2: float = 1.0,
    learning_rate: float = 0.1,
    iterations: int = 1_000,
) -> LogisticModel:
    """Small deterministic logistic classifier for research baselines.

    This is intentionally simple: use it as a baseline before reaching for
    heavier ML frameworks.
    """
    aligned = pd.concat([features, target.rename("_target")], axis=1).dropna()
    if aligned.empty:
        raise ValueError("no aligned feature/target rows")
    y = aligned["_target"].astype(float).to_numpy()
    if len(set(y.tolist())) < 2:
        raise ValueError("target must contain both classes")
    x = aligned.drop(columns=["_target"]).astype(float)
    mean = x.mean(axis=0).to_numpy()
    std = x.std(axis=0, ddof=0).replace(0, 1.0).to_numpy()
    matrix = _prepare_matrix(x, mean, std)
    weights = np.zeros(matrix.shape[1])
    reg = np.r_[0.0, np.ones(matrix.shape[1] - 1)] * l2
    for _ in range(iterations):
        prob = _sigmoid(matrix @ weights)
        gradient = matrix.T @ (prob - y) / len(y) + reg * weights / len(y)
        weights -= learning_rate * gradient
    return LogisticModel(weights=weights, mean=mean, std=std, feature_names=list(x.columns))


def classification_report(target: pd.Series, probabilities: pd.Series, *, threshold: float = 0.5) -> ClassificationReport:
    aligned = pd.concat([target.rename("target"), probabilities.rename("prob")], axis=1).dropna()
    if aligned.empty:
        return ClassificationReport(0, 0.0, 0.0, 0.0, 0.0, 0.0)
    y = aligned["target"].astype(int)
    pred = (aligned["prob"] >= threshold).astype(int)
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    accuracy = float((pred == y).mean())
    precision = float(tp / (tp + fp)) if tp + fp else 0.0
    recall = float(tp / (tp + fn)) if tp + fn else 0.0
    brier = float(((aligned["prob"] - y) ** 2).mean())
    return ClassificationReport(
        observations=int(len(aligned)),
        accuracy=accuracy,
        precision=precision,
        recall=recall,
        brier=brier,
        positive_rate=float(y.mean()),
    )


def threshold_by_expected_value(probabilities: pd.Series, *, avg_win: float, avg_loss: float, min_ev: float = 0.0) -> pd.Series:
    """Return a boolean mask where probability-implied expected value clears the hurdle."""
    expected_value = probabilities * avg_win + (1.0 - probabilities) * avg_loss
    return expected_value >= min_ev
