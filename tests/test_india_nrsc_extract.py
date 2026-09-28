"""Tests for the NRSC atlas row extractor (offline, fixture text)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import india_nrsc_atlas_extract as nx


_T68_LINE = (" 1      01 42D15 00009   36.412   72.901 Gilgit"
             "               M(e)    11.23    4,256    3     01   43E09   00912"
             "   35.904   73.568 Gilgit                  E(o)   18.03    4,109")
_T69_LINE = ("    1      01    42H09     00200         36.879       73.704"
             "   Gilgit                      O         262.57         4,286")

TEXT = "\n".join([
    "front matter",
    "Table 68: List of glacial lakes (>=10 ha) toc line",
    "more front matter",
    "        Table 68: List of glacial lakes",
    "S.No.  Glacial Lake ID No   Lat   Long   Subbasin   GL Type   Area   Elev",
    _T68_LINE,
    " 2      01 42H03 00089   36.263   73.048 Gilgit"
    "                O      21.68    4,618",
    "",
    "Table 69: List of glacial lakes toc line",
    "                                     Table 69: List of glacial lakes",
    "  S.No.   Glacial Lake ID Number     Latitude     Longitude    Subbasin"
    "   GL Type   Area (ha)   Elevation (m)",
    _T69_LINE,
    "trailing prose with no records",
])


def _lines() -> list[str]:
    return TEXT.splitlines()


def test_dual_column_lines_yield_two_records() -> None:
    recs = nx._parse_rows(_lines(), 5, 8, "table_68_ge10ha")
    assert len(recs) == 3  # line 5 has two records, line 6 has one
    first = recs[0]
    assert first["serial_no"] == 1
    assert first["glacial_lake_id"] == "01 42D15 00009"
    assert first["glacial_lake_id_compact"] == "0142D1500009"
    assert first["subbasin"] == "Gilgit"
    assert first["gl_type"] == "M(e)"
    assert first["area_ha"] == 11.23
    assert first["elevation_m"] == 4256
    assert recs[1]["serial_no"] == 3
    assert recs[1]["glacial_lake_id_compact"] == "0143E0900912"


def test_toc_marker_is_not_confused_with_table_body(tmp_path) -> None:
    text = tmp_path / "t.txt"
    text.write_text(TEXT)
    # A tiny fake PDF is enough: extraction hashes it but parses --text.
    pdf = tmp_path / "fake.pdf"
    pdf.write_bytes(b"%PDF-fake")
    doc = nx.extract(pdf, text)
    assert doc["extraction"]["tables"]["table_68_ge10ha"] == 3
    assert doc["extraction"]["tables"]["table_69_ge50ha"] == 1
    assert doc["authority"]["forecast_authorized"] is False


def test_serial_gap_fails_closed(tmp_path) -> None:
    # Remove serial 1's record: serial set must reject (lost row).
    lines = _lines()
    lines[5] = ""
    text = tmp_path / "t.txt"
    text.write_text("\n".join(lines))
    pdf = tmp_path / "fake.pdf"
    pdf.write_bytes(b"%PDF-fake")
    with pytest.raises(ValueError, match="serial gaps"):
        nx.extract(pdf, text)


def test_duplicate_printed_serials_recorded_not_dropped(tmp_path) -> None:
    lines = _lines()
    lines[6] = (" 2      01 42H03 00089   36.263   73.048 Gilgit"
                "                O      21.68    4,618   "
                "1      01 42H15 00357   36.265   73.955 Gilgit   O   27.89    3,706")
    text = tmp_path / "t.txt"
    text.write_text("\n".join(lines))
    pdf = tmp_path / "fake.pdf"
    pdf.write_bytes(b"%PDF-fake")
    doc = nx.extract(pdf, text)
    assert doc["extraction"]["tables"]["table_68_ge10ha"] == 4
    kinds = [a["kind"] for a in doc["extraction"]["anomalies"]]
    assert "PRINTED_SERIAL_DUPLICATE" in kinds


def test_two_letter_subtype_and_comma_area_parse() -> None:
    line = ("1488   03   82F16 09776   30.020   93.967"
            " Lower Yarlung Tsangpo   E(v)   2,658.4    3,475")
    recs = nx._parse_rows([line], 0, 1, "table_68_ge10ha")
    assert len(recs) == 1
    assert recs[0]["area_ha"] == 2658.4
    assert recs[0]["subbasin"] == "Lower Yarlung Tsangpo"
    ml = nx._parse_rows(
        ["223    01 52E11 03389   35.476   77.513 Shyok"
         "               M(lg)    21.93    5,342"], 0, 1, "t68")
    assert ml[0]["gl_type"] == "M(lg)"


def test_nonsense_lines_are_ignored() -> None:
    assert nx._parse_rows(["narrative prose", "Figure 12: lakes"], 0, 2,
                          "t68") == []
