"""Fail-closed territory qualification and digest-chain tests."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import india_territory_classify as territory  # noqa: E402
import india_territory_decision as td  # noqa: E402
import validate_india_source_intake as intake  # noqa: E402


def _assessment(**overrides):
    digests = {
        name: "a" * 64
        for name in set(territory.SHP_COMPONENTS)
        | set(territory.OVERLAY_FILES.values())
    }
    cross_checks = {
        name: {
            "file": f"{name}.geojson",
            "path": f"/evidence/{name}.geojson",
            "sha256": "b" * 64,
            "iou_equal_area": 0.99,
            "status": "checked",
        }
        for name in sorted(td.REQUIRED_CROSS_CHECKS)
    }
    doc = {
        "schema": td.ASSESSMENT_SCHEMA,
        "version": 1,
        "claim_scope": "research_only_phase0_qualification_assessment",
        "boundary_dir": "/evidence/boundary",
        "component_digests": digests,
        "qualification_capability": td.SOURCE_RELATIVE_CAPABILITY,
        "source_authenticity_verified": False,
        "disputed_areas_resolved": False,
        "checks": {"declared_crs": "EPSG:3857", "union_valid": True},
        "cross_checks": cross_checks,
        "territory_artifact": {
            "artifact": None, "path": None, "sha256": None,
            "summary": None, "status": "not_supplied",
        },
        "problems": [],
        "recommendation": "SOURCE_RELATIVE_ONLY",
        "limits": ["source-relative only"],
        "authority": dict(intake.AUTHORITY_FLAGS),
    }
    doc.update(overrides)
    return doc


def _decision(**overrides):
    doc = {
        "schema": td.DECISION_SCHEMA,
        "version": 1,
        "claim_scope": "research_only_phase0_territory_decision",
        "decision_state": "QUALIFIED",
        "qualification_scope": td.SOURCE_RELATIVE_SCOPE,
        "reviewer": {"id": "owner-1", "role": "owner"},
        "reviewer_identity_binding": "ASSERTED_NOT_CRYPTOGRAPHIC",
        "decision_rationale": "Approve source-relative screening only",
        "assessment_artifact": "assessment.json",
        "assessment_sha256": "c" * 64,
        "component_digests": _assessment()["component_digests"],
        "authority": dict(intake.AUTHORITY_FLAGS),
    }
    doc.update(overrides)
    return doc


def _write_bound(path: Path, doc) -> Path:
    path.write_text(json.dumps(doc, sort_keys=True), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    Path(str(path) + ".sha256").write_text(
        f"{digest}  {path.name}\n", encoding="utf-8")
    return path


def _decision_chain(tmp_path):
    boundary = tmp_path / "boundary"
    boundary.mkdir()
    component_digests = {}
    sums = []
    for name in sorted(td._required_component_names()):
        path = boundary / name
        path.write_bytes(name.encode("utf-8"))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        component_digests[name] = digest
        sums.append(f"{digest}  {name}")
    (boundary / "SHA256SUMS.txt").write_text(
        "\n".join(sums) + "\n", encoding="utf-8")

    cross_checks = {}
    for name in sorted(td.REQUIRED_CROSS_CHECKS):
        path = tmp_path / f"{name}.geojson"
        path.write_text("{}", encoding="utf-8")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        Path(str(path) + ".sha256").write_text(
            f"{digest}  {path.name}\n", encoding="utf-8")
        cross_checks[name] = {
            "file": path.name, "path": str(path), "sha256": digest,
            "iou_equal_area": 0.99, "status": "checked",
        }

    assessment = _assessment(
        boundary_dir=str(boundary), component_digests=component_digests,
        cross_checks=cross_checks)
    assessment_path = _write_bound(tmp_path / "assessment.json", assessment)
    decision = _decision(
        assessment_artifact=assessment_path.name,
        assessment_sha256=hashlib.sha256(
            assessment_path.read_bytes()).hexdigest(),
        component_digests=component_digests)
    decision_path = _write_bound(tmp_path / "decision.json", decision)
    evidence = {
        "artifact": decision_path.name,
        "artifact_sha256": hashlib.sha256(
            decision_path.read_bytes()).hexdigest(),
        "decision_state": "QUALIFIED", "binding": "sha256",
    }
    return assessment, assessment_path, decision_path, evidence


def test_assessment_recommendation_is_non_authorizing():
    doc = _assessment()
    assert td._validate_assessment(doc) == []
    assert doc["recommendation"] == "SOURCE_RELATIVE_ONLY"
    assert doc["qualification_capability"] == "SOURCE_RELATIVE_ONLY"
    assert doc["source_authenticity_verified"] is False
    assert doc["disputed_areas_resolved"] is False


def test_assessment_rejects_admin_capability_claims():
    doc = _assessment(
        qualification_capability=td.ADMIN_CAPABILITY,
        source_authenticity_verified=True,
        disputed_areas_resolved=True,
        recommendation="ADMINISTRATION_BOUNDARY",
    )
    problems = td._validate_assessment(doc)
    assert any("only source-relative" in problem for problem in problems)
    assert any("not established" in problem for problem in problems)


def test_missing_required_cross_check_is_a_problem_in_assessment(tmp_path):
    doc = td.assess(tmp_path, extra_geojson={})
    assert doc["recommendation"] == "NOT_QUALIFIED"
    assert doc["cross_checks"] == {
        name: {"status": "missing"}
        for name in sorted(td.REQUIRED_CROSS_CHECKS)
    }


def test_assessment_validator_requires_both_cross_checks():
    doc = _assessment()
    del doc["cross_checks"]["independent_osm"]
    assert any("cross-check set" in problem
               for problem in td._validate_assessment(doc))


def test_assessment_validator_rejects_missing_or_unchecked_cross_check():
    doc = _assessment()
    doc["cross_checks"]["same_lineage"]["status"] = "missing"
    assert any("lacks a problem record" in problem
               for problem in td._validate_assessment(doc))


@pytest.mark.parametrize("reviewer", [
    "owner-1", {"id": "agent-1", "role": "agent"},
    {"id": "", "role": "owner"},
    {"id": "owner-1", "role": "not-owner"},
])
def test_decision_requires_typed_owner_review(reviewer):
    assert any("reviewer" in problem
               for problem in td.validate_decision(_decision(reviewer=reviewer)))


def test_decision_requires_explicit_noncryptographic_identity_boundary():
    doc = _decision(reviewer_identity_binding="AUTHENTICATED")
    assert any("identity limitation" in problem
               for problem in td.validate_decision(doc))


def test_decision_rejects_administrative_scope():
    doc = _decision(qualification_scope=td.ADMINISTRATION_SCOPE)
    assert any("qualification_scope" in problem or "cannot qualify" in problem
               for problem in td.validate_decision(doc))


def test_decision_rejects_unknown_state_and_unbound_inputs():
    doc = _decision(decision_state="MAYBE", assessment_sha256="bad",
                    component_digests={})
    problems = td.validate_decision(doc)
    assert any("decision_state" in problem for problem in problems)
    assert any("assessment_sha256" in problem for problem in problems)
    assert any("component_digests" in problem for problem in problems)


def test_decide_rejects_bad_assessment_sidecar(tmp_path):
    assessment = tmp_path / "assessment.json"
    assessment.write_text(json.dumps(_assessment()), encoding="utf-8")
    Path(str(assessment) + ".sha256").write_text("0" * 64 + "  assessment.json\n")
    problems = td.decide(
        assessment, "QUALIFIED", {"id": "owner-1", "role": "owner"},
        "approve source-relative scope", tmp_path / "decision.json",
        td.SOURCE_RELATIVE_SCOPE)
    assert any("sidecar mismatch" in problem for problem in problems)


def test_assessment_verification_recomputes_live_payload(monkeypatch, tmp_path):
    expected = _assessment()
    monkeypatch.setattr(td, "assess", lambda *_args, **_kwargs: expected)
    path = _write_bound(tmp_path / "assessment.json", expected)
    assert td.verify_assessment(path) == []

    forged = _assessment()
    forged["checks"]["union_valid"] = False
    path = _write_bound(tmp_path / "forged-assessment.json", forged)
    problems = td.verify_assessment(path)
    assert any("recomputation mismatch" in problem for problem in problems)


def test_decision_verification_resolves_hash_chain(monkeypatch, tmp_path):
    assessment = _assessment()
    assessment_path = _write_bound(tmp_path / "assessment.json", assessment)
    decision = _decision(
        assessment_artifact=assessment_path.name,
        assessment_sha256=hashlib.sha256(assessment_path.read_bytes()).hexdigest(),
    )
    decision_path = _write_bound(tmp_path / "decision.json", decision)
    monkeypatch.setattr(td, "assess", lambda *_args, **_kwargs: assessment)
    assert td.verify_decision(decision_path) == []

    changed = json.loads(decision_path.read_text())
    changed["assessment_sha256"] = "0" * 64
    _write_bound(decision_path, changed)
    assert any("assessment digest" in problem
               for problem in td.verify_decision(decision_path))


def test_territory_evidence_must_match_resolved_decision_state(monkeypatch, tmp_path):
    decision = _decision(decision_state="NOT_QUALIFIED")
    path = _write_bound(tmp_path / "decision.json", decision)
    monkeypatch.setattr(td, "verify_decision", lambda _path: [])
    evidence = {
        "artifact": path.name,
        "artifact_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "decision_state": "QUALIFIED",
        "binding": "sha256",
    }
    assert any("does not match decision bytes" in problem
               for problem in td.verify_territory_evidence(
                   evidence, tmp_path, require_administration=False))


def test_source_relative_decision_never_satisfies_admin_requirement(tmp_path):
    evidence = {
        "artifact": "missing.json", "artifact_sha256": "a" * 64,
        "decision_state": "QUALIFIED", "binding": "sha256",
    }
    problems = td.verify_territory_evidence(
        evidence, tmp_path, require_administration=True)
    assert problems
    assert any("missing" in problem or "verify" in problem
               for problem in problems)


def test_live_source_relative_chain_cannot_authorize_frame_in_country(
        monkeypatch, tmp_path):
    import india_lake_frame as lake_frame

    assessment, _assessment_path, decision_path, evidence = _decision_chain(
        tmp_path)
    monkeypatch.setattr(td, "assess", lambda *_args, **_kwargs: assessment)
    verify_decision = td.verify_decision
    verification_calls = []

    def count_verifications(path):
        verification_calls.append(path)
        return verify_decision(path)

    monkeypatch.setattr(td, "verify_decision", count_verifications)

    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps([
        {"source_record_id": "NRSC:1", "lake_id": "1",
         "latitude": 30, "longitude": 80},
        {"source_record_id": "NRSC:2", "lake_id": "2",
         "latitude": 31, "longitude": 81},
    ]), encoding="utf-8")
    frame = lake_frame.build_frame(inventory, "NRSC", "fixture")
    for row in frame["records"]:
        row["location"]["territory_status"] = "IN_COUNTRY"
        row["location"]["territory_evidence"] = dict(evidence)
    problems = lake_frame.validate_frame(frame, territory_evidence_dir=tmp_path)
    assert any("source-relative qualification cannot establish" in problem
               for problem in problems)
    assert len(verification_calls) == 1


def test_territory_verification_cache_reuses_and_invalidates(monkeypatch, tmp_path):
    assessment, assessment_path, decision_path, evidence = _decision_chain(
        tmp_path)
    monkeypatch.setattr(td, "assess", lambda *_args, **_kwargs: assessment)
    verify_decision = td.verify_decision
    verification_calls = []

    def count_verifications(path):
        verification_calls.append(path)
        return verify_decision(path)

    monkeypatch.setattr(td, "verify_decision", count_verifications)
    cache = td.TerritoryDecisionVerificationCache()
    for _ in range(2):
        assert td.verify_territory_evidence(
            evidence, tmp_path, require_administration=False,
            verifier=cache) == []
    assert len(verification_calls) == 1

    changed = json.loads(assessment_path.read_text())
    changed["limits"].append("changed after cached verification")
    _write_bound(assessment_path, changed)
    problems = td.verify_territory_evidence(
        evidence, tmp_path, require_administration=False, verifier=cache)
    assert any("recomputation mismatch" in problem for problem in problems)
    assert len(verification_calls) == 2


def test_territory_verification_cache_detects_dependency_change_at_end(
        monkeypatch, tmp_path):
    assessment, _assessment_path, _decision_path, evidence = _decision_chain(
        tmp_path)
    monkeypatch.setattr(td, "assess", lambda *_args, **_kwargs: assessment)
    cache = td.TerritoryDecisionVerificationCache()
    assert td.verify_territory_evidence(
        evidence, tmp_path, require_administration=False,
        verifier=cache) == []

    dependency = Path(assessment["cross_checks"]["same_lineage"]["path"])
    dependency.write_text('{"changed": true}', encoding="utf-8")
    assert any("changed during validation" in problem
               for problem in cache.verify_unchanged())


def test_legacy_v0_decision_is_not_accepted_as_current_contract(tmp_path):
    legacy = {"schema": "INDIA_TERRITORY_DECISION_V0", "version": 0,
              "decision_state": "QUALIFIED"}
    assert td.validate_decision(legacy)
