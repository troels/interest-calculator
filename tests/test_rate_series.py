"""Tests for the rate_series store and month lookups."""

from __future__ import annotations

from datetime import date

from loan.data.db import CurveDB


def test_put_get_rate_series(tmp_path):
    db = CurveDB(tmp_path / "t.db")
    pts = [("2026-03", 3.16), ("2026-04", 3.17), ("2026-05", 3.00)]
    n = db.put_rate_series("rk_fixed_yield", "dst:MPK3:5500701001", pts)
    assert n == 3
    assert db.get_rate_series("rk_fixed_yield") == pts


def test_rate_on_exact_and_prior_month(tmp_path):
    db = CurveDB(tmp_path / "t.db")
    db.put_rate_series("rk_fixed_yield", "src", [("2026-03", 3.16), ("2026-05", 3.00)])
    # exact month
    assert db.rate_on("rk_fixed_yield", date(2026, 3, 31)) == 3.16
    # April has no point -> falls back to latest prior (March)
    assert db.rate_on("rk_fixed_yield", date(2026, 4, 10)) == 3.16
    # May exact
    assert db.rate_on("rk_fixed_yield", date(2026, 5, 15)) == 3.00


def test_rate_on_before_history_is_none(tmp_path):
    db = CurveDB(tmp_path / "t.db")
    db.put_rate_series("rk_fixed_yield", "src", [("2026-03", 3.16)])
    assert db.rate_on("rk_fixed_yield", date(2020, 1, 1)) is None


def test_put_is_idempotent(tmp_path):
    db = CurveDB(tmp_path / "t.db")
    db.put_rate_series("s", "src", [("2026-03", 3.16)])
    db.put_rate_series("s", "src", [("2026-03", 9.99)])  # overwrite
    assert db.get_rate_series("s") == [("2026-03", 9.99)]
