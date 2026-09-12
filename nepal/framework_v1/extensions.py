"""nepal.framework_v1.extensions — fail-closed C/D optional interfaces (v1).

C (precursor analysis) and D (runout sensitivity) are disabled in v1.  Every
interface returns explicit BLOCKED/NOT_RUN statuses — never numeric zero and
never a pass-through result.  The future implementation orders are pinned
here so that later versions cannot silently re-order the science gates.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from . import contract as C

D_FUTURE_IMPLEMENTATION_ORDER = (
    "1. Fahrboeschung/empirical envelope",
    "2. Calibration on training groups only",
    "3. r.avaflow sensitivity only if it improves the empirical baseline "
    "on training data",
    "4. Freeze parameters",
    "5. Evaluate holdouts without retuning",
    "6. Keep river/flood routing in a separate model",
)


def run_precursor_analysis(screen_artifact: Mapping,
                           product_manifest: Mapping) -> dict:
    """C interface — returns BLOCKED in v1, forever.

    A future v2 may return a precursor analysis only when ALL of these hold:
    B-to-C gate passed, candidate is one of the locked top-five, required
    derived products exist and are checksum-valid, and an acceleration or
    other observable change is actually present.  There is no seismic
    dependency, no Thame row, and no INV claim without visible acceleration.

    For transparency the would-be conditions are evaluated and reported, but
    ``status`` is always BLOCKED in v1."""
    would_pass = {
        "b_to_c_gate_passed": bool(screen_artifact.get(
            "b_to_c_gate_passed", False)),
        "candidate_in_locked_top_five": bool(screen_artifact.get(
            "candidate_in_locked_top_five", False)),
        "derived_products_exist_and_checksum_valid":
            _all_checksum_valid(product_manifest),
        "observable_change_present": bool(screen_artifact.get(
            "observable_change_present", False)),
    }
    return {
        "status": C.OutputStatus.BLOCKED.value,
        "gate": C.GateId.C_OPTIONAL.value,
        "reasons": [
            "C_DISABLED_IN_V1",
            "precursor analysis is outside the framework_v1 scope",
        ],
        "would_pass_conditions": would_pass,
        "exclusions": [
            "no seismic dependency",
            "no Thame rows",
            "no INV claim without visible acceleration",
        ],
    }


def run_runout_sensitivity(event_volume_range: Sequence,
                           dem_artifact: Mapping,
                           calibration_groups: Sequence) -> dict:
    """D interface — returns BLOCKED in v1.

    ``event_volume_range``, ``dem_artifact`` and ``calibration_groups`` are
    accepted for interface stability only; nothing is computed."""
    return {
        "status": C.OutputStatus.BLOCKED.value,
        "gate": C.GateId.D_OPTIONAL.value,
        "reason": "D_DISABLED_IN_V1",
        "future_implementation_order": list(D_FUTURE_IMPLEMENTATION_ORDER),
        "result": C.OutputStatus.NOT_RUN.value,
        "would_be_numeric_result": None,
    }


def _all_checksum_valid(manifest: Mapping) -> bool:
    files = manifest.get("files", {})
    if not files:
        return False
    from .provenance import verify_manifest, check_no_raw_slc_paths
    ok, _ = verify_manifest(manifest.get("root", "."), {
        "algorithm": manifest.get("algorithm"), "files": files,
        "manifest_sha256": manifest.get("manifest_sha256")})
    return ok and not check_no_raw_slc_paths(manifest)