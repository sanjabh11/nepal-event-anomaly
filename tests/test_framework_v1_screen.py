"""Phase B screen tests: terrain math, CRS/nodata/units validation, exposure
and S1 missing-input handling, observability vs HyP3 separation, UNRANKED
semantics, LOO sensitivity, B-to-C gate, sidecar non-promotion."""
import math
from datetime import date

import numpy as np
import pytest

from nepal.framework_v1 import contract as C
from nepal.framework_v1 import screen as S
from nepal.framework_v1.controls import ControlsConfig

CONFIG = ControlsConfig(min_detectable_size_m3=4.0e6,
                        expected_winter_acquisitions=20)


class TestTerrainMath:
    def test_slope_known_value(self):
        dem = np.array([[float(j) * 2.0 for j in range(6)]
                        for _ in range(6)])
        sl = S.slope_degrees(dem, 1.0, 1.0)
        expected = math.degrees(math.atan(2.0))
        assert abs(sl[2, 2] - expected) < 1e-6

    def test_slope_units_respect_resolution(self):
        dem = np.array([[float(j) * 2.0 for j in range(6)]
                        for _ in range(6)])
        sl = S.slope_degrees(dem, 2.0, 2.0)
        assert abs(sl[2, 2] - 45.0) < 1e-6

    def test_slope_flat_zero(self):
        sl = S.slope_degrees(np.zeros((5, 5)), 1.0, 1.0)
        assert abs(sl[2, 2]) < 1e-9

    def test_slope_nodata_masked(self):
        dem = np.zeros((5, 5))
        dem[2, 2] = -9999.0
        sl = S.slope_degrees(dem, 1.0, 1.0, nodata=-9999.0)
        assert math.isnan(sl[2, 2])

    def test_aspect_east_facing_downhill(self):
        dem = np.array([[float(j) * 2.0 for j in range(6)]
                        for _ in range(6)])
        asp = S.aspect_degrees(dem, 1.0, 1.0)
        assert abs(asp[2, 2] - 270.0) < 1e-6

    def test_aspect_flat_flag(self):
        asp = S.aspect_degrees(np.zeros((5, 5)), 1.0, 1.0)
        assert asp[2, 2] == S.FLAT_ASPECT_NODATA

    def test_local_relief(self):
        dem = np.zeros((9, 9))
        dem[4, 4] = 100.0
        relief = S.local_relief(dem, window=9)
        assert abs(relief[4, 4] - 100.0) < 1e-9

    def test_roughness_and_curvature_sign(self):
        flat = np.zeros((7, 7))
        assert abs(S.roughness(flat)[3, 3]) < 1e-9
        pit = np.zeros((7, 7))
        pit[3, 3] = -50.0  # bowl: convex up -> positive Laplacian
        assert S.curvature(pit, 1.0, 1.0)[3, 3] > 0
        peak = np.zeros((7, 7))
        peak[3, 3] = 50.0   # local maximum -> negative Laplacian
        assert S.curvature(peak, 1.0, 1.0)[3, 3] < 0

    def test_percentile_rank_strictly_positive(self):
        pr = S.percentile_rank(np.array([3.0, 1.0, 2.0]))
        assert pr.min() > 0 and pr.max() <= 1
        assert math.isclose(pr.sum(), 1.5)

    def test_percentile_rank_matches_tie_aware_reference(self):
        values = np.array([4.0, 1.0, 1.0, 9.0, np.nan, 4.0])
        finite = values[np.isfinite(values)]
        expected = np.full(values.shape, np.nan)
        for index, value in enumerate(values):
            if np.isfinite(value):
                below = np.sum(finite < value)
                ties = np.sum(finite == value)
                expected[index] = (below + 0.5 * ties) / finite.size
        np.testing.assert_allclose(S.percentile_rank(values), expected,
                                   equal_nan=True)

    def test_percentile_rank_90k_continuous_values_is_bounded(self):
        import time
        values = np.linspace(0.5, 90_000.5, 90_000)
        started = time.perf_counter()
        result = S.percentile_rank(values)
        elapsed = time.perf_counter() - started
        assert result.shape == values.shape
        assert np.isfinite(result).all()
        assert elapsed < 5.0

    def test_geometric_mean_none_when_unsupported(self):
        assert S.geometric_mean([]) is None
        assert S.geometric_mean([None]) is None
        assert math.isclose(S.geometric_mean([0.5, 0.25]),
                            math.sqrt(0.125))


