"""Tests for realkredit cost models."""

from __future__ import annotations

import pytest

from loan.curves import CurveModel
from loan.data.loaders import parse_curve_txt
from loan.models import RealkreditLoan
from loan.valuation import realkredit as rk


@pytest.fixture
def model(sample_curve_txt) -> CurveModel:
    return CurveModel(parse_curve_txt(sample_curve_txt))


def test_all_in_rate_adds_bidrag():
    assert rk.all_in_rate_pct(3.0, 0.6) == pytest.approx(3.6)


def test_annual_interest_bullet():
    # 22.5M at 3.6% all-in = 810,000 per year
    assert rk.annual_interest(22_500_000, 3.6) == pytest.approx(810_000)


def test_fixed_rate_from_dst_yield():
    loan = RealkreditLoan(bidrag_pct=0.6)
    # 2026-05 realkredit yield was 3.00% -> all-in 3.60%
    assert rk.fixed_rate_pct(loan, 3.00) == pytest.approx(3.60)


def test_flex_rate_uses_curve_short_end(model):
    loan = RealkreditLoan(product="flex", bidrag_pct=0.7, flex_tenor_years=5.0)
    # 5Y par rate on the sample curve is 2.9609 -> all-in 3.6609
    assert rk.flex_rate_pct(loan, model) == pytest.approx(2.9609 + 0.7, abs=1e-3)


def test_flex_shorter_tenor_is_cheaper_on_upward_curve(model):
    loan_f1 = RealkreditLoan(product="flex", flex_tenor_years=2.0)
    loan_f5 = RealkreditLoan(product="flex", flex_tenor_years=5.0)
    # upward-sloping curve: 2Y < 5Y, so F1 cheaper than F5
    assert rk.flex_rate_pct(loan_f1, model) < rk.flex_rate_pct(loan_f5, model)


def test_cost_monotonic_in_yield():
    loan = RealkreditLoan()
    lo = rk.annual_interest(loan.notional, rk.fixed_rate_pct(loan, 2.0))
    hi = rk.annual_interest(loan.notional, rk.fixed_rate_pct(loan, 5.0))
    assert hi > lo


def test_callable_buyback_zero_at_par():
    # market yield <= coupon -> bond at/above par -> no buy-back gain
    assert rk.callable_buyback_gain(22_500_000, 5.0, 4.0, 25) == 0.0


def test_callable_buyback_positive_when_rates_rise():
    # coupon 2%, yields rose to 5% -> bond below par -> positive debt reduction
    gain = rk.callable_buyback_gain(22_500_000, 2.0, 5.0, 25)
    assert 0 < gain <= 22_500_000 * 0.6
