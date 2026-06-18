"""Realkredit cost models (fixed callable + flex), driven by real rate data.

Both products are modelled bullet (interest-only) to match the swap. The all-in
borrowing rate is the bond rate plus ``bidrag`` (admin margin):

  * **fixed callable** — bond rate = the DST realkredit redemption yield at the
    date (``rate_series 'rk_fixed_yield'``). Locked for the loan's life once taken.
  * **flex / short** — bond rate = the swap-curve par rate at the reset tenor
    (``flex_tenor_years``); re-reads at each reset, so its cost tracks rates.

Simplifications (documented): the realkredit-vs-swap basis is ignored for the
flex leg (swap short rate used directly); the callable buy-back option upside is
exposed separately rather than baked into the carry cost.
"""

from __future__ import annotations

from datetime import date

from ..curves import CurveModel
from ..models import RealkreditLoan


def all_in_rate_pct(bond_rate_pct: float, bidrag_pct: float) -> float:
    """Borrower's all-in annual rate = bond rate + bidragssats."""
    return bond_rate_pct + bidrag_pct


def annual_interest(notional: float, all_in_pct: float) -> float:
    """Annual interest on a bullet loan."""
    return notional * all_in_pct / 100.0


def fixed_rate_pct(loan: RealkreditLoan, rk_yield_pct: float) -> float:
    """All-in fixed-callable rate from the DST realkredit yield at issuance."""
    return all_in_rate_pct(rk_yield_pct, loan.bidrag_pct)


def flex_rate_pct(loan: RealkreditLoan, model: CurveModel) -> float:
    """All-in flex rate from the swap-curve par rate at the loan's reset tenor."""
    short = model.par_rate_pct(loan.flex_tenor_years)
    return all_in_rate_pct(short, loan.bidrag_pct)


def callable_buyback_gain(notional: float, coupon_pct: float,
                          current_yield_pct: float, remaining_years: float) -> float:
    """Approximate debt reduction from buying back a fixed callable bond below par.

    When market yields rise above the bond's coupon, its price falls below 100
    and the borrower can repay at that price ('konvertering'), cutting principal.
    Rough clean-price approximation: PV of the coupon gap over the remaining life.
    Returns 0 when the bond is at/above par (no buy-back gain).
    """
    if current_yield_pct <= coupon_pct:
        return 0.0
    gap = (current_yield_pct - coupon_pct) / 100.0
    # annuity-style discount of the coupon shortfall the buyer demands
    y = current_yield_pct / 100.0
    if y <= 0:
        annuity = remaining_years
    else:
        annuity = (1 - (1 + y) ** -remaining_years) / y
    discount_frac = min(0.6, gap * annuity)  # cap; clean price won't crater unboundedly
    return notional * discount_frac
