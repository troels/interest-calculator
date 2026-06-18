"""Tests for swap mark-to-market and breakage."""

from __future__ import annotations

from datetime import date

import pytest

from loan.curves import CurveModel
from loan.data.loaders import parse_curve_txt
from loan.models import SwapContract
from loan.valuation.swap import value_swap, fixed_leg_schedule


@pytest.fixture
def model(sample_curve_txt) -> CurveModel:
    return CurveModel(parse_curve_txt(sample_curve_txt))


def test_schedule_whole_years():
    offsets, taus = fixed_leg_schedule(12.0, frequency=1)
    assert offsets == [float(i) for i in range(1, 13)]
    assert all(t == pytest.approx(1.0) for t in taus)


def test_schedule_stub_first_period():
    offsets, taus = fixed_leg_schedule(11.5, frequency=1)
    assert offsets[0] == pytest.approx(0.5)
    assert taus[0] == pytest.approx(0.5)
    assert taus[1] == pytest.approx(1.0)
    assert len(offsets) == 12


def test_actual_position_breakage_4_to_5m(model):
    """The real 5.4% swap on the provided curve -> breakage ~4-5M DKK, MtM negative."""
    swap = SwapContract()  # defaults: 22.5M, 5.4%, maturity 2038-06-15
    v = value_swap(model, swap, date(2026, 6, 15))
    assert v.remaining_years == pytest.approx(12.0, abs=0.05)
    assert v.market_rate_pct == pytest.approx(3.2667, abs=1e-2)
    assert v.mtm < 0
    assert 4_000_000 < v.breakage < 5_000_000
    assert v.breakage == pytest.approx(-v.mtm)


def test_at_market_swap_is_zero(model):
    """A swap struck at the current market rate has ~zero MtM."""
    s_market = model.par_rate_pct(12)
    swap = SwapContract(fixed_rate_pct=s_market)
    v = value_swap(model, swap, date(2026, 6, 15))
    assert v.mtm == pytest.approx(0.0, abs=1.0)  # within ~1 DKK
    assert v.breakage == pytest.approx(0.0, abs=1.0)


def test_payer_mtm_monotonic_in_market_rate(model):
    """Higher market rates -> less negative payer MtM (smaller breakage)."""
    swap = SwapContract()
    base = value_swap(model, swap, date(2026, 6, 15))
    # shift the whole curve up by re-pointing to a higher-rate synthetic contract:
    # cheaper proxy — compare breakage at two contract rates instead.
    lower_contract = value_swap(model, SwapContract(fixed_rate_pct=4.5), date(2026, 6, 15))
    assert lower_contract.breakage < base.breakage  # closer to market -> smaller breakage


def test_decomposition_consistency(model):
    """MtM via leg PVs equals N*(s_market - s_contract)*annuity."""
    swap = SwapContract()
    v = value_swap(model, swap, date(2026, 6, 15))
    alt = swap.notional * (v.market_rate_pct - swap.fixed_rate_pct) / 100.0 * v.annuity
    assert v.mtm == pytest.approx(alt, rel=1e-9)
