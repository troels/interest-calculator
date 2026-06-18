"""Gentle, cache-first client for dfbf.dk swap-rate curves.

Endpoint (provided by the user)::

    POST https://dfbf.dk/wp-admin/admin-ajax.php
    body: action=fetch_rates&table=danish&market=SWAP&date=DD/MM/YYYY

Discipline (explicit user constraint — be *very* gentle):
  * cache-first: a date already on disk never hits the network;
  * one request per missing date, a small inter-request delay, no retries;
  * session cookies come from the ``DFBF_COOKIE`` env var (or a .env), never
    committed and never hardcoded.

The raw response is always stored verbatim in the cache so the parser can be
refined offline without re-fetching.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import date

from ..models import Curve, CurvePoint
from .cache import Cache

URL = "https://dfbf.dk/wp-admin/admin-ajax.php"
NAMESPACE = "dfbf"
DEFAULT_DELAY_S = 1.5

_HEADERS = {
    "accept": "*/*",
    "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
    "origin": "https://dfbf.dk",
    "referer": "https://dfbf.dk/dfbf-benchmarks/information-portal/",
    "user-agent": "Mozilla/5.0 (X11; Linux x86_64) loan-tool/0.1",
    "x-requested-with": "XMLHttpRequest",
}


class DfbfError(RuntimeError):
    pass


def _body(d: date) -> str:
    return f"action=fetch_rates&table=danish&market=SWAP&date={d:%d/%m/%Y}"


def _cookie() -> str:
    cookie = os.environ.get("DFBF_COOKIE", "").strip()
    if not cookie:
        raise DfbfError(
            "DFBF_COOKIE is not set. Put your dfbf.dk session cookie in the "
            "environment or a .env file (it is never committed)."
        )
    return cookie


def fetch_raw(d: date, *, timeout: float = 20.0) -> str:
    """Make a single POST to dfbf for date ``d`` and return the raw response text."""
    import httpx  # local import keeps httpx optional for offline/test use

    headers = dict(_HEADERS, cookie=_cookie())
    resp = httpx.post(URL, data=_body(d), headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.text


# ---------------------------------------------------------------------------
# Parsing
#
# The exact response shape is confirmed against a real discovery fetch and the
# reference txt. The parser is deliberately tolerant: it accepts the JSON shape
# dfbf returns and maps tenor -> Col1 (canonical par rate).
# ---------------------------------------------------------------------------

_TENOR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*Years?", re.IGNORECASE)


def _coerce_rows(payload) -> list[dict]:
    """Normalise the various container shapes WP/admin-ajax may return."""
    if isinstance(payload, dict):
        for key in ("data", "rates", "rows", "result"):
            if key in payload and isinstance(payload[key], (list, dict)):
                return _coerce_rows(payload[key])
        # dict keyed by tenor -> values
        return [{"tenor": k, **(v if isinstance(v, dict) else {"value": v})}
                for k, v in payload.items()]
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, (dict, list))]
    raise DfbfError("unrecognised dfbf payload container")


def _row_tenor(row) -> float | None:
    if isinstance(row, list):
        for cell in row:
            if isinstance(cell, str) and (m := _TENOR_RE.search(cell)):
                return float(m.group(1))
        return None
    for key in ("tenor", "Tenor", "maturity", "term", "label"):
        if key in row and isinstance(row[key], str):
            if m := _TENOR_RE.search(row[key]):
                return float(m.group(1))
    return None


def _row_rate(row) -> float | None:
    if isinstance(row, list):
        # First numeric cell after the tenor cell is the canonical rate.
        nums = [c for c in row if _isnum(c)]
        return float(nums[0]) if nums else None
    # "Fixing Rate" is dfbf's canonical rate (== Col1 in the reference txt);
    # the rest (DSKE/JYKE/NORD/NYKR/SEBB/SYDB) are per-bank contributors.
    for key in ("Fixing Rate", "Col1", "col1", "rate", "value", "mid"):
        if key in row and _isnum(row[key]):
            return float(row[key])
    return None


def _isnum(x) -> bool:
    try:
        float(x)
        return True
    except (TypeError, ValueError):
        return False


def parse_dfbf_response(raw: str, curve_date: date) -> Curve:
    """Parse a raw dfbf response into a :class:`Curve`."""
    raw = raw.strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DfbfError(
            "dfbf response is not JSON; inspect the cached raw file and extend "
            f"parse_dfbf_response (first 120 chars: {raw[:120]!r})"
        ) from exc

    rows = _coerce_rows(payload)
    points: list[CurvePoint] = []
    for row in rows:
        tenor = _row_tenor(row)
        rate = _row_rate(row)
        if tenor is None or rate is None:
            continue
        points.append(CurvePoint(tenor_years=tenor, rate_pct=rate))

    if not points:
        raise DfbfError("no curve points parsed from dfbf response")
    return Curve(curve_date=curve_date, points=tuple(points), source="dfbf", market="SWAP")


def fetch_curve(
    d: date,
    *,
    cache: Cache | None = None,
    allow_network: bool = True,
    delay_s: float = DEFAULT_DELAY_S,
) -> Curve:
    """Return the swap curve for date ``d``, cache-first.

    If the date is cached, parse and return it without any network call. If it
    is missing and ``allow_network`` is True, make exactly one gentle request,
    cache the raw response, then parse it.
    """
    cache = cache or Cache()
    key = d.isoformat()

    raw = cache.get(NAMESPACE, key)
    if raw is not None:
        return parse_dfbf_response(raw, d)

    if not allow_network:
        raise DfbfError(f"{key} not in cache and allow_network=False")

    if delay_s:
        time.sleep(delay_s)  # be gentle
    raw = fetch_raw(d)
    cache.put(NAMESPACE, key, raw)
    return parse_dfbf_response(raw, d)