class TestDemValidation:
    def test_geographic_crs_rejected(self):
        problems = S.validate_dem_metadata("EPSG:4326", [30.0, 30.0],
                                           "degrees", None)
        assert any("not meters" in p for p in problems)
        assert any("geographic" in p for p in problems)

    def test_wrong_projected_crs_rejected(self):
        problems = S.validate_dem_metadata("EPSG:32644", [10.0, 10.0],
                                           "meters", -9999.0)
        assert any("not the required" in p for p in problems)

    def test_valid_projected_meters_passes(self):
        assert S.validate_dem_metadata("EPSG:32645", [10.0, 10.0],
                                       "meters", -9999.0) == []

    def test_undeclared_nodata_rejected(self):
        assert any("nodata" in p for p in S.validate_dem_metadata(
            "EPSG:32645", [10.0, 10.0], "meters", None))

    def test_geographic_dem_is_rejected_before_window_read(self, tmp_path):
        rasterio = pytest.importorskip("rasterio")
        from rasterio.transform import from_origin
        path = tmp_path / "geographic.tif"
        with rasterio.open(path, "w", driver="GTiff", height=4, width=4,
                           count=1, dtype="float32", crs="EPSG:4326",
                           transform=from_origin(85, 29, 0.01, 0.01),
                           nodata=-9999.0) as dst:
            dst.write(np.ones((1, 4, 4), dtype="float32"))
        with pytest.raises(C.ContractViolation, match="geographic"):
            S.read_box_raster(path)

    def test_faridotti_sidecar_blocked_without_documented_crosswalk(self):
        out = S.farinotti_bed_sidecar(
            np.array([5000.0]), np.array([200.0]),
            "RGI2000-v7.0-G-15-05732", {})
        assert out["status"] == "BLOCKED" and out["bed_elevation"] is None

    def test_bed_sidecar_low_confidence_with_documented_crosswalk(self):
        out = S.farinotti_bed_sidecar(
            np.array([5000.0]), np.array([200.0]),
            "RGI2000-v7.0-G-15-05732",
            {"RGI2000-v7.0-G-15-05732": "DOCUMENTED"})
        assert out["status"] == "SIDECAR_LOW_CONFIDENCE"
        assert out["promotion_eligible"] is False
        assert out["bed_elevation"][0] == 4800.0


