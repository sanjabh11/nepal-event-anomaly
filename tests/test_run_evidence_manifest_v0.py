"""Unit contract for ``RunEvidenceManifestV0`` (Phase-4 R1 type).

The TYPE is implemented in ``nepal.research_v0.run_evidence`` as a pure
addition — the runner wiring (accepting a ``run_evidence_manifest``
package key) still awaits the ratified amendment and stays pinned red
in ``tests/test_r11_9_dual_manifest_contract.py``.  These tests pin the
type's own semantics: derived digests, distinct source IDs, exact
seven-key role manifests, and per-role byte verification through the
existing ``verify_source_evidence`` floor.  Fixture bytes only — never
scientific inputs.
"""
from __future__ import annotations

import hashlib
import re

import pytest

from nepal.research_v0._hashing import sha256_canonical
from nepal.research_v0.run_evidence import (
    RUN_EVIDENCE_MANIFEST_SCHEMA, RunEvidenceManifestV0)
from nepal.research_v0.source_intake import build_source_manifest

from tests.test_glof_poc_real_path import _manifest

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _role_manifest(tmp_path, name: str, source_id: str,
                   body: str = "1.0,2.0\n3.0,4.0\n") -> dict:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return build_source_manifest(
        tmp_path, source_id=source_id,
        source_version="v0-fixture",
        source_files=[{
            "relpath": name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}],
        units=["koshi"], feature_allowlist=["f1"],
        lineage="run-evidence unit lane")


class TestDerivedBinding:
    def test_role_digests_are_computed_not_asserted(self, tmp_path):
        event = _manifest()
        feature = _role_manifest(
            tmp_path, "features.csv", "reanalysis-era5-land")
        wrapper = RunEvidenceManifestV0(
            event_manifest=event, feature_manifest=feature)
        assert wrapper.source_ids == {
            "event": event["source_id"],
            "feature": "reanalysis-era5-land"}
        assert wrapper.digests["event"] == sha256_canonical(dict(event))
        assert wrapper.digests["feature"] == sha256_canonical(
            dict(feature))
        assert wrapper.digests["event"] != wrapper.digests["feature"]
        assert _HEX64.fullmatch(wrapper.role_digests_digest)
        assert _HEX64.fullmatch(wrapper.run_evidence_digest)
        assert wrapper.run_evidence_digest not in set(
            wrapper.digests.values())
        assert wrapper.problems() == []

    def test_run_digest_moves_when_any_role_moves(self, tmp_path):
        feature = _role_manifest(
            tmp_path, "features.csv", "reanalysis-era5-land")
        wrapper = RunEvidenceManifestV0(
            event_manifest=_manifest(), feature_manifest=feature)
        tampered = RunEvidenceManifestV0(
            event_manifest=_role_manifest(
                tmp_path, "events2.csv", "other-events"),
            feature_manifest=feature)
        assert tampered.run_evidence_digest != \
            wrapper.run_evidence_digest
        assert tampered.digests["event"] != wrapper.digests["event"]
        assert tampered.digests["feature"] == \
            wrapper.digests["feature"]

    def test_optional_roles_bind_when_present(self, tmp_path):
        wrapper = RunEvidenceManifestV0(
            event_manifest=_manifest(),
            feature_manifest=_role_manifest(
                tmp_path, "features.csv", "reanalysis-era5-land"),
            opportunity_manifest=_role_manifest(
                tmp_path, "lakes.csv", "icimod-pdgl-2015"),
            sidecar_manifest=_role_manifest(
                tmp_path, "sidecars.csv", "p5-sidecars"))
        assert set(wrapper.source_ids) == {
            "event", "opportunity", "feature", "sidecar"}
        assert set(wrapper.digests) == set(wrapper.source_ids)
        assert wrapper.problems() == []
        assert wrapper.verify_problems() == []


class TestInadmissibleWrappers:
    def test_required_roles_must_be_present(self, tmp_path):
        feature = _role_manifest(
            tmp_path, "features.csv", "reanalysis-era5-land")
        no_event = RunEvidenceManifestV0(feature_manifest=feature)
        assert any("event_manifest is required" in p
                   for p in no_event.problems())
        no_feature = RunEvidenceManifestV0(event_manifest=_manifest())
        assert any("feature_manifest is required" in p
                   for p in no_feature.problems())

    def test_source_ids_must_be_distinct(self, tmp_path):
        event = _manifest()
        colliding = _role_manifest(
            tmp_path, "features.csv", event["source_id"])
        wrapper = RunEvidenceManifestV0(
            event_manifest=event, feature_manifest=colliding)
        assert any("distinct" in p for p in wrapper.problems())

    def test_role_manifest_must_be_exact_seven_key(self, tmp_path):
        event = _manifest()
        extra = dict(_role_manifest(
            tmp_path, "features.csv", "reanalysis-era5-land"))
        extra["undeclared"] = "key"
        wrapper = RunEvidenceManifestV0(
            event_manifest=event, feature_manifest=extra)
        assert any("feature_manifest declares keys" in p
                   for p in wrapper.problems())

    def test_fixture_manifest_is_inadmissible_as_role(self, tmp_path):
        wrapper = RunEvidenceManifestV0(
            event_manifest=_manifest(),
            feature_manifest={"fixture": True})
        assert any("feature_manifest declares keys" in p
                   for p in wrapper.problems())

    def test_wrong_schema_tag_rejects(self, tmp_path):
        wrapper = RunEvidenceManifestV0(
            schema="RUN_EVIDENCE_MANIFEST_V9",
            event_manifest=_manifest(),
            feature_manifest=_role_manifest(
                tmp_path, "features.csv", "reanalysis-era5-land"))
        assert any("schema" in p for p in wrapper.problems())


class TestPerRoleByteVerification:
    def test_tampered_role_bytes_are_caught(self, tmp_path):
        feature = _role_manifest(
            tmp_path, "features.csv", "reanalysis-era5-land")
        wrapper = RunEvidenceManifestV0(
            event_manifest=_manifest(), feature_manifest=feature)
        assert wrapper.verify_problems() == []
        (tmp_path / "features.csv").write_text(
            "tampered,bytes\n", encoding="utf-8")
        problems = wrapper.verify_problems()
        assert problems and all(p.startswith("feature: ")
                                for p in problems)

    def test_structurally_invalid_role_is_not_byte_verified(
            self, tmp_path):
        wrapper = RunEvidenceManifestV0(
            event_manifest=_manifest(),
            feature_manifest={"fixture": True})
        # structural floor flags it; byte verification skips it
        assert wrapper.problems()
        assert wrapper.verify_problems() == []


class TestSerialization:
    def test_to_dict_emits_the_documented_shape(self, tmp_path):
        wrapper = RunEvidenceManifestV0(
            event_manifest=_manifest(),
            feature_manifest=_role_manifest(
                tmp_path, "features.csv", "reanalysis-era5-land"))
        d = wrapper.to_dict()
        assert d["schema"] == RUN_EVIDENCE_MANIFEST_SCHEMA
        assert set(d["source_ids"]) == {"event", "feature"}
        assert d["opportunity_manifest"] is None
        assert d["sidecar_manifest"] is None
        assert d["record_type"] == "RunEvidenceManifestV0"
        assert _HEX64.fullmatch(d["run_evidence_digest"])
