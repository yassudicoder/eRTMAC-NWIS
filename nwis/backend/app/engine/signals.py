"""
Live drilling-signal analysis.

Historical analogues tell you what the rock usually does.  The current well
tells you what it is doing right now.  Combining the two is the difference
between a static hazard map and an alert worth waking someone for: three
offset wells lost returns in this sand *and* ECD has been climbing towards the
fracture gradient for the last 80 m is a very different statement from either
half on its own.

These are deliberately simple, explainable trend detectors over the last
stretch of the eRTMAC parameter feed.  Every one of them corresponds to
something a driller already watches on the floor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..assam_geology import FORMATION_BY_NAME

# How far back along the hole a trend is measured.
DEFAULT_WINDOW_M = 120.0


@dataclass
class Signal:
    key: str
    label: str
    value: float
    unit: str
    trend_per_100m: float
    strength: float              # 0..1, how alarming this is right now
    implicates: tuple[str, ...]  # event types this signal supports
    note: str

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "value": round(self.value, 3),
            "unit": self.unit,
            "trend_per_100m": round(self.trend_per_100m, 3),
            "strength": round(self.strength, 3),
            "implicates": list(self.implicates),
            "note": self.note,
        }


def _slope_per_100m(rows: Sequence[dict], key: str) -> float:
    """Least-squares slope of a parameter against measured depth."""
    pts = [(r["md_m"], r[key]) for r in rows if r.get(key) is not None]
    if len(pts) < 3:
        return 0.0
    n = len(pts)
    mean_x = sum(p[0] for p in pts) / n
    mean_y = sum(p[1] for p in pts) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in pts)
    den = sum((x - mean_x) ** 2 for x in (p[0] for p in pts))
    if den <= 0:
        return 0.0
    return (num / den) * 100.0


def _ramp(value: float, start: float, full: float) -> float:
    """0 below ``start``, 1 at or above ``full``, linear in between."""
    if full == start:
        return 1.0 if value >= full else 0.0
    return max(0.0, min(1.0, (value - start) / (full - start)))


def analyse(log_rows: Sequence[dict], window_m: float = DEFAULT_WINDOW_M) -> list[Signal]:
    """
    Compute the live signals from the tail of the drilling log.

    ``log_rows`` must be ordered by measured depth and end at the bit.
    """
    if len(log_rows) < 4:
        return []

    bit_md = log_rows[-1]["md_m"]
    window = [r for r in log_rows if r["md_m"] >= bit_md - window_m]
    if len(window) < 4:
        window = list(log_rows[-4:])

    latest = window[-1]
    signals: list[Signal] = []

    # --- torque -----------------------------------------------------------
    torque_slope = _slope_per_100m(window, "torque_knm")
    if torque_slope > 0.25:
        signals.append(Signal(
            key="torque_rising",
            label="Torque trending up",
            value=latest["torque_knm"],
            unit="kNm",
            trend_per_100m=torque_slope,
            strength=_ramp(torque_slope, 0.25, 2.5),
            implicates=("HIGH_TORQUE_DRAG", "STUCK_PIPE", "WELLBORE_INSTABILITY", "TIGHT_HOLE"),
            note=(f"Torque up {torque_slope:.2f} kNm per 100 m over the last "
                  f"{window_m:.0f} m, now {latest['torque_knm']:.1f} kNm"),
        ))

    # --- standpipe pressure ------------------------------------------------
    spp_slope = _slope_per_100m(window, "spp_ksc")
    if spp_slope > 1.5:
        signals.append(Signal(
            key="spp_rising",
            label="Standpipe pressure trending up",
            value=latest["spp_ksc"],
            unit="ksc",
            trend_per_100m=spp_slope,
            strength=_ramp(spp_slope, 1.5, 12.0),
            implicates=("BIT_BALLING", "TIGHT_HOLE", "WELLBORE_INSTABILITY"),
            note=(f"SPP up {spp_slope:.1f} ksc per 100 m, now {latest['spp_ksc']:.0f} ksc - "
                  f"consistent with annular loading or a balling bit"),
        ))

    # --- gas ---------------------------------------------------------------
    gas_slope = _slope_per_100m(window, "gas_units")
    gas_now = latest["gas_units"]
    if gas_slope > 2.0 or gas_now > 45:
        signals.append(Signal(
            key="gas_rising",
            label="Background gas elevated",
            value=gas_now,
            unit="units",
            trend_per_100m=gas_slope,
            strength=max(_ramp(gas_slope, 2.0, 22.0), _ramp(gas_now, 45.0, 140.0)),
            implicates=("KICK",),
            note=f"Background gas {gas_now:.0f} units, trending {gas_slope:+.1f} per 100 m",
        ))

    # --- ECD against the fracture gradient ---------------------------------
    formation = FORMATION_BY_NAME.get(latest.get("formation") or "")
    if formation:
        margin = formation.frac_gradient_sg - latest["ecd_sg"]
        if margin < 0.22:
            signals.append(Signal(
                key="ecd_margin_narrow",
                label="ECD approaching fracture gradient",
                value=margin,
                unit="sg",
                trend_per_100m=_slope_per_100m(window, "ecd_sg"),
                strength=_ramp(-margin, -0.22, 0.02),
                implicates=("MUD_LOSS",),
                note=(f"ECD {latest['ecd_sg']:.2f} sg against an estimated fracture gradient "
                      f"of {formation.frac_gradient_sg:.2f} sg in the {formation.name} - "
                      f"only {margin:.2f} sg of margin"),
            ))

        # --- mud weight against pore pressure ------------------------------
        overbalance = latest["mud_weight_sg"] - formation.pore_pressure_sg
        if overbalance < 0.05:
            signals.append(Signal(
                key="overbalance_low",
                label="Low overbalance",
                value=overbalance,
                unit="sg",
                trend_per_100m=0.0,
                strength=_ramp(-overbalance, -0.05, 0.06),
                implicates=("KICK",),
                note=(f"Mud weight {latest['mud_weight_sg']:.2f} sg against an estimated pore "
                      f"pressure of {formation.pore_pressure_sg:.2f} sg - "
                      f"{overbalance:.2f} sg overbalance"),
            ))

    # --- rate of penetration ------------------------------------------------
    rop_slope = _slope_per_100m(window, "rop_m_hr")
    if rop_slope < -1.0:
        signals.append(Signal(
            key="rop_falling",
            label="ROP falling",
            value=latest["rop_m_hr"],
            unit="m/hr",
            trend_per_100m=rop_slope,
            strength=_ramp(-rop_slope, 1.0, 8.0),
            implicates=("LOW_ROP", "BIT_BALLING"),
            note=(f"ROP down {abs(rop_slope):.1f} m/hr per 100 m, now "
                  f"{latest['rop_m_hr']:.1f} m/hr"),
        ))

    signals.sort(key=lambda s: -s.strength)
    return signals


def boost_for(signals: Sequence[Signal], event_type: str) -> tuple[float, list[Signal]]:
    """
    How much the live picture raises the risk of a given event type, in [0, 0.2],
    together with the signals responsible so the alert can cite them.
    """
    relevant = [s for s in signals if event_type in s.implicates]
    if not relevant:
        return 0.0, []
    # Strongest signal dominates; extra corroborating signals add a little.
    strongest = max(s.strength for s in relevant)
    corroboration = min(0.25, 0.12 * (len(relevant) - 1))
    return round(min(0.20, 0.20 * (strongest + corroboration)), 4), relevant
