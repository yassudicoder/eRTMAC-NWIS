# Architecture

Written for engineers joining the project or reviewing it.

---

## The shape of the problem

A well is drilling. Somewhere in OIL's archive is the answer to "what goes
wrong in the next 300 m" — spread across completion reports, daily reports,
mud logs, a geological database and the heads of people who may have retired.
Finding it today means knowing which wells to look at, tracking down their
reports, reading them, and mentally adjusting for the fact that the same rock
sits at a different depth in each one.

NWIS does that lookup continuously and shows its working.

---

## Data flow

```
  WCR / DDR / mud logs        drilling database        eRTMAC
  (scanned or digital)        (headers, tops)          (live parameters)
         │                           │                      │
         ▼                           │                      │
   ingest/ocr.py                     │                      │
   text · PDF · Tesseract            │                      │
   pages, lines, provenance          │                      │
         │                           │                      │
         ▼                           │                      │
   ingest/extract.py                 │                      │
   classify · depths · magnitudes    │                      │
   severity · confidence             │                      │
         │                           │                      │
         ▼                           │                      │
   ingest/pipeline.py                │                      │
   evidence aggregation              │                      │
         │                           │                      │
         └───────────┬───────────────┴──────────────────────┘
                     ▼
              store.py  —  well knowledge base
              wells · formation_tops · events · citations
              documents · drilling_log
                     │
     ┌───────────────┼────────────────┬──────────────────┐
     ▼               ▼                ▼                  ▼
 correlate.py   relevance.py     signals.py          geometry.py
 formation ties six-dimension     live parameter      MD <-> TVD
 depth mapping  offset ranking    trend detection     (past the bit)
     │               │                │                  │
     └───────────────┴────────┬───────┴──────────────────┘
                              ▼
                          risk.py
              look-ahead scoring + evidence assembly
                              │
                              ▼
                      FastAPI routers
                              │
                              ▼
                    React dashboard  ·  SSE replay
```

---

## Modules

### `assam_geology.py`

The single source of geological truth, shared by the data generator and the
engines. Holds the stratigraphic column with per-formation pore pressure,
fracture gradient, drillability and hazard profile; the field and fault
locations; and the structural model that predicts a formation top at any
surface location (regional dip + anticlinal closure + fault throw).

Also owns formation alias normalisation — `Tipam Sst.`, `TIPAM SANDSTONE` and
`Tipam Group` all resolve to one canonical name, which is what lets events
extracted from reports written decades apart be compared.

### `store.py`

The well knowledge base. SQLite so the prototype runs with no database server,
with a schema written to port directly to PostgreSQL + PostGIS: `wells` gains
`geometry(Point, 4326)` columns and the radius filter in `relevance.py` becomes
`ST_DWithin`.

Connections are **thread-local**. FastAPI runs synchronous endpoints in a worker
threadpool, and a single shared `sqlite3.Connection` fails under concurrent
requests; each thread gets its own connection to the same WAL-mode file.

### `ingest/ocr.py`

Reduces any document to citable lines. Plain text honours `[PAGE n]` markers;
PDFs use the embedded text layer where there is one and fall back to a 300 dpi
Tesseract pass per page where there is not — which is the usual situation with
archived reports. Mean OCR confidence is carried forward and scales down the
confidence of everything extracted from that document.

### `ingest/extract.py`

A rule-based cascade, chosen over a trained model for three reasons: there is no
labelled corpus of OIL reports to train on, report language is highly formulaic,
and every extraction has to be explainable and traceable.

