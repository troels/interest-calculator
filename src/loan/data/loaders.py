"""Parsers for local curve files.

The provided ``swap_rate_curve_YYYY-MM-DD.txt`` (source: dfbf) is the reference
format. ``Col1`` is the canonical par swap rate; later columns are alternative
fixings we don't use. An "Extrapolated" section appends 11Y/12Y points with only
``Col1`` present — those are flagged ``extrapolated=True``.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from ..models import Curve, CurvePoint

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

# e.g. "15 JUN 2026"
_DATE_RE = re.compile(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})")
# e.g. "15 JUN 2026    2 Years    2.8326    2.8340 ..."
_ROW_RE = re.compile(
    r"^\s*\d{1,2}\s+[A-Za-z]{3}\s+\d{4}\s+(\d+(?:\.\d+)?)\s+Years?\s+(.+)$"
)


def parse_dmy(text: str) -> date:
    """Parse a 'DD MON YYYY' date (e.g. '15 JUN 2026')."""
    m = _DATE_RE.search(text)
    if not m:
        raise ValueError(f"could not parse date from: {text!r}")
    day, mon, year = m.group(1), m.group(2).upper(), m.group(3)
    if mon not in _MONTHS:
        raise ValueError(f"unknown month abbreviation: {mon!r}")
    return date(int(year), _MONTHS[mon], int(day))


def parse_curve_txt(path: str | Path) -> Curve:
    """Parse a swap-rate-curve text file into a :class:`Curve`.

    Raises ``ValueError`` on a missing curve date or zero parsed points, so bad
    files fail loudly rather than producing a silently empty curve.
    """
    text = Path(path).read_text(encoding="utf-8")
    lines = text.splitlines()

    curve_date: date | None = None
    extrapolated = False
    points: list[CurvePoint] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith("curve date"):
            curve_date = parse_dmy(stripped)
            continue
        if stripped.lower().startswith("extrapolated"):
            extrapolated = True
            continue
        if stripped.lower().startswith("date") and "tenor" in stripped.lower():
            continue  # column header

        m = _ROW_RE.match(line)
        if not m:
            continue
        tenor = float(m.group(1))
        # First numeric token after the tenor is Col1 (the canonical rate).
        cols = m.group(2).split()
        rate = float(cols[0])
        points.append(CurvePoint(tenor_years=tenor, rate_pct=rate, extrapolated=extrapolated))

    if curve_date is None:
        raise ValueError(f"no 'Curve date' line found in {path}")
    if not points:
        raise ValueError(f"no curve points parsed from {path}")

    return Curve(curve_date=curve_date, points=tuple(points), source="dfbf", market="SWAP")
