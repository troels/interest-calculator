"""Swap mark-to-market and breakage cost.

Values the user's pay-fixed swap against a bootstrapped curve. The payer pays
fixed ``s_contract`` and receives floating; the floating leg is valued off the
same swap curve (no separate CIBOR series).

MtM to the payer = PV(float received) - PV(fixed paid)::

    MtM = N * [ (1 - DF(T)) - s_contract * Σ τ_i·DF(t_i) ]
        = N * (s_market - s_contract) * Σ τ_i·DF(t_i)          (equivalent form)

With s_contract (5.4%) far above s_market (~3.2%), MtM is strongly negative and
the **breakage cost** the payer must pay to exit is ``-MtM`` (a positive number).

Simplification (v1): annual fixed leg, valuation assumed on a reset date, so the
floating leg PV is N·(1 - DF(T)). A non-reset (mid-period accrual) adjustment is
a documented future refinement; for whole-year remaining terms it is exact.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from datetime import date

from ..curves import CurveModel
from ..models import SwapContract


@dataclass(frozen=True)
class SwapValuation:
    as_of: date
    remaining_years: float
    notional: float
    fixed_rate_pct: float
    market_rate_pct: float        # par swap rate at the remaining tenor
    annuity: float                # Σ τ_i·DF(t_i)
    pv_fixed: float               # PV of fixed leg paid
    pv_float: float               # PV of floating leg received
    mtm: float                    # mark-to-market to the payer (negative here)
    breakage: float               # cost to exit = max(0, -mtm) for the payer

    def to_dict(self) -> dict:
        d = asdict(self)
        d["as_of"] = self.as_of.isoformat()
        return d


def fixed_leg_schedule(remaining_years: float, frequency: int = 1) -> tuple[list[float], list[float]]:
    """Return (offsets, accrual_fractions) for the remaining fixed-leg payments.

    Offsets are years from valuation to each payment (ascending, ending at the
    remaining tenor). The earliest period may be a stub if the remaining term is
    not a whole number of periods.
    """
    period = 1.0 / frequency
    n = int(math.ceil(remaining_years / period - 1e-9))
    offsets = [remaining_years - k * period for k in range(n)]
    offsets = sorted(o for o in offsets if o > 1e-9)
    taus: list[float] = []
    prev = 0.0
    for o in offsets:
        taus.append(o - prev)
        prev = o
    return offsets, taus


def value_swap(model: CurveModel, swap: SwapContract, as_of: date) -> SwapValuation:
    """Value ``swap`` against the bootstrapped ``model`` as of ``as_of``."""
    T = swap.remaining_years(as_of)
    offsets, taus = fixed_leg_schedule(T, swap.fixed_frequency)
    if not offsets:
        # already matured
        return SwapValuation(as_of, 0.0, swap.notional, swap.fixed_rate_pct,
                             model.par_rate_pct(0.0), 0.0, 0.0, 0.0, 0.0, 0.0)

    dfs = [model.discount_factor(o) for o in offsets]
    annuity = sum(tau * df for tau, df in zip(taus, dfs))
    df_T = dfs[-1]

    s_con = swap.fixed_rate_pct / 100.0
    pv_fixed = swap.notional * s_con * annuity
    pv_float = swap.notional * (1.0 - df_T)

    # Payer MtM; flip the leg roles for a receiver swap.
    if swap.pay_fixed:
        mtm = pv_float - pv_fixed
    else:
        mtm = pv_fixed - pv_float

    s_market = (1.0 - df_T) / annuity * 100.0 if annuity else 0.0
    breakage = max(0.0, -mtm)

    return SwapValuation(
        as_of=as_of,
        remaining_years=T,
        notional=swap.notional,
        fixed_rate_pct=swap.fixed_rate_pct,
        market_rate_pct=s_market,
        annuity=annuity,
        pv_fixed=pv_fixed,
        pv_float=pv_float,
        mtm=mtm,
        breakage=breakage,
    )
