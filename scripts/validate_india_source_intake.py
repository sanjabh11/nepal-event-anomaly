"""Validate the bounded India Phase-0 source-intake packet and receipt.

This validator concerns only the approved NRSC inventory PDF. It does not
convert the atlas into event evidence, a canonical lake frame, observation
history, or a verified non-event cohort.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = Path("/Users/sanjayb/nepal-event-anomaly-evidence")
INTAKE_ROOT = EVIDENCE_ROOT / "india-phase0-source-intake"
DEFAULT_PACKET = ROOT / "docs/science/INDIA_PHASE0_SOURCE_INTAKE_V0.json"
DEFAULT_SUPPLEMENT = ROOT / "docs/science/INDIA_INVENTORY_REGISTRY_SUPPLEMENT_V0.json"
DEFAULT_REGISTRY = ROOT / "docs/science/INDIA_INVENTORY_REGISTRY_V1.json"
DEFAULT_RECEIPT = INTAKE_ROOT / "NRSC_GLA_IHR_SOURCE_RECEIPT_V0.json"
DEFAULT_PAYLOAD = INTAKE_ROOT / "IHR_GlacialLake_Atlas.pdf"
SCHEMA = "INDIA_PHASE0_SOURCE_RECEIPT_V0"
NRSC_URL = "https://www.nrsc.gov.in/nrscnew/assets/pdf/atlas/glaciallake/IHR_GlacialLake_Atlas.pdf"
NRSC_TERMS_URL = "https://www.nrsc.gov.in/nrscnew/termsConditions.php"
REQUIRED_SOURCE_DISPOSITIONS = {
    "CWC_GLWB_SEP_2024": "NOT_ACQUIRED_TERMS_UNRESOLVED",
    "CWC_LOKSABHA_AU883_2026": "NOT_ACQUIRED_ENDPOINT_DENIED",
    "ICIMOD_HMAGLOFDB_V130": "NOT_ACQUIRED_VERSION_DRIFT",
}
FORBIDDEN_INTAKE = {
    "ERA5 or ERA5-Land weather data",
    "IMERG, CHIRPS, or other satellite payloads",
    "bulk satellite imagery or DEM archives",
    "seismic waveforms",
    "forecast, warning, detector, causal, odds, or operational work",
}
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
_HEX64 = re.compile(r"[0-9a-f]{64}").fullmatch


class IntakeError(ValueError):
    """Raised when the source intake cannot be verified within its cap."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntakeError(f"{label} unreadable or invalid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise IntakeError(f"{label} root must be an object")
    return document


def _verify_sidecar(path: Path, expected: str | None = None) -> str:
    sidecar = Path(str(path) + ".sha256")
    if not path.is_file() or not sidecar.is_file():
        raise IntakeError(f"missing file or SHA-256 sidecar: {path.name}")
    fields = sidecar.read_text(encoding="utf-8").split()
    actual = sha256_file(path)
    if (not fields or not _HEX64(fields[0]) or fields[0] != actual
            or (len(fields) > 1 and fields[1] != path.name)):
        raise IntakeError(f"sidecar mismatch: {sidecar.name}")
    if expected is not None and actual != expected:
        raise IntakeError(f"bound digest mismatch: {path.name}")
    return actual


