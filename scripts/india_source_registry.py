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
import hashlib
from pathlib import Path
from urllib.parse import urlsplit


SCHEMA = "INDIA_INVENTORY_REGISTRY_V1"
SCHEMA_V2 = "INDIA_INVENTORY_REGISTRY_V2"
SCHEMA_V3 = "INDIA_INVENTORY_REGISTRY_V3"
SCHEMA_V4 = "INDIA_INVENTORY_REGISTRY_V4"
EVIDENCE_ROOT = Path("/Users/sanjayb/nepal-event-anomaly-evidence")
SUPERSEDES = "docs/science/INDIA_INVENTORY_METADATA_V0.json"
SUPERSEDES_V2 = "docs/science/INDIA_INVENTORY_REGISTRY_V1.json"
SUPERSEDES_V3 = "docs/science/INDIA_INVENTORY_REGISTRY_V2.json"
SUPERSEDES_V4 = "docs/science/INDIA_INVENTORY_REGISTRY_V3.json"
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


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
_REQUIRED_SOURCE_FIELDS = {
    "id", "publisher", "title", "source_version", "publication_or_period",
    "source_url", "source_access_status", "scope", "file_format",
    "access_terms", "reported_counts", "row_level_inventory_ingested",
    "local_payload_sha256", "source_bytes_retained",
    "retrieval_instructions", "interpretation_limit", "retrieval_receipt",
}
_RECEIPT_METHODS = {"HEAD", "GET_METADATA_ONLY"}


def _valid_retrieval_receipt(receipt: object, prefix: str) -> list[str]:
    """Metadata-only retrieval receipt: proves the official endpoint was
    reached and that zero payload bytes were acquired.  Absent while a
    source has never been contacted; required once retrieval runs."""
    problems: list[str] = []
    if receipt is None:
        return problems  # pre-retrieval state is honest
    if not isinstance(receipt, dict):
        return [f"{prefix}.retrieval_receipt must be an object or null"]
    for field in ("requested_url", "retrieved_utc", "request_method",
                  "content_type", "terms_reviewed"):
        if not isinstance(receipt.get(field), str) or not receipt[field].strip():
            problems.append(
                f"{prefix}.retrieval_receipt.{field} must be non-empty")
    for field in ("requested_url", "final_url"):
        url = receipt.get(field)
        if url is not None and (not isinstance(url, str)
                                or urlsplit(url).scheme != "https"):
            problems.append(
                f"{prefix}.retrieval_receipt.{field} must be HTTPS or null")
    if not isinstance(receipt.get("http_status"), int) \
            or not 200 <= receipt["http_status"] < 400:
        problems.append(f"{prefix}.retrieval_receipt.http_status must be "
                        "a successful HTTP status (<400)")
    if receipt.get("request_method") not in _RECEIPT_METHODS:
        problems.append(
            f"{prefix}.retrieval_receipt.request_method must be HEAD or "
            "GET_METADATA_ONLY")
    if receipt.get("bytes_retained") is not False:
        problems.append(
            f"{prefix}.retrieval_receipt.bytes_retained must be false")
    digest = receipt.get("response_sha256")
    if digest is not None and not (isinstance(digest, str)
                                   and _HEX64.fullmatch(digest)):
        problems.append(
            f"{prefix}.retrieval_receipt.response_sha256 must be 64 hex "
            "or null")
    if receipt.get("redirects") is not None and not isinstance(
            receipt.get("redirects"), list):
        problems.append(
            f"{prefix}.retrieval_receipt.redirects must be a list or null")
    return problems


def _official_https_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    host = (parsed.hostname or "").casefold().rstrip(".")
    return parsed.scheme == "https" and any(
        host == allowed or host.endswith("." + allowed)
        for allowed in OFFICIAL_HOSTS
    )


