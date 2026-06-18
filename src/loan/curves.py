"""Curve math: interpolation, extrapolation, and discount-factor bootstrapping.

Operates on a :class:`loan.models.Curve` (produced by the data layer). The curve
holds par swap rates (annual-pay, 30/360 convention assumed). From those we:

  * interpolate par rates to any tenor (linear between pillars);
  * extrapolate beyond the last pillar with a linear fit to the tail — dfbf only
    publishes 2..10Y, but the swap runs 12Y, so 11Y/12Y must be extended. This
    reproduces the method noted in the reference txt (regression on the 7-10Y
    tail, slope ~0.0445/yr, anchored at the last pillar for continuity);
  * bootstrap annual discount factors DF(t) from the par curve.

Bootstrap relation for an annual par swap rate ``s`` at integer maturity ``n``::

    s * Σ_{i=1..n} DF(i) = 1 - DF(n)
    =>  DF(n) = (1 - s * Σ_{i=1..n-1} DF(i)) / (1 + s)
"""

from __future__ import annotations

import math

import numpy as np

from .models import Curve


class CurveModel:
    """Interpolatable, bootstrappable view over a par swap-rate :class:`Curve`."""

    def __init__(self, curve: Curve, *, tail_n: int = 4) -> None:
        self.curve = curve
        self._tenors = np.asarray(curve.tenors, dtype=float)
        self._rates = np.asarray(curve.rates, dtype=float)  # percent

        # Tail slope for long-end extrapolation: fit a line to the last `tail_n`
        # *original* (non-extrapolated) points and extend from the last pillar.
        orig = [(p.tenor_years, p.rate_pct) for p in curve.points if not p.extrapolated]
        orig.sort()
        tail = orig[-tail_n:] if len(orig) >= 2 else orig
        if len(tail) >= 2:
            xs = np.array([t for t, _ in tail])
            ys = np.array([r for _, r in tail])
            self._tail_slope = float(np.polyfit(xs, ys, 1)[0])
        else:
            self._tail_slope = 0.0
        self._last_tenor = float(self._tenors[-1])
        self._last_rate = float(self._rates[-1])

        self._df: dict[int, float] = {0: 1.0}
        self._max_boot = 0

    # ------------------------------------------------------------------ rates
    def par_rate_pct(self, t_years: float) -> float:
        """Par swap rate (percent) at tenor ``t_years`` (inter/extrapolated)."""
        tn, r = self._tenors, self._rates
        if t_years <= tn[0]:
            if len(tn) >= 2:
                slope = (r[1] - r[0]) / (tn[1] - tn[0])
                return float(r[0] + slope * (t_years - tn[0]))
            return float(r[0])
        if t_years >= self._last_tenor:
            # anchored linear extrapolation -> continuous at the last pillar
            return self._last_rate + self._tail_slope * (t_years - self._last_tenor)
        return float(np.interp(t_years, tn, r))

    # -------------------------------------------------------- discount factors
    def _ensure_bootstrapped(self, n: int) -> None:
        for k in range(self._max_boot + 1, n + 1):
            s = self.par_rate_pct(k) / 100.0
            prev_annuity = sum(self._df[i] for i in range(1, k))  # DF(1..k-1)
            self._df[k] = (1.0 - s * prev_annuity) / (1.0 + s)
        self._max_boot = max(self._max_boot, n)

    def discount_factor(self, t_years: float) -> float:
        """DF(t). Integer years are bootstrapped; fractional years log-linear."""
        if t_years <= 0:
            return 1.0
        hi = int(math.ceil(t_years))
        self._ensure_bootstrapped(hi)
        if math.isclose(t_years, round(t_years)):
            return self._df[int(round(t_years))]
        lo = int(math.floor(t_years))
        f = t_years - lo
        df_lo = self._df[lo] if lo > 0 else 1.0
        return df_lo ** (1 - f) * self._df[hi] ** f  # log-linear in DF

    def annuity(self, t_years: float) -> float:
        """Σ DF(i) over annual fixed-leg dates i = 1..round(t_years)."""
        n = int(round(t_years))
        if n <= 0:
            return 0.0
        self._ensure_bootstrapped(n)
        return sum(self._df[i] for i in range(1, n + 1))

    def pv01(self, t_years: float) -> float:
        """Value change of the fixed annuity for a 1bp rate move (per unit notional)."""
        return self.annuity(t_years) * 1e-4

    def implied_par_rate_pct(self, n: int) -> float:
        """Re-derive the par rate at integer ``n`` from bootstrapped DFs (round-trip check)."""
        self._ensure_bootstrapped(n)
        annuity = sum(self._df[i] for i in range(1, n + 1))
        return (1.0 - self._df[n]) / annuity * 100.0
