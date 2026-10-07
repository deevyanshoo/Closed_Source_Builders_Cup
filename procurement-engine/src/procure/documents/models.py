"""Located text segments read from a supplier document.

Every segment carries the exact text, where it was found and a SHA-256 hash of that text, so
a later extraction step can cite it as evidence and a reviewer can jump to it. Document text
is untrusted content: readers only report it and never act on what it says.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

UNTRUSTED = "untrusted_document_content"


class DocumentReadError(ValueError):
    """A document could not be read. `code` is a stable machine-readable reason."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Segment:
    """One piece of located text.

    Locators: PDF `p<page>:l<line>`, XLSX `<Sheet>!<Cell>`, CSV `r<row>:c<col>`. All numbers
    are 1-based. Flags mark content a reviewer should treat with suspicion, for example
    `low_visibility`, `hidden_sheet` or `formula_without_cached_value`.
    """
    document_id: str
    kind: str  # pdf_line | sheet_cell | csv_cell
    locator: str
    text: str
    text_sha256: str
    page: int | None = None
    line: int | None = None
    sheet: str | None = None
    cell: str | None = None
    row: int | None = None
    column: int | None = None
    flags: tuple[str, ...] = ()
    value_type: str | None = None  # spreadsheet cells: string, number, date, boolean, formula, ...
    number_format: str | None = None
    formula: str | None = None


def make_segment(document_id: str, kind: str, locator: str, text: str, **fields) -> Segment:
    return Segment(document_id=document_id, kind=kind, locator=locator, text=text,
                   text_sha256=text_hash(text), **fields)


@dataclass(frozen=True)
class DocumentWarning:
    code: str  # for example no_text_layer
    message: str
    page: int | None = None
    locator: str | None = None


@dataclass(frozen=True)
class Document:
    document_id: str  # "sha256:<hex of the file bytes>"
    filename: str
    media_type: str
    byte_size: int
    segments: tuple[Segment, ...]
    warnings: tuple[DocumentWarning, ...] = ()
    metadata: dict = field(default_factory=dict)
    trust: str = UNTRUSTED
    _index: dict = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, "_index", {s.locator: s for s in self.segments})

    def segment(self, locator: str) -> Segment | None:
        return self._index.get(locator)
