"""SQLite store for fetched curve data.

Two tables:

  * ``curve_points`` — one row per (date, tenor): the canonical fixing rate.
  * ``fetch_log``    — one row per date we have *attempted*, with status
    (``ok`` / ``empty`` / ``error``) and the raw response. This doubles as the
    cache-first index: a date present here is never re-fetched, and it records
    coverage (including weekends/holidays that legitimately return no data).

The DB is the store of record for historical curves used by the backtester.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from datetime import date as _date

from ..models import Curve, CurvePoint, RealkreditQuote

DEFAULT_DB_PATH = Path(__file__).resolve().parents[3] / "data" / "loan.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS curve_points (
    curve_date TEXT NOT NULL,
    tenor      REAL NOT NULL,
    rate       REAL NOT NULL,
    source     TEXT NOT NULL DEFAULT 'dfbf',
    market     TEXT NOT NULL DEFAULT 'SWAP',
    PRIMARY KEY (curve_date, tenor, source, market)
);
CREATE TABLE IF NOT EXISTS fetch_log (
    curve_date TEXT PRIMARY KEY,
    status     TEXT NOT NULL,          -- ok | empty | error
    n_points   INTEGER NOT NULL DEFAULT 0,
    raw_json   TEXT,
    fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS realkredit_quotes (
    quote_date TEXT NOT NULL,
    isin       TEXT NOT NULL,
    name       TEXT,
    issuer     TEXT,
    product    TEXT,                   -- fixed_callable | flex | unknown
    coupon     REAL,
    maturity   TEXT,
    price      REAL,                   -- kurs
    yield_pct  REAL,
    source     TEXT NOT NULL DEFAULT 'nasdaq',
    PRIMARY KEY (quote_date, isin)
);
CREATE TABLE IF NOT EXISTS realkredit_fetch_log (
    fetch_key  TEXT PRIMARY KEY,       -- date or instrument id, per source access pattern
    status     TEXT NOT NULL,          -- ok | empty | error
    n_rows     INTEGER NOT NULL DEFAULT 0,
    raw        TEXT,
    fetched_at TEXT
);
-- Monthly (or daily) aggregate rate series, e.g. DST MPK3 realkredit yield.
CREATE TABLE IF NOT EXISTS rate_series (
    series TEXT NOT NULL,              -- e.g. 'rk_fixed_yield'
    period TEXT NOT NULL,              -- 'YYYY-MM' (monthly) or ISO date
    value  REAL NOT NULL,             -- percent p.a.
    source TEXT NOT NULL,             -- e.g. 'dst:MPK3:5500701001'
    PRIMARY KEY (series, period)
);
"""


