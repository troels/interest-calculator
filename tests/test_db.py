"""Tests for the SQLite curve store."""

from __future__ import annotations

from datetime import date

from loan.data.db import CurveDB
from loan.models import CurvePoint


def make_db(tmp_path) -> CurveDB:
    return CurveDB(tmp_path / "test.db")


def test_record_ok_and_read_back(tmp_path):
    db = make_db(tmp_path)
    d = date(2026, 6, 15)
    pts = [CurvePoint(2, 2.8326), CurvePoint(10, 3.1774)]
    db.record(d, "ok", points=pts, raw_json="{}", fetched_at="2026-06-18T00:00:00")
    assert db.attempted(d)
    curve = db.get_curve(d)
    assert curve is not None
    assert curve.tenors == [2, 10]
    assert curve.rates == [2.8326, 3.1774]


def test_empty_day_is_attempted_but_has_no_curve(tmp_path):
    db = make_db(tmp_path)
    d = date(2019, 1, 1)
    db.record(d, "empty", raw_json='{"data":[]}')
    assert db.attempted(d) is True
    assert db.get_curve(d) is None  # no points -> not a usable curve


def test_record_is_idempotent(tmp_path):
    db = make_db(tmp_path)
    d = date(2026, 6, 15)
    db.record(d, "ok", points=[CurvePoint(2, 2.8)])
    db.record(d, "ok", points=[CurvePoint(2, 2.9)])  # re-fetch overwrites
    assert db.get_curve(d).rates == [2.9]


def test_stats_and_curve_dates(tmp_path):
    db = make_db(tmp_path)
    db.record(date(2026, 6, 15), "ok", points=[CurvePoint(2, 2.8)])
    db.record(date(2026, 6, 16), "ok", points=[CurvePoint(2, 2.9)])
    db.record(date(2026, 6, 13), "empty")
    s = db.stats()
    assert s["ok"] == 2 and s["empty"] == 1
    assert s["first_curve"] == "2026-06-15"
    assert db.curve_dates() == [date(2026, 6, 15), date(2026, 6, 16)]
