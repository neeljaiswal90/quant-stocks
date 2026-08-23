from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from qme.data.ndx.giw_xlsx import GiwXlsxError, decode_giw_weightings_xlsx

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "data" / "ndx" / "official"

EXPECTED = {
    "2026-06-18": {
        "xlsx_sha256": "a7ca5d9c:9dde806f:0d1d1073:41613fe0:8baa0a4b:bdb68ce7:4d09936d:bd2ea0e6",
        "csv_sha256": "15b53599:fadc4112:fbb9dcb8:962addf0:19833dad:60a884e9:2d38dae1:0ee73168",
        "rows": 101,
    },
    "2026-06-22": {
        "xlsx_sha256": "f2e543e0:01ca83a4:5655118c:d5e7817d:9fca1d1c:77e88e17:e087ea6e:50a67328",
        "csv_sha256": "3d5f7870:8a5aa28b:e6e1284e:45bbf0c6:0a89a884:b614d150:07fd56ce:045d9e71",
        "rows": 101,
    },
    "2026-07-07": {
        "xlsx_sha256": "b3a8d906:1f23faa5:7b956319:2b10ddb3:904a3862:c116ce8d:a2aae978:2b635cf9",
        "csv_sha256": "43ee2933:e0ed9587:17caad14:481adaa5:c3160107:1b8b2b02:5a03e022:6e7323be",
        "rows": 103,
    },
    "2026-07-31": {
        "xlsx_sha256": "5ffa374f:30906523:5ffd3b46:a1851166:67e519a9:d524d326:0f5c4472:e440e04c",
        "csv_sha256": "43ee2933:e0ed9587:17caad14:481adaa5:c3160107:1b8b2b02:5a03e022:6e7323be",
        "rows": 103,
    },
}


def _ungroup(value: str) -> str:
    return value.replace(":", "")


def _decode(effective_at: str):
    raw = (FIXTURES / f"NDX-{effective_at}-SOD.xlsx").read_bytes()
    return decode_giw_weightings_xlsx(raw)


@pytest.mark.parametrize("effective_at", tuple(EXPECTED))
def test_official_giw_xlsx_reproduces_checked_csv(effective_at: str) -> None:
    expected = EXPECTED[effective_at]
    decoded = _decode(effective_at)
    checked_csv = (FIXTURES / f"NDX-{effective_at}-SOD.csv").read_bytes()
    assert decoded.xlsx_sha256 == _ungroup(expected["xlsx_sha256"])
    assert decoded.csv_sha256 == _ungroup(expected["csv_sha256"])
    assert decoded.row_count == expected["rows"]
    assert decoded.csv_bytes == checked_csv
    assert hashlib.sha256(checked_csv).hexdigest() == _ungroup(expected["csv_sha256"])
    assert b"\r" not in checked_csv


def test_official_membership_deltas_are_exact() -> None:
    june_18 = set(_decode("2026-06-18").symbols)
    june_22 = set(_decode("2026-06-22").symbols)
    july_7 = set(_decode("2026-07-07").symbols)
    july_31 = set(_decode("2026-07-31").symbols)

    assert june_22 - june_18 == {"ALAB", "CRWV", "NBIS", "RKLB", "TER"}
    assert june_18 - june_22 == {"CHTR", "CTSH", "INSM", "VRSK", "ZS"}
    assert july_7 - june_22 == {"HONA", "SPCX"}
    assert june_22 - july_7 == set()
    assert july_31 == july_7
    assert {"GOOG", "GOOGL"} <= july_31


def _minimal_workbook(*, unsafe_member: bool = False, dtd: bool = False) -> bytes:
    workbook = (
        b'<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/'
        b'spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/'
        b'officeDocument/2006/relationships"><sheets><sheet name="Weightings" '
        b'sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    relationships = (
        b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/'
        b'package/2006/relationships"><Relationship Id="rId1" '
        b'Target="worksheets/sheet1.xml"/></Relationships>'
    )
    shared = (
        b'<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/'
        b'spreadsheetml/2006/main"><si><t>Company Name</t></si>'
        b'<si><t>Security Symbol</t></si><si><t>APPLE INC.</t></si>'
        b'<si><t>AAPL</t></si></sst>'
    )
    if dtd:
        shared = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY y "bad">]><x>&y;</x>'
    sheet = (
        b'<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/'
        b'spreadsheetml/2006/main"><sheetData><row r="5"><c r="A5" t="s"><v>0</v>'
        b'</c><c r="B5" t="s"><v>1</v></c></row><row r="6"><c r="A6" t="s">'
        b'<v>2</v></c><c r="B6" t="s"><v>3</v></c></row></sheetData></worksheet>'
    )
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships)
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
        if unsafe_member:
            archive.writestr("../escape", b"bad")
    return stream.getvalue()


def test_minimal_registered_shape_decodes() -> None:
    decoded = decode_giw_weightings_xlsx(_minimal_workbook())
    assert decoded.row_count == 1
    assert decoded.symbols == ("AAPL",)
    assert decoded.csv_bytes == b"Company Name,Security Symbol\nAPPLE INC.,AAPL\n"


def test_decoder_fails_closed_on_unsafe_zip_member_and_dtd() -> None:
    with pytest.raises(GiwXlsxError, match="unsafe ZIP member"):
        decode_giw_weightings_xlsx(_minimal_workbook(unsafe_member=True))
    with pytest.raises(GiwXlsxError, match="forbidden DTD"):
        decode_giw_weightings_xlsx(_minimal_workbook(dtd=True))


@pytest.mark.parametrize("payload", [None, bytearray(), b"", b"not a zip"])
def test_decoder_requires_exact_valid_bytes(payload: object) -> None:
    with pytest.raises(GiwXlsxError):
        decode_giw_weightings_xlsx(payload)  # type: ignore[arg-type]
