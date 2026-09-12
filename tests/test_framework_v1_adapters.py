"""Integration-level tests for read-only reconciled-data B adapters."""
from __future__ import annotations

import json

import numpy as np
import pytest

from nepal.framework_v1 import contract as C
from nepal.framework_v1.adapters import (
    TargetGrid,
    _load_b_observability,
    build_b_screen,
    load_b_input_bundle,
    load_verified_b_input_bundle,
    per_unit_winter_observability,
    reproject_dem_to_target,
    read_target_raster,
    validate_inventory_geometry_crs,
    validate_target_array,
)
from nepal.framework_v1.controls import ControlsConfig, create_controls_lock
from nepal.framework_v1.provenance import bind_gate_artifact, verify_artifact_envelope


def _verified_manifest():
    return {
        "ok": True,
        "can_run_primary": True,
        "checks": {
            "manifest_sha256": "a" * 64,
            "self_hash_verified": True,
            "contract_bound": True,
            "framework_contract_bound": True,
            "framework_contract_runtime_bound": True,
            "canonical_manifest_authorized": True,
            "raw_slc_scan": "PASS",
        },
    }


def _verified_a_gate():
    return bind_gate_artifact({
        "gate_id": C.GateId.A_CATALOG.value,
        "passed": True,
        "checks": {},
    })


def _acquisition(date_s="2025-12-01", available=True):
    return {
        "platform": "S1A", "orbit": "D13", "frame": "42", "path": "013",
        "polarization": "VV VH", "date": date_s, "available": available,
        "analysis_unit_id": "AU-E0000-N0000",
    }


def _pair(date_s="2025-12-01", available=True):
    return {
        "date": date_s, "pair_available": available,
        "analysis_unit_id": "AU-E0000-N0000",
    }


class TestTargetAdapters:
    def test_target_grid_is_frozen_300_by_300(self):
        target = TargetGrid()
        assert target.validate() == []
        assert target.shape == (300, 300)
        assert target.crs == "EPSG:32645"

    def test_inventory_bad_projected_prj_is_rejected(self):
        problems = validate_inventory_geometry_crs(
            "EPSG:32645", [85.0, 28.0, 86.0, 29.0])
        assert any("look geographic" in problem for problem in problems)

    def test_geographic_dem_is_rejected_before_reprojection(self, tmp_path):
        rasterio = pytest.importorskip("rasterio")
        from rasterio.transform import from_origin
        path = tmp_path / "source.tif"
        with rasterio.open(path, "w", driver="GTiff", height=8, width=8,
                           count=1, dtype="float32", crs="EPSG:4326",
                           transform=from_origin(85, 29, 0.01, 0.01),
                           nodata=-9999.0) as dst:
            dst.write(np.ones((1, 8, 8), dtype="float32"))
        with pytest.raises(C.ContractViolation, match="geographic"):
            reproject_dem_to_target(str(path))

    def test_projected_dem_is_reprojected_to_exact_target(self, tmp_path):
        rasterio = pytest.importorskip("rasterio")
        from rasterio.transform import from_origin
        box = C.study_box()
        path = tmp_path / "source.tif"
        with rasterio.open(
            path, "w", driver="GTiff", height=300, width=300, count=1,
            dtype="float32", crs="EPSG:32645",
            transform=from_origin(box["minx"], box["maxy"], 100, 100),
            nodata=-9999.0,
        ) as dst:
            dst.write(np.ones((1, 300, 300), dtype="float32"))
        dem, meta = reproject_dem_to_target(str(path))
        assert dem.shape == (300, 300)
        assert np.isfinite(dem).all()
        assert meta["target_shape"] == [300, 300]

    def test_target_raster_reader_requires_frozen_alignment(self, tmp_path):
        rasterio = pytest.importorskip("rasterio")
        from rasterio.transform import from_origin
        box = C.study_box()
        path = tmp_path / "component.tif"
        with rasterio.open(
            path, "w", driver="GTiff", height=300, width=300, count=1,
            dtype="float32", crs="EPSG:32645",
            transform=from_origin(box["minx"], box["maxy"], 100, 100),
            nodata=-9999.0,
        ) as dst:
            values = np.ones((300, 300), dtype="float32")
            values[0, 0] = -9999.0
            dst.write(values, 1)
        loaded, meta = read_target_raster(str(path))
        assert loaded.shape == (300, 300)
        assert np.isnan(loaded[0, 0])
        assert meta["finite_fraction"] < 1.0

    def test_target_raster_reader_enforces_declared_value_domain(self, tmp_path):
        rasterio = pytest.importorskip("rasterio")
        from rasterio.transform import from_origin
        box = C.study_box()
        path = tmp_path / "binary.tif"
        with rasterio.open(
            path, "w", driver="GTiff", height=300, width=300, count=1,
            dtype="float32", crs="EPSG:32645",
            transform=from_origin(box["minx"], box["maxy"], 100, 100),
            nodata=-1.0,
        ) as dst:
            values = np.zeros((300, 300), dtype="float32")
            values[0, 0] = 2.0
            dst.write(values, 1)
        with pytest.raises(C.ContractViolation, match="binary domain"):
            read_target_raster(
                str(path),
                semantic=C.B_ARTIFACT_SEMANTICS[
                    "osm_infrastructure_grid_300x300_100m_32645"],
            )

    def test_declared_minus_one_nodata_is_not_treated_as_a_negative_signal(self):
        values = np.ones((300, 300), dtype=float)
        values[0, 0] = -1.0
        assert validate_target_array(
            "hanging_ice_support", values, semantic=C.B_ARTIFACT_SEMANTICS[
                "hanging_ice_support_grid"]) == []
        values[0, 0] = -2.0
        assert validate_target_array(
            "hanging_ice_support", values, semantic=C.B_ARTIFACT_SEMANTICS[
                "hanging_ice_support_grid"])


