"""Delta-closure run-index and handoff tests (R05/R06/R09/R10).

RUN_INDEX_V1 names exactly one active generation bound to the candidate
manifest, the official pipeline report, the active audit packet, and the
handoff — all by run-root-relative paths.  Handoff v3 is residual-positive:
``REMEDIATION_COMPLETE_WITH_RESIDUALS`` with non-empty open and controlled
residual registers, and it can never carry readiness/authority claims.
"""
import json

import pytest

from nepal.framework_v1.cli import main
from nepal.framework_v1.orchestrator import (HANDOFF_V3_STATUS,
                                             build_remediation_handoff,
                                             verify_remediation_handoff)
from nepal.framework_v1.provenance import (bind_artifact_envelope,
                                           canonical_json, sha256_file)
from nepal.framework_v1.run_index import (RUN_INDEX_TYPE, build_run_index,
                                          load_run_index, verify_run_index,
                                          write_run_index)

GEN = "GEN-V2-ACTIVE"
GEN_STALE = "GEN-V1-HISTORICAL"


def _run_root(tmp_path):
    """A minimal run root: one candidate, one pipeline dir, one audit."""
    root = tmp_path / "runs"
    (root / "cand-v2").mkdir(parents=True)
    (root / "pipe-v3").mkdir(parents=True)
    (root / "cand-v2" / "manifest.json").write_text(
        canonical_json({"candidate_generation_id": GEN}), encoding="utf-8")
    (root / "pipe-v3" / "pipeline_report.json").write_text(
        canonical_json({"candidate_generation_id": GEN,
                        "manifest_sha256": "a" * 64}), encoding="utf-8")
    (root / "audit.json").write_text(
        canonical_json({"candidate_generation_id": GEN,
                        "manifest_sha256": "a" * 64}), encoding="utf-8")
    (root / "handoff.json").write_text(
        canonical_json({"envelope_type": "REMEDIATION_HANDOFF_V3"}),
        encoding="utf-8")
    return root


def _index_for(root, **over):
    kwargs = dict(
        run_root_name=root.name,
        active_generation_id=GEN,
        candidate_root="cand-v2",
        manifest_sha256="a" * 64,
        manifest_file_sha256=sha256_file(root / "cand-v2" / "manifest.json"),
        pipeline_run_id="pipe-v3",
        pipeline_root="pipe-v3",
        pipeline_report_sha256=sha256_file(
            root / "pipe-v3" / "pipeline_report.json"),
        active_audit_packet="audit.json",
        active_audit_packet_sha256=sha256_file(root / "audit.json"),
        handoff_path="handoff.json",
        handoff_sha256=sha256_file(root / "handoff.json"),
        superseded_generations=[GEN_STALE],
    )
    kwargs.update(over)
    return build_run_index(**kwargs)


class TestRunIndex:
    def test_round_trip_verifies_against_disk(self, tmp_path):
        root = _run_root(tmp_path)
        index = _index_for(root)
        path = write_run_index(root / "run_index.json", index)
        loaded = load_run_index(path, run_root=root)
        assert loaded["active_generation_id"] == GEN
        ok, problems = verify_run_index(loaded, run_root=root)
        assert ok, problems

    def test_exactly_one_active_generation(self, tmp_path):
        root = _run_root(tmp_path)
        index = _index_for(root, superseded_generations=[GEN])
        ok, problems = verify_run_index(index, run_root=root)
        assert not ok
        assert any("active generation cannot also be superseded" in p
                   for p in problems)

    def test_missing_active_generation_rejected(self, tmp_path):
        root = _run_root(tmp_path)
        index = _index_for(root)
        forged = dict(index)
        forged["active_generation_id"] = ""
        forged.pop("artifact_sha256", None)
        forged = bind_artifact_envelope(forged)
        ok, problems = verify_run_index(forged, run_root=root)
        assert not ok
        assert any("active generation" in p for p in problems)

    def test_absolute_path_in_canonical_field_rejected(self, tmp_path):
        root = _run_root(tmp_path)
        index = _index_for(root,
                           candidate_root=str(root / "cand-v2"))
        ok, problems = verify_run_index(index)
        assert not ok
        assert any("absolute path" in p for p in problems)

    def test_stale_bytes_rejected_on_disk(self, tmp_path):
        root = _run_root(tmp_path)
        index = _index_for(root)
        path = write_run_index(root / "run_index.json", index)
        (root / "pipe-v3" / "pipeline_report.json").write_text(
            canonical_json({"candidate_generation_id": GEN_STALE}),
            encoding="utf-8")
        ok, problems = verify_run_index(
            json.loads(path.read_text()), run_root=root)
        assert not ok
        assert any("checksum mismatch" in p for p in problems)

    def test_find_reports_restricted_to_indexed_generation(self, tmp_path):
        root = _run_root(tmp_path)
        index = _index_for(root)
        path = write_run_index(root / "run_index.json", index)
        # a stale sibling report for another generation must not be accepted
        stale_dir = root / "pipe-v1"
        stale_dir.mkdir()
        (stale_dir / "pipeline_report.json").write_text(
            canonical_json({"candidate_generation_id": GEN_STALE,
                            "manifest_sha256": "b" * 64}), encoding="utf-8")
        code = main(["find-reports", "--runs-root", str(root),
                     "--run-index", str(path)])
        assert code == 0
        code = main(["find-reports", "--runs-root", str(root),
                     "--run-index", str(path),
                     "--candidate-generation-id", GEN_STALE])
        assert code == 2

    def test_run_index_cli_verifies(self, tmp_path):
        root = _run_root(tmp_path)
        index = _index_for(root)
        path = write_run_index(root / "run_index.json", index)
        code = main(["run-index", "--index", str(path),
                     "--runs-root", str(root)])
        assert code == 0


