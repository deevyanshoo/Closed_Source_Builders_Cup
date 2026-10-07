"""XLSX cells as located segments, for example `Offers!C7`.

Every non-empty cell on every sheet becomes one segment, in sheet then row order. Cell text
is the stored value, not Excel's display formatting; the number format is kept beside it.
Numbers use the shortest exact decimal spelling of the stored value ("2615", "3.42").

Formulas are never evaluated here. A formula with a cached result reports that result; one
without (common for generated files) reports the formula text, flagged
`formula_without_cached_value`. Hidden sheets, rows and columns and near-white font are
flagged rather than dropped.
"""
from __future__ import annotations

import datetime as dt
import io
import re
import zipfile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from .models import DocumentReadError, make_segment

MAX_SHEET_CELLS = 200_000
_PLAIN_SHEET = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def sheet_locator(sheet: str, coordinate: str) -> str:
    if _PLAIN_SHEET.match(sheet):
        return f"{sheet}!{coordinate}"
    return "'" + sheet.replace("'", "''") + f"'!{coordinate}"


def cell_text(value) -> tuple[str, str]:
    """(text, value_type) for a stored cell value."""
    if isinstance(value, str):
        return value, "string"
    if isinstance(value, bool):
        return ("TRUE" if value else "FALSE"), "boolean"
    if isinstance(value, int):
        return str(value), "number"
    if isinstance(value, float):
        return (str(int(value)) if value.is_integer() else repr(value)), "number"
    if isinstance(value, dt.datetime):
        return (value.date().isoformat() if value.time() == dt.time() else value.isoformat()), "date"
    if isinstance(value, (dt.date, dt.time)):
        return value.isoformat(), "date" if isinstance(value, dt.date) else "time"
    return str(value), "other"


def _near_white_font(cell) -> bool:
    color = cell.font.color if cell.font else None
    rgb = getattr(color, "rgb", None) if color is not None and color.type == "rgb" else None
    if not isinstance(rgb, str) or len(rgb) < 6:
        return False
    return all(int(rgb[i:i + 2], 16) >= 0xF0 for i in range(len(rgb) - 6, len(rgb), 2))


def _hidden_columns(ws) -> set[int]:
    hidden = set()
    for dim in ws.column_dimensions.values():
        if dim.hidden and dim.min and dim.max:
            hidden.update(range(dim.min, dim.max + 1))
    return hidden


def read_xlsx(data: bytes, document_id: str):
    try:
        wb = load_workbook(io.BytesIO(data), data_only=False)
        cached = load_workbook(io.BytesIO(data), data_only=True)
    except (zipfile.BadZipFile, InvalidFileException, KeyError, OSError, ValueError) as exc:
        raise DocumentReadError("unreadable", f"XLSX could not be opened: {exc}") from exc

    segments, count = [], 0
    for ws in wb.worksheets:
        sheet_flags = () if ws.sheet_state == "visible" else ("hidden_sheet",)
        hidden_cols = _hidden_columns(ws)
        cached_ws = cached[ws.title]
        for row in ws.iter_rows():
            for cell in row:
                if cell.value is None:
                    continue
                count += 1
                if count > MAX_SHEET_CELLS:
                    raise DocumentReadError("too_large", f"Workbook has more than {MAX_SHEET_CELLS} non-empty cells")
                flags = list(sheet_flags)
                formula = None
                if cell.data_type == "f":
                    formula = cell.value if isinstance(cell.value, str) else getattr(cell.value, "text", str(cell.value))
                    result = cached_ws[cell.coordinate].value
                    if result is None:
                        text, value_type = formula, "formula"
                        flags.append("formula_without_cached_value")
                    else:
                        text, value_type = cell_text(result)
                else:
                    text, value_type = cell_text(cell.value)
                if not text.strip():
                    continue
                if ws.row_dimensions[cell.row].hidden:
                    flags.append("hidden_row")
                if cell.column in hidden_cols:
                    flags.append("hidden_column")
                if _near_white_font(cell):
                    flags.append("low_visibility")
                segments.append(make_segment(
                    document_id, "sheet_cell", sheet_locator(ws.title, cell.coordinate), text,
                    sheet=ws.title, cell=cell.coordinate, row=cell.row, column=cell.column, flags=tuple(flags),
                    value_type=value_type, number_format=cell.number_format, formula=formula))
    metadata = {"sheets": wb.sheetnames,
                "hidden_sheets": [ws.title for ws in wb.worksheets if ws.sheet_state != "visible"]}
    return segments, [], metadata