class TestPerUnitObservability:
    def test_strict_observability_parser_rejects_wrapper_values_alias(self, tmp_path):
        path = tmp_path / "observability.json"
        path.write_text(json.dumps({
            "grid": {"width": 300, "height": 300,
                     "resolution_m": 100, "crs": "EPSG:32645"},
            "values": {"AU-E0000-N0000": 1.0},
        }), encoding="utf-8")
        values, errors, _ = _load_b_observability(path, TargetGrid())
        assert values == {}
        assert any("nested observability_by_unit" in error for error in errors)

    def test_pair_observability_is_per_unit_and_winter_only(self):
        controls = ControlsConfig(expected_winter_pairs=2)
        values, diagnostics = per_unit_winter_observability(
            [_acquisition()],
            [_pair(available=True), _pair("2025-07-01", available=True)],
            controls,
        )
        assert values == {"AU-E0000-N0000": 0.5}
        assert diagnostics["mode"] == "per_analysis_unit"

    def test_hyp3_signal_is_hard_rejected(self):
        with pytest.raises(C.ContractViolation, match="HyP3"):
            per_unit_winter_observability(
                [dict(_acquisition(), coherence=0.8)], [_pair()],
                ControlsConfig(expected_winter_pairs=1),
            )

    def test_pair_availability_and_unit_ids_are_typed_and_bounded(self):
        with pytest.raises(C.ContractViolation, match="boolean"):
            per_unit_winter_observability(
                [_acquisition()],
                [dict(_pair(), pair_available="false")],
                ControlsConfig(expected_winter_pairs=1),
            )
        with pytest.raises(C.ContractViolation, match="invalid analysis unit IDs"):
            per_unit_winter_observability(
                [_acquisition()],
                [dict(_pair(), analysis_unit_id="face-unknown")],
                ControlsConfig(expected_winter_pairs=1),
            )


