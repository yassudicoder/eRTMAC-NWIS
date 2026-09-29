"""
Generate the synthetic Upper Assam drilling dataset that NWIS runs on.

The generator produces the same artefacts a real asset would hand over:

    data/synthetic/wells.json            well headers + trajectory design
    data/synthetic/formation_tops.json   picked tops per well (MD and TVD)
    data/synthetic/logs/<WELL>_drilling.csv   depth-indexed drilling parameters
    data/synthetic/documents/<WELL>_WCR.txt   well completion report
    data/synthetic/documents/<WELL>_DDR.txt   daily drilling reports
    data/synthetic/documents/<WELL>_MUDLOG.txt  mud log
    data/synthetic/ground_truth_events.json   what the documents actually say

Important: ``ground_truth_events.json`` is *not* consumed by the application.
NWIS re-reads the generated documents through its own ingestion pipeline and
extracts events from the prose, exactly as it would from a scanned WCR.  The
ground truth file exists only so ``scripts/score_extraction.py`` can measure
how good that extraction is.

Everything is driven by a seed, so the dataset is reproducible:

    python scripts/generate_assam_dataset.py --seed 20260928
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.engine.geometry import Trajectory  # noqa: E402
from app.assam_geology import (  # noqa: E402
    CEMENTING_HAZARDS,
    CEMENTING_HAZARD_BY_MODE,
    EVENT_LABELS,
    FIELDS,
    Formation,
    FORMATION_BY_NAME,
    NPT_HOURS_BY_SEVERITY,
    STRATIGRAPHY,
    Field,
    formation_at_depth,
    ground_level_at,
    haversine_km,
    local_xy_km,
    predicted_column,
)

DATA = ROOT / "data" / "synthetic"

# --------------------------------------------------------------------------
# Well design
# --------------------------------------------------------------------------

RIGS = [
    "OIL-RIG-08 (E-1400)",
    "OIL-RIG-12 (E-2000)",
    "OIL-RIG-17 (NOV ideal)",
    "OIL-RIG-21 (E-1400)",
    "OIL-RIG-25 (ZJ-70)",
    "OIL-RIG-30 (ZJ-50)",
]

BIT_TYPES = [
    ("PDC 12-1/4in M323", 0.55),
    ("PDC 8-1/2in M433", 0.48),
    ("TCI 17-1/2in 517", 0.72),
    ("PDC 17-1/2in S123", 0.80),
    ("TCI 12-1/4in 537", 0.50),
    ("Insert 8-1/2in 617", 0.36),
]

MUD_SYSTEMS = [
    "Water-based bentonite / PHPA",
    "KCl-polymer water based mud",
    "Water-based gel-lignosulphonate",
    "KCl-glycol inhibitive WBM",
    "Low-solids non-dispersed WBM",
]

WELL_PURPOSES = ["Development", "Development", "Development", "Appraisal", "Exploratory"]


@dataclass
class CasingPoint:
    hole_size_in: str
    casing_size_in: str
    shoe_md_m: float
    shoe_tvd_m: float
    cement_top_m: float


@dataclass
class Well:
    well_id: str
    well_name: str
    field_name: str
    field_code: str
    purpose: str
    status: str
    surface_lat: float
    surface_lon: float
    bottom_lat: float
    bottom_lon: float
    ground_level_m: float
    spud_date: str
    completion_date: str | None
    rig: str
    mud_system: str
    well_type: str  # Vertical | Deviated | Horizontal
    kop_md_m: float | None
    build_rate_deg_30m: float
    max_inclination_deg: float
    azimuth_deg: float
    td_md_m: float
    td_tvd_m: float
    horizontal_displacement_m: float
    target_formation: str
    casing_scheme: list[CasingPoint] = field(default_factory=list)
    # Populated after simulation.
    days_drilled: int = 0
    total_npt_hours: float = 0.0


@dataclass
class Event:
    event_id: str
    well_id: str
    event_type: str
    severity: int
    md_m: float
    tvd_m: float
    md_end_m: float
    formation: str
    event_date: str
    npt_hours: float
    mud_weight_sg: float
    detail: str  # machine-readable summary of the magnitude
    mechanism: str


# --------------------------------------------------------------------------
# Hot spots - spatially coherent trouble zones
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class HotSpot:
    """
    A local geological problem area.  Hot spots are what make offset-well
    analysis worth doing: they make trouble cluster in space and formation
    rather than being independent per well, so a well drilled next to three
    wells that all lost returns in the Tipam really is at higher risk.
    """

    name: str
    lat: float
    lon: float
    radius_km: float
    formation: str
    event_type: str
    probability_boost: float
    severity_boost: int


HOT_SPOTS: tuple[HotSpot, ...] = (
    HotSpot("Naharkatiya Tipam depleted fairway", 27.2980, 95.3280, 5.0,
            "Tipam Sandstone", "MUD_LOSS", 0.45, 1),
    HotSpot("Naharkatiya Tipam differential-sticking belt", 27.2880, 95.3450, 4.0,
            "Tipam Sandstone", "STUCK_PIPE", 0.35, 1),
    HotSpot("Duliajan-Hugrijan coal caving trend", 27.3900, 95.3900, 6.5,
            "Barail Coal Shale", "WELLBORE_INSTABILITY", 0.40, 1),
    HotSpot("Moran Barail over-pressure cell", 27.1850, 94.9250, 4.5,
            "Barail Arenaceous", "KICK", 0.38, 1),
    HotSpot("Jorajan Kopili pressure ramp", 27.2450, 95.2300, 4.0,
            "Kopili Shale", "KICK", 0.30, 1),
    HotSpot("Tengakhat fractured Sylhet corridor", 27.3050, 95.1300, 5.5,
            "Sylhet Limestone", "MUD_LOSS", 0.35, 1),
    HotSpot("Dikom Girujan swelling-clay zone", 27.4760, 95.0850, 4.2,
            "Girujan Clay", "WELLBORE_INSTABILITY", 0.32, 1),
)


def hotspot_modifier(lat: float, lon: float, formation: str, event_type: str) -> tuple[float, int, str | None]:
    """Extra probability and severity from any hot spot covering this point."""
    boost = 0.0
    sev = 0
    hit: str | None = None
    for hs in HOT_SPOTS:
        if hs.formation != formation or hs.event_type != event_type:
            continue
        d = haversine_km(lat, lon, hs.lat, hs.lon)
        if d > hs.radius_km:
            continue
        # Taper to zero at the edge of the hot spot.
        taper = 1.0 - (d / hs.radius_km) ** 2
        boost += hs.probability_boost * taper
        if taper > 0.4:
            sev = max(sev, hs.severity_boost)
            hit = hs.name
    return boost, sev, hit


# --------------------------------------------------------------------------
# Well construction
# --------------------------------------------------------------------------


def make_wells(rng: random.Random) -> list[Well]:
    wells: list[Well] = []
    # How many wells each field carries, roughly in proportion to how mature
    # the field is.
    field_counts = {
        "NHK": 9, "MRN": 8, "DLJ": 7, "HGJ": 5, "TGK": 6,
        "SLM": 4, "DKM": 5, "BRK": 4, "JRJ": 6, "MKM": 4,
    }
    serial = {code: rng.randint(10, 60) for code in field_counts}

    for fld in FIELDS:
        n = field_counts[fld.code]
        for _ in range(n):
            wells.append(_make_one_well(rng, fld, serial, status="Completed"))

    # Three wells currently drilling - these are what the dashboard follows.
    for code in ("NHK", "MRN", "TGK"):
        fld = next(f for f in FIELDS if f.code == code)
        wells.append(_make_one_well(rng, fld, serial, status="Drilling"))

    return wells


def _make_one_well(rng: random.Random, fld: Field, serial: dict[str, int], status: str) -> Well:
    serial[fld.code] += rng.randint(1, 4)
    num = serial[fld.code]
    well_id = f"{fld.code}-{num:03d}"
    well_name = f"{fld.name}-{num}"

    # Surface location: scattered around the field centre.
    spread_km = rng.uniform(0.6, 5.5)
    theta = rng.uniform(0, 2 * math.pi)
    dlat = (spread_km * math.cos(theta)) / 110.574
    dlon = (spread_km * math.sin(theta)) / (111.320 * math.cos(math.radians(fld.lat)))
    slat = round(fld.lat + dlat, 5)
    slon = round(fld.lon + dlon, 5)

    purpose = rng.choice(WELL_PURPOSES)
    # Exploratory wells drill deeper, into or through the carbonates.
    if purpose == "Exploratory":
        target = rng.choice(["Sylhet Limestone", "Langpar", "Basement", "Kopili Shale"])
    elif purpose == "Appraisal":
        target = rng.choice(["Barail Arenaceous", "Barail Arenaceous", "Kopili Shale"])
    else:
        target = rng.choice(["Barail Arenaceous", "Barail Arenaceous", "Tipam Sandstone"])

    column = predicted_column(slat, slon)
    tops = dict(column)
    target_top = tops[target]
    idx = [f.name for f in STRATIGRAPHY].index(target)
    next_top = column[idx + 1][1] if idx + 1 < len(column) else target_top + 400.0

    # Trajectory design.
    roll = rng.random()
    build_rate = rng.uniform(1.6, 3.2)
    azimuth = rng.uniform(0.0, 360.0)

    if roll < 0.46:
        well_type, max_inc, kop = "Vertical", 0.0, None
    elif roll < 0.90:
        well_type = "Deviated"
        max_inc = rng.uniform(18.0, 42.0)
        kop = rng.uniform(450.0, min(1500.0, max(500.0, target_top * 0.6)))
    else:
        # A horizontal well lands at the reservoir and runs laterally inside
        # it. Only reservoir units are worth landing in, and the kick-off has
        # to sit far enough above the target for the build to finish before
        # the landing point - otherwise the well can never reach target depth,
        # because past 85 degrees it gains almost no TVD per metre drilled.
        well_type = "Horizontal"
        target = "Barail Arenaceous" if target_top > tops["Tipam Sandstone"] else "Tipam Sandstone"
        target_top = tops[target]
        idx = [f.name for f in STRATIGRAPHY].index(target)
        next_top = column[idx + 1][1] if idx + 1 < len(column) else target_top + 400.0
        max_inc = rng.uniform(80.0, 89.0)
        # TVD gained during the build is independent of where the build starts.
        probe = Trajectory(0.0, build_rate, max_inc, azimuth)
        build_length = probe.build_length
        tvd_gained = probe.tvd_at(build_length)
        landing_tvd = target_top + rng.uniform(15.0, 50.0)
        kop = max(300.0, landing_tvd - tvd_gained)

    traj = Trajectory(kop, build_rate, max_inc, azimuth)

    if well_type == "Horizontal":
        # TD is the landing point plus the lateral section.
        landing_md = kop + traj.build_length
        td_md = landing_md + rng.uniform(500.0, 1400.0)
        td_tvd = traj.tvd_at(td_md)
    else:
        # TD a little way into the target formation.
        penetration = rng.uniform(0.35, 0.92) * (next_top - target_top)
        td_tvd = target_top + max(60.0, penetration)
        td_md = traj.md_for_tvd(td_tvd)

    # The trajectory lookup table saturates at MAX_MD. If a design ever gets
    # near it the target is unreachable, which is a bug in the design rather
    # than something to silently emit.
    if td_md >= Trajectory.MAX_MD * 0.95:
        raise ValueError(
            f"{well_id}: unreachable target - {target} at {td_tvd:.0f} m TVD needs "
            f"{td_md:.0f} m MD with {max_inc:.0f} deg inclination"
        )

    disp = traj.displacement_at(td_md)

    # Bottom-hole location, offset along the azimuth.
    az = math.radians(azimuth)
    blat = round(slat + (disp / 1000.0) * math.cos(az) / 110.574, 5)
    blon = round(slon + (disp / 1000.0) * math.sin(az) / (111.320 * math.cos(math.radians(slat))), 5)

    # Dates.
    if status == "Drilling":
        spud = date(2026, 9, 1) + timedelta(days=rng.randint(0, 20))
        completion = None
    else:
        spud = date(2009, 1, 1) + timedelta(days=rng.randint(0, 6000))
        completion = None  # filled in after the day-by-day simulation

    gl = ground_level_at(slat, slon)

    well = Well(
        well_id=well_id,
        well_name=well_name,
        field_name=fld.name,
        field_code=fld.code,
        purpose=purpose,
        status=status,
        surface_lat=slat,
        surface_lon=slon,
        bottom_lat=blat,
        bottom_lon=blon,
        ground_level_m=gl,
        spud_date=spud.isoformat(),
        completion_date=completion.isoformat() if completion else None,
        rig=rng.choice(RIGS),
        mud_system=rng.choice(MUD_SYSTEMS),
        well_type=well_type,
        kop_md_m=round(kop, 1) if kop else None,
        build_rate_deg_30m=round(build_rate, 2),
        max_inclination_deg=round(max_inc, 1),
        azimuth_deg=round(azimuth, 1),
        td_md_m=round(td_md, 1),
        td_tvd_m=round(td_tvd, 1),
        horizontal_displacement_m=round(disp, 1),
        target_formation=target,
    )
    well.casing_scheme = _casing_scheme(well, traj, tops, rng)
    return well


def _casing_scheme(well: Well, traj: Trajectory, tops: dict[str, float], rng: random.Random) -> list[CasingPoint]:
    """
    Standard Upper Assam casing programme: isolate the Girujan clay before
    drilling Tipam, isolate the Barail coal shale before entering the pay.
    """
    scheme: list[CasingPoint] = []

    def add(hole: str, casing: str, shoe_tvd: float, toc: float) -> None:
        shoe_tvd = min(shoe_tvd, well.td_tvd_m - 20.0)
        if scheme and shoe_tvd <= scheme[-1].shoe_tvd_m + 50:
            return
        md = traj.md_for_tvd(shoe_tvd)
        scheme.append(CasingPoint(hole, casing, round(md, 1), round(shoe_tvd, 1), round(toc, 1)))

    add("26in", "20in conductor", rng.uniform(55, 90), 0.0)
    add("17-1/2in", "13-3/8in surface", tops["Tipam Sandstone"] + rng.uniform(20, 90), 0.0)
    if well.td_tvd_m > tops["Barail Arenaceous"] - 100:
        add("12-1/4in", "9-5/8in intermediate",
            tops["Barail Arenaceous"] - rng.uniform(20, 120),
            tops["Tipam Sandstone"] - rng.uniform(50, 250))
    if well.td_tvd_m > tops["Kopili Shale"]:
        add("8-1/2in", "7in liner", tops["Kopili Shale"] - rng.uniform(10, 80),
            tops["Barail Arenaceous"] - rng.uniform(80, 200))
    return scheme


def pick_formation_tops(well: Well, traj: Trajectory, rng: random.Random) -> list[dict]:
    """
    The tops a geologist would actually pick in this well.  Start from the
    structural model at the well's mid-point location, then add pick noise so
    the model and the picks do not agree exactly - which is the whole reason
    cross-well correlation has to be done on picks, not on the model.
    """
    mid_lat = (well.surface_lat + well.bottom_lat) / 2
    mid_lon = (well.surface_lon + well.bottom_lon) / 2
    column = predicted_column(mid_lat, mid_lon)

    out: list[dict] = []
    last_tvd = -1.0
    for name, model_tvd in column:
        noise = rng.gauss(0, max(8.0, model_tvd * 0.012))
        tvd = max(model_tvd + noise, last_tvd + 12.0)
        if tvd >= well.td_tvd_m:
            break
        md = traj.md_for_tvd(tvd)
        out.append({
            "formation": name,
            "top_tvd_m": round(tvd, 1),
            "top_md_m": round(md, 1),
            "lithology": FORMATION_BY_NAME[name].lithology,
        })
        last_tvd = tvd
    return out


# --------------------------------------------------------------------------
# Drilling simulation
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Reservoir characterisation
# --------------------------------------------------------------------------


@dataclass
class ReservoirInterval:
    """Petrophysical summary of one reservoir unit in one well."""

    well_id: str
    formation: str
    fluid: str
    top_md_m: float
    base_md_m: float
    top_tvd_m: float
    base_tvd_m: float
    gross_thickness_m: float
    net_pay_m: float
    net_to_gross: float
    porosity_pct: float
    permeability_md: float
    water_saturation_pct: float
    virgin_pressure_sg: float
    current_pressure_sg: float
    depletion_sg: float
    temperature_c: float


# Year each field came on production. Depletion accumulates from here, which
# is why a well drilled into the Tipam in 2024 meets a much weaker sand than
# one drilled in 2010 - and why it is far more likely to stick pipe.
FIELD_FIRST_PRODUCTION = {
    "NHK": 1953, "MRN": 1956, "DLJ": 1960, "HGJ": 1972, "TGK": 1978,
    "SLM": 1985, "DKM": 1982, "BRK": 1990, "JRJ": 1968, "MKM": 1994,
}


def depletion_for(well: Well, formation: Formation) -> float:
    """Pressure drop this well will meet in a reservoir, in sg of mud weight."""
    if formation.reservoir is None:
        return 0.0
    first = FIELD_FIRST_PRODUCTION.get(well.field_code, 1970)
    years = max(0, int(well.spud_date[:4]) - first)
    drop = years * formation.reservoir.depletion_sg_per_year
    # Depletion cannot run the reservoir below a hydrostatic-ish floor.
    return round(min(drop, formation.pore_pressure_sg - 0.88), 3)


def build_reservoir_intervals(well: Well, tops: list[dict], traj: Trajectory,
                              rng: random.Random) -> list[ReservoirInterval]:
    """The reservoir section this well actually penetrated."""
    out: list[ReservoirInterval] = []
    for i, t in enumerate(tops):
        formation = FORMATION_BY_NAME[t["formation"]]
        if formation.reservoir is None:
            continue
        res = formation.reservoir
        formation_base = tops[i + 1]["top_tvd_m"] if i + 1 < len(tops) else well.td_tvd_m
        formation_base = min(formation_base, well.td_tvd_m)
        # The developed pay sits in the upper part of the unit, not across the
        # whole formation - so cap the interval at a realistic gross thickness
        # rather than calling the entire Tipam Group "net pay".
        pay_gross = rng.uniform(0.6, 1.4) * res.typical_pay_gross_m
        base_tvd = min(formation_base, t["top_tvd_m"] + pay_gross)
        gross = max(0.0, base_tvd - t["top_tvd_m"])
        if gross < 5.0:
            continue

        ntg = max(0.05, min(0.95, rng.gauss(res.net_to_gross, 0.07)))
        depletion = depletion_for(well, formation)
        out.append(ReservoirInterval(
            well_id=well.well_id,
            formation=formation.name,
            fluid=res.fluid,
            top_md_m=round(t["top_md_m"], 1),
            base_md_m=round(traj.md_for_tvd(base_tvd), 1),
            top_tvd_m=round(t["top_tvd_m"], 1),
            base_tvd_m=round(base_tvd, 1),
            gross_thickness_m=round(gross, 1),
            net_pay_m=round(gross * ntg, 1),
            net_to_gross=round(ntg, 3),
            porosity_pct=round(max(4.0, rng.gauss(res.porosity_pct, 2.2)), 1),
            permeability_md=round(min(4000.0, max(1.0, rng.lognormvariate(
                math.log(res.permeability_md), 0.38))), 1),
            water_saturation_pct=round(
                max(8.0, min(92.0, rng.gauss(res.water_saturation_pct, 6.0))), 1),
            virgin_pressure_sg=formation.pore_pressure_sg,
            current_pressure_sg=round(formation.pore_pressure_sg - depletion, 3),
            depletion_sg=depletion,
            temperature_c=round(28.0 + t["top_tvd_m"] / 100.0
                                * res.temperature_gradient_c_per_100m, 1),
        ))
    return out


# --------------------------------------------------------------------------
# Cementing
# --------------------------------------------------------------------------


@dataclass
class CementJob:
    """One primary cement job, and how well it went."""

    well_id: str
    casing_size_in: str
    hole_size_in: str
    shoe_md_m: float
    shoe_tvd_m: float
    planned_toc_m: float
    actual_toc_m: float
    slurry_volume_m3: float
    excess_pct: float
    returns: str          # full | partial | none
    bond_index: float     # 0..1 from the cement bond log
    bond_quality: str     # good | fair | poor
    job_date: str
    issues: list[str]     # CementingHazard modes that occurred


def _interval_formations(column: Sequence[tuple[str, float]],
                         tvd_from: float, tvd_to: float) -> list[str]:
    names = []
    for i, (name, top) in enumerate(column):
        base = column[i + 1][1] if i + 1 < len(column) else 1e9
        if base > tvd_from and top < tvd_to:
            names.append(name)
    return names


def simulate_cementing(well: Well, tops: list[dict], events: Sequence[Event],
                       traj: Trajectory, rng: random.Random
                       ) -> tuple[list[CementJob], list[Event]]:
    """
    Cement every casing string, and decide what went wrong.

    Cementing trouble is not random: it follows what the hole did. Losses
    while drilling mean losses while cementing and a top of cement below plan;
    a washed-out section means the casing is off-centre and the slurry
    channels; coal seams and karstic limestone mean a poor bond log; a gas
    sand behind the shoe means annular migration.
    """
    column = [(t["formation"], t["top_tvd_m"]) for t in tops]
    spud = date.fromisoformat(well.spud_date)
    jobs: list[CementJob] = []
    cement_events: list[Event] = []
    seq = 0

    for cp in well.casing_scheme:
        if cp.casing_size_in.startswith("20in"):
            continue  # conductor is driven/jetted, not a logged cement job

        interval = _interval_formations(column, cp.cement_top_m, cp.shoe_tvd_m)
        if not interval:
            continue

        # What the hole across this interval is going to do to the job.
        loss_events = [e for e in events
                       if e.event_type == "MUD_LOSS"
                       and cp.cement_top_m <= e.tvd_m <= cp.shoe_tvd_m]
        washouts = [e for e in events
                    if e.event_type == "WASHOUT"
                    and cp.cement_top_m <= e.tvd_m <= cp.shoe_tvd_m]
        worst_bond = min(FORMATION_BY_NAME[n].cement_bond_quality for n in interval)
        gas_bearing = any(FORMATION_BY_NAME[n].is_reservoir
                          and "gas" in (FORMATION_BY_NAME[n].reservoir.fluid
                                        if FORMATION_BY_NAME[n].reservoir else "")
                          for n in interval)

        # Most primary cement jobs are acceptable. These rates are set so that
        # roughly a quarter to a third of jobs have any issue at all, which is
        # the realistic proportion - a corpus where every job fails teaches the
        # risk engine nothing, because there is no contrast to learn from.
        drivers = {
            "loss_zone": min(0.42, 0.03 + 0.15 * len(loss_events)
                             + 0.10 * sum(1 for e in loss_events if e.severity >= 4)),
            "poor_bond": min(0.34, (1.0 - worst_bond) * 0.42),
            "washout": min(0.30, 0.02 + 0.13 * len(washouts)),
            "gas_bearing": 0.11 if gas_bearing else 0.01,
        }

        issues: list[str] = []
        for hz in CEMENTING_HAZARDS:
            # A remedial squeeze is what you do *because* the bond failed, not
            # an independent way for the job to go wrong.
            if hz.mode == "REMEDIAL_SQUEEZE":
                continue
            if rng.random() < drivers[hz.driver]:
                issues.append(hz.mode)

        if ("POOR_BOND" in issues or "CHANNELLING" in issues) and rng.random() < 0.38:
            issues.append("REMEDIAL_SQUEEZE")
        # Two reported problems on one job is already a bad job; more than that
        # reads as noise rather than a record.
        issues = issues[:3]

        # Volumes and returns follow from whether the interval took mud.
        annulus_m = cp.shoe_tvd_m - cp.cement_top_m
        excess = rng.uniform(15.0, 38.0) + (22.0 if washouts else 0.0)
        volume = annulus_m * rng.uniform(0.028, 0.045) * (1 + excess / 100.0)

        if "LOSSES_DURING_CEMENTING" in issues:
            returns = "none" if any(e.severity >= 4 for e in loss_events) else "partial"
            shortfall = rng.uniform(0.18, 0.55) * annulus_m
            actual_toc = min(cp.shoe_tvd_m - 30.0, cp.cement_top_m + shortfall)
            if "TOC_BELOW_PLAN" not in issues:
                issues.append("TOC_BELOW_PLAN")
        elif "TOC_BELOW_PLAN" in issues:
            returns = "partial"
            actual_toc = min(cp.shoe_tvd_m - 30.0,
                             cp.cement_top_m + rng.uniform(0.08, 0.25) * annulus_m)
        else:
            returns = "full"
            actual_toc = cp.cement_top_m + rng.uniform(-8.0, 12.0)

        bond_index = max(0.05, min(0.98, rng.gauss(worst_bond, 0.09)
                                   - (0.18 if "CHANNELLING" in issues else 0.0)
                                   - (0.15 if "POOR_BOND" in issues else 0.0)))
        bond_quality = "good" if bond_index >= 0.70 else "fair" if bond_index >= 0.45 else "poor"
        # The cementing record reports the bond for every job. If it reads
        # "poor", that is a finding in its own right - the report and the
        # event list must not disagree about whether the bond was acceptable.
        if bond_quality == "poor" and "POOR_BOND" not in issues:
            issues.append("POOR_BOND")

        # Cementing happens once the section is drilled, so date it from the
        # depth reached rather than from spud.
        day = int(well.days_drilled * min(1.0, cp.shoe_md_m / max(well.td_md_m, 1)))
        job_date = (spud + timedelta(days=day)).isoformat()

        job = CementJob(
            well_id=well.well_id,
            casing_size_in=cp.casing_size_in,
            hole_size_in=cp.hole_size_in,
            shoe_md_m=cp.shoe_md_m,
            shoe_tvd_m=cp.shoe_tvd_m,
            planned_toc_m=cp.cement_top_m,
            actual_toc_m=round(max(0.0, actual_toc), 1),
            slurry_volume_m3=round(volume, 1),
            excess_pct=round(excess, 1),
            returns=returns,
            bond_index=round(bond_index, 3),
            bond_quality=bond_quality,
            job_date=job_date,
            issues=issues,
        )
        jobs.append(job)

        for mode in issues:
            hz = CEMENTING_HAZARD_BY_MODE[mode]
            seq += 1
            # Severity follows the numbers the report actually quotes - the
            # returns, the cement-top shortfall, the bond index - so that it
            # can be recovered from the prose, exactly as for drilling events.
            shortfall = max(0.0, job.actual_toc_m - cp.cement_top_m)
            if mode == "LOSSES_DURING_CEMENTING":
                severity = 5 if returns == "none" else 4
            elif mode == "TOC_BELOW_PLAN":
                severity = 4 if shortfall >= 0.25 * max(annulus_m, 1.0) else 3
            elif mode == "POOR_BOND":
                severity = 4 if job.bond_index < 0.30 else 3
            elif mode == "GAS_MIGRATION":
                severity = 4
            elif mode == "REMEDIAL_SQUEEZE":
                severity = 4
            else:  # CHANNELLING
                severity = 3
            lo, hi = NPT_HOURS_BY_SEVERITY[severity]
            cement_events.append(Event(
                event_id=f"{well.well_id}-C{seq:02d}",
                well_id=well.well_id,
                event_type="CEMENTING_ISSUE",
                severity=severity,
                md_m=cp.shoe_md_m,
                tvd_m=cp.shoe_tvd_m,
                md_end_m=cp.shoe_md_m,
                formation=formation_at_depth(column, cp.shoe_tvd_m),
                event_date=job_date,
                npt_hours=round(rng.uniform(lo, hi), 1),
                mud_weight_sg=0.0,
                detail=(f"mode={mode};casing={cp.casing_size_in};"
                        f"planned_toc_m={cp.cement_top_m:.0f};"
                        f"actual_toc_m={job.actual_toc_m:.0f};"
                        f"bond_index={job.bond_index};returns={returns}"),
                mechanism=hz.mechanism,
            ))

    return jobs, cement_events


def simulate_well(well: Well, traj: Trajectory, tops: list[dict], rng: random.Random,
                  depletion_by_formation: dict[str, float] | None = None,
                  ) -> tuple[list[dict], list[Event], list[dict]]:
    """
    Walk the well down in 10 m steps, producing a depth-indexed parameter log,
    a list of trouble events and a day-by-day operations summary.
    """
    column = [(t["formation"], t["top_tvd_m"]) for t in tops]
    depletion_by_formation = depletion_by_formation or {}
    samples: list[dict] = []
    events: list[Event] = []
    days: list[dict] = []

    md = 0.0
    step = 10.0
    spud = date.fromisoformat(well.spud_date)
    elapsed_hours = 0.0
    day_index = 0
    day_start_md = 0.0
    day_events: list[Event] = []
    npt_total = 0.0

    # Which hazards have already fired in this well, per (formation, type),
    # so the same problem is not reported five times in one interval.
    fired: set[tuple[str, str]] = set()
    bit_name, bit_eff = rng.choice(BIT_TYPES)
    event_seq = 0

    while md < well.td_md_m:
        md = min(md + step, well.td_md_m)
        tvd = traj.tvd_at(md)
        fname = formation_at_depth(column, tvd) or column[0][0]
        fm = FORMATION_BY_NAME[fname]
        inc = traj.inclination_at(md)

        # Mud weight tracks pore pressure with an overbalance margin. In a
        # depleted reservoir the *current* pressure is what matters, but the
        # mud weight cannot be cut freely: it still has to hold up the shales
        # above, so the well ends up heavily overbalanced across the depleted
        # sand. That excess differential pressure is precisely the mechanism
        # behind differential sticking and whole-mud losses in a mature field.
        depletion = depletion_by_formation.get(fname, 0.0)
        current_pp = fm.pore_pressure_sg - depletion
        margin = 0.06 if fm.pore_pressure_sg < 1.2 else 0.10
        mud_weight = round(min(fm.pore_pressure_sg + margin, fm.frac_gradient_sg - 0.04), 2)
        overbalance = mud_weight - current_pp

        # ROP falls with depth and rock strength, rises with bit efficiency.
        depth_penalty = 1.0 / (1.0 + tvd / 2600.0)
        rop = fm.drillability * bit_eff * 46.0 * depth_penalty * rng.uniform(0.75, 1.25)
        rop = max(0.8, rop)

        wob = round(rng.uniform(6, 12) + (1 - fm.drillability) * 14, 1)
        rpm = round(rng.uniform(70, 145) if "PDC" in bit_name else rng.uniform(55, 110))
        torque = round((1 - fm.drillability) * 9.0 + inc * 0.09 + tvd / 900.0 + rng.uniform(-1.0, 1.4), 2)
        flow = round(rng.uniform(1800, 3200) if tvd < 1500 else rng.uniform(1100, 2200))
        spp = round(60 + tvd * 0.021 + rng.uniform(-8, 10), 1)
        ecd = round(mud_weight + 0.03 + tvd * 0.000012 + rng.uniform(0, 0.03), 3)
        gas = round(max(0.0, rng.gauss(2.5, 1.8) + (28.0 if fm.is_reservoir else 0.0)
                        + (18.0 if fname == "Barail Coal Shale" else 0.0)), 1)

        samples.append({
            "md_m": round(md, 1),
            "tvd_m": round(tvd, 1),
            "formation": fname,
            "inclination_deg": round(inc, 2),
            "rop_m_hr": round(rop, 2),
            "wob_t": wob,
            "rpm": rpm,
            "torque_kNm": torque,
            "flow_lpm": flow,
            "spp_ksc": spp,
            "mud_weight_sg": mud_weight,
            "ecd_sg": ecd,
            "gas_units": gas,
            "pore_pressure_sg": round(current_pp, 3),
            "overbalance_sg": round(overbalance, 3),
        })

        # Hazard sampling, once per formation per hazard type.
        for hz in fm.hazards:
            key = (fname, hz.event_type)
            if key in fired:
                continue
            # Per-10 m probability, so a thick formation is more exposed than a
            # thin one but the total stays near the formation probability.
            interval_m = _formation_thickness(column, fname, well.td_tvd_m)
            steps = max(1.0, interval_m / step)
            boost, sev_boost, hs_name = hotspot_modifier(
                well.surface_lat, well.surface_lon, fname, hz.event_type)
            # Differential sticking and whole-mud losses scale with how hard
            # the mud column is pressing on a depleted sand.
            if hz.event_type in ("STUCK_PIPE", "MUD_LOSS") and overbalance > 0.12:
                boost += min(0.30, (overbalance - 0.12) * 1.6)
            p_total = min(0.96, hz.probability + boost)
            p_step = 1.0 - (1.0 - p_total) ** (1.0 / steps)
            if rng.random() >= p_step:
                continue

            fired.add(key)
            event_seq += 1
            lo_sev, hi_sev = SEVERITY_RANGE.get(hz.event_type, (1, 5))
            severity = max(lo_sev, min(hi_sev, hz.severity + sev_boost + rng.choice([-1, 0, 0, 1])))
            lo, hi = NPT_HOURS_BY_SEVERITY[severity]
            npt = round(rng.uniform(lo, hi), 1)
            npt_total += npt
            length = round(rng.uniform(4, 60), 1)
            ev = Event(
                event_id=f"{well.well_id}-E{event_seq:02d}",
                well_id=well.well_id,
                event_type=hz.event_type,
                severity=severity,
                md_m=round(md, 1),
                tvd_m=round(tvd, 1),
                md_end_m=round(min(md + length, well.td_md_m), 1),
                formation=fname,
                event_date=(spud + timedelta(days=day_index)).isoformat(),
                npt_hours=npt,
                mud_weight_sg=mud_weight,
                detail=_event_detail(hz.event_type, severity, md, rng),
                # The hot-spot NAME is deliberately not recorded here. It ends
                # up in the "lessons learnt" prose of the report, and the
                # whole point of scripts/validate_discovery.py is that the
                # application rediscovers those zones without ever being told
                # they exist. Writing the name into the corpus would leak the
                # answer into the question.
                mechanism=hz.mechanism,
            )
            events.append(ev)
            day_events.append(ev)
            elapsed_hours += npt

        # Advance the clock: drilling time plus a share of flat time.
        elapsed_hours += step / rop + rng.uniform(0.02, 0.12)
        new_day = int(elapsed_hours // 24)
        if new_day > day_index:
            for d in range(day_index, new_day):
                days.append({
                    "day": d + 1,
                    "date": (spud + timedelta(days=d)).isoformat(),
                    "md_start_m": round(day_start_md, 1),
                    "md_end_m": round(md, 1),
                    "formation": fname,
                    "bit": bit_name,
                    "events": [asdict(e) for e in day_events] if d == new_day - 1 else [],
                })
                day_start_md = md
                day_events = []
            day_index = new_day

        # Bit changes at casing points.
        for cp in well.casing_scheme:
            if abs(md - cp.shoe_md_m) < step / 2:
                bit_name, bit_eff = rng.choice(BIT_TYPES)
                elapsed_hours += rng.uniform(10, 26)  # trip, run casing, cement

    # Flush the final, partial day so its events reach the DDR.
    if day_events or day_start_md < well.td_md_m:
        days.append({
            "day": day_index + 1,
            "date": (spud + timedelta(days=day_index)).isoformat(),
            "md_start_m": round(day_start_md, 1),
            "md_end_m": round(well.td_md_m, 1),
            "formation": formation_at_depth(column, traj.tvd_at(well.td_md_m)) or column[-1][0],
            "bit": bit_name,
            "events": [asdict(e) for e in day_events],
        })

    well.days_drilled = max(1, day_index + 1)
    well.total_npt_hours = round(npt_total, 1)
    if well.status == "Completed":
        well.completion_date = (spud + timedelta(days=well.days_drilled + rng.randint(4, 20))).isoformat()

    return samples, events, days


def _formation_thickness(column: list[tuple[str, float]], name: str, td_tvd: float) -> float:
    for i, (n, top) in enumerate(column):
        if n == name:
            base = column[i + 1][1] if i + 1 < len(column) else td_tvd
            return max(20.0, min(base, td_tvd) - top)
    return 100.0


# The severity range each event type can actually take.  A stuck pipe is
# never a "severity 1" nuisance, and a washout is never well-threatening.
# Keeping the generator inside these bands also keeps severity recoverable
# from the magnitudes a report quotes.
SEVERITY_RANGE: dict[str, tuple[int, int]] = {
    "MUD_LOSS": (2, 5),
    "KICK": (3, 5),
    "STUCK_PIPE": (3, 5),
    "WELLBORE_INSTABILITY": (2, 5),
    "TIGHT_HOLE": (3, 4),
    "HIGH_TORQUE_DRAG": (3, 4),
    "WASHOUT": (2, 3),
    "BIT_BALLING": (2, 3),
    "LOW_ROP": (2, 3),
    "CEMENTING_ISSUE": (2, 5),
    "EQUIPMENT_FAILURE": (2, 4),
}

# Magnitude bands per severity step.  These are deliberately aligned with the
# thresholds the extractor uses to infer severity from a report, because that
# is how a real report works: "losses of 34 m3/hr" *is* the severity
# statement.  A generator that scattered magnitudes independently of severity
# would be asking the extractor to recover information the text never carried.
MAGNITUDE_BANDS: dict[str, dict[int, tuple[float, float]]] = {
    "MUD_LOSS": {1: (0.5, 1.9), 2: (2.0, 4.9), 3: (5.0, 14.9), 4: (15.0, 39.0), 5: (40.0, 120.0)},
    "KICK_GAIN": {1: (0.6, 1.1), 2: (1.2, 2.4), 3: (1.2, 2.4), 4: (2.5, 5.8), 5: (6.0, 12.0)},
    "KICK_SIDPP": {1: (4.0, 7.9), 2: (8.0, 19.0), 3: (8.0, 19.0), 4: (20.0, 44.0), 5: (45.0, 78.0)},
    "STUCK_PIPE": {1: (6.0, 11.0), 2: (6.0, 11.0), 3: (12.0, 29.0), 4: (30.0, 59.0), 5: (60.0, 95.0)},
    "WELLBORE_INSTABILITY": {1: (0.3, 1.2), 2: (0.5, 2.9), 3: (3.0, 7.9), 4: (8.0, 11.9), 5: (12.0, 20.0)},
    "HIGH_TORQUE_DRAG": {1: (8.0, 13.0), 2: (8.0, 13.0), 3: (14.0, 29.0), 4: (30.0, 42.0), 5: (43.0, 55.0)},
    "WASHOUT": {1: (8.0, 11.0), 2: (12.0, 49.0), 3: (50.0, 85.0), 4: (86.0, 120.0), 5: (86.0, 140.0)},
    "BIT_BALLING": {1: (4.0, 9.0), 2: (10.0, 34.0), 3: (35.0, 55.0), 4: (56.0, 70.0), 5: (56.0, 70.0)},
    "LOW_ROP": {1: (2.6, 3.4), 2: (1.3, 2.5), 3: (0.5, 1.2), 4: (0.3, 0.9), 5: (0.2, 0.6)},
}


def _band(kind: str, severity: int, rng: random.Random) -> float:
    lo, hi = MAGNITUDE_BANDS[kind][max(1, min(5, severity))]
    return rng.uniform(lo, hi)


def _event_detail(event_type: str, severity: int, md: float, rng: random.Random) -> str:
    """A short, machine-readable magnitude for the event, scaled by severity."""
    if event_type == "MUD_LOSS":
        return f"loss_rate_m3_hr={round(_band('MUD_LOSS', severity, rng), 1)}"
    if event_type == "KICK":
        return (f"pit_gain_m3={round(_band('KICK_GAIN', severity, rng), 1)};"
                f"sidpp_ksc={round(_band('KICK_SIDPP', severity, rng), 1)};"
                f"gas_units={round(rng.uniform(120, 600) * severity)}")
    if event_type == "STUCK_PIPE":
        # The free point is where the string is still movable, so it always
        # sits above the stuck depth.
        free_point = round(md * rng.uniform(0.45, 0.94), 1)
        return (f"overpull_t={round(_band('STUCK_PIPE', severity, rng), 1)};"
                f"free_point_m={free_point}")
    if event_type == "WELLBORE_INSTABILITY":
        return f"caving_volume_m3={round(_band('WELLBORE_INSTABILITY', severity, rng), 1)}"
    if event_type == "HIGH_TORQUE_DRAG":
        return f"peak_torque_kNm={round(_band('HIGH_TORQUE_DRAG', severity, rng), 1)}"
    if event_type == "TIGHT_HOLE":
        return f"reaming_passes={rng.randint(2, 7) if severity <= 3 else rng.randint(8, 14)}"
    if event_type == "WASHOUT":
        return f"caliper_excess_pct={round(_band('WASHOUT', severity, rng), 1)}"
    if event_type == "LOW_ROP":
        return f"rop_m_hr={round(_band('LOW_ROP', severity, rng), 2)}"
    if event_type == "BIT_BALLING":
        return f"spp_rise_ksc={round(_band('BIT_BALLING', severity, rng), 1)}"
    return f"severity={severity}"


# --------------------------------------------------------------------------
# Document generation
# --------------------------------------------------------------------------

LOSS_TEMPLATES = [
    "Partial mud losses of {rate} m3/hr observed at {md} m MD ({tvd} m TVD) while drilling {formation}.",
    "Lost circulation encountered at {md} m MD in {formation}. Loss rate recorded as {rate} m3/hr.",
    "Seepage losses ({rate} m3/hr) noted at {md} m MD in the {formation}.",
    "Mud loss of {rate} m3/hr recorded between {md} m and {md_end} m MD in {formation}.",
    "While drilling {formation} at {md} m MD, returns reduced and losses of {rate} m3/hr were measured.",
]
LOSS_TOTAL_TEMPLATES = [
    "Total loss of returns at {md} m MD ({tvd} m TVD) in {formation}. No returns to surface.",
    "Complete loss of circulation at {md} m MD on entering {formation}.",
]
STUCK_TEMPLATES = [
    "String became stuck at {md} m MD ({tvd} m TVD) in {formation}. Overpull of {overpull} t recorded.",
    "Differential sticking at {md} m MD across the {formation}; maximum overpull {overpull} t.",
    "Pipe stuck at {md} m MD while drilling {formation}. Free point established at {free_point} m.",
]
KICK_TEMPLATES = [
    "Well kicked at {md} m MD ({tvd} m TVD) while drilling {formation}. Pit gain {pit_gain} m3, SIDPP {sidpp} ksc. Well shut in.",
    "Gas influx observed at {md} m MD in {formation}; gas units rose to {gas_units}. Pit gain {pit_gain} m3.",
    "Influx taken at {md} m MD in the {formation}. Well shut in, SIDPP {sidpp} ksc; killed by drillers method.",
]
INSTABILITY_TEMPLATES = [
    "Cavings observed at shakers while drilling {formation} at {md} m MD; approximately {caving_volume} m3 recovered.",
    "Hole instability and sloughing from {md} m to {md_end} m MD in {formation}.",
    "Wellbore instability in {formation} from {md} m MD. Excessive cavings ({caving_volume} m3) over the shakers.",
]
TIGHT_TEMPLATES = [
    "Tight hole experienced at {md} m MD in {formation}; {reaming_passes} reaming passes required.",
    "Hole pack-off tendency at {md} m MD in the {formation}. Back-reamed {reaming_passes} times.",
]
TORQUE_TEMPLATES = [
    "High torque and drag from {md} m MD in {formation}; peak torque {peak_torque} kNm.",
    "Torque increased sharply at {md} m MD while drilling {formation}, peaking at {peak_torque} kNm.",
]
BALLING_TEMPLATES = [
    "Bit balling suspected at {md} m MD in {formation}; standpipe pressure rose by {spp_rise} ksc.",
    "Bit and BHA balling up in the {formation} at {md} m MD, SPP increase of {spp_rise} ksc.",
]
WASHOUT_TEMPLATES = [
    "Hole washout logged at {md} m MD in {formation}; caliper {caliper_excess} percent over gauge.",
]
LOWROP_TEMPLATES = [
    "ROP dropped to {rop} m/hr at {md} m MD in {formation}.",
    "Very low penetration rate ({rop} m/hr) drilling {formation} at {md} m MD.",
]

TEMPLATES_BY_TYPE = {
    "MUD_LOSS": LOSS_TEMPLATES,
    "STUCK_PIPE": STUCK_TEMPLATES,
    "KICK": KICK_TEMPLATES,
    "WELLBORE_INSTABILITY": INSTABILITY_TEMPLATES,
    "TIGHT_HOLE": TIGHT_TEMPLATES,
    "HIGH_TORQUE_DRAG": TORQUE_TEMPLATES,
    "BIT_BALLING": BALLING_TEMPLATES,
    "WASHOUT": WASHOUT_TEMPLATES,
    "LOW_ROP": LOWROP_TEMPLATES,
}

REMEDIAL = {
    "MUD_LOSS": [
        "LCM pill of {vol} bbl (medium nut plug and mica) pumped; losses reduced.",
        "Pumped {vol} bbl of coarse LCM slurry and cut mud weight by 0.03 sg.",
        "Cement plug placed across the loss zone and drilled out after {hrs} hrs WOC.",
        "Reduced flow rate and continued drilling blind with water.",
    ],
    "STUCK_PIPE": [
        "Worked and jarred the string; spotted {vol} bbl of pipe-release pill. String freed.",
        "Jarring continued for {hrs} hrs before the string came free.",
        "Back-off performed and fishing assembly run; fish recovered after {hrs} hrs.",
    ],
    "KICK": [
        "Well shut in and killed by drillers method. Mud weight raised to {mw} sg.",
        "Circulated out the influx through the choke; kill mud weight {mw} sg.",
        "Weighted up to {mw} sg and circulated bottoms up twice before resuming.",
    ],
    "WELLBORE_INSTABILITY": [
        "Mud weight raised to {mw} sg and inhibition increased; hole condition improved.",
        "Circulated hi-vis sweeps and back-reamed the interval before continuing.",
        "Increased KCl concentration and raised mud weight to {mw} sg.",
    ],
    "TIGHT_HOLE": [
        "Reamed and circulated the interval clean before continuing.",
        "Worked the string over the tight interval and raised mud weight to {mw} sg.",
    ],
    "HIGH_TORQUE_DRAG": [
        "Added lubricant to the mud system and reduced RPM.",
        "Circulated sweeps to clean the hole; torque returned to normal.",
    ],
    "BIT_BALLING": [
        "Pulled off bottom, circulated and jetted the bit clean.",
        "Raised flow rate and added detergent to the system.",
    ],
    "WASHOUT": [
        "Noted for cement volume calculation; no remedial action taken.",
        "Reduced flow rate through the interval.",
    ],
    "LOW_ROP": [
        "Bit pulled and replaced; drilling parameters optimised.",
        "Changed WOB and RPM combination to improve penetration rate.",
    ],
}


# Cementing prose. Each failure mode gets its own phrasings, because the
# extractor has to tell "losses while cementing" from "poor bond" from
# "annular gas migration" - they call for completely different actions.
CEMENT_TEMPLATES: dict[str, list[str]] = {
    "LOSSES_DURING_CEMENTING": [
        "Losses observed while cementing the {casing} at {shoe} m MD; returns {returns}.",
        "Partial returns lost during the {casing} cement job at {shoe} m MD.",
        "Cement slurry lost to the formation while cementing {casing} casing at {shoe} m MD.",
    ],
    "TOC_BELOW_PLAN": [
        "Top of cement logged at {actual_toc} m against a planned top of {planned_toc} m "
        "behind the {casing}.",
        "CBL indicated top of cement at {actual_toc} m behind the {casing}, {shortfall} m "
        "below the planned top of {planned_toc} m.",
    ],
    "POOR_BOND": [
        "Cement bond log across the {casing} showed poor bonding, bond index {bond_index}.",
        "CBL/VDL run on the {casing} indicated poor to fair bond, bond index {bond_index}.",
    ],
    "CHANNELLING": [
        "Cement channelling suspected behind the {casing} at {shoe} m MD; hole was "
        "washed out over the interval.",
        "Bond log showed channelling behind the {casing}, casing poorly centralised.",
    ],
    "GAS_MIGRATION": [
        "Sustained casing pressure observed after the {casing} cement job; annular gas "
        "migration suspected at {shoe} m MD.",
        "Gas migration through the setting cement behind the {casing} at {shoe} m MD.",
    ],
    "REMEDIAL_SQUEEZE": [
        "Primary cement job on the {casing} failed acceptance; remedial squeeze carried "
        "out at {shoe} m MD.",
        "Remedial cement squeeze performed behind the {casing} at {shoe} m MD before "
        "drilling ahead.",
    ],
}

CEMENT_REMEDIAL: dict[str, list[str]] = {
    "LOSSES_DURING_CEMENTING": [
        "Job completed with reduced returns; top of cement confirmed by temperature survey.",
        "Cut slurry density and continued; losses were cured before the plug bumped.",
    ],
    "TOC_BELOW_PLAN": [
        "Top-up job performed through the annulus to raise the cement top.",
        "Accepted after review; previous shoe covered by the subsequent string.",
    ],
    "POOR_BOND": [
        "Remedial squeeze carried out and the bond log re-run; bond improved to fair.",
        "Accepted after pressure testing the shoe; monitored for annular pressure.",
    ],
    "CHANNELLING": [
        "Additional centralisers run on the following string.",
        "Squeeze performed across the channelled interval.",
    ],
    "GAS_MIGRATION": [
        "Annulus bled down and monitored; gas-tight slurry used on subsequent strings.",
        "Squeeze performed and sustained casing pressure resolved.",
    ],
    "REMEDIAL_SQUEEZE": [
        "Squeeze held the pressure test; drilling resumed.",
        "Second squeeze required before an acceptable test was obtained.",
    ],
}


def _render_cement_sentence(ev: Event, rng: random.Random) -> str:
    d = _detail_fields(ev)
    mode = d.get("mode", "POOR_BOND")
    tpl = rng.choice(CEMENT_TEMPLATES.get(mode, ["Cementing problem at {shoe} m MD."]))
    planned = float(d.get("planned_toc_m", 0) or 0)
    actual = float(d.get("actual_toc_m", 0) or 0)
    return tpl.format(
        casing=d.get("casing", "casing"),
        shoe=f"{ev.md_m:,.0f}",
        returns=d.get("returns", "partial"),
        planned_toc=f"{planned:,.0f}",
        actual_toc=f"{actual:,.0f}",
        shortfall=f"{max(0.0, actual - planned):,.0f}",
        bond_index=d.get("bond_index", "0.40"),
    )


def _render_cement_remedial(ev: Event, rng: random.Random) -> str:
    mode = _detail_fields(ev).get("mode", "POOR_BOND")
    pool = CEMENT_REMEDIAL.get(mode)
    return rng.choice(pool) if pool else ""


def _detail_fields(ev: Event) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in ev.detail.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _render_event_sentence(ev: Event, rng: random.Random) -> str:
    if ev.event_type == "CEMENTING_ISSUE":
        return _render_cement_sentence(ev, rng)
    d = _detail_fields(ev)
    pool = TEMPLATES_BY_TYPE.get(ev.event_type, ["{event_type} at {md} m MD in {formation}."])
    if ev.event_type == "MUD_LOSS" and ev.severity == 5:
        pool = LOSS_TOTAL_TEMPLATES
    tpl = rng.choice(pool)
    values = {
        "md": f"{ev.md_m:,.0f}",
        "md_end": f"{ev.md_end_m:,.0f}",
        "tvd": f"{ev.tvd_m:,.0f}",
        "formation": ev.formation,
        "event_type": ev.event_type.replace("_", " ").title(),
        "rate": d.get("loss_rate_m3_hr", "5.0"),
        "overpull": d.get("overpull_t", "30"),
        "free_point": d.get("free_point_m", "1200"),
        "pit_gain": d.get("pit_gain_m3", "3.0"),
        "sidpp": d.get("sidpp_ksc", "20"),
        "gas_units": d.get("gas_units", "800"),
        "caving_volume": d.get("caving_volume_m3", "3.0"),
        "reaming_passes": d.get("reaming_passes", "5"),
        "peak_torque": d.get("peak_torque_kNm", "25"),
        "spp_rise": d.get("spp_rise_ksc", "25"),
        "caliper_excess": d.get("caliper_excess_pct", "30"),
        "rop": d.get("rop_m_hr", "2.0"),
    }
    return tpl.format(**values)


def _render_remedial(ev: Event, rng: random.Random) -> str:
    if ev.event_type == "CEMENTING_ISSUE":
        return _render_cement_remedial(ev, rng)
    pool = REMEDIAL.get(ev.event_type)
    if not pool:
        return ""
    tpl = rng.choice(pool)
    return tpl.format(
        vol=rng.randint(20, 160),
        hrs=round(ev.npt_hours * rng.uniform(0.3, 0.9), 1),
        mw=round(ev.mud_weight_sg + rng.uniform(0.03, 0.12), 2),
    )


class Paged:
    """Accumulates report text and inserts page breaks every N lines."""

    def __init__(self, lines_per_page: int = 46):
        self.lines: list[str] = []
        self.lines_per_page = lines_per_page
        self._page = 1
        self._count = 0
        self.lines.append(f"[PAGE {self._page}]")

    def write(self, text: str = "") -> None:
        for line in text.split("\n"):
            if self._count >= self.lines_per_page:
                self._page += 1
                self._count = 0
                self.lines.append("")
                self.lines.append(f"[PAGE {self._page}]")
            self.lines.append(line)
            self._count += 1

    def render(self) -> str:
        return "\n".join(self.lines) + "\n"


def write_wcr(well: Well, tops: list[dict], events: list[Event], samples: list[dict],
              rng: random.Random, cement_jobs: Sequence[CementJob] = (),
              reservoirs: Sequence[ReservoirInterval] = ()) -> str:
    p = Paged()
    p.write("OIL INDIA LIMITED")
    p.write("DRILLING DEPARTMENT, DULIAJAN, ASSAM")
    p.write("")
    p.write("WELL COMPLETION REPORT")
    p.write("=" * 72)
    p.write("")
    p.write("1. WELL IDENTIFICATION")
    p.write("-" * 72)
    p.write(f"Well Number            : {well.well_name}")
    p.write(f"Well Code              : {well.well_id}")
    p.write(f"Field / Structure      : {well.field_name}")
    p.write(f"Category               : {well.purpose}")
    p.write(f"Well Profile           : {well.well_type}")
    p.write(f"Surface Coordinates    : {well.surface_lat} N, {well.surface_lon} E")
    p.write(f"Bottom Hole Coordinates: {well.bottom_lat} N, {well.bottom_lon} E")
    p.write(f"Ground Level           : {well.ground_level_m} m above MSL")
    p.write(f"Rig                    : {well.rig}")
    p.write(f"Date Spudded           : {_fmt_date(well.spud_date)}")
    p.write(f"Date Completed         : {_fmt_date(well.completion_date)}")
    p.write(f"Total Depth (MD)       : {well.td_md_m:,.1f} m")
    p.write(f"Total Depth (TVD)      : {well.td_tvd_m:,.1f} m")
    if well.kop_md_m:
        p.write(f"Kick Off Point         : {well.kop_md_m:,.1f} m MD")
        p.write(f"Maximum Inclination    : {well.max_inclination_deg} deg")
        p.write(f"Azimuth                : {well.azimuth_deg} deg")
        p.write(f"Horizontal Displacement: {well.horizontal_displacement_m:,.1f} m")
    p.write(f"Target Formation       : {well.target_formation}")
    p.write(f"Total Days Drilled     : {well.days_drilled}")
    p.write(f"Total NPT              : {well.total_npt_hours} hrs")
    p.write("")

    p.write("2. CASING AND HOLE PROGRAMME")
    p.write("-" * 72)
    p.write(f"{'Hole Size':<12}{'Casing':<24}{'Shoe MD (m)':>14}{'Shoe TVD (m)':>14}{'TOC (m)':>10}")
    for cp in well.casing_scheme:
        p.write(f"{cp.hole_size_in:<12}{cp.casing_size_in:<24}{cp.shoe_md_m:>14,.1f}"
                f"{cp.shoe_tvd_m:>14,.1f}{cp.cement_top_m:>10,.0f}")
    p.write("")
    p.write(f"Mud System             : {well.mud_system}")
    p.write("")

    p.write("2A. CEMENTING RECORD")
    p.write("-" * 72)
    if not cement_jobs:
        p.write("No cased-hole cement jobs were carried out on this well.")
    else:
        p.write(f"{'Casing':<24}{'Shoe MD':>10}{'Planned TOC':>13}{'Actual TOC':>12}"
                f"{'Slurry':>9}{'Returns':>9}{'Bond':>7}")
        p.write(f"{'':<24}{'(m)':>10}{'(m)':>13}{'(m)':>12}{'(m3)':>9}{'':>9}{'index':>7}")
        for j in cement_jobs:
            p.write(f"{j.casing_size_in:<24}{j.shoe_md_m:>10,.0f}{j.planned_toc_m:>13,.0f}"
                    f"{j.actual_toc_m:>12,.0f}{j.slurry_volume_m3:>9,.1f}"
                    f"{j.returns:>9}{j.bond_index:>7.2f}")
        p.write("")
        for j in cement_jobs:
            verdict = "poor to fair" if j.bond_quality == "poor" else j.bond_quality
            p.write(f"Cement bond log across the {j.casing_size_in} indicated "
                    f"{verdict} bond (bond index {j.bond_index:.2f}).")
    p.write("")

    p.write("3. GEOLOGICAL SUMMARY - FORMATION TOPS")
    p.write("-" * 72)
    p.write(f"{'Formation':<26}{'Top MD (m)':>13}{'Top TVD (m)':>14}   Lithology")
    for t in tops:
        p.write(f"{t['formation']:<26}{t['top_md_m']:>13,.1f}{t['top_tvd_m']:>14,.1f}   {t['lithology']}")
    p.write("")
    p.write("The well encountered the normal Upper Assam section. Formation tops were")
    p.write("picked on the basis of cuttings examination and wireline log correlation")
    p.write("with the neighbouring wells of the structure.")
    p.write("")

    p.write("3A. RESERVOIR SUMMARY")
    p.write("-" * 72)
    if not reservoirs:
        p.write("No reservoir interval was penetrated in this well.")
    else:
        p.write(f"{'Formation':<22}{'Fluid':<14}{'Gross':>8}{'Net Pay':>9}{'Poro':>7}"
                f"{'Perm':>9}{'Sw':>7}{'Press':>8}")
        p.write(f"{'':<22}{'':<14}{'(m)':>8}{'(m)':>9}{'(%)':>7}{'(mD)':>9}{'(%)':>7}{'(sg)':>8}")
        for r in reservoirs:
            p.write(f"{r.formation:<22}{r.fluid:<14}{r.gross_thickness_m:>8,.1f}"
                    f"{r.net_pay_m:>9,.1f}{r.porosity_pct:>7.1f}{r.permeability_md:>9,.1f}"
                    f"{r.water_saturation_pct:>7.1f}{r.current_pressure_sg:>8.2f}")
        p.write("")
        for r in reservoirs:
            p.write(f"{r.formation}: net pay {r.net_pay_m:,.1f} m, average porosity "
                    f"{r.porosity_pct:.1f} percent, permeability {r.permeability_md:,.0f} mD, "
                    f"water saturation {r.water_saturation_pct:.1f} percent.")
            if r.depletion_sg > 0.01:
                p.write(f"    Reservoir pressure has declined from a virgin "
                        f"{r.virgin_pressure_sg:.2f} sg to {r.current_pressure_sg:.2f} sg "
                        f"equivalent, a depletion of {r.depletion_sg:.2f} sg. Differential "
                        f"sticking risk across this interval is correspondingly elevated.")
            p.write(f"    Formation temperature at top of interval: {r.temperature_c:.0f} deg C.")
    p.write("")

    p.write("4. DRILLING PARAMETERS BY SECTION")
    p.write("-" * 72)
    p.write(f"{'Formation':<26}{'Avg ROP':>10}{'Avg WOB':>10}{'Avg RPM':>10}{'Mud Wt':>10}")
    p.write(f"{'':<26}{'(m/hr)':>10}{'(t)':>10}{'':>10}{'(sg)':>10}")
    for t in tops:
        rows = [s for s in samples if s["formation"] == t["formation"]]
        if not rows:
            continue
        p.write(f"{t['formation']:<26}"
                f"{sum(r['rop_m_hr'] for r in rows) / len(rows):>10.2f}"
                f"{sum(r['wob_t'] for r in rows) / len(rows):>10.1f}"
                f"{sum(r['rpm'] for r in rows) / len(rows):>10.0f}"
                f"{sum(r['mud_weight_sg'] for r in rows) / len(rows):>10.2f}")
    p.write("")

    p.write("5. DRILLING PROBLEMS AND REMEDIAL MEASURES")
    p.write("-" * 72)
    if not events:
        p.write("No significant drilling problems were encountered. The well was drilled")
        p.write("to total depth without non-productive time.")
    else:
        for i, ev in enumerate(events, 1):
            p.write(f"5.{i}  {ev.formation} - {_fmt_date(ev.event_date)}")
            p.write("     " + _render_event_sentence(ev, rng))
            rem = _render_remedial(ev, rng)
            if rem:
                p.write("     " + rem)
            p.write(f"     Non-productive time attributable to this event: {ev.npt_hours} hrs.")
            p.write("")
    p.write("")

    p.write("6. LESSONS LEARNT AND RECOMMENDATIONS FOR FUTURE WELLS")
    p.write("-" * 72)
    if events:
        for ev in sorted(events, key=lambda e: -e.severity)[:5]:
            # Name the hazard. A real lessons-learnt entry says what the
            # problem was, not only what caused it - and naming it is what
            # lets the lesson be indexed against the same taxonomy as the
            # events, so a warning can quote the lesson beside the evidence.
            label = EVENT_LABELS.get(ev.event_type, ev.event_type).split(" / ")[0]
            p.write(f"- {ev.formation} - {label.lower()}: {ev.mechanism}.")
            p.write(f"  Future wells on this structure should plan for this around")
            p.write(f"  {ev.md_m:,.0f} m MD.")
    else:
        p.write("- No specific precautions beyond the standard programme are recommended.")
    p.write("")
    p.write("Prepared by  : Drilling Engineering Group, OIL, Duliajan")
    p.write("Reviewed by  : Head - Drilling Services")
    return p.render()


def write_ddr(well: Well, days: list[dict], rng: random.Random) -> str:
    p = Paged(lines_per_page=52)
    p.write("OIL INDIA LIMITED - DAILY DRILLING REPORT")
    p.write(f"Well: {well.well_name} ({well.well_id})    Field: {well.field_name}    Rig: {well.rig}")
    p.write("=" * 78)
    p.write("")
    for d in days:
        progress = d["md_end_m"] - d["md_start_m"]
        p.write(f"DAY {d['day']:>3}   DATE {_fmt_date(d['date'])}   DEPTH {d['md_end_m']:,.1f} m MD")
        p.write(f"  Progress        : {progress:,.1f} m")
        p.write(f"  Present Formation: {d['formation']}")
        p.write(f"  Bit in use      : {d['bit']}")
        if d["events"]:
            p.write("  Operations / Remarks:")
            for raw in d["events"]:
                ev = Event(**raw)
                p.write("    - " + _render_event_sentence(ev, rng))
                rem = _render_remedial(ev, rng)
                if rem:
                    p.write("      " + rem)
                p.write(f"      NPT {ev.npt_hours} hrs.")
        else:
            p.write("  Operations / Remarks:")
            p.write(f"    - Drilling ahead as per programme. Circulated and conditioned mud.")
        p.write("")
    return p.render()


def write_mudlog(well: Well, tops: list[dict], samples: list[dict], events: list[Event],
                 rng: random.Random) -> str:
    p = Paged(lines_per_page=50)
    p.write("OIL INDIA LIMITED - MUD LOG / LITHOLOGY LOG")
    p.write(f"Well: {well.well_name} ({well.well_id})    Field: {well.field_name}")
    p.write(f"Mud System: {well.mud_system}")
    p.write("=" * 78)
    p.write("")
    p.write(f"{'MD (m)':>10}{'TVD (m)':>10}{'Formation':<26}{'ROP':>8}{'Gas':>8}{'MW':>7}")
    p.write(f"{'':>10}{'':>10}{'':<26}{'(m/hr)':>8}{'(units)':>8}{'(sg)':>7}")
    p.write("-" * 78)
    events_by_md = {round(e.md_m / 50) * 50: e for e in events}
    for s in samples[::5]:  # one row every 50 m
        p.write(f"{s['md_m']:>10,.0f}{s['tvd_m']:>10,.0f}{s['formation']:<26}"
                f"{s['rop_m_hr']:>8.1f}{s['gas_units']:>8.0f}{s['mud_weight_sg']:>7.2f}")
        key = round(s["md_m"] / 50) * 50
        if key in events_by_md:
            ev = events_by_md.pop(key)
            p.write("           >> " + _render_event_sentence(ev, rng))
    p.write("")
    p.write("LITHOLOGICAL DESCRIPTION")
    p.write("-" * 78)
    for t in tops:
        p.write(f"{t['top_md_m']:,.0f} m MD - {t['formation']}")
        p.write(f"    {t['lithology']}.")
    return p.render()


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return "Not completed"
    y, m, d = iso.split("-")
    return f"{d}-{m}-{y}"


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate the synthetic Upper Assam dataset")
    ap.add_argument("--seed", type=int, default=20260928)
    ap.add_argument("--out", type=Path, default=DATA)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    out: Path = args.out
    (out / "documents").mkdir(parents=True, exist_ok=True)
    (out / "logs").mkdir(parents=True, exist_ok=True)

    # Clear artefacts from any previous run. Well numbering is seed-dependent,
    # so without this a re-seed leaves orphan reports on disk that belong to
    # wells the manifest no longer knows about.
    removed = 0
    for stale in list((out / "documents").glob("*.txt")) +             list((out / "documents").glob("*.pdf")) +             list((out / "logs").glob("*.csv")):
        stale.unlink()
        removed += 1
    if removed:
        print(f"Cleared {removed} artefacts from the previous run")

    wells = make_wells(rng)
    all_tops: dict[str, list[dict]] = {}
    all_events: list[dict] = []
    all_cement_jobs: list[dict] = []
    all_reservoirs: list[dict] = []
    doc_index: list[dict] = []

    for well in wells:
        traj = Trajectory(well.kop_md_m, well.build_rate_deg_30m,
                          well.max_inclination_deg, well.azimuth_deg)
        tops = pick_formation_tops(well, traj, rng)
        depletion_by_formation = {
            t["formation"]: depletion_for(well, FORMATION_BY_NAME[t["formation"]])
            for t in tops
        }
        samples, events, days = simulate_well(well, traj, tops, rng,
                                              depletion_by_formation)
        reservoirs = build_reservoir_intervals(well, tops, traj, rng)
        cement_jobs, cement_events = simulate_cementing(well, tops, events, traj, rng)
        events = sorted(events + cement_events, key=lambda e: e.md_m)
        # A daily report records running and cementing casing, not only
        # drilling. Attach each cement job to the day that reached its shoe,
        # so the record exists even for a well with no completion report yet.
        for ce in cement_events:
            for d in days:
                if d["md_start_m"] <= ce.md_m <= d["md_end_m"]:
                    d["events"].append(asdict(ce))
                    break
            else:
                if days:
                    days[-1]["events"].append(asdict(ce))
        well.total_npt_hours = round(
            well.total_npt_hours + sum(e.npt_hours for e in cement_events), 1)

        # A well that is still drilling has only been logged down to the
        # current bit depth; nothing below it exists yet.
        if well.status == "Drilling":
            bit_md = round(well.td_md_m * rng.uniform(0.52, 0.74), 1)
            samples = [s for s in samples if s["md_m"] <= bit_md]
            events = [e for e in events if e.md_m <= bit_md]
            kept_days = []
            for d in days:
                if d["md_start_m"] >= bit_md:
                    break
                d = dict(d)
                d["md_end_m"] = min(d["md_end_m"], bit_md)
                d["events"] = [e for e in d["events"] if e["md_m"] <= bit_md]
                kept_days.append(d)
            days = kept_days or days[:1]
            well.current_bit_md_m = bit_md  # type: ignore[attr-defined]
            well.planned_td_md_m = well.td_md_m  # type: ignore[attr-defined]
            well.days_drilled = len(days)
            cement_jobs = [j for j in cement_jobs if j.shoe_md_m <= bit_md]
            reservoirs = [r for r in reservoirs if r.top_md_m <= bit_md]

        all_tops[well.well_id] = tops
        all_events.extend(asdict(e) for e in events)
        all_cement_jobs.extend(asdict(j) for j in cement_jobs)
        all_reservoirs.extend(asdict(r) for r in reservoirs)

        # Depth-indexed parameter log.
        log_path = out / "logs" / f"{well.well_id}_drilling.csv"
        with log_path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(samples[0].keys()))
            w.writeheader()
            w.writerows(samples)

        # Reports.  A well that is still drilling has no completion report
        # yet - only the daily reports and the running mud log.
        docs = {
            "DDR": write_ddr(well, days, rng),
            "MUDLOG": write_mudlog(well, tops, samples, events, rng),
        }
        if well.status == "Completed":
            docs["WCR"] = write_wcr(well, tops, events, samples, rng,
                                    cement_jobs, reservoirs)
        for kind, text in docs.items():
            name = f"{well.well_id}_{kind}.txt"
            (out / "documents" / name).write_text(text, encoding="utf-8")
            doc_index.append({
                "document_id": f"{well.well_id}-{kind}",
                "well_id": well.well_id,
                "doc_type": kind,
                "title": {
                    "WCR": f"Well Completion Report - {well.well_name}",
                    "DDR": f"Daily Drilling Reports - {well.well_name}",
                    "MUDLOG": f"Mud Log - {well.well_name}",
                }[kind],
                "filename": name,
                "pages": text.count("[PAGE "),
                "characters": len(text),
            })

    payload_wells = []
    for w in wells:
        d = asdict(w)
        d["current_bit_md_m"] = getattr(w, "current_bit_md_m", None)
        d["planned_td_md_m"] = getattr(w, "planned_td_md_m", None)
        payload_wells.append(d)

    (out / "wells.json").write_text(json.dumps(payload_wells, indent=2), encoding="utf-8")
    (out / "formation_tops.json").write_text(json.dumps(all_tops, indent=2), encoding="utf-8")
    (out / "documents_index.json").write_text(json.dumps(doc_index, indent=2), encoding="utf-8")
    (out / "ground_truth_events.json").write_text(json.dumps(all_events, indent=2), encoding="utf-8")
    (out / "cement_jobs.json").write_text(
        json.dumps(all_cement_jobs, indent=2), encoding="utf-8")
    (out / "reservoir_intervals.json").write_text(
        json.dumps(all_reservoirs, indent=2), encoding="utf-8")
    (out / "hot_spots.json").write_text(
        json.dumps([asdict(h) for h in HOT_SPOTS], indent=2), encoding="utf-8")

    active = [w for w in wells if w.status == "Drilling"]
    print(f"Generated {len(wells)} wells ({len(active)} currently drilling)")
    print(f"  events    : {len(all_events)}")
    print(f"  cement jobs      : {len(all_cement_jobs)}")
    print(f"  reservoir intervals: {len(all_reservoirs)}")
    print(f"  documents : {len(doc_index)}")
    print(f"  output    : {out}")
    for w in active:
        print(f"  active    : {w.well_id} {w.well_name}, bit at "
              f"{getattr(w, 'current_bit_md_m', 0):,.0f} m MD of "
              f"{w.td_md_m:,.0f} m planned TD")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
