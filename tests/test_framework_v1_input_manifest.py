"""Fail-closed tests for the framework-side reconciled input adapter."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from nepal.framework_v1 import contract as C
from nepal.framework_v1 import input_manifest as im
from nepal.framework_v1.input_manifest import (
    DATA_STATUSES,
    B_TARGET_GRID_ARTIFACT_IDS,
    canonical_input_manifest_hash,
    _validate_contract_source_bindings,
    _validate_b_manifest_declarations,
    _validate_package_inventory_files,
    validate_artifact_bundle,
    validate_phase_artifact,
    verify_input_manifest,
    verify_phase_manifest,
)
from nepal.framework_v1.provenance import canonical_json
from nepal.framework_v1.provenance import sha256_file


def _manifest(root, *, status="READY", relative_path="payload.bin"):
    payload = b"payload"
    path = root / relative_path
    if status == "READY":
        relative = Path(relative_path)
        if not relative.is_absolute() and ".." not in relative.parts:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        size = len(payload)
    else:
        digest = ""
        size = 0
    manifest = {
        "schema_version": "2.0.0-reconciled",
        "contract_sha256": C.contract_hash(),
        "artifact_count": 1,
        "artifacts": [{
            "artifact_id": "test_payload",
            "role": "A",
            "kind": "metadata",
            "relative_path": relative_path if status != "UNAVAILABLE" else "",
            "status": status,
            "sha256": digest,
            "bytes": size,
            "crs": "",
            "units": "",
            "license": "test",
            "source_url": "https://example.invalid/source",
            "query_or_request": "test",
            "source_record_id": "test-1",
            "acquired_at": "2026-09-11",
            "observation_start": "",
            "observation_end": "",
            "publication_or_validity_date": "",
            "processing": "none",
            "language_access_status": "en/accessible",
        }],
    }
    manifest["manifest_sha256"] = canonical_input_manifest_hash(manifest)
    return manifest


class TestInputManifestVerification:
    def test_phase_manifest_reuses_hash_for_registered_and_inventory_path(
            self, tmp_path, monkeypatch):
        payload = tmp_path / "payload.bin"
        payload.write_bytes(b"registered-and-inventoried")
        repo_root = Path(__file__).resolve().parents[1]
        digest = hashlib.sha256(payload.read_bytes()).hexdigest()
        manifest = {
            "schema_version": "2.0.0-reconciled",
            "contract_sha256": C.contract_hash(),
            "framework_contract_sha256": C.contract_hash(),
            "artifact_count": 1,
            "artifacts": [{
                "artifact_id": "test_payload",
                "role": "B",
                "kind": "metadata",
                "relative_path": "payload.bin",
                "status": "READY",
                "sha256": digest,
                "bytes": payload.stat().st_size,
                "crs": "",
                "units": "",
                "license": "test",
                "source_url": "https://example.invalid/source",
                "query_or_request": "test",
                "source_record_id": "test-1",
                "acquired_at": "2026-09-11",
                "observation_start": "",
                "observation_end": "",
                "publication_or_validity_date": "",
                "processing": "none",
                "language_access_status": "en/accessible",
            }],
            "required_artifact_ids": list(C.PHASE_REQUIRED_ARTIFACT_IDS["B"]),
            "target_grid_artifact_ids": dict(C.B_TARGET_GRID_ARTIFACT_IDS),
            "manifest_hash_encoding": "canonical_json",
            "canonical_hash_domain": "canonical_json_without_manifest_sha256",
            "data_contract_source_path": "nepal/feature_contract.py",
            "framework_contract_source_path": "nepal/framework_v1/contract.py",
            "data_contract_source_sha256": sha256_file(
                repo_root / "nepal/feature_contract.py"),
            "framework_contract_source_sha256": sha256_file(
                repo_root / "nepal/framework_v1/contract.py"),
            "dirty_diff_sha256": "0" * 64,
            "code_revision": "test-revision",
            "package_inventory": [{
                "relative_path": "payload.bin",
                "sha256": digest,
                "bytes": payload.stat().st_size,
            }],
            "package_inventory_policy": {
                "excluded": ["manifest.json", "**/__pycache__/**"],
            },
            "waivers": [],
        }
        manifest["manifest_sha256"] = canonical_input_manifest_hash(manifest)
        calls = []
        original = im.sha256_file

        def counted(path):
            resolved = Path(path).resolve()
            calls.append(resolved)
            return original(resolved)

        monkeypatch.setattr(im, "sha256_file", counted)
        verify_phase_manifest(
            manifest, tmp_path, "B",
            expected_contract_sha256=C.contract_hash(),
            expected_framework_contract_sha256=C.contract_hash(),
            repo_root=repo_root,
            scan_root_for_raw_slc=False,
        )
        assert calls.count(payload.resolve()) == 1

    def test_manifest_hash_cache_does_not_cross_verification_invocations(
            self, tmp_path):
        manifest = _manifest(tmp_path)
        first = verify_input_manifest(
            manifest, tmp_path,
            expected_contract_sha256=C.contract_hash(),
            expected_framework_contract_sha256=C.contract_hash(),
        )
        assert first.ok is True
        (tmp_path / "payload.bin").write_bytes(b"tampered")
        manifest["artifacts"][0]["sha256"] = hashlib.sha256(
            b"tampered").hexdigest()
        manifest["artifacts"][0]["bytes"] = len(b"tampered")
        manifest["manifest_sha256"] = canonical_input_manifest_hash(manifest)
        second = verify_input_manifest(
            manifest, tmp_path,
            expected_contract_sha256=C.contract_hash(),
            expected_framework_contract_sha256=C.contract_hash(),
        )
        assert second.ok is True
        manifest["artifacts"][0]["sha256"] = hashlib.sha256(
            b"payload").hexdigest()
        manifest["artifacts"][0]["bytes"] = len(b"payload")
        manifest["manifest_sha256"] = canonical_input_manifest_hash(manifest)
        detected = verify_input_manifest(
            manifest, tmp_path,
            expected_contract_sha256=C.contract_hash(),
            expected_framework_contract_sha256=C.contract_hash(),
        )
        assert detected.ok is False
        assert any("checksum mismatch" in error for error in detected.errors)

    def test_manifest_hash_cache_rechecks_file_identity_after_mutation(
            self, tmp_path):
        payload = tmp_path / "payload.bin"
        payload.write_bytes(b"original!!")
        cache = {}
        first = im._cached_file_digest(payload, cache)
        payload.write_bytes(b"mutated!!!")
        second = im._cached_file_digest(payload, cache)
        assert first != second

    def test_ready_artifact_and_canonical_self_hash_pass(self, tmp_path):
        manifest = _manifest(tmp_path)
        result = verify_input_manifest(manifest, tmp_path,
                                       expected_contract_sha256=C.contract_hash(),
                                       expected_framework_contract_sha256=C.contract_hash())
        assert result.ok is True
        assert list(result.errors) == []
        assert list(result.ready_artifact_ids) == ["test_payload"]

    def test_runtime_framework_binding_is_distinct_from_optional_declaration(self,
                                                                              tmp_path):
        manifest = _manifest(tmp_path)
        result = verify_input_manifest(
            manifest, tmp_path,
            expected_contract_sha256=C.contract_hash(),
            expected_framework_contract_sha256=C.contract_hash())
        assert result.checks["framework_contract_runtime_bound"] is True
        assert result.checks["framework_contract_declared"] is False
        assert result.checks["framework_contract_declaration_bound"] is False

    def test_stale_expected_framework_hash_cannot_authorize_current_runtime(
            self, tmp_path):
        manifest = _manifest(tmp_path)
        result = verify_input_manifest(
            manifest, tmp_path,
            expected_contract_sha256=C.contract_hash(),
            expected_framework_contract_sha256="0" * 64)
        assert result.ok is False
        assert result.can_run_primary is False
        assert result.checks["framework_contract_runtime_bound"] is False
        assert any("runtime framework contract" in error.lower()
                   for error in result.errors)

    def test_self_hash_is_canonical_not_pretty_json(self, tmp_path):
        manifest = _manifest(tmp_path)
        assert manifest["manifest_sha256"] == hashlib.sha256(
            canonical_json({k: v for k, v in manifest.items()
                            if k != "manifest_sha256"}).encode("utf-8")
        ).hexdigest()

    def test_unavailable_is_reported_but_not_consumable(self, tmp_path):
        manifest = _manifest(tmp_path, status="UNAVAILABLE")
        result = verify_input_manifest(manifest, tmp_path)
        assert result.ok is True
        assert list(result.ready_artifact_ids) == []
        assert list(result.unavailable_artifact_ids) == ["test_payload"]
        assert result.can_run_primary is False

    def test_absent_required_id_is_a_hard_error(self, tmp_path):
        manifest = _manifest(tmp_path)
        result = verify_input_manifest(
            manifest, tmp_path, expected_contract_sha256=C.contract_hash(),
            required_artifact_ids=["missing_primary"])
        assert result.ok is False
        assert result.can_run_primary is False
        assert any("absent" in error for error in result.errors)

    def test_missing_root_is_not_an_acceptable_empty_handoff(self, tmp_path):
        manifest = _manifest(tmp_path)
        result = verify_input_manifest(
            manifest, tmp_path / "does-not-exist",
            expected_contract_sha256=C.contract_hash(),
            required_artifact_ids=["test_payload"])
        assert result.ok is False
        assert result.checks["root_exists"] is False

    def test_downloader_pretty_hash_is_explicit_compatibility_only(self, tmp_path):
        manifest = _manifest(tmp_path)
        from nepal.framework_v1.input_manifest import pretty_input_manifest_hash
        manifest["manifest_sha256"] = pretty_input_manifest_hash(manifest)
        result = verify_input_manifest(
            manifest, tmp_path, expected_contract_sha256=C.contract_hash())
        assert result.ok is True
        assert result.can_run_primary is False
        assert result.checks["canonical_manifest_authorized"] is False
        assert result.checks["manifest_hash_encoding"] == \
            "reconciled_pretty_json_compatibility"
        assert any("compatibility" in warning for warning in result.warnings)

    def test_ghsl_binary_declaration_is_rejected_for_built_up_surface(self):
        spec = C.PHASE_ARTIFACT_CONTRACTS["B"]["ghsl_built_up_surface"]
        artifact = {
            "artifact_id": "ghsl_built_up_surface",
            "role": spec["role"],
            "kind": spec["kind"],
            "status": "READY",
            "crs": spec["crs"],
            "source_semantics": spec["source_semantics"],
            "target_grid": {"width": 300, "height": 300,
                            "resolution_m": 100, "crs": "EPSG:32645"},
            "units": "binary (1=built-up, 0=not)",
            "value_domain": "binary_01",
        }
        problems = validate_phase_artifact(artifact, "B")
        assert any("units" in problem or "m2" in problem
                   for problem in problems)

    def test_provisional_is_not_primary_consumable(self, tmp_path):
        manifest = _manifest(tmp_path, status="PROVISIONAL")
        result = verify_input_manifest(manifest, tmp_path)
        assert result.ok is True
        assert result.can_run_primary is False
        assert any("PROVISIONAL" in warning for warning in result.warnings)

    @pytest.mark.parametrize("relative_path", [
        "../outside.bin", "/absolute.bin", "nested/../../outside.bin",
    ])
    def test_path_escape_is_hard_error(self, tmp_path, relative_path):
        manifest = _manifest(tmp_path, relative_path=relative_path)
        result = verify_input_manifest(manifest, tmp_path)
        assert result.ok is False
        assert any("path" in error.lower() for error in result.errors)

    def test_symlink_artifact_is_rejected(self, tmp_path):
        outside = tmp_path.parent / "outside-payload.bin"
        outside.write_bytes(b"payload")
        path = tmp_path / "payload.bin"
        path.symlink_to(outside)
        manifest = _manifest(tmp_path, status="UNAVAILABLE")
        manifest["artifacts"][0].update({
            "relative_path": "payload.bin",
            "status": "READY",
            "sha256": hashlib.sha256(b"payload").hexdigest(),
            "bytes": 7,
        })
        manifest["manifest_sha256"] = canonical_input_manifest_hash(manifest)
        result = verify_input_manifest(manifest, tmp_path)
        assert result.ok is False
        assert any("symlink" in error.lower() for error in result.errors)

    def test_raw_slc_file_under_root_is_rejected_even_if_unlisted(self, tmp_path):
        manifest = _manifest(tmp_path)
        raw = tmp_path / "sentinel1" / "S1D_IW_SLC__1SDV_20260101T000000.SAFE"
        raw.parent.mkdir()
        raw.write_bytes(b"never store raw SLC")
        result = verify_input_manifest(manifest, tmp_path)
        assert result.ok is False
        assert any("raw SLC" in error for error in result.errors)

    def test_duplicate_artifact_ids_and_paths_fail(self, tmp_path):
        manifest = _manifest(tmp_path)
        duplicate = dict(manifest["artifacts"][0])
        duplicate["artifact_id"] = "other"
        manifest["artifacts"].append(duplicate)
        manifest["artifact_count"] = 2
        manifest["manifest_sha256"] = canonical_input_manifest_hash(manifest)
        result = verify_input_manifest(manifest, tmp_path)
        assert result.ok is False
        assert any("duplicate" in error.lower() for error in result.errors)

    def test_required_artifact_must_have_requested_primary_role(self, tmp_path):
        manifest = _manifest(tmp_path)
        result = verify_input_manifest(
            manifest, tmp_path,
            expected_contract_sha256=C.contract_hash(),
            expected_framework_contract_sha256=C.contract_hash(),
            required_artifact_ids=["test_payload"],
            required_role="B",
        )
        assert result.can_run_primary is False
        assert any("role" in error.lower() for error in result.errors)

    def test_phase_artifact_contract_checks_role_kind_source_and_target_grid(self):
        artifact = {
            "artifact_id": "ghsl_built_up_surface",
            "role": "A",
            "kind": "vector",
            "relative_path": "exposure/buildings.shp",
            "status": "READY",
            "sha256": "a" * 64,
            "bytes": 1,
            "crs": "EPSG:4326",
            "units": "binary",
            "source_semantics": "OSM substitute",
            "processing": "substitute; needs target grid",
        }
        problems = validate_phase_artifact(artifact, "B")
        assert any("role" in problem for problem in problems)
        assert any("kind" in problem for problem in problems)
        assert any("source" in problem.lower() for problem in problems)
        assert any("target" in problem.lower() for problem in problems)

    def test_ready_substitute_is_not_phase_consumable(self):
        artifact = {
            "artifact_id": "ghsl_built_up_surface",
            "role": "B",
            "kind": "raster",
            "relative_path": "exposure/ghsl.tif",
            "status": "READY",
            "sha256": "a" * 64,
            "bytes": 1,
            "crs": "EPSG:32645",
            "units": "binary",
            "source_semantics": "GHSL built-up surface",
            "target_grid": {"width": 300, "height": 300,
                            "resolution_m": 100, "crs": "EPSG:32645"},
            "processing": "GHSL substitute from OSM",
        }
        problems = validate_phase_artifact(artifact, "B")
        assert any("substitut" in problem.lower() for problem in problems)

    def test_ready_artifact_with_incomplete_reason_is_not_phase_consumable(self):
        artifact = {
            "artifact_id": "hanging_ice_support_grid",
            "role": "B",
            "kind": "raster",
            "status": "READY",
            "processing": "support proxy only; not independent detection",
            "incomplete_reason": "producer linkage is missing",
        }
        problems = validate_phase_artifact(artifact, "B")
        assert any("incomplete_reason" in problem for problem in problems)

    def test_required_artifact_waiver_blocks_strict_b(self):
        manifest = {
            "manifest_hash_encoding": "canonical_json",
            "canonical_hash_domain": "canonical_json_without_manifest_sha256",
            "data_contract_source_path": "nepal/feature_contract.py",
            "framework_contract_source_path": "nepal/framework_v1/contract.py",
            "data_contract_source_sha256": "a" * 64,
            "framework_contract_source_sha256": "b" * 64,
            "dirty_diff_sha256": "c" * 64,
            "code_revision": "test-revision",
            "package_inventory": [{
                "relative_path": "test.json",
                "sha256": "d" * 64,
                "bytes": 0,
            }],
            "waivers": [{
                "artifact_id": "dem_300x300_100m_32645",
                "claim_boundary": "must not authorize strict B",
                "status": "INCOMPLETE",
            }],
        }
        problems = _validate_b_manifest_declarations(manifest)
        assert any("waiver" in problem.lower() and "strict b" in problem.lower()
                   for problem in problems)

    def test_package_inventory_rejects_unlisted_files_but_honors_exclusions(
            self, tmp_path):
        listed = tmp_path / "listed.json"
        listed.write_text("listed", encoding="utf-8")
        (tmp_path / "unlisted.json").write_text("unlisted", encoding="utf-8")
        cache = tmp_path / "scripts" / "__pycache__"
        cache.mkdir(parents=True)
        (cache / "fixture.pyc").write_bytes(b"excluded")
        manifest = {
            "package_inventory": [{
                "relative_path": "listed.json",
                "sha256": hashlib.sha256(b"listed").hexdigest(),
                "bytes": len(b"listed"),
            }],
            "package_inventory_policy": {
                "excluded": [
                    "manifest.json",
                    "**/__pycache__/**",
                    "**/*.pyc",
                    "**/*.partial*",
                ],
            },
        }

        problems = _validate_package_inventory_files(tmp_path, manifest)

        assert any("unlisted.json" in problem for problem in problems)
        assert not any("fixture.pyc" in problem for problem in problems)

    def test_b_manifest_requires_an_explicit_inventory_policy(self):
        manifest = {
            "manifest_hash_encoding": "canonical_json",
            "canonical_hash_domain": "canonical_json_without_manifest_sha256",
            "data_contract_source_path": "nepal/feature_contract.py",
            "framework_contract_source_path": "nepal/framework_v1/contract.py",
            "data_contract_source_sha256": "a" * 64,
            "framework_contract_source_sha256": "b" * 64,
            "dirty_diff_sha256": "c" * 64,
            "code_revision": "test-revision",
            "package_inventory": [],
            "waivers": [],
        }

        problems = _validate_b_manifest_declarations(manifest)

        assert any("package_inventory_policy" in problem for problem in problems)

    def test_vector_bundle_requires_and_verifies_sidecars(self, tmp_path):
        main = tmp_path / "subset.shp"
        main.write_bytes(b"shp")
        artifact = {
            "artifact_id": "rgi15_fixed_box_subset",
            "role": "B", "kind": "vector", "relative_path": "subset.shp",
            "status": "READY", "sha256": hashlib.sha256(b"shp").hexdigest(),
            "bytes": 3,
            "bundle_files": [{"relative_path": "subset.shp",
                              "sha256": hashlib.sha256(b"shp").hexdigest(),
                              "bytes": 3}],
        }
        problems = validate_artifact_bundle(tmp_path, artifact)
        assert any("sidecar" in problem.lower() for problem in problems)

    def test_b_target_grid_contract_names_are_explicit(self):
        assert B_TARGET_GRID_ARTIFACT_IDS["infrastructure"]
        assert B_TARGET_GRID_ARTIFACT_IDS["river_connectivity"]

    def test_phase_manifest_binds_required_ids_and_role(self, tmp_path):
        manifest = _manifest(tmp_path)
        result = verify_phase_manifest(
            manifest, tmp_path, "B",
            expected_contract_sha256=C.contract_hash(),
            expected_framework_contract_sha256=C.contract_hash(),
            scan_root_for_raw_slc=False,
        )
        assert result.ok is False
        assert result.can_run_primary is False
        assert any("required artifact IDs are absent" in error
                   for error in result.errors)

    def test_b_contract_source_hashes_are_compared_to_authoritative_files(self,
                                                                            tmp_path):
        repo = tmp_path / "repo"
        handoff = repo / "data" / "handoff"
        (repo / "nepal" / "framework_v1").mkdir(parents=True)
        (repo / "nepal").mkdir(exist_ok=True)
        data_contract = repo / "nepal" / "feature_contract.py"
        framework_contract = repo / "nepal" / "framework_v1" / "contract.py"
        data_contract.write_bytes(b"data-contract")
        framework_contract.write_bytes(b"framework-contract")
        manifest = {
            "data_contract_source_path": "nepal/feature_contract.py",
            "framework_contract_source_path": "nepal/framework_v1/contract.py",
            "data_contract_source_sha256": hashlib.sha256(
                data_contract.read_bytes()).hexdigest(),
            "framework_contract_source_sha256": hashlib.sha256(
                framework_contract.read_bytes()).hexdigest(),
        }
        errors, diagnostics = _validate_contract_source_bindings(
            manifest, handoff, repo)
        assert errors == []
        assert diagnostics["bindings"]["data_contract_source_path"]["passed"] is True
        framework_contract.write_bytes(b"tampered")
        errors, _ = _validate_contract_source_bindings(manifest, handoff, repo)
        assert any("authoritative source" in error for error in errors)


def test_data_status_taxonomy_is_separate_from_output_statuses():
    assert DATA_STATUSES == frozenset({
        "READY", "INCOMPLETE", "INVALID", "UNAVAILABLE", "PROVISIONAL",
    })
    assert not DATA_STATUSES.intersection(C.OUTPUT_STATUSES)
