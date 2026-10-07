"""PDF text as visual lines, numbered from 1 on each page, top to bottom.

Uses pdfplumber (MIT). Characters that share a baseline form one visual line, read left to
right. Spaces written in the PDF are kept, so fixed-width tables stay readable. Where a PDF
positions words without space characters, a gap wider than a quarter of the font size becomes
one space, and a wide gap between two cells on the same row becomes two spaces.

Text that is near-white, tiny or off the page is still returned, flagged `low_visibility`,
so hidden instructions are visible to review instead of silently read or dropped. A page
with no text layer (a scan) produces a `no_text_layer` warning; no OCR is attempted.
pdfminer does not expose text opacity on character objects. If a page declares a transparent
graphics state, every extracted line on that page gets `visibility_unverified` so callers
cannot mistake an unflagged black glyph for proven visible text.
"""
from __future__ import annotations

import io

import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect
from pdfminer.pdfparser import PDFSyntaxError
from pdfminer.pdftypes import resolve1
from pdfminer.psparser import PSException
from pdfplumber.utils.exceptions import PdfminerException

from .models import DocumentReadError, DocumentWarning, make_segment

MAX_PDF_PAGES = 200
BASELINE_TOLERANCE = 2.0  # points
MIN_VISIBLE_SIZE = 3.0  # points
NEAR_WHITE = 0.94
WORD_GAP = 0.25  # fraction of font size
COLUMN_GAP = 1.5  # fraction of font size
MIN_VISIBLE_OPACITY = 0.05


def _near_white(color) -> bool:
    if not isinstance(color, (tuple, list)) or not all(isinstance(c, (int, float)) for c in color):
        return False
    if len(color) in (1, 3):  # gray or RGB
        return all(c >= NEAR_WHITE for c in color)
    if len(color) == 4:  # CMYK
        return all(c <= 1 - NEAR_WHITE for c in color)
    return False


def _low_visibility(ch: dict, width: float, height: float) -> bool:
    off_page = ch["x1"] <= 0 or ch["x0"] >= width or ch["bottom"] <= 0 or ch["top"] >= height
    return _near_white(ch.get("non_stroking_color")) or ch["size"] < MIN_VISIBLE_SIZE or off_page


def _line_text(chars: list[dict]) -> str:
    parts = [chars[0]["text"]]
    for prev, ch in zip(chars, chars[1:]):
        gap = ch["x0"] - prev["x1"]
        if not prev["text"].isspace() and not ch["text"].isspace():
            size = max(prev["size"], 1.0)
            if gap > COLUMN_GAP * size:
                parts.append("  ")
            elif gap > WORD_GAP * size:
                parts.append(" ")
        parts.append(ch["text"])
    return "".join(parts).strip()


def _visual_lines(page) -> list[tuple[str, tuple[str, ...]]]:
    chars = [dict(ch, baseline=page.height - ch["matrix"][5]) for ch in page.chars]
    chars.sort(key=lambda c: (c["baseline"], c["x0"]))

    groups: list[list[dict]] = []
    for ch in chars:
        if groups and abs(ch["baseline"] - groups[-1][0]["baseline"]) <= BASELINE_TOLERANCE:
            groups[-1].append(ch)
        else:
            groups.append([ch])

    lines = []
    for group in groups:
        group.sort(key=lambda c: c["x0"])
        text = _line_text(group)
        if not text:
            continue
        hidden = [_low_visibility(c, page.width, page.height) for c in group if not c["text"].isspace()]
        flags = ("low_visibility",) if all(hidden) else ("partly_low_visibility",) if any(hidden) else ()
        lines.append((text, flags))
    return lines


def _page_has_uncertain_opacity(page) -> bool:
    states = resolve1(page.page_obj.resources.get("ExtGState", {}))
    if not isinstance(states, dict):
        return True
    for state in states.values():
        settings = resolve1(state)
        if not isinstance(settings, dict):
            return True
        try:
            if any(float(settings.get(key, 1)) < MIN_VISIBLE_OPACITY for key in ("ca", "CA")):
                return True
        except (TypeError, ValueError):
            return True
        if settings.get("SMask") not in (None, "None"):
            return True
    return False


def read_pdf(data: bytes, document_id: str):
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            page_count = len(pdf.pages)
            if page_count == 0:
                raise DocumentReadError("unreadable", "PDF has no pages")
            if page_count > MAX_PDF_PAGES:
                raise DocumentReadError("too_large", f"PDF has more than {MAX_PDF_PAGES} pages")
            segments, warnings = [], []
            for page_no, page in enumerate(pdf.pages, start=1):
                lines = _visual_lines(page)
                opacity_uncertain = _page_has_uncertain_opacity(page)
                if not lines:
                    warnings.append(DocumentWarning("no_text_layer",
                                                    f"Page {page_no} has no extractable text (scanned?)", page=page_no))
                for line_no, (text, flags) in enumerate(lines, start=1):
                    if opacity_uncertain:
                        flags += ("visibility_unverified",)
                    segments.append(make_segment(document_id, "pdf_line", f"p{page_no}:l{line_no}", text,
                                                 page=page_no, line=line_no, flags=flags))
    except DocumentReadError:
        raise
    except (PdfminerException, PDFPasswordIncorrect, PDFSyntaxError, PSException, ValueError, KeyError,
            TypeError) as exc:
        cause = exc.args[0] if isinstance(exc, PdfminerException) and exc.args else exc
        if isinstance(cause, PDFPasswordIncorrect) or isinstance(exc.__cause__, PDFPasswordIncorrect):
            raise DocumentReadError("encrypted", "PDF is password protected") from exc
        raise DocumentReadError("unreadable", f"PDF could not be read: {cause}") from exc
    return segments, warnings, {"pages": page_count}
