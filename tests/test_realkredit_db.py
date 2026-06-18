"""Tests for realkredit quote storage in the DB."""

from __future__ import annotations

from datetime import date

from loan.data.db import CurveDB
from loan.models import RealkreditQuote


def q(isin, product, coupon, price, d=date(2023, 6, 15)):
    return RealkreditQuote(
        quote_date=d, isin=isin, name=f"{coupon}% {isin}", issuer="RD",
        product=product, coupon_pct=coupon, maturity=date(2053, 10, 1),
        price=price, yield_pct=None, source="nasdaq",
    )


def test_record_and_read_quotes(tmp_path):
    db = CurveDB(tmp_path / "t.db")
    d = date(2023, 6, 15)
    quotes = [q("DK0001", "fixed_callable", 5.0, 98.5),
              q("DK0002", "fixed_callable", 4.0, 92.1)]
    db.rk_record(d.isoformat(), "ok", quotes=quotes, raw="{}", fetched_at="2026-06-18T00:00:00")
    assert db.rk_attempted(d.isoformat())
    got = db.rk_quotes_on(d)
    assert [x.coupon_pct for x in got] == [4.0, 5.0]   # ordered by coupon
    assert got[0].price == 92.1
    assert got[0].maturity == date(2053, 10, 1)


def test_filter_by_product(tmp_path):
    db = CurveDB(tmp_path / "t.db")
    d = date(2023, 6, 15)
    db.rk_record(d.isoformat(), "ok", quotes=[
        q("DK0001", "fixed_callable", 5.0, 98.5),
        q("DK0009", "flex", 3.2, 100.0),
    ])
    fixed = db.rk_quotes_on(d, product="fixed_callable")
    assert len(fixed) == 1 and fixed[0].isin == "DK0001"


def test_rk_fetch_log_is_cache_first(tmp_path):
    db = CurveDB(tmp_path / "t.db")
    assert db.rk_attempted("2019-01-01") is False
    db.rk_record("2019-01-01", "empty")
    assert db.rk_attempted("2019-01-01") is True


def test_curve_and_realkredit_coexist(tmp_path):
    """Both data sets live in the same loan.db without interfering."""
    from loan.models import CurvePoint
    db = CurveDB(tmp_path / "t.db")
    db.record(date(2026, 6, 15), "ok", points=[CurvePoint(2, 2.83)])
    db.rk_record("2026-06-15", "ok", quotes=[q("DK0001", "fixed_callable", 5.0, 98.5,
                                               d=date(2026, 6, 15))])
    assert db.get_curve(date(2026, 6, 15)) is not None
    assert len(db.rk_quotes_on(date(2026, 6, 15))) == 1
