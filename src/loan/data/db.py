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

from ..models import Curve, CurvePoint

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
