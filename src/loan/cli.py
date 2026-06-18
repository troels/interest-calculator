"""Command-line interface for the loan engine.

Thin presentation layer over the engine — every command resolves to plain
engine functions so a later API/React frontend can call the same code.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import typer

from .config import load_env
from .backtest import run_backtest
from .charts import plot_strategy_costs, plot_backtest
from .compare import compare_now
from .curves import CurveModel
from .data.cache import Cache
from .data import dfbf
from .data import dst
from .data.backfill import backfill_curves
from .data.db import CurveDB
from .data.loaders import parse_curve_txt, parse_dmy
from .engine import curve_model_on, rk_fixed_rate_on, rk_flex_rate_on
from .models import SwapContract
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
def fetch_realkredit(
    sector: str = typer.Option(dst.DEFAULT_SECTOR, "--sector",
                               help="DST INDSEK (1100=corp, 1400=households)"),
    loansize: str = typer.Option(dst.DEFAULT_LOANSIZE, "--loansize",
                                 help="DST LAANSTR (S75M=>7.5M, ALLE=all)"),
) -> None:
    """Fetch all-in effective realkredit rates (fixed + flex) from DST DNRNURI into the DB."""
    db = CurveDB()
    for series, fetch, rentfix in (
        (dst.RK_FIXED_SERIES, dst.realkredit_fixed_rate, dst.RENTFIX_FIXED),
        (dst.RK_FLEX_SERIES, dst.realkredit_flex_rate, dst.RENTFIX_FLEX),
    ):
        points = fetch(sector=sector, loansize=loansize)
        n = db.put_rate_series(series, dst.source_tag(rentfix, sector, loansize), points)
        typer.echo(f"stored {n} months for '{series}' "
                   f"({points[0][0]}={points[0][1]}% .. {points[-1][0]}={points[-1][1]}%)")
    db.close()


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
    rk_fixed: float = typer.Option(None, "--rk-fixed", help="Anchor long-fixed RK rate %% (default: DST DB)"),
    rk_flex: float = typer.Option(None, "--rk-flex", help="F5 flex RK rate %% (default: DST DB)"),
    bank_margin: float = typer.Option(0.5, "--bank-margin", help="Bank margin on the swapped loan %%"),
    amortize: bool = typer.Option(False, "--amortize/--interest-only",
                                  help="Amortizing (annuity) vs interest-only (afdragsfri)"),
    discount_rate: float = typer.Option(None, "--discount-rate",
                                        help="Flat annual discount rate %% for future money (default: swap curve)"),
    out: Path = typer.Option(None, "--out", help="Write a cost chart to this PNG"),
) -> None:
    """Compare staying in the swap vs converting to 10/20/30Y fixed realkredit (+ F5)."""
    d = _parse_cli_date(as_of) if as_of else date.today()
    db = CurveDB()
    model, cdate = _load_curve_model(db, d, curve)
    f = rk_fixed if rk_fixed is not None else rk_fixed_rate_on(db, d)
    x = rk_flex if rk_flex is not None else rk_flex_rate_on(db, d)
    db.close()
    if f is None:
        raise typer.BadParameter("no fixed realkredit rate for that date — run fetch-realkredit or pass --rk-fixed")

    swap = SwapContract()
    sv = value_swap(model, swap, d)
    res = compare_now(d, swap, sv, model, f, bank_margin_pct=bank_margin, amortize=amortize,
                      discount_rate_pct=discount_rate, flex_rate_pct=x)
    typer.echo(f"decision date {d} (curve {cdate})   horizon {res['horizon_years']:.2f}y   "
               f"anchor 30Y fixed {f:.2f}%" + (f"  F5 {x:.2f}%" if x is not None else "")
               + f"   [{'amortizing' if amortize else 'interest-only'}]")
    typer.echo(f"swap breakage to exit: {res['breakage']:,.0f} DKK   "
               f"discounting: {res['discount_basis']}\n")
    typer.echo(f"{'strategy':<16}{'rate%':>8}{'breakage':>14}{'PV interest':>16}{'TOTAL PV':>16}")
    for s in res["strategies"]:
        mark = "  <- best" if s["name"] == res["best"] else ""
        typer.echo(f"{s['name']:<16}{s['rate_pct']:>8.2f}{s['breakage']:>14,.0f}"
                   f"{s['interest_pv']:>16,.0f}{s['total_pv']:>16,.0f}{mark}")
    typer.echo(f"\nbreak-even fixed realkredit rate (convert ties stay): "
               f"{res['break_even_rate_pct']:.2f}%  (a fixed loan below this beats staying)")
    if out:
        path = plot_strategy_costs(res, out)
        typer.echo(f"chart written to {path}")


@app.command("backtest")
def backtest_cmd(
    start: str = typer.Option("2020-01-01", "--from", help="Backtest start (YYYY-MM-DD)"),
    end: str = typer.Option(None, "--to", help="Backtest end (default: today)"),
    bank_margin: float = typer.Option(0.5, "--bank-margin", help="Bank margin %%"),
    amortize: bool = typer.Option(False, "--amortize/--interest-only",
                                  help="Amortizing vs interest-only realkredit"),
    discount_rate: float = typer.Option(None, "--discount-rate", help="Flat discount rate %%"),
    out: Path = typer.Option("output/backtest.png", "--out", help="Chart PNG path"),
) -> None:
    """Replay history: when would converting to realkredit have been optimal?"""
    start_d = _parse_cli_date(start)
    end_d = _parse_cli_date(end) if end else date.today()
    db = CurveDB()
    res = run_backtest(db, start=start_d, end=end_d, bank_margin_pct=bank_margin,
                       amortize=amortize, discount_rate_pct=discount_rate)
    db.close()
    typer.echo(f"backtest {res['start']}..{res['end']}: {res['months']} months, {res['gaps']} gaps "
               f"[{'amortizing' if amortize else 'interest-only'}]")
    if not res["rows"]:
        raise typer.Exit(1)
    b = res["best_month"]
    typer.echo(f"\noptimal conversion month: {b['as_of']}  "
               f"advantage {b['convert_advantage_pv']:,.0f} DKK PV (via {b['best_fixed_name']})")
    typer.echo(f"  (breakage {b['breakage']:,.0f}, RK fixed {b['rk_fixed_pct']:.2f}%, "
               f"swap mkt {b['market_rate_pct']:.2f}%)")
    # show a yearly sample
    typer.echo(f"\n{'month':<12}{'breakage':>14}{'RKfixed':>9}{'convert adv PV':>18}{'best':>16}")
    seen = set()
    for r in res["rows"]:
        yr = r["as_of"][:4]
        if yr in seen:
            continue
        seen.add(yr)
        typer.echo(f"{r['as_of']:<12}{r['breakage']:>14,.0f}{r['rk_fixed_pct']:>9.2f}"
                   f"{r['convert_advantage_pv']:>18,.0f}{r['best']:>16}")
    path = plot_backtest(res["rows"], out)
    typer.echo(f"\nchart written to {path}")


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
