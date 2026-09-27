"""Adversarial contract tests for the India evidence register."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import india_evidence_register as er


def _receipt(**kw):
    base = {
        "source_url": "https://example.org/ev.bin",
        "final_url": "https://example.org/ev.bin",
        "retrieved_utc": "2026-09-27T00:00:00Z", "http_status": 200,
        "content_type": "application/octet-stream",
        "response_sha256": "b" * 64, "request_params": None,
        "terms_reviewed": "fixture terms",
    }
    base.update(kw)
    return base


def _record(**kw):
    base = {
        "evidence_id": "EV:A", "source_name": "src", "source_version": "1",
        "locator_type": "URL", "locator": "https://example.org/x",
        "sha256": "b" * 64,
        "payload": {"kind": "EXTERNAL_RETRIEVAL_RECEIPT",
                    "size_bytes": 1234, "receipt": _receipt()},
        "verification_state": "BYTES_VERIFIED",
        "access_terms": "CC BY 4.0",
        "coverage": {"temporal_start": "2000-01-01",
                     "temporal_end": "2020-12-31",
                     "spatial": {"countries": ["India"],
                                 "basins": ["Ganga"], "bbox": None,
                                 "polygon_ref": None},
                     "spatial_label": "test"},
        "limitations": ["fixture"],
    }
    base.update(kw)
    return base


def _doc(records):
    return {"schema": er.SCHEMA, "version": 0,
            "claim_scope": "research_only_no_operational_authorization",
            "authority": dict(er.AUTHORITY_FLAGS), "register_state": "OPEN",
            "records": records}


def test_valid_bytes_verified_record_passes():
    doc = _doc([_record()])
    assert er.validate_register(doc) == []
    assert "EV:A" in er.verified_records(doc)


@pytest.mark.parametrize("mutator", [
    lambda d: d.__setitem__("schema", "WRONG"),
    lambda d: d.__setitem__("version", 1),
    lambda d: d["authority"].__setitem__("forecast_authorized", True),
])
def test_doc_level_escalation_rejected(mutator):
    doc = _doc([_record()])
    mutator(doc)
    assert er.validate_register(doc)


@pytest.mark.parametrize("mutator,match", [
    (lambda r: r.__setitem__("sha256", None), "requires a sha256"),
    (lambda r: r.__setitem__("sha256", "zz"), "64 lowercase hex"),
    (lambda r: r["coverage"].__setitem__("temporal_start", None),
     "explicit temporal coverage"),
    (lambda r: r["coverage"].__setitem__("spatial", "India"),
     "spatial must be an object"),
    (lambda r: r["coverage"]["spatial"].__setitem__("countries", []),
     "declared country coverage"),
    (lambda r: r.__setitem__("verification_state", "METADATA_VERIFIED"),
     "payload objects require BYTES_VERIFIED"),
    (lambda r: r.__setitem__("payload", None), "requires a payload object"),
    (lambda r: r.__setitem__("verification_state", "UNRESOLVED"),
     "require BYTES_VERIFIED"),
    (lambda r: r.__setitem__("locator_type", "FTP"), "locator_type"),
    (lambda r: r.__setitem__("limitations", []), "limitations"),
    (lambda r: r["coverage"].__setitem__("temporal_start", "2000-1-1"),
     "ISO"),
])
def test_record_contract_rejected(mutator, match):
    record = _record()
    mutator(record)
    problems = er.validate_register(_doc([record]))
    assert any(match in p for p in problems), problems


def test_digest_valid_but_nonexistent_local_payload_fails(tmp_path):
    # A syntactically valid digest pointing at bytes that do not exist
    # must fail — the whole point of resolvable-evidence checks.
    record = _record(payload={
        "kind": "LOCAL_PATH_LABEL", "size_bytes": 10,
        "evidence_root": str(tmp_path), "path": "missing.bin"})
    problems = er.validate_register(_doc([record]))
    assert any("not resolvable" in p for p in problems)


def test_local_payload_verified_against_real_bytes(tmp_path):
    payload_file = tmp_path / "ev.bin"
    payload_file.write_bytes(b"real evidence bytes")
    digest = hashlib.sha256(payload_file.read_bytes()).hexdigest()
    record = _record(
        sha256=digest,
        payload={"kind": "LOCAL_PATH_LABEL",
                 "size_bytes": len(payload_file.read_bytes()),
                 "evidence_root": str(tmp_path), "path": "ev.bin"})
    assert er.validate_register(_doc([record])) == []
    # Wrong digest fails.
    record["payload"]["size_bytes"] = 999999
    assert any("digest mismatch" in p or "size mismatch" in p
               for p in er.validate_register(_doc([record])))
    record["payload"]["size_bytes"] = len(payload_file.read_bytes())
    payload_file.write_bytes(b"tampered")
    assert any("digest mismatch" in p
               for p in er.validate_register(_doc([record])))


def test_altered_external_receipt_fails():
    record = _record()
    record["payload"]["receipt"]["response_sha256"] = "c" * 64
    assert any("equal the pinned digest" in p
               for p in er.validate_register(_doc([record])))
    record = _record()
    record["payload"]["receipt"]["http_status"] = 404
    assert any("http_status" in p for p in er.validate_register(_doc([record])))
    record = _record()
    record["payload"]["receipt"]["source_url"] = "http://insecure.example/x"
    assert any("HTTPS" in p for p in er.validate_register(_doc([record])))


def test_duplicate_evidence_ids_rejected():
    assert any("duplicate evidence_id" in p
               for p in er.validate_register(_doc([_record(), _record()])))


def test_resolution_requires_verified_covering_spatial_interval():
    verified = er.verified_records(_doc([_record()]))
    good = er.resolved_evidence_ids(["evidence:EV:A"], verified,
                                    "2005-06-01", "2005-06-30",
                                    country="India", basin="Ganga")
    assert good == ["EV:A"]
    # Basin match alone resolves (basin inside declared coverage).
    assert er.resolved_evidence_ids(["evidence:EV:A"], verified,
                                    "2005-06-01", "2005-06-30",
                                    country=None, basin="ganga") == ["EV:A"]
    # Wrong-country evidence does not resolve.
    assert er.resolved_evidence_ids(["evidence:EV:A"], verified,
                                    "2005-06-01", "2005-06-30",
                                    country="Nepal", basin=None) == []
    # No spatial context at all fails closed.
    assert er.resolved_evidence_ids(["evidence:EV:A"], verified,
                                    "2005-06-01", "2005-06-30") == []
    # Free-text or foreign citations never resolve.
    assert er.resolved_evidence_ids(["paper:1"], verified,
                                    "2005-06-01", "2005-06-30",
                                    country="India") == []
    # Unknown ids never resolve.
    assert er.resolved_evidence_ids(["evidence:EV:MISSING"], verified,
                                    "2005-06-01", "2005-06-30",
                                    country="India") == []
    # Claims outside declared coverage never resolve.
    assert er.resolved_evidence_ids(["evidence:EV:A"], verified,
                                    "2021-01-01", "2021-01-02",
                                    country="India") == []
    # Unbounded claims never resolve.
    assert er.resolved_evidence_ids(["evidence:EV:A"], verified,
                                    None, "2005-06-30", country="India") == []
    # Metadata-verified records never satisfy.
    meta = er.verified_records(_doc([_record(verification_state="METADATA_VERIFIED",
                                           sha256=None, payload=None)]))
    assert meta == {}
    assert er.resolved_evidence_ids(["evidence:EV:A"], meta,
                                    "2005-06-01", "2005-06-30",
                                    country="India") == []
