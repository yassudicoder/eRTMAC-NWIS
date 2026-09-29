"""
Look-ahead risk detection.

The question this answers is the one a drilling engineer actually asks:

    "The bit is at 2,572 m. What went wrong in the next 300 m, in the wells
     that are genuinely comparable to this one, and how do I know?"

The chain is:

  1. Take the interval ahead of the bit, in TVD.
  2. Rank offset wells for *that interval* - relevance is depth-specific, so a
     well that stopped above the interval scores low however close it is.
  3. Map the interval into each offset well through its formation ties, so we
     compare rock to rock rather than metre to metre.
  4. Collect the events that fall in the mapped interval.
  5. Group them by hazard, and score each group on how much relevant,
     corroborating, severe history stands behind it.
  6. Fold in what the current well's own parameters are doing right now.
  7. Emit an alert carrying its evidence: which wells, which events, which
     document, which page.

Nothing is asserted that cannot be traced to a line in a report.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..assam_geology import EVENT_LABELS, FORMATION_BY_NAME
from .correlate import Correlation
from .geometry import Trajectory
from .relevance import OffsetWell, RelevanceWeights, rank_offsets
from .signals import Signal, analyse, boost_for

# Default distance ahead of the bit to look, in measured depth.
DEFAULT_LOOKAHEAD_M = 300.0

# An offset event counts towards the interval if it maps inside it, or within
# this tolerance of it - formation picks and event depths both carry error.
DEPTH_TOLERANCE_M = 45.0

# How far behind the bit a correlated event may still land and be counted.
# Small and non-zero: correlation error is real, but anything meaningfully
# above the bit is rock this well has already drilled without incident.
BEHIND_BIT_TOLERANCE_M = 15.0

# Offsets below this relevance are not allowed to raise an alert at all.
MIN_CONTRIBUTING_RELEVANCE = 0.30

BAND_HIGH = 0.62
BAND_MEDIUM = 0.35


# --------------------------------------------------------------------------
# Recommended actions
# --------------------------------------------------------------------------

# Standing guidance per hazard.  The *first* recommendation an alert shows is
# always what the offset crews actually did, lifted from their reports; this
# library backs that up with the general practice for the hazard.
ACTION_LIBRARY: dict[str, list[str]] = {
    "MUD_LOSS": [
        "Have LCM material mixed and ready before entering the interval.",
        "Reduce ECD: cut flow rate on approach and break circulation slowly.",
        "Keep the trip tank lined up and monitor returns continuously.",
        "Review the mud weight - carry the minimum that keeps the hole stable.",
    ],
    "STUCK_PIPE": [
        "Keep the string moving; avoid leaving it stationary in the interval.",
        "Have a pipe-release / spotting pill available on the rig floor.",
        "Work the string on connections and monitor overpull trend.",
        "Consider reducing differential pressure across the permeable section.",
    ],
    "KICK": [
        "Confirm kill sheet is current and BOP function-tested before entering the interval.",
        "Hold a pre-entry well-control drill with the crew.",
        "Monitor pit levels and flow-out closely; slow down on gas increases.",
        "Verify mud weight against the expected pore pressure for the interval.",
    ],
    "WELLBORE_INSTABILITY": [
        "Raise inhibition and review mud weight before entering the interval.",
        "Plan hole-cleaning sweeps and watch cavings volume at the shakers.",
        "Minimise time with the hole open across the unstable section.",
        "Avoid excessive surge and swab on trips.",
    ],
    "TIGHT_HOLE": [
        "Plan a wiper trip before and after the interval.",
        "Ream carefully through the interval rather than forcing ahead.",
        "Monitor drag trend on connections.",
    ],
    "HIGH_TORQUE_DRAG": [
        "Add lubricant to the mud system before entering the interval.",
        "Optimise hole cleaning; circulate sweeps regularly.",
        "Watch for a rising torque baseline as an early pack-off warning.",
    ],
    "BIT_BALLING": [
        "Increase flow rate and consider detergent in the system.",
        "Avoid excessive WOB in the reactive section.",
    ],
    "WASHOUT": [
        "Allow for excess hole volume in the cement programme.",
        "Consider reducing flow rate through the unconsolidated section.",
    ],
    "LOW_ROP": [
        "Plan the bit programme for the harder section; review WOB/RPM.",
        "Consider a bit change before entering the interval to avoid a mid-section trip.",
    ],
    "EQUIPMENT_FAILURE": [
        "Verify critical equipment and spares before entering the interval.",
    ],
}


# --------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------


@dataclass
class ContributingEvent:
    well_id: str
    well_name: str
    relevance_score: float
    distance_km: float
    correlation_quality: float
    event_id: int
    severity: int
    confidence: float
    npt_hours: float
    formation: str | None
    event_date: str | None
    magnitude: dict
    remedial_action: str | None
    # Depth in the offset well, and where that maps to in the current well.
    offset_tvd_m: float
    offset_md_m: float
    projected_tvd_m: float
    projected_md_m: float
    citations: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "well_id": self.well_id,
            "well_name": self.well_name,
            "relevance_score": round(self.relevance_score, 3),
            "distance_km": round(self.distance_km, 2),
            "correlation_quality": round(self.correlation_quality, 3),
            "event_id": self.event_id,
            "severity": self.severity,
            "confidence": round(self.confidence, 3),
            "npt_hours": self.npt_hours,
            "formation": self.formation,
            "event_date": self.event_date,
            "magnitude": self.magnitude,
            "remedial_action": self.remedial_action,
            "offset_tvd_m": round(self.offset_tvd_m, 1),
            "offset_md_m": round(self.offset_md_m, 1),
            "projected_tvd_m": round(self.projected_tvd_m, 1),
            "projected_md_m": round(self.projected_md_m, 1),
            "citations": self.citations,
        }


@dataclass
class RiskAlert:
    alert_id: str
    event_type: str
    label: str
    risk_score: float
    risk_band: str
    confidence: float
    formation: str | None
    predicted_md_from: float
    predicted_md_to: float
    predicted_tvd_from: float
    predicted_tvd_to: float
    metres_ahead: float
    contributing: list[ContributingEvent]
    why: list[str]
    recommended_actions: list[str]
    live_signals: list[Signal] = field(default_factory=list)
    live_boost: float = 0.0

    @property
    def well_count(self) -> int:
        return len({c.well_id for c in self.contributing})

    @property
    def evidence_count(self) -> int:
        return sum(len(c.citations) for c in self.contributing)

    def as_dict(self) -> dict:
        return {
            "alert_id": self.alert_id,
            "event_type": self.event_type,
            "label": self.label,
            "risk_score": round(self.risk_score, 3),
            "risk_band": self.risk_band,
            "confidence": round(self.confidence, 3),
            "formation": self.formation,
            "predicted_md_from": round(self.predicted_md_from, 1),
            "predicted_md_to": round(self.predicted_md_to, 1),
            "predicted_tvd_from": round(self.predicted_tvd_from, 1),
            "predicted_tvd_to": round(self.predicted_tvd_to, 1),
            "metres_ahead": round(self.metres_ahead, 1),
            "well_count": self.well_count,
            "event_count": len(self.contributing),
            "evidence_count": self.evidence_count,
            "total_npt_hours": round(sum(c.npt_hours or 0.0 for c in self.contributing), 1),
            "why": self.why,
            "recommended_actions": self.recommended_actions,
            "live_boost": round(self.live_boost, 3),
            "live_signals": [s.as_dict() for s in self.live_signals],
            "contributing": [c.as_dict() for c in self.contributing],
        }


@dataclass
class LookAheadResult:
    well: dict
    bit_md_m: float
    bit_tvd_m: float
    lookahead_m: float
    window_md: tuple[float, float]
    window_tvd: tuple[float, float]
    formations_ahead: list[dict]
    alerts: list[RiskAlert]
    offsets_considered: int
    offsets_used: int
    signals: list[Signal]

    def as_dict(self) -> dict:
        return {
            "well_id": self.well["well_id"],
            "well_name": self.well["well_name"],
            "field_name": self.well["field_name"],
            "status": self.well["status"],
            "bit_md_m": round(self.bit_md_m, 1),
            "bit_tvd_m": round(self.bit_tvd_m, 1),
            "planned_td_md_m": self.well.get("planned_td_md_m") or self.well.get("td_md_m"),
            "lookahead_m": self.lookahead_m,
            "window_md": [round(self.window_md[0], 1), round(self.window_md[1], 1)],
            "window_tvd": [round(self.window_tvd[0], 1), round(self.window_tvd[1], 1)],
            "formations_ahead": self.formations_ahead,
            "offsets_considered": self.offsets_considered,
            "offsets_used": self.offsets_used,
            "signals": [s.as_dict() for s in self.signals],
            "alerts": [a.as_dict() for a in self.alerts],
        }


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------


def look_ahead(
    store: Any,
    well_id: str,
    bit_md_m: float | None = None,
    lookahead_m: float = DEFAULT_LOOKAHEAD_M,
    radius_km: float = 15.0,
    weights: RelevanceWeights | None = None,
    max_offsets: int = 12,
    min_relevance: float = MIN_CONTRIBUTING_RELEVANCE,
) -> LookAheadResult | None:
    """Run the full look-ahead analysis for one well."""
    well = store.well(well_id)
    if well is None:
        return None

    trajectory = Trajectory.from_well(well)
    if bit_md_m is None:
        bit_md_m = float(well.get("current_bit_md_m") or well.get("td_md_m") or 0.0)

    planned_td = float(well.get("planned_td_md_m") or well.get("td_md_m") or bit_md_m)
    md_to = min(bit_md_m + lookahead_m, planned_td)
    bit_tvd = trajectory.tvd_at(bit_md_m)
    tvd_to = trajectory.tvd_at(md_to)
    window_tvd = (bit_tvd, max(tvd_to, bit_tvd + 1.0))

    # What rock is in the window, according to this well's own picked tops
    # extended by the regional model below TD.
    formations_ahead = _formations_in_window(store, well, trajectory, bit_md_m, md_to)

    # Live picture from the current well's own parameters.
    log_rows = store.drilling_log(well_id, md_to=bit_md_m)
    signals = analyse(log_rows)

    # Rank offsets for *this interval*.
    offsets = rank_offsets(
        store, well_id,
        radius_km=radius_km,
        weights=weights,
        focus_interval_tvd=window_tvd,
        limit=max_offsets,
    )
    usable = [o for o in offsets if o.score >= min_relevance and o.correlation is not None]

    alerts = _build_alerts(store, well, trajectory, usable, window_tvd,
                           (bit_md_m, md_to), signals)

    return LookAheadResult(
        well=well,
        bit_md_m=bit_md_m,
        bit_tvd_m=bit_tvd,
        lookahead_m=lookahead_m,
        window_md=(bit_md_m, md_to),
        window_tvd=window_tvd,
        formations_ahead=formations_ahead,
        alerts=alerts,
        offsets_considered=len(offsets),
        offsets_used=len(usable),
        signals=signals,
    )


def _formations_in_window(store: Any, well: dict, trajectory: Trajectory,
                          md_from: float, md_to: float) -> list[dict]:
    """
    Which formations the interval ahead crosses.

    Tops below the bit have not been picked in this well, so they come from
    the regional structural model at the well's location - which is exactly
    what a geologist's prognosis is.
    """
    from ..assam_geology import predicted_column

    picked = {t["formation"]: t["top_tvd_m"] for t in store.tops(well["well_id"])}
    modelled = dict(predicted_column(well["bottom_lat"], well["bottom_lon"]))

    # Anchor the model on the picks: shift it by the mean residual so the
    # prognosis below the bit is consistent with what this well actually saw.
    shared = set(picked) & set(modelled)
    shift = (
        sum(picked[n] - modelled[n] for n in shared) / len(shared) if shared else 0.0
    )

    tvd_from = trajectory.tvd_at(md_from)
    tvd_to = trajectory.tvd_at(md_to)

    out: list[dict] = []
    for name, model_top in modelled.items():
        top = picked.get(name, model_top + shift)
        if tvd_from - 1.0 <= top <= tvd_to:
            formation = FORMATION_BY_NAME.get(name)
            out.append({
                "formation": name,
                "top_tvd_m": round(top, 1),
                "top_md_m": round(trajectory.md_for_tvd(top), 1),
                "source": "picked" if name in picked else "prognosis",
                "lithology": formation.lithology if formation else None,
                "pore_pressure_sg": formation.pore_pressure_sg if formation else None,
                "frac_gradient_sg": formation.frac_gradient_sg if formation else None,
            })

    # Also report the unit the bit is currently in.
    current = None
    for name, model_top in modelled.items():
        top = picked.get(name, model_top + shift)
        if top <= tvd_from:
            current = name
    if current and not any(o["formation"] == current for o in out):
        formation = FORMATION_BY_NAME.get(current)
        out.insert(0, {
            "formation": current,
            "top_tvd_m": round(picked.get(current, modelled[current] + shift), 1),
            "top_md_m": None,
            "source": "current",
            "lithology": formation.lithology if formation else None,
            "pore_pressure_sg": formation.pore_pressure_sg if formation else None,
            "frac_gradient_sg": formation.frac_gradient_sg if formation else None,
        })
    out.sort(key=lambda o: o["top_tvd_m"])
    return out


def _build_alerts(store: Any, well: dict, trajectory: Trajectory,
                  offsets: Sequence[OffsetWell], window_tvd: tuple[float, float],
                  window_md: tuple[float, float],
                  signals: Sequence[Signal]) -> list[RiskAlert]:
    tvd_lo, tvd_hi = window_tvd
    offset_ids = [o.well["well_id"] for o in offsets]
    events_by_well = store.events_bulk(offset_ids)

    # Collect every offset event that maps into the interval ahead.
    grouped: dict[str, list[ContributingEvent]] = {}
    all_event_ids: list[int] = []

    for offset in offsets:
        correlation: Correlation = offset.correlation  # type: ignore[assignment]
        mapped_lo, mapped_hi = correlation.window_to_target(tvd_lo, tvd_hi)
        mapped_lo -= DEPTH_TOLERANCE_M
        mapped_hi += DEPTH_TOLERANCE_M

        for ev in events_by_well.get(offset.well["well_id"], []):
            tvd = ev.get("tvd_m")
            if tvd is None or not (mapped_lo <= tvd <= mapped_hi):
                continue
            projected_tvd = correlation.to_reference(tvd)
            # The tolerance above is applied in the offset well's depth frame,
            # so an event can still project behind the bit once it is mapped
            # back. That rock is already drilled - it is history, not a
            # look-ahead risk - so it does not get a vote.
            if projected_tvd < tvd_lo - BEHIND_BIT_TOLERANCE_M:
                continue
            contributing = ContributingEvent(
                well_id=offset.well["well_id"],
                well_name=offset.well["well_name"],
                relevance_score=offset.score,
                distance_km=offset.distance_km,
                correlation_quality=correlation.quality,
                event_id=ev["event_id"],
                severity=ev["severity"],
                confidence=float(ev.get("confidence") or 0.6),
                npt_hours=float(ev.get("npt_hours") or 0.0),
                formation=ev.get("formation"),
                event_date=ev.get("event_date"),
                magnitude=ev.get("magnitude") or {},
                remedial_action=ev.get("remedial_action"),
                offset_tvd_m=tvd,
                offset_md_m=ev.get("md_m") or tvd,
                projected_tvd_m=projected_tvd,
                projected_md_m=trajectory.md_for_tvd(projected_tvd),
            )
            grouped.setdefault(ev["event_type"], []).append(contributing)
            all_event_ids.append(ev["event_id"])

    citations = store.citations_bulk(all_event_ids)
    for bucket in grouped.values():
        for c in bucket:
            c.citations = citations.get(c.event_id, [])

    alerts: list[RiskAlert] = []
    for event_type, contributions in grouped.items():
        alert = _score_group(well, event_type, contributions, window_tvd, window_md,
                             trajectory, signals)
        if alert is not None:
            alerts.append(alert)

    alerts.sort(key=lambda a: -a.risk_score)
    return alerts


def _score_group(well: dict, event_type: str, contributions: list[ContributingEvent],
                 window_tvd: tuple[float, float], window_md: tuple[float, float],
                 trajectory: Trajectory, signals: Sequence[Signal]) -> RiskAlert | None:
    """
    Turn a set of analogous events into a single scored, explained alert.

    The score is built from four things, each of which a reviewer can check:

      support      how much *relevant* history stands behind it.  One event in
                   a marginal well is weak; four events across three highly
                   relevant wells is strong.
      severity     how bad it was when it happened, weighted by relevance.
      agreement    do the mapped depths cluster, or are they scattered across
                   the interval?
      live signals is the current well already showing the symptom?
    """
    if not contributions:
        return None

    # One vote per well: take that well's worst event of this type, so a well
    # whose report mentions the same loss zone twice does not count double.
    best_per_well: dict[str, ContributingEvent] = {}
    for c in contributions:
        prior = best_per_well.get(c.well_id)
        if prior is None or (c.severity, c.relevance_score) > (prior.severity, prior.relevance_score):
            best_per_well[c.well_id] = c
    voters = list(best_per_well.values())

    support = sum(c.relevance_score * c.confidence for c in voters)
    recurrence = 1.0 - math.exp(-support / 1.15)

    weight_total = sum(c.relevance_score for c in voters) or 1.0
    severity_weighted = sum(c.severity * c.relevance_score for c in voters) / weight_total
    severity_factor = 0.45 + 0.11 * severity_weighted        # 0.56 .. 1.00

    centre = sum(c.projected_tvd_m for c in voters) / len(voters)
    spread = (
        (sum((c.projected_tvd_m - centre) ** 2 for c in voters) / len(voters)) ** 0.5
        if len(voters) > 1 else 0.0
    )
    agreement = 1.0 / (1.0 + (spread / 90.0) ** 2)
    agreement_factor = 0.72 + 0.28 * agreement

    base = recurrence * severity_factor * agreement_factor
    live_boost, live_signals = boost_for(signals, event_type)
    risk = min(1.0, base + live_boost)

    mean_correlation = sum(c.correlation_quality for c in voters) / len(voters)
    mean_confidence = sum(c.confidence for c in voters) / len(voters)
    corroboration = min(1.0, len(voters) / 2.5)
    confidence = round(mean_correlation * mean_confidence * (0.55 + 0.45 * corroboration), 3)

    band = "High" if risk >= BAND_HIGH else "Medium" if risk >= BAND_MEDIUM else "Low"

    # Predicted interval: where in the *current* well this is expected.
    lo_tvd = max(window_tvd[0], min(c.projected_tvd_m for c in voters) - 20.0)
    hi_tvd = min(window_tvd[1], max(c.projected_tvd_m for c in voters) + 20.0)
    if hi_tvd <= lo_tvd:
        lo_tvd, hi_tvd = window_tvd
    lo_md = max(window_md[0], trajectory.md_for_tvd(lo_tvd))
    hi_md = min(window_md[1], trajectory.md_for_tvd(hi_tvd))

    formation = _dominant_formation(voters)
    label = EVENT_LABELS.get(event_type, event_type.replace("_", " ").title())

    contributions.sort(key=lambda c: (-c.relevance_score, -c.severity))

    return RiskAlert(
        alert_id=f"{well['well_id']}-{event_type}-{int(lo_md)}",
        event_type=event_type,
        label=label,
        risk_score=risk,
        risk_band=band,
        confidence=confidence,
        formation=formation,
        predicted_md_from=lo_md,
        predicted_md_to=hi_md,
        predicted_tvd_from=lo_tvd,
        predicted_tvd_to=hi_tvd,
        metres_ahead=max(0.0, lo_md - window_md[0]),
        contributing=contributions,
        why=_why(voters, event_type, formation, spread, mean_correlation, live_signals),
        recommended_actions=_recommend(event_type, voters),
        live_signals=list(live_signals),
        live_boost=live_boost,
    )


def _dominant_formation(voters: Sequence[ContributingEvent]) -> str | None:
    counts: dict[str, float] = {}
    for c in voters:
        if c.formation:
            counts[c.formation] = counts.get(c.formation, 0.0) + c.relevance_score
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _why(voters: Sequence[ContributingEvent], event_type: str, formation: str | None,
         spread: float, mean_correlation: float, live_signals: Sequence[Signal]) -> list[str]:
    """The bullet list behind the alert, in the order a driller would read it."""
    label = EVENT_LABELS.get(event_type, event_type).lower()
    reasons: list[str] = []

    n = len(voters)
    if formation:
        reasons.append(f"{n} relevant offset well{'s' if n > 1 else ''} recorded {label} "
                       f"in the {formation}")
    else:
        reasons.append(f"{n} relevant offset well{'s' if n > 1 else ''} recorded {label} "
                       f"at the equivalent depth")

    nearest = min(voters, key=lambda c: c.distance_km)
    magnitude = _format_magnitude(event_type, nearest.magnitude)
    reasons.append(
        f"Closest analogue {nearest.well_name} ({nearest.distance_km:.1f} km, relevance "
        f"{nearest.relevance_score:.2f}){f' - {magnitude}' if magnitude else ''} at "
        f"{nearest.offset_md_m:,.0f} m MD, correlating to "
        f"{nearest.projected_md_m:,.0f} m MD here"
    )

    worst = max(voters, key=lambda c: c.severity)
    total_npt = sum(c.npt_hours or 0.0 for c in voters)
    if total_npt > 0:
        reasons.append(f"Cost these wells {total_npt:.0f} hrs of non-productive time "
                       f"(worst single event: severity {worst.severity}/5 in {worst.well_name})")

    if len(voters) > 1:
        reasons.append(f"Mapped depths agree to within {spread:.0f} m after formation-tie "
                       f"correlation (tie quality {mean_correlation:.2f})")
    else:
        reasons.append(f"Single-well evidence only - depth correlation quality "
                       f"{mean_correlation:.2f}")

    for signal in live_signals[:2]:
        reasons.append(f"Current well is already showing it: {signal.note}")

    return reasons


def _format_magnitude(event_type: str, magnitude: dict) -> str:
    if not magnitude:
        return ""
    if event_type == "MUD_LOSS" and "loss_rate_m3_hr" in magnitude:
        return f"lost {magnitude['loss_rate_m3_hr']:g} m3/hr"
    if event_type == "KICK" and "pit_gain_m3" in magnitude:
        return f"{magnitude['pit_gain_m3']:g} m3 pit gain"
    if event_type == "STUCK_PIPE" and "overpull_t" in magnitude:
        return f"{magnitude['overpull_t']:g} t overpull"
    if event_type == "WELLBORE_INSTABILITY" and "caving_volume_m3" in magnitude:
        return f"{magnitude['caving_volume_m3']:g} m3 of cavings"
    if event_type == "HIGH_TORQUE_DRAG" and "peak_torque_kNm" in magnitude:
        return f"peak torque {magnitude['peak_torque_kNm']:g} kNm"
    key, value = next(iter(magnitude.items()))
    return f"{key.replace('_', ' ')} {value:g}"


def _recommend(event_type: str, voters: Sequence[ContributingEvent]) -> list[str]:
    """
    What to do about it.

    What the offset crews actually did comes first and is attributed, because
    "Naharkatiya-57 pumped a 40 bbl LCM pill and the losses stopped" carries
    more weight on a rig floor than generic best practice.
    """
    actions: list[str] = []
    seen: set[str] = set()

    for c in sorted(voters, key=lambda v: -v.relevance_score):
        if not c.remedial_action:
            continue
        text = c.remedial_action.strip().rstrip(".")
        normalised = text.lower()[:40]
        if normalised in seen:
            continue
        seen.add(normalised)
        actions.append(f"{c.well_name}: {text}.")
        if len(actions) >= 3:
            break

    actions.extend(ACTION_LIBRARY.get(event_type, []))
    return actions
