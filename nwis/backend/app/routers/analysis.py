"""Offset ranking, cross-well correlation and look-ahead risk."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..config import settings
from ..deps import get_store, require_well, weight_params
from ..engine.correlate import build_correlation
from ..engine.geometry import Trajectory
from ..engine.relevance import RelevanceWeights, rank_offsets
from ..engine.risk import look_ahead
from ..engine.signals import analyse

router = APIRouter(prefix="/api", tags=["analysis"])


@router.get("/wells/{well_id}/offsets", summary="Rank relevant nearby wells")
def offsets(
    well_id: str,
    radius_km: float = Query(settings.default_radius_km, gt=0, le=100),
    limit: int = Query(12, ge=1, le=60),
    min_score: float = Query(0.0, ge=0, le=1),
    focus_from_tvd: float | None = Query(None, ge=0, description="Restrict relevance to an interval"),
    focus_to_tvd: float | None = Query(None, ge=0),
    include_correlation: bool = Query(True),
    weights: RelevanceWeights = Depends(weight_params),
) -> dict:
    """
    Rank the wells around ``well_id`` by combined multi-parameter similarity.

    Every result carries its per-dimension breakdown, so the ranking can be
    inspected rather than taken on trust.
    """
    require_well(well_id)
    focus = None
    if focus_from_tvd is not None and focus_to_tvd is not None:
        if focus_to_tvd <= focus_from_tvd:
            raise HTTPException(400, "focus_to_tvd must be greater than focus_from_tvd")
        focus = (focus_from_tvd, focus_to_tvd)

    results = rank_offsets(
        get_store(), well_id,
        radius_km=radius_km,
        weights=weights,
        focus_interval_tvd=focus,
        limit=limit,
        min_score=min_score,
    )
    return {
        "well_id": well_id,
        "radius_km": radius_km,
        "focus_interval_tvd": list(focus) if focus else None,
        "weights": weights.normalised(),
        "count": len(results),
        "offsets": [r.as_dict(include_correlation=include_correlation) for r in results],
    }


@router.get("/wells/{well_id}/correlation/{offset_id}", summary="Cross-well correlation panel")
def correlation(well_id: str, offset_id: str,
                every: int = Query(5, ge=1, le=50)) -> dict:
    """
    The data behind a two-well correlation panel: shared formation tie points,
    both wells' logs, and the offset's events projected onto the reference
    well's depth scale.
    """
    store = get_store()
    reference = require_well(well_id)
    offset = require_well(offset_id)

    ref_tops = store.tops(well_id)
    off_tops = store.tops(offset_id)
    corr = build_correlation(well_id, ref_tops, offset_id, off_tops)
    trajectory = Trajectory.from_well(reference)

    events = store.events(well_id=offset_id)
    citation_map = store.citations_bulk([e["event_id"] for e in events])
    projected = []
    for e in events:
        if e.get("tvd_m") is None:
            continue
        ref_tvd = corr.to_reference(e["tvd_m"])
        projected.append({
            **e,
            "citations": citation_map.get(e["event_id"], []),
            "projected_tvd_m": round(ref_tvd, 1),
            "projected_md_m": round(trajectory.md_for_tvd(ref_tvd), 1),
        })

    return {
        "reference": {
            "well": reference,
            "formation_tops": ref_tops,
            "log": store.drilling_log(well_id, every=every),
        },
        "offset": {
            "well": offset,
            "formation_tops": off_tops,
            "log": store.drilling_log(offset_id, every=every),
        },
        "correlation": corr.as_dict(),
        "projected_events": projected,
    }


@router.get("/wells/{well_id}/risk", summary="Look-ahead risk alerts")
def risk(
    well_id: str,
    bit_md: float | None = Query(None, ge=0, description="Override the current bit depth"),
    lookahead_m: float = Query(settings.default_lookahead_m, gt=0, le=2000),
    radius_km: float = Query(settings.default_radius_km, gt=0, le=100),
    max_offsets: int = Query(settings.max_offsets, ge=1, le=40),
    min_relevance: float = Query(0.30, ge=0, le=1),
    weights: RelevanceWeights = Depends(weight_params),
) -> dict:
    """
    What the relevant offset wells hit in the interval ahead of the bit, with
    the evidence behind each alert.
    """
    require_well(well_id)
    result = look_ahead(
        get_store(), well_id,
        bit_md_m=bit_md,
        lookahead_m=lookahead_m,
        radius_km=radius_km,
        weights=weights,
        max_offsets=max_offsets,
        min_relevance=min_relevance,
    )
    if result is None:
        raise HTTPException(404, f"Unknown well: {well_id}")
    return result.as_dict()


@router.get("/wells/{well_id}/signals", summary="Live drilling-parameter signals")
def signals(
    well_id: str,
    bit_md: float | None = Query(None, ge=0),
    window_m: float = Query(120.0, gt=0, le=1000),
) -> dict:
    """Trend detectors over the tail of the current well's parameter feed."""
    well = require_well(well_id)
    store = get_store()
    md = bit_md if bit_md is not None else (well.get("current_bit_md_m") or well["td_md_m"])
    rows = store.drilling_log(well_id, md_to=md)
    found = analyse(rows, window_m=window_m)
    return {
        "well_id": well_id,
        "bit_md_m": md,
        "window_m": window_m,
        "latest": rows[-1] if rows else None,
        "signals": [s.as_dict() for s in found],
    }
