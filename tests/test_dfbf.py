"""Tests for the dfbf client: parsing tolerance + cache-first behaviour.

The network is never touched here — ``fetch_raw`` is monkeypatched. These tests
also assert the gentle discipline: a cached date triggers zero network calls.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from loan.data import dfbf
from loan.data.cache import Cache

FIXTURE = Path(__file__).parent / "fixtures" / "dfbf_2026-06-17.json"


# A representative raw response shape (list of per-tenor rows, Col1 = canonical).
SAMPLE_ROWS = [
    {"Tenor": "2 Years", "Col1": 2.8326, "Col2": 2.8340},
    {"Tenor": "3 Years", "Col1": 2.8650},
    {"Tenor": "10 Years", "Col1": 3.1774},
]
SAMPLE_RAW = json.dumps({"success": True, "data": SAMPLE_ROWS})


def test_parse_list_of_dicts():
    curve = dfbf.parse_dfbf_response(SAMPLE_RAW, date(2026, 6, 15))
    assert curve.curve_date == date(2026, 6, 15)
    assert curve.tenors == [2, 3, 10]
    by_tenor = {p.tenor_years: p.rate_pct for p in curve.points}
    assert by_tenor[2] == pytest.approx(2.8326)
    assert by_tenor[10] == pytest.approx(3.1774)


def test_parse_real_dfbf_fixture():
    """Parse a real captured dfbf response: 'Fixing Rate' is the canonical rate."""
    raw = FIXTURE.read_text(encoding="utf-8")
    curve = dfbf.parse_dfbf_response(raw, date(2026, 6, 17))
    assert curve.tenors == [2, 3, 4, 5, 6, 7, 8, 9, 10]
    by_tenor = {p.tenor_years: p.rate_pct for p in curve.points}
    assert by_tenor[2] == pytest.approx(2.8454)   # Fixing Rate, not DSKE (2.8475)
    assert by_tenor[10] == pytest.approx(3.1492)


def test_parse_bare_list():
    raw = json.dumps([["2 Years", 2.8326, 2.834], ["3 Years", 2.8650]])
    curve = dfbf.parse_dfbf_response(raw, date(2026, 6, 15))
    assert curve.tenors == [2, 3]
    assert curve.rates[0] == pytest.approx(2.8326)


def test_parse_non_json_raises():
    with pytest.raises(dfbf.DfbfError, match="not JSON"):
        dfbf.parse_dfbf_response("<html>nope</html>", date(2026, 6, 15))


def test_parse_empty_rows_raises():
    with pytest.raises(dfbf.DfbfError, match="no curve points"):
        dfbf.parse_dfbf_response(json.dumps({"data": []}), date(2026, 6, 15))


def test_fetch_curve_cache_first(tmp_path, monkeypatch):
    """Missing date -> one network call + cache write; second call -> no network."""
    calls = {"n": 0}

    def fake_fetch_raw(d, *, timeout=20.0):
        calls["n"] += 1
        return SAMPLE_RAW

    monkeypatch.setattr(dfbf, "fetch_raw", fake_fetch_raw)
    cache = Cache(tmp_path)
    d = date(2026, 6, 15)

    curve1 = dfbf.fetch_curve(d, cache=cache, delay_s=0)
    assert calls["n"] == 1
    assert cache.has(dfbf.NAMESPACE, d.isoformat())

    curve2 = dfbf.fetch_curve(d, cache=cache, delay_s=0)
    assert calls["n"] == 1  # served from cache, no extra network call
    assert curve1.tenors == curve2.tenors


def test_fetch_curve_no_network_misses(tmp_path):
    cache = Cache(tmp_path)
    with pytest.raises(dfbf.DfbfError, match="not in cache"):
        dfbf.fetch_curve(date(2026, 6, 15), cache=cache, allow_network=False)


def test_cookie_required(monkeypatch):
    monkeypatch.delenv("DFBF_COOKIE", raising=False)
    with pytest.raises(dfbf.DfbfError, match="DFBF_COOKIE"):
        dfbf.fetch_raw(date(2026, 6, 15))
