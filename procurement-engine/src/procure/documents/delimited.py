"""CSV cells as located segments, `r<row>:c<col>` with 1-based record and column numbers.

Rows are CSV records, so a quoted value spanning two physical lines is still one row. No row
is assumed to be the header: supplier exports often start with title lines. Values are kept
verbatim ("2.598,00" stays as written); interpreting them is the extractor's job.

Only UTF-8 (with or without BOM) is accepted. Other encodings are rejected instead of guessed.
The delimiter is the candidate that splits the most records into the same number of columns.
"""
from __future__ import annotations

import csv
import io
from collections import Counter

from .models import DocumentReadError, make_segment

MAX_CSV_CELLS = 200_000
DELIMITERS = (",", ";", "\t", "|")
_SAMPLE_RECORDS = 200


def _rows(text: str, delimiter: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True))


def detect_delimiter(text: str) -> str:
    best, best_score = DELIMITERS[0], 0
    for delimiter in DELIMITERS:
        try:
            rows = _rows(text, delimiter)[:_SAMPLE_RECORDS]
        except csv.Error:
            continue
        widths = Counter(len(r) for r in rows if len(r) > 1)
        score = widths.most_common(1)[0][1] if widths else 0
        if score > best_score:
            best, best_score = delimiter, score
    return best


def read_csv(data: bytes, document_id: str):
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentReadError("unsupported_encoding", "CSV is not UTF-8; re-export it as UTF-8") from exc
    delimiter = detect_delimiter(text)
    try:
        rows = _rows(text, delimiter)
    except csv.Error as exc:
        raise DocumentReadError("unreadable", f"CSV could not be parsed: {exc}") from exc

    segments = []
    for r, row in enumerate(rows, start=1):
        for c, value in enumerate(row, start=1):
            if not value.strip():
                continue
            if len(segments) >= MAX_CSV_CELLS:
                raise DocumentReadError("too_large", f"CSV has more than {MAX_CSV_CELLS} non-empty cells")
            segments.append(make_segment(document_id, "csv_cell", f"r{r}:c{c}", value, row=r, column=c))
    metadata = {"delimiter": delimiter, "encoding": "utf-8", "records": len(rows)}
    return segments, [], metadata