class TestMissingInputs:
    def test_missing_exposure_component_leaves_unranked(self):
        res = S.rank_cells([{
            "analysis_unit_id": "U1",
            "terrain_components": {"slope": 0.9, "aspect": 0.8},
            "exposure_components": {},
            "winter_observability": 0.75,
        }])
        assert res[0]["status"] == "UNRANKED"
        assert res[0]["priority_index"] is None
        assert res[0]["terrain_index"] is not None

    def test_missing_observability_leaves_unranked(self):
        res = S.rank_cells([{
            "analysis_unit_id": "U2",
            "terrain_components": {"slope": 0.9},
            "exposure_components": {"built_up": 0.5},
            "winter_observability": None,
        }])
        assert res[0]["status"] == "UNRANKED"

    def test_missing_component_never_imputed_zero(self):
        res = S.rank_cells([{
            "analysis_unit_id": "U3",
            "terrain_components": {"slope": 0.5},
            "exposure_components": {"built_up": 0.5},
            "winter_observability": 0.5,
        }])
        rec = res[0]
        # terrain has 1 supported component: geomean == that value
        assert rec["terrain_index"] == 0.5
        assert rec["priority_index"] == 0.5 * 0.5 * 0.5

    def test_strict_real_data_mode_requires_every_primary_component(self):
        complete = {
            "analysis_unit_id": "U-strict",
            "terrain_components": {"slope": 0.5, "local_relief": 0.5,
                                    "roughness": 0.5, "glacier_support": 0.5,
                                    "hanging_ice_support": 0.5},
            "exposure_components": {"built_up": 0.5, "population": 0.5,
                                     "infrastructure": 0.5,
                                     "river_connectivity": 0.5},
            "winter_observability": 0.5,
        }
        assert S.rank_cells([complete], require_all_components=True)[0][
            "status"] == "RANKED"

        incomplete = dict(complete)
        incomplete["terrain_components"] = {"slope": 0.5}
        assert S.rank_cells([incomplete], require_all_components=True)[0][
            "status"] == "UNRANKED"

    def test_aspect_is_not_averaged_as_primary_component(self):
        assert "aspect" not in C.TERRAIN_COMPONENTS
        assert "aspect" in C.TERRAIN_CONTEXT_COMPONENTS
        assert S.terrain_index_for_cell({"slope": 0.8, "aspect": 359.0}) == 0.8

    def test_misaligned_grids_fail_as_contract_errors(self):
        with pytest.raises(C.ContractViolation, match="does not match"):
            S.rank_box({"slope": np.ones((2, 2))},
                       {"built_up": np.ones((3, 2))}, 0.5)
        with pytest.raises(C.ContractViolation, match="two-dimensional"):
            S.rank_box({"slope": np.ones(4)}, {"built_up": np.ones(4)}, 0.5)

    def test_unscreenable_inventory_not_low_susceptibility(self):
        res = S.rank_cells([{"analysis_unit_id": "U4", "screenable": False,
                             "terrain_components": {}, "exposure_components": {}}])
class TestObservabilityVsHyp3:
    def test_winter_fraction_metadata_only(self):
        acqs = [_acq("2025-12-01", True),
                _acq("2025-07-01", True),
                _acq("2026-02-15", False),
                _acq("2026-04-30", True)]
        frac = S.winter_observability_fraction(acqs, 4, CONFIG)
        assert abs(frac - 2.0 / 4.0) < 1e-12

    def test_undefined_expectation_returns_none(self):
        from nepal.framework_v1.controls import ControlsConfig as _CC
        no_expectation = _CC()  # expected_winter_acquisitions defaults None
        assert S.winter_observability_fraction([], None, no_expectation) is None
        assert S.winter_observability_fraction([], 0, CONFIG) is None

    def test_hyp3_derived_records_rejected_as_b_features(self):
        md, rejected = S.separate_hyp3_signals([
            _acq("2025-12-01", True),
            dict(_acq("2025-12-02", True), coherence=0.7),
            dict(_acq("2025-12-03", True), displacement=3.1),
        ])
        assert len(md) == 1
        assert len(rejected) == 2
        assert all("not permitted" in r["reason"] for r in rejected)

    def test_metadata_required_fields(self):
        problems = S.validate_acquisition_record({"date": "2025-12-01"})
        assert "missing field 'platform'" in problems


def _acq(date_s, available):
    return {"platform": "S1A", "orbit": "D13", "frame": "42", "path": "013",
            "polarization": "VV VH", "date": date_s, "available": available}


BOX = {"minx": 0.0, "miny": 0.0}


def _redundant_grids():
    field = np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])
    terrain = {"slope": field, "aspect": field * 1.3,
               "local_relief": field * 2.0, "roughness": field * 0.7}
    exposure = {"built_up": field, "population": field + 10.0,
                "infrastructure": field * 4.0,
                "river_connectivity": field * 0.5}
    return terrain, exposure


