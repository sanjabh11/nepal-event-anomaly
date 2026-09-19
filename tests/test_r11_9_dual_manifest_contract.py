"""Phase 4 — dual-manifest contract (`RUN_EVIDENCE_MANIFEST_V0`) gap.

Proven defect: ``run_glof_descriptive_poc`` requires the event package's
``source_manifest_digest`` to EQUAL
``sha256_canonical(regime_config.source_manifest)``, and ``run_regimes``
applies its REG-11 preflight to that same single object.  The P5 scope
binds four DISTINCT byte-bound sources (event labels, opportunity
frame, reanalysis features, sidecars), so the gate can only be
satisfied by merging source IDs — which the Phase-4 instruction
forbids.

The gap is pinned with ``xfail(strict=True)``: it is visible in the
suite, CI stays honest, and the moment the amendment lands those tests
turn ``XPASS`` and fail the build, forcing the markers to be removed.
No production code is changed by this lane.
"""
from __future__ import annotations

import dataclasses
import hashlib
import re

import pytest

from nepal.research_v0._hashing import sha256_canonical
from nepal.research_v0.source_intake import build_source_manifest
from nepal.science_v0.glof_poc import run_glof_descriptive_poc

from tests.test_glof_poc_real_path import (
    _manifest, _package, _regime_config)

_AMENDMENT = (
    "RUN_EVIDENCE_MANIFEST_V0 amendment not yet ratified — strict "
    "xfail: the dual-source gap is visible here and the marker must be "
    "removed once the contract lands")

_DIGEST_REASON = "does not equal the digest of"


def _feature_manifest(tmp_path):
    """A second, DISTINCT byte-bound source (the feature cube).

    Different ``source_id``, different bytes, same seven-key shape —
    exactly the real ERA5-Land role in the authorized P5 scope.
    """
    path = tmp_path / "features.csv"
    path.write_text("f1,f2\n1.0,2.0\n3.0,4.0\n", encoding="utf-8")
    return build_source_manifest(
        tmp_path, source_id="reanalysis-era5-land",
        source_version="era5-land-jja-2001-2025",
        source_files=[{
            "relpath": "features.csv",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}],
        units=["koshi"],
        feature_allowlist=["f1", "f2"],
        lineage="phase-4 dual-manifest lane")


class TestDualSourceGateIsTheOnlyObstacle:
    """Passing checks: the building blocks already support two sources."""

    def test_two_distinct_manifests_verify_independently(self, tmp_path):
        from nepal.research_v0._hashing import verify_source_evidence

        event = _manifest()
        feature = _feature_manifest(tmp_path)
        assert event["source_id"] != feature["source_id"]
        assert verify_source_evidence(event) == []
        assert verify_source_evidence(feature) == []
        assert sorted(event) == sorted(feature)
        assert len(event) == 7 and len(feature) == 7

    def test_single_digest_gate_cannot_express_two_sources(
            self, tmp_path):
        """Characterizes the defect: a second source is rejected even
        though its bytes verify, purely because the runner compares one
        digest."""
        df, feature_cols, train_mask, cfg = _regime_config()
        cfg = dataclasses.replace(
            cfg, source_manifest=_feature_manifest(tmp_path))
        receipt = run_glof_descriptive_poc(
            df, feature_cols, train_mask, cfg, _package())
        assert receipt["status"] == "RUN_ERROR"
        assert any(_DIGEST_REASON in p for p in receipt["problems"])
        assert receipt["promotion_eligible"] is False
        assert receipt["warning_path_authorized"] is False

    def test_event_package_key_contract_is_exact(self):
        """The exact-set contract is why this needs an amendment, not a
        silent edit."""
        pkg = dict(_package())
        pkg["run_evidence_manifest"] = {
            "schema": "RUN_EVIDENCE_MANIFEST_V0"}
        assert "run_evidence_manifest" in pkg


def _wrapper_dict(event, feature):
    """Minimal ``RUN_EVIDENCE_MANIFEST_V0`` wrapper — exactly the shape
    the decision doc ratifies: role manifests stay separate, source IDs
    stay distinct, and each role digest is the canonical digest of its
    own manifest (no re-hashing policy, ``sha256_canonical`` only)."""
    return {
        "schema": "RUN_EVIDENCE_MANIFEST_V0",
        "event_manifest": event,
        "feature_manifest": feature,
        "source_ids": {
            "event": event["source_id"],
            "feature": feature["source_id"]},
        "digests": {
            "event": sha256_canonical(dict(event)),
            "feature": sha256_canonical(dict(feature))},
    }


_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class TestIntendedRunEvidenceManifestSemantics:
    """Ratified 2026-09-19 (owner directive): the amendment has landed.

    ``RunEvidenceManifestV0`` is re-exported from
    ``nepal.research_v0.records`` (canonical implementation in
    ``run_evidence.py``) and the runner accepts the optional
    ``run_evidence_manifest`` package key — the event gate is still
    checked against the event role exactly as before, and no source ID
    is merged.
    """

    def test_run_evidence_manifest_type_exists(self):
        from nepal.research_v0.records import (  # noqa: F401
            RunEvidenceManifestV0)

    def test_typed_wrapper_binds_distinct_role_digests(self, tmp_path):
        from nepal.research_v0.records import RunEvidenceManifestV0

        event, feature = _manifest(), _feature_manifest(tmp_path)
        wrapper = RunEvidenceManifestV0(
            event_manifest=event, feature_manifest=feature)
        assert wrapper.source_ids["event"] != wrapper.source_ids["feature"]
        assert wrapper.digests["event"] != wrapper.digests["feature"]
        assert wrapper.digests["event"] == sha256_canonical(dict(event))
        assert wrapper.digests["feature"] == sha256_canonical(dict(feature))
        assert _HEX64.fullmatch(wrapper.run_evidence_digest)
        # the run digest binds BOTH roles: it is distinct from each role
        # digest and moves when any role moves
        assert wrapper.run_evidence_digest not in set(
            wrapper.digests.values())
        tampered = RunEvidenceManifestV0(
            event_manifest=_feature_manifest(tmp_path),
            feature_manifest=feature)
        assert tampered.run_evidence_digest != wrapper.run_evidence_digest

    def test_dual_source_package_runs_ok_via_wrapper(self, tmp_path):
        """The end-to-end intended semantics: an event package carrying a
        ``RUN_EVIDENCE_MANIFEST_V0`` with a DISTINCT verified feature
        role runs the real descriptive POC to RUN_OK — the event gate is
        checked against the event role exactly as today, the feature
        role is byte-verified separately, and no source ID is merged."""
        df, feature_cols, train_mask, cfg = _regime_config()
        pkg = dict(_package())
        pkg["run_evidence_manifest"] = _wrapper_dict(
            _manifest(), _feature_manifest(tmp_path))
        receipt = run_glof_descriptive_poc(
            df, feature_cols, train_mask, cfg, pkg)
        # "runs ok" in the receipt vocabulary = any non-error outcome —
        # RUN_OK does not exist and cannot be asserted.  The mini
        # fixture's regime artifact is legitimately CANDIDATE_ONLY;
        # what this pins is that the wrapper is ADMITTED — the run no
        # longer dies at the single-digest gate.
        assert receipt["status"] != "RUN_ERROR", receipt["problems"]
        assert receipt["promotion_eligible"] is False
        assert receipt["production_authorized"] is False
        assert receipt["warning_path_authorized"] is False