"""CSV cells as located segments, `r<row>:c<col>` with 1-based record and column numbers.

Rows are CSV records, so a quoted value spanning two physical lines is still one row. No row
is assumed to be the header: supplier exports often start with title lines. Values are kept
verbatim ("2.598,00" stays as written); interpreting them is the extractor's job.

Only UTF-8 (with or without BOM) is accepted. Other encodings are rejected instead of guessed.
The delimiter is chosen from a bounded sample by sustained, consistent columns.
"""
from __future__ import annotations

import csv
import io
from itertools import islice

from .models import DocumentReadError, make_segment

MAX_CSV_CELLS = 200_000
MAX_CSV_RECORDS = 200_000
MAX_CSV_RECORD_CHARS = 65_536
MAX_CSV_FIELDS_PER_RECORD = 20_000
MAX_CSV_SAMPLE_FIELDS = 20_000
DELIMITERS = (",", ";", "\t", "|")
_SAMPLE_RECORDS = 200


class _BoundedLines:
    """Keep a quoted multiline record bounded while csv.reader requests lines."""

    def __init__(self, text: str):
        self.source = io.StringIO(text, newline="")
        self.record_chars = 0

    def __iter__(self):
        return self

    def __next__(self):
        line = self.source.readline(MAX_CSV_RECORD_CHARS - self.record_chars + 1)
        if not line:
            raise StopIteration
        self.record_chars += len(line)
        if self.record_chars > MAX_CSV_RECORD_CHARS:
            raise DocumentReadError("too_large", f"CSV record exceeds {MAX_CSV_RECORD_CHARS} characters")
        return line


def _rows(text: str, delimiter: str):
    lines = _BoundedLines(text)
    for row in csv.reader(lines, delimiter=delimiter, strict=True):
        lines.record_chars = 0
        yield row


def _structure(rows: list[list[str]]) -> tuple[int, int, int]:
    """Longest equal-width run, all matching rows, then fewest breaks."""
    widths = [len(row) for row in rows]
    runs = []
    for width in set(widths):
        if width <= 1:
            continue
        current = longest = 0
        for value in widths:
            current = current + 1 if value == width else 0
            longest = max(longest, current)
        runs.append((longest, widths.count(width), -sum(w != width for w in widths)))
    return max(runs, default=(0, 0, 0))


def _sample_rows(text: str, delimiter: str) -> list[list[str]]:
    rows, cells = [], 0
    for row in islice(_rows(text, delimiter), _SAMPLE_RECORDS):
        if len(row) > MAX_CSV_FIELDS_PER_RECORD:
            raise DocumentReadError("too_large", f"CSV record has more than {MAX_CSV_FIELDS_PER_RECORD} fields")
        cells += len(row)
        if cells > MAX_CSV_SAMPLE_FIELDS:
            break
        rows.append(row)
    return rows


def detect_delimiter(text: str) -> str:
    scores = {}
    for delimiter in DELIMITERS:
        try:
            rows = _sample_rows(text, delimiter)
        except csv.Error:
            continue
        scores[delimiter] = _structure(rows)
    if not scores:
        raise DocumentReadError("unreadable", "CSV could not be parsed with a supported delimiter")
    best_score = max(scores.values())
    if best_score[0] == 0:
        if not text.strip(" \t\r\n;|,"):
            return ","
        raise DocumentReadError("ambiguous_delimiter", "CSV has no consistent multi-column records")
    winners = [delimiter for delimiter, score in scores.items() if score == best_score]
    if len(winners) != 1:
        raise DocumentReadError("ambiguous_delimiter", "CSV delimiter is ambiguous")
    return winners[0]


def read_csv(data: bytes, document_id: str):
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentReadError("unsupported_encoding", "CSV is not UTF-8; re-export it as UTF-8") from exc
    delimiter = detect_delimiter(text)
    rows = _rows(text, delimiter)
    segments, records, cells = [], 0, 0
    try:
        for r, row in enumerate(rows, start=1):
            records = r
            if records > MAX_CSV_RECORDS:
                raise DocumentReadError("too_large", f"CSV has more than {MAX_CSV_RECORDS} records")
            if len(row) > MAX_CSV_FIELDS_PER_RECORD:
                raise DocumentReadError("too_large", f"CSV record has more than {MAX_CSV_FIELDS_PER_RECORD} fields")
            cells += len(row)
            if cells > MAX_CSV_CELLS:
                raise DocumentReadError("too_large", f"CSV has more than {MAX_CSV_CELLS} fields")
            for c, value in enumerate(row, start=1):
                if not value.strip():
                    continue
                segments.append(make_segment(document_id, "csv_cell", f"r{r}:c{c}", value, row=r, column=c))
    except csv.Error as exc:
        raise DocumentReadError("unreadable", f"CSV could not be parsed: {exc}") from exc
    metadata = {"delimiter": delimiter, "encoding": "utf-8", "records": records}
    return segments, [], metadata
