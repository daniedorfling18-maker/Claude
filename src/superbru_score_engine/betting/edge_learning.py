"""Learn calibration and edge over time from realised outcomes.

The model's probabilities are the de-vigged market consensus. This module learns, from
completed matches, a calibration map that corrects any systematic bias in those
probabilities (Platt scaling on the logit, shrunk toward identity so it is safe on small
samples). Feeding calibrated fairs to the trading engine is the prerequisite for any real
edge, and the fit sharpens as results accrue.

It also exposes reliability bins and a favourite-band edge table so you can see *where*
realised frequency departs from the model — the empirical hunt for edge.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass

try:
    from scipy.optimize import minimize
except ImportError:  # pragma: no cover - scipy is a project dependency
    minimize = None


def _clip(p: float, eps: float = 1e-6) -> float:
    return min(1.0 - eps, max(eps, float(p)))


def logit(p: float) -> float:
    p = _clip(p)
    return math.log(p / (1.0 - p))


def sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def brier(probs, outcomes) -> float | None:
    if not probs:
        return None
    return sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / len(probs)


def log_loss(probs, outcomes) -> float | None:
    if not probs:
        return None
    return -sum(o * math.log(_clip(p)) + (1 - o) * math.log(1 - _clip(p)) for p, o in zip(probs, outcomes)) / len(probs)


@dataclass(frozen=True)
class Calibration:
    """Platt scaling p_cal = sigmoid(a * logit(p) + b). a=1,b=0 is identity (no change)."""

    a: float = 1.0
    b: float = 0.0
    n: int = 0
    brier_before: float | None = None
    brier_after: float | None = None
    log_loss_before: float | None = None
    log_loss_after: float | None = None
    fitted_at_utc: str = ""

    def apply(self, p: float) -> float:
        return sigmoid(self.a * logit(p) + self.b)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, text: str) -> "Calibration":
        d = json.loads(text)
        fields = ("a", "b", "n", "brier_before", "brier_after", "log_loss_before", "log_loss_after", "fitted_at_utc")
        return cls(**{k: d[k] for k in fields if k in d})


# Minimum labelled outcomes before we trust a fitted curve; below this we stay identity.
MIN_FIT_SAMPLES = 8


def fit_calibration(probs, outcomes, *, l2: float = 1.0, fitted_at_utc: str = "") -> Calibration:
    """Fit Platt scaling by minimising log-loss, L2-shrunk toward identity (a=1, b=0).

    Returns an identity calibration (no change) when there is too little data or scipy is
    unavailable, so applying it can never make probabilities worse on a thin sample.
    """
    probs = [float(p) for p in probs]
    outcomes = [float(o) for o in outcomes]
    n = len(probs)
    bb, lb = brier(probs, outcomes), log_loss(probs, outcomes)
    if n < MIN_FIT_SAMPLES or minimize is None:
        return Calibration(1.0, 0.0, n, bb, bb, lb, lb, fitted_at_utc)

    z = [logit(p) for p in probs]

    def nll(params):
        a, b = params
        loss = 0.0
        for zi, oi in zip(z, outcomes):
            pi = _clip(sigmoid(a * zi + b))
            loss -= oi * math.log(pi) + (1 - oi) * math.log(1 - pi)
        return loss / n + (l2 / n) * ((a - 1.0) ** 2 + b ** 2)

    res = minimize(nll, x0=[1.0, 0.0], method="Nelder-Mead", options={"xatol": 1e-6, "fatol": 1e-8, "maxiter": 1000})
    a, b = float(res.x[0]), float(res.x[1])
    cal = Calibration(a, b, n)
    cp = [cal.apply(p) for p in probs]
    return Calibration(a, b, n, bb, brier(cp, outcomes), lb, log_loss(cp, outcomes), fitted_at_utc)


def reliability_table(probs, outcomes, bins=(0.0, 0.2, 0.4, 0.6, 0.8, 1.01)) -> list[dict]:
    rows = []
    for lo, hi in zip(bins, bins[1:]):
        idx = [i for i, p in enumerate(probs) if lo <= p < hi]
        if not idx:
            continue
        mean_p = sum(probs[i] for i in idx) / len(idx)
        obs = sum(outcomes[i] for i in idx) / len(idx)
        rows.append({
            "band": f"[{lo:.1f},{hi:.1f})", "n": len(idx),
            "mean_predicted": round(mean_p, 4), "observed_freq": round(obs, 4),
            "gap_observed_minus_predicted": round(obs - mean_p, 4),
        })
    return rows