class TestRanking:
    def test_redundant_layers_fully_ranked(self):
        t, e = _redundant_grids()
        res = S.rank_box(t, e, winter_obs=0.75, box=BOX)
        assert res["n_screened"] == 6 and res["n_unranked"] == 0
        assert len(res["top_five"]) == 5

    def test_priority_index_product_of_three(self):
        t, e = _redundant_grids()
        rec = S.rank_box(t, e, 0.75, box=BOX)["top_five"][0]
        assert abs(rec["priority_index"] - (rec["terrain_index"]
                                            * rec["exposure_index"]
                                            * 0.75)) < 1e-12

    def test_deterministic_ordering(self):
        t, e = _redundant_grids()
        ids1 = [r["analysis_unit_id"] for r in S.rank_box(t, e, 0.75,
                                                          box=BOX)["ranked"]]
        ids2 = [r["analysis_unit_id"] for r in S.rank_box(t, e, 0.75,
                                                          box=BOX)["ranked"]]
        assert ids1 == ids2

    def test_disclaimer_present(self):
        t, e = _redundant_grids()
        assert "not probability" in S.rank_box(t, e, 0.5,
                                               box=BOX)["disclaimer"]

    def test_sidecar_present_but_non_promoting(self):
        t, e = _redundant_grids()
        rs = np.random.RandomState
        side = {"thermal": rs(0).rand(3, 2) * 40 - 10,
                "farinotti_bed": rs(1).rand(3, 2) * 900,
                "permafrost": rs(2).rand(3, 2)}
        base = S.rank_box(t, e, 0.75, box=BOX)
        withside = S.rank_box(t, e, 0.75, box=BOX, sidecar_grids=side)
        assert [r["analysis_unit_id"] for r in base["ranked"]] == \
            [r["analysis_unit_id"] for r in withside["ranked"]]
        assert [r["priority_index"] for r in base["ranked"]] == \
            [r["priority_index"] for r in withside["ranked"]]
        assert withside["sidecar_summary"]["thermal"]["nodata_fraction"] == 0.0

    def test_sidecar_shape_must_match_primary_grid(self):
        t, e = _redundant_grids()
        with pytest.raises(C.ContractViolation, match="shape"):
            S.rank_box(t, e, 0.75, box=BOX,
                       sidecar_grids={"thermal": np.zeros((1, 1))})

    def test_sidecar_undeclared_key_rejected(self):
        t, e = _redundant_grids()
        with pytest.raises(C.SidechainLayerError):
            S.rank_box(t, e, 0.5, box=BOX,
                       sidecar_grids={"random_layer": np.zeros((3, 2))})

    def test_sidecar_in_component_registry_rejected(self):
        with pytest.raises(C.SidechainLayerError):
            S.terrain_index_for_cell({"slope": 0.9, "thermal": 0.1})
        with pytest.raises(C.SidechainLayerError):
            S.terrain_index_for_cell({"slope": 0.9, "nodata_coverage": 0.1})
