"""Tests for the byte-capped, inventory-only NRSC intake contract."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import validate_india_source_intake as intake  # noqa: E402


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, document: dict) -> None:
    path.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n",
                    encoding="utf-8")


def _write_sidecar(path: Path) -> None:
    Path(str(path) + ".sha256").write_text(
        f"{_digest(path)}  {path.name}\n", encoding="utf-8")


def _fixture(tmp_path: Path, *, payload_bytes: bytes = b"%PDF-1.7 fixture") -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    evidence = tmp_path / "evidence"
    evidence.mkdir(mode=0o700)
    os.chmod(evidence, 0o700)
    payload = evidence / "IHR_GlacialLake_Atlas.pdf"
    payload.write_bytes(payload_bytes)
    os.chmod(payload, 0o600)
    digest = _digest(payload)
    size = payload.stat().st_size

    packet = json.loads((ROOT / "docs/science/INDIA_PHASE0_SOURCE_INTAKE_V0.json")
                        .read_text(encoding="utf-8"))
    packet["scope"]["storage_root"] = str(evidence)
    nrsc = next(row for row in packet["sources"]
                if row["id"] == "NRSC_GLA_IHR")
    nrsc.update({
        "planned_bytes": size,
        "actual_bytes": size,
        "payload_sha256": digest,
        "receipt_path": "test/NRSC_GLA_IHR_SOURCE_RECEIPT_V0.json",
        "receipt_sha256": "0" * 64,
        "row_level_records_extracted": False,
    })

    # Keep the fixture hermetic: never read the host's external evidence root.
    # These fields mirror the receipt contract, not a real acquisition receipt.
    receipt = {
        "schema": intake.SCHEMA,
        "version": 0,
        "source_id": "NRSC_GLA_IHR",
        "authorization": {
            "basis": "Explicit in-thread owner approval of bounded India Phase-0 data intake",
            "cryptographic_signature": False,
        },
        "request": {"method": "GET", "url": nrsc["url"]},
        "terms": {"url": nrsc["terms_url"]},
        "response": {
            "http_status": 200,
            "final_url": nrsc["url"],
            "redirects": [],
            "content_type": "application/pdf",
            "content_length_bytes": size,
            "response_sha256": digest,
        },
        "retention": {
            "path": str(payload.resolve()),
            "file_mode_octal": "0600",
            "parent_directory_mode_octal": "0700",
            "maximum_total_intake_bytes": packet["scope"][
                "maximum_payload_bytes_total"],
            "maximum_per_source_bytes": packet["scope"][
                "maximum_payload_bytes_per_source"],
            "retained_outside_git": True,
        },
        "verification_boundary": {
            "weather_satellite_dem_seismic_payloads_acquired": False,
        },
    }
    receipt_path = evidence / "NRSC_GLA_IHR_SOURCE_RECEIPT_V0.json"
    _write_json(receipt_path, receipt)
    os.chmod(receipt_path, 0o600)
    _write_sidecar(receipt_path)
    receipt_sha = _digest(receipt_path)
    nrsc["receipt_sha256"] = receipt_sha
    packet_path = tmp_path / "INDIA_PHASE0_SOURCE_INTAKE_V0.json"
    _write_json(packet_path, packet)
    _write_sidecar(packet_path)
    packet_sha = _digest(packet_path)

    registry_path = tmp_path / "INDIA_INVENTORY_REGISTRY_V1.json"
    _write_json(registry_path, {"schema": "fixture-registry"})
    registry_sha = _digest(registry_path)
    supplement = json.loads(
        (ROOT / "docs/science/INDIA_INVENTORY_REGISTRY_SUPPLEMENT_V0.json")
        .read_text(encoding="utf-8"))
    supplement["supersedes_registry"]["sha256"] = registry_sha
    supplement["source_intake_packet"]["sha256"] = packet_sha
    update = supplement["source_updates"][0]
    update["payload_bytes"] = size
    update["payload_sha256"] = digest
    update["external_receipt"]["sha256"] = receipt_sha
    supplement_path = tmp_path / "INDIA_INVENTORY_REGISTRY_SUPPLEMENT_V0.json"
    _write_json(supplement_path, supplement)
    _write_sidecar(supplement_path)
    return {"packet": packet_path, "supplement": supplement_path,
            "registry": registry_path, "receipt": receipt_path,
            "payload": payload, "packet_doc": packet,
            "supplement_doc": supplement, "receipt_doc": receipt}


def test_valid_bounded_nrsc_intake_is_byte_verified(tmp_path, monkeypatch):
    # Prove test collection/execution does not depend on a developer-local receipt.
    monkeypatch.setattr(
        intake, "DEFAULT_RECEIPT", tmp_path / "missing-host-receipt.json")
    fixture = _fixture(tmp_path)
    result = intake.validate_intake(
        fixture["packet"], fixture["supplement"], fixture["registry"],
        fixture["receipt"], fixture["payload"])
    assert result["status"] == "SOURCE_INTAKE_OK"
    assert result["payload_sha256"] == _digest(fixture["payload"])
    assert result["row_level_records_extracted"] is False
    assert all(value is False for value in result["authority"].values())


def test_intake_fails_closed_when_actual_size_exceeds_cap(tmp_path):
    fixture = _fixture(tmp_path)
    doc = fixture["packet_doc"]
    row = next(source for source in doc["sources"]
               if source["id"] == "NRSC_GLA_IHR")
    row["actual_bytes"] = doc["scope"]["maximum_payload_bytes_total"] + 1
    _write_json(fixture["packet"], doc)
    _write_sidecar(fixture["packet"])
    with pytest.raises(intake.IntakeError, match="approved cap"):
        intake.validate_intake(
            fixture["packet"], fixture["supplement"], fixture["registry"],
            fixture["receipt"], fixture["payload"])


def test_intake_rejects_any_row_extraction_or_scope_expansion(tmp_path):
    fixture = _fixture(tmp_path)
    doc = fixture["packet_doc"]
    nrsc = next(source for source in doc["sources"]
                if source["id"] == "NRSC_GLA_IHR")
    nrsc["row_level_records_extracted"] = True
    _write_json(fixture["packet"], doc)
    _write_sidecar(fixture["packet"])
    with pytest.raises(intake.IntakeError, match="row extraction"):
        intake.validate_intake(
            fixture["packet"], fixture["supplement"], fixture["registry"],
            fixture["receipt"], fixture["payload"])


def test_intake_rejects_authority_flag_or_tampered_response(tmp_path):
    fixture = _fixture(tmp_path)
    doc = fixture["packet_doc"]
    doc["authority"]["satellite_bulk_authorized"] = True
    _write_json(fixture["packet"], doc)
    _write_sidecar(fixture["packet"])
    with pytest.raises(intake.IntakeError, match="authority flags"):
        intake.validate_intake(
            fixture["packet"], fixture["supplement"], fixture["registry"],
            fixture["receipt"], fixture["payload"])

    fixture = _fixture(tmp_path / "second")
    sidecar = Path(str(fixture["receipt"]) + ".sha256")
    sidecar.write_text("0" * 64 + " bad.json\n", encoding="utf-8")
    with pytest.raises(intake.IntakeError, match="sidecar mismatch"):
        intake.validate_intake(
            fixture["packet"], fixture["supplement"], fixture["registry"],
            fixture["receipt"], fixture["payload"])


def test_intake_rejects_same_bytes_from_unrecorded_path(tmp_path):
    fixture = _fixture(tmp_path)
    alternate_root = tmp_path / "alternate"
    alternate_root.mkdir(mode=0o700)
    os.chmod(alternate_root, 0o700)
    alternate = alternate_root / "same-bytes.pdf"
    alternate.write_bytes(fixture["payload"].read_bytes())
    os.chmod(alternate, 0o600)
    fixture["packet_doc"]["scope"]["storage_root"] = str(alternate_root)
    _write_json(fixture["packet"], fixture["packet_doc"])
    _write_sidecar(fixture["packet"])
    fixture["supplement_doc"]["source_intake_packet"]["sha256"] = _digest(
        fixture["packet"])
    _write_json(fixture["supplement"], fixture["supplement_doc"])
    _write_sidecar(fixture["supplement"])
    with pytest.raises(intake.IntakeError, match="retention path"):
        intake.validate_intake(
            fixture["packet"], fixture["supplement"], fixture["registry"],
            fixture["receipt"], alternate)


def _v2_fixture(tmp_path: Path) -> dict:
    """Hermetic V1->V2 chain: predecessor packet bytes + extraction
    artifact + sidecar, all inside tmp_path."""
    fixture = _fixture(tmp_path)
    evidence = fixture["payload"].parent
    packet_doc = fixture["packet_doc"]
    # The supplement binds the original V0 packet bytes; the fixture's
    # V0-named file must keep them, so write the chain as separate files.
    v0_bytes = fixture["packet"].read_bytes()
    v1_path = tmp_path / "INDIA_PHASE0_SOURCE_INTAKE_V1.json"
    _write_json(v1_path, dict(packet_doc, schema="INDIA_PHASE0_SOURCE_INTAKE_V1",
                            version=1))
    v2_path = tmp_path / "INDIA_PHASE0_SOURCE_INTAKE_V2.json"
    fixture["packet"] = v2_path
    nrsc = next(s for s in packet_doc["sources"] if s["id"] == "NRSC_GLA_IHR")
    t68_records = [{
        "serial_no": i,
        "glacial_lake_id": f"01 42A01 {i:05d}",
        "glacial_lake_id_compact": f"0142A01{i:05d}",
        "latitude": 36.0, "longitude": 73.0,
        "subbasin": "Test", "gl_type": "O",
        "area_ha": 15.0, "elevation_m": 4000,
        "source_table": "table_68_ge10ha",
    } for i in range(1, 2432)]
    # Printed-serial defect rows: duplicated serials, distinct lake IDs,
    # below the stated >=10 ha table threshold.
    t68_records += [
        {"serial_no": 2001, "glacial_lake_id": "01 42B02 99998",
         "glacial_lake_id_compact": "0142B0299998", "latitude": 36.0,
         "longitude": 73.0, "subbasin": "Test", "gl_type": "O",
         "area_ha": 6.75, "elevation_m": 4000,
         "source_table": "table_68_ge10ha"},
        {"serial_no": 2002, "glacial_lake_id": "01 42B02 99999",
         "glacial_lake_id_compact": "0142B0299999", "latitude": 36.0,
         "longitude": 73.0, "subbasin": "Test", "gl_type": "O",
         "area_ha": 5.29, "elevation_m": 4000,
         "source_table": "table_68_ge10ha"},
    ]
    t69_records = [{
        "serial_no": i,
        "glacial_lake_id": f"01 42A01 {i:05d}",
        "glacial_lake_id_compact": f"0142A01{i:05d}",
        "latitude": 36.0, "longitude": 73.0,
        "subbasin": "Test", "gl_type": "O",
        "area_ha": 55.0, "elevation_m": 4100,
        "source_table": "table_69_ge50ha",
    } for i in range(1, 300)]
    extraction = {
        "schema": "NRSC_ATLAS_TABLE_EXTRACTION_V0",
        "extraction": {
            "source_pdf_sha256": nrsc["payload_sha256"],
            "tables": {"table_68_ge10ha": 2433, "table_69_ge50ha": 299},
            "anomalies": [{"kind": "PRINTED_SERIAL_DUPLICATE",
                           "serials": [2001, 2002]}],
        },
        "records": t68_records + t69_records,
    }
    art_path = evidence / "NRSC_GLA_IHR_TABLE_EXTRACTION_V0.json"
    _write_json(art_path, extraction)
    _write_sidecar(art_path)
    packet_doc["schema"] = "INDIA_PHASE0_SOURCE_INTAKE_V2"
    packet_doc["version"] = 2
    packet_doc["supersedes"] = v1_path.name
    packet_doc["supersedes_sha256"] = _digest(v1_path)
    nrsc["row_level_records_extracted"] = True
    nrsc["extraction"] = {
        "artifact": str(art_path.relative_to(intake.EVIDENCE_ROOT))
        if str(art_path).startswith(str(intake.EVIDENCE_ROOT))
        else str(art_path),
        "artifact_sha256": _digest(art_path),
        "rows": {"table_68_ge10ha": 2433, "table_69_ge50ha": 299},
    }
    nrsc["interpretation_limit"] = "Bounded subset extracted."
    nrsc["count_reconciliation"] = {
        "table_68_declared_rows": 2431,
        "table_68_extracted_rows": 2433,
        "table_68_valid_ge_10ha_rows": 2431,
        "table_69_extracted_rows": 299,
    }
    _write_json(v2_path, packet_doc)
    _write_sidecar(v2_path)
    return fixture


def test_v2_packet_with_extraction_binding_validates(tmp_path, monkeypatch):
    fixture = _v2_fixture(tmp_path)
    # Point the evidence-root lookup at the hermetic fixture root.
    monkeypatch.setattr(intake, "EVIDENCE_ROOT",
                        fixture["payload"].parent)
    nrsc = next(s for s in fixture["packet_doc"]["sources"]
                if s["id"] == "NRSC_GLA_IHR")
    nrsc["extraction"]["artifact"] = "NRSC_GLA_IHR_TABLE_EXTRACTION_V0.json"
    _write_json(fixture["packet"], fixture["packet_doc"])
    _write_sidecar(fixture["packet"])
    result = intake.validate_intake(
        fixture["packet"], fixture["supplement"], fixture["registry"],
        fixture["receipt"], fixture["payload"])
    assert result["status"] == "SOURCE_INTAKE_OK"
    assert result["row_level_records_extracted"] is True


def test_v2_rejects_stale_no_rows_wording(tmp_path, monkeypatch):
    fixture = _v2_fixture(tmp_path)
    monkeypatch.setattr(intake, "EVIDENCE_ROOT",
                        fixture["payload"].parent)
    nrsc = next(s for s in fixture["packet_doc"]["sources"]
                if s["id"] == "NRSC_GLA_IHR")
    nrsc["extraction"]["artifact"] = "NRSC_GLA_IHR_TABLE_EXTRACTION_V0.json"
    nrsc["interpretation_limit"] = "No rows are extracted."
    _write_json(fixture["packet"], fixture["packet_doc"])
    _write_sidecar(fixture["packet"])
    with pytest.raises(intake.IntakeError, match="stale"):
        intake.validate_intake(
            fixture["packet"], fixture["supplement"], fixture["registry"],
            fixture["receipt"], fixture["payload"])


def test_v2_rejects_wrong_count_reconciliation(tmp_path, monkeypatch):
    fixture = _v2_fixture(tmp_path)
    monkeypatch.setattr(intake, "EVIDENCE_ROOT",
                        fixture["payload"].parent)
    nrsc = next(s for s in fixture["packet_doc"]["sources"]
                if s["id"] == "NRSC_GLA_IHR")
    nrsc["extraction"]["artifact"] = "NRSC_GLA_IHR_TABLE_EXTRACTION_V0.json"
    nrsc["count_reconciliation"]["table_68_extracted_rows"] = 2431
    _write_json(fixture["packet"], fixture["packet_doc"])
    _write_sidecar(fixture["packet"])
    with pytest.raises(intake.IntakeError, match="reconciliation"):
        intake.validate_intake(
            fixture["packet"], fixture["supplement"], fixture["registry"],
            fixture["receipt"], fixture["payload"])


_EVIDENCE_PRESENT = Path(
    "/Users/sanjayb/nepal-event-anomaly-evidence").is_dir()


@pytest.mark.skipif(not _EVIDENCE_PRESENT,
                    reason="external evidence root unavailable")
def test_live_v3_packet_validates_with_derived_artifacts(tmp_path):
    packet = ROOT / "docs/science/INDIA_PHASE0_SOURCE_INTAKE_V3.json"
    supplement = ROOT / "docs/science/INDIA_INVENTORY_REGISTRY_SUPPLEMENT_V0.json"
    registry = ROOT / "docs/science/INDIA_INVENTORY_REGISTRY_V3.json"
    ev = intake.EVIDENCE_ROOT / "india-phase0-source-intake"
    result = intake.validate_intake(
        packet, supplement, registry,
        ev / "NRSC_GLA_IHR_SOURCE_RECEIPT_V0.json",
        ev / "IHR_GlacialLake_Atlas.pdf")
    assert result["status"] == "SOURCE_INTAKE_OK"
    assert result["row_level_records_extracted"] is True
