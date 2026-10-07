"""PDF text as visual lines, numbered from 1 on each page, top to bottom.

PyMuPDF reports text lines per block. Lines that share a baseline (for example two table
cells side by side) are merged into one visual line, left to right, joined by two spaces.
Spacing inside a line is kept, so fixed-width tables stay readable.

Text that is near-white, tiny or off the page is still returned, flagged `low_visibility`,
so hidden instructions are visible to review instead of silently read or dropped. A page
with no text layer (a scan) produces a `no_text_layer` warning; no OCR is attempted.
"""
from __future__ import annotations

import pymupdf

from .models import DocumentReadError, DocumentWarning, make_segment

MAX_PDF_PAGES = 200
BASELINE_TOLERANCE = 2.0  # points
MIN_VISIBLE_SIZE = 3.0  # points
NEAR_WHITE = 0xF0


def _near_white(color: int) -> bool:
    return all(((color >> shift) & 0xFF) >= NEAR_WHITE for shift in (16, 8, 0))


def _low_visibility(span: dict, page_rect) -> bool:
    x0, y0, x1, y1 = span["bbox"]
    off_page = x1 <= page_rect.x0 or x0 >= page_rect.x1 or y1 <= page_rect.y0 or y0 >= page_rect.y1
    return _near_white(span["color"]) or span["size"] < MIN_VISIBLE_SIZE or off_page


def _visual_lines(page) -> list[tuple[str, tuple[str, ...]]]:
    raw = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            spans = [s for s in line["spans"] if s["text"].strip()]
            if spans:
                raw.append((spans[0]["origin"][1], line["bbox"][0], spans))
    raw.sort(key=lambda r: (r[0], r[1]))

    groups: list[list] = []
    for entry in raw:
        if groups and abs(entry[0] - groups[-1][0][0]) <= BASELINE_TOLERANCE:
            groups[-1].append(entry)
        else:
            groups.append([entry])

    lines = []
    for group in groups:
        group.sort(key=lambda r: r[1])
        text = "  ".join("".join(s["text"] for s in spans).strip() for _, _, spans in group)
        hidden = [_low_visibility(s, page.rect) for _, _, spans in group for s in spans]
        flags = ("low_visibility",) if all(hidden) else ("partly_low_visibility",) if any(hidden) else ()
        lines.append((text, flags))
    return lines


def read_pdf(data: bytes, document_id: str):
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # PyMuPDF raises several types for damaged files
        raise DocumentReadError("unreadable", f"PDF could not be opened: {exc}") from exc
    with doc:
        if doc.needs_pass:
            raise DocumentReadError("encrypted", "PDF is password protected")
        if doc.page_count == 0:
            raise DocumentReadError("unreadable", "PDF has no pages")
        if doc.page_count > MAX_PDF_PAGES:
            raise DocumentReadError("too_large", f"PDF has more than {MAX_PDF_PAGES} pages")
        segments, warnings = [], []
        for page_no, page in enumerate(doc, start=1):
            lines = _visual_lines(page)
            if not lines:
                warnings.append(DocumentWarning("no_text_layer", f"Page {page_no} has no extractable text (scanned?)",
                                                page=page_no))
            for line_no, (text, flags) in enumerate(lines, start=1):
                segments.append(make_segment(document_id, "pdf_line", f"p{page_no}:l{line_no}", text,
                                             page=page_no, line=line_no, flags=flags))
        metadata = {"pages": doc.page_count}
    return segments, warnings, metadata
