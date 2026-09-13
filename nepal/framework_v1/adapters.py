"""Read-only data adapters and the real-data Phase B assembly.

The adapters deliberately sit between the reconciled handoff and the pure
ranking functions.  They perform CRS/window/shape checks before allocating
large arrays, never use thermal/bed/permafrost sidecars to promote a unit, and
never accept HyP3-derived signal as B observability metadata.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

import numpy as np

from . import contract as C
from .controls import ControlsConfig, ControlsLock
from .input_manifest import (B_TARGET_GRID_ARTIFACT_IDS,
                              InputManifestVerification,
                              validate_artifact_semantics,
                              verify_phase_manifest)
from .provenance import (bind_artifact_envelope, bind_gate_artifact,
                         gate_input_artifact_sha256, verify_gate_input,
                         write_deterministic_json)
from .screen import (leave_one_layer_out_top5, rank_box, separate_hyp3_signals,
                     terrain_components_from_dem, validate_acquisition_record,
                     evaluate_b_to_c_gate)


class BExecutionTimeout(C.ContractViolation):
    """A bounded B run expired before a complete result was materialized."""


@dataclass(frozen=True)
class TargetGrid:
    """The only grid accepted by the v1 real-data B adapter."""

    width: int = C.STUDY_BOX_SIZE_M // C.ANALYSIS_UNIT_CELL_M
    height: int = C.STUDY_BOX_SIZE_M // C.ANALYSIS_UNIT_CELL_M
    resolution_m: int = C.ANALYSIS_UNIT_CELL_M
    crs: str = C.STUDY_BOX_CRS

    def validate(self) -> list[str]:
        problems: list[str] = []
        if self.width != 300 or self.height != 300:
            problems.append("target grid must be exactly 300 x 300 cells")
        if self.resolution_m != 100:
            problems.append("target grid resolution must be exactly 100 m")
        if self.crs != C.STUDY_BOX_CRS:
            problems.append(f"target grid CRS must be {C.STUDY_BOX_CRS}")
        return problems

    @property
    def shape(self) -> tuple[int, int]:
        return self.height, self.width

    def transform(self):
        try:
            from rasterio.transform import from_origin
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("rasterio is required for target-grid adapters") from exc
        box = C.study_box()
        return from_origin(box["minx"], box["maxy"],
                           self.resolution_m, self.resolution_m)


@dataclass(frozen=True)
class BInputBundle:
    """Validated, read-only inputs handed from the data lane to Phase B."""

    status: str
    verification: Optional[InputManifestVerification]
    errors: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    terrain_grids: Mapping[str, Any] = field(default_factory=dict)
    exposure_grids: Mapping[str, Any] = field(default_factory=dict)
    observability_by_unit: Mapping[str, Optional[float]] = field(default_factory=dict)
    artifact_paths: Mapping[str, str] = field(default_factory=dict)
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in {"READY", C.PHASE_STATUS_LOAD_READY} and not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "artifact_paths": dict(self.artifact_paths),
            "observability_units": len(self.observability_by_unit),
            "diagnostics": dict(self.diagnostics),
            "manifest_verification": (
                self.verification.to_dict() if self.verification is not None else None),
        }


def _load_b_observability(path: Path, target: TargetGrid) -> tuple[
        dict[str, Optional[float]], list[str], dict[str, Any]]:
    """Load an explicit per-unit observability map, never a summary statistic."""
    problems: list[str] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, [f"per-unit observability JSON is unreadable: {exc}"], {}
    if not isinstance(payload, Mapping):
        return {}, ["per-unit observability artifact must be an object"], {}
    grid = payload.get("grid")
    if not isinstance(grid, Mapping):
        problems.append("per-unit observability grid metadata is required")
    else:
        for key, expected in (("width", target.width), ("height", target.height),
                              ("resolution_m", target.resolution_m),
                              ("crs", target.crs)):
            if grid.get(key) != expected:
                problems.append(
                    f"per-unit observability grid {key} must be {expected!r}")
    values = payload.get("observability_by_unit")
    if not isinstance(values, Mapping):
        return {}, problems + [
            "nested observability_by_unit mapping is required; wrapper metadata or summary-only coverage is not consumable"], {}

    box = C.study_box()
    origin = (box["minx"], box["miny"])
    expected_ids = {
        C.analysis_unit_id(
            box["minx"] + (col + 0.5) * target.resolution_m,
            box["maxy"] - (row + 0.5) * target.resolution_m,
            target.resolution_m,
            origin,
        )
        for row in range(target.height)
        for col in range(target.width)
    }
    actual_ids = {str(key) for key in values}
    missing = sorted(expected_ids - actual_ids)
    extra = sorted(actual_ids - expected_ids)
    if missing:
        problems.append(
            f"per-unit observability mapping is incomplete ({len(missing)} units missing)")
    if extra:
        problems.append(
            f"per-unit observability mapping has {len(extra)} unexpected unit IDs")
    normalized: dict[str, Optional[float]] = {}
    for unit_id, value in values.items():
        uid = str(unit_id)
        if value is None:
            problems.append(f"observability for {uid!r} is missing")
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            problems.append(f"observability for {uid!r} is not numeric")
            continue
        if not np.isfinite(numeric) or not 0.0 <= numeric <= 1.0:
            problems.append(f"observability for {uid!r} must be within [0, 1]")
            continue
        normalized[uid] = numeric
    diagnostics = {
        "mode": "per_analysis_unit",
        "observable": "metadata_pair_coverage_fraction",
        "metadata_only": True,
        "grid": {"width": target.width, "height": target.height,
                  "resolution_m": target.resolution_m, "crs": target.crs},
        "units_expected": len(expected_ids),
        "units_received": len(actual_ids),
    }
    return normalized, problems, diagnostics


def load_b_input_bundle(
    root: str | Path,
    manifest: Optional[Mapping[str, Any]] = None,
    *,
    expected_contract_sha256: Optional[str] = None,
    expected_framework_contract_sha256: Optional[str] = None,
    controls_lock: Optional[ControlsLock] = None,
    repo_root: Optional[str | Path] = None,
    target: TargetGrid = TargetGrid(),
    manifest_path: Optional[str | Path] = None,
    trusted_manifest_file_sha256: Optional[str] = None,
    expected_candidate_generation_id: Optional[str] = None,
) -> BInputBundle:
    """Read and validate the reconciled B handoff without writing to it.

    The loader intentionally returns a blocked bundle for an incomplete or
    semantically stale handoff.  It does not repair metadata, derive missing
    products, resample misaligned rasters, or turn a coverage summary into a
    per-unit map.
    """
    root_path = Path(root)
    errors: list[str] = []
    warnings: list[str] = []
    artifact_paths: dict[str, str] = {}
    terrain: dict[str, Any] = {}
    exposure: dict[str, Any] = {}
    resolved_manifest_path: Optional[Path] = None
    observability: dict[str, Optional[float]] = {}
    diagnostics: dict[str, Any] = {}

    if manifest is None:
        resolved_manifest_path = (Path(manifest_path) if manifest_path
                                  is not None else root_path / "manifest.json")
        try:
            manifest = json.loads(
                resolved_manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            errors.append(
                f"B input manifest is missing: {resolved_manifest_path}")
        except (OSError, ValueError) as exc:
            errors.append(f"B input manifest is unreadable: {exc}")
    elif manifest_path is not None:
        # A caller-supplied mapping can never satisfy a file anchor: the
        # manifest must be read from disk so the verifier re-hashes real bytes.
        resolved_manifest_path = Path(manifest_path)
        if trusted_manifest_file_sha256 is not None:
            errors.append(
                "trusted manifest file anchor requires the manifest to be "
                "loaded from disk, not supplied as a mapping")
            return BInputBundle("BLOCKED", None, tuple(errors), tuple(warnings))
    if not isinstance(manifest, Mapping):
        errors.append("B input manifest must be a mapping")
        return BInputBundle("BLOCKED", None, tuple(errors), tuple(warnings))

    verification = verify_phase_manifest(
        manifest, root_path, "B",
        expected_contract_sha256=expected_contract_sha256,
        expected_framework_contract_sha256=(
            C.contract_hash() if expected_framework_contract_sha256 is None
            else expected_framework_contract_sha256),
        trusted_manifest_file_sha256=trusted_manifest_file_sha256,
        manifest_file_path=resolved_manifest_path,
        require_manifest_file_anchor=(
            trusted_manifest_file_sha256 is not None),
        expected_candidate_generation_id=expected_candidate_generation_id,
        repo_root=repo_root,
    )
    errors.extend(verification.errors)
    warnings.extend(verification.warnings)
    if controls_lock is not None and not controls_lock.verify():
        errors.append("controls lock does not verify")
    if not verification.ok or not verification.can_run_primary:
        return BInputBundle("BLOCKED", verification, tuple(dict.fromkeys(errors)),
                            tuple(dict.fromkeys(warnings)))

    artifacts = {
        str(artifact.get("artifact_id")): artifact
        for artifact in manifest.get("artifacts", [])
        if isinstance(artifact, Mapping)
    }

    def artifact_path(artifact_id: str) -> Path:
        artifact = artifacts.get(artifact_id)
        if not isinstance(artifact, Mapping) or artifact.get("status") != "READY":
            raise C.ContractViolation(f"B artifact {artifact_id} is not READY")
        relative = artifact.get("relative_path")
        if not isinstance(relative, str) or not relative:
            raise C.ContractViolation(f"B artifact {artifact_id} has no relative path")
        relative_path = Path(relative)
        current = root_path.resolve(strict=True)
        for part in relative_path.parts:
            current = current / part
            if current.is_symlink():
                raise C.ContractViolation(
                    f"B artifact {artifact_id} path contains a symlink")
        candidate = (root_path / relative_path).resolve(strict=False)
        resolved_root = root_path.resolve(strict=True)
        try:
            candidate.relative_to(resolved_root)
        except ValueError as exc:
            raise C.ContractViolation(
                f"B artifact {artifact_id} path escapes the handoff root") from exc
        if not candidate.is_file():
            raise C.ContractViolation(f"B artifact {artifact_id} file is missing")
        artifact_paths[artifact_id] = relative
        return candidate

    try:
        dem_path = artifact_path("dem_300x300_100m_32645")
        # Read once for an explicit target-grid diagnostic before deriving
        # terrain metrics through the existing DEM adapter.
        dem, dem_meta = read_target_raster(
            str(dem_path), target=target,
            semantic=C.B_ARTIFACT_SEMANTICS["dem_300x300_100m_32645"],
            declared_artifact=artifacts["dem_300x300_100m_32645"])
        diagnostics["dem"] = dem_meta
        rgi_path = artifact_path("rgi15_fixed_box_subset")
        inventory = read_rgi_inventory([str(rgi_path)], target=target)
        glacier_support = rasterize_glacier_support(inventory, target=target)
        hanging_path = artifact_path("hanging_ice_support_grid")
        hanging, hanging_meta = read_target_raster(
            str(hanging_path), target=target,
            semantic=C.B_ARTIFACT_SEMANTICS["hanging_ice_support_grid"],
            declared_artifact=artifacts["hanging_ice_support_grid"])
        diagnostics["hanging_ice_support"] = hanging_meta
        terrain = terrain_components_from_dem(
            str(dem_path), glacier_support=glacier_support,
            hanging_ice_support=hanging)
        for name in C.REQUIRED_TERRAIN_COMPONENTS:
            if name not in terrain:
                errors.append(f"missing terrain component from B bundle: {name}")
            else:
                errors.extend(validate_target_array(name, terrain[name], target))

        for name, artifact_id in B_TARGET_GRID_ARTIFACT_IDS.items():
            if name == "hanging_ice_support":
                continue
            path = artifact_path(artifact_id)
            values, meta = read_target_raster(
                str(path), target=target,
                semantic=C.B_ARTIFACT_SEMANTICS.get(artifact_id),
                declared_artifact=artifacts[artifact_id])
            exposure[name] = values
            diagnostics[name] = meta

        population_artifact = artifacts.get("worldpop_population")
        if isinstance(population_artifact, Mapping) and population_artifact.get("status") == "READY":
            population_path = artifact_path("worldpop_population")
            population, population_meta = read_target_raster(
                str(population_path), target=target,
                semantic=C.B_ARTIFACT_SEMANTICS["worldpop_population"],
                declared_artifact=population_artifact)
            exposure["population"] = population
            diagnostics["population"] = population_meta

        obs_path = artifact_path("sentinel1_per_unit_observability")
        observability, obs_errors, obs_diagnostics = _load_b_observability(
            obs_path, target)
        errors.extend(obs_errors)
        diagnostics["observability"] = obs_diagnostics
    except (C.ContractViolation, OSError, ValueError, RuntimeError) as exc:
        errors.append(str(exc))

    if errors:
        return BInputBundle("BLOCKED", verification,
                            tuple(dict.fromkeys(errors)),
                            tuple(dict.fromkeys(warnings)),
                            artifact_paths=artifact_paths,
                            diagnostics=diagnostics)
    return BInputBundle(C.PHASE_STATUS_LOAD_READY, verification, tuple(), tuple(warnings),
                        terrain_grids=terrain, exposure_grids=exposure,
                        observability_by_unit=observability,
                        artifact_paths=artifact_paths,
                        diagnostics=diagnostics)


def load_verified_b_input_bundle(
    root: str | Path,
    manifest: Optional[Mapping[str, Any]] = None,
    *,
    expected_contract_sha256: Optional[str],
    expected_framework_contract_sha256: Optional[str],
    controls_lock: Optional[ControlsLock] = None,
    repo_root: Optional[str | Path] = None,
    target: TargetGrid = TargetGrid(),
    manifest_path: Optional[str | Path] = None,
    trusted_manifest_file_sha256: Optional[str] = None,
    candidate_generation_id: Optional[str] = None,
) -> BInputBundle:
    """Strict one-shot B loader with both contract domains explicitly bound.

    The ordinary loader remains useful for diagnostics and deliberately blocks
    when the data contract hash is omitted.  This wrapper is the only loader
    intended for a primary B screening invocation.  Strict primary execution
    additionally requires ``trusted_manifest_file_sha256``: an externally
    pinned SHA-256 of the on-disk manifest bytes, and
    ``candidate_generation_id``: the candidate generation the strict run is
    authorized for.  Loadability alone is not authorization.
    """
    problems: list[str] = []
    if not isinstance(candidate_generation_id, str) or \
            not candidate_generation_id:
        problems.append(
            "candidate_generation_id must be an explicit non-empty string "
            "for strict loading")
    for name, value in (
            ("expected_contract_sha256", expected_contract_sha256),
            ("expected_framework_contract_sha256",
             expected_framework_contract_sha256),
            ("trusted_manifest_file_sha256", trusted_manifest_file_sha256)):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            problems.append(f"{name} must be an explicit lowercase SHA-256")
    if (isinstance(expected_framework_contract_sha256, str) and
            re.fullmatch(r"[0-9a-f]{64}", expected_framework_contract_sha256) and
            expected_framework_contract_sha256 != C.contract_hash()):
        problems.append(
            "expected framework contract hash does not match the runtime "
            "framework contract")
    if manifest is not None and manifest_path is not None:
        problems.append(
            "strict loading reads the manifest from manifest_path; do not "
            "also supply a detached mapping")
    if problems:
        return BInputBundle("BLOCKED", None, tuple(problems))
    return load_b_input_bundle(
        root, manifest,
        expected_contract_sha256=expected_contract_sha256,
        expected_framework_contract_sha256=expected_framework_contract_sha256,
        controls_lock=controls_lock,
        repo_root=repo_root,
        target=target,
        manifest_path=manifest_path,
        trusted_manifest_file_sha256=trusted_manifest_file_sha256,
        expected_candidate_generation_id=candidate_generation_id,
    )


def build_b_screen_from_bundle(
    bundle: BInputBundle,
    *,
    controls_lock: ControlsLock,
    a_gate_passed: Optional[bool] = None,
    a_gate_artifact: Optional[Mapping[str, Any]] = None,
    sidecar_grids: Optional[Mapping[str, Any]] = None,
    timeout_seconds: Optional[float] = None,
    checkpoint_path: Optional[str | Path] = None,
) -> dict[str, Any]:
    """Run the pure B assembler only after the read-only bundle is READY."""
    problems: list[str] = []
    if not bundle.ok or bundle.verification is None:
        problems = list(bundle.errors) or ["validated B input bundle is required"]
        return bind_artifact_envelope({
            "status": C.OutputStatus.BLOCKED.value,
            "gate_id": C.GateId.B_TO_C.value,
            "gate_passed": False,
            "phase_status": C.PHASE_STATUS_B_TO_C_BLOCKED,
            "errors": sorted(set(problems)),
            "blocked_reasons": sorted(set(problems)),
            "gate": bind_gate_artifact({
                "gate_id": C.GateId.B_TO_C.value, "passed": False,
                "checks": {}, "problems": sorted(set(problems)),
            }),
        })
    if a_gate_artifact is None:
        problem = ("hash-bound A_CATALOG gate artifact is required for strict B "
                   "screening; caller booleans are not authorization evidence")
        return bind_artifact_envelope({
            "status": C.OutputStatus.BLOCKED.value,
            "gate_id": C.GateId.B_TO_C.value,
            "gate_passed": False,
            "phase_status": C.PHASE_STATUS_B_TO_C_BLOCKED,
            "errors": [problem],
            "blocked_reasons": [problem],
            "gate": bind_gate_artifact({
                "gate_id": C.GateId.B_TO_C.value, "passed": False,
                "checks": {"gate_A_passed": {"passed": False}},
                "problems": [problem],
            }),
        })
    if a_gate_artifact is not None:
        a_ok, a_inner, _, a_problems = verify_gate_input(
            a_gate_artifact, expected_gate_id=C.GateId.A_CATALOG.value,
            require_outer_envelope=True)
        if not a_ok:
            problems = [f"A gate artifact: {problem}" for problem in a_problems]
        elif not isinstance(a_inner, Mapping) or a_inner.get("passed") is not True:
            problems = ["A_CATALOG gate artifact is not passed"]
        if problems:
            return bind_artifact_envelope({
                "status": C.OutputStatus.BLOCKED.value,
                "gate_id": C.GateId.B_TO_C.value,
                "gate_passed": False,
                "phase_status": C.PHASE_STATUS_B_TO_C_BLOCKED,
                "errors": sorted(set(problems)),
                "blocked_reasons": sorted(set(problems)),
                "provenance": {"a_gate_artifact_sha256":
                                gate_input_artifact_sha256(a_gate_artifact)},
                "gate": bind_gate_artifact({
                    "gate_id": C.GateId.B_TO_C.value, "passed": False,
                    "checks": {"gate_A_passed": {"passed": False}},
                    "problems": sorted(set(problems)),
                }),
            })
        a_gate_passed = True
    if a_gate_passed is None:
        problem = "verified A_CATALOG gate did not produce a passed authorization"
        return bind_artifact_envelope({
            "status": C.OutputStatus.BLOCKED.value,
            "gate_id": C.GateId.B_TO_C.value,
            "gate_passed": False,
            "phase_status": C.PHASE_STATUS_B_TO_C_BLOCKED,
            "errors": [problem],
            "gate": bind_gate_artifact({
                "gate_id": C.GateId.B_TO_C.value, "passed": False,
                "checks": {"gate_A_passed": {"passed": False}},
                "problems": [problem],
            }),
        })
    assert a_gate_passed is not None
    a_gate_for_gate = bool(a_gate_passed)
    return build_b_screen(
        bundle.terrain_grids,
        bundle.exposure_grids,
        bundle.observability_by_unit,
        controls_lock=controls_lock,
        a_gate_passed=a_gate_for_gate,
        a_gate_artifact=a_gate_artifact,
        manifest_verification=bundle.verification,
        sidecar_grids=sidecar_grids,
        timeout_seconds=timeout_seconds,
        checkpoint_path=checkpoint_path,
    )


def _b_worker_entry(bundle: BInputBundle,
                    kwargs: Mapping[str, Any],
                    result_path: str,
                    error_path: str) -> None:
    """Child-process entry point: run B and hand the envelope back by file."""
    try:
        result = build_b_screen_from_bundle(bundle, **dict(kwargs))
        write_deterministic_json(result_path, result)
    except BaseException as exc:  # noqa: BLE001 - boundary handoff
        try:
            write_deterministic_json(error_path, {
                "error_type": type(exc).__name__,
                "error": str(exc)[:2000],
            })
        except OSError:
            pass


def _b_timeout_envelope(stage: str, elapsed: float) -> dict[str, Any]:
    problem = (f"B execution exceeded its bounded worker deadline "
               f"({elapsed:.1f}s) during {stage}; worker was terminated")
    gate = bind_gate_artifact({
        "gate_id": C.GateId.B_TO_C.value,
        "passed": False,
        "checks": {"bounded_execution": {"passed": False}},
        "problems": [problem],
    })
    return bind_artifact_envelope({
        "status": C.B_TIMEOUT_STATUS,
        "gate_id": C.GateId.B_TO_C.value,
        "gate_passed": False,
        "phase_status": C.PHASE_STATUS_B_TO_C_BLOCKED,
        "errors": [problem],
        "blocked_reasons": [problem],
        "gate": gate,
        "provenance": {"framework_contract_sha256": C.contract_hash()},
    })


def run_b_screen_in_worker(
    bundle: BInputBundle,
    *,
    controls_lock: ControlsLock,
    a_gate_artifact: Optional[Mapping[str, Any]] = None,
    sidecar_grids: Optional[Mapping[str, Any]] = None,
    timeout_seconds: Optional[float] = None,
    checkpoint_path: Optional[str | Path] = None,
) -> dict[str, Any]:
    """Run strict B inside a killable worker process.

    In-process deadline checks cannot interrupt a hang inside a native
    ranking/LOO call; a child process can be terminated.  The worker writes
    checkpoints itself (same atomic path) and hands the result envelope back
    through a file.  A deadline breach yields the B timeout envelope — never a
    passed result.
    """
    import multiprocessing
    import tempfile

    effective_timeout = (
        C.B_DEFAULT_TIMEOUT_SECONDS if timeout_seconds is None
        else float(timeout_seconds))
    if not math.isfinite(effective_timeout) or effective_timeout < 0:
        raise C.ContractViolation(
            "B timeout_seconds must be finite and non-negative")

    kwargs: dict[str, Any] = {
        "controls_lock": controls_lock,
        "a_gate_artifact": a_gate_artifact,
        "sidecar_grids": sidecar_grids,
        "timeout_seconds": effective_timeout,
        "checkpoint_path": (str(checkpoint_path)
                            if checkpoint_path is not None else None),
    }
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="nepal-b-worker-") as tmp:
        result_path = os.path.join(tmp, "b_result.json")
        error_path = os.path.join(tmp, "b_error.json")
        ctx = multiprocessing.get_context("spawn")
        proc = ctx.Process(
            target=_b_worker_entry,
            args=(bundle, kwargs, result_path, error_path),
            name="nepal-b-screen-worker",
        )
        proc.start()
        # Parent-side grace beyond the worker's own deadline lets an orderly
        # BExecutionTimeout envelope reach disk before a hard kill.
        proc.join(effective_timeout + C.B_WORKER_KILL_GRACE_SECONDS)
        if proc.is_alive():
            proc.terminate()
            proc.join(C.B_WORKER_TERMINATE_WAIT_SECONDS)
            if proc.is_alive():
                proc.kill()
                proc.join(5)
            if checkpoint_path is not None:
                try:
                    write_deterministic_json(
                        checkpoint_path,
                        bind_artifact_envelope({
                            "framework_version": C.FRAMEWORK_VERSION,
                            "status": "TIMEOUT",
                            "stage": "worker_terminated",
                            "worker_pid": proc.pid,
                        }))
                except OSError:
                    pass
            return _b_timeout_envelope("worker", time.monotonic() - started)
        if os.path.isfile(result_path):
            return dict(json.loads(
                Path(result_path).read_text(encoding="utf-8")))
        if os.path.isfile(error_path):
            detail = dict(json.loads(
                Path(error_path).read_text(encoding="utf-8")))
            problem = (f"B worker failed: {detail.get('error_type', 'Error')}: "
                       f"{detail.get('error', '')}")
        else:
            problem = (f"B worker exited without a result "
                       f"(exitcode={proc.exitcode})")
    return bind_artifact_envelope({
        "status": C.OutputStatus.BLOCKED.value,
        "gate_id": C.GateId.B_TO_C.value,
        "gate_passed": False,
        "phase_status": C.PHASE_STATUS_B_TO_C_BLOCKED,
        "errors": [problem],
        "blocked_reasons": [problem],
        "gate": bind_gate_artifact({
            "gate_id": C.GateId.B_TO_C.value, "passed": False,
            "checks": {"worker_completed": {"passed": False}},
            "problems": [problem],
        }),
        "provenance": {"framework_contract_sha256": C.contract_hash()},
    })


def validate_target_array(name: str, array: Any,
                          target: TargetGrid = TargetGrid(),
                          semantic: Optional[Mapping[str, Any]] = None) -> list[str]:
    """Validate an adapter array without coercing missing data to zero."""
    problems = target.validate()
    try:
        arr = np.asarray(array, dtype=float)
    except (TypeError, ValueError):
        problems.append(f"{name} must contain numeric values")
        return problems
    if arr.shape != target.shape:
        problems.append(f"{name} shape {arr.shape} != target {target.shape}")
    if arr.ndim != 2:
        problems.append(f"{name} must be a two-dimensional grid")
    if arr.ndim == 2 and np.isinf(arr).any():
        problems.append(f"{name} contains infinite values")
    if semantic is not None and arr.ndim == 2:
        finite = np.isfinite(arr)
        if not finite.any():
            problems.append(f"{name} has no finite values")
        else:
            values = arr[finite]
            domain = semantic.get("value_domain")
            if domain == "nonnegative_m2" and np.any(values < 0):
                problems.append(f"{name} contains negative built-up surface values")
            elif domain == "binary_01" and not np.all(
                    np.isclose(values, 0.0) | np.isclose(values, 1.0)):
                problems.append(f"{name} contains values outside binary domain {sorted(set(values))[:5]}")
            elif domain == "fraction_0_1_with_minus1_unscreenable" and not np.all(
                    ((values >= 0.0) & (values <= 1.0)) |
                    np.isclose(values, -1.0)):
                problems.append(f"{name} contains values outside fraction domain [0, 1]")
            elif domain == "nonnegative_persons_with_minus1_nodata" and not np.all(
                    (values >= 0.0) | np.isclose(values, -1.0)):
                problems.append(
                    f"{name} contains negative population values outside -1 nodata")
            elif domain == "fraction_0_1" and not np.all(
                    (values >= 0.0) & (values <= 1.0)):
                problems.append(f"{name} contains values outside fraction domain [0, 1]")
    return problems


_COMPONENT_SEMANTICS: dict[str, Mapping[str, Any]] = {
    "built_up": C.B_ARTIFACT_SEMANTICS["ghsl_built_up_surface"],
    "infrastructure": C.B_ARTIFACT_SEMANTICS[
        "osm_infrastructure_grid_300x300_100m_32645"],
    "river_connectivity": C.B_ARTIFACT_SEMANTICS[
        "hydrorivers_connectivity_grid_300x300_100m_32645"],
    "hanging_ice_support": C.B_ARTIFACT_SEMANTICS[
        "hanging_ice_support_grid"],
    "population": C.B_ARTIFACT_SEMANTICS["worldpop_population"],
}


def _component_semantic(name: str) -> Optional[Mapping[str, Any]]:
    """Return the artifact semantic contract for a derived component name."""
    return _COMPONENT_SEMANTICS.get(name)


def reproject_dem_to_target(dem_path: str, *,
                            target: TargetGrid = TargetGrid()) -> tuple[np.ndarray, dict[str, Any]]:
    """Reproject a declared projected DEM into the exact v1 target grid.

    A geographic source is rejected before any window read.  This prevents
    the classic projected-bounds-as-degrees mistake and makes the current
    legacy DEM fail honestly until a reconciled projected product exists.
    """
    problems = target.validate()
    if problems:
        raise C.ContractViolation("; ".join(problems))
    try:
        import rasterio
        from rasterio.enums import Resampling
        from rasterio.warp import reproject
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("rasterio is required for DEM adaptation") from exc

    destination = np.full(target.shape, np.nan, dtype=np.float32)
    with rasterio.open(dem_path) as src:
        if src.crs is None:
            raise C.ContractViolation("DEM has no CRS")
        if src.nodata is None:
            raise C.ContractViolation(
                "DEM nodata is undeclared; refuse to invent a missing-data mask")
        if src.crs.to_string().upper() in {"EPSG:4326", "OGC:CRS84"} or \
                getattr(src.crs, "is_geographic", False):
            raise C.ContractViolation(
                f"DEM CRS {src.crs} is geographic; reprojected EPSG:32645 input required")
        if src.crs.to_string().upper() != C.STUDY_BOX_CRS:
            # A different projected source is allowed only through an explicit
            # reprojection, never by passing its transform to from_bounds.
            source_crs = src.crs
        else:
            source_crs = src.crs
        reproject(
            source=rasterio.band(src, 1),
            destination=destination,
            src_transform=src.transform,
            src_crs=source_crs,
            src_nodata=src.nodata,
            dst_transform=target.transform(),
            dst_crs=target.crs,
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )
        meta = {
            "source_crs": src.crs.to_string(),
            "target_crs": target.crs,
            "target_shape": list(target.shape),
            "target_resolution_m": target.resolution_m,
            "source_nodata": src.nodata,
            "valid_fraction": float(np.mean(np.isfinite(destination))),
            "resampling": "bilinear",
        }
    if not np.isfinite(destination).any():
        raise C.ContractViolation("DEM target grid has no finite cells")
    return destination, meta


def read_target_raster(path: str, *,
                       target: TargetGrid = TargetGrid(),
                       semantic: Optional[Mapping[str, Any]] = None,
                       declared_artifact: Optional[Mapping[str, Any]] = None,
                       ) -> tuple[np.ndarray, dict[str, Any]]:
    """Read a derived component raster only when it is already target-aligned.

    Component rasters are not silently resampled: a caller must either provide
    the exact 300 x 300 EPSG:32645 grid or use an explicit preprocessing step
    and record that product separately.
    """
    problems = target.validate()
    if problems:
        raise C.ContractViolation("; ".join(problems))
    try:
        import rasterio
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("rasterio is required for target-raster adaptation") from exc
    with rasterio.open(path) as src:
        if src.crs is None:
            raise C.ContractViolation("target raster has no CRS")
        if src.crs.to_string().upper() != target.crs:
            raise C.ContractViolation(
                f"target raster CRS {src.crs} != required {target.crs}")
        if src.width != target.width or src.height != target.height:
            raise C.ContractViolation(
                f"target raster shape {(src.height, src.width)} != {target.shape}")
        if src.count < 1:
            raise C.ContractViolation("target raster has no band 1")
        if src.nodata is None:
            raise C.ContractViolation(
                "target raster nodata is undeclared; refuse to invent a mask")
        expected = target.transform()
        actual = src.transform
        if any(abs(float(a) - float(b)) > 1e-6
               for a, b in zip(actual, expected)):
            raise C.ContractViolation("target raster transform is not the frozen grid")
        raw_values = np.asarray(src.read(1), dtype=float)
        if np.isinf(raw_values).any():
            raise C.ContractViolation("target raster contains infinite values")
        nodata = float(src.nodata)
        if np.isnan(nodata):
            values = raw_values
        else:
            if np.isnan(raw_values).any():
                raise C.ContractViolation(
                    "target raster contains NaN values outside its declared nodata")
            values = raw_values
            values[np.isclose(values, nodata)] = np.nan
        if declared_artifact is not None:
            declared_dtype = declared_artifact.get("dtype")
            if (isinstance(declared_dtype, str) and declared_dtype and
                    declared_dtype != str(src.dtypes[0])):
                raise C.ContractViolation(
                    f"target raster dtype {src.dtypes[0]!r} != declared {declared_dtype!r}")
            declared_nodata = declared_artifact.get("nodata")
            nodata_matches = False
            if (isinstance(declared_nodata, (int, float)) and
                    not isinstance(declared_nodata, bool)):
                declared_nodata_float = float(declared_nodata)
                nodata_matches = (
                    math.isnan(declared_nodata_float) and math.isnan(nodata))
            if not nodata_matches and (
                    isinstance(declared_nodata, (int, float)) and
                    not isinstance(declared_nodata, bool)):
                try:
                    nodata_matches = math.isclose(
                        float(declared_nodata), nodata,
                        rel_tol=0.0, abs_tol=1e-9)
                except (TypeError, ValueError):
                    nodata_matches = False
            if not nodata_matches:
                raise C.ContractViolation(
                    f"target raster nodata {src.nodata!r} != declared "
                    f"{declared_nodata!r}")
        semantic_problems = validate_target_array(
            Path(path).stem, values, target, semantic=semantic)
        if semantic_problems:
            raise C.ContractViolation("; ".join(semantic_problems))
        meta = {
            "crs": target.crs,
            "shape": list(target.shape),
            "resolution_m": target.resolution_m,
            "nodata": src.nodata,
            "finite_fraction": float(np.mean(np.isfinite(values))),
            "dtype": str(src.dtypes[0]),
            "value_domain": semantic.get("value_domain") if semantic else None,
        }
    return values, meta


def validate_inventory_geometry_crs(crs: Any, bounds: Sequence[float]) -> list[str]:
    """Detect the common RGI bad-.prj case before spatial clipping."""
    try:
        from pyproj import CRS
        parsed = CRS.from_user_input(crs)
    except Exception as exc:
        return [f"inventory CRS is invalid: {crs!r} ({exc})"]
    if len(bounds) != 4 or not all(np.isfinite(float(v)) for v in bounds):
        return ["inventory bounds are not finite four-tuples"]
    minx, miny, maxx, maxy = (float(v) for v in bounds)
    problems: list[str] = []
    if parsed.is_projected and max(abs(minx), abs(maxx)) <= 180 and \
            max(abs(miny), abs(maxy)) <= 90:
        problems.append(
            "inventory declares a projected CRS but coordinates look geographic; "
            "reject corrupt .prj instead of guessing a repair")
    if parsed.is_geographic and not (-180 <= minx <= maxx <= 180 and
                                     -90 <= miny <= maxy <= 90):
        problems.append("inventory geographic bounds are outside longitude/latitude range")
    return problems


def read_rgi_inventory(paths: Sequence[str], *,
                       target: TargetGrid = TargetGrid()):
    """Read, validate, reproject and clip RGI vectors to the frozen box."""
    try:
        import geopandas as gpd  # pyright: ignore[reportMissingModuleSource]
        import pandas as pd
        from shapely.geometry import box as make_box
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("geopandas and shapely are required for RGI adaptation") from exc
    if not paths:
        raise C.ContractViolation("no RGI inventory paths supplied")
    frames = []
    for path in paths:
        frame = gpd.read_file(path)
        if frame.crs is None:
            raise C.ContractViolation(f"RGI inventory has no CRS: {path}")
        if frame.empty:
            continue
        bounds = tuple(float(v) for v in frame.total_bounds)
        problems = validate_inventory_geometry_crs(frame.crs, bounds)
        if problems:
            raise C.ContractViolation(f"{path}: {'; '.join(problems)}")
        frames.append(frame.to_crs(target.crs))
    if not frames:
        raise C.ContractViolation("RGI inventories contain no features")
    merged: Any = pd.concat(frames, ignore_index=True)
    b = C.study_box()
    clipped = gpd.clip(merged, make_box(b["minx"], b["miny"], b["maxx"], b["maxy"]))
    return clipped.reset_index(drop=True)


def rasterize_glacier_support(inventory, *,
                              target: TargetGrid = TargetGrid()) -> np.ndarray:
    """Return a binary, explicit glacier-support mask on the target grid."""
    problems = target.validate()
    if problems:
        raise C.ContractViolation("; ".join(problems))
    try:
        from rasterio.features import rasterize
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("rasterio is required for inventory rasterization") from exc
    geometries = [(geom, 1) for geom in inventory.geometry
                  if geom is not None and not geom.is_empty]
    if not geometries:
        raise C.ContractViolation("RGI inventory has no usable geometries in the box")
    return rasterize(geometries, out_shape=target.shape,
                     transform=target.transform(), fill=0,
                     dtype="float32", all_touched=False)


def per_unit_winter_observability(
    acquisitions: Sequence[Mapping[str, Any]],
    pairs: Sequence[Mapping[str, Any]],
    controls: ControlsConfig,
) -> tuple[dict[str, Optional[float]], dict[str, Any]]:
    """Compute winter pair observability for each analysis unit.

    A scalar scene fraction cannot be used to rank cells.  Each record must
    identify one or more analysis units; absent/unknown units remain None.
    Derived HyP3 fields are rejected rather than stripped.
    """
    metadata, rejected_acq = separate_hyp3_signals(acquisitions)
    metadata_pairs, rejected_pairs = separate_hyp3_signals(pairs)
    problems: list[str] = []
    for rec in metadata:
        problems.extend(validate_acquisition_record(rec))
    if problems:
        raise C.ContractViolation("invalid S1 acquisition metadata: " + "; ".join(sorted(set(problems))))
    for rec in metadata_pairs:
        if "date" not in rec:
            problems.append("pair metadata missing field 'date'")
        if "pair_available" not in rec and "available" not in rec:
            problems.append("pair metadata missing field 'pair_available' or 'available'")
        flag = rec.get("pair_available", rec.get("available"))
        if ("pair_available" in rec or "available" in rec) and not isinstance(flag, bool):
            problems.append("pair availability must be a boolean")
    if problems:
        raise C.ContractViolation("invalid S1 pair metadata: " + "; ".join(sorted(set(problems))))

    expected_default = controls.expected_winter_pairs
    observed: dict[str, int] = {}
    expected: dict[str, int] = {}
    unexpected_units: set[str] = set()
    for rec in metadata_pairs:
        unit_values = rec.get("analysis_unit_ids", rec.get("unit_ids"))
        if unit_values is None:
            unit_values = [rec.get("analysis_unit_id", rec.get("unit_id"))]
        if isinstance(unit_values, str):
            unit_values = [unit_values]
        if not isinstance(unit_values, (list, tuple, set, frozenset)):
            problems.append("pair analysis unit IDs must be a string or sequence")
            continue
        try:
            d = date.fromisoformat(str(rec.get("date"))[:10])
        except (TypeError, ValueError):
            problems.append("pair metadata date must be ISO-parseable")
            continue
        if not C.is_winter_date(d):
            continue
        local_expected = rec.get("expected_pairs", expected_default)
        if local_expected is not None:
            try:
                local_expected = int(local_expected)
            except (TypeError, ValueError):
                problems.append("expected_pairs must be a positive integer")
                continue
            if local_expected <= 0:
                problems.append("expected_pairs must be a positive integer")
                continue
        for unit_id in unit_values:
            if not unit_id:
                continue
            uid = str(unit_id)
            try:
                prefix, easting, northing = uid.split("-", 2)
                if prefix != "AU" or not easting.startswith("E") or not northing.startswith("N"):
                    raise ValueError
                if not (0 <= int(easting[1:]) < 300 and
                        0 <= int(northing[1:]) < 300):
                    raise ValueError
            except (ValueError, TypeError):
                unexpected_units.add(uid)
                continue
            if local_expected is not None:
                expected[uid] = max(expected.get(uid, 0), local_expected)
            if bool(rec.get("pair_available", rec.get("available"))):
                observed[uid] = observed.get(uid, 0) + 1
    if unexpected_units:
        problems.append("pair metadata has invalid analysis unit IDs: " +
                        ", ".join(sorted(unexpected_units)[:10]))
        if len(unexpected_units) > 10:
            problems.append(f"pair metadata has {len(unexpected_units)} invalid analysis unit IDs")
    if problems:
        raise C.ContractViolation("invalid S1 pair metadata: " + "; ".join(
            sorted(set(problems))))
    result: dict[str, Optional[float]] = {}
    for uid in sorted(set(expected) | set(observed)):
        denom = expected.get(uid, expected_default)
        result[uid] = (min(1.0, observed.get(uid, 0) / float(denom))
                       if denom is not None and int(denom) > 0 else None)
    diagnostics = {
        "mode": "per_analysis_unit",
        "n_acquisitions": len(metadata),
        "n_pairs": len(metadata_pairs),
        "rejected_derived_records": len(rejected_acq) + len(rejected_pairs),
        "units_with_observability": len(result),
        "unknown_or_unexpected_units": sorted(
            {str(r.get("analysis_unit_id", r.get("unit_id")))
             for r in metadata_pairs if not r.get("analysis_unit_id", r.get("unit_id"))}),
    }
    if rejected_acq or rejected_pairs:
        raise C.ContractViolation(
            "HyP3-derived S1 fields are not permitted in B observability metadata")
    return result, diagnostics


def _observability_grid(values: Mapping[str, Optional[float]],
                        target: TargetGrid) -> np.ndarray:
    grid = np.full(target.shape, np.nan, dtype=float)
    box = C.study_box()
    origin = (box["minx"], box["miny"])
    for row in range(target.height):
        for col in range(target.width):
            uid = C.analysis_unit_id(
                box["minx"] + (col + 0.5) * target.resolution_m,
                box["maxy"] - (row + 0.5) * target.resolution_m,
                target.resolution_m,
                origin)
            value = values.get(uid)
            if value is not None and np.isfinite(float(value)):
                grid[row, col] = float(value)
    return grid


def build_b_screen(
    terrain_grids: Mapping[str, Any],
    exposure_grids: Mapping[str, Any],
    observability_by_unit: Mapping[str, Optional[float]],
    *,
    controls_lock: ControlsLock,
    a_gate_passed: Optional[bool] = None,
    a_gate_artifact: Optional[Mapping[str, Any]] = None,
    manifest_verification: Optional[Any] = None,
    target: TargetGrid = TargetGrid(),
    sidecar_grids: Optional[Mapping[str, Any]] = None,
    timeout_seconds: Optional[float] = None,
    checkpoint_path: Optional[str | Path] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """Assemble strict real-data B output and compute B-to-C from evidence."""
    problems: list[str] = []
    effective_timeout_seconds = (
        C.B_DEFAULT_TIMEOUT_SECONDS if timeout_seconds is None
        else float(timeout_seconds))
    if not math.isfinite(effective_timeout_seconds) or effective_timeout_seconds < 0:
        raise C.ContractViolation(
            "B timeout_seconds must be finite and non-negative")
    started = time.monotonic()
    perf_started = time.perf_counter()
    deadline = started + effective_timeout_seconds

    def _peak_rss_bytes() -> Optional[int]:
        try:
            import resource
        except ImportError:
            return None
        # ru_maxrss is KiB on Linux, bytes on macOS/BSD.
        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return value * 1024 if sys.platform == "linux" else value

    verification_checks = (
        manifest_verification.checks
        if isinstance(manifest_verification, InputManifestVerification)
        else {})
    input_fingerprint = {
        "input_manifest_sha256": verification_checks.get("manifest_sha256"),
        "manifest_file_sha256": verification_checks.get("manifest_file_sha256"),
        "candidate_generation_id": verification_checks.get(
            "candidate_generation_id"),
        "a_gate_artifact_sha256": (
            gate_input_artifact_sha256(a_gate_artifact)
            if isinstance(a_gate_artifact, Mapping) else None),
        "controls_lock_sha256": controls_lock.sha256,
    }

    def _checkpoint(stage: str, status: str = "RUNNING", **extra: Any) -> None:
        if checkpoint_path is None:
            return
        payload = {
            "framework_version": C.FRAMEWORK_VERSION,
            "status": status,
            "stage": stage,
            "component_registry_version": C.COMPONENT_REGISTRY_VERSION,
            "active_terrain_components": list(C.ACTIVE_TERRAIN_COMPONENTS),
            "active_exposure_components": list(C.ACTIVE_EXPOSURE_COMPONENTS),
            "input_fingerprint": input_fingerprint,
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "peak_rss_bytes": _peak_rss_bytes(),
        }
        payload.update(extra)
        # A checkpoint is a resumability/security boundary, so authenticate
        # the complete payload just like the result envelope.  This prevents
        # a mutable progress file from being mistaken for the stage state it
        # claims to describe.
        write_deterministic_json(
            checkpoint_path, bind_artifact_envelope(payload))

    def _check(stage: str) -> None:
        if progress_callback is not None:
            progress_callback(stage)
        if deadline is not None and time.monotonic() >= deadline:
            _checkpoint(stage, "TIMEOUT")
            raise BExecutionTimeout(
                f"B execution timed out during {stage}")
        _checkpoint(stage)

    def _timeout_result(stage: str) -> dict[str, Any]:
        problem = f"B execution timed out during {stage}"
        gate = bind_gate_artifact({
            "gate_id": C.GateId.B_TO_C.value,
            "passed": False,
            "checks": {"bounded_execution": {"passed": False}},
            "problems": [problem],
        })
        return bind_artifact_envelope({
            "status": C.B_TIMEOUT_STATUS,
            "gate_id": C.GateId.B_TO_C.value,
            "gate_passed": False,
            "phase_status": C.PHASE_STATUS_B_TO_C_BLOCKED,
            "errors": [problem],
            "blocked_reasons": [problem],
            "gate": gate,
            "provenance": {
                "framework_contract_sha256": C.contract_hash(),
                "component_registry_version": C.COMPONENT_REGISTRY_VERSION,
                "timeout_seconds": effective_timeout_seconds,
                "elapsed_seconds": time.perf_counter() - perf_started,
                "timeout": True,
                "checkpoint_path": str(checkpoint_path) if checkpoint_path else None,
            },
        })

    manifest_checks = {}
    verification_payload = None
    if isinstance(manifest_verification, InputManifestVerification):
        verification_payload = manifest_verification.to_dict()
    elif manifest_verification is not None:
        problems.append(
            "typed input manifest verification is required; mappings are not trusted")
    if isinstance(verification_payload, Mapping):
        checks = verification_payload.get("checks", {})
        if isinstance(checks, Mapping):
            manifest_checks = dict(checks)
    a_gate_artifact_hash = None
    if a_gate_artifact is not None:
        if not isinstance(a_gate_artifact, Mapping):
            problems.append("A gate artifact must be a mapping")
            a_gate_passed = False
        else:
            a_ok, a_inner, _, a_problems = verify_gate_input(
                a_gate_artifact, expected_gate_id=C.GateId.A_CATALOG.value)
            a_gate_artifact_hash = gate_input_artifact_sha256(a_gate_artifact)
            if not a_ok:
                problems.extend(f"A gate artifact: {problem}"
                                for problem in a_problems)
                a_gate_passed = False
            elif not isinstance(a_inner, Mapping) or a_inner.get(
                    "passed") is not True:
                problems.append("A_CATALOG gate artifact is not passed")
                a_gate_passed = False
            else:
                a_gate_passed = True
    else:
        # ``a_gate_passed`` remains a compatibility parameter for callers that
        # still use the low-level assembler, but it is never authorization
        # evidence.  Every B result, including direct API calls, must carry a
        # verified A gate artifact.
        problems.append("hash-bound A_CATALOG gate artifact is required")
        a_gate_passed = False
    provenance = {
        "framework_contract_sha256": C.contract_hash(),
        "input_manifest_sha256": manifest_checks.get("manifest_sha256"),
        "input_manifest_hash_encoding": manifest_checks.get("manifest_hash_encoding"),
        "input_manifest_contract_bound": bool(
            manifest_checks.get("contract_bound", False)),
        "input_manifest_framework_runtime_bound": bool(
            manifest_checks.get(
                "framework_contract_runtime_bound",
                manifest_checks.get("framework_contract_bound", False))),
        "input_manifest_framework_declaration_bound": bool(
            manifest_checks.get("framework_contract_declaration_bound", False)),
        "input_manifest_framework_contract_bound": bool(
            manifest_checks.get("framework_contract_bound", False)),
        "input_manifest_raw_slc_scan": manifest_checks.get("raw_slc_scan"),
        "controls_lock_sha256": (controls_lock.sha256
                                  if isinstance(controls_lock, ControlsLock)
                                  else None),
        "input_manifest_canonical_authorized": bool(
            manifest_checks.get("canonical_manifest_authorized", False)),
        "candidate_generation_id": manifest_checks.get(
            "candidate_generation_id"),
        "input_manifest_file_sha256": manifest_checks.get(
            "manifest_file_sha256"),
        "input_fingerprint": dict(input_fingerprint),
        "component_registry_version": C.COMPONENT_REGISTRY_VERSION,
        "active_terrain_components": list(C.ACTIVE_TERRAIN_COMPONENTS),
        "active_exposure_components": list(C.ACTIVE_EXPOSURE_COMPONENTS),
        "optional_exposure_components": list(C.OPTIONAL_EXPOSURE_COMPONENTS),
        "a_gate_artifact_sha256": a_gate_artifact_hash,
        "timeout_seconds": effective_timeout_seconds,
        "elapsed_seconds": None,
        "timeout": False,
        "checkpoint_path": str(checkpoint_path) if checkpoint_path else None,
    }
    try:
        _check("inputs_verified")
    except BExecutionTimeout as exc:
        return _timeout_result(str(exc))
    controls_lock_ok = (isinstance(controls_lock, ControlsLock) and
                        controls_lock.verify())
    if not controls_lock_ok:
        problems.append("controls lock does not verify")
    if not isinstance(verification_payload, Mapping):
        problems.append("input manifest verification is required")
    else:
        if verification_payload.get("ok") is not True:
            problems.append("input manifest does not verify")
        if verification_payload.get("can_run_primary") is not True:
            problems.append("required primary input is not READY")
        for key, expected in (("self_hash_verified", True),
                              ("canonical_manifest_authorized", True),
                              ("contract_bound", True),
                              ("framework_contract_runtime_bound", True),
                              ("raw_slc_scan", "PASS")):
            if manifest_checks.get(key) != expected:
                problems.append(f"input manifest check {key} did not pass")
    for name in C.ACTIVE_TERRAIN_COMPONENTS:
        if name not in terrain_grids:
            problems.append(f"missing terrain component: {name}")
        else:
            problems.extend(validate_target_array(
                name, terrain_grids[name], target,
                semantic=_component_semantic(name)))
    for name in (*C.ACTIVE_EXPOSURE_COMPONENTS,
                 *C.OPTIONAL_EXPOSURE_COMPONENTS):
        if name not in C.ACTIVE_EXPOSURE_COMPONENTS and name not in exposure_grids:
            continue
        if name not in exposure_grids:
            problems.append(f"missing exposure component: {name}")
        else:
            problems.extend(validate_target_array(
                name, exposure_grids[name], target,
                semantic=_component_semantic(name)))
    if not isinstance(observability_by_unit, Mapping):
        problems.append("per-analysis-unit winter observability must be a mapping")
    else:
        for unit_id, value in observability_by_unit.items():
            try:
                prefix, easting, northing = str(unit_id).split("-", 2)
                col = int(easting[1:]) if easting.startswith("E") else -1
                row = int(northing[1:]) if northing.startswith("N") else -1
            except (TypeError, ValueError):
                problems.append(f"invalid observability analysis unit ID: {unit_id!r}")
                continue
            if (prefix != "AU" or col < 0 or col >= target.width or
                    row < 0 or row >= target.height):
                problems.append(f"invalid observability analysis unit ID: {unit_id!r}")
            if value is not None:
                try:
                    finite_value = float(value)
                except (TypeError, ValueError):
                    finite_value = float("nan")
                if not np.isfinite(finite_value) or not 0.0 <= finite_value <= 1.0:
                    problems.append(
                        f"observability for {unit_id!r} must be within [0, 1]")
    obs_values = (observability_by_unit
                  if isinstance(observability_by_unit, Mapping) else {})
    obs_grid = _observability_grid(obs_values, target)
    if not np.isfinite(obs_grid).all():
        problems.append("per-analysis-unit winter observability is incomplete")
    if problems:
        gate = bind_gate_artifact({
            "gate_id": C.GateId.B_TO_C.value,
            "passed": False,
            "checks": {"gate_A_passed": {"passed": bool(a_gate_passed)}},
            "problems": sorted(set(problems)),
        })
        return bind_artifact_envelope({
            "status": C.OutputStatus.BLOCKED.value,
            "gate_id": C.GateId.B_TO_C.value,
            "gate_passed": False,
            "phase_status": C.PHASE_STATUS_B_TO_C_BLOCKED,
            "gate": gate,
            "errors": sorted(set(problems)),
            "blocked_reasons": sorted(set(problems)),
            "provenance": provenance,
            "runtime": {
                "elapsed_seconds": time.perf_counter() - perf_started,
                "timeout_seconds": effective_timeout_seconds,
                "timeout": False,
            },
        })

    try:
        _check("baseline_ranking_start")
        result = rank_box(terrain_grids, exposure_grids, obs_grid,
                          box=C.study_box(), sidecar_grids=sidecar_grids,
                          require_all_components=True,
                          required_terrain_components=C.ACTIVE_TERRAIN_COMPONENTS,
                          required_exposure_components=C.ACTIVE_EXPOSURE_COMPONENTS)
        _check("baseline_ranking_complete")
    except BExecutionTimeout as exc:
        return _timeout_result(str(exc))
    result["winter_observability_mode"] = "per_analysis_unit"
    result["input_contract"] = "framework_v1_strict_real_data"
    result["provenance"] = provenance
    top5 = [r["analysis_unit_id"] for r in result["top_five"]]
    try:
        _check("loo_start")
        loo = leave_one_layer_out_top5(
            terrain_grids, exposure_grids, obs_grid, box=C.study_box(),
            required_terrain_components=C.ACTIVE_TERRAIN_COMPONENTS,
            required_exposure_components=C.ACTIVE_EXPOSURE_COMPONENTS,
            active_only=True,
            progress_callback=_check,
        )
        _check("loo_complete")
        with_sidecar = rank_box(terrain_grids, exposure_grids, obs_grid,
                                box=C.study_box(), sidecar_grids=sidecar_grids,
                                require_all_components=True,
                                required_terrain_components=C.ACTIVE_TERRAIN_COMPONENTS,
                                required_exposure_components=C.ACTIVE_EXPOSURE_COMPONENTS)
        _check("sidecar_noninfluence_complete")
    except BExecutionTimeout as exc:
        return _timeout_result(str(exc))
    sidecar_noninfluence = (
        [r["analysis_unit_id"] for r in with_sidecar["ranked"]] ==
        [r["analysis_unit_id"] for r in result["ranked"]]
        and [r["priority_index"] for r in with_sidecar["ranked"]] ==
        [r["priority_index"] for r in result["ranked"]])
    assert a_gate_passed is not None
    result["gate"] = evaluate_b_to_c_gate(
        a_gate_passed=bool(a_gate_passed),
        box_inputs_valid=True,
        screen_result=result,
        controls_lock_ok=controls_lock_ok,
        top5_ids=top5,
        loo_top5=loo,
        sidecar_noninfluence=sidecar_noninfluence,
    )
    result["loo_top5"] = loo
    result["optional_components_missing"] = [
        name for name in C.OPTIONAL_EXPOSURE_COMPONENTS
        if name not in exposure_grids
    ]
    result["status"] = C.PHASE_STATUS_SCREEN_RANKED
    result["gate_id"] = C.GateId.B_TO_C.value
    result["gate_passed"] = bool(result["gate"].get("passed"))
    result["phase_status"] = (
        C.PHASE_STATUS_B_TO_C_READY
        if result["gate_passed"] else C.PHASE_STATUS_B_TO_C_BLOCKED)
    bound = bind_artifact_envelope(result)
    bound["runtime"] = {
        "elapsed_seconds": time.perf_counter() - perf_started,
        "timeout_seconds": effective_timeout_seconds,
        "timeout": False,
    }
    bound["resource_evidence"] = {
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "peak_rss_bytes": _peak_rss_bytes(),
        "platform": sys.platform,
        "rss_units": "bytes",
        "timeout_seconds": effective_timeout_seconds,
    }
    bound = bind_artifact_envelope(bound)
    try:
        _checkpoint("completed", "COMPLETED",
                    result_artifact_sha256=bound["artifact_sha256"],
                    phase_status=bound["phase_status"])
    except OSError as exc:
        # A result cannot be advertised as resumable if its checkpoint could
        # not be written, but the already-computed envelope remains usable.
        bound.setdefault("warnings", []).append(
            f"checkpoint write failed after completion: {exc}")
        bound = bind_artifact_envelope(bound)
    return bound
