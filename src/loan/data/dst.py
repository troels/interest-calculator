"""Client for the Danmarks Statistik (statbank) public API.

No auth, no rate-limit concerns: one request returns an entire series, so unlike
dfbf there is no gentle backfill — we fetch the whole history in a single call.

We use table **MPK3** ("Interest rates, by type"), series ``5500701001`` =
"Unit mortgage bonds (redemption yield)" — the realkredit bond redemption yield,
monthly back to 1985. This is the fixed-realkredit rate proxy for the cost model
(the flex/short rate is taken from the dfbf swap-curve short end instead).

Caveats (documented, not hidden):
  * it is a *blended* realkredit bond yield, not split by maturity/coupon;
  * monthly ultimo values; missing months come back as ``..`` and are skipped.
"""

from __future__ import annotations

import csv
import io

DATA_URL = "https://api.statbank.dk/v1/data/{table}/CSV"

MPK3_TABLE = "MPK3"
MPK3_RK_FIXED_YIELD = "5500701001"   # Unit mortgage bonds (redemption yield)

# Logical series name + provenance used when storing in rate_series.
RK_FIXED_SERIES = "rk_fixed_yield"
RK_FIXED_SOURCE = f"dst:{MPK3_TABLE}:{MPK3_RK_FIXED_YIELD}"


class DstError(RuntimeError):
    pass


def _parse_period(tid: str) -> str:
    """'2026M05' -> '2026-05'."""
    tid = tid.strip()
    if "M" in tid:
        y, m = tid.split("M")
        return f"{int(y):04d}-{int(m):02d}"
    return tid


def _parse_value(raw: str) -> float | None:
    """Danish decimal comma; '..' / '' mean missing."""
    raw = raw.strip()
    if not raw or raw == "..":
        return None
    return float(raw.replace(".", "").replace(",", ".")) if "," in raw else float(raw)


def parse_mpk3_csv(text: str) -> list[tuple[str, float]]:
    """Parse a semicolon MPK3 CSV (TYPE;TID;INDHOLD) into (period, value) points."""
    text = text.lstrip("﻿")
    reader = csv.reader(io.StringIO(text), delimiter=";")
    rows = list(reader)
    if not rows:
        raise DstError("empty DST response")
    header = [h.strip().upper() for h in rows[0]]
    try:
        i_tid, i_val = header.index("TID"), header.index("INDHOLD")
    except ValueError as exc:
        raise DstError(f"unexpected DST header: {rows[0]}") from exc

    points: list[tuple[str, float]] = []
    for row in rows[1:]:
        if len(row) <= max(i_tid, i_val):
            continue
        value = _parse_value(row[i_val])
        if value is None:
            continue
        points.append((_parse_period(row[i_tid]), value))
    return points


def fetch_mpk3_series(type_code: str = MPK3_RK_FIXED_YIELD, *, timeout: float = 30.0) -> str:
    """Fetch the raw CSV for one MPK3 type across all available months."""
    import httpx

    params = {
        "valuePresentation": "Value",
        "delimiter": "Semicolon",
        "TYPE": type_code,
        "Tid": "*",
    }
    resp = httpx.get(DATA_URL.format(table=MPK3_TABLE), params=params,
                     headers={"accept": "text/csv"}, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def realkredit_fixed_yield(*, timeout: float = 30.0) -> list[tuple[str, float]]:
    """Full monthly realkredit fixed-yield history as (period 'YYYY-MM', percent)."""
    raw = fetch_mpk3_series(MPK3_RK_FIXED_YIELD, timeout=timeout)
    points = parse_mpk3_csv(raw)
    if not points:
        raise DstError("no realkredit yield points parsed from MPK3")
    return points
