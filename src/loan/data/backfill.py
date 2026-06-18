"""Gentle, resumable backfill of dfbf swap curves into the DB.

Discipline (explicit user constraints — "be very gentle", "don't awaken the
beast"):
  * randomized date order (not a predictable sequential scan);
  * a generous, jittered delay between requests;
  * cache-first: dates already in ``fetch_log`` are skipped, so a re-run after a
    cookie expiry resumes where it left off;
  * stop after too many consecutive errors (cookie likely expired) rather than
    hammering a dead session.

Network and sleep are injected so the engine is unit-testable offline.
"""

from __future__ import annotations

import random
import time
from datetime import date, timedelta
from typing import Callable

from .db import CurveDB
from . import dfbf


def business_days(start: date, end: date) -> list[date]:
    """All weekdays in [start, end] (holidays are discovered as empty responses)."""
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5:  # Mon..Fri
            days.append(d)
        d += timedelta(days=1)
    return days


def backfill_curves(
    db: CurveDB,
    *,
    start: date,
    end: date,
    min_delay: float = 4.0,
    max_delay: float = 9.0,
    max_consecutive_errors: int = 6,
    fetch_raw: Callable[[date], str] = dfbf.fetch_raw,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
    log: Callable[[str], None] = print,
    now_iso: Callable[[], str] | None = None,
) -> dict:
    """Fetch every available curve in [start, end], randomized and gently."""
    rng = rng or random.Random()
    pending = [d for d in business_days(start, end) if not db.attempted(d)]
    rng.shuffle(pending)  # random sequence, per the gentleness requirement

    total = len(pending)
    log(f"backfill: {total} dates pending in [{start} .. {end}] (skipping already-fetched)")

    consecutive_errors = 0
    done = ok = empty = err = 0
    for d in pending:
        try:
            raw = fetch_raw(d)
        except Exception as exc:  # noqa: BLE001
            consecutive_errors += 1
            err += 1
            stamp = now_iso() if now_iso else None
            db.record(d, "error", raw_json=str(exc)[:500], fetched_at=stamp)
            log(f"  {d}: ERROR {exc} ({consecutive_errors} in a row)")
            if consecutive_errors >= max_consecutive_errors:
                log(f"backfill: stopping after {consecutive_errors} consecutive errors "
                    "(cookie likely expired — refresh DFBF_COOKIE and re-run to resume)")
                break
            sleep(rng.uniform(min_delay, max_delay))
            continue

        consecutive_errors = 0
        stamp = now_iso() if now_iso else None
        try:
            curve = dfbf.parse_dfbf_response(raw, d)
            db.record(d, "ok", points=list(curve.points), raw_json=raw, fetched_at=stamp)
            ok += 1
            tag = f"ok {len(curve.points)}pts"
        except dfbf.DfbfError:
            # valid response but no data (weekend/holiday/pre-history)
            db.record(d, "empty", raw_json=raw, fetched_at=stamp)
            empty += 1
            tag = "empty"

        done += 1
        if done % 25 == 0 or done == total:
            log(f"  progress {done}/{total} (ok={ok} empty={empty} err={err}) last={d} {tag}")
        sleep(rng.uniform(min_delay, max_delay))

    return {"pending": total, "ok": ok, "empty": empty, "error": err}
