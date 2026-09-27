"""Validate the India Phase-0 evidence register.

An evidence register is the only surface that can make a citation
resolvable.  A register record binds a locator to a source name/version,
an optional payload digest, a verification state, declared coverage, and
explicit limitations.  Catalog reference strings and reviewer citations
are attribution until they resolve to a register record whose
verification state is ``BYTES_VERIFIED`` — meaning the referenced bytes
were pinned and hashed.  This module performs no network activity and
authorizes no acquisition.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "scripts"))
from p5_safe_io import write_once_json, write_once_sidecar  # noqa: E402


SCHEMA = "INDIA_EVIDENCE_REGISTER_V0"
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
VERIFICATION_STATES = {"BYTES_VERIFIED", "METADATA_VERIFIED", "UNRESOLVED"}
LOCATOR_TYPES = {"URL", "DOI", "REPORT", "DATASET_RECORD", "LOCAL_PATH_LABEL"}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_CITATION_RE = re.compile(r"^evidence:(?P<evidence_id>.+)$")


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _iso_date(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        return dt.date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _iso_utc(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def validate_register(document: object) -> list[str]:
    """Return all contract violations for an evidence register."""
    problems: list[str] = []
    if not isinstance(document, dict):
        return ["register must be an object"]
    if document.get("schema") != SCHEMA:
        problems.append(f"schema must be {SCHEMA}")
    if document.get("version") != 0:
        problems.append("version must be integer 0")
    if document.get("claim_scope") != "research_only_no_operational_authorization":
        problems.append("claim_scope must remain research-only")
    if document.get("authority") != AUTHORITY_FLAGS:
        problems.append("authority flags must be present and false")

    records = document.get("records")
    if not isinstance(records, list):
        return problems + ["records must be a list"]
    seen: set[str] = set()
    for index, record in enumerate(records):
        prefix = f"records[{index}]"
        if not isinstance(record, dict):
            problems.append(f"{prefix} must be an object")
            continue
        evidence_id = record.get("evidence_id")
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            problems.append(f"{prefix}.evidence_id must be a non-empty string")
        elif evidence_id in seen:
            problems.append(f"duplicate evidence_id: {evidence_id}")
        else:
            seen.add(evidence_id)
        for field in ("source_name", "source_version", "locator",
                      "access_terms"):
            if not isinstance(record.get(field), str) or not record[field].strip():
                problems.append(f"{prefix}.{field} must be a non-empty string")
        if record.get("locator_type") not in LOCATOR_TYPES:
            problems.append(f"{prefix}.locator_type must be one of "
                            f"{sorted(LOCATOR_TYPES)}")
        state = record.get("verification_state")
        if state not in VERIFICATION_STATES:
            problems.append(f"{prefix}.verification_state must be one of "
                            f"{sorted(VERIFICATION_STATES)}")
        digest = record.get("sha256")
        if digest is not None and not (isinstance(digest, str)
                                      and _HEX64.fullmatch(digest)):
            problems.append(f"{prefix}.sha256 must be 64 lowercase hex or null")
        coverage = record.get("coverage")
        if not isinstance(coverage, dict):
            problems.append(f"{prefix}.coverage must be an object")
            coverage = {}
        start, end = coverage.get("temporal_start"), coverage.get("temporal_end")
        if start is not None and not _iso_date(start):
            problems.append(f"{prefix}.coverage.temporal_start must be ISO date or null")
        if end is not None and not _iso_date(end):
            problems.append(f"{prefix}.coverage.temporal_end must be ISO date or null")
        if start is not None and end is not None and start > end:
            problems.append(f"{prefix}.coverage temporal interval is reversed")
        spatial = coverage.get("spatial_scope")
        if not isinstance(spatial, str) or not spatial.strip():
            problems.append(f"{prefix}.coverage.spatial_scope must be non-empty")
        limitations = record.get("limitations")
        if not isinstance(limitations, list) or not limitations or any(
                not isinstance(item, str) or not item.strip()
                for item in limitations):
            problems.append(f"{prefix}.limitations must be a non-empty list")
        if record.get("registered_utc") is not None and not _iso_utc(
                record["registered_utc"]):
            problems.append(f"{prefix}.registered_utc must be timezone-aware ISO")
        # A digest binds bytes: only BYTES_VERIFIED records may carry one.
        if state == "BYTES_VERIFIED":
            if digest is None:
                problems.append(
                    f"{prefix}: BYTES_VERIFIED requires a sha256 payload digest")
            if start is None or end is None:
                problems.append(
                    f"{prefix}: BYTES_VERIFIED requires explicit temporal coverage")
        elif digest is not None:
            problems.append(
                f"{prefix}: sha256 requires verification_state BYTES_VERIFIED")
    return problems


def verified_records(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return {evidence_id: record} for BYTES_VERIFIED register entries."""
    out: dict[str, dict[str, Any]] = {}
    for record in document.get("records", []):
        if isinstance(record, dict) and record.get("verification_state") == "BYTES_VERIFIED":
            out[record["evidence_id"]] = record
    return out


def resolved_evidence_ids(
        citations: Any,
        verified: dict[str, dict[str, Any]],
        start: str | None,
        end: str | None) -> list[str]:
    """Return the verified evidence ids a citation list resolves to.

    A citation resolves only when it is an ``evidence:<id>`` reference to a
    BYTES_VERIFIED register record whose declared temporal coverage contains
    the claimed interval [start, end].  An unbounded claim (missing start or
    end) never resolves — coverage cannot be demonstrated.
    """
    resolved: list[str] = []
    if not isinstance(citations, list) or not start or not end or start > end:
        return resolved
    for citation in citations:
        if not isinstance(citation, str):
            continue
        match = _CITATION_RE.fullmatch(citation.strip())
        if not match:
            continue
        evidence_id = match.group("evidence_id")
        record = verified.get(evidence_id)
        if record is None:
            continue
        coverage = record.get("coverage", {})
        cov_start = coverage.get("temporal_start")
        cov_end = coverage.get("temporal_end")
        if cov_start and cov_end and cov_start <= start and cov_end >= end:
            resolved.append(evidence_id)
    return sorted(set(resolved))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--register", required=True, type=Path,
                        help="evidence register JSON to validate")
    args = parser.parse_args()
    document = json.loads(args.register.read_text(encoding="utf-8"))
    problems = validate_register(document)
    if problems:
        for problem in problems:
            print(f"EVIDENCE_REGISTER_INVALID: {problem}")
        return 1
    print(f"EVIDENCE_REGISTER_OK: {args.register} "
          f"({len(document.get('records', []))} records, "
          f"{len(verified_records(document))} bytes-verified)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