class TestStrictBAssembly:
    def test_caller_gate_boolean_cannot_authorize_b_without_a_artifact(self):
        result = build_b_screen(
            {}, {}, {},
            controls_lock=create_controls_lock(
                ControlsConfig(expected_winter_pairs=1)),
            a_gate_passed=True,
            manifest_verification=_verified_manifest(),
        )
        assert result["status"] == "BLOCKED"
        assert any("hash-bound A_CATALOG gate artifact" in error
                   for error in result["errors"])

    def test_missing_manifest_verification_blocks_real_data(self):
        result = build_b_screen(
            {}, {}, {},
            controls_lock=create_controls_lock(
                ControlsConfig(expected_winter_pairs=1)),
            a_gate_artifact=_verified_a_gate(),
        )
        assert result["status"] == "BLOCKED"
        assert any("manifest verification" in error
                   for error in result["errors"])

    def test_missing_optional_population_is_reported_not_imputed(self):
        shape = (300, 300)
        terrain = {name: np.ones(shape, dtype=float)
                   for name in C.TERRAIN_COMPONENTS}
        exposure = {name: np.ones(shape, dtype=float)
                    for name in C.REQUIRED_EXPOSURE_COMPONENTS}
        box = C.study_box()
        values = {}
        for row in range(300):
            for col in range(300):
                values[C.analysis_unit_id(
                    box["minx"] + (col + 0.5) * 100,
                    box["maxy"] - (row + 0.5) * 100,
                )] = 1.0
        result = build_b_screen(
            terrain, exposure, values,
            controls_lock=create_controls_lock(
                ControlsConfig(expected_winter_pairs=1)),
            a_gate_artifact=_verified_a_gate(),
            manifest_verification=_verified_manifest(),
        )
        assert result["optional_components_missing"] == ["population"]

    def test_b_screen_computes_gate_from_strict_inputs(self):
        shape = (300, 300)
        terrain = {name: np.ones(shape, dtype=float)
                   for name in C.TERRAIN_COMPONENTS}
        exposure = {name: np.ones(shape, dtype=float)
                    for name in C.EXPOSURE_COMPONENTS}
        uid = C.analysis_unit_id(C.study_box()["minx"] + 50,
                                 C.study_box()["maxy"] - 50)
        # A reduced observability map is intentionally incomplete: the
        # fail-closed result must not fabricate the other 89,999 units.
        blocked = build_b_screen(
            terrain, exposure, {uid: 1.0},
            controls_lock=create_controls_lock(
                ControlsConfig(expected_winter_pairs=1)),
            a_gate_artifact=_verified_a_gate(),
            manifest_verification=_verified_manifest(),
        )
        assert blocked["status"] == "BLOCKED"
        assert any("observability" in error for error in blocked["errors"])

    def test_b_screen_timeout_is_explicit_and_checkpointed(self, tmp_path):
        checkpoint = tmp_path / "b_checkpoint.json"
        result = build_b_screen(
            {}, {}, {},
            controls_lock=create_controls_lock(
                ControlsConfig(expected_winter_pairs=1)),
            a_gate_artifact=_verified_a_gate(),
            manifest_verification=_verified_manifest(),
            timeout_seconds=0.0,
            checkpoint_path=checkpoint,
        )
        assert result["status"] == C.B_TIMEOUT_STATUS
        assert result["gate_passed"] is False
        assert checkpoint.exists()
        assert checkpoint.read_text().find('"status":"TIMEOUT"') >= 0

    def test_b_screen_with_complete_map_has_explicit_gate(self):
        shape = (300, 300)
        terrain = {name: np.ones(shape, dtype=float)
                   for name in C.TERRAIN_COMPONENTS}
        exposure = {name: np.ones(shape, dtype=float)
                    for name in C.EXPOSURE_COMPONENTS}
        box = C.study_box()
        values = {}
        for row in range(300):
            for col in range(300):
                values[C.analysis_unit_id(
                    box["minx"] + (col + 0.5) * 100,
                    box["maxy"] - (row + 0.5) * 100,
                )] = 1.0
        result = build_b_screen(
            terrain, exposure, values,
            controls_lock=create_controls_lock(
                ControlsConfig(expected_winter_pairs=1)),
            a_gate_artifact=_verified_a_gate(),
            manifest_verification=_verified_manifest(),
            sidecar_grids={"thermal": np.zeros(shape)},
        )
        assert result["gate"]["gate_id"] == "B_TO_C"
        assert result["gate"]["checks"][
            "primary_components_complete"]["passed"] is True
        assert len(result["top_five"]) == 5
        assert result["status"] == C.PHASE_STATUS_SCREEN_RANKED
        assert result["phase_status"] == C.PHASE_STATUS_B_TO_C_READY
        assert verify_artifact_envelope(result) == (True, [])


def test_b_input_loader_is_read_only_and_fail_closed_without_manifest(tmp_path):
    before = sorted(path.relative_to(tmp_path).as_posix()
                    for path in tmp_path.rglob("*"))
    bundle = load_b_input_bundle(tmp_path)
    after = sorted(path.relative_to(tmp_path).as_posix()
                   for path in tmp_path.rglob("*"))
    assert bundle.status == "BLOCKED"
    assert any("manifest" in error.lower() for error in bundle.errors)
    assert before == after


def test_verified_b_loader_requires_both_explicit_contract_hashes(tmp_path):
    bundle = load_verified_b_input_bundle(
        tmp_path,
        expected_contract_sha256=None,
        expected_framework_contract_sha256=C.contract_hash(),
    )
    assert bundle.status == "BLOCKED"
    assert any("explicit" in error for error in bundle.errors)


def test_current_reconciled_handoff_stays_blocked_until_semantic_b_inputs_exist():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "data" / \
        "framework_inputs_v1_reconciled"
    bundle = load_b_input_bundle(root)
    assert bundle.status == "BLOCKED"
    assert bundle.verification is not None
    # The reconciled data lane still carries the pre-hardening framework hash;
    # a changed runtime contract must not be treated as implicitly compatible.
    assert bundle.verification.checks["framework_contract_runtime_bound"] is False
    assert bundle.verification.checks["framework_contract_declared"] is True
    assert any("framework contract" in error.lower() or
               "hanging" in error.lower() or "source" in error.lower()
               for error in bundle.errors)
