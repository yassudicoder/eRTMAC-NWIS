# Compliance with SIH 2026 Problem Statement 121

**PS Number 26121 · Oil India Limited · eRTMAC-NWIS · Software · Smart Automation**

Written for the evaluation panel. Every row names where the capability lives
in the code and how to verify it in under a minute.

---

## Problem description — the unified platform must:

### PD-i · Display nearby wells on a geospatial map relative to the active well

**Met.** Leaflet map with street and terrain basemaps. The active well is a
distinct marker; offsets are coloured by relevance band and sized by relevance
score; a line joins each offset to the active well; the user-defined search
radius is drawn as a ring and is included in the auto-fit so it stays visible.
Popups give great-circle distance and compass bearing, computed server-side.

* Code: [`frontend/src/components/WellMap.jsx`](../frontend/src/components/WellMap.jsx),
  `haversine_km` / `bearing_deg` in [`backend/app/assam_geology.py`](../backend/app/assam_geology.py)
* Verify: open **Nearby Wells**. The caption states how many wells are in
  radius and how many are shown, so the count never contradicts the map.

### PD-ii · Instant access to historical drilling experiences and operational events from offset wells

**Met.** Each offset expands in place to its scoring breakdown; a full dossier
is one call away (`/api/wells/{id}/experience`) giving that well's events,
lessons, casing, cementing, reservoir intervals, documents and NPT breakdown.
Every event carries its citations, and a citation opens the report page with
the cited line highlighted and scrolled into view.

* Code: [`backend/app/routers/knowledge.py`](../backend/app/routers/knowledge.py),
  [`frontend/src/components/EvidenceDrawer.jsx`](../frontend/src/components/EvidenceDrawer.jsx)

### PD-iii · Correlate drilling parameters, reservoir characteristics, mud losses, kicks, stuck pipe, casing programs, cementing practices and formation-specific risks across wells

**Met — all eight named items.**

| Named item | Where it lives |
|---|---|
| Drilling parameters | `drilling_log` table; per-formation `section_averages`; a scored relevance dimension |
| Reservoir characteristics | `reservoir_intervals` table — fluid, porosity, permeability, Sw, net pay, virgin vs **current** pressure, temperature |
| Mud losses | `MUD_LOSS` events with loss rate and severity |
| Kicks | `KICK` events with pit gain, SIDPP, gas units |
| Stuck pipe | `STUCK_PIPE` events with overpull and free point |
| **Casing programs** | `casing_strings` table — **read out of the reports' own tables** by the document pipeline, not loaded from a database |
| **Cementing practices** | `cement_jobs` table + `CEMENTING_ISSUE` hazard class with six failure modes |
| Formation-specific risks | Per-formation hazard profiles; correlation on shared formation tops |

Cross-well comparison is on a **common depth frame**: shared formation tops
become tie points and depth is mapped between wells by piecewise-linear
interpolation, so "they set the 9-5/8in at the top of the coal shale" survives
the coal shale being 200 m deeper in the offset.

* Code: [`backend/app/engine/correlate.py`](../backend/app/engine/correlate.py),
  `/api/wells/{id}/casing-comparison`
* Verify: `GET /api/wells/NHK-072/casing-comparison?radius_km=15`

### PD-iv · Proactive alerts when operations approach depths or formations where similar challenges were encountered

**Met.** The look-ahead engine takes the interval ahead of the bit in TVD,
maps it into each relevant offset through that offset's formation ties,
collects the events that land in the mapped interval, groups them by hazard
and scores each group on recurrence, severity and depth agreement — then folds
in what the current well's own parameters are doing right now.

The simulated eRTMAC feed re-runs the whole analysis as the bit advances and
pushes it over SSE; the dashboard consumes it and shows a live banner.

* Code: [`backend/app/engine/risk.py`](../backend/app/engine/risk.py),
  [`backend/app/routers/realtime.py`](../backend/app/routers/realtime.py)
* Verify: press **Play** in the sidebar, then open **Risk Alerts** — the
  banner shows the simulated bit depth and alerts recompute as it advances.

---

## Expected outcome — the solution should:

### EO-i · Use AI, NLP, OCR and data analytics to extract and structure information from historical reports

**Met, with one honest caveat.** A rule-based NLP cascade reads 180 reports
(657 pages) into structured, citable records: hazard class and sub-mode,
depths, magnitudes, severity, NPT, remedial action, plus the casing table and
the lessons section. Intake handles plain text, PDF text layers and scanned
pages via Tesseract; OCR confidence is carried through and scales down the
confidence of everything read from that document.

Measured against ground truth: **100% precision, 99.1% recall**, 0.02 m depth
error, 91.6% exact severity agreement across 10 hazard classes.

*Caveat:* the corpus is synthetic and its phrasing is templated, so those
figures describe the pipeline, not real archive performance. The scorecard is
the tuning harness — point it at labelled OIL reports and it gives the same
per-hazard breakdown. See [`data.md`](data.md).

*On "AI":* extraction is rules, deliberately (no labelled corpus exists; report
language is formulaic; every extraction must be traceable to a line). The
learned component is the predictive model under EO-v.

* Verify: `python scripts/score_extraction.py`

### EO-ii · Interactive map-based visualisation of nearby wells within a user-defined radius

**Met.** Radius is a slider (2–40 km) with 5/10/20 km presets, and it drives
candidate selection server-side. Both the ring and the result set respond.

### EO-iii · Searchable knowledge repository of drilling events, lessons learned, operational challenges and mitigation measures

**Met — all four.**

