"""
Pore-pressure and overpressure prognosis from offset wells.

"Overpressure zones" is one of the risks the problem statement names, and it
is the one that cannot be answered by counting past events: an overpressured
interval is dangerous even in a well where nothing went wrong, because the
previous crew carried enough mud weight to keep it quiet.

The evidence for it is therefore not the event record but the *mud weight*
the offsets actually carried. Mud weight is the industry's working proxy for
pore pressure - a crew that raised the mud to 1.42 sg through the Kopili was
telling you, in the only units that matter on a rig, what that shale was
doing. NWIS reads those mud weights back out of the offset logs, maps them
onto the current well through the same formation ties the rest of the engine
uses, and compares them with what the current well is carrying now.

Two things come out of that:

  * a predicted pore-pressure and mud-weight profile for the interval ahead,
    built from real offsets rather than from a regional curve, and
  * an explicit overpressure warning when the offsets carried materially more
    mud than this well is carrying, or when the window between pore pressure
    and fracture gradient is about to become too narrow to drill safely.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from ..assam_geology import FORMATION_BY_NAME

# How much more mud weight the offsets must have carried before this is worth
# saying out loud, in sg. Below this it is noise between mud engineers.
MUD_WEIGHT_GAP_SG = 0.06

# A drilling window narrower than this is uncomfortable; narrower than the
# critical value, a kick and losses can happen in the same stand.
WINDOW_TIGHT_SG = 0.22
WINDOW_CRITICAL_SG = 0.12


@dataclass
class PressurePoint:
    """One depth on the prognosis, in the current well's frame."""

    tvd_m: float
    md_m: float
    formation: str | None
    # What the offsets carried at the equivalent depth, relevance-weighted.
    offset_mud_weight_sg: float | None
    offset_max_mud_weight_sg: float | None
    contributing_wells: int
    # Regional model values for the same depth.
    model_pore_pressure_sg: float | None
    model_frac_gradient_sg: float | None
    # What this well is carrying now, where it has been drilled.
    current_mud_weight_sg: float | None

    def as_dict(self) -> dict:
        return {
            "tvd_m": round(self.tvd_m, 1),
            "md_m": round(self.md_m, 1),
            "formation": self.formation,
            "offset_mud_weight_sg": (round(self.offset_mud_weight_sg, 3)
                                     if self.offset_mud_weight_sg is not None else None),
            "offset_max_mud_weight_sg": (round(self.offset_max_mud_weight_sg, 3)
                                         if self.offset_max_mud_weight_sg is not None else None),
            "contributing_wells": self.contributing_wells,
            "model_pore_pressure_sg": self.model_pore_pressure_sg,
            "model_frac_gradient_sg": self.model_frac_gradient_sg,
            "current_mud_weight_sg": (round(self.current_mud_weight_sg, 3)
                                      if self.current_mud_weight_sg is not None else None),
        }


@dataclass
class OverpressureFinding:
    """An interval ahead where the offsets say more mud will be needed."""

    tvd_from: float
    tvd_to: float
    md_from: float
    md_to: float
    formation: str | None
    current_mud_weight_sg: float
    required_mud_weight_sg: float
    gap_sg: float
    window_sg: float
    severity: int
    contributing_wells: list[dict]
    reason: str

    def as_dict(self) -> dict:
        return {
            "tvd_from": round(self.tvd_from, 1),
            "tvd_to": round(self.tvd_to, 1),
            "md_from": round(self.md_from, 1),
            "md_to": round(self.md_to, 1),
            "formation": self.formation,
            "current_mud_weight_sg": round(self.current_mud_weight_sg, 3),
            "required_mud_weight_sg": round(self.required_mud_weight_sg, 3),
            "gap_sg": round(self.gap_sg, 3),
            "window_sg": round(self.window_sg, 3),
            "severity": self.severity,
            "contributing_wells": self.contributing_wells,
            "reason": self.reason,
        }


