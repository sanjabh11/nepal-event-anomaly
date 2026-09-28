"""Territory qualification decision contract tests."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import india_territory_decision as td  # noqa: E402

E = Path("/Users/sanjayb/nepal-event-anomaly-evidence")
_ASSESS = (E / "india-phase0-source-intake"
           / "INDIA_TERRITORY_QUALIFICATION_ASSESSMENT_V0.json")
_DECIDE = (E / "india-phase0-source-intake"
           / "INDIA_TERRITORY_DECISION_V0.json")
_PRESENT = _ASSESS.is_file() and _DECIDE.is_file()


def test_decision_requires_reviewer_and_rationale(tmp_path):
    doc = {"schema": td.DECISION_SCHEMA, "version": 0,
           "decision_state": "QUALIFIED", "decided_by": "",
           "decision_rationale": "", "assessment_sha256": "a" * 64,
           "boundary_sha256": "b" * 64}
    problems = td.validate_decision(doc)
    assert "decided_by required" in problems
    assert "decision_rationale required" in problems


def test_decision_rejects_unknown_state():
    doc = {"schema": td.DECISION_SCHEMA, "decision_state": "MAYBE",
           "decided_by": "x", "decision_rationale": "y",
           "assessment_sha256": "a" * 64, "boundary_sha256": "b" * 64}
    assert td.validate_decision(doc)


def test_cannot_qualify_a_failed_assessment(tmp_path):
    a = tmp_path / "a.json"
    a.write_text(json.dumps({"problems": ["x"], "component_digests": {}}))
    problems = td.decide(a, "QUALIFIED", "r", "r", tmp_path / "d.json", "s")
    assert problems


@pytest.mark.skipif(not _PRESENT, reason="external evidence root unavailable")
def test_live_assessment_clean_and_bound():
    a = json.loads(_ASSESS.read_text())
    assert a["problems"] == []
    assert a["recommendation"].startswith("QUALIFIED")
    assert a["checks"]["declared_crs"] == "EPSG:3857"
    d = json.loads(_DECIDE.read_text())
    assert td.validate_decision(d) == []
    assert d["decision_state"] == "QUALIFIED"
    assert d["assessment_sha256"] == __import__("hashlib").sha256(
        _ASSESS.read_bytes()).hexdigest()
