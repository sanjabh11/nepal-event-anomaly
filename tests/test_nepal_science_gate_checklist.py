"""Tests for the enforced Nepal-derived science safeguards in India Phase 0."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import validate_nepal_science_gate_checklist as gate  # noqa: E402


def _write_checklist(tmp_path: Path, doc: dict) -> Path:
    path = tmp_path / "checklist.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    Path(str(path) + ".sha256").write_text(
        f"{digest}  {path.name}\n", encoding="utf-8")
    return path


def _valid_ready_denominators() -> dict[str, int]:
    return {
        "catalog_rows": 1,
        "mapped_lake_rows": 1,
        "verified_source_bytes": 1,
        "unreviewed_event_rows": 0,
        "eligible_unverified_evidence": 0,
        "unverified_control_candidates": 0,
        "uncertain_territory_lakes": 0,
        "unresolved_lake_identities": 0,
        "in_country_canonical_lakes": 1,
        "observable_lake_years": 1,
        "observation_unknown_lake_rows": 1,
        "observation_partial_lake_rows": 0,
        "observation_known_breach_lake_rows": 0,
        "observation_verified_non_event_lake_rows": 0,
    }


def test_valid_checklist_requires_matching_sidecar(tmp_path):
    path = _write_checklist(tmp_path, json.loads(
        gate.DEFAULT_CHECKLIST.read_text(encoding="utf-8")))
    doc, digest = gate.load_verified_checklist(path)
    assert doc["schema"] == gate.SCHEMA
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()


def test_checklist_rejects_missing_or_contradictory_policy(tmp_path):
    doc = json.loads(gate.DEFAULT_CHECKLIST.read_text(encoding="utf-8"))
    doc["policy_invariants"].pop()
    assert any("exactly match" in p for p in gate.validate_checklist(doc))

    doc = json.loads(gate.DEFAULT_CHECKLIST.read_text(encoding="utf-8"))
    doc["authority"]["weather_download_authorized"] = True
    assert any("authority" in p for p in gate.validate_checklist(doc))

    doc = json.loads(gate.DEFAULT_CHECKLIST.read_text(encoding="utf-8"))
    doc["phase0_required_conditions"][0]["required_before_decision_ready"] = False
    assert any("required before decision readiness" in p
               for p in gate.validate_checklist(doc))


def test_checklist_sidecar_tampering_blocks_preflight(tmp_path):
    path = _write_checklist(tmp_path, json.loads(
        gate.DEFAULT_CHECKLIST.read_text(encoding="utf-8")))
    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="sidecar does not match"):
        gate.load_verified_checklist(path)


def test_phase0_readiness_reports_explicit_required_conditions():
    ready = gate.phase0_readiness(_valid_ready_denominators())
    assert ready["status"] == "READY_FOR_OWNER_REVIEW"
    assert set(ready["required_condition_status"]) == gate.REQUIRED_PHASE0_CONDITIONS
    assert all(value == "SATISFIED"
               for value in ready["required_condition_status"].values())
    assert "never acquisition" in ready["meaning"]


@pytest.mark.parametrize("malformed", [None, [], "counts", 3])
def test_phase0_readiness_fails_closed_on_non_object_denominators(malformed):
    result = gate.phase0_readiness(malformed)
    assert result["status"] == "BLOCKED"
    assert result["blocking_reasons"] == ["INVALID_DENOMINATOR_OBJECT"]


def test_phase0_readiness_blocks_unknowns_and_unverified_controls():
    missing = gate.phase0_readiness({})
    assert missing["status"] == "BLOCKED"
    assert "NO_PINNED_SOURCE_BYTES" in missing["blocking_reasons"]
    assert "NO_LAKE_FRAME_ROWS" in missing["blocking_reasons"]

    bad_control = _valid_ready_denominators()
    bad_control["unverified_control_candidates"] = 1
    result = gate.phase0_readiness(bad_control)
    assert result["status"] == "BLOCKED"
    assert "CONTROL_EVIDENCE_UNVERIFIED" in result["blocking_reasons"]

    bad_partition = _valid_ready_denominators()
    bad_partition["observation_unknown_lake_rows"] = 0
    result = gate.phase0_readiness(bad_partition)
    assert result["status"] == "BLOCKED"
    assert "OBSERVATION_STATUS_DENOMINATOR_MISMATCH" in result[
        "blocking_reasons"]

    bad_event_evidence = _valid_ready_denominators()
    bad_event_evidence["eligible_unverified_evidence"] = 1
    result = gate.phase0_readiness(bad_event_evidence)
    assert result["status"] == "BLOCKED"
    assert result["required_condition_status"][
        "EVENT_ROWS_RETAINED_AND_ADJUDICATION_COMPLETE"] == "UNRESOLVED"

    invalid_count = _valid_ready_denominators()
    invalid_count["mapped_lake_rows"] = True
    result = gate.phase0_readiness(invalid_count)
    assert result["status"] == "BLOCKED"
    assert "INVALID_DENOMINATOR:mapped_lake_rows" in result["blocking_reasons"]


def test_zero_verified_controls_is_a_feasibility_result_not_a_fake_negative():
    result = gate.phase0_readiness(_valid_ready_denominators())
    assert result["required_condition_status"][
        "VERIFIED_NON_EVENT_STATUS_NOT_INFERRED_FROM_ABSENCE"] == "SATISFIED"
    # A zero control count is allowed here because this is only Phase-0
    # feasibility reporting; the lake-year screen will still expose it.
