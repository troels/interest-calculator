"""Tests for the Danmarks Statistik (DNRNURI) realkredit-rate parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from loan.data import dst

FIXTURE = Path(__file__).parent / "fixtures" / "dnrnuri_fixed.csv"


def test_parse_period():
    assert dst._parse_period("2026M04") == "2026-04"
    assert dst._parse_period("2013M10") == "2013-10"


def test_parse_value_danish_decimal():
    assert dst._parse_value("4,488") == pytest.approx(4.488)
    assert dst._parse_value("..") is None
    assert dst._parse_value("") is None


def test_parse_real_fixture_skips_missing():
    """Non-profit (1500) long-fixed has data from 2013-10; earlier months are '..'."""
    points = dst.parse_series_csv(FIXTURE.read_text(encoding="utf-8"))
    periods = [p for p, _ in points]
    assert periods[0] == "2013-10"
    assert periods[-1] == "2026-04"
    by = dict(points)
    assert by["2026-04"] == pytest.approx(5.116)
    # all-in fixed rate is a realistic ~3-6% over the period, never the bogus 3.0 blend
    assert all(0.0 < v < 8.0 for v in by.values())


def test_source_tag():
    assert dst.source_tag("S10A") == "dst:DNRNURI:AL51EFFR:S10A:1500:ALLE"


def test_bad_header_raises():
    with pytest.raises(dst.DstError):
        dst.parse_series_csv("FOO;BAR\n1;2\n")