Per line: classify against a weighted hazard lexicon → apply blockers (a "Kick
Off Point" is a directional milestone, not a well-control event) → extract
depths, preferring `m MD` and skipping decoys like free points and casing shoes
→ parse the magnitude → infer severity from that magnitude → score confidence.

Lines describing a **remedial action** are attached to the event above them
rather than starting a new one. This removes a whole class of double-counting,
and it is what populates the "what the crew actually did" field that drives
recommendations.

`merge_events` then collapses the same physical event reported in the WCR, the
DDR and the mud log into one record carrying all three citations, raising
confidence for corroboration.

### `engine/correlate.py`

Builds a depth mapping between two wells from their shared formation tops.
Tie points are sorted, non-monotonic picks are dropped, and depth is mapped by
piecewise-linear interpolation between ties; outside the tied interval the
nearest segment's compaction gradient is extrapolated, clamped to a sane range.

Quality falls with few ties and with disagreement between the tie shifts. A
large spread means the two wells are not in the same structural block, so any
mapping between them is shaky — and the alerts built on it say so rather than
pretending otherwise.

### `engine/relevance.py`

Six dimensions plus a recency tie-breaker, each scored in [0, 1] and combined
with normalised, configurable weights:

| Dimension | Default | What it measures |
|---|---|---|
| Geology | 0.26 | Shared formations (overlap) and whether tops track each other once a constant structural shift is removed (conformance) |
| Depth | 0.14 | Coverage of the interval of interest. With a focus interval — the section ahead of the bit — coverage of *that* is what counts |
| Distance | 0.18 | Bottom-hole separation, exponential decay |
| Trajectory | 0.10 | Inclination, azimuth (only where both wells are deviated), well type |
| Parameters | 0.12 | Per-formation ROP, WOB, RPM and mud weight over the shared section |
| Experience | 0.15 | Weighted count of recorded events, discounted outside the shared section |
| Recency | 0.05 | Newer wells reflect current practice |

Each result carries its dimension breakdown and a short list of supports and
caveats, so a ranking can be interrogated rather than trusted.

### `engine/signals.py`

Trend detectors over the tail of the parameter feed — rising torque, rising
standpipe pressure, elevated gas, narrowing ECD margin to the fracture
gradient, low overbalance, falling ROP. Each names the hazards it implicates,
so the risk engine can raise a specific alert rather than a general one.

These are deliberately simple and explainable; every one corresponds to
something a driller already watches.

### `engine/risk.py`

1. Take the interval ahead of the bit, in TVD, using the *planned* trajectory —
   the bit has not been there, so there is no survey.
2. Rank offsets for that interval specifically.
3. Map the interval into each offset well through its formation ties.
4. Collect events falling in the mapped interval. Drop any that project back
   behind the bit — that rock is already drilled.
5. Group by hazard. One vote per well: a well's worst event of that type.
6. Score: `recurrence × severity × depth-agreement`, plus a live-signal boost.
7. Assemble evidence, explanation and recommendations.

Confidence is separate from risk, and combines correlation quality, extraction
confidence and how many independent wells corroborate.

---

## Why the dashboard looks the way it does

The interface has one job: make a driller believe a number enough to act on it,
or dismiss it for a good reason. So every claim is expandable into its basis —
relevance rows open into their arithmetic, alerts open into their contributing
events, and every citation opens the page of the report it came from with the
cited line highlighted and scrolled into view.

The correlation panel exists for the same reason. Once you can see that the
Barail sits 36 m deeper in the offset well, "the offset lost returns at 2,840 m"
stops being a number and becomes a statement about where *this* well will meet
the same rock.

---

## Performance

On the 61-well dataset, on a laptop:

| Operation | Time |
|---|---|
| Generate the dataset | ~0.6 s |
| Ingest 180 documents, 560 pages, 24,010 lines | ~0.3 s |
| Build the knowledge base end to end | ~1.5 s |
| Rank offsets within 15 km (over HTTP) | ~30 ms |
| Full look-ahead analysis (over HTTP) | ~30 ms |
| Correlation panel (over HTTP) | ~25 ms |

The look-ahead is comfortably inside what a real-time loop needs, which is why
the SSE replay can re-run it as the bit advances. The expensive part is
`rank_offsets`, which is why the replay re-analyses every fifth frame rather
than every one.

Scaling to OIL's full well count is a candidate-selection problem, and that is
exactly what PostGIS `ST_DWithin` on an indexed geometry column solves.

---

## Production path

| Prototype | Production |
|---|---|
| SQLite, WAL, thread-local connections | PostgreSQL + PostGIS; schema ports directly |
| Radius filter in Python | `ST_DWithin` on a spatial index |
| Synthetic corpus | OIL archive, role-based access, encryption at rest |
| `routers/realtime.py` replay | WITSML / eRTMAC client — the only module that changes |
| Rule-based extraction | Same rules as baseline plus a model fine-tuned on labelled OIL reports; `score_extraction.py` is the regression harness for both |
| Tops below TD from the regional model | Geological department prognosis and seismic interpretation |
| Weights hand-tuned, exposed as sliders | Weights fitted against well pairs whose relationships engineers already agree on |
