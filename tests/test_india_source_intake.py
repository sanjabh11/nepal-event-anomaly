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

    receipt = json.loads(
        (intake.DEFAULT_RECEIPT).read_text(encoding="utf-8"))
    receipt["retention"]["path"] = str(payload)
    receipt["retention"]["file_mode_octal"] = "0600"
    receipt["retention"]["parent_directory_mode_octal"] = "0700"
    receipt["response"]["content_length_bytes"] = size
    receipt["response"]["response_sha256"] = digest
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


def test_valid_bounded_nrsc_intake_is_byte_verified(tmp_path):
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
