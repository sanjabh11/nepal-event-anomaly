"""V2 successor: explicit per-epoch coverage and bounded source metadata.

V1 remains immutable and is imported as the deterministic computation engine.
This wrapper adds an explicit missing-epoch flag, binds the V1 artifacts and
both code digests, records the Greater Himalaya inventory scope/runtime, and
seals a metadata-only screen of annual higher-frequency alternatives. It does
not acquire data or activate the event-association branch.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.metadata
import json
import platform
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import hma_lake_trajectory_poc as v1  # noqa: E402
from threadpoolctl import threadpool_limits  # noqa: E402

OUTPUT_ROOT = v1.INTAKE_ROOT / "hma-lake-trajectory-poc-v2"
SCHEMA_PREFLIGHT = "HMA_LAKE_TRAJECTORY_PREFLIGHT_V2"
SCHEMA_TRAJECTORIES = "HMA_LAKE_TRAJECTORIES_V2"
SCHEMA_PLAN = "FROZEN_EVAL_PLAN_V2"
SCHEMA_RESULT = "HMA_LAKE_CLUSTER_STABILITY_V2"
SCHEMA_HIGH_FREQUENCY = "HMA_HIGHER_FREQUENCY_ASSESSMENT_V0"
MISSING_EPOCH_FLAG = "NO_SOURCE_FEATURE_AT_EPOCH"
EVENT_BRANCH_STATUS = "DORMANT"

HIGHER_FREQUENCY_ASSESSMENT: dict[str, Any] = {
    "schema": SCHEMA_HIGH_FREQUENCY,
    "version": 0,
    "status": "METADATA_ONLY_REVIEW_COMPLETE",
    "review_date": "2026-09-29",
    "scope": "candidate-source fit only; not a data acquisition or analysis authorization",
    "payload_acquired": False,
    "event_association_branch": EVENT_BRANCH_STATUS,
    "candidates": [
        {
            "source": "Hi-MAG annual HMA glacial-lake inventory",
            "citation": "Chen et al. (2021), Earth System Science Data 13, 741-766",
            "citation_url": "https://essd.copernicus.org/articles/13/741/2021/",
            "dataset_doi_url": "https://doi.org/10.5281/zenodo.4275164",
            "reported_temporal_coverage": "annual, 2008-2017",
            "reported_mapping_resolution": "30 m Landsat",
            "reported_minimum_mapping_unit_km2": 0.0081,
            "reported_area_uncertainty": {
                "method": "plus_or_minus_one_pixel_boundary assumption",
                "reported_range_percent": [0.3, 50.0],
                "reported_mean_percent": 17.0,
            },
            "fit_assessment": "Best metadata-level candidate for a future broad-region annual trajectory extension.",
            "limitations": [
                "Annual inventory epochs do not resolve within-year event timing or duration.",
                "Cross-source lake identity, feature-ID continuity, exact coverage, and license/retention terms were not verified from payload bytes.",
                "Mapping threshold, acquisition/compositing choices, and delineation method differ from the pinned five-epoch Figshare inventory; direct concatenation is not justified.",
            ],
            "payload_or_network_request_made": False,
        },
        {
            "source": "Glacial Lake Observatory (GLO) annual Nepal/transboundary inventory",
            "citation": "Rawlins et al. (2026), Earth System Science Data 18, 5143-5165",
            "citation_url": "https://essd.copernicus.org/articles/18/5143/2026/",
            "dataset_doi_url": "https://doi.org/10.5281/zenodo.17802333",
            "reported_temporal_coverage": "annual, 2017-2024",
            "reported_study_domain": "59,602 km2 around glaciers within Nepal and transboundary catchments in India and China; Central Himalaya, not all HMA",
            "reported_sensors": ["Sentinel-1", "Sentinel-2"],
            "reported_validation": "F1 scores 0.80-0.92 across reported validation comparisons; not a guarantee for this study's cohort.",
            "fit_assessment": "Potential future Central Himalaya subregional comparator; not a replacement for the broad Figshare inventory.",
            "limitations": [
                "Geographic coverage is restricted to Nepal and associated transboundary catchments.",
                "Sensor-specific and combined detections require separate identity, coverage, and area-comparability review.",
                "Cross-source lake identity, exact field semantics, and dataset license/retention terms were not verified from payload bytes.",
            ],
            "payload_or_network_request_made": False,
        },
    ],
    "decision": {
        "preferred_future_metadata_candidate": "Hi-MAG, conditional on a separately approved and preregistered harmonization pilot",
        "secondary_future_candidate": "GLO for a separately scoped Central Himalaya subregional comparison",
        "current_clustering_inputs_changed": False,
        "new_acquisition_authorized": False,
        "post_result_source_switch_prohibited": True,
        "future_use_requires": [
            "separate owner approval for any payload intake",
            "source/version/license and retention verification",
            "spatial/temporal coverage and identity crosswalk",
            "new cohort and frozen evaluation contract before clustering",
        ],
    },
}


def _sha256_file(path: Path) -> str:
    return v1.sha256_file(path)


def _runtime_environment() -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "numpy": v1.np.__version__,
        "scikit_learn": importlib.metadata.version("scikit-learn"),
        "threadpoolctl": importlib.metadata.version("threadpoolctl"),
        "numerical_thread_limit": 1,
    }


def _verified_v1_bindings() -> dict[str, str]:
    expected_schemas = {
        "preflight": v1.SCHEMA_PREFLIGHT,
        "trajectories": v1.SCHEMA_TRAJECTORIES,
        "plan": v1.SCHEMA_PLAN,
        "result": v1.SCHEMA_RESULT,
    }
    bindings: dict[str, str] = {}
    for key, path in v1._artifact_paths(v1.OUTPUT_ROOT).items():
        document, digest = v1._read_bound_json(path)
        if document.get("schema") != expected_schemas[key]:
            raise ValueError(f"unexpected V1 predecessor schema: {path.name}")
        bindings[path.name] = digest
    if len(bindings) != 4:
        raise ValueError("V1 predecessor artifact set is incomplete")
    return bindings


def _coverage_flags(trajectory_doc: dict[str, Any]) -> dict[str, Any]:
    per_epoch: dict[str, Counter[str]] = {
        str(epoch): Counter() for epoch in v1.linkage.EPOCHS
    }
    observations_seen = 0
    for trajectory in trajectory_doc.get("trajectories", []):
        observations = trajectory.get("observations")
        if not isinstance(observations, list) or len(observations) != len(v1.linkage.EPOCHS):
            raise ValueError("each candidate path must expose one observation slot per epoch")
        if [row.get("epoch") for row in observations] != list(v1.linkage.EPOCHS):
            raise ValueError("candidate path observation epochs are missing, duplicated, or reordered")
        for observation in observations:
            if not observation.get("source_feature_present"):
                observation["coverage_flag"] = MISSING_EPOCH_FLAG
            elif not observation.get("coverage_flag"):
                observation["coverage_flag"] = "OBSERVED_FEATURE_INCOMPLETE_MEASUREMENT"
            if observation["coverage_flag"] == MISSING_EPOCH_FLAG:
                if observation.get("feature_id") is not None:
                    raise ValueError("missing-epoch flag contradicts a source feature ID")
                if observation.get("area_m2") is not None:
                    raise ValueError("missing-epoch flag contradicts a measured area")
            per_epoch[str(observation["epoch"])][observation["coverage_flag"]] += 1
            observations_seen += 1
    expected = len(trajectory_doc.get("trajectories", [])) * len(v1.linkage.EPOCHS)
    if observations_seen != expected:
        raise ValueError("coverage flags do not account for every trajectory-epoch slot")
    return {
        "contract": {
            "one_explicit_flag_per_candidate_path_and_epoch": True,
            "missing_source_feature_flag": MISSING_EPOCH_FLAG,
            "missing_epoch_is_not_zero_area": True,
        },
        "trajectory_epoch_slots": observations_seen,
        "counts_by_epoch_and_flag": {
            epoch: dict(sorted(counts.items())) for epoch, counts in per_epoch.items()
        },
    }


def _inventory_domain(trajectory_doc: dict[str, Any]) -> dict[str, Any]:
    regions = sorted({
        str(row.get("region_source") or "UNKNOWN")
        for trajectory in trajectory_doc.get("trajectories", [])
        for row in trajectory.get("observations", [])
        if row.get("source_feature_present")
    })
    return {
        "source_inventory_name": "Greater Himalaya glacial lake inventory",
        "observed_source_region_labels": regions,
        "claim_limit": "Results describe candidate paths in the pinned Greater Himalaya inventory domain; they are not a census or result for every High Mountain Asia subregion.",
        "full_hma_coverage_claimed": False,
    }


def _artifact_paths(output_root: Path) -> dict[str, Path]:
    return {
        "preflight": output_root / "HMA_LAKE_TRAJECTORY_PREFLIGHT_V2.json",
        "trajectories": output_root / "HMA_LAKE_TRAJECTORIES_V2.json",
        "higher_frequency": output_root / "HMA_HIGHER_FREQUENCY_ASSESSMENT_V0.json",
        "plan": output_root / "FROZEN_EVAL_PLAN_V2.json",
        "result": output_root / "HMA_LAKE_CLUSTER_STABILITY_V2.json",
    }


def _prepare(output_root: Path) -> tuple[dict[str, Any], ...]:
    v1_bindings = _verified_v1_bindings()
    v0_bindings = v1._prior_artifact_digests()
    inputs = v1._load_and_validate_inputs(replay_source=True)
    preflight, trajectory_doc, _ = v1._preflight_and_trajectory(inputs, v0_bindings)

    # Correct only the explicit coverage contract and source-scope wording.
    preflight = copy.deepcopy(preflight)
    trajectory_doc = copy.deepcopy(trajectory_doc)
    coverage = _coverage_flags(trajectory_doc)
    domain = _inventory_domain(trajectory_doc)
    preflight.update({
        "schema": SCHEMA_PREFLIGHT,
        "version": 2,
        "coverage_flag_audit": coverage,
        "inventory_domain": domain,
        "higher_frequency_assessment_status": "METADATA_ONLY_REVIEW_COMPLETE",
        "event_association_branch": EVENT_BRANCH_STATUS,
        "successor_of": v1_bindings,
    })
    trajectory_doc.update({
        "schema": SCHEMA_TRAJECTORIES,
        "version": 2,
        "coverage_flag_audit": coverage,
        "inventory_domain": domain,
        "event_association_branch": EVENT_BRANCH_STATUS,
        "successor_of": v1_bindings,
    })
    high_frequency = copy.deepcopy(HIGHER_FREQUENCY_ASSESSMENT)
    high_frequency["inventory_domain"] = domain

    paths = _artifact_paths(output_root)
    preflight_sha = _sha256_file(paths["preflight"]) if paths["preflight"].is_file() else None
    # The dependent artifact hashes are completed in run()/verify() after the
    # write-once preflight/trajectory and metadata-only review are bound.
    return (preflight, trajectory_doc, high_frequency, v1_bindings,
            inputs, preflight_sha)


def _make_plan(trajectory_doc: dict[str, Any], trajectory_sha: str,
               preflight_sha: str, higher_frequency_sha: str,
               v1_bindings: dict[str, str]) -> dict[str, Any]:
    plan = v1.frozen_plan(trajectory_doc, trajectory_sha, preflight_sha)
    engine_sha = _sha256_file(Path(v1.__file__))
    plan.update({
        "schema": SCHEMA_PLAN,
        "version": 2,
        "analysis_code_sha256": _sha256_file(Path(__file__)),
        "engine_code_sha256": engine_sha,
        "runtime_environment": _runtime_environment(),
        "preflight_artifact": {
            "schema": SCHEMA_PREFLIGHT,
            "sha256": preflight_sha,
        },
        "trajectory_artifact": {
            "schema": SCHEMA_TRAJECTORIES,
            "sha256": trajectory_sha,
        },
        "higher_frequency_assessment_artifact": {
            "schema": SCHEMA_HIGH_FREQUENCY,
            "sha256": higher_frequency_sha,
        },
        "successor_of": v1_bindings,
        "inventory_domain": _inventory_domain(trajectory_doc),
        "coverage_flag_contract": trajectory_doc["coverage_flag_audit"]["contract"],
        "event_association_branch": EVENT_BRANCH_STATUS,
    })
    plan["not_run_or_not_authorized"]["event_label_association"] = "DORMANT_SEPARATE_BRANCH"
    plan["not_run_or_not_authorized"]["new_data_acquisition"] = False
    return plan


def _publish_or_match(path: Path, document: dict[str, Any]) -> str:
    return v1._publish_or_match(path, document)


def _load_existing_exact(path: Path, expected: dict[str, Any]) -> str:
    return v1._load_existing_exact(path, expected)


def run(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    preflight, trajectory_doc, higher_frequency, v1_bindings, _, _ = _prepare(output_root)
    paths = _artifact_paths(output_root)
    preflight_sha = _publish_or_match(paths["preflight"], preflight)
    trajectory_sha = _publish_or_match(paths["trajectories"], trajectory_doc)
    higher_frequency_sha = _publish_or_match(paths["higher_frequency"], higher_frequency)
    plan = _make_plan(trajectory_doc, trajectory_sha, preflight_sha,
                      higher_frequency_sha, v1_bindings)
    plan_sha = _publish_or_match(paths["plan"], plan)
    # The immutable numeric contract is written before clustering begins.
    with threadpool_limits(limits=1):
        result = v1.evaluate(trajectory_doc, plan)
    result.update({
        "schema": SCHEMA_RESULT,
        "version": 2,
        "analysis_code_sha256": _sha256_file(Path(__file__)),
        "engine_code_sha256": _sha256_file(Path(v1.__file__)),
        "runtime_environment": _runtime_environment(),
        "frozen_eval_plan_sha256": plan_sha,
        "event_association_branch": EVENT_BRANCH_STATUS,
        "higher_frequency_assessment_sha256": higher_frequency_sha,
    })
    result_sha = _publish_or_match(paths["result"], result)
    return {
        "status": "HMA_LAKE_TRAJECTORY_POC_V2_COMPLETE",
        "source_replay": "EXACT_MATCH",
        "preflight": preflight["status"],
        "inventory_domain": preflight["inventory_domain"],
        "candidate_trajectories": trajectory_doc["source_feature_conservation"]["candidate_trajectory_count"],
        "complete_evaluation_paths": plan["cohort"]["candidate_path_count"],
        "numeric_stability_result": result["status"],
        "event_association_branch": EVENT_BRANCH_STATUS,
        "artifacts": {
            key: {"path": str(path), "sha256": digest}
            for key, path, digest in (
                ("preflight", paths["preflight"], preflight_sha),
                ("trajectories", paths["trajectories"], trajectory_sha),
                ("higher_frequency_assessment", paths["higher_frequency"], higher_frequency_sha),
                ("frozen_eval_plan", paths["plan"], plan_sha),
                ("stability_result", paths["result"], result_sha),
            )
        },
    }


def verify(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    preflight, trajectory_doc, higher_frequency, v1_bindings, _, _ = _prepare(output_root)
    paths = _artifact_paths(output_root)
    preflight_sha = _load_existing_exact(paths["preflight"], preflight)
    trajectory_sha = _load_existing_exact(paths["trajectories"], trajectory_doc)
    higher_frequency_sha = _load_existing_exact(paths["higher_frequency"], higher_frequency)
    plan = _make_plan(trajectory_doc, trajectory_sha, preflight_sha,
                      higher_frequency_sha, v1_bindings)
    plan_sha = _load_existing_exact(paths["plan"], plan)
    with threadpool_limits(limits=1):
        result = v1.evaluate(trajectory_doc, plan)
    result.update({
        "schema": SCHEMA_RESULT,
        "version": 2,
        "analysis_code_sha256": _sha256_file(Path(__file__)),
        "engine_code_sha256": _sha256_file(Path(v1.__file__)),
        "runtime_environment": _runtime_environment(),
        "frozen_eval_plan_sha256": plan_sha,
        "event_association_branch": EVENT_BRANCH_STATUS,
        "higher_frequency_assessment_sha256": higher_frequency_sha,
    })
    result_sha = _load_existing_exact(paths["result"], result)
    if plan["event_association_branch"] != EVENT_BRANCH_STATUS \
            or result["event_association_branch"] != EVENT_BRANCH_STATUS:
        raise ValueError("event-association branch must remain dormant")
    return {
        "status": "HMA_LAKE_TRAJECTORY_V2_VERIFY_OK",
        "source_replay": "EXACT_MATCH",
        "replay": "EXACT_MATCH",
        "preflight_sha256": preflight_sha,
        "trajectory_sha256": trajectory_sha,
        "higher_frequency_assessment_sha256": higher_frequency_sha,
        "frozen_eval_plan_sha256": plan_sha,
        "stability_result_sha256": result_sha,
        "numeric_stability_result": result["status"],
        "event_association_branch": EVENT_BRANCH_STATUS,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("run", "verify"):
        child = sub.add_parser(command)
        child.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    args = parser.parse_args(argv)
    try:
        result = run(args.output_root) if args.command == "run" else verify(args.output_root)
    except (OSError, ValueError, KeyError, TypeError, IndexError,
            json.JSONDecodeError, ArithmeticError) as exc:
        print(f"HMA_LAKE_TRAJECTORY_V2_{args.command.upper()}_BLOCKED: {exc}")
        return 1
    print(result["status"])
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
