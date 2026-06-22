import pytest

from superbru_score_engine.betting.edge_learning import (
    Calibration,
    brier,
    fit_calibration,
    logit,
    reliability_table,
    sigmoid,
)


def test_sigmoid_logit_roundtrip():
    for p in (0.1, 0.37, 0.5, 0.82, 0.99):
        assert sigmoid(logit(p)) == pytest.approx(p, abs=1e-6)


def test_identity_calibration_leaves_probs_unchanged():
    cal = Calibration()
    for p in (0.05, 0.4, 0.95):
        assert cal.apply(p) == pytest.approx(p, abs=1e-9)


def test_small_sample_stays_identity():
    probs = [0.8, 0.2, 0.6]
    outs = [1.0, 0.0, 1.0]
    cal = fit_calibration(probs, outs)
    assert cal.a == 1.0 and cal.b == 0.0  # below MIN_FIT_SAMPLES -> identity


def test_fit_improves_brier_on_miscalibrated_data():
    # Predicted 0.8 everywhere, but the outcome only happens half the time.
    probs = [0.8] * 20
    outs = ([1.0, 0.0] * 10)
    cal = fit_calibration(probs, outs, l2=0.0)
    assert cal.brier_after <= cal.brier_before
    assert cal.apply(0.8) < 0.8  # pulled toward the observed ~0.5


def test_well_calibrated_data_stays_near_identity():
    probs = [0.5] * 20
    outs = ([1.0, 0.0] * 10)  # exactly 50%
    cal = fit_calibration(probs, outs, l2=0.0)
    assert cal.apply(0.5) == pytest.approx(0.5, abs=0.05)


def test_calibration_json_roundtrip():
    cal = Calibration(a=1.2, b=-0.3, n=42, brier_before=0.3, brier_after=0.28)
    back = Calibration.from_json(cal.to_json())
    assert back.a == pytest.approx(1.2) and back.b == pytest.approx(-0.3) and back.n == 42


def test_reliability_table():
    probs = [0.1, 0.15, 0.85, 0.9]
    outs = [0.0, 0.0, 1.0, 1.0]
    rows = reliability_table(probs, outs)
    assert rows[0]["band"] == "[0.0,0.2)" and rows[0]["n"] == 2
    assert rows[-1]["observed_freq"] == pytest.approx(1.0)


def test_brier_basic():
    assert brier([1.0, 0.0], [1.0, 0.0]) == pytest.approx(0.0)
    assert brier([0.5, 0.5], [1.0, 0.0]) == pytest.approx(0.25)
