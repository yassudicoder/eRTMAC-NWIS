"""
NWIS - Nearby Wells Intelligence System.

FastAPI application wiring the well knowledge base, the relevance and
correlation engines, the look-ahead risk engine and the simulated real-time
connector behind one HTTP API, and serving the dashboard build if one exists.
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .deps import get_store, reset_store
from .startup import run_preflight
from .routers import analysis, analytics, documents, knowledge, realtime, wells

log = logging.getLogger("nwis")

DESCRIPTION = """
Turns Oil India Limited's historical drilling record into proactive,
evidence-backed guidance for the well that is drilling right now.

* **Ingestion** - WCR / DDR / mud-log documents through OCR and rule-based NLP
  into structured, citable drilling events.
* **Relevance** - offset wells ranked on geology, depth, distance, trajectory,
  drilling parameters and recorded experience, not distance alone.
* **Correlation** - depths mapped between wells through shared formation tops.
* **Risk** - what the relevant offsets hit in the interval ahead of the bit,
  scored, explained, and traceable to a page of a report.

Pilot area: Upper Assam. The dataset shipped with this prototype is synthetic
and generated from a published regional stratigraphic model - see
`docs/data.md`.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Validate the deployment, then serve.

    In production this raises on anything missing, which exits the process
    non-zero and fails the deploy. That is deliberate: a container that can
    only half-serve the application should never replace one that can serve
    all of it. See app/startup.py for what is checked and why.
    """
    run_preflight(settings)

    # Development convenience only. The production image is built with the
    # database already seeded, so this never runs there - and if it somehow
    # did, preflight would have stopped us first.
    if settings.autoseed and not settings.db_path.exists():
        log.warning("Knowledge base missing at %s - building it now", settings.db_path)
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
        from seed_db import build  # type: ignore[import-not-found]

        # Deliberately not wrapped in try/except. A failed seed leaves every
        # endpoint returning 503; failing here instead makes the cause obvious.
        build(settings.data_dir, settings.db_path)

    yield
    reset_store()


app = FastAPI(
    title="NWIS - Nearby Wells Intelligence System",
    description=DESCRIPTION,
    version="0.1.0",
    lifespan=lifespan,
)

# CORS is a development concern here, not a production one.
#
# Production serves the dashboard and the API from the same origin, so the
# browser never makes a cross-origin request and no CORS header is required.
# Adding a wildcard "to be safe" would hand every website on the internet the
# ability to call this API from a visitor's browser, in exchange for nothing.
#
# Development is different: Vite serves the dashboard on :5173 while the API
# runs on :8000, so that origin is granted explicitly. An operator who really
# does host the dashboard elsewhere can name those origins in
# NWIS_ALLOWED_ORIGINS, and only those.
_allowed_origins = settings.allowed_origins
if _allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    log.info("CORS enabled for: %s", ", ".join(_allowed_origins))
else:
    log.info("CORS middleware not installed - same-origin deployment")

app.include_router(wells.router)
app.include_router(analysis.router)
app.include_router(documents.router)
app.include_router(knowledge.router)
app.include_router(analytics.router)
app.include_router(realtime.router)


@app.get("/health", tags=["meta"], summary="Liveness probe")
def health_probe() -> dict:
    """
    Cheap liveness check for the platform.

    Deliberately does no database work: a health check that queries the store
    turns a slow query into a restart loop. /api/health is the detailed one.
    """
    return {"status": "ok", "service": "nwis", "env": settings.env}


@app.get("/api/health", tags=["meta"], summary="Readiness and knowledge-base state")
def health() -> dict:
    ready = settings.db_path.exists()
    payload: dict = {
        "status": "ok" if ready else "no-knowledge-base",
        "env": settings.env,
        "database": str(settings.db_path),
        "data_dir": str(settings.data_dir),
        "model_loaded": settings.model_path.is_file(),
        "dashboard_served": settings.serves_dashboard,
    }
    if ready:
        try:
            summary = get_store().stats()
            payload["wells"] = summary["wells"]
            payload["events"] = summary["events"]
            payload["documents"] = summary["documents"]
        except Exception as exc:  # pragma: no cover
            payload["status"] = "degraded"
            payload["error"] = str(exc)
    return payload


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

if settings.serves_dashboard:
    assets = settings.frontend_dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(settings.frontend_dist / "index.html")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        """Serve the dashboard for any non-API path, so client routing works."""
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        candidate = settings.frontend_dist / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(settings.frontend_dist / "index.html")

else:

    # Reached only in development - preflight refuses to start production
    # without a dashboard build, so this can never be what a user sees in
    # production.
    @app.get("/", include_in_schema=False)
    def index_placeholder() -> JSONResponse:
        return JSONResponse({
            "service": "NWIS",
            "message": (
                "API is running, but the dashboard has not been built. "
                "Run 'npm install && npm run build' in frontend/, or "
                "'npm run dev' for the dev server on port 5173."
            ),
            "docs": "/docs",
            "health": "/api/health",
        }, status_code=503)
