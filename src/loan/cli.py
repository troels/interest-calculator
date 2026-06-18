"""Command-line interface for the loan engine.

Thin presentation layer over the engine — every command resolves to plain
engine functions so a later API/React frontend can call the same code.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import typer

from .config import load_env
from .charts import plot_strategy_costs
from .compare import compare_now
from .curves import CurveModel
from .data.cache import Cache
from .data import dfbf
from .data import dst
from .data.backfill import backfill_curves
from .data.db import CurveDB
from .data.loaders import parse_curve_txt, parse_dmy
from .engine import curve_model_on, rk_fixed_yield_on
from .models import RealkreditLoan, SwapContract
from .valuation.swap import value_swap

load_env()  # pull DFBF_COOKIE etc. from an untracked .env, if present

app = typer.Typer(add_completion=False, help="Realkredit vs. interest-swap cost tool.")


def _parse_cli_date(s: str) -> date:
    """Accept DD/MM/YYYY (dfbf style) or YYYY-MM-DD."""
    s = s.strip()
    if "/" in s:
        d, m, y = (int(x) for x in s.split("/"))
        return date(y, m, d)
    return date.fromisoformat(s)


@app.command("fetch-curve")
def fetch_curve(
    date_: str = typer.Option(..., "--date", help="Start date, DD/MM/YYYY or YYYY-MM-DD"),
    to: str = typer.Option(None, "--to", help="Optional end date (inclusive) for a range"),
    no_network: bool = typer.Option(False, "--no-network", help="Cache-only; never hit dfbf"),
    delay: float = typer.Option(dfbf.DEFAULT_DELAY_S, "--delay", help="Seconds between requests"),
) -> None:
    """Fetch dated swap curve(s) from dfbf.dk into the cache (cache-first, gentle)."""
    cache = Cache()
    start = _parse_cli_date(date_)
    end = _parse_cli_date(to) if to else start
    if end < start:
        raise typer.BadParameter("--to must be on or after --date")

    cur = start
    fetched = cached = failed = 0
    while cur <= end:
        cached_already = cache.has(dfbf.NAMESPACE, cur.isoformat())
        try:
            curve = dfbf.fetch_curve(
                cur, cache=cache, allow_network=not no_network, delay_s=delay
            )
            if cached_already:
                cached += 1
            else:
                fetched += 1
            typer.echo(f"{cur.isoformat()}: {len(curve.points)} points "
                       f"({'cache' if cached_already else 'fetched'})")
        except Exception as exc:  # noqa: BLE001 - report and continue the range
            failed += 1
            typer.echo(f"{cur.isoformat()}: ERROR {exc}", err=True)
        cur += timedelta(days=1)

    typer.echo(f"done: {fetched} fetched, {cached} from cache, {failed} failed")


@app.command("backfill-curves")
def backfill_curves_cmd(
    start: str = typer.Option("2019-11-01", "--start", help="First date (default: dfbf history start)"),
    end: str = typer.Option(None, "--end", help="Last date (default: today)"),
    min_delay: float = typer.Option(4.0, "--min-delay", help="Min seconds between requests"),
    max_delay: float = typer.Option(9.0, "--max-delay", help="Max seconds between requests"),
) -> None:
    """Download all available dfbf swap curves into the DB — randomized + gentle, resumable."""
    start_d = _parse_cli_date(start)
    end_d = _parse_cli_date(end) if end else date.today()
    db = CurveDB()
    try:
        result = backfill_curves(
            db, start=start_d, end=end_d, min_delay=min_delay, max_delay=max_delay,
            now_iso=lambda: datetime.now().isoformat(timespec="seconds"),
        )
    finally:
        s = db.stats()
        db.close()
    typer.echo(f"run: {result}")
    typer.echo(f"db totals: ok={s['ok']} empty={s['empty']} error={s['error']} "
               f"range={s['first_curve']}..{s['last_curve']}")


@app.command("fetch-realkredit")
def fetch_realkredit() -> None:
    """Fetch the realkredit fixed-yield history from Danmarks Statistik (MPK3) into the DB."""
    points = dst.realkredit_fixed_yield()
    db = CurveDB()
    n = db.put_rate_series(dst.RK_FIXED_SERIES, dst.RK_FIXED_SOURCE, points)
    first, last = points[0], points[-1]
    db.close()
    typer.echo(f"stored {n} monthly points for '{dst.RK_FIXED_SERIES}' "
               f"({first[0]}={first[1]}% .. {last[0]}={last[1]}%)")


@app.command("db-status")
def db_status() -> None:
    """Show DB coverage: how many curves, date range, empties/errors."""
    db = CurveDB()
    s = db.stats()
    db.close()
    typer.echo(f"curves (ok):   {s['ok']}")
    typer.echo(f"empty days:    {s['empty']}")
    typer.echo(f"errors:        {s['error']}")
    typer.echo(f"curve range:   {s['first_curve']} .. {s['last_curve']}")


def _load_curve_model(db: CurveDB, as_of: date, curve_txt: Path | None):
    """Resolve a CurveModel: explicit txt file wins, else nearest DB curve."""
    if curve_txt is not None:
        return CurveModel(parse_curve_txt(curve_txt)), as_of
    got = curve_model_on(db, as_of)
    if got is None:
        raise typer.BadParameter(
            f"no swap curve at/before {as_of} in DB — run fetch-curve/backfill-curves "
            "or pass --curve <file>")
    return got


@app.command("value-swap")
def value_swap_cmd(
    as_of: str = typer.Option(None, "--as-of", help="Valuation date (default: today)"),
    curve: Path = typer.Option(None, "--curve", help="Use a curve txt file instead of the DB"),
    notional: float = typer.Option(22_500_000, "--notional"),
    fixed_rate: float = typer.Option(5.4, "--fixed-rate", help="Swap fixed rate %"),
) -> None:
    """Value the pay-fixed swap: mark-to-market and breakage cost."""
    d = _parse_cli_date(as_of) if as_of else date.today()
    db = CurveDB()
    model, cdate = _load_curve_model(db, d, curve)
    db.close()
    swap = SwapContract(notional=notional, fixed_rate_pct=fixed_rate)
    v = value_swap(model, swap, d)
    typer.echo(f"as-of {d} (curve {cdate}):  remaining {v.remaining_years:.2f}y")
    typer.echo(f"  market swap rate (12y): {v.market_rate_pct:.3f}%   contract: {fixed_rate:.3f}%")
    typer.echo(f"  swap MtM:   {v.mtm:>14,.0f} DKK")
    typer.echo(f"  breakage:   {v.breakage:>14,.0f} DKK  (cost to exit now)")


@app.command("compare")
def compare_cmd(
    as_of: str = typer.Option(None, "--as-of", help="Decision date (default: today)"),
    curve: Path = typer.Option(None, "--curve", help="Use a curve txt file instead of the DB"),
    rk_yield: float = typer.Option(None, "--rk-yield", help="Realkredit yield %% (default: DST DB)"),
    bidrag: float = typer.Option(0.6, "--bidrag", help="Bidragssats %%"),
    bank_margin: float = typer.Option(0.5, "--bank-margin", help="Bank margin on the swapped loan %%"),
    flex_tenor: float = typer.Option(5.0, "--flex-tenor", help="Flex reset tenor (years)"),
    out: Path = typer.Option(None, "--out", help="Write a cost chart to this PNG"),
) -> None:
    """Compare staying in the swap vs converting to realkredit (fixed + flex)."""
    d = _parse_cli_date(as_of) if as_of else date.today()
    db = CurveDB()
    model, cdate = _load_curve_model(db, d, curve)
    y = rk_yield if rk_yield is not None else rk_fixed_yield_on(db, d)
    db.close()
    if y is None:
        raise typer.BadParameter("no realkredit yield for that date — run fetch-realkredit or pass --rk-yield")

    swap = SwapContract()
    sv = value_swap(model, swap, d)
    res = compare_now(
        d, swap, sv, model, y, bank_margin_pct=bank_margin,
        fixed_loan=RealkreditLoan(notional=swap.notional, bidrag_pct=bidrag),
        flex_loan=RealkreditLoan(notional=swap.notional, product="flex",
                                 bidrag_pct=bidrag, flex_tenor_years=flex_tenor),
    )
    typer.echo(f"decision date {d} (curve {cdate})   horizon {res['horizon_years']:.2f}y   "
               f"RK yield {y:.2f}%")
    typer.echo(f"swap breakage to exit: {res['breakage']:,.0f} DKK\n")
    typer.echo(f"{'strategy':<16}{'rate%':>8}{'breakage':>14}{'PV interest':>16}{'TOTAL PV':>16}")
    for s in res["strategies"]:
        mark = "  <- best" if s["name"] == res["best"] else ""
        typer.echo(f"{s['name']:<16}{s['rate_pct']:>8.2f}{s['breakage']:>14,.0f}"
                   f"{s['interest_pv']:>16,.0f}{s['total_pv']:>16,.0f}{mark}")
    typer.echo(f"\nbreak-even realkredit yield (convert ties stay): "
               f"{res['break_even_yield_pct']:.2f}%  (today: {y:.2f}%)")
    if out:
        path = plot_strategy_costs(res, out)
        typer.echo(f"chart written to {path}")


@app.command("show-curve")
def show_curve(
    path: Path = typer.Argument(..., help="Path to a swap_rate_curve_*.txt file"),
) -> None:
    """Parse and print a local curve file (sanity check for the txt parser)."""
    curve = parse_curve_txt(path)
    typer.echo(f"Curve date: {curve.curve_date.isoformat()}  source={curve.source}")
    for p in curve.points:
        flag = " (extrapolated)" if p.extrapolated else ""
        typer.echo(f"  {p.tenor_years:>5}Y  {p.rate_pct:.4f}%{flag}")


if __name__ == "__main__":
    app()
