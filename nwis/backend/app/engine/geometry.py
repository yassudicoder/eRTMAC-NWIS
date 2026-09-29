"""
Wellbore geometry.

The look-ahead engine has to reason about depths the bit has not reached yet,
where no survey and no log exist.  That means converting measured depth to
true vertical depth from the *planned* trajectory rather than from data.
"""

from __future__ import annotations

import math


class Trajectory:
    """
    Build-and-hold trajectory: vertical to the kick-off point, build at a
    constant rate to the maximum inclination, then hold to TD.

    MD -> TVD and MD -> horizontal displacement are integrated once at
    construction into a lookup table and interpolated afterwards, so walking
    a well down is constant time per query.
    """

    STEP = 2.0
    MAX_MD = 9000.0

    def __init__(self, kop_md: float | None, build_rate: float,
                 max_inc: float, azimuth: float = 0.0):
        self.kop_md = kop_md
        self.build_rate = build_rate or 2.0   # degrees per 30 m
        self.max_inc = max_inc or 0.0
        self.azimuth = azimuth or 0.0
        self.build_length = (
            0.0 if not kop_md or self.build_rate <= 0
            else (self.max_inc / self.build_rate) * 30.0
        )

        n = int(self.MAX_MD / self.STEP) + 1
        self._tvd = [0.0] * n
        self._disp = [0.0] * n
        tvd = disp = 0.0
        for i in range(1, n):
            inc = math.radians(self.inclination_at((i - 0.5) * self.STEP))
            tvd += self.STEP * math.cos(inc)
            disp += self.STEP * math.sin(inc)
            self._tvd[i] = tvd
            self._disp[i] = disp

    @classmethod
    def from_well(cls, well: dict) -> "Trajectory":
        return cls(
            kop_md=well.get("kop_md_m"),
            build_rate=well.get("build_rate_deg_30m") or 2.0,
            max_inc=well.get("max_inclination_deg") or 0.0,
            azimuth=well.get("azimuth_deg") or 0.0,
        )

    def inclination_at(self, md: float) -> float:
        if self.kop_md is None or md <= self.kop_md:
            return 0.0
        into_build = md - self.kop_md
        if into_build >= self.build_length:
            return self.max_inc
        return self.build_rate * into_build / 30.0

    def _interpolate(self, table: list[float], md: float) -> float:
        if md <= 0:
            return 0.0
        pos = md / self.STEP
        i = int(pos)
        if i >= len(table) - 1:
            return table[-1]
        return table[i] + (pos - i) * (table[i + 1] - table[i])

    def tvd_at(self, md: float) -> float:
        return self._interpolate(self._tvd, md)

    def displacement_at(self, md: float) -> float:
        return self._interpolate(self._disp, md)

    def md_for_tvd(self, target_tvd: float) -> float:
        """Invert tvd_at by binary search over the precomputed table."""
        if self.kop_md is None:
            return target_tvd
        table = self._tvd
        if target_tvd >= table[-1]:
            return self.MAX_MD
        lo, hi = 0, len(table) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if table[mid] < target_tvd:
                lo = mid + 1
            else:
                hi = mid
        if lo == 0:
            return 0.0
        span = table[lo] - table[lo - 1]
        frac = 0.0 if span <= 0 else (target_tvd - table[lo - 1]) / span
        return (lo - 1 + frac) * self.STEP
