"""
Document intake.

NWIS treats every source document as *pages of text plus a provenance trail*.
Whether those pages came from a native-text PDF, a Tesseract pass over a
scanned WCR, or a plain text export does not change anything downstream, so
this module is the only place that has to know.

In the prototype the synthetic corpus is plain text with explicit ``[PAGE n]``
markers.  The PDF and image paths are wired up and will run if PyMuPDF /
pytesseract are installed, but they are not required to run the demo - that is
deliberate, so the system can be evaluated with no external binaries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

PAGE_MARKER = re.compile(r"^\[PAGE (\d+)\]\s*$")


@dataclass(frozen=True)
class Line:
    """One line of a document, with everything needed to cite it."""

    page: int
    line_no: int       # 1-based, within the whole document
    text: str


@dataclass
class DocumentText:
    """A document reduced to citable lines."""

    document_id: str
    source_path: str
    lines: list[Line]
    page_count: int
    # How the text was obtained: "text", "pdf", "ocr".
    extraction_method: str
    # Mean OCR confidence where available, else 1.0 for born-digital text.
    ocr_confidence: float = 1.0

    def page_of(self, line_no: int) -> int:
        for ln in self.lines:
            if ln.line_no == line_no:
                return ln.page
        return 1

    def window(self, line_no: int, before: int = 1, after: int = 1) -> str:
        """Neighbouring lines, used to give an extraction some context."""
        lo, hi = line_no - before, line_no + after
        return "\n".join(l.text for l in self.lines if lo <= l.line_no <= hi)


def read_text_document(path: Path, document_id: str) -> DocumentText:
    """Read a text document, honouring ``[PAGE n]`` markers."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines: list[Line] = []
    page = 1
    line_no = 0
    for physical in raw.split("\n"):
        m = PAGE_MARKER.match(physical)
        if m:
            page = int(m.group(1))
            continue
        line_no += 1
        lines.append(Line(page=page, line_no=line_no, text=physical.rstrip()))
    return DocumentText(
        document_id=document_id,
        source_path=str(path),
        lines=lines,
        page_count=max((l.page for l in lines), default=1),
        extraction_method="text",
    )


def read_pdf_document(path: Path, document_id: str) -> DocumentText:
    """
    Read a PDF.  Uses the embedded text layer when there is one and falls back
    to OCR per page when there is not, which is the usual situation with
    archived WCRs from the 1980s and 1990s.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "PDF ingestion needs PyMuPDF. Install it with: pip install pymupdf"
        ) from exc

    lines: list[Line] = []
    line_no = 0
    method = "pdf"
    confidences: list[float] = []

    with fitz.open(path) as doc:
        for page_index, page in enumerate(doc, start=1):
            text = page.get_text("text")
            if len(text.strip()) < 40:
                text, conf = _ocr_page_image(page)
                method = "ocr"
                confidences.append(conf)
            for physical in text.split("\n"):
                line_no += 1
                lines.append(Line(page=page_index, line_no=line_no, text=physical.rstrip()))

    return DocumentText(
        document_id=document_id,
        source_path=str(path),
        lines=lines,
        page_count=max((l.page for l in lines), default=1),
        extraction_method=method,
        ocr_confidence=round(sum(confidences) / len(confidences), 3) if confidences else 1.0,
    )


def _ocr_page_image(page) -> tuple[str, float]:  # pragma: no cover - optional dependency
    """Rasterise a PDF page at 300 dpi and run Tesseract over it."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "Scanned-page OCR needs pytesseract and Pillow, plus the Tesseract binary. "
            "Install with: pip install pytesseract pillow"
        ) from exc

    import io

    pix = page.get_pixmap(dpi=300)
    image = Image.open(io.BytesIO(pix.tobytes("png")))
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)

    words = [w for w in data["text"] if w.strip()]
    confs = [float(c) for c in data["conf"] if c not in ("-1", -1)]
    mean_conf = (sum(confs) / len(confs) / 100.0) if confs else 0.0

    # Rebuild lines from Tesseract's block/line numbering.
    rows: dict[tuple[int, int, int], list[str]] = {}
    for i, word in enumerate(data["text"]):
        if not word.strip():
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        rows.setdefault(key, []).append(word)
    text = "\n".join(" ".join(v) for _, v in sorted(rows.items()))
    return text or " ".join(words), mean_conf


def read_document(path: Path, document_id: str) -> DocumentText:
    """Dispatch on file type."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return read_pdf_document(path, document_id)
    if suffix in {".txt", ".text", ".log", ".md"}:
        return read_text_document(path, document_id)
    raise ValueError(f"Unsupported document type: {path.name}")
