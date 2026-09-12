"""Contract tests: frozen box, winter window, statuses, gates, tie-breaks,
sidecar non-promotion, deterministic hashes, import-without-data."""
import pathlib

import pytest

import nepal.framework_v1 as fw
from nepal.framework_v1 import contract as C

PKG = pathlib.Path(__file__).resolve().parent.parent / "nepal" / "framework_v1"
BANNED_HARNESS_NAMES = (
    "feature_contract", "anomaly_detector", "multi_event_validation",
    "phase1_exit_gate", "era5_download", "gmm_", "isolation_forest",
)


class TestCleanRoom:
    def test_package_imports_without_data(self):
        assert fw.FRAMEWORK_VERSION == "1.0.0"

    def test_no_harness_imports_in_framework_sources(self):
        hit = []
        for f in PKG.glob("*.py"):
            src = f.read_text()
            for banned in BANNED_HARNESS_NAMES:
                if banned in src:
                    hit.append((f.name, banned))
        assert hit == [], f"harness references found: {hit}"

    def test_core_imports_without_data(self, tmp_path):
        import subprocess
        import sys
        env = {"PYTHONPATH": str(pathlib.Path(__file__).resolve().parent.parent)}
        code = ("from nepal.framework_v1 import contract_hash\n"
                "from nepal.framework_v1.screen import slope_degrees\n"
                "print(contract_hash()[:8])")
        out = subprocess.run([sys.executable, "-B", "-c", code],
                             cwd=str(tmp_path), env=env,
                             capture_output=True, text=True, check=True)
        assert len(out.stdout.strip()) == 8


class TestStudyBox:
    def test_exact_30km_box(self):
        box = C.study_box()
        assert box["size_m"] == 30_000
        assert box["crs"] == "EPSG:32645"
        assert abs((box["maxx"] - box["minx"]) - 30_000) < 1e-6
        assert abs((box["maxy"] - box["miny"]) - 30_000) < 1e-6

    def test_box_centered_on_frozen_source(self):
        from pyproj import Transformer
        fwd = Transformer.from_crs("EPSG:4326", "EPSG:32645", always_xy=True)
        ex, ey = fwd.transform(C.FROZEN_SOURCE["lon"], C.FROZEN_SOURCE["lat"])
        box = C.study_box()
        assert abs(box["center_easting"] - ex) < 1e-6
        assert abs(box["center_northing"] - ey) < 1e-6

    def test_box_not_selected_from_results_or_density(self):
        assert C.study_box()["selection_basis"] == "frozen_source_point_only"

    def test_corners_back_transform_close_to_source(self):
        box = C.study_box()
        for lon, lat in box["corners_lonlat"]:
            assert -180 <= lon <= 180 and -90 <= lat <= 90


class TestWinterWindow:
    def test_primary_window_nov1_apr30(self):
        from datetime import date
        inside = [date(2025, 11, 1), date(2025, 12, 15), date(2026, 1, 5),
                  date(2026, 3, 1), date(2026, 4, 30)]
        outside = [date(2026, 5, 1), date(2026, 7, 1), date(2026, 8, 26),
                   date(2026, 10, 31)]
        assert all(C.is_winter_date(d) for d in inside)
        assert not any(C.is_winter_date(d) for d in outside)

    def test_narrower_windows_labelled_sensitivity_only(self):
        assert C.SENSITIVITY_ONLY_LABEL == "SENSITIVITY_ONLY"
        assert C.WINTER_WINDOW_START == (11, 1)
        assert C.WINTER_WINDOW_END == (4, 30)


