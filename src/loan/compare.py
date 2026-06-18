"""Strategy comparison: stay in the swap vs. convert to realkredit.

Given a decision date, compares the total remaining cost of:

  * **stay_swap**     — keep paying fixed (5.4%) + bank margin to maturity;
  * **convert_fixed** — pay the swap breakage now, then a fixed realkredit rate
    (DST yield + bidrag) to maturity;
  * **convert_flex**  — pay breakage now, then a flex rate (swap short end +
    bidrag), held flat at today's level for this *current-decision* view.

Costs are reported both nominal (undiscounted interest sum) and PV (discounted
on the swap curve). The break-even realkredit rate is the fixed rate at which
converting-now ties with staying — compare it to the rate actually on offer.

These are pure functions; data loading (DB, curve) lives in the CLI layer.
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
    rate_pct: float            # the all-in annual rate applied over the horizon
    interest_nominal: float    # undiscounted sum of interest
    interest_pv: float         # discounted interest

    @property
    def total_nominal(self) -> float:
        return self.breakage + self.interest_nominal

    @property
    def total_pv(self) -> float:
        return self.breakage + self.interest_pv

    def to_dict(self) -> dict:
        return {
            "name": self.name, "breakage": self.breakage, "rate_pct": self.rate_pct,
            "interest_nominal": self.interest_nominal, "interest_pv": self.interest_pv,
            "total_nominal": self.total_nominal, "total_pv": self.total_pv,
        }


def remaining_interest(notional: float, rate_pct: float, model: CurveModel,
                       horizon_years: float, frequency: int = 1) -> tuple[float, float]:
    """(nominal, PV) interest for a bullet loan at a constant rate over the horizon."""
    offsets, taus = fixed_leg_schedule(horizon_years, frequency)
    per = notional * rate_pct / 100.0
    nominal = sum(per * tau for tau in taus)
    pv = sum(per * tau * model.discount_factor(o) for tau, o in zip(taus, offsets))
    return nominal, pv


def compare_now(
    as_of,
    swap: SwapContract,
    swap_val: SwapValuation,
    model: CurveModel,
    rk_fixed_rate_pct: float,
    *,
    bank_margin_pct: float = 0.5,
    rk_flex_rate_pct: float | None = None,
    notional: float | None = None,
) -> dict:
    """Compare strategies as of ``as_of``. Returns strategies + break-even + best.

    ``rk_fixed_rate_pct`` / ``rk_flex_rate_pct`` are **all-in** effective realkredit
    rates (bidrag already included), e.g. from DST DNRNURI.
    """
    N = notional if notional is not None else swap.notional
    horizon = swap.remaining_years(as_of)

    # Stay: swap fixed + bank margin, no breakage.
    stay_rate = swap.fixed_rate_pct + bank_margin_pct
    stay_nom, stay_pv = remaining_interest(N, stay_rate, model, horizon)
    strategies = [StrategyCost("stay_swap", 0.0, stay_rate, stay_nom, stay_pv)]

    # Convert to fixed realkredit (rate already all-in).
    bk = swap_val.breakage
    f_nom, f_pv = remaining_interest(N, rk_fixed_rate_pct, model, horizon)
    strategies.append(StrategyCost("convert_fixed", bk, rk_fixed_rate_pct, f_nom, f_pv))

    # Convert to flex realkredit (rate held flat at today's level for this view).
    if rk_flex_rate_pct is not None:
        x_nom, x_pv = remaining_interest(N, rk_flex_rate_pct, model, horizon)
        strategies.append(StrategyCost("convert_flex", bk, rk_flex_rate_pct, x_nom, x_pv))

    # Break-even fixed realkredit rate: where convert_fixed total ties stay total.
    # PV basis: bk + N*x/100*annuity_pv = stay_pv  =>  x = (stay_pv - bk)*100 / (N*annuity_pv)
    offsets, taus = fixed_leg_schedule(horizon)
    annuity_pv = sum(tau * model.discount_factor(o) for tau, o in zip(taus, offsets))
    break_even_pv = (stay_pv - bk) * 100.0 / (N * annuity_pv) if annuity_pv else 0.0

    best = min(strategies, key=lambda s: s.total_pv)
    return {
        "as_of": as_of.isoformat(),
        "horizon_years": horizon,
        "breakage": bk,
        "strategies": [s.to_dict() for s in strategies],
        "break_even_rate_pct": break_even_pv,
        "current_rk_fixed_pct": rk_fixed_rate_pct,
        "best": best.name,
    }
