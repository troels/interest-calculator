"""Strategy comparison: stay in the swap vs. convert to fixed realkredit.

Compares, at a decision date, the total cost of:

  * **stay_swap**  — keep paying fixed (5.4%) + bank margin to maturity (bullet);
  * **fixed_10y / fixed_20y / fixed_30y** — pay the swap breakage now, then a
    fixed realkredit whose rate is anchored to the realised long-fixed realkredit
    rate (DST, incl. bidrag) and shaped across maturities by the swap curve;
  * **F5** (optional) — a 5-year-refixing realkredit at today's F5 rate.

**Amortization** is handled explicitly. Each strategy's cost is the PV of *all*
debt outflows over the swap's remaining horizon plus the residual balance still
owed at the end — so an amortizing loan (lower interest, but principal injected)
and an interest-only loan (afdragsfri) are compared fairly:

    total_pv = breakage + PV(interest) + PV(principal repaid) + PV(residual balance)

Future money is discounted on the swap curve by default, or at a flat
``discount_rate_pct`` (the borrower's cost of capital) if given.

Pure functions; data loading lives in the CLI/engine layer.
"""

from __future__ import annotations

from dataclasses import dataclass

from .curves import CurveModel
from .models import SwapContract
from .valuation.swap import SwapValuation, fixed_leg_schedule


@dataclass(frozen=True)
class StrategyCost:
    name: str
    breakage: float            # upfront switching cost (0 for stay)
    rate_pct: float            # all-in annual rate over the horizon
    interest_pv: float         # PV of interest paid over the horizon
    principal_pv: float        # PV of principal repaid over the horizon (amortization)
    residual_pv: float         # PV of balance still owed at the horizon
    interest_nominal: float    # undiscounted interest sum (for reference)

    @property
    def total_pv(self) -> float:
        return self.breakage + self.interest_pv + self.principal_pv + self.residual_pv

    def to_dict(self) -> dict:
        return {
            "name": self.name, "breakage": self.breakage, "rate_pct": self.rate_pct,
            "interest_pv": self.interest_pv, "principal_pv": self.principal_pv,
            "residual_pv": self.residual_pv, "interest_nominal": self.interest_nominal,
            "total_pv": self.total_pv,
        }


def discount_factor(model: CurveModel, t: float, discount_rate_pct: float | None) -> float:
    """DF for time ``t``: market swap curve, or a flat ``discount_rate_pct`` if given."""
    if discount_rate_pct is None:
        return model.discount_factor(t)
    return (1.0 + discount_rate_pct / 100.0) ** (-t)


def loan_cost(notional: float, rate_pct: float, model: CurveModel, horizon_years: float,
              *, amort_years: float | None = None, frequency: int = 1,
              discount_rate_pct: float | None = None) -> tuple[float, float, float, float]:
    """PV components of carrying ``notional`` over the horizon.

    Returns (interest_pv, principal_pv, residual_pv, interest_nominal). ``amort_years``
    None means interest-only (bullet); otherwise a level annuity over that term.
    """
    offsets, taus = fixed_leg_schedule(horizon_years, frequency)
    r = rate_pct / 100.0
    rp = r / frequency
    bal = notional
    interest_pv = principal_pv = interest_nom = 0.0

    level = None
    if amort_years:
        n = max(1, int(round(amort_years * frequency)))
        level = notional * rp / (1 - (1 + rp) ** -n) if rp > 0 else notional / n

    for tau, o in zip(taus, offsets):
        df = discount_factor(model, o, discount_rate_pct)
        interest = bal * r * tau
        principal = max(0.0, min(bal, (level - bal * rp) * tau)) if amort_years else 0.0
        interest_pv += interest * df
        principal_pv += principal * df
        interest_nom += interest
        bal -= principal

    residual_pv = bal * discount_factor(model, horizon_years, discount_rate_pct)
    return interest_pv, principal_pv, residual_pv, interest_nom


def fixed_rate_at_tenor(anchor_rate_pct: float, anchor_tenor: float,
                        tenor: float, model: CurveModel) -> float:
    """Fixed realkredit rate at a loan maturity: anchored level + swap-curve shape.

        rate(T) = anchor + (swap_par(T) - swap_par(anchor_tenor))
    """
    return anchor_rate_pct + (model.par_rate_pct(tenor) - model.par_rate_pct(anchor_tenor))


def compare_now(
    as_of,
    swap: SwapContract,
    swap_val: SwapValuation,
    model: CurveModel,
    anchor_fixed_rate_pct: float,
    *,
    anchor_tenor: float = 30.0,
    fixed_tenors: tuple[float, ...] = (10.0, 20.0, 30.0),
    bank_margin_pct: float = 0.5,
    amortize: bool = False,
    discount_rate_pct: float | None = None,
    flex_rate_pct: float | None = None,
    notional: float | None = None,
) -> dict:
    """Compare staying vs converting to fixed realkredit at several maturities.

    ``amortize`` False = interest-only (afdragsfri); True = annuity over each loan's
    term (fixed_<T>y amortizes over T; F5 over its loan term, default 30y). The swap
    'stay' leg is always bullet (the underlying swapped loan is interest-only).
    """
    N = notional if notional is not None else swap.notional
    horizon = swap.remaining_years(as_of)
    bk = swap_val.breakage

    def make(name, rate, breakage, amort_years):
        i_pv, p_pv, r_pv, i_nom = loan_cost(
            N, rate, model, horizon, amort_years=amort_years,
            discount_rate_pct=discount_rate_pct)
        return StrategyCost(name, breakage, rate, i_pv, p_pv, r_pv, i_nom)

    stay_rate = swap.fixed_rate_pct + bank_margin_pct
    strategies = [make("stay_swap", stay_rate, 0.0, None)]  # swap leg is bullet

    for T in fixed_tenors:
        r = fixed_rate_at_tenor(anchor_fixed_rate_pct, anchor_tenor, T, model)
        strategies.append(make(f"fixed_{int(T)}y", r, bk, T if amortize else None))

    if flex_rate_pct is not None:
        strategies.append(make("F5", flex_rate_pct, bk, 30.0 if amortize else None))

    # Break-even fixed rate (afdragsfri threshold): bullet convert ties bullet stay
    # (principal/residual cancel), so bk + N*x/100*annuity_pv = stay interest_pv.
    offsets, taus = fixed_leg_schedule(horizon)
    annuity_pv = sum(tau * discount_factor(model, o, discount_rate_pct)
                     for tau, o in zip(taus, offsets))
    stay_interest_pv = strategies[0].interest_pv
    break_even_pv = (stay_interest_pv - bk) * 100.0 / (N * annuity_pv) if annuity_pv else 0.0

    best = min(strategies, key=lambda s: s.total_pv)
    return {
        "as_of": as_of.isoformat(),
        "horizon_years": horizon,
        "breakage": bk,
        "amortize": amortize,
        "discount_basis": ("flat %.2f%%" % discount_rate_pct) if discount_rate_pct is not None else "swap curve",
        "strategies": [s.to_dict() for s in strategies],
        "break_even_rate_pct": break_even_pv,
        "anchor_fixed_pct": anchor_fixed_rate_pct,
        "best": best.name,
    }
