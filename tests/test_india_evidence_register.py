"""Adversarial contract tests for the India evidence register."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import india_evidence_register as er


def _record(**kw):
    base = {
        "evidence_id": "EV:A", "source_name": "src", "source_version": "1",
        "locator_type": "URL", "locator": "https://example.org/x",
        "sha256": "b" * 64, "verification_state": "BYTES_VERIFIED",
        "access_terms": "CC BY 4.0",
        "coverage": {"temporal_start": "2000-01-01",
                     "temporal_end": "2020-12-31",
                     "spatial_scope": "test"},
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
    (lambda r: r.__setitem__("verification_state", "METADATA_VERIFIED"),
     "requires verification_state BYTES_VERIFIED"),
    (lambda r: r.__setitem__("verification_state", "UNRESOLVED"),
     "requires verification_state BYTES_VERIFIED"),
    (lambda r: r.__setitem__("locator_type", "FTP"), "locator_type"),
    (lambda r: r.__setitem__("limitations", []), "limitations"),
    (lambda r: r["coverage"].__setitem__("temporal_start", "2000-1-1"),
     "ISO date"),
])
def test_record_contract_rejected(mutator, match):
    record = _record()
    mutator(record)
    problems = er.validate_register(_doc([record]))
    assert any(match in p for p in problems), problems


def test_duplicate_evidence_ids_rejected():
    assert any("duplicate evidence_id" in p
               for p in er.validate_register(_doc([_record(), _record()])))


def test_resolution_requires_bytes_verified_covering_interval():
    verified = er.verified_records(_doc([_record()]))
    good = er.resolved_evidence_ids(["evidence:EV:A"], verified,
                                    "2005-06-01", "2005-06-30")
    assert good == ["EV:A"]
    # Free-text or foreign citations never resolve.
    assert er.resolved_evidence_ids(["paper:1"], verified,
                                    "2005-06-01", "2005-06-30") == []
    # Unknown ids never resolve.
    assert er.resolved_evidence_ids(["evidence:EV:MISSING"], verified,
                                    "2005-06-01", "2005-06-30") == []
    # Claims outside declared coverage never resolve.
    assert er.resolved_evidence_ids(["evidence:EV:A"], verified,
                                    "2021-01-01", "2021-01-02") == []
    # Unbounded claims never resolve.
    assert er.resolved_evidence_ids(["evidence:EV:A"], verified,
                                    None, "2005-06-30") == []
    # Metadata-verified records never satisfy.
    meta = er.verified_records(_doc([_record(verification_state="METADATA_VERIFIED",
                                             sha256=None)]))
    assert meta == {}
    assert er.resolved_evidence_ids(["evidence:EV:A"], meta,
                                    "2005-06-01", "2005-06-30") == []
