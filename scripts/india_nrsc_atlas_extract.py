"""Extract row-level lake records from the verified NRSC atlas PDF.

Reads the byte-verified `IHR_GlacialLake_Atlas.pdf` from the external
evidence root (never from the repository — third-party payloads are not
tracked), extracts Table 68 (2,431 lakes >= 10 ha, dual-column layout)
and Table 69 (lakes >= 50 ha) into hash-bound JSON artifacts with a
verifiable extraction receipt.

This is local text processing of an already-acquired artifact. It
authorizes no acquisition and performs no adjudication: extracted rows
carry territory UNASSESSED and identity UNRECONCILED — adjudication
decides both later.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = Path("/Users/sanjayb/nepal-event-anomaly-evidence")
INTAKE_ROOT = EVIDENCE_ROOT / "india-phase0-source-intake"
DEFAULT_PDF = INTAKE_ROOT / "IHR_GlacialLake_Atlas.pdf"

SCHEMA = "NRSC_ATLAS_TABLE_EXTRACTION_V0"
AUTHORITY_FLAGS = {
    "bulk_acquisition_authorized": False,
    "weather_download_authorized": False,
    "satellite_bulk_authorized": False,
    "seismic_waveform_authorized": False,
    "forecast_authorized": False,
    "warning_authorized": False,
    "detector_authorized": False,
    "odds_authorized": False,
    "causal_authorized": False,
    "operational_authorized": False,
}

# A lake record: S.No, 3-part lake id (basin sheet serial), lat, long,
# subbasin words, GL type code, area ha, elevation m. Non-anchored so a
# dual-column Table-68 line yields two records via finditer.
_ROW = re.compile(
    r"(?P<sno>\d{1,4})\s+"
    r"(?P<basin>\d{2})\s+(?P<sheet>[0-9]{2}[A-Z][0-9]{2})\s+(?P<serial>\d{5})\s+"
    r"(?P<lat>\d{2}\.\d{3})\s+(?P<lon>\d{2}\.\d{3})\s+"
    r"(?P<subbasin>[A-Za-z][A-Za-z .'-]*?)\s{2,}"
    r"(?P<gltype>[A-Z]\([a-z]+\)|[A-Z])\s+"
    r"(?P<area>[\d,]+\.\d{1,2})\s+(?P<elev>[\d,]+)(?=\s|$)")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _page_of(text_path: Path, needle: str) -> tuple[int, list[str]]:
    """Return (line index, lines) splitting pdftotext output by formfeed."""
    lines = text_path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if needle in line:
            return i, lines
    raise ValueError(f"marker not found: {needle!r}")


def _lake_record(match: re.Match[str], table: str) -> dict[str, Any]:
    lake_id = (f"{match['basin']} {match['sheet']} {match['serial']}")
    return {
        "serial_no": int(match["sno"]),
        "glacial_lake_id": lake_id,
        "glacial_lake_id_compact": lake_id.replace(" ", ""),
        "latitude": float(match["lat"]),
        "longitude": float(match["lon"]),
        "subbasin": " ".join(match["subbasin"].split()),
        "gl_type": match["gltype"],
        "area_ha": float(match["area"].replace(",", "")),
        "elevation_m": int(match["elev"].replace(",", "")),
        "source_table": table,
    }


def _parse_rows(lines: list[str], start: int, end: int,
                table: str) -> list[dict]:
    """Each line may hold one record (Table 69) or two (Table 68)."""
    records: list[dict] = []
    for line in lines[start:end]:
        for m in _ROW.finditer(line):
            records.append(_lake_record(m, table))
    return records


def extract(pdf_path: Path, text_path: Path | None = None) -> dict:
    if text_path is None:
        proc = subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), "-"],
            capture_output=True, text=True, check=True)
        lines = proc.stdout.splitlines()
    else:
        lines = Path(text_path).read_text(encoding="utf-8").splitlines()

    def _marker(needle: str, after: int = 0) -> int:
        # TOC entries match the needle too; a real table header has a
        # S.No column row within the next few lines.
        for i in range(after, len(lines)):
            if needle in lines[i] and any(
                    "S.No" in lines[j]
                    for j in range(i + 1, min(i + 8, len(lines)))):
                return i
        raise ValueError(f"marker not found: {needle!r}")

    t68_start = _marker("Table 68: List of glacial lakes")
    t69_start = _marker("Table 69: List of glacial lakes", t68_start)
    t68 = _parse_rows(lines, t68_start, t69_start, "table_68_ge10ha")
    t69 = _parse_rows(lines, t69_start, len(lines), "table_69_ge50ha")

    anomalies: list[dict] = []
    for name, recs in (("table_68_ge10ha", t68), ("table_69_ge50ha", t69)):
        serials = sorted(r["serial_no"] for r in recs)
        expected = list(range(1, (serials[-1] if serials else 0) + 1))
        missing = sorted(set(expected) - set(serials))
        if missing:
            raise ValueError(f"{name}: serial gaps (lost rows) "
                             f"missing {missing[:10]}")
        dup = sorted(s for s in set(serials) if serials.count(s) > 1)
        if dup:
            anomalies.append({
                "table": name, "kind": "PRINTED_SERIAL_DUPLICATE",
                "serials": dup,
                "note": "the atlas prints the same S.No on distinct lake "
                        "IDs; all rows retained with serials as printed"})
        ids = [r["glacial_lake_id"] for r in recs]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{name}: duplicate lake ids extracted")
        recs.sort(key=lambda r: (r["serial_no"], r["glacial_lake_id"]))
    return {
        "schema": SCHEMA,
        "version": 0,
        "claim_scope": "research_only_no_operational_authorization",
        "authority": dict(AUTHORITY_FLAGS),
        "extraction": {
            "method": "pdftotext -layout + fixed-pattern row parse",
            "source_pdf_sha256": sha256_file(pdf_path),
            "source_pdf_bytes": pdf_path.stat().st_size,
            "extracted_utc": datetime.now(timezone.utc).isoformat(),
            "tables": {"table_68_ge10ha": len(t68),
                       "table_69_ge50ha": len(t69)},
            "anomalies": anomalies,
            "interpretation_limit": (
                "Rows are atlas-printed inventory attributes only: no "
                "observation history, no event linkage, no territory "
                "adjudication (the atlas covers transboundary reaches)."),
        },
        "records": t68 + t69,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--text", type=Path,
                        help="pre-extracted pdftotext -layout output")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    doc = extract(args.pdf, args.text)
    import sys
    sys.path.insert(0, str(REPO / "scripts"))
    from p5_safe_io import write_once_json, write_once_sidecar
    write_once_json(args.out, doc)
    write_once_sidecar(args.out)
    t = doc["extraction"]["tables"]
    print(f"NRSC_EXTRACT_OK: {args.out} "
          f"(t68={t['table_68_ge10ha']}, t69={t['table_69_ge50ha']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
