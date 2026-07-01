from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd

POSITIVE_WORDS = {
    "beat",
    "beats",
    "bullish",
    "gain",
    "gains",
    "growth",
    "improve",
    "improves",
    "profit",
    "rally",
    "record",
    "surge",
    "upgraded",
    "win",
}

NEGATIVE_WORDS = {
    "bearish",
    "cut",
    "decline",
    "downgraded",
    "drop",
    "falls",
    "fraud",
    "loss",
    "miss",
    "misses",
    "risk",
    "slump",
    "warning",
    "weak",
}


@dataclass(frozen=True)
class SentimentScore:
    text: str
    score: float
    positive_hits: int
    negative_hits: int


def lexicon_sentiment(text: str) -> SentimentScore:
    words = [chunk.strip(".,:;!?()[]{}\"'").lower() for chunk in text.split()]
    positive = sum(1 for word in words if word in POSITIVE_WORDS)
    negative = sum(1 for word in words if word in NEGATIVE_WORDS)
    total = positive + negative
    score = (positive - negative) / total if total else 0.0
    return SentimentScore(text=text, score=float(score), positive_hits=positive, negative_hits=negative)


def score_headlines(rows: Iterable[dict[str, object]]) -> pd.DataFrame:
    out: list[dict[str, object]] = []
    for row in rows:
        text = str(row.get("headline") or row.get("text") or "")
        scored = lexicon_sentiment(text)
        out.append({**row, "sentiment_score": scored.score, "positive_hits": scored.positive_hits, "negative_hits": scored.negative_hits})
    return pd.DataFrame(out)


def aggregate_sentiment(frame: pd.DataFrame, *, ticker_column: str = "ticker", time_column: str = "timestamp") -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=[ticker_column, "date", "sentiment_mean", "sentiment_count"])
    data = frame.copy()
    data["date"] = pd.to_datetime(data[time_column]).dt.date
    return (
        data.groupby([ticker_column, "date"])["sentiment_score"]
        .agg(sentiment_mean="mean", sentiment_count="count")
        .reset_index()
    )
