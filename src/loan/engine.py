"""Data-access glue between the DB and the pure valuation/compare functions.

Keeps the CLI and backtester thin: they ask the engine for a ``CurveModel`` and
the realkredit yield on a date, and pass those into the pure functions.
"""

from __future__ import annotations

from datetime import date

from .curves import CurveModel
from .data.db import CurveDB
from .data.dst import RK_FIXED_SERIES, RK_FLEX_SERIES


def curve_model_on(db: CurveDB, d: date, *, max_lookback_days: int = 14
                   ) -> tuple[CurveModel, date] | None:
    """Return (CurveModel, actual_curve_date) for the nearest curve at/before ``d``."""
    found = db.nearest_curve_date(d, max_lookback_days)
    if found is None:
        return None
    curve = db.get_curve(found)
    if curve is None:
        return None
    return CurveModel(curve), found


def rk_fixed_rate_on(db: CurveDB, d: date) -> float | None:
    """All-in effective fixed (30Y) realkredit rate (percent, incl. bidrag) for month of ``d``."""
    return db.rate_on(RK_FIXED_SERIES, d)


def rk_flex_rate_on(db: CurveDB, d: date) -> float | None:
    """All-in effective flex/short realkredit rate (percent, incl. bidrag) for month of ``d``."""
    return db.rate_on(RK_FLEX_SERIES, d)