class TestLeaveOneLayerOut:
    def test_strict_active_registry_excludes_context_and_optional_layers(self):
        t, e = _redundant_grids()
        loo = S.leave_one_layer_out_top5(
            t, e, 0.75, box=BOX,
            required_terrain_components=("slope", "local_relief", "roughness"),
            required_exposure_components=C.ACTIVE_EXPOSURE_COMPONENTS,
            active_only=True,
        )
        assert "aspect" not in loo
        assert "population" not in loo
        assert set(loo) == {"slope", "local_relief", "roughness",
                            "built_up", "infrastructure",
                            "river_connectivity"}

    def test_redundant_layers_top5_stable(self):
        t, e = _redundant_grids()
        res = S.rank_box(t, e, 0.75, box=BOX)
        top5 = [r["analysis_unit_id"] for r in res["top_five"]]
        loo = S.leave_one_layer_out_top5(t, e, 0.75, box=BOX)
        assert loo and all(set(ids) == set(top5) for ids in loo.values())
        gate = S.evaluate_b_to_c_gate(a_gate_passed=True, box_inputs_valid=True,
                                      screen_result=res, controls_lock_ok=True,
                                      top5_ids=top5, loo_top5=loo,
                                      sidecar_noninfluence=True)
        assert gate["passed"] is True

    def test_decisive_layer_detected_by_loo_order(self):
        # Anti-correlating one exposure layer changes the top-5 ORDER of the
        # ranking; the leave-one-layer-out machinery records that order
        # difference even where the geomean keeps the same SET.
        field = np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])
        t = {"slope": field}
        e = {"built_up": field, "population": field}
        base_list = [r["analysis_unit_id"]
                     for r in S.rank_box(t, e, 0.75, box=BOX)["top_five"]]
        loo = S.leave_one_layer_out_top5(t, e, 0.75, box=BOX)
        assert loo["population"] == base_list
        e2 = dict(e)
        e2["population"] = np.array([[6.0, 5.0], [4.0, 3.0], [2.0, 1.0]])
        res2 = S.rank_box(t, e2, 0.75, box=BOX)
        res2_list = [r["analysis_unit_id"] for r in res2["top_five"]]
        loo2 = S.leave_one_layer_out_top5(t, e2, 0.75, box=BOX)
        assert loo2["population"] != res2_list
        assert set(loo2["population"]) == set(res2_list)

    def test_gate_blocks_when_A_failed_or_inputs_invalid(self):
        t, e = _redundant_grids()
        res = S.rank_box(t, e, 0.75, box=BOX)
        top5 = [r["analysis_unit_id"] for r in res["top_five"]]
        assert S.evaluate_b_to_c_gate(a_gate_passed=False,
                                      box_inputs_valid=True,
                                      screen_result=res,
                                      controls_lock_ok=True,
                                      top5_ids=top5)["passed"] is False
        assert S.evaluate_b_to_c_gate(a_gate_passed=True,
                                      box_inputs_valid=False,
                                      screen_result=res,
                                      controls_lock_ok=True,
                                      top5_ids=top5)["passed"] is False
        assert S.evaluate_b_to_c_gate(a_gate_passed=True,
                                      box_inputs_valid=True,
                                      screen_result=res,
                                      controls_lock_ok=False,
                                      top5_ids=top5)["passed"] is False

    def test_gate_reports_unstable_loo_layers(self):
        t, e = _redundant_grids()
        res = S.rank_box(t, e, 0.75, box=BOX)
        top5 = [r["analysis_unit_id"] for r in res["top_five"]]
        # Fabricate a LOO result that disagrees with the locked top five.
        other = [i for i in top5]
        other[0] = "AU-E9999-N9999"
        gate = S.evaluate_b_to_c_gate(a_gate_passed=True,
                                      box_inputs_valid=True,
                                      screen_result=res,
                                      controls_lock_ok=True,
                                      top5_ids=top5,
                                      loo_top5={"slope": other})
        assert gate["passed"] is False
        assert gate["checks"]["leave_one_layer_out_top5_stable"][
            "unstable_layers"] == ["slope"]

    def test_sidecar_noninfluence_must_be_true_for_gate(self):
        t, e = _redundant_grids()
        res = S.rank_box(t, e, 0.75, box=BOX)
        top5 = [r["analysis_unit_id"] for r in res["top_five"]]
        gate = S.evaluate_b_to_c_gate(a_gate_passed=True,
                                      box_inputs_valid=True,
                                      screen_result=res,
                                      controls_lock_ok=True,
                                      top5_ids=top5, loo_top5={},
                                      sidecar_noninfluence=False)
        assert gate["passed"] is False
        assert gate["checks"]["no_sidecar_layer_changes_result"][
            "passed"] is False