def build_prognosis(store: Any, well: dict, trajectory: Any,
                    offsets: Sequence[Any], tvd_from: float, tvd_to: float,
                    step_m: float = 25.0) -> list[PressurePoint]:
    """
    Predict the mud weight the interval ahead will need, from the offsets.

    Each offset's log is sampled at the depth that correlates to the depth of
    interest here, weighted by that offset's relevance, so a highly relevant
    well on the same structural block counts for more than a marginal one.
    """
    if tvd_to <= tvd_from:
        return []

    # Pull each offset's log once and index it by TVD.
    logs: dict[str, list[dict]] = {}
    for off in offsets:
        wid = off.well["well_id"]
        rows = store.drilling_log(wid, every=2)
        if rows:
            logs[wid] = rows

    picked = store.tops(well["well_id"])
    current_log = store.drilling_log(well["well_id"], every=2)

    def formation_at(tvd: float) -> str | None:
        hit = None
        for t in picked:
            if tvd >= t["top_tvd_m"]:
                hit = t["formation"]
            else:
                break
        return hit

    def current_mw(tvd: float) -> float | None:
        best = None
        for row in current_log:
            if abs(row["tvd_m"] - tvd) < 30.0:
                best = row["mud_weight_sg"]
        return best

    points: list[PressurePoint] = []
    tvd = tvd_from
    while tvd <= tvd_to + 1e-6:
        weighted_sum = 0.0
        weight_total = 0.0
        highest = None
        contributors = 0

        for off in offsets:
            wid = off.well["well_id"]
            rows = logs.get(wid)
            corr = off.correlation
            if not rows or corr is None:
                continue
            mapped = corr.to_target(tvd)
            # Nearest logged sample to the correlated depth.
            nearest = min(rows, key=lambda r: abs(r["tvd_m"] - mapped))
            if abs(nearest["tvd_m"] - mapped) > 40.0:
                continue
            mw = nearest["mud_weight_sg"]
            w = max(0.05, off.score)
            weighted_sum += mw * w
            weight_total += w
            highest = mw if highest is None else max(highest, mw)
            contributors += 1

        fname = formation_at(tvd)
        formation = FORMATION_BY_NAME.get(fname or "")
        points.append(PressurePoint(
            tvd_m=tvd,
            md_m=trajectory.md_for_tvd(tvd),
            formation=fname,
            offset_mud_weight_sg=(weighted_sum / weight_total) if weight_total else None,
            offset_max_mud_weight_sg=highest,
            contributing_wells=contributors,
            model_pore_pressure_sg=formation.pore_pressure_sg if formation else None,
            model_frac_gradient_sg=formation.frac_gradient_sg if formation else None,
            current_mud_weight_sg=current_mw(tvd),
        ))
        tvd += step_m

    return points


def find_overpressure(points: Sequence[PressurePoint], offsets: Sequence[Any],
                      current_mud_weight: float | None) -> list[OverpressureFinding]:
    """
    Where the interval ahead needs more mud than the well is carrying.

    Contiguous depths that trigger are merged into one finding, because a
    driller wants "raise mud weight before 2,780 m", not thirty consecutive
    alerts twenty-five metres apart.
    """
    if current_mud_weight is None:
        return []

    flagged: list[PressurePoint] = []
    findings: list[OverpressureFinding] = []

    def close(run: list[PressurePoint]) -> None:
        if not run:
            return
        required = max(p.offset_max_mud_weight_sg or 0.0 for p in run)
        gap = required - current_mud_weight
        frac = min((p.model_frac_gradient_sg or 99.0) for p in run)
        window = frac - required
        formations = [p.formation for p in run if p.formation]
        formation = max(set(formations), key=formations.count) if formations else None

        if window <= WINDOW_CRITICAL_SG:
            severity, reason = 5, (
                f"Offsets needed {required:.2f} sg here, leaving only {window:.2f} sg to the "
                f"fracture gradient - the window between a kick and losing returns is "
                f"nearly closed")
        elif window <= WINDOW_TIGHT_SG:
            severity, reason = 4, (
                f"Offsets carried up to {required:.2f} sg against a fracture gradient of "
                f"{frac:.2f} sg - a narrow {window:.2f} sg drilling window")
        elif gap >= 0.15:
            severity, reason = 4, (
                f"Offsets carried {required:.2f} sg through this interval, {gap:.2f} sg more "
                f"than the {current_mud_weight:.2f} sg in the hole now")
        else:
            severity, reason = 3, (
                f"Offsets carried {required:.2f} sg through this interval, {gap:.2f} sg above "
                f"the current {current_mud_weight:.2f} sg")

        contributors = [
            {
                "well_id": o.well["well_id"],
                "well_name": o.well["well_name"],
                "relevance_score": round(o.score, 3),
                "distance_km": round(o.distance_km, 2),
            }
            for o in sorted(offsets, key=lambda x: -x.score)[:4]
        ]

        findings.append(OverpressureFinding(
            tvd_from=run[0].tvd_m,
            tvd_to=run[-1].tvd_m,
            md_from=run[0].md_m,
            md_to=run[-1].md_m,
            formation=formation,
            current_mud_weight_sg=current_mud_weight,
            required_mud_weight_sg=required,
            gap_sg=gap,
            window_sg=window,
            severity=severity,
            contributing_wells=contributors,
            reason=reason,
        ))

    for p in points:
        needed = p.offset_max_mud_weight_sg
        tight = (needed is not None and p.model_frac_gradient_sg is not None
                 and p.model_frac_gradient_sg - needed <= WINDOW_TIGHT_SG)
        if p.contributing_wells >= 2 and needed is not None and (
                needed - current_mud_weight >= MUD_WEIGHT_GAP_SG or tight):
            flagged.append(p)
        else:
            close(flagged)
            flagged = []
    close(flagged)

    return findings
