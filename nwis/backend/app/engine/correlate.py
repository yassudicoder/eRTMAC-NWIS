"""
Cross-well depth and formation correlation.

Two wells a few kilometres apart do not see the same rock at the same depth.
On the Upper Assam shelf the section dips to the south-east and is broken by
NE-SW faults, so the Barail pay can sit 300 m deeper in a well 4 km away.
Comparing raw measured depths between wells is therefore meaningless, and
warning a driller about "mud loss at 2,840 m because the offset well lost mud
at 2,840 m" is how you produce an alert nobody trusts.

NWIS correlates on picked formation tops instead.  Shared tops become tie
points, and depth is mapped between wells by piecewise-linear interpolation
between those ties - the same construction a geologist makes by hand when
flattening a correlation panel on a marker horizon.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..assam_geology import FORMATION_ORDER

# Outside the tied interval, depth is extrapolated using the nearest segment's
# compaction gradient.  Clamped, because extrapolating a steep local gradient
# a long way below the deepest shared top produces nonsense.
MIN_GRADIENT = 0.70
MAX_GRADIENT = 1.45


@dataclass(frozen=True)
class TiePoint:
    formation: str
    reference_tvd_m: float   # depth in the well we are correlating *from*
    target_tvd_m: float      # depth of the same top in the well we map *to*

    @property
    def shift_m(self) -> float:
        return self.target_tvd_m - self.reference_tvd_m


@dataclass
class Correlation:
    """A depth mapping between two wells, built from their shared tops."""

    reference_well: str
    target_well: str
    ties: list[TiePoint]
    quality: float           # 0..1, how much to trust the mapping
    shared_formations: list[str]
    mean_shift_m: float
    shift_spread_m: float

    def to_target(self, reference_tvd: float) -> float:
        """Map a TVD in the reference well onto the equivalent TVD in the target."""
        if not self.ties:
            return reference_tvd
        if len(self.ties) == 1:
            return reference_tvd + self.ties[0].shift_m

        ties = self.ties
        if reference_tvd <= ties[0].reference_tvd_m:
            grad = _gradient(ties[0], ties[1])
            return ties[0].target_tvd_m - (ties[0].reference_tvd_m - reference_tvd) * grad
        if reference_tvd >= ties[-1].reference_tvd_m:
            grad = _gradient(ties[-2], ties[-1])
            return ties[-1].target_tvd_m + (reference_tvd - ties[-1].reference_tvd_m) * grad

        for lo, hi in zip(ties, ties[1:]):
            if lo.reference_tvd_m <= reference_tvd <= hi.reference_tvd_m:
                span = hi.reference_tvd_m - lo.reference_tvd_m
                if span <= 0:
                    return lo.target_tvd_m
                frac = (reference_tvd - lo.reference_tvd_m) / span
                return lo.target_tvd_m + frac * (hi.target_tvd_m - lo.target_tvd_m)
        return reference_tvd

    def to_reference(self, target_tvd: float) -> float:
        """The inverse mapping, target well back onto the reference well."""
        if not self.ties:
            return target_tvd
        if len(self.ties) == 1:
            return target_tvd - self.ties[0].shift_m

        ties = self.ties
        if target_tvd <= ties[0].target_tvd_m:
            grad = _gradient(ties[0], ties[1])
            return ties[0].reference_tvd_m - (ties[0].target_tvd_m - target_tvd) / max(grad, 1e-6)
        if target_tvd >= ties[-1].target_tvd_m:
            grad = _gradient(ties[-2], ties[-1])
            return ties[-1].reference_tvd_m + (target_tvd - ties[-1].target_tvd_m) / max(grad, 1e-6)

        for lo, hi in zip(ties, ties[1:]):
            if lo.target_tvd_m <= target_tvd <= hi.target_tvd_m:
                span = hi.target_tvd_m - lo.target_tvd_m
                if span <= 0:
                    return lo.reference_tvd_m
                frac = (target_tvd - lo.target_tvd_m) / span
                return lo.reference_tvd_m + frac * (hi.reference_tvd_m - lo.reference_tvd_m)
        return target_tvd

    def window_to_target(self, tvd_from: float, tvd_to: float) -> tuple[float, float]:
        lo = self.to_target(min(tvd_from, tvd_to))
        hi = self.to_target(max(tvd_from, tvd_to))
        return (min(lo, hi), max(lo, hi))

    def as_dict(self) -> dict:
        return {
            "reference_well": self.reference_well,
            "target_well": self.target_well,
            "quality": round(self.quality, 3),
            "shared_formations": self.shared_formations,
            "tie_count": len(self.ties),
            "mean_shift_m": round(self.mean_shift_m, 1),
            "shift_spread_m": round(self.shift_spread_m, 1),
            "ties": [
                {
                    "formation": t.formation,
                    "reference_tvd_m": round(t.reference_tvd_m, 1),
                    "target_tvd_m": round(t.target_tvd_m, 1),
                    "shift_m": round(t.shift_m, 1),
                }
                for t in self.ties
            ],
        }


def _gradient(lo: TiePoint, hi: TiePoint) -> float:
    ref_span = hi.reference_tvd_m - lo.reference_tvd_m
    tgt_span = hi.target_tvd_m - lo.target_tvd_m
    if ref_span <= 0:
        return 1.0
    return max(MIN_GRADIENT, min(MAX_GRADIENT, tgt_span / ref_span))


def build_correlation(reference_well: str, reference_tops: Sequence[dict],
                      target_well: str, target_tops: Sequence[dict]) -> Correlation:
    """
    Tie two wells together on the formation tops they both penetrated.

    Quality falls when there are few ties, and when the tie shifts disagree
    with each other - a large spread means the two wells are not in the same
    structural block, so any mapping between them is shaky and downstream
    alerts should say so rather than pretend otherwise.
    """
    ref_by_name = {t["formation"]: float(t["top_tvd_m"]) for t in reference_tops}
    tgt_by_name = {t["formation"]: float(t["top_tvd_m"]) for t in target_tops}
    shared = sorted(
        set(ref_by_name) & set(tgt_by_name),
        key=lambda n: FORMATION_ORDER.get(n, 99),
    )

    ties = [TiePoint(n, ref_by_name[n], tgt_by_name[n]) for n in shared]
    ties.sort(key=lambda t: t.reference_tvd_m)
    # Guard against a bad pick inverting the mapping.
    ties = _enforce_monotonic(ties)

    shifts = [t.shift_m for t in ties]
    mean_shift = sum(shifts) / len(shifts) if shifts else 0.0
    spread = (
        (sum((s - mean_shift) ** 2 for s in shifts) / len(shifts)) ** 0.5
        if len(shifts) > 1 else 0.0
    )

    # More ties is better; disagreement between them is worse.
    count_term = min(1.0, len(ties) / 5.0)
    consistency_term = 1.0 / (1.0 + (spread / 90.0) ** 2)
    quality = round(count_term * consistency_term, 3) if ties else 0.0

    return Correlation(
        reference_well=reference_well,
        target_well=target_well,
        ties=ties,
        quality=quality,
        shared_formations=[t.formation for t in ties],
        mean_shift_m=mean_shift,
        shift_spread_m=spread,
    )


def _enforce_monotonic(ties: list[TiePoint]) -> list[TiePoint]:
    """Drop ties whose target depth goes backwards, keeping the mapping sane."""
    kept: list[TiePoint] = []
    for t in ties:
        if kept and t.target_tvd_m <= kept[-1].target_tvd_m:
            continue
        kept.append(t)
    return kept


def column_similarity(tops_a: Sequence[dict], tops_b: Sequence[dict]) -> tuple[float, dict]:
    """
    How alike two wells' geological columns are, in [0, 1].

    Two independent questions, because they fail independently:

      overlap     - did they drill the same units at all?
      conformance - once a constant structural shift is removed, do the tops
                    track each other?  A well 300 m downthrown but otherwise
                    parallel is an excellent analogue; a well whose section
                    thickens and thins erratically is not.
    """
    a = {t["formation"]: float(t["top_tvd_m"]) for t in tops_a}
    b = {t["formation"]: float(t["top_tvd_m"]) for t in tops_b}
    shared = set(a) & set(b)
    union = set(a) | set(b)
    if not union:
        return 0.0, {"overlap": 0.0, "conformance": 0.0, "shared": 0}

    overlap = len(shared) / len(union)
    if len(shared) < 2:
        conformance = 0.35 if shared else 0.0
        spread = 0.0
    else:
        residuals = [b[n] - a[n] for n in shared]
        mean = sum(residuals) / len(residuals)
        spread = (sum((r - mean) ** 2 for r in residuals) / len(residuals)) ** 0.5
        # 40 m of scatter is a good correlation; 150 m is a different block.
        conformance = 1.0 / (1.0 + (spread / 70.0) ** 2)

    score = 0.45 * overlap + 0.55 * conformance
    return round(score, 4), {
        "overlap": round(overlap, 3),
        "conformance": round(conformance, 3),
        "shared": len(shared),
        "shift_spread_m": round(spread, 1),
    }


def interval_overlap(a_lo: float, a_hi: float, b_lo: float, b_hi: float) -> float:
    """Length of the overlap between two depth intervals, never negative."""
    return max(0.0, min(a_hi, b_hi) - max(a_lo, b_lo))
