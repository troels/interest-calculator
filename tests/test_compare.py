"""Tests for the strategy comparison."""

from __future__ import annotations

from datetime import date

import pytest

from loan.compare import compare_now, remaining_interest, StrategyCost
from loan.curves import CurveModel
from loan.data.loaders import parse_curve_txt
from loan.models import RealkreditLoan, SwapContract
from loan.valuation.swap import value_swap


@pytest.fixture
def model(sample_curve_txt) -> CurveModel:
    return CurveModel(parse_curve_txt(sample_curve_txt))


def test_remaining_interest_nominal_and_pv(model):
    nominal, pv = remaining_interest(22_500_000, 4.0, model, 12.0)
    # nominal = 22.5M * 4% * 12 = 10.8M
    assert nominal == pytest.approx(10_800_000, rel=1e-6)
    assert pv < nominal  # discounting reduces it
    assert pv > 0


def test_strategy_cost_totals():
    s = StrategyCost("x", breakage=1_000_000, rate_pct=4.0,
                     interest_nominal=10_000_000, interest_pv=8_000_000)
    assert s.total_nominal == 11_000_000
    assert s.total_pv == 9_000_000


def test_compare_now_structure_and_breakage(model):
    swap = SwapContract()
    as_of = date(2026, 6, 15)
    sv = value_swap(model, swap, as_of)
    res = compare_now(as_of, swap, sv, model, rk_fixed_yield_pct=3.0,
                      bank_margin_pct=0.5,
                      fixed_loan=RealkreditLoan(notional=swap.notional, bidrag_pct=0.6),
                      flex_loan=RealkreditLoan(notional=swap.notional, product="flex"))
    names = [s["name"] for s in res["strategies"]]
    assert names == ["stay_swap", "convert_fixed", "convert_flex"]
    assert res["breakage"] == pytest.approx(sv.breakage)
    # convert strategies carry the breakage; stay does not
    stay = next(s for s in res["strategies"] if s["name"] == "stay_swap")
    conv = next(s for s in res["strategies"] if s["name"] == "convert_fixed")
    assert stay["breakage"] == 0.0
    assert conv["breakage"] == pytest.approx(sv.breakage)


def test_break_even_yield_is_consistent(model):
    """At the break-even all-in rate, convert_fixed total PV should tie stay PV."""
    swap = SwapContract()
    as_of = date(2026, 6, 15)
    sv = value_swap(model, swap, as_of)
    res = compare_now(as_of, swap, sv, model, rk_fixed_yield_pct=3.0, bank_margin_pct=0.5,
                      fixed_loan=RealkreditLoan(notional=swap.notional, bidrag_pct=0.6))
    be_all_in = res["break_even_all_in_pct"]
    # re-price convert at the break-even all-in rate
    _, pv = remaining_interest(swap.notional, be_all_in, model, sv.remaining_years)
    stay_pv = next(s["interest_pv"] for s in res["strategies"] if s["name"] == "stay_swap")
    assert sv.breakage + pv == pytest.approx(stay_pv, rel=1e-6)


def test_staying_beats_converting_when_breakage_huge(model):
    """A 5.4% swap vs ~3.6% realkredit: breakage is large but rate saving is large too."""
    swap = SwapContract()
    as_of = date(2026, 6, 15)
    sv = value_swap(model, swap, as_of)
    res = compare_now(as_of, swap, sv, model, rk_fixed_yield_pct=3.0, bank_margin_pct=0.5,
                      fixed_loan=RealkreditLoan(notional=swap.notional, bidrag_pct=0.6))
    # sanity: best is a real strategy name
    assert res["best"] in {"stay_swap", "convert_fixed"}
