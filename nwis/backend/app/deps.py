"""Shared dependencies: the knowledge-base handle and query helpers."""

from __future__ import annotations

from functools import lru_cache

from fastapi import HTTPException, Query

from .config import settings
from .engine.model import RiskModelBundle
from .startup import StartupError
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
    The trained hazard models.

    In development the model is optional - the evidence-based engine works
    without it, and a developer who has not run the trainer should not be
    blocked. In production its absence is a deployment fault: the file is
    committed precisely so production never trains it, so a missing or
    unreadable model means the image was built wrong. Returning None there
    would silently drop every prediction from every alert while still
    answering 200, which is the worst of both worlds.

    Start-up preflight already refuses to boot production without it; this is
    the second line of defence, for the case where the file exists but cannot
    be parsed.
    """
    global _model, _model_loaded
    if not _model_loaded:
        _model = RiskModelBundle.load(settings.model_path)
        _model_loaded = True
        if _model is None and settings.is_production:
            raise StartupError(
                f"Trained model at {settings.model_path} is missing or unreadable. "
                f"Production must not run without it - rebuild the image, and "
                f"check that data/risk_model.json was committed and COPYed in."
            )
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
