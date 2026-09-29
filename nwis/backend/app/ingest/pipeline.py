"""
The ingestion pipeline: a folder of reports in, a well knowledge base out.

    documents -> intake (text / PDF / OCR)
              -> information extraction (events, depths, magnitudes)
              -> normalisation (formation aliases, units)
              -> evidence aggregation (merge duplicates, pool citations)
              -> structured events with provenance
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .extract import (
    ExtractedCasing,
    ExtractedEvent,
    ExtractedLesson,
    extract_casing_programme,
    extract_events,
    extract_lessons,
    merge_events,
)
from .ocr import read_document


@dataclass
class IngestStats:
    documents: int = 0
    pages: int = 0
    lines: int = 0
    raw_events: int = 0
    merged_events: int = 0
    lessons: int = 0
    casing_strings: int = 0
    needs_review: int = 0
    by_doc_type: dict[str, int] = field(default_factory=dict)
    by_event_type: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class IngestResult:
    events: list[ExtractedEvent]
    lessons: list[ExtractedLesson]
    casing: list[ExtractedCasing]
    documents: list[dict]
    stats: IngestStats


def ingest_corpus(data_dir: Path, review_threshold: float = 0.55) -> IngestResult:
    """
    Run the whole corpus through intake and extraction.

    ``documents_index.json`` is the manifest an operator would hand over - it
    only says which file belongs to which well, never what is inside it.
    """
    started = time.perf_counter()
    index_path = data_dir / "documents_index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))

    stats = IngestStats()
    raw: list[ExtractedEvent] = []
    lessons: list[ExtractedLesson] = []
    casing: list[ExtractedCasing] = []

    for entry in index:
        path = data_dir / "documents" / entry["filename"]
        if not path.exists():
            continue
        doc = read_document(path, entry["document_id"])
        stats.documents += 1
        stats.pages += doc.page_count
        stats.lines += len(doc.lines)

        # The casing table is read first: a cement-job sentence names the
        # string it ran on, and the table is what turns that into a depth.
        doc_casing = extract_casing_programme(doc, entry["well_id"], entry["doc_type"])
        casing.extend(doc_casing)

        found = extract_events(doc, entry["well_id"], entry["doc_type"],
                               review_threshold=review_threshold,
                               casing=doc_casing)
        raw.extend(found)
        stats.by_doc_type[entry["doc_type"]] = stats.by_doc_type.get(entry["doc_type"], 0) + len(found)

        # Lessons live in a section whose whole grammar is the future tense,
        # so they need their own pass with the negation rules suspended.
        lessons.extend(extract_lessons(doc, entry["well_id"], entry["doc_type"]))

    stats.raw_events = len(raw)
    events = merge_events(raw)
    stats.merged_events = len(events)
    stats.needs_review = sum(1 for e in events if e.needs_review)
    for e in events:
        stats.by_event_type[e.event_type] = stats.by_event_type.get(e.event_type, 0) + 1
    stats.lessons = len(lessons)
    stats.casing_strings = len(casing)
    stats.seconds = round(time.perf_counter() - started, 2)

    return IngestResult(events=events, lessons=lessons, casing=casing,
                        documents=index, stats=stats)
