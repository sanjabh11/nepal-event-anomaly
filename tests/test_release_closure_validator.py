"""A3-07 release-closure validator regression tests.

The post-publication gate must accept a fully-bound
``P5_RELEASE_CLOSURE_V4`` document and fail closed on every defect
class the audit surfaced: path/SHA mismatches (A3-01), diverging suite
counts (A3-04), undeclared publication slots, stale sidecars, wrong
replay scope, true authority flags, inconsistent owner-approval
fields, missing files, and bad schema.  All fixtures are synthetic
``tmp_path`` trees; nothing touches the real evidence roots.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]
                     / "scripts"))

import validate_release_closure as vrc  # noqa: E402


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_path(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _write_json(path: Path, doc) -> str:
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    return _sha_path(path)


def _write_sidecar(path: Path) -> None:
    digest = _sha_path(path)
    Path(str(path) + ".sha256").write_text(f"{digest}  {path.name}\n",
                                           encoding="utf-8")


HEAD = "a" * 40
CONTENT_HEAD = "b" * 40
MANIFEST_SHA = "c" * 64
RECEIPT_COUNTS = {"collected": 11, "passed": 10, "skipped": 1,
                  "failed": 0, "errors": 0, "warnings": 3}


@pytest.fixture
def world(tmp_path):
    """A minimal but complete evidence layout plus a valid closure."""
    daily = tmp_path / "daily"
    retrieval = daily / "retrieval"
    retrieval.mkdir(parents=True)
    seasonal = tmp_path / "seasonal"
    (seasonal / "run").mkdir(parents=True)
    audit = tmp_path / "_glmdrift-audit"
    audit.mkdir()

    receipt_path = retrieval / "p5_suite_receipt.json"
    daily_replay = retrieval / "p5_replay_report_v5.json"
    seasonal_replay = seasonal / "run" / "seasonal_replay_report_v5.json"
    owner_path = retrieval / "p5_d_owner_disposition_v2.json"
    surface_path = audit / "INCIDENT_SURFACE_V1.json"
    index_path = retrieval / "p5_evidence_index_v2.json"
    closure_path = retrieval / "p5_release_closure_v4.json"

    receipt = {
        "schema": "P5_SUITE_RECEIPT_V1",
        "exit_code": 0,
        "counts": dict(RECEIPT_COUNTS),
        "repository_head": HEAD,
        "activity_id": "suite-run-1",
    }
    _write_json(receipt_path, receipt)
    _write_sidecar(receipt_path)
    _write_json(daily_replay, {"status": "REPLAY_OK",
                               "replay_scope":
                               "artifact_integrity_replay"})
    _write_json(seasonal_replay, {"status": "REPLAY_OK",
                                  "replay_scope":
                                  "artifact_integrity_replay"})
    _write_json(owner_path, {
        "schema": "P5_D_OWNER_DISPOSITION_V2",
        "disposition": "RECOMMENDED-DEFAULT adopted",
        "approval_status": "PENDING_OWNER_APPROVAL",
        "approved_by": None,
        "approval_utc": None})
    _write_json(surface_path, {"schema": "INCIDENT_SURFACE_V1",
                               "main_release_excluded": True,
                               "exclusion_reason": "audit artifacts",
                               "generated_utc": "2026-09-22T00:00:00Z",
                               "entries": []})

    index_doc = {
        "schema": "P5_EVIDENCE_INDEX_V2",
        "manifest_sha256": MANIFEST_SHA,
        "roots": {
            "daily_p5a2": {"path": str(daily), "role": "daily evidence"},
            "seasonal_v1_current": {"path": str(seasonal),
                                    "role": "seasonal evidence"},
        },
        "exclusions": [{
            "root_id": "daily_p5a2",
            "relpath": "retrieval/p5_release_closure_v4.json",
            "state": "planned",
            "reason": "post-publication closure slot",
            "owner": "release-coordinator"}],
        "final_verification": {"status": "CLOSURE_PENDING"},
    }
    _write_json(index_path, index_doc)
    _write_sidecar(index_path)

    def closure_doc() -> dict:
        return {
            "schema": "P5_RELEASE_CLOSURE_V4",
            "title": "Detached P5 audit-3 release-integrity closure",
            "claim_scope":
                "research_only_no_operational_authorization",
            "activity_id": "closure-build-1",
            "started_utc": "2026-09-22T00:00:00Z",
            "completed_utc": "2026-09-22T00:01:00Z",
            "root_of_trust": {
                "index_schema": "P5_EVIDENCE_INDEX_V2",
                "root_id": "daily_p5a2",
                "relpath": "retrieval/p5_evidence_index_v2.json",
                "sha256": _sha_path(index_path),
                "validator_status": "INDEX_OK"},
            "repository": {
                "head": HEAD,
                "content_head": CONTENT_HEAD,
                "manifest_commit": CONTENT_HEAD,
                "manifest_sha256": MANIFEST_SHA},
            "suite": {
                "receipt_relpath": "retrieval/p5_suite_receipt.json",
                "receipt_sha256": _sha_path(receipt_path),
                "counts": dict(RECEIPT_COUNTS),
                "generator_activity_id": "suite-run-1"},
            "replays": {
                "daily": {"root_id": "daily_p5a2",
                          "relpath": "retrieval/p5_replay_report_v5.json",
                          "sha256": _sha_path(daily_replay),
                          "status": "REPLAY_OK",
                          "scope": "artifact_integrity_replay"},
                "seasonal": {"root_id": "seasonal_v1_current",
                             "relpath":
                             "run/seasonal_replay_report_v5.json",
                             "sha256": _sha_path(seasonal_replay),
                             "status": "REPLAY_OK",
                             "scope": "artifact_integrity_replay"}},
            "authority": {"promotion_eligible": False,
                          "production_authorized": False,
                          "warning_path_authorized": False,
                          "operational_claim": False},
            "incident_surface": {
                "logical_path": "_glmdrift-audit/INCIDENT_SURFACE_V1.json",
                "sha256": _sha_path(surface_path),
                "validator_status": "INCIDENT_SURFACE_OK",
                "main_release_excluded": True},
            "owner_disposition": {
                "relpath": "retrieval/p5_d_owner_disposition_v2.json",
                "sha256": _sha_path(owner_path),
                "schema": "P5_D_OWNER_DISPOSITION_V2"},
            "environment": {"python": "3.14.0",
                            "environment_digest": "d" * 64},
        }

    def publish(doc=None) -> Path:
        _write_json(closure_path, doc if doc is not None
                    else closure_doc())
        _write_sidecar(closure_path)
        return closure_path

    publish()
    return SimpleNamespace(
        tmp=tmp_path, daily=daily, retrieval=retrieval,
        seasonal=seasonal, audit=audit, receipt_path=receipt_path,
        daily_replay=daily_replay, seasonal_replay=seasonal_replay,
        owner_path=owner_path, surface_path=surface_path,
        index_path=index_path, index_doc=index_doc,
        closure_path=closure_path, closure_doc=closure_doc,
        publish=publish)


def _report(world) -> dict:
    return vrc.validate_closure(world.closure_path)


def _rewrite(path: Path, **changes) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc.update(changes)
    _write_json(path, doc)


class TestReleaseClosureValidator:
    def test_valid_closure_passes(self, world):
        report = _report(world)
        assert report["status"] == "CLOSURE_OK", report["problems"]
        assert report["problems"] == []
        assert all(v == "PASS" for v in report["checks"].values())

    def test_missing_closure_file_fails(self, world):
        report = vrc.validate_closure(world.tmp / "nope.json")
        assert report["status"] == "CLOSURE_FAIL"
        assert report["checks"]["closure_file"] == "FAIL"

    def test_invalid_json_fails(self, world):
        world.closure_path.write_bytes(b"{not json")
        _write_sidecar(world.closure_path)
        assert _report(world)["status"] == "CLOSURE_FAIL"

    def test_wrong_schema_fails(self, world):
        doc = world.closure_doc()
        doc["schema"] = "P5_RELEASE_CLOSURE_V2"
        world.publish(doc)
        report = _report(world)
        assert report["status"] == "CLOSURE_FAIL"
        assert report["checks"]["schema"] == "FAIL"

    def test_missing_sidecar_fails(self, world):
        Path(str(world.closure_path) + ".sha256").unlink()
        report = _report(world)
        assert report["status"] == "CLOSURE_FAIL"
        assert report["checks"]["sidecar"] == "FAIL"

    def test_stale_sidecar_fails(self, world):
        # Rewrite the payload bytes without refreshing the sidecar.
        with world.closure_path.open("a") as fh:
            fh.write(" ")
        report = _report(world)
        assert report["status"] == "CLOSURE_FAIL"
        assert report["checks"]["sidecar"] == "FAIL"

    def test_root_of_trust_path_sha_mismatch_fails(self, world):
        """A3-01: recording the v2 index relpath while hashing other
        bytes must fail closed."""
        doc = world.closure_doc()
        doc["root_of_trust"]["sha256"] = _sha_path(world.receipt_path)
        world.publish(doc)
        report = _report(world)
        assert report["status"] == "CLOSURE_FAIL"
        assert report["checks"]["root_of_trust"] == "FAIL"

    def test_root_of_trust_missing_file_fails(self, world):
        doc = world.closure_doc()
        doc["root_of_trust"]["relpath"] = "retrieval/no_such_index.json"
        world.publish(doc)
        report = _report(world)
        assert report["status"] == "CLOSURE_FAIL"
        assert report["checks"]["root_of_trust"] == "FAIL"

    def test_validator_status_not_index_ok_fails(self, world):
        doc = world.closure_doc()
        doc["root_of_trust"]["validator_status"] = "INDEX_FAIL"
        world.publish(doc)
        assert _report(world)["checks"]["root_of_trust"] == "FAIL"

    def test_unplanned_slot_fails(self, world):
        """The closure must occupy a slot the index declared 'planned'
        before publication; an undeclared path fails closed."""
        index = world.index_doc
        index["exclusions"][0]["state"] = "present"
        _write_json(world.index_path, index)
        world.publish()
        report = _report(world)
        assert report["status"] == "CLOSURE_FAIL"
        assert report["checks"]["index_crosscheck"] == "FAIL"
        assert any("planned" in p for p in report["problems"])

    def test_final_verification_bad_status_fails(self, world):
        index = world.index_doc
        index["final_verification"]["status"] = "OPEN"
        _write_json(world.index_path, index)
        world.publish()
        report = _report(world)
        assert report["checks"]["index_crosscheck"] == "FAIL"

    def test_manifest_sha_mismatch_fails(self, world):
        doc = world.closure_doc()
        doc["repository"]["manifest_sha256"] = "e" * 64
        world.publish(doc)
        report = _report(world)
        assert report["status"] == "CLOSURE_FAIL"
        assert report["checks"]["index_crosscheck"] == "FAIL"

    def test_suite_count_divergence_fails(self, world):
        """A3-04: the recorded warnings count may not diverge from the
        machine-generated receipt."""
        doc = world.closure_doc()
        doc["suite"]["counts"]["warnings"] = 99
        world.publish(doc)
        report = _report(world)
        assert report["status"] == "CLOSURE_FAIL"
        assert report["checks"]["suite_binding"] == "FAIL"

    def test_receipt_not_green_fails(self, world):
        _rewrite(world.receipt_path, exit_code=1)
        world.publish()
        report = _report(world)
        assert report["checks"]["suite_binding"] == "FAIL"

    def test_receipt_head_unbound_fails(self, world):
        _rewrite(world.receipt_path, repository_head="f" * 40)
        world.publish()
        report = _report(world)
        assert report["checks"]["suite_binding"] == "FAIL"

    def test_replay_live_status_fails(self, world):
        _rewrite(world.daily_replay, status="REPLAY_FAIL")
        world.publish()
        report = _report(world)
        assert report["checks"]["replay_daily"] == "FAIL"

    def test_replay_wrong_scope_fails(self, world):
        doc = world.closure_doc()
        doc["replays"]["seasonal"]["scope"] = "full_replay"
        world.publish(doc)
        report = _report(world)
        assert report["checks"]["replay_seasonal"] == "FAIL"

    def test_authority_flag_true_fails(self, world):
        doc = world.closure_doc()
        doc["authority"]["operational_claim"] = True
        world.publish(doc)
        report = _report(world)
        assert report["checks"]["authority"] == "FAIL"

    def test_claim_scope_wrong_fails(self, world):
        doc = world.closure_doc()
        doc["claim_scope"] = "operational"
        world.publish(doc)
        assert _report(world)["checks"]["authority"] == "FAIL"

    def test_owner_approval_half_set_fails(self, world):
        _rewrite(world.owner_path, approved_by="owner@example.org")
        world.publish()
        report = _report(world)
        assert report["checks"]["owner_disposition"] == "FAIL"

    def test_owner_approved_without_identity_fails(self, world):
        _rewrite(world.owner_path, approval_status="APPROVED")
        world.publish()
        report = _report(world)
        assert report["checks"]["owner_disposition"] == "FAIL"

    def test_environment_digest_bad_fails(self, world):
        doc = world.closure_doc()
        doc["environment"]["environment_digest"] = "not-hex"
        world.publish(doc)
        assert _report(world)["checks"]["environment"] == "FAIL"

    def test_reversed_timestamps_fail(self, world):
        doc = world.closure_doc()
        doc["started_utc"] = "2026-09-22T01:00:00Z"
        doc["completed_utc"] = "2026-09-22T00:00:00Z"
        world.publish(doc)
        assert _report(world)["checks"]["environment"] == "FAIL"

    def test_incident_surface_sha_mismatch_fails(self, world):
        doc = world.closure_doc()
        doc["incident_surface"]["sha256"] = "0" * 64
        world.publish(doc)
        report = _report(world)
        assert report["checks"]["incident_surface"] == "FAIL"

    def test_cli_exit_codes(self, world, capsys):
        assert vrc.main([str(world.closure_path)]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "CLOSURE_OK"
        doc = world.closure_doc()
        doc["authority"]["promotion_eligible"] = True
        world.publish(doc)
        assert vrc.main([str(world.closure_path)]) == 1
