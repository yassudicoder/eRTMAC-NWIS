"""Runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path(os.getenv("NWIS_DATA_DIR", PROJECT_ROOT / "data" / "synthetic"))
    db_path: Path = Path(os.getenv("NWIS_DB", PROJECT_ROOT / "data" / "nwis.db"))
    frontend_dist: Path = Path(
        os.getenv("NWIS_FRONTEND", PROJECT_ROOT / "frontend" / "dist"))

    # Defaults the API uses when a request does not override them.
    default_radius_km: float = float(os.getenv("NWIS_RADIUS_KM", "15"))
    default_lookahead_m: float = float(os.getenv("NWIS_LOOKAHEAD_M", "300"))
    max_offsets: int = int(os.getenv("NWIS_MAX_OFFSETS", "12"))

    # Build the knowledge base automatically on first start if it is missing.
    autoseed: bool = os.getenv("NWIS_AUTOSEED", "1") != "0"


settings = Settings()