def _handoff_kwargs():
    return dict(
        active_generation={"id": GEN, "candidate_root": "cand-v2"},
        code_revision="cfe60e9bd4c166295ee9e8f5c99b044ffbd4a6e7",
        stage_statuses={"A_CATALOG": "A_READY",
                        "B_SCREEN": "B_TO_C_BLOCKED",
                        "E_VALIDATION": "E_BLOCKED",
                        "F_BRIEFING": "F_BLOCKED"},
        candidate_counts={"artifact_count": 51, "ready": 43,
                          "incomplete": 2, "unavailable": 6,
                          "inventory_count": 51,
                          "package_inventory_count": 108},
        inventory_hash="c" * 64,
        open_residuals=[{"id": "R04", "status": "BLOCKED",
                         "note": "promotion is a separate I1 integration"}],
        controlled_residuals=[{"id": "C01", "note": "B LOO instability"}],
        verification_evidence={"pytest": {"passed": 447, "failed": 0}},
    )


class TestHandoffV3:
    def test_build_and_verify(self):
        handoff = build_remediation_handoff(**_handoff_kwargs())
        ok, problems = verify_remediation_handoff(handoff)
        assert ok, problems
        assert handoff["engineering_status"] == HANDOFF_V3_STATUS
        assert handoff["promotion_eligible"] is False
        assert handoff["production_authorized"] is False

    def test_empty_residuals_refused(self):
        kwargs = _handoff_kwargs()
        kwargs["open_residuals"] = []
        with pytest.raises(ValueError, match="open residual"):
            build_remediation_handoff(**kwargs)
        kwargs = _handoff_kwargs()
        kwargs["controlled_residuals"] = []
        with pytest.raises(ValueError, match="controlled residual"):
            build_remediation_handoff(**kwargs)

    def test_forbidden_claim_token_rejected(self):
        kwargs = _handoff_kwargs()
        kwargs["stage_statuses"] = {"X": "SCIENTIFICALLY_VALIDATED"}
        with pytest.raises(ValueError, match="forbidden"):
            build_remediation_handoff(**kwargs)

    def test_forged_ready_status_rejected(self):
        handoff = build_remediation_handoff(**_handoff_kwargs())
        forged = dict(handoff)
        forged["engineering_status"] = "READY"
        forged.pop("artifact_sha256", None)
        forged = bind_artifact_envelope(forged)
        ok, problems = verify_remediation_handoff(forged)
        assert not ok

    def test_absolute_path_in_canonical_field_rejected(self):
        kwargs = _handoff_kwargs()
        kwargs["active_generation"] = {"id": GEN,
                                       "candidate_root": "/abs/cand-v2"}
        with pytest.raises(ValueError, match="absolute path"):
            build_remediation_handoff(**kwargs)


# ---------- INDEX-01/02: path safety + active-profile binding ----------


def _forged_index(index, **overrides):
    """Return a copy of a built index with edits and a fresh self-hash —
    an internally consistent forgery that isolates the check under
    test."""
    forged = dict(index)
    forged.update(overrides)
    forged.pop("artifact_sha256", None)
    return bind_artifact_envelope(forged)


