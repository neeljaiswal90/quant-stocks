"""Strict decoder for the narrow Nasdaq GIW ``ExportWeightings`` workbook.

The GIW endpoint returns a small OOXML workbook whose ``Weightings`` sheet has
``Company Name`` and ``Security Symbol`` in columns A and B.  This module does
not implement a general spreadsheet reader.  It accepts only that registered
shape and emits a deterministic UTF-8/LF CSV suitable for the existing manual
GIW snapshot ingestion contract.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree

MAX_WORKBOOK_BYTES = 8 * 1024 * 1024
MAX_MEMBER_COUNT = 64
MAX_UNCOMPRESSED_BYTES = 4 * 1024 * 1024

_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,15}$")
_CELL_RE = re.compile(r"^(?P<column>[A-Z]+)(?P<row>[1-9][0-9]*)$")


class GiwXlsxError(ValueError):
    """Raised when an XLSX is not the registered GIW export shape."""


@dataclass(frozen=True)
class DecodedGiwWorkbook:
    """Deterministic decoded view of one exact GIW workbook."""

    xlsx_sha256: str
    xlsx_bytes: int
    csv_sha256: str
    csv_bytes: bytes
    row_count: int
    symbols: tuple[str, ...]


def _xml_root(payload: bytes, *, member: str) -> ElementTree.Element:
    upper = payload.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise GiwXlsxError(f"{member} contains a forbidden DTD or entity")
    try:
        return ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise GiwXlsxError(f"{member} is not well-formed XML") from exc


def _validated_members(archive: zipfile.ZipFile) -> dict[str, bytes]:
    entries = archive.infolist()
    if len(entries) > MAX_MEMBER_COUNT:
        raise GiwXlsxError("workbook contains too many ZIP members")
    names = [entry.filename for entry in entries]
    if len(names) != len(set(names)):
        raise GiwXlsxError("workbook contains duplicate ZIP member names")

    total = 0
    result: dict[str, bytes] = {}
    for entry in entries:
        name = entry.filename
        if (
            not name
            or name.startswith(("/", "\\"))
            or "\\" in name
            or any(part in {"", ".", ".."} for part in name.split("/"))
        ):
            raise GiwXlsxError("workbook contains an unsafe ZIP member name")
        if entry.flag_bits & 0x1:
            raise GiwXlsxError("encrypted workbook members are not supported")
        total += entry.file_size
        if total > MAX_UNCOMPRESSED_BYTES:
            raise GiwXlsxError("workbook expands beyond the registered limit")
        if entry.is_dir():
            continue
        result[name] = archive.read(entry)
    return result


def _shared_strings(payload: bytes) -> tuple[str, ...]:
    root = _xml_root(payload, member="xl/sharedStrings.xml")
    strings: list[str] = []
    for item in root.findall(f"{{{_MAIN_NS}}}si"):
        text_nodes = item.findall(f".//{{{_MAIN_NS}}}t")
        if not text_nodes:
            raise GiwXlsxError("shared string has no text node")
        strings.append("".join(node.text or "" for node in text_nodes))
    return tuple(strings)


def _shared_cell_text(
    cell: ElementTree.Element,
    shared: tuple[str, ...],
    *,
    cell_ref: str,
) -> str | None:
    value = cell.find(f"{{{_MAIN_NS}}}v")
    if value is None or value.text is None:
        return None
    if cell.get("t") != "s":
        raise GiwXlsxError(f"{cell_ref} is not a shared-string cell")
    try:
        index = int(value.text)
        return shared[index]
    except (ValueError, IndexError) as exc:
        raise GiwXlsxError(f"{cell_ref} has an invalid shared-string index") from exc


def _weightings_sheet_path(members: dict[str, bytes]) -> str:
    try:
        workbook = _xml_root(members["xl/workbook.xml"], member="xl/workbook.xml")
        relationships = _xml_root(
            members["xl/_rels/workbook.xml.rels"],
            member="xl/_rels/workbook.xml.rels",
        )
    except KeyError as exc:
        raise GiwXlsxError("workbook is missing its workbook relationship metadata") from exc

    sheets = workbook.findall(f".//{{{_MAIN_NS}}}sheet")
    if len(sheets) != 1 or sheets[0].get("name") != "Weightings":
        raise GiwXlsxError("workbook must contain exactly one Weightings sheet")
    relationship_id = sheets[0].get(f"{{{_REL_NS}}}id")
    targets = {
        relation.get("Id"): relation.get("Target")
        for relation in relationships.findall(f"{{{_PACKAGE_REL_NS}}}Relationship")
    }
    target = targets.get(relationship_id)
    if target != "worksheets/sheet1.xml":
        raise GiwXlsxError("Weightings sheet must resolve to worksheets/sheet1.xml")
    return "xl/worksheets/sheet1.xml"


def decode_giw_weightings_xlsx(payload: bytes) -> DecodedGiwWorkbook:
    """Decode exact GIW XLSX bytes into deterministic two-column CSV bytes."""

    if type(payload) is not bytes:
        raise GiwXlsxError("payload must be exact bytes")
    if not payload or len(payload) > MAX_WORKBOOK_BYTES:
        raise GiwXlsxError("workbook byte length is outside the registered limit")
    try:
        with zipfile.ZipFile(io.BytesIO(payload), mode="r") as archive:
            members = _validated_members(archive)
    except (zipfile.BadZipFile, OSError) as exc:
        raise GiwXlsxError("payload is not a valid OOXML ZIP container") from exc

    try:
        shared = _shared_strings(members["xl/sharedStrings.xml"])
        sheet_path = _weightings_sheet_path(members)
        sheet = _xml_root(members[sheet_path], member=sheet_path)
    except KeyError as exc:
        raise GiwXlsxError("workbook is missing a required GIW member") from exc

    parsed_rows: list[tuple[int, str, str]] = []
    for row in sheet.findall(f".//{{{_MAIN_NS}}}row"):
        row_number_text = row.get("r")
        if row_number_text is None:
            raise GiwXlsxError("worksheet row is missing its row number")
        try:
            row_number = int(row_number_text)
        except ValueError as exc:
            raise GiwXlsxError("worksheet row number is invalid") from exc

        populated: dict[str, str] = {}
        for cell in row.findall(f"{{{_MAIN_NS}}}c"):
            cell_ref = cell.get("r")
            match = _CELL_RE.fullmatch(cell_ref or "")
            if match is None or int(match.group("row")) != row_number:
                raise GiwXlsxError("worksheet cell reference is invalid")
            text = _shared_cell_text(cell, shared, cell_ref=cell_ref or "")
            if text is not None:
                populated[match.group("column")] = text
        if not populated:
            continue
        if set(populated) - {"A", "B"}:
            raise GiwXlsxError("Weightings sheet has populated cells outside columns A and B")
        if set(populated) != {"A", "B"}:
            raise GiwXlsxError("Weightings row must populate both Company Name and Security Symbol")
        parsed_rows.append((row_number, populated["A"], populated["B"]))

    if not parsed_rows or parsed_rows[0] != (5, "Company Name", "Security Symbol"):
        raise GiwXlsxError("Weightings header must be exactly row 5 columns A and B")

    components = parsed_rows[1:]
    if not components:
        raise GiwXlsxError("Weightings sheet contains no components")
    expected_rows = list(range(6, 6 + len(components)))
    if [row_number for row_number, _, _ in components] != expected_rows:
        raise GiwXlsxError("component rows must be contiguous beginning at row 6")

    seen: set[str] = set()
    output_rows: list[tuple[str, str]] = []
    for row_number, company_name, symbol in components:
        company = company_name.strip()
        security_symbol = symbol.strip().upper()
        if not company:
            raise GiwXlsxError(f"row {row_number} has an empty company name")
        if not _SYMBOL_RE.fullmatch(security_symbol):
            raise GiwXlsxError(f"row {row_number} has an invalid security symbol")
        if security_symbol in seen:
            raise GiwXlsxError(f"duplicate security symbol: {security_symbol}")
        seen.add(security_symbol)
        output_rows.append((company, security_symbol))

    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(("Company Name", "Security Symbol"))
    writer.writerows(output_rows)
    csv_bytes = stream.getvalue().encode("utf-8")
    return DecodedGiwWorkbook(
        xlsx_sha256=hashlib.sha256(payload).hexdigest(),
        xlsx_bytes=len(payload),
        csv_sha256=hashlib.sha256(csv_bytes).hexdigest(),
        csv_bytes=csv_bytes,
        row_count=len(output_rows),
        symbols=tuple(symbol for _, symbol in output_rows),
    )


__all__ = [
    "DecodedGiwWorkbook",
    "GiwXlsxError",
    "decode_giw_weightings_xlsx",
]