def _v4_hmaglofdb_crosschecks(source: dict) -> list[str]:
    """V4: the HMAGLOFDB entry's declared digests must resolve to the
    retained archive, its inner CSV member and the derived crosswalk."""
    problems: list[str] = []
    sd = source.get("status_detail")
    if not isinstance(sd, dict):
        return ["ICIMOD_HMAGLOFDB_V130: V4 requires status_detail"]
    retain = sd.get("source_pdf_retained")
    if not isinstance(retain, dict) or retain.get("retained") is not True:
        problems.append(
            "ICIMOD_HMAGLOFDB_V130 must record retained archive bytes")
    else:
        rel = retain.get("relpath")
        archive = EVIDENCE_ROOT / str(rel or "")
        if not archive.is_file():
            problems.append(f"HMAGLOFDB archive path absent: {rel}")
        else:
            if sha256_file(archive) != retain.get("sha256"):
                problems.append("HMAGLOFDB archive sha256 differs from bytes")
            if archive.stat().st_size != retain.get("bytes"):
                problems.append("HMAGLOFDB archive size differs")
        member_sha = retain.get("inner_member_sha256")
        csv_file = (EVIDENCE_ROOT / "p5-glof-2026-09-19" / "glof-events"
                    / "HMAGLOFDB.csv")
        if not csv_file.is_file():
            problems.append("HMAGLOFDB working CSV absent")
        elif sha256_file(csv_file) != member_sha:
            problems.append("HMAGLOFDB inner member sha256 differs from CSV")
    be = sd.get("bounded_extraction")
    if not isinstance(be, dict) or be.get("extracted") is not True:
        problems.append(
            "ICIMOD_HMAGLOFDB_V130 must record bounded_extraction")
    else:
        rel = be.get("artifact")
        art_file = EVIDENCE_ROOT / str(rel or "")
        if not art_file.is_file():
            problems.append(f"crosswalk artifact absent: {rel}")
        else:
            if sha256_file(art_file) != be.get("artifact_sha256"):
                problems.append(
                    "crosswalk artifact_sha256 differs from bytes")
            else:
                try:
                    doc = json.loads(art_file.read_text(encoding="utf-8"))
                    rows = doc.get("summary", {}).get("n_total_rows")
                    if rows != be.get("rows"):
                        problems.append(
                            "crosswalk rows differ from artifact summary")
                except Exception:
                    problems.append("crosswalk artifact unreadable")
    return problems


def _v3_nrsc_crosschecks(sd: dict) -> list[str]:
    """Cross-validate V3 status_detail against the retained evidence
    bytes themselves (PDF digest, extraction artifact digest and row
    counts), not just field syntax."""
    problems: list[str] = []
    pdf = sd.get("source_pdf_retained")
    if not isinstance(pdf, dict) or pdf.get("retained") is not True:
        problems.append("NRSC status_detail must record source_pdf_retained")
    else:
        rel = pdf.get("relpath")
        digest = pdf.get("sha256")
        size = pdf.get("bytes")
        pdf_file = EVIDENCE_ROOT / str(rel or "")
        if not pdf_file.is_file():
            problems.append(f"source_pdf_retained path absent: {rel}")
        elif sha256_file(pdf_file) != digest:
            problems.append("source_pdf_retained sha256 differs from bytes")
        elif pdf_file.stat().st_size != size:
            problems.append("source_pdf_retained bytes differs from size")
    be = sd.get("bounded_extraction")
    if not isinstance(be, dict) or be.get("extracted") is not True:
        problems.append("NRSC status_detail must record bounded_extraction")
    else:
        rel = be.get("artifact")
        art_sha = be.get("artifact_sha256")
        tables = be.get("tables")
        art_file = EVIDENCE_ROOT / str(rel or "")
        if not art_file.is_file():
            problems.append(f"bounded_extraction artifact absent: {rel}")
        elif sha256_file(art_file) != art_sha:
            problems.append(
                "bounded_extraction artifact_sha256 differs from bytes")
        else:
            try:
                art_doc = json.loads(art_file.read_text(encoding="utf-8"))
                recs = art_doc.get("records", [])
                t68 = sum(1 for r in recs
                          if r.get("source_table") == "table_68_ge10ha")
                t69 = sum(1 for r in recs
                          if r.get("source_table") == "table_69_ge50ha")
                if (tables.get("table_68_ge10ha") != t68
                        or tables.get("table_69_ge50ha") != t69):
                    problems.append(
                        "bounded_extraction tables differ from artifact rows")
            except Exception:
                problems.append("bounded_extraction artifact unreadable")
    return problems


