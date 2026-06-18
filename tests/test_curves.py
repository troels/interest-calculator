"""Tests for curve interpolation, extrapolation, and bootstrapping."""

from __future__ import annotations

import pytest

from loan.curves import CurveModel
from loan.data.loaders import parse_curve_txt


@pytest.fixture
def model(sample_curve_txt) -> CurveModel:
    return CurveModel(parse_curve_txt(sample_curve_txt))


def test_par_rate_hits_pillars(model):
    # interpolation must reproduce the input pillars exactly
    assert model.par_rate_pct(2) == pytest.approx(2.8326)
    assert model.par_rate_pct(5) == pytest.approx(2.9609)
    assert model.par_rate_pct(10) == pytest.approx(3.1774)


def test_par_rate_interpolates_between_pillars(model):
    # 2.5Y lies between 2Y (2.8326) and 3Y (2.8650)
    mid = model.par_rate_pct(2.5)
    assert 2.8326 < mid < 2.8650
    assert mid == pytest.approx((2.8326 + 2.8650) / 2, abs=1e-6)


def test_bootstrap_roundtrips_par_rates(model):
    """DFs must re-price the input par rates back to themselves at each pillar."""
    for n in range(2, 13):
        assert model.implied_par_rate_pct(n) == pytest.approx(model.par_rate_pct(n), abs=1e-9)


def test_discount_factors_monotonic_and_le_one(model):
    dfs = [model.discount_factor(t) for t in range(1, 13)]
    assert all(0 < df <= 1 for df in dfs)
    assert all(dfs[i] > dfs[i + 1] for i in range(len(dfs) - 1))  # strictly decreasing


def test_fractional_df_between_neighbours(model):
    lo = model.discount_factor(5)
    hi = model.discount_factor(6)
    mid = model.discount_factor(5.5)
    assert hi < mid < lo


def test_tail_extrapolation_matches_reference_txt(model):
    """The 7-10Y tail fit should reproduce the txt's extrapolated 11Y/12Y (~0.0445/yr)."""
    # reference file values: 11Y = 3.2222, 12Y = 3.2667
    assert model.par_rate_pct(11) == pytest.approx(3.2222, abs=2e-3)
    assert model.par_rate_pct(12) == pytest.approx(3.2667, abs=3e-3)


def test_annuity_and_pv01_sane(model):
    # 12Y annuity ~ sum of ~12 discount factors a touch below their count
    ann = model.annuity(12)
    assert 9.0 < ann < 11.0
    assert model.pv01(12) == pytest.approx(ann * 1e-4)
