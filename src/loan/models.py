"""Core data models for the loan engine.

These are plain, JSON-serializable dataclasses. They hold data only — no parsing
(that lives in ``loan.data``) and no curve math (that lives in ``loan.curves``).
Keeping them UI-agnostic is what lets a later FastAPI/React frontend reuse them.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import date


@dataclass(frozen=True)
class CurvePoint:
    """A single point on a par swap-rate curve."""

    tenor_years: float
    rate_pct: float          # canonical par swap rate (Col1), in percent
    extrapolated: bool = False


@dataclass(frozen=True)
class Curve:
    """A dated par swap-rate curve (the only rate source in this project)."""

    curve_date: date
    points: tuple[CurvePoint, ...]
    source: str = "dfbf"
    market: str = "SWAP"

    def __post_init__(self) -> None:
        if not self.points:
            raise ValueError("Curve must have at least one point")
        # Store points sorted by tenor for predictable interpolation downstream.
        ordered = tuple(sorted(self.points, key=lambda p: p.tenor_years))
        object.__setattr__(self, "points", ordered)

    @property
    def tenors(self) -> list[float]:
        return [p.tenor_years for p in self.points]

    @property
    def rates(self) -> list[float]:
        return [p.rate_pct for p in self.points]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["curve_date"] = self.curve_date.isoformat()
        return d


@dataclass(frozen=True)
class RealkreditQuote:
    """A single dated quote for one realkredit bond (kurs/coupon/yield).

    Source-agnostic: whether the price comes from Nasdaq, an issuer kursliste,
    or a provided file, it normalises to this shape. ``product`` distinguishes a
    fixed callable bond (``fixed_callable``) from a short/flex refinancing bond
    (``flex``). Fields are optional because different sources expose different
    subsets (e.g. some give price but not yield).
    """

    quote_date: date
    isin: str
    name: str | None = None
    issuer: str | None = None            # e.g. RD, NYK, TOT, NDA
    product: str | None = None           # fixed_callable | flex | unknown
    coupon_pct: float | None = None
    maturity: date | None = None
    price: float | None = None           # kurs (clean price)
    yield_pct: float | None = None
    source: str = "nasdaq"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["quote_date"] = self.quote_date.isoformat()
        d["maturity"] = self.maturity.isoformat() if self.maturity else None
        return d


@dataclass(frozen=True)
class SwapContract:
    """The user's pay-fixed interest rate swap.

    Defaults describe the actual position: 22.5M DKK bullet, pay-fixed 5.4% vs
    6M CIBOR, ~12 years remaining as of the 2026-06-15 curve. The exact 2008
    start date is not needed for valuation — remaining schedule is what matters,
    derived from ``maturity`` relative to a valuation date.
    """

    notional: float = 22_500_000.0
    fixed_rate_pct: float = 5.4
    maturity: date = date(2038, 6, 15)
    pay_fixed: bool = True          # we pay fixed, receive floating
    fixed_frequency: int = 1        # annual fixed leg
    day_count: str = "30/360"

    def remaining_years(self, as_of: date) -> float:
        """Whole-ish years from ``as_of`` to maturity (actual/365.25)."""
        return max(0.0, (self.maturity - as_of).days / 365.25)


@dataclass(frozen=True)
class RealkreditLoan:
    """A realkredit loan to compare against the swap.

    Modelled bullet (interest-only) to match the swap's structure. ``product``
    selects the rate model: ``fixed_callable`` reads the DST realkredit yield;
    ``flex`` reads the swap-curve par rate at ``flex_tenor_years`` (e.g. 5 = F5,
    1 = F1/short). ``bidrag_pct`` is the admin margin added on top.
    """

    notional: float = 22_500_000.0
    product: str = "fixed_callable"      # fixed_callable | flex
    bidrag_pct: float = 0.60             # bidragssats (admin margin) p.a., percent
    flex_tenor_years: float = 5.0        # reset tenor for flex products

