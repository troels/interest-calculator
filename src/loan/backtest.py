"""Historical backtester: at each past month, convert-to-realkredit vs stay.

For every decision month in the window we reconstruct the curve as of that month,
value the swap breakage, and run the same :func:`loan.compare.compare_now`
comparison. The headline series is the *convert advantage* — how much PV the
borrower would have saved (or lost) by converting to a fixed realkredit that
month instead of staying in the swap. The month with the largest advantage is
the historical optimal conversion point.

Honest about gaps: months without a curve (pre-history, missing fetch) or without
a realkredit yield are skipped and counted, not silently dropped.
"""

from __future__ import annotations

from datetime import date

from .compare import compare_now
from .data.db import CurveDB
from .engine import curve_model_on, rk_fixed_rate_on, rk_flex_rate_on
from .models import SwapContract
from .valuation.swap import value_swap


def month_ends(start: date, end: date) -> list[date]:
    """Last calendar day of each month in [start, end]."""
    out: list[date] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        nm_y, nm_m = (y + 1, 1) if m == 12 else (y, m + 1)
        last = date(nm_y, nm_m, 1).toordinal() - 1
        out.append(date.fromordinal(last))
        y, m = nm_y, nm_m
    return out


def run_backtest(
    db: CurveDB,
    *,
    start: date,
    end: date,
    swap: SwapContract | None = None,
    bank_margin_pct: float = 0.5,
) -> dict:
    """Run the convert-vs-stay comparison across every month in the window."""
    swap = swap or SwapContract()
    rows: list[dict] = []
    gaps = 0

    for d in month_ends(start, end):
        if swap.remaining_years(d) <= 0:
            continue
        got = curve_model_on(db, d)
        f = rk_fixed_rate_on(db, d)
        x = rk_flex_rate_on(db, d)
        if got is None or f is None:
            gaps += 1
            continue
        model, cdate = got
        sv = value_swap(model, swap, d)
        res = compare_now(d, swap, sv, model, f, bank_margin_pct=bank_margin_pct,
                          rk_flex_rate_pct=x)
        strat = {s["name"]: s for s in res["strategies"]}
        stay_pv = strat["stay_swap"]["total_pv"]
        conv_pv = strat["convert_fixed"]["total_pv"]
        flex_pv = strat["convert_flex"]["total_pv"] if "convert_flex" in strat else None
        rows.append({
            "as_of": d.isoformat(),
            "curve_date": cdate.isoformat(),
            "remaining_years": sv.remaining_years,
            "rk_fixed_pct": f,
            "rk_flex_pct": x,
            "market_rate_pct": sv.market_rate_pct,
            "breakage": sv.breakage,
            "stay_total_pv": stay_pv,
            "convert_fixed_total_pv": conv_pv,
            "convert_flex_total_pv": flex_pv,
            "convert_advantage_pv": stay_pv - conv_pv,   # positive => converting (fixed) wins
            "best": res["best"],
        })

    best = max(rows, key=lambda r: r["convert_advantage_pv"]) if rows else None
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "months": len(rows),
        "gaps": gaps,
        "rows": rows,
        "best_month": best,
    }
