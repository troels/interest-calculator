"""Tests for the backfill engine (network + sleep injected, fully offline)."""

from __future__ import annotations

import json
import random
from datetime import date

from loan.data.backfill import business_days, backfill_curves
from loan.data.db import CurveDB

OK_RAW = json.dumps({"status": "ok", "data": [
    {"Tenor": "2 Years", "Fixing Rate": "2.8"},
    {"Tenor": "10 Years", "Fixing Rate": "3.1"},
]})
EMPTY_RAW = json.dumps({"status": "ok", "data": [], "message": ""})


def test_business_days_excludes_weekends():
    days = business_days(date(2026, 6, 15), date(2026, 6, 21))  # Mon..Sun
    assert days == [date(2026, 6, 15 + i) for i in range(5)]  # Mon..Fri


def test_backfill_stores_ok_and_empty(tmp_path):
    db = CurveDB(tmp_path / "t.db")

    def fake_fetch(d):
        # weekday 2026-06-17 has data; everything else empty
        return OK_RAW if d == date(2026, 6, 17) else EMPTY_RAW

    res = backfill_curves(
        db, start=date(2026, 6, 15), end=date(2026, 6, 19),
        fetch_raw=fake_fetch, sleep=lambda s: None,
        rng=random.Random(0), log=lambda m: None,
    )
    assert res["ok"] == 1
    assert res["empty"] == 4
    assert db.get_curve(date(2026, 6, 17)).rates == [2.8, 3.1]


def test_backfill_is_resumable(tmp_path):
    """A second run skips already-attempted dates (cache-first)."""
    db = CurveDB(tmp_path / "t.db")
    calls = {"n": 0}

    def fake_fetch(d):
        calls["n"] += 1
        return EMPTY_RAW

    args = dict(start=date(2026, 6, 15), end=date(2026, 6, 19),
                fetch_raw=fake_fetch, sleep=lambda s: None,
                rng=random.Random(1), log=lambda m: None)
    backfill_curves(db, **args)
    first = calls["n"]
    assert first == 5
    backfill_curves(db, **args)  # nothing pending now
    assert calls["n"] == first   # no further network calls


def test_backfill_stops_after_consecutive_errors(tmp_path):
    db = CurveDB(tmp_path / "t.db")
    calls = {"n": 0}

    def boom(d):
        calls["n"] += 1
        raise RuntimeError("401 expired session")

    res = backfill_curves(
        db, start=date(2020, 1, 1), end=date(2020, 12, 31),
        fetch_raw=boom, sleep=lambda s: None, max_consecutive_errors=3,
        rng=random.Random(2), log=lambda m: None,
    )
    assert calls["n"] == 3          # stopped early, did not hammer
    assert res["error"] == 3
