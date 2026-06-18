"""Client for Danmarks Statistik (statbank) — realkredit effective lending rates.

Source table **DNRNURI** "New domestic mortgage loans from mortgage banks"
(Nationalbanken's MFI interest-rate statistics, published via DST). No auth; the
whole monthly history (from 2003) comes in one request per series.

We read ``AL51EFFR`` = "Annualised agreed rate incl. administration rate" — the
all-in effective realkredit rate the borrower actually pays (**bidrag already
included**), split by original interest-rate fixation:

  * fixed 30Y  -> ``RENTFIX = S10A`` (rate fixed over 10 years)
  * flex/short -> ``RENTFIX = M1A``  (rate fixed up to 1 year)

Defaults target this borrower's profile: an **andelsboligforening** (housing
co-op), which is a non-profit — ``INDSEK=1500`` "Non-profit institutions serving
households", ``LAANSTR=ALLE``. The short (<=1y) bucket is confidential for this
sector, so the flex rate uses the 1-5y bucket (``RENTFIX=1A5A``, i.e. F3/F5-type
loans, the realistic flex choice for a co-op). All are configurable.

Replaces the earlier MPK3 series, which used a niche bond category and gave an
incorrect (too low) rate.
"""

from __future__ import annotations

import csv
import io

DATA_URL = "https://api.statbank.dk/v1/data/{table}/CSV"

TABLE = "DNRNURI"
EFF_RATE = "AL51EFFR"          # effective rate incl. bidrag (all-in)
BIDRAG = "AL51BIDS"            # administration rate (bidrag) alone

RENTFIX_FIXED = "S10A"         # > 10 years fixation -> long fixed (anchor ~30Y)
RENTFIX_FLEX = "1A5A"         # 1-5 years -> F3/F5-type (the <=1y bucket is confidential for co-ops)

DEFAULT_SECTOR = "1500"        # non-profit institutions serving households (andelsboligforening)
DEFAULT_LOANSIZE = "ALLE"      # all loan sizes (size split is corp-only)
CURRENCY = "DKK"

# Logical series names stored in rate_series.
RK_FIXED_SERIES = "rk_fixed_eff"
RK_FLEX_SERIES = "rk_flex_eff"


class DstError(RuntimeError):
    pass


def _parse_period(tid: str) -> str:
    tid = tid.strip()
    if "M" in tid:
        y, m = tid.split("M")
        return f"{int(y):04d}-{int(m):02d}"
    return tid


def _parse_value(raw: str) -> float | None:
    raw = raw.strip()
    if not raw or raw == "..":
        return None
    return float(raw.replace(".", "").replace(",", ".")) if "," in raw else float(raw)


def parse_series_csv(text: str) -> list[tuple[str, float]]:
    """Parse a single-series DNRNURI CSV (…;TID;INDHOLD) into (period, value)."""
    text = text.lstrip("﻿")
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    if not rows:
        raise DstError("empty DST response")
    header = [h.strip().upper() for h in rows[0]]
    try:
        i_tid, i_val = header.index("TID"), header.index("INDHOLD")
    except ValueError as exc:
        raise DstError(f"unexpected DST header: {rows[0]}") from exc
    out: list[tuple[str, float]] = []
    for row in rows[1:]:
        if len(row) <= max(i_tid, i_val):
            continue
        v = _parse_value(row[i_val])
        if v is None:
            continue
        out.append((_parse_period(row[i_tid]), v))
    return out


def fetch_series(rentfix: str, *, data: str = EFF_RATE, sector: str = DEFAULT_SECTOR,
                 loansize: str = DEFAULT_LOANSIZE, timeout: float = 30.0) -> list[tuple[str, float]]:
    """Fetch one DNRNURI series (single data type + rate-fixation) across all months."""
    import httpx

    body = {
        "table": TABLE, "format": "CSV", "valuePresentation": "Value",
        "delimiter": "Semicolon",
        "variables": [
            {"code": "DATA", "values": [data]},
            {"code": "INDSEK", "values": [sector]},
            {"code": "VALUTA", "values": [CURRENCY]},
            {"code": "LØBETID1", "values": ["ALLE"]},
            {"code": "RENTFIX", "values": [rentfix]},
            {"code": "LAANSTR", "values": [loansize]},
            {"code": "Tid", "values": ["*"]},
        ],
    }
    resp = httpx.post(DATA_URL.format(table=TABLE), json=body, timeout=timeout)
    resp.raise_for_status()
    points = parse_series_csv(resp.text)
    if not points:
        raise DstError(f"no points parsed for RENTFIX={rentfix}")
    return points


def realkredit_fixed_rate(**kw) -> list[tuple[str, float]]:
    """All-in effective 30Y-fixed realkredit rate history (incl. bidrag)."""
    return fetch_series(RENTFIX_FIXED, **kw)


def realkredit_flex_rate(**kw) -> list[tuple[str, float]]:
    """All-in effective flex/short realkredit rate history (incl. bidrag)."""
    return fetch_series(RENTFIX_FLEX, **kw)


def source_tag(rentfix: str, sector: str = DEFAULT_SECTOR, loansize: str = DEFAULT_LOANSIZE) -> str:
    return f"dst:{TABLE}:{EFF_RATE}:{rentfix}:{sector}:{loansize}"
