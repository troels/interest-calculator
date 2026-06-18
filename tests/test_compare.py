"""Tests for the strategy comparison (multi-tenor fixed, amortization, discounting)."""

from __future__ import annotations

from datetime import date

import pytest

from loan.compare import (compare_now, loan_cost, fixed_rate_at_tenor,
                          discount_factor, StrategyCost)
from loan.curves import CurveModel
from loan.data.loaders import parse_curve_txt
from loan.models import SwapContract
from loan.valuation.swap import value_swap


@pytest.fixture
def model(sample_curve_txt) -> CurveModel:
    return CurveModel(parse_curve_txt(sample_curve_txt))


def test_loan_cost_bullet_interest_only(model):
    i_pv, p_pv, r_pv, i_nom = loan_cost(22_500_000, 4.0, model, 12.0)
    assert p_pv == 0.0                       # no amortization
    assert i_nom == pytest.approx(10_800_000, rel=1e-6)  # 22.5M*4%*12
    assert r_pv > 0                          # full notional still owed at horizon
    assert i_pv < i_nom                       # discounting


def test_loan_cost_amortizing_pays_less_interest(model):
    bullet = loan_cost(22_500_000, 4.0, model, 12.0, amort_years=None)
    amort = loan_cost(22_500_000, 4.0, model, 12.0, amort_years=30.0)
    # amortizing -> declining balance -> less interest, but principal repaid + lower residual
    assert amort[0] < bullet[0]              # interest_pv lower
    assert amort[1] > 0                       # principal_pv positive
    assert amort[2] < bullet[2]              # residual_pv lower (paid some down)


def test_flat_discount_factor():
    assert discount_factor(None, 0, 5.0) == 1.0
    assert discount_factor(None, 1, 5.0) == pytest.approx(1 / 1.05)
    assert discount_factor(None, 2, 10.0) == pytest.approx(1 / 1.10 ** 2)


def test_fixed_rate_term_structure(model):
    """Shorter fixed loans are cheaper on an upward swap curve."""
    anchor = 5.0
    r10 = fixed_rate_at_tenor(anchor, 30, 10, model)
    r20 = fixed_rate_at_tenor(anchor, 30, 20, model)
    r30 = fixed_rate_at_tenor(anchor, 30, 30, model)
    assert r30 == pytest.approx(anchor)      # anchored at 30Y
    assert r10 < r20 < r30                    # upward curve -> shorter cheaper


def test_strategy_cost_total():
    s = StrategyCost("x", breakage=1e6, rate_pct=4.0, interest_pv=8e6,
                     principal_pv=5e6, residual_pv=10e6, interest_nominal=12e6)
    assert s.total_pv == pytest.approx(1e6 + 8e6 + 5e6 + 10e6)


def test_compare_now_has_three_fixed_tenors_and_f5(model):
    swap = SwapContract()
    as_of = date(2026, 6, 15)
    sv = value_swap(model, swap, as_of)
    res = compare_now(as_of, swap, sv, model, 5.1, bank_margin_pct=0.5, flex_rate_pct=3.8)
    names = [s["name"] for s in res["strategies"]]
    assert names == ["stay_swap", "fixed_10y", "fixed_20y", "fixed_30y", "F5"]
    # convert strategies carry the breakage; stay does not
    by = {s["name"]: s for s in res["strategies"]}
    assert by["stay_swap"]["breakage"] == 0.0
    assert by["fixed_30y"]["breakage"] == pytest.approx(sv.breakage)


def test_amortize_changes_costs(model):
    swap = SwapContract()
    as_of = date(2026, 6, 15)
    sv = value_swap(model, swap, as_of)
    io = compare_now(as_of, swap, sv, model, 5.1, amortize=False)
    am = compare_now(as_of, swap, sv, model, 5.1, amortize=True)
    io_30 = next(s for s in io["strategies"] if s["name"] == "fixed_30y")
    am_30 = next(s for s in am["strategies"] if s["name"] == "fixed_30y")
    assert am_30["interest_pv"] < io_30["interest_pv"]   # amortizing pays less interest
    assert am_30["principal_pv"] > 0


def test_break_even_consistency(model):
    """At the break-even rate, a bullet fixed convert ties staying."""
    swap = SwapContract()
    as_of = date(2026, 6, 15)
    sv = value_swap(model, swap, as_of)
    res = compare_now(as_of, swap, sv, model, 5.1)
    be = res["break_even_rate_pct"]
    i_pv, _, r_pv, _ = loan_cost(swap.notional, be, model, sv.remaining_years)
    stay = next(s for s in res["strategies"] if s["name"] == "stay_swap")
    # bullet convert at break-even: breakage + interest + residual == stay total
    assert sv.breakage + i_pv + r_pv == pytest.approx(stay["total_pv"], rel=1e-6)


def test_discount_rate_lowers_future_cost(model):
    swap = SwapContract()
    as_of = date(2026, 6, 15)
    sv = value_swap(model, swap, as_of)
    curve_based = compare_now(as_of, swap, sv, model, 5.1)
    high_disc = compare_now(as_of, swap, sv, model, 5.1, discount_rate_pct=8.0)
    stay_curve = next(s for s in curve_based["strategies"] if s["name"] == "stay_swap")
    stay_disc = next(s for s in high_disc["strategies"] if s["name"] == "stay_swap")
    assert stay_disc["total_pv"] < stay_curve["total_pv"]  # heavier discounting -> lower PV
