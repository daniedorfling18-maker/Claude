from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

_SCRIPT = Path(__file__).parents[1] / "scripts" / "validate_correct_score_blend.py"
_spec = importlib.util.spec_from_file_location("validate_correct_score_blend", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

js_divergence = _mod.js_divergence
ev_under_distribution = _mod.ev_under_distribution
parse_score = _mod.parse_score
mkey = _mod.mkey
slug = _mod.slug


def test_js_divergence_identical_is_zero():
    p = np.array([0.2, 0.3, 0.5])
    assert js_divergence(p, p) == pytest.approx(0.0, abs=1e-9)


def test_js_divergence_disjoint_is_one_bit():
    p = np.array([1.0, 0.0])
    q = np.array([0.0, 1.0])
    # JS divergence in bits between disjoint distributions is exactly 1.
    assert js_divergence(p, q) == pytest.approx(1.0, abs=1e-9)


def test_js_divergence_pads_unequal_sizes():
    p = np.array([0.5, 0.5])
    q = np.array([0.25, 0.25, 0.25, 0.25])
    val = js_divergence(p, q)
    assert 0.0 < val < 1.0


def test_ev_under_distribution_exact_match_dominates():
    # All mass on 1-0; picking 1-0 yields 3.0 expected points.
    matrix = np.zeros((3, 3))
    matrix[1, 0] = 1.0
    assert ev_under_distribution(matrix, (1, 0), ci_cutoff=1.5) == pytest.approx(3.0)


def test_ev_under_distribution_wrong_outcome_zero():
    matrix = np.zeros((3, 3))
    matrix[0, 1] = 1.0  # away win
    # Picking a home win 1-0 against a certain away win scores 0.
    assert ev_under_distribution(matrix, (1, 0), ci_cutoff=1.5) == pytest.approx(0.0)


def test_ev_under_distribution_close_score_partial():
    # All mass on 2-0; pick 1-0 → same outcome, within 1 goal → close (1.5 pts).
    matrix = np.zeros((4, 4))
    matrix[2, 0] = 1.0
    ev = ev_under_distribution(matrix, (1, 0), ci_cutoff=1.5)
    assert ev == pytest.approx(1.5)


def test_parse_score():
    assert parse_score("2-1") == (2, 1)
    assert parse_score("0 - 0") == (0, 0)
    assert parse_score("") is None
    assert parse_score("abc") is None


def test_mkey_aliases():
    # Ivory Coast and Côte d'Ivoire should canonicalise to the same key.
    assert mkey("Ivory Coast", "X")[0] == mkey("Cote d'Ivoire", "X")[0]
    assert mkey("United States", "X")[0] == "usa"
    assert mkey("South Korea", "X")[0] == "korea-republic"


def test_slug():
    assert slug("Bosnia & Herzegovina") == "bosnia-and-herzegovina"
    assert slug("DR Congo") == "dr-congo"
