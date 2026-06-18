"""Tests for the local curve-file parser."""

from __future__ import annotations

from datetime import date

import pytest

from loan.data.loaders import parse_curve_txt, parse_dmy
from loan.models import Curve


def test_parse_dmy():
    assert parse_dmy("15 JUN 2026") == date(2026, 6, 15)
    assert parse_dmy("Curve date: 1 JAN 2020") == date(2020, 1, 1)


def test_parse_dmy_bad_month():
    with pytest.raises(ValueError):
        parse_dmy("15 XXX 2026")


def test_parse_sample_curve_date_and_count(sample_curve_txt):
    curve = parse_curve_txt(sample_curve_txt)
    assert isinstance(curve, Curve)
    assert curve.curve_date == date(2026, 6, 15)
    assert curve.source == "dfbf"
    # 2Y..10Y (9) + extrapolated 11Y,12Y (2) = 11 points
    assert len(curve.points) == 11
    assert curve.tenors == [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]


def test_parse_sample_col1_values(sample_curve_txt):
    """Col1 is the canonical rate; spot-check a few against the file."""
    curve = parse_curve_txt(sample_curve_txt)
    by_tenor = {p.tenor_years: p for p in curve.points}
    assert by_tenor[2].rate_pct == pytest.approx(2.8326)
    assert by_tenor[10].rate_pct == pytest.approx(3.1774)
    assert by_tenor[12].rate_pct == pytest.approx(3.2667)


def test_parse_sample_extrapolation_flags(sample_curve_txt):
    curve = parse_curve_txt(sample_curve_txt)
    by_tenor = {p.tenor_years: p for p in curve.points}
    assert by_tenor[10].extrapolated is False
    assert by_tenor[11].extrapolated is True
    assert by_tenor[12].extrapolated is True


def test_missing_date_raises(tmp_path):
    f = tmp_path / "bad.txt"
    f.write_text("15 JUN 2026  2 Years  2.8\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Curve date"):
        parse_curve_txt(f)


def test_no_points_raises(tmp_path):
    f = tmp_path / "empty.txt"
    f.write_text("Curve date: 15 JUN 2026\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no curve points"):
        parse_curve_txt(f)
