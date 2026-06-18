"""Tests for the Danmarks Statistik (MPK3) parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from loan.data import dst

FIXTURE = Path(__file__).parent / "fixtures" / "dst_mpk3_rk_fixed.csv"


def test_parse_period():
    assert dst._parse_period("2026M05") == "2026-05"
    assert dst._parse_period("1985M01") == "1985-01"


def test_parse_value_danish_decimal():
    assert dst._parse_value("3,00") == pytest.approx(3.0)
    assert dst._parse_value("13,85") == pytest.approx(13.85)
    assert dst._parse_value("..") is None
    assert dst._parse_value("") is None


def test_parse_real_fixture():
    points = dst.parse_mpk3_csv(FIXTURE.read_text(encoding="utf-8"))
    # 497 months of data in the fixture (1985M01..2026M05)
    assert len(points) == 497
    assert points[0] == ("1985-01", pytest.approx(13.85))
    assert points[-1] == ("2026-05", pytest.approx(3.00))
    # periods are sorted and well-formed
    assert all(len(p) == 7 and p[4] == "-" for p, _ in points)


def test_parse_skips_missing_rows():
    csv = "TYPE;TID;INDHOLD\nX;2020M01;2,50\nX;2020M02;..\nX;2020M03;2,60\n"
    points = dst.parse_mpk3_csv(csv)
    assert [p for p, _ in points] == ["2020-01", "2020-03"]


def test_bad_header_raises():
    with pytest.raises(dst.DstError):
        dst.parse_mpk3_csv("FOO;BAR\n1;2\n")
