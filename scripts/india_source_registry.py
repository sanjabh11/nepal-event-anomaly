"""Validate the India Phase-0 source registry (V1 successor).

The V1 registry supersedes the metadata-only V0 registry while retaining
it unchanged.  Each source entry pins the actual source version,
publication period, official URL, access terms, file format, CRS, and —
when bytes have been legitimately retained — the payload size and
SHA-256.  ``row_level_inventory_ingested`` stays false until an approved
row-level intake lands; nothing here authorizes acquisition, weather,
satellite, seismic, or operational work.

Reported counts from different products are bound to their own scope and
vintage and are never treated as a trend or a shared denominator.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlsplit


SCHEMA = "INDIA_INVENTORY_REGISTRY_V1"
SUPERSEDES = "docs/science/INDIA_INVENTORY_METADATA_V0.json"
OFFICIAL_HOSTS = (
    "nrsc.gov.in", "bhuvan.nrsc.gov.in", "cwc.gov.in",
    "icimod.org", "rds.icimod.org", "essd.copernicus.org",
    "sansad.in", "mowr.gov.in", "india.gov.in",
)
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
_REQUIRED_SOURCE_FIELDS = {
    "id", "publisher", "title", "source_version", "publication_or_period",
    "source_url", "source_access_status", "scope", "file_format",
    "access_terms", "reported_counts", "row_level_inventory_ingested",
    "local_payload_sha256", "source_bytes_retained",
    "retrieval_instructions", "interpretation_limit",
}


def _official_https_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    host = (parsed.hostname or "").casefold().rstrip(".")
    return parsed.scheme == "https" and any(
        host == allowed or host.endswith("." + allowed)
        for allowed in OFFICIAL_HOSTS
    )


def validate_registry(document: object) -> list[str]:
    """Return all contract violations; never authorize acquisition."""
    problems: list[str] = []
    if not isinstance(document, dict):
        return ["registry must be an object"]
    if document.get("schema") != SCHEMA:
        problems.append(f"schema must be {SCHEMA}")
    if document.get("version") != 1:
        problems.append("version must be integer 1")
    if document.get("claim_scope") != "research_only_phase0_registry":
        problems.append("claim_scope must remain registry scope")
    if document.get("authority") != AUTHORITY_FLAGS:
        problems.append("authority flags must be present and false")
    if document.get("supersedes") != SUPERSEDES:
        problems.append(f"registry must declare supersedes {SUPERSEDES}")

    acquisition = document.get("acquisition")
    expected_acquisition = {
        "policy": "REGISTRY_ONLY",
        "metadata_only_queries_run": True,
        "payload_requests_issued": False,
    }
    if not isinstance(acquisition, dict):
        problems.append("acquisition must be an object")
    else:
        for key, expected in expected_acquisition.items():
            if acquisition.get(key) != expected:
                problems.append(f"acquisition.{key} must be {expected!r}")

    sources = document.get("sources")
    if not isinstance(sources, list) or not sources:
        return problems + ["sources must be a non-empty list"]
    seen: set[str] = set()
    for index, source in enumerate(sources):
        prefix = f"sources[{index}]"
        if not isinstance(source, dict):
            problems.append(f"{prefix} must be an object")
            continue
        missing = sorted(_REQUIRED_SOURCE_FIELDS - set(source))
        problems.extend(f"{prefix} missing {field}" for field in missing)
        source_id = source.get("id")
        if not isinstance(source_id, str) or not source_id:
            problems.append(f"{prefix}.id must be a non-empty string")
        elif source_id in seen:
            problems.append(f"duplicate source id: {source_id}")
        else:
            seen.add(source_id)
        if not _official_https_url(source.get("source_url")):
            problems.append(f"{prefix}.source_url must be an official HTTPS URL")
        for field in ("source_version", "publication_or_period",
                      "file_format", "access_terms",
                      "retrieval_instructions", "interpretation_limit"):
            if not isinstance(source.get(field), str) or not source[field].strip():
                problems.append(f"{prefix}.{field} must be a non-empty string")
        if source.get("row_level_inventory_ingested") is not False:
            problems.append(
                f"{prefix}.row_level_inventory_ingested must be false")
        if source.get("source_bytes_retained") is not False:
            problems.append(f"{prefix}.source_bytes_retained must be false")
        digest = source.get("local_payload_sha256")
        if digest is not None and not (isinstance(digest, str)
                                      and _HEX64.fullmatch(digest)):
            problems.append(
                f"{prefix}.local_payload_sha256 must be 64 lowercase hex "
                "or null")
        if not isinstance(source.get("reported_counts"), dict):
            problems.append(f"{prefix}.reported_counts must be an object")
        if not isinstance(source.get("scope"), str) or not source["scope"]:
            problems.append(f"{prefix}.scope must be non-empty")
        # Distinct products never collapse into a shared denominator.
        if source.get("comparable_to") is not None and not isinstance(
                source.get("comparable_to"), list):
            problems.append(f"{prefix}.comparable_to must be a list or null")

    by_id = {source.get("id"): source for source in sources
             if isinstance(source, dict)}
    nrsc = by_id.get("NRSC_GLA_IHR")
    if isinstance(nrsc, dict):
        if nrsc.get("reported_counts", {}).get(
                "mapped_lakes_ge_0_25ha") != 28043:
            problems.append("NRSC mapped-lake count must be the pinned 28043")
        if nrsc.get("mapping_epoch") != "2016-2017":
            problems.append("NRSC mapping_epoch must be the pinned 2016-2017")
        if nrsc.get("minimum_lake_area_ha") != 0.25:
            problems.append("NRSC minimum_lake_area_ha must be 0.25")
    else:
        problems.append("NRSC_GLA_IHR source is required")
    cwc = by_id.get("CWC_GLWB_SEP_2024")
    if isinstance(cwc, dict):
        if cwc.get("reported_counts", {}).get(
                "monitored_glacial_lakes_and_water_bodies") != 902:
            problems.append("CWC monitored count must be the pinned 902")
        if cwc.get("publication_or_period") != "2024-09":
            problems.append(
                "CWC publication_or_period must be the pinned 2024-09")
    else:
        problems.append("CWC_GLWB_SEP_2024 source is required")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", required=True, type=Path)
    args = parser.parse_args(argv)
    document = json.loads(args.registry.read_text(encoding="utf-8"))
    problems = validate_registry(document)
    if problems:
        for problem in problems:
            print(f"SOURCE_REGISTRY_INVALID: {problem}")
        return 1
    print(f"SOURCE_REGISTRY_OK: {args.registry} "
          f"({len(document.get('sources', []))} sources)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
