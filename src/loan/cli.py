"""Command-line interface for the loan engine.

Thin presentation layer over the engine — every command resolves to plain
engine functions so a later API/React frontend can call the same code.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import typer

from .config import load_env
from .data.cache import Cache
from .data import dfbf
from .data.backfill import backfill_curves
from .data.db import CurveDB
from .data.loaders import parse_curve_txt, parse_dmy

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
