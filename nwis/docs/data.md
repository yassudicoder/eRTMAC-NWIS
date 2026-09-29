# The synthetic Upper Assam dataset

Written for reviewers who need to know exactly what is real, what is generated,
and what that means for the results.

---

## Why synthetic

OIL's drilling records are proprietary and were not available for this
prototype. Rather than demonstrate on a handful of hand-written examples, the
project generates a dataset large and structured enough to exercise every stage
of the system: 61 wells, 10 fields, 180 reports, 560 pages, 20,737 parameter
samples.

Reproduce it with:

```bash
python scripts/generate_assam_dataset.py --seed 20260928
```

Same seed, same dataset, every time.

---

## What is real

Everything structural and geological comes from open literature on the Upper
Assam shelf.

**The stratigraphic column**, youngest to oldest, with the depths NWIS uses at
the structural reference point (Duliajan):

| Formation | Ref. top | Lithology | Known for |
|---|---:|---|---|
| Dihing | 0 m | Unconsolidated pebbly sandstone, clay, gravel | Washouts, seepage losses |
| Namsang | 190 m | Medium–coarse sandstone with clay interbeds | Seepage losses |
| Girujan Clay | 630 m | Mottled clay with silt/sand bands | Sloughing, bit balling, tight hole |
| Tipam Sandstone | 1,160 m | Massive ferruginous sandstone | **Mud losses**, differential sticking |
| Barail Coal Shale | 2,460 m | Carbonaceous shale with coal seams | **Coal cavings**, pack-off, high torque |
| Barail Arenaceous | 3,060 m | Fine–medium sandstone — **principal pay** | **Gas kicks**, differential sticking |
| Kopili Shale | 3,640 m | Dark grey marine shale | Over-pressure, kicks, sloughing |
| Sylhet Limestone | 4,040 m | Fossiliferous, locally karstified | **Total losses** |
| Langpar | 4,360 m | Argillaceous limestone, calcareous shale | Losses, tight hole |
| Basement | 4,650 m | Weathered granitic gneiss | Fractured-basement losses, low ROP |

**The structural model.** The shelf dips south-east into the Naga Schuppen
thrust belt at roughly 24 m/km, fields sit on anticlinal closures, and three
NE–SW faults sub-parallel to the thrust front offset the section. A formation
top at any location is regional dip + closure + fault throw, with deeper units
feeling all three more strongly.

**The fields.** Ten OIL operating areas in Upper Assam with their approximate
surface centres: Naharkatiya, Moran, Duliajan, Hugrijan, Tengakhat, Shalmari,
Dikom, Barekuri, Jorajan, Makum.

**The hazard associations.** Which problems each formation is prone to, with
what severity and by what mechanism — losses into the permeable Tipam, cavings
from Barail coal seams, kicks from the Barail pay and over-pressured Kopili,
total losses into fractured Sylhet.

---

## What is generated

Per well: a surface location scattered around its field centre, a purpose
(development / appraisal / exploratory), a trajectory design (52% vertical, 39%
deviated, 8% horizontal — horizontals land in the Barail pay and run a
1,500–1,800 m lateral), a casing programme that isolates the Girujan before
drilling the Tipam and the coal shale before entering the pay, picked formation
tops with realistic pick noise applied to the structural model, and a
depth-indexed drilling log at 10 m spacing carrying ROP, WOB, RPM, torque, flow,
standpipe pressure, mud weight, ECD and gas.

Drilling events are sampled from each formation's hazard profile as the well is
walked down, with magnitudes drawn from severity-conditioned bands so that a
report's numbers genuinely carry its severity — which is how a real report
works. "Losses of 34 m³/hr" *is* the severity statement.

Three wells are left **currently drilling**: their logs stop at the bit, their
daily reports stop at the bit, and they have no completion report, because a
well that has not finished does not have one.

### Hazard zones

Seven zones are planted where a specific hazard recurs in a specific formation
over a few kilometres — for example the depleted Tipam fairway at Naharkatiya
that takes mud, and the over-pressure cell in the Moran Barail.

These exist so that offset analysis has something real to find. Without them,
trouble would be independent per well and no amount of correlation would help,
which is not how a real field behaves.

`data/synthetic/hot_spots.json` records them. **The application never reads that
file.** `scripts/validate_discovery.py` uses it to check whether the clustering
rediscovers the zones from the extracted event record alone.

---

## The documents

For each well the generator writes reports in the format OIL uses, with
`[PAGE n]` markers so provenance can be tracked:

- **Well completion report** — identification, casing and hole programme,
  formation tops, drilling parameters by section, drilling problems with
  remedial measures, lessons learnt.
- **Daily drilling reports** — one entry per day: depth, progress, formation,
  bit, operations and remarks.
- **Mud log** — depth-indexed lithology, ROP, gas and mud weight, with events
  annotated inline.

Events are written into prose using a pool of phrasings per hazard, so
extraction has to handle variation:

```
Partial mud losses of 8.5 m3/hr observed at 1,432 m MD (1,398 m TVD) ...
Lost circulation encountered at 1,860 m MD in Tipam Sandstone. Loss rate ...
While drilling Tipam Sandstone at 2,040 m MD, returns reduced and losses ...
Total loss of returns at 4,210 m MD (4,090 m TVD) in Sylhet Limestone ...
```

The same event typically appears in two or three documents, which is what the
evidence-aggregation stage exists to reconcile.

---

## The honest caveat

`scripts/score_extraction.py` reports 100% precision and recall on this corpus.
That number is a statement about the *pipeline*, not about how the extractor
would perform on OIL's real archive.

The generated prose is templated. A real archive holds reports written by
different people across decades, with inconsistent terminology, abbreviations,
handwriting digitised by OCR of varying quality, and formats that changed
several times. Recall on that will be lower, and the lexicon will need tuning.

What transfers is:

1. **The pipeline is complete and correct.** Documents in, structured citable
   events out, with no shortcut through the generator's internal state.
2. **The scorecard is the tuning harness.** Point `score_extraction.py` at
   labelled real reports and it produces the same breakdown — per hazard,
   precision and recall, depth error, severity agreement — which is how the
   rules would be improved during a pilot, and the regression test for any
   model that replaces them.
3. **Low-confidence extractions are flagged, not dropped.** The human-in-the-loop
   path is already there for exactly the messier reality.

---

## Files

```
data/synthetic/
  wells.json                  61 well headers with trajectory design and casing
  formation_tops.json         picked tops per well, MD and TVD
  documents_index.json        the manifest: which file belongs to which well
  documents/                  180 reports (WCR, DDR, mud log)
  logs/                       61 depth-indexed parameter CSVs
  ground_truth_events.json    what the generator wrote  — scoring only
  hot_spots.json              planted hazard zones      — validation only
```

The two files marked "only" are never read by the application. Everything NWIS
knows, it read out of the documents and the structured sources.