def validate_intake(packet_path: Path, supplement_path: Path,
                    registry_path: Path, receipt_path: Path,
                    payload_path: Path) -> dict[str, Any]:
    packet_path = Path(packet_path)
    supplement_path = Path(supplement_path)
    registry_path = Path(registry_path)
    receipt_path = Path(receipt_path)
    payload_path = Path(payload_path)
    packet_sha = _verify_sidecar(packet_path)
    supplement_sha = _verify_sidecar(supplement_path)
    packet = _load_object(packet_path, "source-intake packet")
    supplement = _load_object(supplement_path, "registry supplement")
    registry_sha = sha256_file(registry_path)

    if packet.get("schema") != "INDIA_PHASE0_SOURCE_INTAKE_V0" \
            or packet.get("version") != 0:
        raise IntakeError("unexpected source-intake packet schema/version")
    if packet.get("authority") != AUTHORITY_FLAGS:
        raise IntakeError("source-intake authority flags must all be false")
    scope = packet.get("scope")
    if not isinstance(scope, dict):
        raise IntakeError("source-intake scope must be an object")
    cap = scope.get("maximum_payload_bytes_total")
    per_source_cap = scope.get("maximum_payload_bytes_per_source")
    if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0
           for value in (cap, per_source_cap)):
        raise IntakeError("source-intake byte caps must be positive integers")
    if per_source_cap > cap:
        raise IntakeError("per-source cap cannot exceed total cap")
    if (not isinstance(scope.get("storage_root"), str)
            or Path(scope["storage_root"]).resolve()
            != payload_path.parent.resolve()):
        raise IntakeError("source-intake storage root differs from the payload parent")
    if (not isinstance(scope.get("forbidden"), list)
            or set(scope["forbidden"]) != FORBIDDEN_INTAKE
            or len(scope["forbidden"]) != len(FORBIDDEN_INTAKE)):
        raise IntakeError("forbidden acquisition list differs from the frozen scope")

    sources = packet.get("sources")
    if not isinstance(sources, list):
        raise IntakeError("source-intake sources must be a list")
    by_id = {row.get("id"): row for row in sources if isinstance(row, dict)}
    expected_source_ids = {
        "NRSC_GLA_IHR", "CWC_GLWB_SEP_2024",
        "CWC_LOKSABHA_AU883_2026", "ICIMOD_HMAGLOFDB_V130",
    }
    nrsc = by_id.get("NRSC_GLA_IHR")
    if (not isinstance(nrsc, dict) or len(by_id) != len(sources)
            or set(by_id) != expected_source_ids):
        raise IntakeError("source ids must exactly match the frozen four-source packet")
    if nrsc.get("acquisition_status") != "BYTES_VERIFIED":
        raise IntakeError("NRSC atlas must be marked BYTES_VERIFIED")
    if nrsc.get("row_level_records_extracted") is not False:
        raise IntakeError("atlas row extraction is outside this intake")
    if nrsc.get("payload_sha256") is None or not _HEX64(
            nrsc.get("payload_sha256", "")):
        raise IntakeError("NRSC payload SHA-256 must be pinned")
    actual_bytes = nrsc.get("actual_bytes")
    if (not isinstance(actual_bytes, int) or isinstance(actual_bytes, bool)
            or actual_bytes <= 0 or actual_bytes > per_source_cap
            or actual_bytes > cap):
        raise IntakeError("NRSC payload size exceeds or violates the approved cap")
    for source_id, row in by_id.items():
        if source_id == "NRSC_GLA_IHR":
            continue
        if row.get("disposition") != REQUIRED_SOURCE_DISPOSITIONS[source_id]:
            raise IntakeError(f"unapproved source acquisition in packet: {source_id}")
    if nrsc.get("url") != NRSC_URL or nrsc.get("terms_url") != NRSC_TERMS_URL:
        raise IntakeError("NRSC source and terms URLs differ from the pinned record")
    if nrsc.get("disposition") != "ACQUIRE_ONE_STATIC_PDF_WITHIN_CAP":
        raise IntakeError("NRSC disposition must remain one bounded static PDF")

    if supplement.get("schema") != "INDIA_INVENTORY_REGISTRY_SUPPLEMENT_V0" \
            or supplement.get("version") != 0:
        raise IntakeError("unexpected registry supplement schema/version")
    if supplement.get("claim_scope") != "research_only_phase0_inventory_intake":
        raise IntakeError("registry supplement has unexpected claim scope")
    if supplement.get("authority") != AUTHORITY_FLAGS:
        raise IntakeError("registry supplement authority flags must all be false")
    supersedes = supplement.get("supersedes_registry")
    if not isinstance(supersedes, dict) or supersedes.get("sha256") != registry_sha:
        raise IntakeError("registry supplement does not bind the prior registry bytes")
    packet_binding = supplement.get("source_intake_packet")
    if not isinstance(packet_binding, dict) or packet_binding.get("sha256") != packet_sha:
        raise IntakeError("registry supplement does not bind the intake packet bytes")
    unchanged = supplement.get("unchanged_source_dispositions")
    if not isinstance(unchanged, dict) or any(
            value.startswith("ACQUIRED") for value in unchanged.values()
            if isinstance(value, str)):
        raise IntakeError("unchanged source dispositions must not claim acquisition")
    if supplement.get("phase_state") != (
            "METADATA_AND_ONE_PINNED_INVENTORY_PDF_ONLY_NO_ROW_LEVEL_INGESTION"):
        raise IntakeError("registry supplement overstates the phase state")
    updates = supplement.get("source_updates")
    if not isinstance(updates, list) or len(updates) != 1:
        raise IntakeError("supplement must contain exactly one bounded source update")
    update = updates[0]
    if not isinstance(update, dict) or update.get("source_id") != "NRSC_GLA_IHR":
        raise IntakeError("supplement may only update the approved NRSC source")

    if _verify_sidecar(receipt_path, nrsc.get("receipt_sha256")) != nrsc.get(
            "receipt_sha256"):
        raise IntakeError("source receipt digest differs from the packet")
    receipt = _load_object(receipt_path, "source receipt")
    if receipt.get("schema") != SCHEMA or receipt.get("version") != 0:
        raise IntakeError("unexpected source receipt schema/version")
    if receipt.get("source_id") != "NRSC_GLA_IHR":
        raise IntakeError("source receipt must describe only NRSC_GLA_IHR")
    authorization = receipt.get("authorization")
    if not isinstance(authorization, dict) or authorization.get(
            "cryptographic_signature") is not False:
        raise IntakeError("receipt must disclose unsigned owner authorization")
    receipt_basis = authorization.get("basis")
    packet_basis = packet.get("authorization_basis")
    if (receipt_basis !=
            "Explicit in-thread owner approval of bounded India Phase-0 data intake"
            or packet_basis !=
            "Explicit in-thread owner approval for bounded Phase-0 data intake; this is not a cryptographic signature."):
        raise IntakeError("packet and receipt must identify bounded owner approval")
    request = receipt.get("request")
    if (not isinstance(request, dict) or request.get("method") != "GET"
            or request.get("url") != nrsc.get("url")):
        raise IntakeError("source receipt URL differs from the authorized NRSC URL")
    if not isinstance(receipt.get("terms"), dict) or receipt["terms"].get(
            "url") != nrsc.get("terms_url"):
        raise IntakeError("source receipt terms URL differs from packet")
    response = receipt.get("response")
    if not isinstance(response, dict):
        raise IntakeError("source receipt response must be an object")
    if response.get("http_status") != 200:
        raise IntakeError("NRSC response status must be 200")
    if (response.get("final_url") != NRSC_URL
            or response.get("redirects") != []
            or response.get("content_type") != "application/pdf"):
        raise IntakeError("NRSC response endpoint/content type differs from the pinned file")
    if response.get("response_sha256") != nrsc.get("payload_sha256"):
        raise IntakeError("receipt response digest differs from packet")
    if response.get("content_length_bytes") != actual_bytes:
        raise IntakeError("receipt response length differs from packet")
    retention = receipt.get("retention")
    if not isinstance(retention, dict) or retention.get(
            "path") != str(payload_path.resolve()):
        raise IntakeError("receipt retention path differs from supplied payload path")
    if (retention.get("file_mode_octal") != "0600"
            or retention.get("parent_directory_mode_octal") != "0700"
            or retention.get("maximum_total_intake_bytes") != cap
            or retention.get("maximum_per_source_bytes") != per_source_cap
            or retention.get("retained_outside_git") is not True):
        raise IntakeError("receipt retention limits or permissions differ from the approved scope")
    if payload_path.resolve().is_relative_to(ROOT.resolve()):
        raise IntakeError("source payload must remain outside the Git repository")
    if payload_path.stat().st_size != actual_bytes or sha256_file(
            payload_path) != nrsc["payload_sha256"]:
        raise IntakeError("retained NRSC payload size or digest mismatch")
    if stat.S_IMODE(payload_path.stat().st_mode) != 0o600:
        raise IntakeError("retained NRSC payload must have mode 0600")
    if stat.S_IMODE(payload_path.parent.stat().st_mode) != 0o700:
        raise IntakeError("NRSC source intake directory must have mode 0700")

    supplement_receipt = update.get("external_receipt")
    if (update.get("payload_sha256") != nrsc.get("payload_sha256")
            or update.get("payload_bytes") != actual_bytes
            or not isinstance(supplement_receipt, dict)
            or supplement_receipt.get("sha256") != nrsc.get("receipt_sha256")):
        raise IntakeError("packet, registry supplement, and receipt bindings differ")
    if update.get("local_payload_retained_outside_git") is not True:
        raise IntakeError("source payload must remain outside Git")
    if update.get("source_authenticity_cryptographically_attested") is not False:
        raise IntakeError("source authenticity boundary must remain disclosed")
    verification_boundary = receipt.get("verification_boundary")
    if not isinstance(verification_boundary, dict) or verification_boundary.get(
            "weather_satellite_dem_seismic_payloads_acquired") is not False:
        raise IntakeError("receipt claims an out-of-scope payload acquisition")

    return {
        "status": "SOURCE_INTAKE_OK",
        "source_id": "NRSC_GLA_IHR",
        "payload_bytes": actual_bytes,
        "payload_sha256": nrsc["payload_sha256"],
        "receipt_sha256": nrsc["receipt_sha256"],
        "packet_sha256": packet_sha,
        "supplement_sha256": supplement_sha,
        "row_level_records_extracted": False,
        "authority": AUTHORITY_FLAGS,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, default=DEFAULT_PACKET)
    parser.add_argument("--supplement", type=Path, default=DEFAULT_SUPPLEMENT)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--payload", type=Path, default=DEFAULT_PAYLOAD)
    args = parser.parse_args(argv)
    try:
        report = validate_intake(args.packet, args.supplement, args.registry,
                                 args.receipt, args.payload)
    except (OSError, IntakeError) as exc:
        print(f"SOURCE_INTAKE_BLOCKED: {exc}")
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
