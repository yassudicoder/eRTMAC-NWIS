"""
Upper Assam Shelf - stratigraphic and operational reference model.

This module is the single source of geological truth shared by the synthetic
data generator and the correlation / risk engines.  Depths, lithologies and
hazard profiles follow the published stratigraphy of the Upper Assam basin
(Dihing -> Girujan -> Tipam -> Barail -> Kopili -> Sylhet -> Langpar ->
Archean basement), which is the section OIL drills around Duliajan,
Naharkatiya and Moran.

Nothing here is proprietary: the column, the field locations and the hazard
associations are all from open literature.  The *well-level* numbers under
data/synthetic are generated from this model, not taken from real records.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

# Structural reference point: OIL HQ, Duliajan.
REF_LAT = 27.3600
REF_LON = 95.3200

# Regional dip. The Upper Assam shelf deepens towards the SE, into the
# Naga Schuppen thrust belt.  Azimuth in degrees from north.
REGIONAL_DIP_AZIMUTH = 135.0
REGIONAL_DIP_M_PER_KM = 24.0


@dataclass(frozen=True)
class Hazard:
    """A drilling hazard that a formation is prone to."""

    event_type: str
    # Probability that a well penetrating this formation sees the event.
    probability: float
    # Typical severity 1 (nuisance) .. 5 (well-threatening).
    severity: int
    # Free-text mechanism, reused when generating report prose.
    mechanism: str


@dataclass(frozen=True)
class Reservoir:
    """
    Petrophysical character of a reservoir unit.

    These are the "reservoir characteristics" the problem statement asks to be
    correlated across wells.  They matter operationally, not just
    geologically: a depleted high-permeability sand is exactly where
    differential sticking and whole-mud losses happen, so carrying porosity,
    permeability and - above all - *current* pressure against virgin pressure
    is what makes those alerts credible rather than generic.
    """

    fluid: str                      # oil | gas | oil and gas | water
    porosity_pct: float             # mean effective porosity
    permeability_md: float          # mean horizontal permeability
    water_saturation_pct: float
    net_to_gross: float             # 0..1
    # Pressure decline per year of field production, in sg of mud-weight
    # equivalent. Upper Assam's Barail and Tipam sands are mature and
    # materially depleted.
    depletion_sg_per_year: float
    # Typical GROSS thickness of the productive zone, in metres. This is a pay
    # interval inside the formation, not the whole unit - the Tipam Group is
    # over a kilometre thick but only its upper sands are developed.
    typical_pay_gross_m: float = 120.0
    temperature_gradient_c_per_100m: float = 2.8


@dataclass(frozen=True)
class Formation:
    name: str
    aliases: tuple[str, ...]
    lithology: str
    # Depth to the top of this unit at the structural reference point, in
    # metres below ground level, before local structure is applied.
    ref_top_m: float
    # Extra structural relief multiplier - deeper units have grown more.
    dip_factor: float
    # Pore-pressure gradient expressed as mud-weight equivalent, in sg.
    # For a reservoir this is the VIRGIN pressure; the producing pressure a
    # well actually meets is this less accumulated depletion.
    pore_pressure_sg: float
    # Fracture gradient expressed as mud-weight equivalent, in sg.
    frac_gradient_sg: float
    # Drillability 0..1; higher drills faster.
    drillability: float
    hazards: tuple[Hazard, ...] = ()
    is_reservoir: bool = False
    reservoir: Reservoir | None = None
    # How well cement bonds to this rock, 0..1. Coals, washed-out
    # unconsolidated sand and karstic carbonate all bond badly, which is what
    # drives most cementing trouble in this section.
    cement_bond_quality: float = 0.8


STRATIGRAPHY: tuple[Formation, ...] = (
    Formation(
        name="Dihing",
        aliases=("Dihing Formation", "Alluvium", "Dihing Fm"),
        lithology="Unconsolidated pebbly sandstone, clay and gravel",
        ref_top_m=0.0,
        dip_factor=0.35,
        pore_pressure_sg=1.02,
        frac_gradient_sg=1.35,
        drillability=0.95,
        hazards=(
            Hazard("WASHOUT", 0.28, 2, "unconsolidated gravel eroded by high flow rate"),
            Hazard("MUD_LOSS", 0.18, 2, "seepage into permeable gravel beds"),
        ),
        cement_bond_quality=0.45,
    ),
    Formation(
        name="Namsang",
        aliases=("Namsang Formation", "Dupi Tila", "Dupitila"),
        lithology="Medium to coarse sandstone with clay interbeds",
        ref_top_m=190.0,
        dip_factor=0.45,
        pore_pressure_sg=1.03,
        frac_gradient_sg=1.42,
        drillability=0.88,
        hazards=(
            Hazard("MUD_LOSS", 0.22, 2, "seepage losses into coarse sand stringers"),
            Hazard("WASHOUT", 0.16, 2, "poorly consolidated sand"),
        ),
        cement_bond_quality=0.55,
    ),
    Formation(
        name="Girujan Clay",
        aliases=("Girujan", "Girujan Clay Formation", "Girujan Fm"),
        lithology="Mottled clay with thin siltstone and sandstone bands",
        ref_top_m=630.0,
        dip_factor=0.62,
        pore_pressure_sg=1.06,
        frac_gradient_sg=1.55,
        drillability=0.72,
        hazards=(
            Hazard("WELLBORE_INSTABILITY", 0.41, 3, "reactive clay hydrating and sloughing into the hole"),
            Hazard("BIT_BALLING", 0.26, 2, "sticky clay packing the bit and BHA"),
            Hazard("TIGHT_HOLE", 0.30, 3, "plastic clay creeping and closing the hole"),
        ),
        cement_bond_quality=0.6,
    ),
    Formation(
        name="Tipam Sandstone",
        aliases=("Tipam", "Tipam Sst", "Tipam Sandstone Formation", "Tipam Group"),
        lithology="Massive ferruginous sandstone with clay partings",
        ref_top_m=1160.0,
        dip_factor=0.78,
        pore_pressure_sg=1.02,
        frac_gradient_sg=1.58,
        drillability=0.64,
        hazards=(
            Hazard("MUD_LOSS", 0.46, 3, "high-permeability sandstone taking whole mud"),
            Hazard("STUCK_PIPE", 0.27, 4, "differential sticking against depleted permeable sand"),
            Hazard("WASHOUT", 0.14, 2, "friable sandstone eroding"),
        ),
        is_reservoir=True,
        reservoir=Reservoir(
            fluid="oil",
            porosity_pct=24.0,
            permeability_md=850.0,
            water_saturation_pct=32.0,
            net_to_gross=0.72,
            # Tipam sands have been on production the longest and are the most
            # depleted, which is why they take whole mud and stick pipe.
            depletion_sg_per_year=0.0075,
            typical_pay_gross_m=165.0,
        ),
        cement_bond_quality=0.72,
    ),
    Formation(
        name="Barail Coal Shale",
        aliases=("Barail Coal Shale", "Tikak Parbat", "Barail CS", "Coal Shale"),
        lithology="Carbonaceous shale with coal seams and thin sandstone",
        ref_top_m=2460.0,
        dip_factor=0.90,
        pore_pressure_sg=1.14,
        frac_gradient_sg=1.70,
        drillability=0.55,
        hazards=(
            Hazard("WELLBORE_INSTABILITY", 0.48, 4, "coal seams caving and producing large cavings at the shakers"),
            Hazard("HIGH_TORQUE_DRAG", 0.34, 3, "cavings packing the annulus and loading the string"),
            Hazard("STUCK_PIPE", 0.24, 4, "pack-off on coal cavings"),
            Hazard("KICK", 0.11, 4, "gas influx from coal seams"),
        ),
        cement_bond_quality=0.35,
    ),
    Formation(
        name="Barail Arenaceous",
        aliases=("Barail Arenaceous", "Baragolai", "Naogaon", "Barail Sandstone", "Barail Main Sand"),
        lithology="Fine to medium sandstone with shale interbeds - principal pay",
        ref_top_m=3060.0,
        dip_factor=1.00,
        pore_pressure_sg=1.19,
        frac_gradient_sg=1.76,
        drillability=0.50,
        hazards=(
            Hazard("KICK", 0.29, 5, "gas influx from the Barail pay sand"),
            Hazard("STUCK_PIPE", 0.22, 4, "differential sticking across the depleted pay"),
            Hazard("MUD_LOSS", 0.20, 3, "losses into the reservoir after raising mud weight for kick margin"),
        ),
        is_reservoir=True,
        reservoir=Reservoir(
            fluid="oil and gas",
            porosity_pct=17.5,
            permeability_md=180.0,
            water_saturation_pct=28.0,
            net_to_gross=0.58,
            depletion_sg_per_year=0.0045,
            typical_pay_gross_m=210.0,
        ),
        cement_bond_quality=0.78,
    ),
    Formation(
        name="Kopili Shale",
        aliases=("Kopili", "Kopili Formation", "Kopili Fm", "Kopili Shale"),
        lithology="Dark grey marine shale with thin limestone stringers",
        ref_top_m=3640.0,
        dip_factor=1.08,
        pore_pressure_sg=1.38,
        frac_gradient_sg=1.83,
        drillability=0.44,
        hazards=(
            Hazard("KICK", 0.24, 5, "overpressured shale entering an under-balanced hole"),
            Hazard("WELLBORE_INSTABILITY", 0.35, 4, "over-pressured shale sloughing"),
            Hazard("HIGH_TORQUE_DRAG", 0.27, 3, "narrow mud-weight window and cuttings loading"),
        ),
        cement_bond_quality=0.65,
    ),
    Formation(
        name="Sylhet Limestone",
        aliases=("Sylhet", "Sylhet Limestone", "Sylhet Fm", "Sylhet Ls"),
        lithology="Fossiliferous limestone, locally fractured and karstified",
        ref_top_m=4040.0,
        dip_factor=1.14,
        pore_pressure_sg=1.24,
        frac_gradient_sg=1.68,
        drillability=0.38,
        hazards=(
            Hazard("MUD_LOSS", 0.57, 5, "total losses into fractured and karstified limestone"),
            Hazard("STUCK_PIPE", 0.16, 4, "string stuck after losing returns and dropping the annular level"),
        ),
        cement_bond_quality=0.4,
    ),
    Formation(
        name="Langpar",
        aliases=("Langpar", "Langpar Formation", "Langpar Fm"),
        lithology="Argillaceous limestone and calcareous shale",
        ref_top_m=4360.0,
        dip_factor=1.18,
        pore_pressure_sg=1.26,
        frac_gradient_sg=1.72,
        drillability=0.35,
        hazards=(
            Hazard("MUD_LOSS", 0.31, 4, "losses into fractured carbonate"),
            Hazard("TIGHT_HOLE", 0.19, 3, "reactive calcareous shale"),
        ),
        cement_bond_quality=0.62,
    ),
    Formation(
        name="Basement",
        aliases=("Archean Basement", "Basement", "Granitic Gneiss", "Archaean"),
        lithology="Weathered and fractured granitic gneiss",
        ref_top_m=4650.0,
        dip_factor=1.22,
        pore_pressure_sg=1.10,
        frac_gradient_sg=1.90,
        drillability=0.18,
        hazards=(
            Hazard("MUD_LOSS", 0.34, 4, "losses into the fractured weathered basement"),
            Hazard("LOW_ROP", 0.52, 2, "hard crystalline rock destroying bit life"),
        ),
        cement_bond_quality=0.7,
    ),
)

FORMATION_BY_NAME = {f.name: f for f in STRATIGRAPHY}
FORMATION_ORDER = {f.name: i for i, f in enumerate(STRATIGRAPHY)}

# Alias lookup used by the document extractor to normalise whatever spelling a
# WCR happens to use.
_ALIAS_INDEX: dict[str, str] = {}
for _f in STRATIGRAPHY:
    _ALIAS_INDEX[_f.name.lower()] = _f.name
    for _a in _f.aliases:
        _ALIAS_INDEX[_a.lower()] = _f.name


def normalise_formation(raw: str) -> str | None:
    """Map a free-text formation mention onto a canonical formation name."""
    if not raw:
        return None
    key = " ".join(raw.strip().lower().replace(".", " ").split())
    if key in _ALIAS_INDEX:
        return _ALIAS_INDEX[key]
    # Longest alias first, so "tipam sandstone" wins over "tipam".
    for alias in sorted(_ALIAS_INDEX, key=len, reverse=True):
        if alias in key:
            return _ALIAS_INDEX[alias]
    return None


@dataclass(frozen=True)
class Field:
    """An OIL operating area in Upper Assam."""

    name: str
    code: str
    lat: float
    lon: float
    # Anticlinal closure: amplitude (m of uplift) and radius (km).
    structure_amplitude_m: float
    structure_radius_km: float
    ground_level_m: float


# Surface field centres to roughly a kilometre, all inside OIL's Upper Assam
# operating area.
FIELDS: tuple[Field, ...] = (
    Field("Naharkatiya", "NHK", 27.2930, 95.3360, 165.0, 4.2, 122.0),
    Field("Moran", "MRN", 27.1830, 94.9160, 140.0, 3.8, 108.0),
    Field("Duliajan", "DLJ", 27.3600, 95.3200, 120.0, 3.4, 128.0),
    Field("Hugrijan", "HGJ", 27.4120, 95.4410, 95.0, 2.9, 131.0),
    Field("Tengakhat", "TGK", 27.3050, 95.1240, 110.0, 3.1, 118.0),
    Field("Shalmari", "SLM", 27.4480, 95.2380, 88.0, 2.6, 134.0),
    Field("Dikom", "DKM", 27.4760, 95.0820, 102.0, 3.0, 125.0),
    Field("Barekuri", "BRK", 27.4950, 95.3640, 78.0, 2.4, 137.0),
    Field("Jorajan", "JRJ", 27.2410, 95.2270, 125.0, 3.3, 115.0),
    Field("Makum", "MKM", 27.5210, 95.4720, 84.0, 2.5, 142.0),
)

FIELD_BY_CODE = {f.code: f for f in FIELDS}


@dataclass(frozen=True)
class Fault:
    """A normal fault segment, modelled as a line with a throw."""

    name: str
    lat: float
    lon: float
    strike_deg: float
    throw_m: float
    width_km: float


# NE-SW trending faults, sub-parallel to the Naga thrust front.
FAULTS: tuple[Fault, ...] = (
    Fault("Naharkatiya Fault", 27.3200, 95.2700, 48.0, 85.0, 1.6),
    Fault("Moran Fault", 27.2200, 94.9700, 52.0, 62.0, 1.4),
    Fault("Hugrijan Fault", 27.4400, 95.3900, 44.0, 48.0, 1.2),
)


# Hazard taxonomy shared across ingestion, correlation and the UI.
EVENT_TYPES: tuple[str, ...] = (
    "MUD_LOSS",
    "STUCK_PIPE",
    "KICK",
    "WELLBORE_INSTABILITY",
    "TIGHT_HOLE",
    "HIGH_TORQUE_DRAG",
    "BIT_BALLING",
    "WASHOUT",
    "LOW_ROP",
    "CEMENTING_ISSUE",
    "OVERPRESSURE",
    "EQUIPMENT_FAILURE",
)

EVENT_LABELS: dict[str, str] = {
    "MUD_LOSS": "Mud loss / lost circulation",
    "STUCK_PIPE": "Stuck pipe",
    "KICK": "Kick / well control",
    "WELLBORE_INSTABILITY": "Wellbore instability",
    "TIGHT_HOLE": "Tight hole / pack-off",
    "HIGH_TORQUE_DRAG": "High torque and drag",
    "BIT_BALLING": "Bit balling",
    "WASHOUT": "Hole washout",
    "LOW_ROP": "Low ROP",
    "CEMENTING_ISSUE": "Cementing problem",
    "OVERPRESSURE": "Overpressured zone",
    "EQUIPMENT_FAILURE": "Equipment failure",
}

# Hazards that happen to the *hole* as it is drilled, versus hazards that
# happen to a *casing string* when it is run and cemented. Cementing trouble
# is tied to a casing shoe, not to a drilling depth, so it is generated and
# correlated on a different axis from the rest.
CASING_EVENT_TYPES: tuple[str, ...] = ("CEMENTING_ISSUE",)


@dataclass(frozen=True)
class CementingHazard:
    """A way a cement job goes wrong, and what in the hole causes it."""

    mode: str                 # machine-readable failure mode
    label: str
    # Which condition across the cemented interval drives it.
    driver: str               # loss_zone | poor_bond | washout | gas_bearing
    severity: int
    mechanism: str


CEMENTING_HAZARDS: tuple[CementingHazard, ...] = (
    CementingHazard(
        mode="LOSSES_DURING_CEMENTING",
        label="Losses while cementing",
        driver="loss_zone",
        severity=4,
        mechanism="cement slurry lost into a permeable or fractured interval, "
                  "leaving the top of cement below the planned depth",
    ),
    CementingHazard(
        mode="TOC_BELOW_PLAN",
        label="Top of cement below plan",
        driver="loss_zone",
        severity=3,
        mechanism="cement column fell short of the planned top, leaving the "
                  "previous shoe uncovered",
    ),
    CementingHazard(
        mode="POOR_BOND",
        label="Poor cement bond",
        driver="poor_bond",
        severity=3,
        mechanism="bond log showed poor to fair bonding across the interval, "
                  "risking annular communication",
    ),
    CementingHazard(
        mode="CHANNELLING",
        label="Cement channelling",
        driver="washout",
        severity=3,
        mechanism="washed-out hole left the casing poorly centralised, so the "
                  "slurry channelled up one side of the annulus",
    ),
    CementingHazard(
        mode="GAS_MIGRATION",
        label="Annular gas migration",
        driver="gas_bearing",
        severity=4,
        mechanism="gas percolated through the setting cement, producing "
                  "sustained casing pressure",
    ),
    CementingHazard(
        mode="REMEDIAL_SQUEEZE",
        label="Remedial squeeze required",
        driver="poor_bond",
        severity=4,
        mechanism="the primary cement job failed acceptance and a remedial "
                  "squeeze was required before drilling ahead",
    ),
)

CEMENTING_HAZARD_BY_MODE = {h.mode: h for h in CEMENTING_HAZARDS}

# Typical non-productive time in hours per severity step, used when the
# generator has to put an NPT figure into a report.
NPT_HOURS_BY_SEVERITY: dict[int, tuple[float, float]] = {
    1: (0.5, 3.0),
    2: (2.0, 8.0),
    3: (6.0, 22.0),
    4: (16.0, 60.0),
    5: (36.0, 150.0),
}


# --------------------------------------------------------------------------
# Geometry helpers
# --------------------------------------------------------------------------

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from point 1 to point 2, degrees from north."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    y = math.sin(dlam) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlam)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def local_xy_km(lat: float, lon: float) -> tuple[float, float]:
    """Flat-earth offsets in km from the structural reference point."""
    x = (lon - REF_LON) * 111.320 * math.cos(math.radians(REF_LAT))
    y = (lat - REF_LAT) * 110.574
    return x, y


def _fault_throw_at(lat: float, lon: float) -> float:
    """Cumulative downthrow from nearby faults, in metres."""
    total = 0.0
    px, py = local_xy_km(lat, lon)
    for flt in FAULTS:
        fx, fy = local_xy_km(flt.lat, flt.lon)
        strike = math.radians(flt.strike_deg)
        # Signed perpendicular distance from the fault line.
        perp = (px - fx) * math.cos(strike) - (py - fy) * math.sin(strike)
        # Smooth step: the downthrown side sits deeper.
        total += flt.throw_m * math.tanh(perp / max(flt.width_km, 0.2))
    return total


def _structural_relief_at(lat: float, lon: float) -> float:
    """Uplift in metres from the anticlinal closures that host the fields."""
    uplift = 0.0
    px, py = local_xy_km(lat, lon)
    for fld in FIELDS:
        fx, fy = local_xy_km(fld.lat, fld.lon)
        d2 = (px - fx) ** 2 + (py - fy) ** 2
        uplift += fld.structure_amplitude_m * math.exp(-d2 / (2 * fld.structure_radius_km ** 2))
    return uplift


def formation_top_tvd(formation: Formation, lat: float, lon: float) -> float:
    """
    Predicted true vertical depth (m below ground level) of a formation top at
    a surface location, from the regional structural model: regional dip plus
    anticlinal closure plus fault throw.  Shallow units feel less of all
    three, which is what ``dip_factor`` encodes.
    """
    px, py = local_xy_km(lat, lon)
    az = math.radians(REGIONAL_DIP_AZIMUTH)
    # Distance down-dip, positive towards the SE.
    downdip_km = px * math.sin(az) + py * math.cos(az)
    regional = REGIONAL_DIP_M_PER_KM * downdip_km * formation.dip_factor
    structure = -_structural_relief_at(lat, lon) * formation.dip_factor
    faulting = _fault_throw_at(lat, lon) * 0.01 * formation.dip_factor
    return max(0.0, formation.ref_top_m + regional + structure + faulting)


def predicted_column(lat: float, lon: float) -> list[tuple[str, float]]:
    """
    The full predicted formation column at a surface location, as
    ``[(formation_name, top_tvd_m), ...]`` ordered youngest to oldest and
    forced to increase monotonically with depth.
    """
    column: list[tuple[str, float]] = []
    last = -1.0
    for f in STRATIGRAPHY:
        top = formation_top_tvd(f, lat, lon)
        # Structural modelling can invert thin units; enforce ordering.
        top = max(top, last + 15.0)
        column.append((f.name, round(top, 1)))
        last = top
    return column


def formation_at_depth(column: Sequence[tuple[str, float]], tvd: float) -> str | None:
    """Which formation a TVD falls in, given a well's formation column."""
    hit = None
    for name, top in column:
        if tvd >= top:
            hit = name
        else:
            break
    return hit


def ground_level_at(lat: float, lon: float) -> float:
    """Ground elevation above MSL, inverse-distance weighted from the fields."""
    num = 0.0
    den = 0.0
    for fld in FIELDS:
        d = haversine_km(lat, lon, fld.lat, fld.lon) + 0.5
        w = 1.0 / (d ** 2)
        num += fld.ground_level_m * w
        den += w
    return round(num / den, 1)
