"""
Offset-well relevance ranking.

The nearest well is not always the useful one.  A well 2 km away that stopped
in the Tipam tells you nothing about the Barail pay you are about to drill; a
well 9 km away on the same structural block that drilled the identical section
with the same mud system tells you a great deal.

So NWIS scores every candidate across six independent dimensions and combines
them with configurable weights.  Each dimension is reported separately
alongside the total, because a driller needs to see *why* a well was ranked -
"high relevance" with no explanation is not something anyone will act on.

    geology      do the two wells share a section, and does it conform?
    depth        does the offset cover the interval we care about?
    distance     how far away is it, measured bottom-hole to bottom-hole?
    trajectory   was it drilled at a similar angle, in a similar direction?
    parameters   was it drilled with similar ROP / WOB / mud weight?
    experience   does it actually carry event history worth learning from?

A seventh, recency, is a small tie-breaker: a 2023 well reflects current
practice better than a 2009 one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Sequence

from ..assam_geology import EVENT_LABELS, haversine_km
from .correlate import Correlation, build_correlation, column_similarity, interval_overlap

# Distance at which the proximity score falls to 1/e.  Tuned to the Upper
# Assam field spacing: wells on the same structure are typically <5 km apart.
DISTANCE_DECAY_KM = 6.0

BAND_HIGH = 0.68
BAND_MEDIUM = 0.45


@dataclass
class RelevanceWeights:
    geology: float = 0.26
    depth: float = 0.14
    distance: float = 0.18
    trajectory: float = 0.10
    parameters: float = 0.12
    experience: float = 0.15
    recency: float = 0.05

    def normalised(self) -> dict[str, float]:
        raw = {
            "geology": max(0.0, self.geology),
            "depth": max(0.0, self.depth),
            "distance": max(0.0, self.distance),
            "trajectory": max(0.0, self.trajectory),
            "parameters": max(0.0, self.parameters),
            "experience": max(0.0, self.experience),
            "recency": max(0.0, self.recency),
        }
        total = sum(raw.values()) or 1.0
        return {k: v / total for k, v in raw.items()}

    @classmethod
    def from_dict(cls, data: dict | None) -> "RelevanceWeights":
        if not data:
            return cls()
        known = {f: data[f] for f in cls().__dict__ if f in data}
        return cls(**known)


@dataclass
class Dimension:
    key: str
    label: str
    score: float
    weight: float
    detail: str

    @property
    def contribution(self) -> float:
        return self.score * self.weight

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "score": round(self.score, 3),
            "weight": round(self.weight, 3),
            "contribution": round(self.contribution, 4),
            "detail": self.detail,
        }


@dataclass
class OffsetWell:
    well: dict
    score: float
    band: str
    distance_km: float
    surface_distance_km: float
    bearing_label: str
    dimensions: list[Dimension]
    supports: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    correlation: Correlation | None = None
    event_count: int = 0
    npt_hours: float = 0.0
    shared_formations: list[str] = field(default_factory=list)

    def as_dict(self, include_correlation: bool = True) -> dict:
        out = {
            "well_id": self.well["well_id"],
            "well_name": self.well["well_name"],
            "field_name": self.well["field_name"],
            "status": self.well["status"],
            "purpose": self.well["purpose"],
            "well_type": self.well["well_type"],
            "spud_date": self.well["spud_date"],
            "completion_date": self.well["completion_date"],
            "td_md_m": self.well["td_md_m"],
            "td_tvd_m": self.well["td_tvd_m"],
            "target_formation": self.well["target_formation"],
            "surface_lat": self.well["surface_lat"],
            "surface_lon": self.well["surface_lon"],
            "bottom_lat": self.well["bottom_lat"],
            "bottom_lon": self.well["bottom_lon"],
            "relevance_score": round(self.score, 4),
            "relevance_band": self.band,
            "distance_km": round(self.distance_km, 2),
            "surface_distance_km": round(self.surface_distance_km, 2),
            "bearing": self.bearing_label,
            "dimensions": [d.as_dict() for d in self.dimensions],
            "supports": self.supports,
            "caveats": self.caveats,
            "event_count": self.event_count,
            "npt_hours": round(self.npt_hours, 1),
            "shared_formations": self.shared_formations,
        }
        if include_correlation and self.correlation is not None:
            out["correlation"] = self.correlation.as_dict()
        return out


# --------------------------------------------------------------------------
# Individual dimension scores
# --------------------------------------------------------------------------


def _score_distance(distance_km: float, decay_km: float = DISTANCE_DECAY_KM) -> tuple[float, str]:
    score = math.exp(-distance_km / max(decay_km, 0.5))
    return score, f"{distance_km:.1f} km bottom-hole separation"


def _score_geology(ref_tops: Sequence[dict], cand_tops: Sequence[dict]) -> tuple[float, str, dict]:
    score, parts = column_similarity(ref_tops, cand_tops)
    detail = (f"{parts['shared']} shared formations, "
              f"tops conform to {parts['shift_spread_m']:.0f} m")
    return score, detail, parts


def _score_depth(ref_well: dict, cand_well: dict,
                 focus: tuple[float, float] | None) -> tuple[float, str]:
    """
    Does the candidate cover the depth that matters?

    When a focus interval is given - the section ahead of the bit - coverage
    of *that* is what counts.  Without one, fall back to overall agreement in
    total depth.
    """
    cand_td = float(cand_well["td_tvd_m"] or 0.0)
    if focus:
        lo, hi = focus
        covered = interval_overlap(lo, hi, 0.0, cand_td)
        span = max(hi - lo, 1.0)
        score = covered / span
        if score >= 0.999:
            detail = f"covers the full {lo:,.0f}-{hi:,.0f} m interval of interest"
        elif score <= 0.001:
            detail = f"TD {cand_td:,.0f} m is above the interval of interest"
        else:
            detail = f"covers {score:.0%} of the {lo:,.0f}-{hi:,.0f} m interval"
        return score, detail

    ref_td = float(ref_well["planned_td_md_m"] or ref_well["td_tvd_m"] or 0.0)
    if ref_td <= 0 or cand_td <= 0:
        return 0.0, "depth range unknown"
    score = min(ref_td, cand_td) / max(ref_td, cand_td)
    return score, f"TD {cand_td:,.0f} m TVD against {ref_td:,.0f} m"


def _score_trajectory(ref: dict, cand: dict) -> tuple[float, str]:
    ref_inc = float(ref["max_inclination_deg"] or 0.0)
    cand_inc = float(cand["max_inclination_deg"] or 0.0)
    inc_score = 1.0 - min(1.0, abs(ref_inc - cand_inc) / 90.0)

    # Azimuth only means anything once a well is actually deviated.
    if ref_inc > 10.0 and cand_inc > 10.0:
        delta = math.radians(float(ref["azimuth_deg"] or 0.0) - float(cand["azimuth_deg"] or 0.0))
        az_score = (1.0 + math.cos(delta)) / 2.0
        az_note = f", azimuth {abs(math.degrees(delta)) % 360:.0f} deg apart"
    else:
        az_score = 1.0
        az_note = ""

    type_score = 1.0 if ref["well_type"] == cand["well_type"] else 0.45
    score = 0.50 * inc_score + 0.25 * az_score + 0.25 * type_score
    return score, f"{cand['well_type'].lower()}, max inclination {cand_inc:.0f} deg{az_note}"


def _score_parameters(ref_sections: dict[str, dict], cand_sections: dict[str, dict],
                      shared: Sequence[str]) -> tuple[float, str]:
    """Compare per-formation drilling parameters over the section both wells drilled."""
    metrics = ("rop_m_hr", "wob_t", "rpm", "mud_weight_sg")
    sims: list[float] = []
    for formation in shared:
        a = ref_sections.get(formation)
        b = cand_sections.get(formation)
        if not a or not b:
            continue
        for m in metrics:
            av, bv = a.get(m), b.get(m)
            if av is None or bv is None:
                continue
            denom = 0.5 * (abs(av) + abs(bv))
            if denom <= 1e-9:
                continue
            sims.append(max(0.0, 1.0 - abs(av - bv) / denom))
    if not sims:
        return 0.0, "no comparable drilled section"
    score = sum(sims) / len(sims)
    return score, f"parameters compared over {len(sims) // len(metrics)} shared sections"


def _score_experience(events: Sequence[dict], shared: set[str],
                      focus_formations: set[str] | None) -> tuple[float, str, float]:
    """
    How much usable experience this well carries.

    Events are only worth something if they happened in rock the current well
    will actually drill, so events outside the shared section are discounted
    heavily rather than dropped - they still say something about the area.
    """
    weighted = 0.0
    counted = 0
    npt = 0.0
    for ev in events:
        formation = ev.get("formation")
        if focus_formations and formation in focus_formations:
            relevance = 1.0
        elif formation in shared:
            relevance = 0.55
        else:
            relevance = 0.15
        weighted += relevance * (ev["severity"] / 5.0) * float(ev.get("confidence") or 0.6)
        npt += float(ev.get("npt_hours") or 0.0)
        counted += 1

    score = 1.0 - math.exp(-weighted / 1.6)
    if counted == 0:
        detail = "no recorded drilling events"
    else:
        detail = f"{counted} recorded events, {npt:.0f} hrs NPT"
    return score, detail, npt


def _score_recency(cand: dict) -> tuple[float, str]:
    iso = cand.get("completion_date") or cand.get("spud_date")
    if not iso:
        return 0.4, "date unknown"
    try:
        when = date.fromisoformat(iso)
    except ValueError:
        return 0.4, "date unparseable"
    years = max(0.0, (date.today() - when).days / 365.25)
    return math.exp(-years / 11.0), f"drilled {when.year}"


def _bearing_label(bearing: float) -> str:
    points = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
              "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
    return points[int((bearing + 11.25) % 360 // 22.5)]


# --------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------


def rank_offsets(
    store: Any,
    reference_well_id: str,
    radius_km: float = 15.0,
    weights: RelevanceWeights | None = None,
    focus_interval_tvd: tuple[float, float] | None = None,
    limit: int | None = None,
    min_score: float = 0.0,
) -> list[OffsetWell]:
    """
    Rank every completed well within ``radius_km`` of the reference well.

    ``focus_interval_tvd`` narrows "relevant" to a specific depth window in
    the reference well - the section ahead of the bit, when the caller is the
    risk engine.  Without it, the whole well is the interval of interest.
    """
    w = (weights or RelevanceWeights()).normalised()
    reference = store.well(reference_well_id)
    if reference is None:
        return []

    ref_tops = store.tops(reference_well_id)
    ref_sections = store.section_averages(reference_well_id)

    # Candidate selection: completed wells inside the radius.  This is the
    # PostGIS ST_DWithin query in the production deployment.
    candidates: list[dict] = []
    for cand in store.wells():
        if cand["well_id"] == reference_well_id or cand["status"] != "Completed":
            continue
        d = haversine_km(reference["bottom_lat"], reference["bottom_lon"],
                         cand["bottom_lat"], cand["bottom_lon"])
        if d <= radius_km:
            cand = dict(cand)
            cand["_distance_km"] = d
            candidates.append(cand)

    if not candidates:
        return []

    ids = [c["well_id"] for c in candidates]
    tops_by_well = store.tops_bulk(ids)
    events_by_well = store.events_bulk(ids)

    focus_formations: set[str] | None = None
    if focus_interval_tvd:
        lo, hi = focus_interval_tvd
        focus_formations = {
            t["formation"] for t in ref_tops
            if t["top_tvd_m"] <= hi and t["top_tvd_m"] >= lo - 400
        }

    results: list[OffsetWell] = []
    for cand in candidates:
        cand_id = cand["well_id"]
        cand_tops = tops_by_well.get(cand_id, [])
        cand_events = events_by_well.get(cand_id, [])

        geology_score, geology_detail, _ = _score_geology(ref_tops, cand_tops)
        distance_score, distance_detail = _score_distance(cand["_distance_km"])
        depth_score, depth_detail = _score_depth(reference, cand, focus_interval_tvd)
        traj_score, traj_detail = _score_trajectory(reference, cand)

        correlation = build_correlation(reference_well_id, ref_tops, cand_id, cand_tops)
        shared = set(correlation.shared_formations)

        param_score, param_detail = _score_parameters(
            ref_sections, store.section_averages(cand_id), correlation.shared_formations)
        exp_score, exp_detail, npt = _score_experience(cand_events, shared, focus_formations)
        rec_score, rec_detail = _score_recency(cand)

        dimensions = [
            Dimension("geology", "Geological similarity", geology_score, w["geology"], geology_detail),
            Dimension("depth", "Depth / interval coverage", depth_score, w["depth"], depth_detail),
            Dimension("distance", "Proximity", distance_score, w["distance"], distance_detail),
            Dimension("trajectory", "Trajectory similarity", traj_score, w["trajectory"], traj_detail),
            Dimension("parameters", "Drilling parameters", param_score, w["parameters"], param_detail),
            Dimension("experience", "Historical experience", exp_score, w["experience"], exp_detail),
            Dimension("recency", "Recency", rec_score, w["recency"], rec_detail),
        ]
        total = sum(d.contribution for d in dimensions)
        if total < min_score:
            continue

        band = "High" if total >= BAND_HIGH else "Medium" if total >= BAND_MEDIUM else "Low"
        supports, caveats = _explain(dimensions, correlation, cand_events, focus_formations)

        results.append(OffsetWell(
            well=cand,
            score=total,
            band=band,
            distance_km=cand["_distance_km"],
            surface_distance_km=haversine_km(
                reference["surface_lat"], reference["surface_lon"],
                cand["surface_lat"], cand["surface_lon"]),
            bearing_label=_bearing_label(_bearing(reference, cand)),
            dimensions=dimensions,
            supports=supports,
            caveats=caveats,
            correlation=correlation,
            event_count=len(cand_events),
            npt_hours=npt,
            shared_formations=correlation.shared_formations,
        ))

    results.sort(key=lambda r: -r.score)
    return results[:limit] if limit else results


def _bearing(ref: dict, cand: dict) -> float:
    from ..assam_geology import bearing_deg
    return bearing_deg(ref["bottom_lat"], ref["bottom_lon"],
                       cand["bottom_lat"], cand["bottom_lon"])


def _sentence_case(text: str) -> str:
    """Capitalise the first letter only - ``str.capitalize`` lowercases units."""
    return text[:1].upper() + text[1:] if text else text


def _explain(dimensions: Sequence[Dimension], correlation: Correlation,
             events: Sequence[dict], focus_formations: set[str] | None
             ) -> tuple[list[str], list[str]]:
    """Turn dimension scores into the short reasons the dashboard shows."""
    supports: list[str] = []
    caveats: list[str] = []

    by_key = {d.key: d for d in dimensions}

    if by_key["geology"].score >= 0.7:
        supports.append(f"Similar formation column ({len(correlation.shared_formations)} shared tops)")
    elif by_key["geology"].score < 0.45:
        caveats.append("Different formation column or poor structural conformance")

    if by_key["depth"].score >= 0.85:
        supports.append(_sentence_case(by_key["depth"].detail))
    elif by_key["depth"].score < 0.3:
        caveats.append(_sentence_case(by_key["depth"].detail))

    if by_key["distance"].score >= 0.6:
        supports.append(f"Close by - {by_key['distance'].detail}")
    elif by_key["distance"].score < 0.25:
        caveats.append(f"Distant - {by_key['distance'].detail}")

    if by_key["trajectory"].score >= 0.8:
        supports.append(f"Comparable trajectory ({by_key['trajectory'].detail})")
    elif by_key["trajectory"].score < 0.45:
        caveats.append("Markedly different well trajectory")

    if by_key["parameters"].score >= 0.75:
        supports.append("Similar drilling parameters over the shared section")
    elif by_key["parameters"].score < 0.4:
        caveats.append("Drilling parameters differ substantially")

    relevant_events = [
        e for e in events
        if not focus_formations or e.get("formation") in focus_formations
    ]
    if relevant_events:
        kinds: dict[str, int] = {}
        for e in relevant_events:
            kinds[e["event_type"]] = kinds.get(e["event_type"], 0) + 1
        top = sorted(kinds.items(), key=lambda kv: -kv[1])[:2]
        supports.append("History of " + " and ".join(
            f"{EVENT_LABELS.get(k, k).lower()} ({n})" for k, n in top))
    elif not events:
        caveats.append("No drilling events on record")

    if correlation.quality < 0.4:
        caveats.append(f"Weak depth correlation (tie spread {correlation.shift_spread_m:.0f} m)")

    return supports, caveats
