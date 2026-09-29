"""
Runtime configuration.

Everything here is driven by environment variables so that the same image runs
unchanged on a laptop and on Render. The defaults are the *development*
defaults; production overrides them through the environment, and
``NWIS_ENV=production`` tightens several behaviours that are deliberately
forgiving in development.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _origins(raw: str | None) -> list[str]:
    """Parse a comma-separated allow-list, dropping blanks."""
    if not raw:
        return []
    return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]


# In development the dashboard runs on the Vite dev server, which is a
# different origin from the API, so it needs an explicit CORS grant.
# Production is single-origin and needs none.
DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


@dataclass(frozen=True)
class Settings:
    # "development" or "production". Production refuses to start on a
    # misconfiguration that development merely warns about.
    env: str = os.getenv("NWIS_ENV", "development").strip().lower()

    data_dir: Path = Path(os.getenv("NWIS_DATA_DIR", PROJECT_ROOT / "data" / "synthetic"))
    db_path: Path = Path(os.getenv("NWIS_DB", PROJECT_ROOT / "data" / "nwis.db"))
    frontend_dist: Path = Path(
        os.getenv("NWIS_FRONTEND", PROJECT_ROOT / "frontend" / "dist"))
    model_path: Path = Path(
        os.getenv("NWIS_MODEL", PROJECT_ROOT / "data" / "risk_model.json"))

    # Defaults the API uses when a request does not override them.
    default_radius_km: float = float(os.getenv("NWIS_RADIUS_KM", "15"))
    default_lookahead_m: float = float(os.getenv("NWIS_LOOKAHEAD_M", "300"))
    max_offsets: int = int(os.getenv("NWIS_MAX_OFFSETS", "12"))

    # Build the knowledge base on first start if it is missing. The container
    # image already contains a seeded database, so this is a development
    # convenience; in production a missing database is a build fault and the
    # app says so rather than quietly spending a minute rebuilding it.
    autoseed: bool = _flag("NWIS_AUTOSEED", True)

    # Extra browser origins allowed to call the API, comma separated. Only
    # needed when the dashboard is served from somewhere other than this
    # service - which the production deployment deliberately avoids.
    extra_origins: list[str] = field(
        default_factory=lambda: _origins(os.getenv("NWIS_ALLOWED_ORIGINS")))

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def allowed_origins(self) -> list[str]:
        """
        Browser origins granted CORS access.

        Development gets the Vite dev server. Production gets nothing unless
        an operator explicitly names an origin, because the dashboard is
        served from this same service and a same-origin request needs no CORS
        header at all. Handing out a wildcard "to make deployment easier"
        would widen the attack surface for no benefit.
        """
        origins = list(self.extra_origins)
        if not self.is_production:
            origins = DEV_ORIGINS + origins
        # Preserve order, drop duplicates.
        return list(dict.fromkeys(origins))

    @property
    def serves_dashboard(self) -> bool:
        return (self.frontend_dist / "index.html").is_file()


settings = Settings()
