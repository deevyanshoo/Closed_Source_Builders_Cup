"""Entry point: pick the reader from the file extension, after checking the bytes agree.

The extension decides the reader and the leading bytes must match it, so a PDF renamed to
.csv is rejected rather than read as text. Missing, unsupported, empty, oversized, mismatched
and password-protected inputs raise DocumentReadError with an explicit code. A readable file
with no text gets a `no_content` warning.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from . import delimited, pdf, spreadsheet
from .models import Document, DocumentReadError, DocumentWarning

MAX_DOCUMENT_BYTES = 25 * 1024 * 1024

_FORMATS = {
    ".pdf": ("pdf", "application/pdf", pdf.read_pdf),
    ".xlsx": ("zip", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", spreadsheet.read_xlsx),
    ".csv": ("text", "text/csv", delimited.read_csv),
}


_OLE_CONTAINER = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # password-protected workbooks and legacy .xls


def _signature(data: bytes) -> str:
    if b"%PDF-" in data[:1024]:
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        return "zip"
    if data.startswith(_OLE_CONTAINER):
        return "ole"
    return "text"


def read_document(source: str | Path | bytes, filename: str | None = None) -> Document:
    """Read a PDF, XLSX or CSV quote into located, hashed text segments.

    `source` is a path or the raw bytes. With bytes, `filename` is required so the format
    is known. Nothing in the document is interpreted as an instruction.
    """
    if isinstance(source, (bytes, bytearray)):
        data, name = bytes(source), filename
    else:
        path = Path(source)
        name = filename or path.name
        if not path.is_file():
            raise DocumentReadError("not_found", f"{name} does not exist or is not a file")
        if path.stat().st_size > MAX_DOCUMENT_BYTES:
            raise DocumentReadError("too_large", f"{name} is larger than {MAX_DOCUMENT_BYTES} bytes")
        data = path.read_bytes()
    if not name:
        raise DocumentReadError("unsupported_format", "A filename with a .pdf, .xlsx or .csv extension is required")
    suffix = Path(name).suffix.lower()
    if suffix not in _FORMATS:
        raise DocumentReadError("unsupported_format", f"{name}: only .pdf, .xlsx and .csv are supported")
    if not data:
        raise DocumentReadError("empty", f"{name} is empty")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise DocumentReadError("too_large", f"{name} is larger than {MAX_DOCUMENT_BYTES} bytes")
    signature, media_type, read = _FORMATS[suffix]
    actual = _signature(data)
    if suffix == ".xlsx" and actual == "ole":
        raise DocumentReadError("encrypted_or_legacy_format",
                                f"{name} is password protected or a legacy .xls workbook; save it as an unprotected .xlsx")
    if actual != signature:
        raise DocumentReadError("format_mismatch", f"{name}: file content does not match its {suffix} extension")

    document_id = "sha256:" + hashlib.sha256(data).hexdigest()
    segments, warnings, metadata = read(data, document_id)
    if not segments and not warnings:
        warnings = [DocumentWarning("no_content", f"{name} contains no text")]
    return Document(document_id=document_id, filename=name, media_type=media_type, byte_size=len(data),
                    segments=tuple(segments), warnings=tuple(warnings), metadata=metadata)
