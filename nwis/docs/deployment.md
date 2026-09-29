# Deploying NWIS

**Target: one Render Web Service, built from Docker, serving the dashboard and
the API from a single origin.**

---

## Why one service rather than two

The API already serves the dashboard's build output, so splitting the frontend
onto a second provider would buy a CDN and cost three things:

* **CORS.** Two origins means the API must hand out cross-origin permission.
  One origin means the browser never asks.
* **A worse failure mode.** On a free tier the API sleeps after inactivity. A
  CDN-hosted frontend would load *instantly* and then show an error in every
  panel for the ~1 minute the API takes to wake. One service gives one honest
  loading state instead.
* **A second thing to configure, and a second thing to break** the night
  before a demo.

---

## Render setup

### Option A — Blueprint (recommended)

[`render.yaml`](../../render.yaml) at the repository root already describes the
service. In the Render dashboard: **New → Blueprint → pick this repository**.
Everything below is applied automatically.

### Option B — manual

**New → Web Service**, connect the repo, then:

| Setting | Value |
|---|---|
| Language / Runtime | `Docker` |
| Root Directory | `nwis` |
| Dockerfile Path | `./Dockerfile` |
| Docker Build Context Directory | `.` |
| Instance Type | `Free` |
| Region | `Singapore` (closest to India) |
| Health Check Path | `/health` |

**Build Command** and **Start Command**: leave both blank. The Dockerfile owns
them — the image builds the dashboard, seeds the database and starts uvicorn.

### Environment variables

| Variable | Production value | Purpose |
|---|---|---|
| `NWIS_ENV` | `production` | Turns on strict start-up validation. Without it the service would boot in a degraded state rather than failing. |
| `NWIS_AUTOSEED` | `0` | The database is baked into the image; nothing to seed at start-up. |
| `PORT` | *(injected by Render)* | Do not set this yourself. |

Already baked into the image by the Dockerfile, so you do not need to set them:
`NWIS_DATA_DIR`, `NWIS_DB`, `NWIS_MODEL`, `NWIS_FRONTEND`, `PYTHONPATH`.

**Deliberately not set: `NWIS_ALLOWED_ORIGINS`.** Production is same-origin, so
no CORS grant is needed. Setting a wildcard would let any site on the internet
call this API from a visitor's browser in exchange for nothing. Only set it if
you host the dashboard somewhere else, and then name that exact origin.

### Tunables (optional)

`NWIS_RADIUS_KM` (default `15`), `NWIS_LOOKAHEAD_M` (default `300`),
`NWIS_MAX_OFFSETS` (default `12`).

---

## URL structure

Everything is under one origin, `https://<service>.onrender.com`:

| Path | What |
|---|---|
| `/` | Dashboard (SPA; unknown paths fall through to `index.html`) |
| `/assets/*` | Built JS/CSS |
| `/health` | Liveness probe. No database work — a health check that runs a query turns a slow query into a restart loop. |
| `/api/health` | Readiness: database, model and dashboard state |
| `/api/*` | The API |
| `/docs` | OpenAPI browser |

---

## Running the production image locally

```bash
cd nwis
docker build -t nwis .
docker run --rm -e PORT=10000 -p 10000:10000 nwis
# -> http://localhost:10000
```

Or with compose, which mirrors the Render environment:

```bash
cd nwis
docker compose up --build      # -> http://localhost:8000
```

Passing `PORT` explicitly is intentional: it exercises the same `${PORT}` path
Render uses, rather than a fallback that only works on your machine.

---

## Development vs production

|  | Development | Production |
|---|---|---|
| `NWIS_ENV` | unset / `development` | `production` |
| Frontend | Vite dev server on `:5173`, proxying `/api` to `:8000` | Built into the image, served by FastAPI |
| Origins | Two — CORS granted to `localhost:5173` | One — no CORS middleware installed at all |
| Database | Seeded on first run if missing | Baked into the image at build time |
| Model missing | Warning; alerts simply have no prediction | **Refuses to start** |
| Dashboard missing | Warning; API still runs | **Refuses to start** |
| Corpus missing | Warning | **Refuses to start** |

Development workflow is unchanged:

```bash
cd backend && uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev        # :5173
```

---

## How the image is built

Two stages, so the Node toolchain never ships in the final image:

1. **`node:20-alpine`** — `npm ci` from the lockfile, `npm run build` →
   `frontend/dist`.
2. **`python:3.12-slim`** — install the API's two dependencies, copy the
   backend, the corpus, the committed model and the dashboard build across,
   then:
   * **seed the knowledge base into the image** (`scripts/seed_db.py`), and
   * **run the start-up preflight as a build step**, so a broken image fails
     the *build* rather than the deploy.

   Finally it drops to a non-root user (`uid 10001`).

