"""C/D disabled-interface tests: fail-closed sentinels, no numeric results,
future implementation order pinned, raw-SLC manifests rejected."""
import pytest

from nepal.framework_v1.extensions import (run_precursor_analysis,
                                           run_runout_sensitivity,
                                           D_FUTURE_IMPLEMENTATION_ORDER)
from nepal.framework_v1.provenance import build_manifest


class TestPrecursorAnalysis:
    def test_returns_blocked_sentinel_even_when_conditions_met(self):
        out = run_precursor_analysis(
            {"b_to_c_gate_passed": True,
             "candidate_in_locked_top_five": True,
             "observable_change_present": True},
            {"files": {"derived.pkl": "checksum"}})
        assert out["status"] == "BLOCKED"
        assert out["gate"] == "C_OPTIONAL"
        assert "C_DISABLED_IN_V1" in out["reasons"]

    def test_would_be_conditions_evaluated_for_transparency(self):
        out = run_precursor_analysis(
            {"b_to_c_gate_passed": False,
             "candidate_in_locked_top_five": False,
             "observable_change_present": False},
            {"files": {}})
        assert out["would_pass_conditions"] == {
            "b_to_c_gate_passed": False,
            "candidate_in_locked_top_five": False,
            "derived_products_exist_and_checksum_valid": False,
            "observable_change_present": False,
        }
        assert out["status"] == "BLOCKED"

    def test_no_numeric_result_and_no_pass_through(self):
        out = run_precursor_analysis({}, {})
        assert out["status"] == "BLOCKED"
        assert not isinstance(out.get("result"), (int, float))
        for key in ("displacement", "coherence", "acceleration"):
            assert key not in out

    def test_exclusions_recorded(self):
        out = run_precursor_analysis({}, {})
        assert "no seismic dependency" in out["exclusions"]
        assert "no Thame rows" in out["exclusions"]
        assert "no INV claim without visible acceleration" in out["exclusions"]


class TestRunoutSensitivity:
    def test_returns_blocked_sentinel(self):
        out = run_runout_sensitivity([1.0e6, 5.0e6], {}, ["G1", "G2"])
        assert out["status"] == "BLOCKED"
        assert out["gate"] == "D_OPTIONAL"
        assert out["reason"] == "D_DISABLED_IN_V1"
        assert out["result"] == "NOT_RUN"

    def test_would_be_numeric_result_is_none(self):
        out = run_runout_sensitivity([1.0e6, 5.0e6], {}, [])
        assert out["would_be_numeric_result"] is None

    def test_future_implementation_order_pinned(self):
        out = run_runout_sensitivity([1.0e6], {}, [])
        assert out["future_implementation_order"] == \
            list(D_FUTURE_IMPLEMENTATION_ORDER)
        assert out["future_implementation_order"][0].startswith(
            "1. Fahrboeschung")
        assert "training groups only" in out["future_implementation_order"][1]
        assert "r.avaflow" in out["future_implementation_order"][2]
        assert "Freeze parameters" in out["future_implementation_order"][3]
        assert "without retuning" in out["future_implementation_order"][4]
        assert "separate model" in out["future_implementation_order"][5]

    def test_inputs_not_computed(self):
        # The interface accepts its arguments for stability but must not
        # compute anything from them.
        out = run_runout_sensitivity([1.0e6, 5.0e6], {"dem": object()},
                                     ["G1"])
        assert out["status"] == "BLOCKED"


class TestManifestHygieneAtInterfaces:
    def test_product_manifest_with_raw_slc_never_valid(self):
        from nepal.framework_v1.extensions import _all_checksum_valid
        manifest = build_manifest({"derived.pkl": b"x"},
                                  manifest_type="artifact")
        manifest["slc_path"] = "S1A_IW_SLC__1SDV_20250101T101010.SAFE"
        assert check_no_raw(manifest)

    def test_blocked_even_with_checksum_valid_products(self):
        # Even a clean, checksum-valid manifest cannot unlock C in v1.
        out = run_precursor_analysis(
            {"b_to_c_gate_passed": True, "candidate_in_locked_top_five": True,
             "observable_change_present": True}, _valid_manifest())
        assert out["status"] == "BLOCKED"


def _valid_manifest():
    from nepal.framework_v1.provenance import build_manifest, \
        verify_manifest
    import tempfile
    from pathlib import Path
    d = Path(tempfile.mkdtemp())
    f = d / "derived.json"
    f.write_bytes(b"{}")
    manifest = build_manifest({"derived.json": f}, manifest_type="artifact")
    ok, problems = verify_manifest(d, manifest)
    assert ok and not problems
    manifest["root"] = str(d)
    return manifest


def check_no_raw(manifest):
    from nepal.framework_v1.provenance import check_no_raw_slc_paths
    return check_no_raw_slc_paths(manifest)