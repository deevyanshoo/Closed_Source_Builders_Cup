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
import colorsys
import io
import re
import zipfile
from xml.etree import ElementTree

from openpyxl import load_workbook
from openpyxl.styles.colors import COLOR_INDEX
from openpyxl.utils.cell import column_index_from_string, coordinate_from_string
from openpyxl.utils.exceptions import InvalidFileException

from .models import DocumentReadError, make_segment

MAX_SHEET_CELLS = 200_000
MAX_XLSX_MEMBERS = 1_024
MAX_XLSX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_XLSX_EXPANSION_RATIO = 200
_PLAIN_SHEET = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_SHEET_MEMBER = re.compile(r"^xl/worksheets/[^/]+\.xml$")


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


def _theme_colors(wb) -> list[str | None]:
    try:
        root = ElementTree.fromstring(wb.loaded_theme)
        scheme = root.find(".//{http://schemas.openxmlformats.org/drawingml/2006/main}clrScheme")
        return [(list(entry)[0].get("val") or list(entry)[0].get("lastClr")) for entry in scheme]
    except (TypeError, ValueError, IndexError, AttributeError, ElementTree.ParseError):
        return []


def _color_rgb(color, wb, theme_colors: list[str | None]) -> str | None:
    if color is None:
        return None
    if color.type == "rgb":
        value = color.rgb
    elif color.type == "indexed":
        index = color.indexed
        palette = wb._colors if isinstance(wb._colors, (list, tuple)) else COLOR_INDEX
        value = palette[index] if isinstance(index, int) and 0 <= index < len(palette) else None
    elif color.type == "theme":
        index = color.theme
        value = theme_colors[index] if isinstance(index, int) and 0 <= index < len(theme_colors) else None
    else:
        return None
    if not isinstance(value, str) or len(value) not in (6, 8):
        return None
    value = value[-6:]
    try:
        channels = [int(value[i:i + 2], 16) for i in (0, 2, 4)]
        tint = float(color.tint or 0)
    except (TypeError, ValueError):
        return None
    if not -1 <= tint <= 1:
        return None
    if tint:
        hue, lightness, saturation = colorsys.rgb_to_hls(*(channel / 255 for channel in channels))
        lightness = lightness * (1 + tint) if tint < 0 else lightness * (1 - tint) + tint
        channels = [round(channel * 255) for channel in colorsys.hls_to_rgb(hue, lightness, saturation)]
    return "".join(f"{channel:02X}" for channel in channels)


def _near_white_font(cell, wb, theme_colors: list[str | None]) -> bool:
    color = cell.font.color if cell.font else None
    if color is None:
        return False
    rgb = _color_rgb(color, wb, theme_colors)
    if rgb is None:
        return True  # Unknown color is unsafe to assume visible on a white sheet.
    return all(int(rgb[i:i + 2], 16) >= 0xF0 for i in (0, 2, 4))


def _preflight(data: bytes) -> None:
    """Check ZIP expansion and stored cell coordinates before openpyxl allocates cells."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > MAX_XLSX_MEMBERS:
                raise DocumentReadError("too_large", f"XLSX has more than {MAX_XLSX_MEMBERS} ZIP members")
            expanded = 0
            occupied = rectangular_work = 0
            for info in members:
                expanded += info.file_size
                if expanded > MAX_XLSX_UNCOMPRESSED_BYTES:
                    raise DocumentReadError("too_large", "XLSX declared uncompressed content is too large")
                if info.file_size > MAX_XLSX_EXPANSION_RATIO * max(info.compress_size, 1):
                    raise DocumentReadError("too_large", "XLSX ZIP member expands too much")
                if not _SHEET_MEMBER.match(info.filename):
                    continue
                max_row = max_col = 0
                with archive.open(info) as sheet:
                    root = None
                    for event, element in ElementTree.iterparse(sheet, events=("start", "end")):
                        if root is None:
                            root = element
                        if event == "end":
                            if element.tag.rsplit("}", 1)[-1] == "row":
                                root.clear()
                            continue
                        if element.tag.rsplit("}", 1)[-1] != "c":
                            continue
                        coordinate = element.get("r")
                        if not coordinate:
                            raise DocumentReadError("unreadable", "XLSX cell has no coordinate")
                        try:
                            letters, row = coordinate_from_string(coordinate)
                            col = column_index_from_string(letters)
                        except ValueError as exc:
                            raise DocumentReadError("unreadable", "XLSX cell has an invalid coordinate") from exc
                        max_row, max_col = max(max_row, row), max(max_col, col)
                        occupied += 1
                        if occupied > MAX_SHEET_CELLS or rectangular_work + max_row * max_col > MAX_SHEET_CELLS:
                            raise DocumentReadError("too_large", f"XLSX exceeds {MAX_SHEET_CELLS} cell work units")
                rectangular_work += max_row * max_col
    except (zipfile.BadZipFile, RuntimeError, OSError, ElementTree.ParseError) as exc:
        raise DocumentReadError("unreadable", f"XLSX archive could not be checked: {exc}") from exc


def _hidden_columns(ws) -> set[int]:
    hidden = set()
    for dim in ws.column_dimensions.values():
        if dim.hidden and dim.min and dim.max:
            hidden.update(range(dim.min, dim.max + 1))
    return hidden


def read_xlsx(data: bytes, document_id: str):
    _preflight(data)
    try:
        wb = load_workbook(io.BytesIO(data), data_only=False)
        cached = load_workbook(io.BytesIO(data), data_only=True)
    except (zipfile.BadZipFile, InvalidFileException, KeyError, OSError, ValueError) as exc:
        raise DocumentReadError("unreadable", f"XLSX could not be opened: {exc}") from exc

    segments, count = [], 0
    theme_colors = _theme_colors(wb)
    for ws in wb.worksheets:
        sheet_flags = () if ws.sheet_state == "visible" else ("hidden_sheet",)
        hidden_cols = _hidden_columns(ws)
        cached_ws = cached[ws.title]
        for _, cell in sorted(ws._cells.items()):
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
            if _near_white_font(cell, wb, theme_colors):
                flags.append("low_visibility")
            segments.append(make_segment(
                document_id, "sheet_cell", sheet_locator(ws.title, cell.coordinate), text,
                sheet=ws.title, cell=cell.coordinate, row=cell.row, column=cell.column, flags=tuple(flags),
                value_type=value_type, number_format=cell.number_format, formula=formula))
    metadata = {"sheets": wb.sheetnames,
                "hidden_sheets": [ws.title for ws in wb.worksheets if ws.sheet_state != "visible"]}
    return segments, [], metadata
