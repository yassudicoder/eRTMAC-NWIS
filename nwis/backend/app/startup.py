"""
Start-up preflight.

The rule this enforces: **a production container either serves a complete,
correct application or it refuses to start.** It never boots into a
half-working state.

That matters more than it sounds. The failure modes this catches are all ones
that look fine from the outside: a missing model file makes every alert lose
its prediction while still returning 200; a missing frontend build makes the
root URL return a JSON blob that looks like an API rather than an obviously
broken page; a failed seed makes every endpoint return 503 while the health
check cheerfully reports the process is alive. Each of those would reach a
judge as "the demo is broken" with nothing in the logs to say why.

So on a production boot every one of them is a hard failure with a message
that says what is missing and how it was supposed to get there. In
development the same checks run but only warn, because a developer who has
not yet run ``npm run build`` should still be able to work on the API.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings

log = logging.getLogger("nwis.startup")


class StartupError(RuntimeError):
    """Raised when a production deployment is missing something it needs."""


@dataclass
class PreflightResult:
    ok: bool
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)


def preflight(settings: Settings) -> PreflightResult:
    """Check everything the application needs before it accepts traffic."""
    problems: list[str] = []
    warnings: list[str] = []
    details: dict = {
        "env": settings.env,
        "data_dir": str(settings.data_dir),
        "db_path": str(settings.db_path),
        "model_path": str(settings.model_path),
        "frontend_dist": str(settings.frontend_dist),
    }

    # --- the corpus, without which the database cannot be rebuilt ---------
    manifest = settings.data_dir / "documents_index.json"
    if not manifest.is_file():
        problems.append(
            f"Corpus manifest missing at {manifest}. The image must contain "
            f"data/synthetic/ - check the COPY step in the Dockerfile, and that "
            f"data/synthetic is not gitignored."
        )
    else:
        docs = settings.data_dir / "documents"
        count = len(list(docs.glob("*.txt"))) if docs.is_dir() else 0
        details["corpus_documents"] = count
        if count == 0:
            problems.append(
                f"Corpus manifest found but {docs} contains no documents. "
                f"The knowledge base would be empty."
            )

    # --- the knowledge base ------------------------------------------------
    if settings.db_path.is_file():
        details["database"] = "present"
    elif settings.is_production:
        problems.append(
            f"Knowledge base missing at {settings.db_path}. In production it is "
            f"baked into the image at build time - the Dockerfile should run "
            f"'python scripts/seed_db.py' during the build. Rebuilding it at "
            f"start-up would delay every cold start."
        )
    elif settings.autoseed:
        details["database"] = "will be seeded on start"
    else:
        problems.append(
            f"Knowledge base missing at {settings.db_path} and NWIS_AUTOSEED is "
            f"off. Run: python scripts/seed_db.py"
        )

    # --- the trained model -------------------------------------------------
    if settings.model_path.is_file():
        details["model"] = "present"
    elif settings.is_production:
        problems.append(
            f"Trained model missing at {settings.model_path}. It is committed to "
            f"the repository precisely so production never has to train it - "
            f"training takes minutes and would time out. Check the COPY step in "
            f"the Dockerfile, and that data/risk_model.json is not gitignored."
        )
    else:
        warnings.append(
            f"No trained model at {settings.model_path}. Alerts will have no "
            f"model prediction. Run: python scripts/train_risk_model.py"
        )
        details["model"] = "absent"

    # --- the dashboard -----------------------------------------------------
    if settings.serves_dashboard:
        details["dashboard"] = "present"
    elif settings.is_production:
        problems.append(
            f"Dashboard build missing at {settings.frontend_dist}/index.html. "
            f"The Dockerfile's node stage should produce it and the python stage "
            f"should COPY it across. Without it the service would answer '/' with "
            f"JSON instead of the UI."
        )
    else:
        warnings.append(
            f"No dashboard build at {settings.frontend_dist}. The API will run; "
            f"use 'npm run dev' in frontend/, or 'npm run build' to serve it from "
            f"here."
        )
        details["dashboard"] = "absent"

    return PreflightResult(ok=not problems, problems=problems,
                           warnings=warnings, details=details)


def run_preflight(settings: Settings) -> PreflightResult:
    """
    Run the checks and act on them.

    Production raises, which makes uvicorn exit non-zero, which makes the
    platform mark the deploy as failed. That is the correct outcome: a deploy
    that cannot work should not replace one that can.
    """
    result = preflight(settings)

    for warning in result.warnings:
        log.warning("%s", warning)

    if result.ok:
        log.info("Preflight passed: %s", result.details)
        return result

    message = "\n".join(
        [f"NWIS cannot start in {settings.env} mode - "
         f"{len(result.problems)} problem(s):"]
        + [f"  {i}. {p}" for i, p in enumerate(result.problems, 1)]
    )

    if settings.is_production:
        log.error("%s", message)
        raise StartupError(message)

    log.warning("%s", message)
    log.warning("Continuing anyway because NWIS_ENV is not 'production'.")
    return result
