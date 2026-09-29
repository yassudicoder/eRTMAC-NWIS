"""
Build the NWIS well knowledge base from the dataset.

    python scripts/seed_db.py

This runs the full ingestion chain once and writes data/nwis.db:

    structured sources (drilling database, tops, eRTMAC logs)  -> tables
    reports (WCR / DDR / mud log) -> OCR/text -> NLP extraction -> events
    measured depth -> true vertical depth backfill
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ingest.pipeline import ingest_corpus  # noqa: E402
from app.store import Store  # noqa: E402


def build(data_dir: Path, db_path: Path, rebuild: bool = True) -> dict:
    if rebuild and db_path.exists():
        db_path.unlink()
        for suffix in ("-wal", "-shm"):
            extra = db_path.with_name(db_path.name + suffix)
            if extra.exists():
                extra.unlink()

    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = Store.open(db_path)

    print("Loading structured sources (well headers, formation tops, drilling logs)...")
    store.load_structured_sources(data_dir)
    store.load_reservoir_and_cementing(data_dir)

    print("Running document ingestion and NLP extraction...")
    result = ingest_corpus(data_dir)
    stats = result.stats
    print(f"  {stats.documents} documents, {stats.pages} pages, {stats.lines:,} lines")
    print(f"  {stats.raw_events} raw mentions -> {stats.merged_events} events after evidence merge")
    print(f"  {stats.needs_review} flagged for human review")

    store.load_extracted_events(result.events, stats.as_dict())
    fixed = store.backfill_event_tvd()
    print(f"  backfilled TVD for {fixed} events from the drilling logs")

    store.load_lessons(result.lessons)
    print(f"  {stats.lessons} lessons learnt captured")
    n_casing = store.load_casing(result.casing, data_dir)
    print(f"  {n_casing} casing strings ({stats.casing_strings} read from reports)")

    print("Building the full-text search index...")
    rows = store.build_search_index(data_dir)
    print(f"  {rows:,} rows indexed")

    summary = store.stats()
    store.close()
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=ROOT / "data" / "synthetic")
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "nwis.db")
    ap.add_argument("--keep", action="store_true", help="add to the existing database")
    args = ap.parse_args()

    summary = build(args.data, args.db, rebuild=not args.keep)

    print()
    print("Knowledge base ready:", args.db)
    for key in ("wells", "wells_drilling", "fields", "documents", "document_pages",
                "events", "citations", "lessons", "casing_strings", "cement_jobs",
                "reservoir_intervals", "formation_tops", "log_samples",
                "search_rows", "total_npt_hours", "events_needing_review"):
        print(f"  {key:22}: {summary[key]:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