class TestIndexPathSafety:
    """INDEX-01: cross-platform path safety — no drive letters, UNC,
    backslashes, absolute paths, traversal, or symlink escapes in stored
    run-root-relative paths."""

    @pytest.mark.parametrize("bad", ["C:\\cand", "C:/cand",
                                     "cand\\v2", "\\\\server\\share",
                                     "//server/share", "/abs/cand",
                                     "../cand", "cand/../x"])
    def test_verify_rejects_nonportable_paths(self, tmp_path, bad):
        root = _run_root(tmp_path)
        index = _index_for(root)
        forged = _forged_index(index, candidate_root=bad)
        ok, problems = verify_run_index(forged, run_root=root)
        assert not ok
        assert any("safe relative" in p or "backslash" in p
                   or "drive" in p or "absolute" in p
                   for p in problems)

    def test_verify_rejects_noncanonical_stored_path(self, tmp_path):
        root = _run_root(tmp_path)
        index = _index_for(root)
        forged = _forged_index(index, handoff_path="a//b/handoff.json")
        ok, problems = verify_run_index(forged, run_root=root)
        assert not ok

    def test_build_canonicalizes_forward_slashes(self, tmp_path):
        root = _run_root(tmp_path)
        index = _index_for(root, candidate_root="cand-v2//")
        assert index["candidate_root"] == "cand-v2"
        ok, problems = verify_run_index(index, run_root=root)
        assert ok, problems

    def test_symlinked_indexed_file_rejected(self, tmp_path):
        root = _run_root(tmp_path)
        index = _index_for(root)
        path = write_run_index(root / "run_index.json", index)
        # keep identical bytes behind a symlink so only the symlink
        # check can fail
        real = (root / "audit_real.json")
        real.write_bytes((root / "audit.json").read_bytes())
        (root / "audit.json").unlink()
        (root / "audit.json").symlink_to(real)
        ok, problems = verify_run_index(
            json.loads(path.read_text()), run_root=root)
        assert not ok and any("symlink" in p for p in problems)

    def test_symlinked_parent_dir_rejected(self, tmp_path):
        root = _run_root(tmp_path)
        index = _index_for(root)
        path = write_run_index(root / "run_index.json", index)
        outside = tmp_path / "outside_cand"
        outside.mkdir()
        (outside / "manifest.json").write_bytes(
            (root / "cand-v2" / "manifest.json").read_bytes())
        for p in (root / "cand-v2").iterdir():
            p.unlink()
        (root / "cand-v2").rmdir()
        (root / "cand-v2").symlink_to(outside)
        ok, problems = verify_run_index(
            json.loads(path.read_text()), run_root=root)
        assert not ok and any("symlink" in p for p in problems)

    def test_backslash_path_on_disk_rejected(self, tmp_path):
        """A stored backslash path must never resolve — even where a
        same-named file exists."""
        root = _run_root(tmp_path)
        index = _index_for(root)
        forged = _forged_index(index, active_audit_packet="a\\u.json")
        ok, problems = verify_run_index(forged, run_root=root)
        assert not ok


class TestActiveProfileBinding:
    """INDEX-02: index_profile=ACTIVE requires a non-null handoff
    (digest+relpath), run-root name equality, and an on-disk handoff
    that verifies as an artifact envelope."""

    def _active_root(self, tmp_path):
        root = _run_root(tmp_path)
        handoff = bind_artifact_envelope(
            {"envelope_type": "REMEDIATION_HANDOFF_V3",
             "research_diagnostic_only": True})
        (root / "handoff.json").write_text(
            canonical_json(handoff) + "\n", encoding="utf-8")
        return root

    def test_active_profile_round_trip(self, tmp_path):
        root = self._active_root(tmp_path)
        index = _index_for(root, index_profile="ACTIVE",
                           handoff_sha256=sha256_file(
                               root / "handoff.json"))
        assert index["index_profile"] == "ACTIVE"
        ok, problems = verify_run_index(index, run_root=root)
        assert ok, problems

    def test_active_null_handoff_rejected(self, tmp_path):
        root = self._active_root(tmp_path)
        index = _index_for(
            root, index_profile="ACTIVE",
            handoff_sha256=sha256_file(root / "handoff.json"))
        forged = _forged_index(index, handoff_sha256=None)
        ok, problems = verify_run_index(forged, run_root=root)
        assert not ok and any("handoff" in p for p in problems)

    def test_active_build_requires_handoff_digest(self, tmp_path):
        root = self._active_root(tmp_path)
        with pytest.raises(ValueError):
            _index_for(root, index_profile="ACTIVE",
                       handoff_sha256=None)

    def test_active_missing_handoff_file_rejected(self, tmp_path):
        root = self._active_root(tmp_path)
        index = _index_for(root, index_profile="ACTIVE",
                           handoff_path="missing-handoff.json",
                           handoff_sha256="0" * 64)
        ok, problems = verify_run_index(index, run_root=root)
        assert not ok and any("handoff" in p for p in problems)

    def test_active_handoff_not_envelope_rejected(self, tmp_path):
        root = _run_root(tmp_path)  # handoff.json is NOT an envelope
        index = _index_for(root, index_profile="ACTIVE",
                           handoff_sha256=sha256_file(
                               root / "handoff.json"))
        ok, problems = verify_run_index(index, run_root=root)
        assert not ok and any("handoff" in p for p in problems)

    def test_active_run_root_name_mismatch_rejected(self, tmp_path):
        import shutil as _sh
        root = self._active_root(tmp_path)
        index = _index_for(root, index_profile="ACTIVE",
                           handoff_sha256=sha256_file(
                               root / "handoff.json"))
        sibling = tmp_path / "renamed-root"
        _sh.copytree(root, sibling)
        ok, problems = verify_run_index(index, run_root=sibling)
        assert not ok and any("run-root name" in p or
                              "run_root_name" in p for p in problems)

    def test_unknown_profile_rejected(self, tmp_path):
        root = _run_root(tmp_path)
        index = _index_for(root)
        forged = _forged_index(index, index_profile="EXECUTABLE-ISH")
        ok, problems = verify_run_index(forged, run_root=root)
        assert not ok and any("index_profile" in p for p in problems)

    def test_historical_profile_allows_null_handoff(self, tmp_path):
        root = _run_root(tmp_path)
        index = _index_for(root, index_profile="HISTORICAL",
                           handoff_sha256=None)
        ok, problems = verify_run_index(index)
        assert ok, problems
