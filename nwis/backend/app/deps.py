"""Shared dependencies: the knowledge-base handle and query helpers."""

from __future__ import annotations

from functools import lru_cache

from fastapi import HTTPException, Query

from .config import settings
from .engine.model import RiskModelBundle
from .engine.relevance import RelevanceWeights
from .store import Store

_store: Store | None = None
_model: RiskModelBundle | None = None
_model_loaded = False


def get_store() -> Store:
    """The process-wide knowledge base handle."""
    global _store
    if _store is None:
        if not settings.db_path.exists():
            raise HTTPException(
                status_code=503,
                detail=("Knowledge base not built. Run: python scripts/seed_db.py"),
            )
        _store = Store.open(settings.db_path)
    return _store


def get_model() -> RiskModelBundle | None:
    """
    The trained hazard models, or None if they have not been trained yet.

    Absence is not an error: the evidence-based engine works on its own, and
    the API says plainly when no model is loaded rather than pretending to a
    prediction it cannot make.
    """
    global _model, _model_loaded
    if not _model_loaded:
        _model = RiskModelBundle.load(settings.model_path)
        _model_loaded = True
    return _model


def reset_store() -> None:
    """Drop the cached handle, so a rebuild is picked up without a restart."""
    global _store, _model, _model_loaded
    if _store is not None:
        _store.close()
    _store = None
    _model = None
    _model_loaded = False


def require_well(well_id: str) -> dict:
    well = get_store().well(well_id)
    if well is None:
        raise HTTPException(status_code=404, detail=f"Unknown well: {well_id}")
    return well


def weight_params(
    w_geology: float | None = Query(None, ge=0, le=1, description="Weight: geological similarity"),
    w_depth: float | None = Query(None, ge=0, le=1, description="Weight: depth / interval coverage"),
    w_distance: float | None = Query(None, ge=0, le=1, description="Weight: proximity"),
    w_trajectory: float | None = Query(None, ge=0, le=1, description="Weight: trajectory similarity"),
    w_parameters: float | None = Query(None, ge=0, le=1, description="Weight: drilling parameters"),
    w_experience: float | None = Query(None, ge=0, le=1, description="Weight: historical experience"),
    w_recency: float | None = Query(None, ge=0, le=1, description="Weight: recency"),
) -> RelevanceWeights:
    """
    Relevance weights as query parameters, so the dashboard can expose them as
    sliders.  Anything left out keeps its default; the engine normalises the
    set, so callers do not have to make them sum to one.
    """
    supplied = {
        "geology": w_geology,
        "depth": w_depth,
        "distance": w_distance,
        "trajectory": w_trajectory,
        "parameters": w_parameters,
        "experience": w_experience,
        "recency": w_recency,
    }
    given = {k: v for k, v in supplied.items() if v is not None}
    return RelevanceWeights(**given) if given else RelevanceWeights()
