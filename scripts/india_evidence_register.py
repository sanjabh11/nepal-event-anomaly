"""Validate the India Phase-0 evidence register.

Every record an adjudication or control claim cites must resolve to a
register entry.  A citation is a reference; a BYTES_VERIFIED record is
the only resolvable evidence — it requires:

- a pinned payload SHA-256 and declared byte size,
- a resolvable payload: either LOCAL bytes under a declared evidence
  root (verified on disk against the digest) or an EXTERNAL immutable
  retrieval receipt (URL, redirect chain, retrieval time, status,
  content type, response digest equal to the pinned digest),
- declared temporal coverage containing the claimed interval, and
- declared spatial coverage (structured country/basin/bbox metadata)
  containing the claimed location.

METADATA_VERIFIED records prove a source exists and what its terms are;
they cannot satisfy an evidence check.  UNRESOLVED records are
bookkeeping for references whose bytes have not yet been pinned.  The
register authorizes no acquisition, weather, satellite, seismic, or
operational work.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit


SCHEMA = "INDIA_EVIDENCE_REGISTER_V0"
LOCATOR_TYPES = {"URL", "DOI", "PATH", "DATASET_RECORD", "LOCAL_PATH_LABEL"}
VERIFICATION_STATES = {"UNRESOLVED", "METADATA_VERIFIED", "BYTES_VERIFIED"}
PAYLOAD_KINDS = {"LOCAL_PATH_LABEL", "EXTERNAL_RETRIEVAL_RECEIPT"}
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
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _norm(value: object) -> str:
    return str(value).strip() if isinstance(value, str) else ""


def _iso_date_or_none(value: object) -> bool:
    return value is None or (isinstance(value, str)
                             and bool(_ISO_DATE.fullmatch(value)))


def _valid_spatial(spatial: object, strict: bool) -> list[str]:
    """Structured coverage geography: countries required, basins/bbox
    optional; strict mode (BYTES_VERIFIED) demands nonempty countries."""
    problems: list[str] = []
    if not isinstance(spatial, dict):
        return ["coverage.spatial must be an object"]
    countries = spatial.get("countries")
    if not isinstance(countries, list) or any(
            not isinstance(c, str) or not c.strip() for c in countries):
        problems.append("coverage.spatial.countries must be a list of strings")
    elif strict and not countries:
        problems.append(
            "BYTES_VERIFIED requires declared country coverage")
    for field in ("basins",):
        value = spatial.get(field)
        if value is not None and (not isinstance(value, list) or any(
                not isinstance(v, str) or not v.strip() for v in value)):
            problems.append(f"coverage.spatial.{field} must be a list of strings")
    bbox = spatial.get("bbox")
    if bbox is not None and (not isinstance(bbox, list) or len(bbox) != 4
                             or any(not isinstance(v, (int, float))
                                    for v in bbox)):
        problems.append("coverage.spatial.bbox must be [w,s,e,n] numbers or null")
    if spatial.get("polygon_ref") is not None and not isinstance(
            spatial.get("polygon_ref"), str):
        problems.append("coverage.spatial.polygon_ref must be a string or null")
    return problems


def _valid_receipt(receipt: object, sha256: str) -> list[str]:
    """Immutable external retrieval receipt: proves bytes were pinned
    without retaining them in the public tree."""
    problems: list[str] = []
    if not isinstance(receipt, dict):
        return ["payload.receipt must be an object"]
    url = receipt.get("source_url")
    if not isinstance(url, str) or urlsplit(url).scheme != "https":
        problems.append("payload.receipt.source_url must be an HTTPS URL")
    if receipt.get("final_url") is not None and (
            not isinstance(receipt["final_url"], str)
            or urlsplit(receipt["final_url"]).scheme != "https"):
        problems.append("payload.receipt.final_url must be HTTPS or null")
    if not isinstance(receipt.get("retrieved_utc"), str) \
            or not receipt["retrieved_utc"].strip():
        problems.append("payload.receipt.retrieved_utc must be non-empty")
    if receipt.get("http_status") != 200:
        problems.append("payload.receipt.http_status must be 200")
    if not isinstance(receipt.get("content_type"), str) \
            or not receipt["content_type"].strip():
        problems.append("payload.receipt.content_type must be non-empty")
    response = receipt.get("response_sha256")
    if not (isinstance(response, str) and _HEX64.fullmatch(response)):
        problems.append("payload.receipt.response_sha256 must be 64 hex")
    elif response != sha256:
        problems.append(
            "payload.receipt.response_sha256 must equal the pinned digest")
    if receipt.get("request_params") is not None and not isinstance(
            receipt.get("request_params"), dict):
        problems.append("payload.receipt.request_params must be an object or null")
    if not isinstance(receipt.get("terms_reviewed"), str) \
            or not receipt["terms_reviewed"].strip():
        problems.append("payload.receipt.terms_reviewed must be non-empty")
    return problems


def _valid_payload(record: dict) -> list[str]:
    """Resolvable-payload contract for BYTES_VERIFIED records: a digest
    without resolvable bytes or an immutable receipt is not evidence."""
    problems: list[str] = []
    payload = record.get("payload")
    sha256 = record.get("sha256")
    if not isinstance(payload, dict):
        return ["BYTES_VERIFIED requires a payload object"]
    kind = payload.get("kind")
    if kind not in PAYLOAD_KINDS:
        return problems + [f"payload.kind must be one of {sorted(PAYLOAD_KINDS)}"]
    if not isinstance(payload.get("size_bytes"), int) \
            or payload["size_bytes"] < 0:
        problems.append("payload.size_bytes must be a non-negative integer")
    if kind == "LOCAL_PATH_LABEL":
        path = _norm(payload.get("path"))
        root = _norm(payload.get("evidence_root"))
        if not path or not root:
            problems.append(
                "LOCAL_PATH_LABEL payload requires path and evidence_root")
        elif Path(path).is_absolute() or ".." in Path(path).parts:
            problems.append("payload.path must stay under evidence_root")
        else:
            candidate = Path(root) / path
            if not candidate.is_file():
                problems.append(
                    f"payload bytes not resolvable: {candidate}")
            elif sha256_file(candidate) != sha256:
                problems.append(
                    f"payload digest mismatch: {candidate}")
            elif candidate.stat().st_size != payload.get("size_bytes"):
                problems.append(
                    f"payload size mismatch: {candidate}")
    else:
        problems.extend(_valid_receipt(payload.get("receipt"), sha256))
    return problems


def validate_register(document: object) -> list[str]:
    """Return all contract violations; an empty list means resolvable."""
    problems: list[str] = []
    if not isinstance(document, dict):
        return ["register must be an object"]
    if document.get("schema") != SCHEMA:
        problems.append(f"schema must be {SCHEMA}")
    if document.get("version") != 0:
        problems.append("version must be integer 0")
    if document.get("claim_scope") != "research_only_no_operational_authorization":
        problems.append("unexpected claim scope")
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
        if record.get("locator_type") not in LOCATOR_TYPES:
            problems.append(f"{prefix}.locator_type must be one of {sorted(LOCATOR_TYPES)}")
        if not _norm(record.get("locator")):
            problems.append(f"{prefix}.locator must be a non-empty string")
        if record.get("verification_state") not in VERIFICATION_STATES:
            problems.append(f"{prefix}.verification_state must be one of {sorted(VERIFICATION_STATES)}")
        digest = record.get("sha256")
        if digest is not None and not (isinstance(digest, str)
                                      and _HEX64.fullmatch(digest)):
            problems.append(f"{prefix}.sha256 must be 64 lowercase hex or null")
        coverage = record.get("coverage")
        if not isinstance(coverage, dict):
            problems.append(f"{prefix}.coverage must be an object")
            coverage = None
        else:
            if not _iso_date_or_none(coverage.get("temporal_start")) \
                    or not _iso_date_or_none(coverage.get("temporal_end")):
                problems.append(f"{prefix}.coverage dates must be ISO YYYY-MM-DD or null")
            if (coverage.get("temporal_start") and coverage.get("temporal_end")
                    and coverage["temporal_start"] > coverage["temporal_end"]):
                problems.append(f"{prefix}.coverage temporal_start after temporal_end")
            problems.extend(f"{prefix}.{p}" for p in _valid_spatial(
                coverage.get("spatial"),
                strict=record.get("verification_state") == "BYTES_VERIFIED"))
        if not isinstance(record.get("limitations"), list) or not record["limitations"]:
            problems.append(f"{prefix}.limitations must be a non-empty list")
        if not isinstance(record.get("access_terms"), str) or not record["access_terms"].strip():
            problems.append(f"{prefix}.access_terms must be non-empty")
        if record.get("verification_state") == "BYTES_VERIFIED":
            if not isinstance(digest, str) or not _HEX64.fullmatch(digest):
                problems.append(f"{prefix}: BYTES_VERIFIED requires a sha256 digest")
            if coverage is not None and not (
                    coverage.get("temporal_start") and coverage.get("temporal_end")):
                problems.append(f"{prefix}: BYTES_VERIFIED requires explicit temporal coverage")
            if isinstance(digest, str) and _HEX64.fullmatch(digest):
                problems.extend(f"{prefix}.{p}" for p in _valid_payload(record))
        elif record.get("payload") is not None:
            problems.append(
                f"{prefix}: payload objects require BYTES_VERIFIED state")
    return problems


def verified_records(document: dict) -> dict[str, dict]:
    """Index of BYTES_VERIFIED records only; nothing else is resolvable."""
    return {
        record["evidence_id"]: record
        for record in document.get("records", [])
        if isinstance(record, dict)
        and record.get("verification_state") == "BYTES_VERIFIED"
    }


def _spatial_contains(spatial: dict, country: str | None,
                      basin: str | None) -> bool:
    """Structured containment: a declared country or basin must appear
    in the record's coverage.  Declaring neither means no spatial claim
    can be verified — fail closed."""
    countries = {c.strip().casefold() for c in spatial.get("countries", [])
                 if isinstance(c, str)}
    basins = {b.strip().casefold() for b in spatial.get("basins") or []
              if isinstance(b, str)}
    if country and country.casefold() in countries:
        return True
    if basin and basin.casefold() in basins:
        return True
    return False


def resolved_evidence_ids(
        citations: object, verified: dict[str, dict],
        claim_start: str | None, claim_end: str | None,
        country: str | None = None, basin: str | None = None) -> list[str]:
    """Resolve citation refs to BYTES_VERIFIED records whose declared
    temporal and spatial coverage contains the claim.  Citations without
    an ``evidence:`` prefix are attribution strings, never resolvable.
    A claim outside declared coverage — temporally or spatially — has no
    evidence.
    """
    if not claim_start or not claim_end:
        return []
    refs = citations if isinstance(citations, list) else []
    resolved = []
    for ref in refs:
        if not isinstance(ref, str) or not ref.startswith("evidence:"):
            continue
        evidence_id = ref.split(":", 1)[1].strip()
        record = verified.get(evidence_id)
        if not record:
            continue
        coverage = record.get("coverage", {})
        if not isinstance(coverage, dict):
            continue
        start = coverage.get("temporal_start")
        end = coverage.get("temporal_end")
        if not start or not end or start > claim_start or end < claim_end:
            continue
        spatial = coverage.get("spatial")
        if not isinstance(spatial, dict):
            continue
        if not _spatial_contains(spatial, country, basin):
            continue
        resolved.append(evidence_id)
    return resolved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--register", required=True, type=Path)
    args = parser.parse_args(argv)
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