class CurveDB:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "CurveDB":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ----------------------------------------------------------- cache-first
    def attempted(self, d: date) -> bool:
        """True if this date has already been fetched (any status)."""
        cur = self.conn.execute(
            "SELECT 1 FROM fetch_log WHERE curve_date = ?", (d.isoformat(),)
        )
        return cur.fetchone() is not None

    def attempted_dates(self) -> set[str]:
        cur = self.conn.execute("SELECT curve_date FROM fetch_log")
        return {row[0] for row in cur.fetchall()}

    # ----------------------------------------------------------------- write
    def record(
        self,
        d: date,
        status: str,
        *,
        points: list[CurvePoint] | None = None,
        raw_json: str | None = None,
        fetched_at: str | None = None,
    ) -> None:
        iso = d.isoformat()
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO fetch_log "
                "(curve_date, status, n_points, raw_json, fetched_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (iso, status, len(points or []), raw_json, fetched_at),
            )
            if points:
                self.conn.executemany(
                    "INSERT OR REPLACE INTO curve_points "
                    "(curve_date, tenor, rate, source, market) VALUES (?, ?, ?, 'dfbf', 'SWAP')",
                    [(iso, p.tenor_years, p.rate_pct) for p in points],
                )

    # ------------------------------------------------------------------ read
    def get_curve(self, d: date) -> Curve | None:
        cur = self.conn.execute(
            "SELECT tenor, rate FROM curve_points WHERE curve_date = ? ORDER BY tenor",
            (d.isoformat(),),
        )
        rows = cur.fetchall()
        if not rows:
            return None
        points = tuple(CurvePoint(tenor_years=r["tenor"], rate_pct=r["rate"]) for r in rows)
        return Curve(curve_date=d, points=points, source="dfbf", market="SWAP")

    def curve_dates(self) -> list[date]:
        """All dates that have curve points (status ok), ascending."""
        cur = self.conn.execute(
            "SELECT DISTINCT curve_date FROM curve_points ORDER BY curve_date"
        )
        return [date.fromisoformat(row[0]) for row in cur.fetchall()]

    # ----------------------------------------------------------- realkredit
    def rk_attempted(self, key: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM realkredit_fetch_log WHERE fetch_key = ?", (key,)
        )
        return cur.fetchone() is not None

    def rk_record(
        self,
        key: str,
        status: str,
        *,
        quotes: list[RealkreditQuote] | None = None,
        raw: str | None = None,
        fetched_at: str | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO realkredit_fetch_log "
                "(fetch_key, status, n_rows, raw, fetched_at) VALUES (?, ?, ?, ?, ?)",
                (key, status, len(quotes or []), raw, fetched_at),
            )
            if quotes:
                self.conn.executemany(
                    "INSERT OR REPLACE INTO realkredit_quotes "
                    "(quote_date, isin, name, issuer, product, coupon, maturity, "
                    " price, yield_pct, source) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (q.quote_date.isoformat(), q.isin, q.name, q.issuer, q.product,
                         q.coupon_pct, q.maturity.isoformat() if q.maturity else None,
                         q.price, q.yield_pct, q.source)
                        for q in quotes
                    ],
                )

    def rk_quotes_on(self, d: _date, product: str | None = None) -> list[RealkreditQuote]:
        sql = "SELECT * FROM realkredit_quotes WHERE quote_date = ?"
        params: list = [d.isoformat()]
        if product:
            sql += " AND product = ?"
            params.append(product)
        sql += " ORDER BY coupon"
        rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_quote(r) for r in rows]

    @staticmethod
    def _row_to_quote(r: sqlite3.Row) -> RealkreditQuote:
        return RealkreditQuote(
            quote_date=_date.fromisoformat(r["quote_date"]),
            isin=r["isin"], name=r["name"], issuer=r["issuer"], product=r["product"],
            coupon_pct=r["coupon"],
            maturity=_date.fromisoformat(r["maturity"]) if r["maturity"] else None,
            price=r["price"], yield_pct=r["yield_pct"], source=r["source"],
        )

    # --------------------------------------------------------- rate series
    def put_rate_series(self, series: str, source: str,
                        points: list[tuple[str, float]]) -> int:
        with self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO rate_series (series, period, value, source) "
                "VALUES (?, ?, ?, ?)",
                [(series, period, value, source) for period, value in points],
            )
        return len(points)

    def get_rate_series(self, series: str) -> list[tuple[str, float]]:
        cur = self.conn.execute(
            "SELECT period, value FROM rate_series WHERE series = ? ORDER BY period",
            (series,),
        )
        return [(r["period"], r["value"]) for r in cur.fetchall()]

    def rate_on(self, series: str, d: _date) -> float | None:
        """Latest value at or before month ``d`` (monthly series, 'YYYY-MM')."""
        period = f"{d.year:04d}-{d.month:02d}"
        cur = self.conn.execute(
            "SELECT value FROM rate_series WHERE series = ? AND period <= ? "
            "ORDER BY period DESC LIMIT 1",
            (series, period),
        )
        row = cur.fetchone()
        return row[0] if row else None

    def stats(self) -> dict:
        ok = self.conn.execute(
            "SELECT COUNT(*) FROM fetch_log WHERE status='ok'"
        ).fetchone()[0]
        empty = self.conn.execute(
            "SELECT COUNT(*) FROM fetch_log WHERE status='empty'"
        ).fetchone()[0]
        err = self.conn.execute(
            "SELECT COUNT(*) FROM fetch_log WHERE status='error'"
        ).fetchone()[0]
        rng = self.conn.execute(
            "SELECT MIN(curve_date), MAX(curve_date) FROM curve_points"
        ).fetchone()
        return {
            "ok": ok, "empty": empty, "error": err,
            "first_curve": rng[0], "last_curve": rng[1],
        }
