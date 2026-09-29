"""
The searchable knowledge repository.

Search, lessons learnt, casing programmes, cementing records and reservoir
data - the parts of the problem statement that are about *finding and
comparing* what the asset already knows, rather than about the well that is
drilling right now.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..assam_geology import EVENT_LABELS, haversine_km
from ..config import settings
from ..deps import get_store, require_well
from ..engine.correlate import build_correlation

router = APIRouter(prefix="/api", tags=["knowledge"])


@router.get("/search", summary="Full-text search across the knowledge base")
def search(
    q: str = Query(..., min_length=2, description="Free text, e.g. 'differential sticking tipam'"),
    kind: str | None = Query(None, description="event | lesson | document"),
    well_id: str | None = Query(None),
    limit: int = Query(40, ge=1, le=200),
) -> dict:
    """
    One search box over everything: extracted events, lessons learnt, and the
    raw text of every report page.

    Matched terms come back wrapped in `<<` `>>` so the caller can highlight
    them, and every hit carries enough identity to open the thing it found.
    """
    store = get_store()
    try:
        hits = store.search(q, kind=kind, well_id=well_id, limit=limit)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    wells = {w["well_id"]: w for w in store.wells()}
    for h in hits:
        w = wells.get(h["well_id"])
        h["well_name"] = w["well_name"] if w else h["well_id"]
        h["field_name"] = w["field_name"] if w else None
        # Give the caller what it needs to open the hit.
        if h["kind"] == "document":
            doc_id, page, line_no = h["ref_id"].split("#")
            h["open"] = {"document_id": doc_id, "page": int(page), "line_no": int(line_no)}
        else:
            h["open"] = {"id": int(h["ref_id"])}
    counts: dict[str, int] = {}
    for h in hits:
        counts[h["kind"]] = counts.get(h["kind"], 0) + 1
    return {"query": q, "count": len(hits), "by_kind": counts, "results": hits}


@router.get("/lessons", summary="Lessons learnt across the asset")
def lessons(
    formation: str | None = None,
    hazard_type: str | None = None,
    well_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    """
    What previous crews wrote down for the next well.

    These are read out of the "lessons learnt and recommendations" section of
    each completion report. They are written in the future tense, so they are
    extracted by a separate pass from the event extractor - see
    `ingest/extract.py`.
    """
    store = get_store()
    rows = store.lessons(well_id=well_id, formation=formation,
                         hazard_type=hazard_type, limit=limit)
    wells = {w["well_id"]: w for w in store.wells()}
    for r in rows:
        w = wells.get(r["well_id"])
        r["well_name"] = w["well_name"] if w else r["well_id"]
        r["field_name"] = w["field_name"] if w else None
        r["hazard_label"] = EVENT_LABELS.get(r["hazard_type"] or "", r["hazard_type"])
    return {"count": len(rows), "lessons": rows}


@router.get("/wells/{well_id}/lessons", summary="Lessons recorded on one well")
def well_lessons(well_id: str) -> list[dict]:
    require_well(well_id)
    rows = get_store().lessons(well_id=well_id)
    for r in rows:
        r["hazard_label"] = EVENT_LABELS.get(r["hazard_type"] or "", r["hazard_type"])
    return rows


@router.get("/wells/{well_id}/casing", summary="Casing programme and cementing record")
def well_casing(well_id: str) -> dict:
    """
    The casing programme and how each string was cemented.

    Casing strings marked `extracted` were read out of the completion report's
    own table by the document pipeline; `database` means the well has no
    completion report yet and the programme came from the drilling database.
    """
    require_well(well_id)
    store = get_store()
    return {
        "well_id": well_id,
        "casing": store.casing(well_id),
        "cement_jobs": store.cement_jobs(well_id),
    }


@router.get("/wells/{well_id}/reservoir", summary="Reservoir intervals in one well")
def well_reservoir(well_id: str) -> list[dict]:
    require_well(well_id)
    return get_store().reservoir(well_id)


@router.get("/wells/{well_id}/casing-comparison",
            summary="Compare this well's casing programme with its offsets")
def casing_comparison(
    well_id: str,
    radius_km: float = Query(settings.default_radius_km, gt=0, le=100),
    limit: int = Query(10, ge=1, le=40),
) -> dict:
    """
    How nearby wells cased and cemented the same section.

    Shoe depths are compared in the reference well's depth frame, through the
    same formation ties the risk engine uses - so "they set the 9-5/8in at the
    top of the coal shale" survives the fact that the coal shale is 200 m
    deeper over there.
    """
    reference = require_well(well_id)
    store = get_store()
    ref_tops = store.tops(well_id)

    candidates = []
    for cand in store.wells():
        if cand["well_id"] == well_id:
            continue
        d = haversine_km(reference["bottom_lat"], reference["bottom_lon"],
                         cand["bottom_lat"], cand["bottom_lon"])
        if d <= radius_km:
            candidates.append((cand, d))
    candidates.sort(key=lambda p: p[1])
    candidates = candidates[:limit]

    ids = [c["well_id"] for c, _ in candidates]
    casing_by_well = store.casing_bulk(ids)
    tops_by_well = store.tops_bulk(ids)

    out = []
    for cand, dist in candidates:
        corr = build_correlation(well_id, ref_tops, cand["well_id"],
                                 tops_by_well.get(cand["well_id"], []))
        strings = []
        for c in casing_by_well.get(cand["well_id"], []):
            shoe_tvd = c["shoe_tvd_m"]
            strings.append({
                **c,
                "equivalent_tvd_here_m": (round(corr.to_reference(shoe_tvd), 1)
                                          if shoe_tvd is not None else None),
            })
        out.append({
            "well_id": cand["well_id"],
            "well_name": cand["well_name"],
            "status": cand["status"],
            "distance_km": round(dist, 2),
            "correlation_quality": round(corr.quality, 3),
            "casing": strings,
            "cement_jobs": store.cement_jobs(cand["well_id"]),
        })

    return {
        "well_id": well_id,
        "reference_casing": store.casing(well_id),
        "reference_cement_jobs": store.cement_jobs(well_id),
        "offsets": out,
    }


@router.get("/wells/{well_id}/experience",
            summary="Everything the knowledge base holds on one offset well")
def well_experience(well_id: str) -> dict:
    """
    The offset-well dossier.

    "Instant access to historical drilling experiences and operational events
    from offset wells" means being able to open a neighbour and read its whole
    story without navigating away from the well you are drilling.
    """
    well = require_well(well_id)
    store = get_store()

    events = store.events(well_id=well_id)
    citation_map = store.citations_bulk([e["event_id"] for e in events])
    for e in events:
        e["citations"] = citation_map.get(e["event_id"], [])
        e["label"] = EVENT_LABELS.get(e["event_type"], e["event_type"])

    lessons_rows = store.lessons(well_id=well_id)
    for r in lessons_rows:
        r["hazard_label"] = EVENT_LABELS.get(r["hazard_type"] or "", r["hazard_type"])

    npt_by_type: dict[str, float] = {}
    for e in events:
        npt_by_type[e["event_type"]] = npt_by_type.get(e["event_type"], 0.0) + float(
            e.get("npt_hours") or 0.0)

    return {
        "well": well,
        "formation_tops": store.tops(well_id),
        "events": events,
        "lessons": lessons_rows,
        "casing": store.casing(well_id),
        "cement_jobs": store.cement_jobs(well_id),
        "reservoir": store.reservoir(well_id),
        "documents": store.documents(well_id),
        "section_averages": store.section_averages(well_id),
        "summary": {
            "event_count": len(events),
            "lesson_count": len(lessons_rows),
            "total_npt_hours": round(sum(npt_by_type.values()), 1),
            "npt_by_event_type": {k: round(v, 1) for k, v in
                                  sorted(npt_by_type.items(), key=lambda kv: -kv[1])},
            "worst_event": max(events, key=lambda e: e["severity"], default=None),
        },
    }