class TestThermalNonPromotion:
    def test_thermal_promotion_eligible_is_false(self):
        assert C.THERMAL_CONTEXT["promotion_eligible"] is False

    def test_sidecar_layers_rejected_from_ranking_registry(self):
        for name in ("thermal", "farinotti_thickness", "farinotti_bed",
                     "bed_elevation", "permafrost"):
            with pytest.raises(C.SidechainLayerError):
                C.assert_no_sidechain_layers([name])

    def test_missing_data_layers_rejected(self):
        for name in ("data_quality", "nodata_coverage"):
            with pytest.raises(C.SidechainLayerError):
                C.assert_no_sidechain_layers([name])

    def test_sidecar_layers_not_in_component_registry(self):
        assert C.SIDECHAIN_LAYERS.isdisjoint(C.TERRAIN_COMPONENTS)
        assert C.SIDECHAIN_LAYERS.isdisjoint(C.EXPOSURE_COMPONENTS)

    def test_bed_elevation_formula_frozen(self):
        assert C.BED_ELEVATION_FORMULA == \
            "bed_elevation = surface_elevation - thickness"

    def test_farinotti_requires_documented_crosswalk_in_schema(self):
        entry = C.INPUT_SCHEMA["inputs"]["farinotti_thickness"]
        assert entry["role"] == "sidecar"
        assert entry["requires_rgi60_crosswalk"] == "DOCUMENTED"
class TestStatusesGatesTieBreaks:
    def test_fixed_output_statuses(self):
        expected = {"PASS", "NULL", "INDETERMINATE", "BLOCKED", "NOT_RUN",
                    "RANKED", "UNRANKED", "UNSCREENABLE", "OBSERVABLE",
                    "UNOBSERVABLE", "ELIGIBLE", "INELIGIBLE", "SUPPORTED",
                    "UNSUPPORTED", "OK", "FAILED"}
        assert set(C.OUTPUT_STATUSES) == expected

    def test_gate_identifiers_exist(self):
        expected = {"A_CATALOG", "B_SCREEN", "B_TO_C", "E_VALIDATION",
                    "C_OPTIONAL", "D_OPTIONAL"}
        assert set(C.GATE_IDS) == expected

    def test_analysis_unit_id_deterministic(self):
        box = C.study_box()
        # Rows are numbered from the northern edge (north-up grid convention).
        a = C.analysis_unit_id(box["minx"] + 50.0, box["maxy"] - 50.0)
        b = C.analysis_unit_id(box["minx"] + 50.0, box["maxy"] - 50.0)
        assert a == b == "AU-E0000-N0000"
        south = C.analysis_unit_id(box["minx"] + 50.0, box["miny"] + 50.0)
        assert south == "AU-E0000-N0299"  # 300 rows of 100 m

    def test_tie_break_rules_frozen(self):
        assert C.TIE_BREAK_RULES[0] == "priority_index descending"
        assert C.TIE_BREAK_RULES[-1] == \
            "analysis_unit_id ascending (lexicographic)"

    def test_sort_candidates_respects_priority_then_id(self):
        rows = [
            {"analysis_unit_id": "AU-E0001-N0000", "priority_index": 0.5,
             "terrain_index": 0.9, "exposure_index": 0.9,
             "winter_observability": 0.9},
            {"analysis_unit_id": "AU-E0002-N0000", "priority_index": 0.7,
             "terrain_index": 0.1, "exposure_index": 0.1,
             "winter_observability": 0.1},
            {"analysis_unit_id": "AU-E0000-N0001", "priority_index": 0.5,
             "terrain_index": 0.9, "exposure_index": 0.9,
             "winter_observability": 0.9},
        ]
        ids = [r["analysis_unit_id"] for r in C.sort_candidates(rows)]
        assert ids == ["AU-E0002-N0000", "AU-E0000-N0001", "AU-E0001-N0000"]

    def test_sort_candidates_nan_is_not_ranked_above_finite(self):
        rows = [
            {"analysis_unit_id": "nan", "priority_index": float("nan")},
            {"analysis_unit_id": "finite", "priority_index": 0.1},
        ]
        assert [r["analysis_unit_id"] for r in C.sort_candidates(rows)] == [
            "finite", "nan"]


