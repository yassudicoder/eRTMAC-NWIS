"""
Source documents and the evidence trail.

Every alert NWIS raises can be followed back to a line on a page of a real
report.  These endpoints are what makes that click-through work.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..config import settings
from ..deps import get_store
from ..ingest.ocr import read_document

router = APIRouter(prefix="/api", tags=["evidence"])


@router.get("/documents", summary="List source documents")
def list_documents(well_id: str | None = None, doc_type: str | None = None) -> list[dict]:
    rows = get_store().documents(well_id=well_id)
    if doc_type:
        rows = [r for r in rows if r["doc_type"] == doc_type]
    return rows


@router.get("/documents/{document_id}", summary="Document metadata")
def document(document_id: str) -> dict:
    row = get_store().document(document_id)
    if row is None:
        raise HTTPException(404, f"Unknown document: {document_id}")
    return row


@router.get("/documents/{document_id}/page/{page}", summary="One page of a document")
def document_page(
    document_id: str,
    page: int,
    highlight: int | None = Query(None, description="Line number to mark as the cited line"),
) -> dict:
    """
    The text of a single page, line by line.

    This is the evidence viewer: the UI opens the exact page a citation points
    at and marks the line the extractor fired on, so a driller can read the
    original wording rather than trusting a summary of it.
    """
    store = get_store()
    meta = store.document(document_id)
    if meta is None:
        raise HTTPException(404, f"Unknown document: {document_id}")

    path = settings.data_dir / "documents" / meta["filename"]
    if not path.exists():
        raise HTTPException(404, f"Source file missing: {meta['filename']}")

    doc = read_document(path, document_id)
    lines = [l for l in doc.lines if l.page == page]
    if not lines:
        raise HTTPException(404, f"Page {page} not found in {document_id}")

    return {
        "document_id": document_id,
        "title": meta["title"],
        "doc_type": meta["doc_type"],
        "well_id": meta["well_id"],
        "page": page,
        "page_count": doc.page_count,
        "extraction_method": doc.extraction_method,
        "ocr_confidence": doc.ocr_confidence,
        "highlight_line": highlight,
        "lines": [
            {"line_no": l.line_no, "text": l.text, "highlighted": l.line_no == highlight}
            for l in lines
        ],
    }


@router.get("/events/{event_id}", summary="One event with its full evidence trail")
def event_detail(event_id: int, context: int = Query(2, ge=0, le=10)) -> dict:
    """An extracted event, its citations, and the lines around each citation."""
    store = get_store()
    row = store.conn.execute(
        "SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"Unknown event: {event_id}")

    import json as _json

    event = {k: row[k] for k in row.keys()}
    event["magnitude"] = _json.loads(event.pop("magnitude_json") or "{}")
    event["needs_review"] = bool(event["needs_review"])

    citations = store.citations(event_id)
    for c in citations:
        meta = store.document(c["document_id"])
        c["title"] = meta["title"] if meta else None
        c["context"] = []
        if meta:
            path = settings.data_dir / "documents" / meta["filename"]
            if path.exists():
                doc = read_document(path, c["document_id"])
                lo, hi = c["line_no"] - context, c["line_no"] + context
                c["context"] = [
                    {"line_no": l.line_no, "text": l.text, "highlighted": l.line_no == c["line_no"]}
                    for l in doc.lines if lo <= l.line_no <= hi
                ]

    well = store.well(event["well_id"])
    return {"event": event, "well": well, "citations": citations}
