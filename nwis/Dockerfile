# NWIS - single-service production image.
#
# One container serves both the dashboard and the API from one origin. That is
# a deliberate choice, not a simplification: a split deployment would need CORS
# configured, a second provider configured, and would show the user a fully
# rendered page whose every panel errors while the API container is still
# waking from sleep. One origin means one loading state and one URL.
#
#   Stage 1 (node)   builds the dashboard into frontend/dist
#   Stage 2 (python) installs the API, copies the build across, and seeds the
#                    knowledge base AT BUILD TIME so container start is instant
#
# Build context is the nwis/ directory:
#     docker build -t nwis .
#     docker run --rm -e PORT=10000 -p 10000:10000 nwis

# ---------------------------------------------------------------------------
# Stage 1 - build the dashboard
# ---------------------------------------------------------------------------
FROM node:20-alpine AS frontend

WORKDIR /build

# Copy the lockfile first so this layer caches until dependencies change.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build && test -f dist/index.html


# ---------------------------------------------------------------------------
# Stage 2 - the application
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS app

# Uncomment to enable OCR ingestion of scanned PDF reports. Not needed for the
# shipped corpus, which is text; see docs/data.md.
# RUN apt-get update && apt-get install -y --no-install-recommends \
#         tesseract-ocr && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Application code and the two data sources production needs.
COPY backend/ ./backend/
COPY scripts/ ./scripts/
COPY data/synthetic/ ./data/synthetic/

# The trained model is committed rather than trained here: fitting it takes
# minutes of CPU, which would be paid on every image build and would risk
# timing out a free-tier builder. It is 20 KB.
COPY data/risk_model.json ./data/risk_model.json

# The dashboard build from stage 1.
COPY --from=frontend /build/dist ./frontend/dist

ENV PYTHONPATH=/app/backend \
    NWIS_ENV=production \
    NWIS_DATA_DIR=/app/data/synthetic \
    NWIS_DB=/app/data/nwis.db \
    NWIS_MODEL=/app/data/risk_model.json \
    NWIS_FRONTEND=/app/frontend/dist \
    NWIS_AUTOSEED=0

# Seed the knowledge base into the image.
#
# Render's filesystem is ephemeral - anything written at runtime is lost on
# restart, and free instances restart whenever they wake from sleep. Seeding
# here means the database ships inside the image: start-up is instant and
# every container is byte-identical. Seeding at start-up instead would add
# ~20-30 s to an already slow cold start, every single time.
RUN python scripts/seed_db.py --data /app/data/synthetic --db /app/data/nwis.db \
    && test -s /app/data/nwis.db

# Fail the build rather than the deploy if anything the app needs is absent.
RUN python -c "import sys; sys.path.insert(0, '/app/backend'); \
from app.config import settings; from app.startup import preflight; \
r = preflight(settings); print('preflight:', r.details); \
sys.exit(0 if r.ok else 'BUILD FAILED - ' + '; '.join(r.problems))"

# Drop privileges. Nothing at runtime needs to write to the image.
RUN useradd --create-home --uid 10001 nwis && chown -R nwis:nwis /app
USER nwis

# Render injects $PORT and ignores EXPOSE; this documents the local default.
EXPOSE 8000

# Shell form so ${PORT} is expanded at runtime. The fallback keeps
# `docker run` working locally without setting PORT.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