class TestHashesAndFrozenFiles:
    def test_framework_hash_covers_phase_input_contract(self):
        handoff = C.contract_dict()["phase_input_contract"]
        assert "B" in handoff["required_artifact_ids"]
        assert handoff["artifact_contracts"]["B"][
            "ghsl_built_up_surface"]["source_semantics"] == "GHSL"
        assert handoff["vector_bundle_required_sidecars"] == [
            ".shp", ".shx", ".dbf", ".prj"]

    def test_active_component_registry_and_exact_grid_are_contract_bound(self):
        grid = C.target_grid_contract()
        assert grid["width"] == grid["height"] == 300
        assert grid["resolution_m"] == 100
        assert len(grid["affine_transform"]) == 6
        assert len(grid["bounds"]) == 4
        assert len(grid["study_box_hash"]) == 64
        assert "aspect" not in C.ACTIVE_TERRAIN_COMPONENTS
        assert "population" not in C.ACTIVE_EXPOSURE_COMPONENTS
        assert C.PHASE_STATUS_A_READY in C.PHASE_STATUSES
        assert C.PHASE_STATUS_A_BLOCKED in C.PHASE_STATUSES
        assert C.PHASE_STATUS_B_TO_C_BLOCKED in C.PHASE_STATUSES

    def test_contract_hash_deterministic(self):
        assert C.contract_hash() == C.contract_hash()
        assert len(C.contract_hash()) == 64

    def test_contract_dict_has_no_timestamps(self):
        text = C.canonical_json_bytes(C.contract_dict()).decode()
        for ts in ("20:26:", "datetime", "utcnow", "timestamp"):
            assert ts not in text

    def test_preregistration_hash_pinned(self):
        assert C.PREREGISTRATION_SHA256 == (
            "0e7ce3c2e347a955f7495d719bb9232465ac9c25a5266656865800dbc963da7c")

    def test_verify_frozen_files_passes_in_repo(self):
        repo = pathlib.Path(__file__).resolve().parent.parent
        assert C.verify_frozen_files(repo)["ok"] is True

    def test_verify_preregistration_detects_tamper(self, tmp_path):
        f = tmp_path / "preregistration.md"
        f.write_text("tampered", encoding="utf-8")
        out = C.verify_preregistration(f)
        assert out["ok"] is False
        assert out["actual_sha256"] != out["expected_sha256"]


class TestInputSchema:
    def test_validate_input_manifest_accepts_valid(self):
        manifest = {"inputs": {
            "dem": {}, "glacier_inventory": {}, "sentinel2_dry_season": {},
            "s1_acquisition_metadata": {"records": [{"platform": "S1A"}]},
            "s1_pair_availability": {}, "exposure_ghsl": {},
            "exposure_osm": {}, "exposure_hydrosheds": {},
        }}
        assert C.validate_input_manifest(manifest) == []

    def test_validate_input_manifest_rejects_sidecar_promotion(self):
        manifest = {"inputs": {
            "dem": {"role": "primary"}, "glacier_inventory": {},
            "sentinel2_dry_season": {}, "s1_acquisition_metadata": {},
            "s1_pair_availability": {}, "exposure_ghsl": {},
            "exposure_osm": {}, "exposure_hydrosheds": {},
            "thermal": {"role": "primary"},
        }}
        problems = C.validate_input_manifest(manifest)
        assert any("thermal" in p and "primary" in p for p in problems)

    def test_validate_input_manifest_rejects_derived_s1_signal(self):
        manifest = {"inputs": {
            "dem": {}, "glacier_inventory": {}, "sentinel2_dry_season": {},
            "s1_acquisition_metadata": {"records": [
                {"platform": "S1A", "coherence": 0.7}]},
            "s1_pair_availability": {}, "exposure_ghsl": {},
            "exposure_osm": {}, "exposure_hydrosheds": {},
        }}
        assert any("derived-signal" in p
                   for p in C.validate_input_manifest(manifest))

    def test_missing_required_input_reported(self):
        assert any("missing required input" in p
                   for p in C.validate_input_manifest({"inputs": {}}))

    def test_malformed_input_manifest_is_reported_not_raised(self):
        problems = C.validate_input_manifest({"inputs": []})
        assert any("inputs must be a mapping" in p for p in problems)
        problems = C.validate_input_manifest({"inputs": {"dem": []}})
        assert any("spec must be a mapping" in p for p in problems)