Two things are deliberately *not* done at container start:

* **Seeding.** Render's filesystem is ephemeral — its docs name "local SQLite
  databases" as lost on every redeploy, restart and spin-down — so seeding at
  runtime would repeat ~20–30 s of work on 0.1 CPU for every cold start and
  keep nothing. Files written during the *build* do ship in the deployed
  image, which is why the build is the right place.
* **Model training.** Fitting takes ~200 s on a laptop and far longer on 0.1
  CPU; Render's start-command timeout is 15 minutes. `data/risk_model.json` is
  committed (20 KB) and copied in.

---

## Known limitations on Render Free

These are properties of the free tier, not of the application. Know them before
a demo.

### Cold start, ~1 minute

Free services **spin down after 15 minutes without inbound traffic** and take
about a minute to wake. A judge opening a cold link waits on a Render loading
page.

*Mitigation:* open the URL yourself 2–3 minutes before the demo and leave a tab
on it.

### SSE may not count as traffic

Render resets the spin-down timer on "an incoming HTTP request" or "an incoming
WebSocket message". **Server-Sent Events are outbound only and are not named in
that list.** A long-running eRTMAC replay with no other interaction may
therefore not keep the service awake.

*Mitigation:* click around the dashboard during a long demo. Ordinary API calls
are inbound HTTP and do reset the timer.

### SQLite is read-only in practice

The database ships inside the image. Anything written at runtime is **lost on
every restart, redeploy and spin-down**. NWIS only reads it, so this is fine —
but it is the reason not to add a write feature without moving to Postgres
first. Free Render Postgres expires 30 days after creation.

### Other free-tier ceilings

* **750 instance hours per month** per workspace. Sleeping services do not
  consume them.
* **5 GB outbound bandwidth per month** on Hobby (reduced from 100 GB in April
  2026). Exceeding it with no payment method on file spins down every service
  in the workspace until the next month. This app serves ~440 KB per load, so
  the practical ceiling is roughly ten thousand page loads.
* **0.1 CPU / 512 MB RAM.** Adequate — a look-ahead takes ~30 ms and the whole
  knowledge base is 9.5 MB — but not fast.
* **No credit card required.**

---

## Reproducibility

A fresh clone plus `docker build` reproduces the same application state, because
everything the image needs is committed:

* the corpus (`data/synthetic/`, 4.2 MB) — the database is derived from it
  deterministically by the seed script;
* the trained model (`data/risk_model.json`, 20 KB);
* the lockfile (`frontend/package-lock.json`) — `npm ci` installs exact
  versions.

`.dockerignore` keeps the host's `node_modules`, `dist` and local `nwis.db` out
of the build context, so the image cannot pick up whatever state a developer's
machine happened to be in.

To change the data: re-run `scripts/generate_assam_dataset.py` and
`scripts/train_risk_model.py`, commit the result, and rebuild.

### This was measured, not assumed

Two images were built `--no-cache` from independent trees: one from a Windows
working copy (CRLF line endings, because `core.autocrlf=true` and the repo has
no `.gitattributes`) and one from a fresh `git clone` with LF endings, which is
what a Render builder gets. Comparing the seeded knowledge base table by table:

| | Result |
|---|---|
| `wells`, `events`, `citations`, `documents`, `drilling_log`, `formation_tops`, `casing_strings`, `cement_jobs`, `reservoir_intervals`, `lessons`, `search_index` | **identical content hashes** |
| `ingest_runs` | differs in `started_at` only - the build clock. Its `stats_json` is character-identical. |
| `/api/health`, `/api/wells`, `/api/wells/{id}/risk`, `/api/search` | **byte-identical responses** |

So line endings do not reach application state: the extractor reads text with
Python's universal newlines, so a CRLF file and an LF file arrive as the same
string. A fresh clone genuinely reproduces the same application.

The only file `.dockerignore` keeps out of the image is
`backend/tests/test_nwis.py`, which is deliberate - tests run on the host, not
in production. Everything else in `backend/`, `scripts/` and `data/` is
present.

---

## Verifying a deployment

```bash
BASE=https://<your-service>.onrender.com

curl -s $BASE/health                      # {"status":"ok",...,"env":"production"}
curl -s $BASE/api/health                  # model_loaded: true, dashboard_served: true
curl -s $BASE/api/wells | head -c 200     # 61 wells
curl -s "$BASE/api/search?q=differential+sticking&limit=3"
curl -sN "$BASE/api/realtime/MRN-068/stream?interval_ms=500" | head -20
```

Then open `$BASE` in a browser: the dashboard should load, **Play** should
start the replay, and the Risk Alerts view should show a live banner with the
bit depth advancing.
