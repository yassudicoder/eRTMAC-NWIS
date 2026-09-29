"""
NWIS - Nearby Wells Intelligence System.

FastAPI application wiring the well knowledge base, the relevance and
correlation engines, the look-ahead risk engine and the simulated real-time
connector behind one HTTP API, and serving the dashboard build if one exists.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .deps import get_store, reset_store
from .routers import analysis, analytics, documents, realtime, wells

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
    if settings.autoseed and not settings.db_path.exists():
        log.warning("Knowledge base missing at %s - building it now", settings.db_path)
        try:
            import sys

            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
            from seed_db import build  # type: ignore[import-not-found]

            build(settings.data_dir, settings.db_path)
        except Exception:  # pragma: no cover - startup convenience only
            log.exception("Auto-seed failed. Run: python scripts/seed_db.py")
    yield
    reset_store()


app = FastAPI(
    title="NWIS - Nearby Wells Intelligence System",
    description=DESCRIPTION,
    version="0.1.0",
    lifespan=lifespan,
)

# The dashboard runs on the Vite dev server during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(wells.router)
app.include_router(analysis.router)
app.include_router(documents.router)
app.include_router(analytics.router)
app.include_router(realtime.router)


@app.get("/api/health", tags=["meta"], summary="Liveness and knowledge-base state")
def health() -> dict:
    ready = settings.db_path.exists()
    payload: dict = {
        "status": "ok" if ready else "no-knowledge-base",
        "database": str(settings.db_path),
        "data_dir": str(settings.data_dir),
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

if settings.frontend_dist.exists():
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

    @app.get("/", include_in_schema=False)
    def index_placeholder() -> JSONResponse:
        return JSONResponse({
            "service": "NWIS",
            "message": (
                "API is running. The dashboard has not been built yet - "
                "run 'npm install && npm run build' in frontend/, or "
                "'npm run dev' for the dev server on port 5173."
            ),
            "docs": "/docs",
            "health": "/api/health",
        })
