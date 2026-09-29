"""Well headers, formation tops, drilling logs and events."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..assam_geology import FIELDS, STRATIGRAPHY, haversine_km
from ..deps import get_store, require_well
from ..engine.geometry import Trajectory

router = APIRouter(prefix="/api", tags=["wells"])


@router.get("/stats", summary="Knowledge-base summary")
def stats() -> dict:
    """Headline counts for the dashboard, plus the last ingestion run."""
    store = get_store()
    return {
        **store.stats(),
        "event_types": store.event_type_counts(),
        "formations": store.formation_event_counts(),
    }


@router.get("/fields", summary="Operating areas")
def fields() -> list[dict]:
    store = get_store()
    counts: dict[str, int] = {}
    for w in store.wells():
        counts[w["field_code"]] = counts.get(w["field_code"], 0) + 1
    return [
        {
            "name": f.name,
            "code": f.code,
            "lat": f.lat,
            "lon": f.lon,
            "well_count": counts.get(f.code, 0),
            "ground_level_m": f.ground_level_m,
        }
        for f in FIELDS
    ]


@router.get("/stratigraphy", summary="Upper Assam reference column")
def stratigraphy() -> list[dict]:
    """
    The regional column NWIS correlates against, including the hazards each
    unit is known for.  This is reference data, not per-well data.
    """
    return [
        {
            "name": f.name,
            "lithology": f.lithology,
            "reference_top_m": f.ref_top_m,
            "pore_pressure_sg": f.pore_pressure_sg,
            "frac_gradient_sg": f.frac_gradient_sg,
            "is_reservoir": f.is_reservoir,
            "hazards": [
                {"event_type": h.event_type, "probability": h.probability,
                 "severity": h.severity, "mechanism": h.mechanism}
                for h in f.hazards
            ],
        }
        for f in STRATIGRAPHY
    ]


@router.get("/wells", summary="List wells")
def list_wells(
    status: str | None = Query(None, description="Completed | Drilling"),
    field_code: str | None = Query(None),
    q: str | None = Query(None, description="Free-text match on well name or id"),
    near: str | None = Query(None, description="Well id to measure distance from"),
    radius_km: float | None = Query(None, ge=0, description="Only with 'near'"),
) -> list[dict]:
    store = get_store()
    rows = store.wells(status=status)

    if field_code:
        rows = [r for r in rows if r["field_code"] == field_code]
    if q:
        needle = q.lower()
        rows = [r for r in rows
                if needle in r["well_name"].lower() or needle in r["well_id"].lower()]

    if near:
        origin = store.well(near)
        if origin is None:
            raise HTTPException(404, f"Unknown well: {near}")
        for r in rows:
            r["distance_km"] = round(haversine_km(
                origin["bottom_lat"], origin["bottom_lon"],
                r["bottom_lat"], r["bottom_lon"]), 2)
        if radius_km is not None:
            rows = [r for r in rows if r["distance_km"] <= radius_km]
        rows.sort(key=lambda r: r["distance_km"])

    return rows


@router.get("/wells/{well_id}", summary="Well detail")
def well_detail(well_id: str) -> dict:
    store = get_store()
    well = require_well(well_id)
    events = store.events(well_id=well_id)
    citation_map = store.citations_bulk([e["event_id"] for e in events])
    for e in events:
        e["citations"] = citation_map.get(e["event_id"], [])

    trajectory = Trajectory.from_well(well)
    bit_md = well.get("current_bit_md_m") or well.get("td_md_m") or 0.0

    return {
        "well": well,
        "formation_tops": store.tops(well_id),
        "section_averages": store.section_averages(well_id),
        "events": events,
        "documents": store.documents(well_id),
        "bit_md_m": bit_md,
        "bit_tvd_m": round(trajectory.tvd_at(bit_md), 1),
        "progress_pct": round(
            100.0 * bit_md / float(well.get("planned_td_md_m") or well["td_md_m"] or 1), 1),
    }


@router.get("/wells/{well_id}/log", summary="Depth-indexed drilling parameters")
def well_log(
    well_id: str,
    md_from: float = Query(0.0, ge=0),
    md_to: float = Query(1e9, ge=0),
    every: int = Query(1, ge=1, le=50, description="Return every Nth sample"),
) -> dict:
    require_well(well_id)
    rows = get_store().drilling_log(well_id, md_from=md_from, md_to=md_to, every=every)
    return {"well_id": well_id, "count": len(rows), "samples": rows}


@router.get("/wells/{well_id}/events", summary="Extracted drilling events")
def well_events(
    well_id: str,
    event_type: str | None = None,
    min_severity: int = Query(0, ge=0, le=5),
) -> list[dict]:
    require_well(well_id)
    store = get_store()
    events = store.events(well_id=well_id, event_type=event_type, min_severity=min_severity)
    citation_map = store.citations_bulk([e["event_id"] for e in events])
    for e in events:
        e["citations"] = citation_map.get(e["event_id"], [])
    return events
