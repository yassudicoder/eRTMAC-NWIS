"""
Field-wide analytics.

Where the rest of the API answers "what about this well", these endpoints
answer "what about this asset": which formations cost the most rig time, and
where the same trouble keeps recurring.

The clustering endpoint is the "risk pattern detection" step: it finds
recurring hazard zones *from the extracted event record alone*, with no prior
knowledge of where the trouble areas are.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from ..assam_geology import EVENT_LABELS, haversine_km
from ..deps import get_store

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/npt", summary="Non-productive time breakdown")
def npt() -> dict:
    store = get_store()
    by_type = store.event_type_counts()
    for row in by_type:
        row["label"] = EVENT_LABELS.get(row["event_type"], row["event_type"])
    return {
        "by_event_type": by_type,
        "by_formation": store.formation_event_counts(),
        "total_npt_hours": round(sum(r["npt_hours"] for r in by_type), 1),
        "total_events": sum(r["count"] for r in by_type),
    }


@router.get("/field-summary", summary="Per-field rollup")
def field_summary() -> list[dict]:
    store = get_store()
    wells = store.wells()
    events = store.events()
    by_well = {w["well_id"]: w for w in wells}

    rollup: dict[str, dict] = {}
    for w in wells:
        r = rollup.setdefault(w["field_code"], {
            "field_code": w["field_code"], "field_name": w["field_name"],
            "wells": 0, "drilling": 0, "events": 0, "npt_hours": 0.0,
            "lat": 0.0, "lon": 0.0,
        })
        r["wells"] += 1
        r["drilling"] += 1 if w["status"] == "Drilling" else 0
        r["lat"] += w["surface_lat"]
        r["lon"] += w["surface_lon"]

    for e in events:
        w = by_well.get(e["well_id"])
        if not w:
            continue
        r = rollup[w["field_code"]]
        r["events"] += 1
        r["npt_hours"] += float(e.get("npt_hours") or 0.0)

    out = []
    for r in rollup.values():
        n = max(r["wells"], 1)
        r["lat"] = round(r["lat"] / n, 5)
        r["lon"] = round(r["lon"] / n, 5)
        r["npt_hours"] = round(r["npt_hours"], 1)
        r["npt_hours_per_well"] = round(r["npt_hours"] / n, 1)
        out.append(r)
    out.sort(key=lambda r: -r["npt_hours"])
    return out


@router.get("/clusters", summary="Recurring hazard zones")
def clusters(
    radius_km: float = Query(6.0, gt=0, le=40),
    min_wells: int = Query(3, ge=2, le=20),
) -> list[dict]:
    """
    Find places where the same hazard keeps happening in the same formation.

    Greedy agglomeration: repeatedly take the densest remaining seed, absorb
    every event of the same hazard and formation within ``radius_km``, and
    emit the group if enough distinct wells contributed.  Simple, fast and
    explainable - and it runs purely on what the extractor read out of the
    reports, so a cluster is a claim about the record, not about the model
    that happened to generate it.
    """
    store = get_store()
    wells = {w["well_id"]: w for w in store.wells()}
    events = [e for e in store.events() if e.get("formation")]

    # Group first by the things that must match exactly.
    buckets: dict[tuple[str, str], list[dict]] = {}
    for e in events:
        w = wells.get(e["well_id"])
        if not w:
            continue
        point = dict(e)
        point["lat"] = w["surface_lat"]
        point["lon"] = w["surface_lon"]
        point["well_name"] = w["well_name"]
        point["field_name"] = w["field_name"]
        buckets.setdefault((e["event_type"], e["formation"]), []).append(point)

    found: list[dict] = []
    for (event_type, formation), points in buckets.items():
        remaining = list(points)
        while remaining:
            # Seed on the point with the most neighbours.
            best_seed = None
            best_members: list[dict] = []
            for seed in remaining:
                members = [p for p in remaining
                           if haversine_km(seed["lat"], seed["lon"], p["lat"], p["lon"]) <= radius_km]
                if len(members) > len(best_members):
                    best_seed, best_members = seed, members
            if best_seed is None or not best_members:
                break

            remaining = [p for p in remaining if p not in best_members]
            distinct_wells = {p["well_id"] for p in best_members}
            if len(distinct_wells) < min_wells:
                continue

            lat = sum(p["lat"] for p in best_members) / len(best_members)
            lon = sum(p["lon"] for p in best_members) / len(best_members)
            extent = max(
                (haversine_km(lat, lon, p["lat"], p["lon"]) for p in best_members),
                default=0.0)
            depths = [p["tvd_m"] for p in best_members if p.get("tvd_m") is not None]
            severities = [p["severity"] for p in best_members]

            found.append({
                "event_type": event_type,
                "label": EVENT_LABELS.get(event_type, event_type),
                "formation": formation,
                "centre_lat": round(lat, 5),
                "centre_lon": round(lon, 5),
                "extent_km": round(extent, 2),
                "well_count": len(distinct_wells),
                "event_count": len(best_members),
                "mean_severity": round(sum(severities) / len(severities), 2),
                "max_severity": max(severities),
                "npt_hours": round(sum(float(p.get("npt_hours") or 0.0) for p in best_members), 1),
                "tvd_range_m": [round(min(depths), 1), round(max(depths), 1)] if depths else None,
                "fields": sorted({p["field_name"] for p in best_members}),
                "wells": sorted(distinct_wells),
            })

    found.sort(key=lambda c: (-c["well_count"], -c["npt_hours"]))
    return found
