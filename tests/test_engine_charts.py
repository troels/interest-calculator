"""Tests for engine data-access helpers and chart smoke output."""

from __future__ import annotations

from datetime import date

import pytest

from loan.curves import CurveModel
from loan.data.db import CurveDB
from loan.data.loaders import parse_curve_txt
from loan.engine import curve_model_on, rk_fixed_yield_on


def _seed_curve(db, d, txt):
    curve = parse_curve_txt(txt)
    db.record(d, "ok", points=list(curve.points))


def test_curve_model_on_exact(tmp_path, sample_curve_txt):
    db = CurveDB(tmp_path / "t.db")
    _seed_curve(db, date(2026, 6, 15), sample_curve_txt)
    got = curve_model_on(db, date(2026, 6, 15))
    assert got is not None
    model, cdate = got
    assert isinstance(model, CurveModel)
    assert cdate == date(2026, 6, 15)


def test_curve_model_on_falls_back_to_nearest_prior(tmp_path, sample_curve_txt):
    db = CurveDB(tmp_path / "t.db")
    _seed_curve(db, date(2026, 6, 15), sample_curve_txt)
    # ask for a few days later -> nearest prior curve returned
    model, cdate = curve_model_on(db, date(2026, 6, 18))
    assert cdate == date(2026, 6, 15)


def test_curve_model_on_too_far_returns_none(tmp_path, sample_curve_txt):
    db = CurveDB(tmp_path / "t.db")
    _seed_curve(db, date(2026, 6, 15), sample_curve_txt)
    assert curve_model_on(db, date(2026, 8, 1)) is None  # beyond lookback


def test_rk_fixed_yield_on(tmp_path):
    db = CurveDB(tmp_path / "t.db")
    db.put_rate_series("rk_fixed_yield", "src", [("2026-05", 3.0)])
    assert rk_fixed_yield_on(db, date(2026, 5, 20)) == 3.0
    assert rk_fixed_yield_on(db, date(2026, 6, 1)) == 3.0  # carries forward


def test_plot_strategy_costs_writes_png(tmp_path):
    from loan.charts import plot_strategy_costs
    result = {
        "as_of": "2026-06-15", "breakage": 4_746_568, "break_even_yield_pct": 3.17,
        "strategies": [
            {"name": "stay_swap", "breakage": 0, "interest_pv": 13_127_432, "total_pv": 13_127_432},
            {"name": "convert_fixed", "breakage": 4_746_568, "interest_pv": 8_009_959, "total_pv": 12_756_527},
        ],
    }
    out = plot_strategy_costs(result, tmp_path / "c.png")
    assert out.exists() and out.stat().st_size > 1000