| Named item | Implementation |
|---|---|
| **Searchable** | SQLite FTS5 over 25,451 rows — every event, every lesson, every report line. BM25 ranked, structured records weighted above raw text, matched terms returned marked up for highlighting |
| Drilling events | 547 extracted events across 10 hazard classes, 2,094 citations |
| **Lessons learned** | 276 lessons, extracted by a **separate pass** — they are written in the future tense, so the negation rules that make event extraction work destroy them |
| Mitigation measures | `remedial_action` on each event, surfaced first and attributed in the recommendations |

* Code: `build_search_index` / `search` in [`backend/app/store.py`](../backend/app/store.py),
  `extract_lessons` in [`backend/app/ingest/extract.py`](../backend/app/ingest/extract.py)
* Verify: open **Search & Lessons**, type `differential sticking` — 60 hits
  across events and lessons.

### EO-iv · Correlate geological, drilling and reservoir data across wells based on depth and formation

**Met.** Geological correlation is the formation-tie mapping under PD-iii.
Drilling data is compared per formation over the shared section, and is one of
the six relevance dimensions. Reservoir data is per-well and per-interval, so
porosity, permeability and **current** pressure can be compared between wells
rather than being a single regional constant.

Depletion is modelled from field first-production dates and is **load-bearing**:
it lowers the reservoir pressure a later well meets, which raises the
overbalance the mud column applies across the depleted sand, which raises the
generated probability of differential sticking and whole-mud losses. The data
is causally connected to the hazards rather than decorative.

### EO-v · Predictive analytics models identifying mud losses, stuck pipe, overpressure zones, torque spikes or cementing issues

**Met — all five named risks, with a genuinely trained model.**

An L2-regularised logistic regression per hazard, over 13 features built from
what was knowable *before* the section was drilled: offset incidence and
severity, distance to the nearest offset that saw it, the field prior, depth,
overbalance, depletion, fracture margin, inclination and petrophysics.

Validated **leave-one-well-out** — every score below was produced by a model
that had never seen that well. Wells are the unit of independence, not rows.

| Hazard | Rows | Base rate | **AUC** | Brier | vs base rate |
|---|---:|---:|---:|---:|---:|
| Cementing problem | 381 | 0.28 | **0.922** | 0.094 | +0.109 |
| High torque and drag | 381 | 0.09 | **0.935** | 0.053 | +0.026 |
| Kick | 381 | 0.09 | **0.912** | 0.061 | +0.025 |
| Mud loss | 381 | 0.23 | **0.822** | 0.135 | +0.041 |
| Stuck pipe | 381 | 0.17 | **0.888** | 0.097 | +0.043 |
| Wellbore instability | 381 | 0.17 | **0.883** | 0.095 | +0.045 |

What the models learned is geologically sensible on inspection: torque keys on
inclination, losses on the fracture margin, kicks on depth.

**Overpressure zones** are handled separately and deliberately, because they
cannot be found by counting events — an overpressured interval is dangerous
even where nothing went wrong, because the previous crew carried enough mud to
keep it quiet. NWIS reads the **mud weights** the offsets actually carried,
maps them onto the current well through the formation ties, and warns when the
offsets needed materially more mud than is in the hole now, or when the window
between pore pressure and fracture gradient is about to close.

The model is presented **beside** the evidence score, never instead of it.
Where the two disagree, that disagreement is shown and is itself informative.

* Code: [`backend/app/engine/model.py`](../backend/app/engine/model.py),
  [`backend/app/engine/pressure.py`](../backend/app/engine/pressure.py)
* Verify: `python scripts/train_risk_model.py`, or `GET /api/model/metrics`

---

## Background requirements

| From the background paragraph | Status |
|---|---|
| Well completion reports, drilling reports | Both ingested; 180 documents, 657 pages |
| Mud logging information | Mud logs ingested; gas, lithology, ROP indexed |
| **PDF documents** | PDF intake implemented via PyMuPDF with per-page Tesseract fallback. The shipped corpus is text; the PDF path runs when PyMuPDF is installed |
| Individual experience and memory | Lessons learnt captured as first-class records — the literal reading of "institutional memory" |
| Standalone platform alongside eRTMAC | Separate service; eRTMAC is a connector (`routers/realtime.py`) with a defined frame shape, replaced by a WITSML client in production |

---

## Known gaps, stated plainly

1. **The dataset is synthetic.** Extraction and model figures describe the
   pipeline, not OIL's real archive. Both harnesses transfer.
2. **OCR has not been exercised on real scans.** The code path is complete and
   PyMuPDF is installed; the Tesseract binary is not present on the
   development machine, so scanned-page OCR quality is unmeasured here.
3. **The look-ahead has no back-test yet.** Relevance ranking and cluster
   discovery are validated (`validate_discovery.py`), and the model is
   validated out-of-well — but alert hit rate and lead distance against
   held-out wells are not yet measured.
4. **No write surface.** The knowledge base is seeded and read; document
   upload, event review and alert acknowledgement are not implemented, so
   "institutional memory" currently accumulates only at ingest time.
5. **Scale is untested.** 61 wells is small. The radius filter is a Python
   scan; PostGIS `ST_DWithin` on an indexed geometry column is the production
   path and the schema is already written to port.

---

## One-minute verification

```bash
python scripts/generate_assam_dataset.py   # rebuild the corpus
python scripts/seed_db.py                  # ingest, extract, index
python scripts/score_extraction.py         # 100% precision / 99.1% recall
python scripts/train_risk_model.py         # AUC 0.82-0.94, leave-one-well-out
python scripts/validate_discovery.py       # rediscovers planted hazard zones
python backend/tests/test_nwis.py          # 39 tests
cd backend && uvicorn app.main:app --port 8000
```