def validate_registry(document: object) -> list[str]:
    """Return all contract violations; never authorize acquisition."""
    problems: list[str] = []
    if not isinstance(document, dict):
        return ["registry must be an object"]
    is_v1 = (document.get("schema") == SCHEMA
             and document.get("version") == 1)
    is_v2 = (document.get("schema") == SCHEMA_V2
             and document.get("version") == 2)
    is_v3 = (document.get("schema") == SCHEMA_V3
             and document.get("version") == 3)
    is_v4 = (document.get("schema") == SCHEMA_V4
             and document.get("version") == 4)
    if not (is_v1 or is_v2 or is_v3 or is_v4):
        problems.append(
            f"schema must be {SCHEMA} (v1), {SCHEMA_V2} (v2), "
            f"{SCHEMA_V3} (v3) or {SCHEMA_V4} (v4)")
    if document.get("claim_scope") != "research_only_phase0_registry":
        problems.append("claim_scope must remain registry scope")
    if document.get("authority") != AUTHORITY_FLAGS:
        problems.append("authority flags must be present and false")
    if is_v1 and document.get("supersedes") != SUPERSEDES:
        problems.append(f"registry must declare supersedes {SUPERSEDES}")
    if is_v2:
        if document.get("supersedes") != SUPERSEDES_V2:
            problems.append(f"registry V2 must declare supersedes {SUPERSEDES_V2}")
        v1_file = (Path(__file__).resolve().parents[1]
                   / "docs" / "science" / "INDIA_INVENTORY_REGISTRY_V1.json")
        if v1_file.is_file():
            if document.get("supersedes_sha256") != sha256_file(v1_file):
                problems.append("registry V2 supersedes_sha256 must bind V1 bytes")
        else:
            problems.append("cannot verify registry V2 supersedes: V1 file absent")
        nrsc_v2 = [x for x in document.get("sources", [])
                   if isinstance(x, dict) and x.get("id") == "NRSC_GLA_IHR"]
        if nrsc_v2:
            be = nrsc_v2[0].get("bounded_extraction")
            if not isinstance(be, dict):
                problems.append("V2 NRSC entry must carry bounded_extraction")
            else:
                art = be.get("artifact_sha256")
                if not (isinstance(art, str) and _HEX64.fullmatch(art)):
                    problems.append("bounded_extraction.artifact_sha256 must be 64 hex")
                if be.get("full_inventory_ingested") is not False:
                    problems.append("bounded_extraction must not claim full inventory ingestion")
    if is_v3:
        if document.get("supersedes") != SUPERSEDES_V3:
            problems.append(
                f"registry V3 must declare supersedes {SUPERSEDES_V3}")
        v2_file = (Path(__file__).resolve().parents[1]
                   / "docs" / "science" / "INDIA_INVENTORY_REGISTRY_V2.json")
        if v2_file.is_file():
            if document.get("supersedes_sha256") != sha256_file(v2_file):
                problems.append(
                    "registry V3 supersedes_sha256 must bind V2 bytes")
        else:
            problems.append(
                "cannot verify registry V3 supersedes: V2 file absent")
        for source in document.get("sources", []):
            if not isinstance(source, dict):
                continue
            sd = source.get("status_detail")
            prefix_id = source.get("id", "?")
            if not isinstance(sd, dict):
                problems.append(f"{prefix_id}: V3 requires status_detail")
                continue
            if sd.get("full_inventory_ingested") is not False:
                problems.append(
                    f"{prefix_id}: status_detail.full_inventory_ingested "
                    "must be false")
        nrsc_v3 = [x for x in document.get("sources", [])
                   if isinstance(x, dict) and x.get("id") == "NRSC_GLA_IHR"]
        if nrsc_v3:
            sd = nrsc_v3[0].get("status_detail")
            if isinstance(sd, dict):
                problems.extend(_v3_nrsc_crosschecks(sd))
    # Cross-field retention invariant (V4+ corrected contract): retained
    # payload detail and the top-level flag must never contradict each
    # other. V3's ambiguous wording predates this rule and is superseded.
    for source in document.get("sources", []):
        if not isinstance(source, dict):
            continue
        sd = source.get("status_detail")
        if is_v4 and isinstance(sd, dict):
            retained_detail = (
                isinstance(sd.get("source_pdf_retained"), dict)
                and sd["source_pdf_retained"].get("retained") is True)
            if retained_detail and source.get("source_bytes_retained") \
                    is not True:
                problems.append(
                    f"{source.get('id', '?')}: status_detail says bytes "
                    "retained but source_bytes_retained is not true")
    if is_v4:
        if document.get("supersedes") != SUPERSEDES_V4:
            problems.append(
                f"registry V4 must declare supersedes {SUPERSEDES_V4}")
        v3_file = (Path(__file__).resolve().parents[1]
                   / "docs" / "science" / "INDIA_INVENTORY_REGISTRY_V3.json")
        if v3_file.is_file():
            if document.get("supersedes_sha256") != sha256_file(v3_file):
                problems.append(
                    "registry V4 supersedes_sha256 must bind V3 bytes")
        else:
            problems.append(
                "cannot verify registry V4 supersedes: V3 file absent")
        for source in document.get("sources", []):
            if not isinstance(source, dict):
                continue
            sd = source.get("status_detail")
            prefix_id = source.get("id", "?")
            if not isinstance(sd, dict):
                problems.append(f"{prefix_id}: V4 requires status_detail")
                continue
            if sd.get("full_inventory_ingested") is not False:
                problems.append(
                    f"{prefix_id}: status_detail.full_inventory_ingested "
                    "must be false")
        nrsc_v4 = [x for x in document.get("sources", [])
                   if isinstance(x, dict) and x.get("id") == "NRSC_GLA_IHR"]
        if nrsc_v4:
            sd = nrsc_v4[0].get("status_detail")
            if isinstance(sd, dict):
                problems.extend(_v3_nrsc_crosschecks(sd))
        hma = [x for x in document.get("sources", [])
               if isinstance(x, dict)
               and x.get("id") == "ICIMOD_HMAGLOFDB_V130"]
        if hma:
            problems.extend(_v4_hmaglofdb_crosschecks(hma[0]))

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
        if not is_v4 and source.get("source_bytes_retained") is not False:
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
        problems.extend(
            _valid_retrieval_receipt(source.get("retrieval_receipt"), prefix))

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
