"""Fail-closed adapter for the reconciled data handoff.

The downloader and the framework have deliberately separate write lanes.  This
module is therefore a read-only boundary: it never repairs, rewrites, or
"helpfully" completes a data manifest.  It validates the manifest, resolves
only safe relative paths under the supplied root, and reports which artifacts
are actually consumable by the framework.

Data statuses (``READY``/``UNAVAILABLE``/...) are intentionally distinct from
the framework's result statuses (``PASS``/``BLOCKED``/...).
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from . import contract as C
from .provenance import (canonical_json, check_no_raw_slc_tree,
                         sha256_file)

DATA_STATUSES = frozenset(C.DATA_STATUSES)
PHASE_REQUIRED_ARTIFACT_IDS = C.PHASE_REQUIRED_ARTIFACT_IDS
B_TARGET_GRID_ARTIFACT_IDS = C.B_TARGET_GRID_ARTIFACT_IDS
_HANDOFF_ROLES = frozenset(C.HANDOFF_ROLES)
_HANDOFF_KINDS = frozenset(C.HANDOFF_KINDS)
_READY_PROCESSING_MARKERS = C.PHASE_READY_PROCESSING_MARKERS
_PHASE_ARTIFACT_CONTRACTS = C.PHASE_ARTIFACT_CONTRACTS
PRIMARY_ROLES = frozenset({"A", "B", "E"})
REQUIRED_ARTIFACT_FIELDS = frozenset({
    "artifact_id", "role", "kind", "relative_path", "status", "sha256",
    "bytes", "crs", "units", "license", "source_url", "query_or_request",
    "source_record_id", "acquired_at", "observation_start", "observation_end",
    "publication_or_validity_date", "processing", "language_access_status",
})
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

AUTHORITATIVE_DATA_CONTRACT_PATH = "nepal/feature_" + "contract.py"
AUTHORITATIVE_FRAMEWORK_CONTRACT_PATH = "nepal/framework_v1/contract.py"
COMPATIBILITY_CONTRACT_PATH = (
    "data/framework_inputs_v1_reconciled/scripts/contract.py")


def contract_source_inventory(repo_root: str | Path = ".") -> dict[str, Any]:
    """Report authoritative contract sources and compatibility drift.

    The reconciled downloader helper is intentionally inventoried, not
    silently promoted to an authorization source.  Hashing this inventory is
    read-only and gives audit/report tooling a deterministic drift signal.
    """
    root = Path(repo_root)
    paths = {
        "data_contract": AUTHORITATIVE_DATA_CONTRACT_PATH,
        "framework_contract": AUTHORITATIVE_FRAMEWORK_CONTRACT_PATH,
        "compatibility_contract": COMPATIBILITY_CONTRACT_PATH,
    }
    entries: dict[str, Any] = {}
    for name, relative in paths.items():
        path = root / relative
        entries[name] = {
            "relative_path": relative,
            "present": path.is_file(),
            "sha256": sha256_file(path) if path.is_file() else None,
            "authorization_role": (
                "authoritative" if name != "compatibility_contract"
                else "compatibility_only"),
        }
    return {
        "authoritative_data_contract": AUTHORITATIVE_DATA_CONTRACT_PATH,
        "authoritative_framework_contract": AUTHORITATIVE_FRAMEWORK_CONTRACT_PATH,
        "compatibility_contract": COMPATIBILITY_CONTRACT_PATH,
        "entries": entries,
        "drift_policy": "compatibility helper is never an authorization source",
    }


def canonical_input_manifest_hash(manifest: Mapping[str, Any]) -> str:
    """Return the canonical self-hash, excluding only ``manifest_sha256``."""
    payload = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def pretty_input_manifest_hash(manifest: Mapping[str, Any]) -> str:
    """Compatibility hash used by the reconciled downloader's v2 script.

    It is reported as compatibility-only; new framework-authored manifests
    must use :func:`canonical_input_manifest_hash`.
    """
    payload = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
    text = json.dumps(payload, indent=2, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_relative_path(value: Any) -> tuple[Optional[Path], Optional[str]]:
    if not isinstance(value, str):
        return None, "relative_path must be a string"
    if value == "":
        return None, None
    if "\x00" in value:
        return None, "relative_path contains NUL"
    # Reject Windows separators too; accepting them on POSIX would make a
    # manifest behave differently when replayed on another platform.
    if "\\" in value:
        return None, f"relative_path uses a backslash: {value!r}"
    path = Path(value)
    if path.is_absolute():
        return None, f"relative_path is absolute: {value!r}"
    if any(part == ".." for part in path.parts):
        return None, f"relative_path escapes its root: {value!r}"
    if path == Path("."):
        return None, "relative_path points at the root directory"
    return path, None


def _path_under_root(root: Path, relative: Path) -> tuple[Optional[Path], Optional[str]]:
    candidate = root / relative
    try:
        resolved_root = root.resolve(strict=True)
    except FileNotFoundError:
        return None, f"root does not exist: {root}"

    # Check every existing path component before resolving the leaf.  If the
    # leaf itself is a symlink, resolving first would hide the more useful
    # reason behind a generic "escapes root" error.
    current = resolved_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return None, f"symlink path component is forbidden: {relative.as_posix()!r}"
    try:
        resolved_candidate = candidate.resolve(strict=False)
        resolved_candidate.relative_to(resolved_root)
    except FileNotFoundError:
        return None, f"root does not exist: {root}"
    except ValueError:
        return None, f"resolved path escapes root: {relative.as_posix()!r}"

    return candidate, None


def _phase_text(value: Any) -> str:
    """Return bounded text for checking declared provenance semantics."""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return " ".join(f"{key}={_phase_text(item)}"
                        for key, item in sorted(value.items(), key=lambda pair: str(pair[0])))
    if isinstance(value, (list, tuple, set, frozenset)):
        return " ".join(_phase_text(item) for item in value)
    return "" if value is None else str(value)


def _validate_target_grid_metadata(artifact: Mapping[str, Any]) -> list[str]:
    grid = artifact.get("target_grid")
    if not isinstance(grid, Mapping):
        return [f"{artifact.get('artifact_id')}: target_grid metadata is required"]
    problems: list[str] = []
    expected = C.target_grid_contract()
    for key, value in expected.items():
        actual = grid.get(key)
        if key in {"affine_transform", "bounds"}:
            if (not isinstance(actual, (list, tuple)) or
                    len(actual) != len(value)):
                problems.append(
                    f"{artifact.get('artifact_id')}: target_grid {key} must be "
                    f"the frozen {len(value)}-value sequence")
                continue
            if not all(isinstance(item, (int, float)) and
                       not isinstance(item, bool) and math.isfinite(float(item))
                       for item in actual):
                problems.append(
                    f"{artifact.get('artifact_id')}: target_grid {key} must contain finite numbers")
                continue
            if not all(math.isclose(float(item), float(expected_item),
                                    rel_tol=0.0, abs_tol=1e-6)
                       for item, expected_item in zip(actual, value)):
                problems.append(
                    f"{artifact.get('artifact_id')}: target_grid {key} does not match "
                    "the frozen study-box geometry")
        elif actual != value:
            problems.append(
                f"{artifact.get('artifact_id')}: target_grid {key} must be {value!r}")
    return problems


def _normalized_text(value: Any) -> str:
    return " ".join(str(value).strip().lower().replace("²", "2").split())


def _validate_artifact_semantics(artifact: Mapping[str, Any]) -> list[str]:
    """Validate declared value semantics before a B artifact is consumed."""
    artifact_id = str(artifact.get("artifact_id"))
    spec = C.B_ARTIFACT_SEMANTICS.get(artifact_id)
    if spec is None:
        return []
    problems: list[str] = []
    units = artifact.get("units")
    normalized_units = _normalized_text(units) if isinstance(units, str) else ""
    allowed_units = {_normalized_text(item) for item in spec.get("units", ())}
    if not normalized_units:
        problems.append(f"{artifact_id}: units metadata is required")
    elif normalized_units not in allowed_units:
        # GHSL's binary declaration is a particularly dangerous semantic
        # mismatch: the real product is measured built-up surface in m².
        problems.append(
            f"{artifact_id}: units {units!r} do not match the declared semantic "
            f"domain {spec.get('value_domain')!r}")

    domain = artifact.get("value_domain")
    if domain != spec.get("value_domain"):
        problems.append(
            f"{artifact_id}: value_domain must be {spec.get('value_domain')!r}, "
            f"got {domain!r}")
    if spec.get("nodata_policy") and "nodata" not in artifact:
        problems.append(f"{artifact_id}: nodata metadata is required")
    if spec.get("nodata_policy") and artifact.get("nodata") is None:
        problems.append(f"{artifact_id}: nodata value must be declared")
    if spec.get("nodata_policy") and artifact.get("nodata") is not None:
        nodata = artifact.get("nodata")
        if (not isinstance(nodata, (int, float)) or
                isinstance(nodata, bool) or not math.isfinite(float(nodata))):
            problems.append(f"{artifact_id}: nodata must be a finite numeric value")
    if artifact.get("dtype") in (None, ""):
        problems.append(f"{artifact_id}: dtype metadata is required")
    if artifact.get("finite_value_policy") in (None, ""):
        problems.append(f"{artifact_id}: finite_value_policy metadata is required")

    source_ids = artifact.get("source_artifact_ids")
    if (not isinstance(source_ids, list) or
            not source_ids or not all(isinstance(item, str) and item for item in source_ids)):
        problems.append(f"{artifact_id}: source_artifact_ids lineage is required")
    source_hashes = artifact.get("source_hashes")
    if (not isinstance(source_hashes, Mapping) or
            not source_hashes or
            not all(isinstance(value, str) and SHA256_RE.fullmatch(value)
                    for value in source_hashes.values())):
        problems.append(f"{artifact_id}: source_hashes lineage is required")
    elif (isinstance(source_ids, list) and
          not all(source_id in source_hashes for source_id in source_ids)):
        problems.append(
            f"{artifact_id}: every source_artifact_id must have a source hash")
    for field_name in ("processing_script_sha256", "processing_config_sha256"):
        value = artifact.get(field_name)
        if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
            problems.append(f"{artifact_id}: {field_name} is required")
    if spec.get("nodata_values") is not None:
        nodata = artifact.get("nodata")
        allowed_nodata = spec["nodata_values"]
        if not any(
                isinstance(nodata, (int, float)) and
                not isinstance(nodata, bool) and
                math.isclose(float(nodata), float(candidate),
                             rel_tol=0.0, abs_tol=1e-9)
                for candidate in allowed_nodata):
            problems.append(
                f"{artifact_id}: nodata must be one of {list(allowed_nodata)!r}")
    if artifact.get("kind") == "raster" and artifact.get("target_grid"):
        resampling = artifact.get("resampling_method")
        if not isinstance(resampling, str) or not resampling.strip():
            problems.append(
                f"{artifact_id}: resampling_method must be explicitly declared")

    if artifact_id == "sentinel1_per_unit_observability":
        if artifact.get("observable") != spec.get("observable"):
            problems.append(
                f"{artifact_id}: observable must be {spec.get('observable')!r}")
        if artifact.get("metadata_only") is not True:
            problems.append(f"{artifact_id}: metadata_only must be true")
        if "sentinel1_compatible_pair_table" not in (source_ids or []):
            problems.append(
                f"{artifact_id}: compatible-pair source artifact linkage is required")
        pair_hash = artifact.get("pair_table_sha256")
        if not isinstance(pair_hash, str) or not SHA256_RE.fullmatch(pair_hash):
            problems.append(f"{artifact_id}: pair_table_sha256 is required")

    if artifact_id == "hanging_ice_support_grid":
        selected = artifact.get("selected_scene_ids")
        if (not isinstance(selected, list) or len(selected) != 12 or
                not all(isinstance(item, str) and item for item in selected)):
            problems.append(
                f"{artifact_id}: exactly 12 selected_scene_ids are required")
        if not isinstance(artifact.get("scene_selection_rule"), str) or not artifact.get(
                "scene_selection_rule"):
            problems.append(f"{artifact_id}: scene_selection_rule is required")
        for field_name in ("scene_metadata_sha256", "algorithm_config_sha256"):
            value = artifact.get(field_name)
            if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
                problems.append(f"{artifact_id}: {field_name} is required")
        if (not isinstance(artifact.get("scene_manifest_artifact_id"), str) or
                not artifact.get("scene_manifest_artifact_id")):
            problems.append(f"{artifact_id}: scene_manifest_artifact_id is required")
        if not isinstance(artifact.get("valid_scene_count_artifact_id"), str) or not artifact.get(
                "valid_scene_count_artifact_id"):
            problems.append(
                f"{artifact_id}: a bound per-cell valid-scene-count artifact is required")
        if "support proxy" not in _phase_text(artifact.get("processing")).lower():
            problems.append(
                f"{artifact_id}: processing must identify this as a support proxy, not detection")
    return problems


def validate_artifact_semantics(artifact: Mapping[str, Any]) -> list[str]:
    """Public semantic validator shared by manifest and raster adapters."""
    return _validate_artifact_semantics(artifact)


def _validate_b_manifest_declarations(manifest: Mapping[str, Any]) -> list[str]:
    """Validate package-level declarations required for strict B replay."""
    problems: list[str] = []
    expected_strings = {
        "manifest_hash_encoding": "canonical_json",
        "canonical_hash_domain": "canonical_json_without_manifest_sha256",
        "data_contract_source_path": AUTHORITATIVE_DATA_CONTRACT_PATH,
        "framework_contract_source_path": AUTHORITATIVE_FRAMEWORK_CONTRACT_PATH,
    }
    for key, expected in expected_strings.items():
        if manifest.get(key) != expected:
            problems.append(f"B manifest {key} must be {expected!r}")
    for key in ("data_contract_source_sha256", "framework_contract_source_sha256",
                "dirty_diff_sha256"):
        value = manifest.get(key)
        if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
            problems.append(f"B manifest {key} must be a lowercase SHA-256")
    if not isinstance(manifest.get("code_revision"), str) or not manifest.get(
            "code_revision"):
        problems.append("B manifest code_revision is required")
    inventory = manifest.get("package_inventory")
    if not isinstance(inventory, list) or not inventory:
        problems.append("B manifest package_inventory must be a non-empty list")
    else:
        for index, entry in enumerate(inventory):
            if not isinstance(entry, Mapping):
                problems.append(f"B package_inventory[{index}] must be an object")
                continue
            if not isinstance(entry.get("relative_path"), str) or not entry.get(
                    "relative_path"):
                problems.append(
                    f"B package_inventory[{index}] relative_path is required")
            digest = entry.get("sha256")
            if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
                problems.append(
                    f"B package_inventory[{index}] sha256 is required")
            size = entry.get("bytes")
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                problems.append(
                    f"B package_inventory[{index}] bytes is required")
    waivers = manifest.get("waivers")
    if not isinstance(waivers, list):
        problems.append("B manifest waivers must be an explicit list")
    return problems


def _find_authoritative_repo_root(handoff_root: Path,
                                  repo_root: Optional[str | Path]) -> Optional[Path]:
    """Resolve the checkout that owns the two declared contract sources."""
    candidates = ([Path(repo_root).resolve()] if repo_root is not None else
                  [handoff_root.resolve(), *handoff_root.resolve().parents])
    for candidate in candidates:
        if ((candidate / AUTHORITATIVE_DATA_CONTRACT_PATH).is_file() and
                (candidate / AUTHORITATIVE_FRAMEWORK_CONTRACT_PATH).is_file()):
            return candidate
    return None


def _validate_contract_source_bindings(
        manifest: Mapping[str, Any], handoff_root: Path,
        repo_root: Optional[str | Path]) -> tuple[list[str], dict[str, Any]]:
    """Compare manifest source commitments with the actual authoritative files."""
    resolved_root = _find_authoritative_repo_root(handoff_root, repo_root)
    diagnostics: dict[str, Any] = {
        "repo_root": str(resolved_root) if resolved_root else None,
        "bindings": {},
    }
    if resolved_root is None:
        return ["B manifest authoritative contract source root could not be resolved"], diagnostics

    problems: list[str] = []
    for path_key, hash_key in (
            ("data_contract_source_path", "data_contract_source_sha256"),
            ("framework_contract_source_path", "framework_contract_source_sha256")):
        relative = manifest.get(path_key)
        declared = manifest.get(hash_key)
        if not isinstance(relative, str) or not relative:
            continue
        path, path_error = _safe_relative_path(relative)
        if path_error or path is None:
            problems.append(f"B manifest {path_key} is not a safe relative path")
            continue
        source = (resolved_root / path).resolve(strict=False)
        try:
            source.relative_to(resolved_root)
        except ValueError:
            problems.append(f"B manifest {path_key} escapes the authoritative root")
            continue
        if not source.is_file():
            problems.append(f"B manifest source file is missing: {relative}")
            continue
        actual = sha256_file(source)
        diagnostics["bindings"][path_key] = {
            "relative_path": relative,
            "declared_sha256": declared,
            "actual_sha256": actual,
            "passed": declared == actual,
        }
        if declared != actual:
            problems.append(
                f"B manifest {hash_key} does not match authoritative source {relative}")
    return problems, diagnostics


def _validate_package_inventory_files(root: Path,
                                      manifest: Mapping[str, Any]) -> list[str]:
    """Verify every locally packaged extra is safe, present, and hash-bound."""
    problems: list[str] = []
    inventory = manifest.get("package_inventory")
    if not isinstance(inventory, list):
        return problems
    seen_paths: set[str] = set()
    for index, entry in enumerate(inventory):
        if not isinstance(entry, Mapping):
            continue
        relative, path_error = _safe_relative_path(entry.get("relative_path"))
        if path_error or relative is None:
            problems.append(
                f"B package_inventory[{index}] has unsafe relative_path")
            continue
        rel_text = relative.as_posix()
        if rel_text in seen_paths:
            problems.append(
                f"B package_inventory[{index}] duplicates relative_path: {rel_text}")
        seen_paths.add(rel_text)
        resolved, resolve_error = _path_under_root(root, relative)
        if resolve_error or resolved is None:
            problems.append(
                f"B package_inventory[{index}] {resolve_error or 'path is invalid'}")
            continue
        if not resolved.exists() or not resolved.is_file():
            problems.append(
                f"B package_inventory[{index}] file is missing: {relative.as_posix()}")
            continue
        if resolved.stat().st_size != entry.get("bytes"):
            problems.append(
                f"B package_inventory[{index}] byte count mismatch: {relative.as_posix()}")
        digest = entry.get("sha256")
        if isinstance(digest, str) and SHA256_RE.fullmatch(digest):
            if sha256_file(resolved) != digest:
                problems.append(
                    f"B package_inventory[{index}] checksum mismatch: {relative.as_posix()}")
    return problems


def _validate_b_source_references(manifest: Mapping[str, Any],
                                  artifact_ids: set[str]) -> list[str]:
    """Require every derived B source ID to be registered or inventoried."""
    known = set(artifact_ids)
    inventory = manifest.get("package_inventory")
    if isinstance(inventory, list):
        known.update(
            str(entry.get("artifact_id"))
            for entry in inventory
            if isinstance(entry, Mapping) and isinstance(entry.get("artifact_id"), str)
            and entry.get("artifact_id"))
    problems: list[str] = []
    for artifact in manifest.get("artifacts", []):
        if not isinstance(artifact, Mapping):
            continue
        spec = C.B_ARTIFACT_SEMANTICS.get(str(artifact.get("artifact_id")))
        if spec is None:
            continue
        source_ids = artifact.get("source_artifact_ids")
        if isinstance(source_ids, list):
            unknown = sorted({str(source_id) for source_id in source_ids} - known)
            if unknown:
                problems.append(
                    f"{artifact.get('artifact_id')}: unregistered source artifact IDs: "
                    + ", ".join(unknown))
        if artifact.get("artifact_id") == "hanging_ice_support_grid":
            for field_name, label in (
                    ("scene_manifest_artifact_id", "scene manifest"),
                    ("valid_scene_count_artifact_id", "valid-scene-count")):
                bound_id = artifact.get(field_name)
                if isinstance(bound_id, str) and bound_id not in known:
                    problems.append(
                        "hanging_ice_support_grid: " + label +
                        f" artifact is not registered or inventoried: {bound_id}")
    return problems


def validate_phase_artifact(artifact: Mapping[str, Any], phase: str) -> list[str]:
    """Validate the semantic contract for one phase artifact.

    Generic manifest verification proves file identity.  This function proves
    that an artifact with a given ID is the kind of input the requested phase
    is authorized to consume; it never infers source identity from a filename
    or silently accepts a substitute product.
    """
    phase_name = str(phase).upper()
    if phase_name not in _PHASE_ARTIFACT_CONTRACTS:
        return [f"no artifact contract for phase {phase!r}"]
    if not isinstance(artifact, Mapping):
        return ["phase artifact must be an object"]
    artifact_id = artifact.get("artifact_id")
    spec = _PHASE_ARTIFACT_CONTRACTS[phase_name].get(str(artifact_id))
    if spec is None:
        return []
    problems: list[str] = []
    for key in ("role", "kind"):
        expected = spec[key]
        if artifact.get(key) != expected:
            problems.append(
                f"{artifact_id}: {key} {artifact.get(key)!r} does not match "
                f"phase {phase_name} contract {expected!r}")
    if artifact.get("status") != "READY":
        problems.append(
            f"{artifact_id}: required phase {phase_name} artifact status is "
            f"{artifact.get('status')!r}, not READY")
        return problems

    processing = _phase_text(artifact.get("processing")).lower()
    for marker in _READY_PROCESSING_MARKERS:
        if marker in processing:
            problems.append(
                f"{artifact_id}: READY artifact processing declares an "
                f"incomplete or substituted product ({marker})")
    expected_crs = spec.get("crs")
    if expected_crs is not None and artifact.get("crs") != expected_crs:
        problems.append(
            f"{artifact_id}: CRS {artifact.get('crs')!r} does not match "
            f"{expected_crs!r}")
    expected_source = str(spec.get("source_semantics", "")).lower()
    if expected_source:
        declared_source = _phase_text(artifact.get("source_semantics")).lower()
        if not declared_source:
            problems.append(f"{artifact_id}: source_semantics metadata is required")
        elif expected_source not in declared_source:
            problems.append(
                f"{artifact_id}: source semantics {artifact.get('source_semantics')!r} "
                f"do not identify {spec['source_semantics']}")
    if spec.get("target_grid"):
        problems.extend(_validate_target_grid_metadata(artifact))
    problems.extend(_validate_artifact_semantics(artifact))
    return problems


def validate_artifact_bundle(root: str | Path,
                            artifact: Mapping[str, Any]) -> list[str]:
    """Verify a registered vector artifact and all consumed sidecars.

    The primary ``sha256``/``bytes`` fields identify the main path.  A vector
    bundle must additionally enumerate and checksum its complete structural
    sidecars (``.shp/.shx/.dbf/.prj``); sidecars are provenance inputs, never
    ranking components.
    """
    if not isinstance(artifact, Mapping):
        return ["artifact must be an object"]
    if artifact.get("status") != "READY":
        return []
    if artifact.get("kind") != "vector":
        return []
    root_path = Path(root)
    entries = artifact.get("bundle_files")
    if not isinstance(entries, list) or not entries:
        return [f"{artifact.get('artifact_id')}: vector bundle_files are required"]
    problems: list[str] = []
    seen: set[str] = set()
    entry_by_path: dict[str, Mapping[str, Any]] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            problems.append(f"{artifact.get('artifact_id')}: bundle_files[{index}] must be an object")
            continue
        relative, path_error = _safe_relative_path(entry.get("relative_path"))
        if path_error or relative is None:
            problems.append(
                f"{artifact.get('artifact_id')}: bundle_files[{index}] "
                f"{path_error or 'relative_path is required'}")
            continue
        rel_text = relative.as_posix()
        if rel_text in seen:
            problems.append(f"{artifact.get('artifact_id')}: duplicate vector bundle path {rel_text}")
            continue
        seen.add(rel_text)
        entry_by_path[rel_text] = entry
        digest = entry.get("sha256")
        size = entry.get("bytes")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            problems.append(f"{artifact.get('artifact_id')}: invalid bundle sha256 for {rel_text}")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            problems.append(f"{artifact.get('artifact_id')}: invalid bundle bytes for {rel_text}")
        resolved, resolve_error = _path_under_root(root_path, relative)
        if resolve_error:
            problems.append(f"{artifact.get('artifact_id')}: {resolve_error}")
            continue
        assert resolved is not None
        if not resolved.exists():
            problems.append(f"{artifact.get('artifact_id')}: missing vector bundle file {rel_text}")
            continue
        if not resolved.is_file():
            problems.append(f"{artifact.get('artifact_id')}: vector bundle path is not a file {rel_text}")
            continue
        if isinstance(size, int) and resolved.stat().st_size != size:
            problems.append(f"{artifact.get('artifact_id')}: vector bundle byte count mismatch {rel_text}")
        if isinstance(digest, str) and SHA256_RE.fullmatch(digest):
            actual = sha256_file(resolved)
            if actual != digest:
                problems.append(f"{artifact.get('artifact_id')}: vector bundle checksum mismatch {rel_text}")

    main = _safe_relative_path(artifact.get("relative_path"))[0]
    main_text = main.as_posix() if main is not None else None
    if main_text is None or main_text not in entry_by_path:
        problems.append(f"{artifact.get('artifact_id')}: vector bundle must include the primary relative_path")
    elif (entry_by_path[main_text].get("sha256") != artifact.get("sha256") or
          entry_by_path[main_text].get("bytes") != artifact.get("bytes")):
        problems.append(f"{artifact.get('artifact_id')}: primary and bundle checksum metadata disagree")

    if main_text and main_text.lower().endswith(".shp"):
        main_path = Path(main_text)
        required_sidecars = {
            main_path.with_suffix(suffix).as_posix() for suffix in
            C.PHASE_VECTOR_BUNDLE_REQUIRED_SIDECARS
        }
        missing = sorted(required_sidecars - set(entry_by_path))
        if missing:
            problems.append(
                f"{artifact.get('artifact_id')}: vector bundle missing structural sidecars: "
                + ", ".join(missing))
    return problems


@dataclass(frozen=True)
class InputManifestVerification:
    """Structured result for a read-only input-manifest verification."""

    ok: bool
    can_run_primary: bool
    errors: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    ready_artifact_ids: tuple[str, ...] = field(default_factory=tuple)
    unavailable_artifact_ids: tuple[str, ...] = field(default_factory=tuple)
    nonready_artifact_ids: tuple[str, ...] = field(default_factory=tuple)
    checks: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "can_run_primary": self.can_run_primary,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "ready_artifact_ids": list(self.ready_artifact_ids),
            "unavailable_artifact_ids": list(self.unavailable_artifact_ids),
            "nonready_artifact_ids": list(self.nonready_artifact_ids),
            "checks": dict(self.checks),
        }


def verify_input_manifest(
    manifest: Mapping[str, Any],
    root: str | Path,
    *,
    expected_contract_sha256: Optional[str] = None,
    expected_framework_contract_sha256: Optional[str] = None,
    trusted_manifest_sha256: Optional[str] = None,
    required_artifact_ids: Optional[Iterable[str]] = None,
    required_role: Optional[str] = None,
    primary_roles: Iterable[str] = PRIMARY_ROLES,
    phase: Optional[str] = None,
    repo_root: Optional[str | Path] = None,
    require_self_hash: bool = True,
    scan_root_for_raw_slc: bool = True,
) -> InputManifestVerification:
    """Verify a reconciled input manifest without modifying any file.

    ``expected_contract_sha256`` binds the handoff's data-contract hash.
    ``expected_framework_contract_sha256`` binds the framework semantic
    contract separately.  The two domains must not be silently substituted for
    one another before a primary run.
    """
    errors: list[str] = []
    warnings: list[str] = []
    ready: list[str] = []
    unavailable: list[str] = []
    nonready: list[str] = []
    checks: dict[str, Any] = {}
    root_path = Path(root)
    phase_name = str(phase).upper() if phase is not None else None
    phase_contract_errors: list[str] = []
    if phase_name is not None and phase_name not in _PHASE_ARTIFACT_CONTRACTS:
        errors.append(f"no artifact contract for phase {phase!r}")

    if not isinstance(manifest, Mapping):
        return InputManifestVerification(False, False,
                                         ("manifest must be a mapping",))
    if not isinstance(manifest.get("schema_version"), str):
        errors.append("missing manifest schema_version")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        errors.append("artifacts must be a list")
        artifacts = []
    if manifest.get("artifact_count") != len(artifacts):
        errors.append("artifact_count does not equal artifacts length")

    root_exists = root_path.exists() and root_path.is_dir()
    checks["root_exists"] = root_exists
    if not root_exists:
        errors.append(f"manifest root is not an existing directory: {root_path}")

    stored_hash = manifest.get("manifest_sha256")
    canonical_hash_value: Optional[str] = None
    self_hash_verified = False
    if require_self_hash and not isinstance(stored_hash, str):
        errors.append("manifest_sha256 is required")
    elif isinstance(stored_hash, str):
        try:
            expected_hash = canonical_input_manifest_hash(manifest)
        except (TypeError, ValueError) as exc:
            expected_hash = None
            errors.append(f"manifest contains non-canonical JSON values: {exc}")
        if expected_hash is not None:
            canonical_hash_value = expected_hash
            checks["manifest_sha256"] = expected_hash
            if stored_hash == expected_hash:
                checks["manifest_hash_encoding"] = "canonical_json"
                self_hash_verified = True
            else:
                try:
                    compatibility_hash = pretty_input_manifest_hash(manifest)
                except (TypeError, ValueError):
                    compatibility_hash = None
                if stored_hash == compatibility_hash:
                    checks["manifest_hash_encoding"] = "reconciled_pretty_json_compatibility"
                    self_hash_verified = True
                    warnings.append(
                        "manifest uses downloader pretty-JSON self-hash compatibility; "
                        "new framework artifacts must use canonical_json")
                else:
                    errors.append("manifest_sha256 does not match canonical manifest bytes")
    checks["self_hash_verified"] = self_hash_verified
    checks["canonical_manifest_authorized"] = bool(
        self_hash_verified and
        checks.get("manifest_hash_encoding") == "canonical_json")

    trusted_anchor_valid = (
        isinstance(trusted_manifest_sha256, str) and
        bool(SHA256_RE.fullmatch(trusted_manifest_sha256)))
    if trusted_manifest_sha256 is not None and not trusted_anchor_valid:
        errors.append(
            "trusted_manifest_sha256 must be a lowercase 64-character SHA-256")
    trusted_anchor_bound = bool(
        trusted_anchor_valid and canonical_hash_value is not None and
        canonical_hash_value == trusted_manifest_sha256)
    if trusted_anchor_valid and not trusted_anchor_bound:
        errors.append(
            "canonical manifest hash does not match the trusted manifest anchor")
    checks["trusted_manifest_anchor_bound"] = trusted_anchor_bound
    checks["trusted_manifest_sha256_supplied"] = trusted_manifest_sha256 is not None

    data_contract = manifest.get("contract_sha256")
    if not isinstance(data_contract, str) or not SHA256_RE.fullmatch(data_contract):
        errors.append("contract_sha256 must be a lowercase 64-character SHA-256")
    if expected_contract_sha256 is not None and (
            not isinstance(expected_contract_sha256, str) or
            not SHA256_RE.fullmatch(expected_contract_sha256)):
        errors.append("requested contract hash is not a lowercase 64-character SHA-256")
    elif expected_contract_sha256 is not None and data_contract != expected_contract_sha256:
        errors.append("manifest contract_sha256 does not match the requested contract")
    checks["contract_bound"] = expected_contract_sha256 is not None and (
        data_contract == expected_contract_sha256)
    if expected_contract_sha256 is None:
        warnings.append("manifest contract is format-checked but not bound to a requested contract")

    declared_framework = manifest.get("framework_contract_sha256")
    if declared_framework is not None and (
            not isinstance(declared_framework, str) or
            not SHA256_RE.fullmatch(declared_framework)):
        errors.append(
            "framework_contract_sha256 must be a lowercase 64-character SHA-256")
    if expected_framework_contract_sha256 is not None and (
            not isinstance(expected_framework_contract_sha256, str) or
            not SHA256_RE.fullmatch(expected_framework_contract_sha256)):
        errors.append(
            "requested framework contract hash is not a lowercase 64-character SHA-256")
    elif (expected_framework_contract_sha256 is not None and
          declared_framework is not None and
          declared_framework != expected_framework_contract_sha256):
        errors.append(
            "manifest framework_contract_sha256 does not match the requested framework contract")
    expected_framework_valid = (
        isinstance(expected_framework_contract_sha256, str) and
        bool(SHA256_RE.fullmatch(expected_framework_contract_sha256)))
    runtime_framework_match = (
        expected_framework_valid and
        expected_framework_contract_sha256 == C.contract_hash())
    if expected_framework_valid and not runtime_framework_match:
        errors.append(
            "requested framework contract hash does not match the runtime "
            "framework contract")
    declared_framework_valid = (
        declared_framework is None or
        (isinstance(declared_framework, str) and
         bool(SHA256_RE.fullmatch(declared_framework))))
    # The runtime binding is established by the verifier's expected hash.  A
    # manifest-side declaration is optional, but if present it must be valid
    # and agree with that runtime binding.
    checks["framework_contract_declared"] = declared_framework is not None
    checks["framework_contract_declaration_valid"] = declared_framework_valid
    checks["framework_contract_declaration_bound"] = (
        declared_framework is not None and expected_framework_valid and
        runtime_framework_match and
        declared_framework == expected_framework_contract_sha256)
    checks["framework_contract_runtime_bound"] = (
        runtime_framework_match and declared_framework_valid and
        (declared_framework is None or
         declared_framework == expected_framework_contract_sha256))
    # Backward-compatible alias: this field means runtime binding, not merely
    # the presence of an optional manifest declaration.
    checks["framework_contract_bound"] = checks["framework_contract_runtime_bound"]
    if expected_framework_contract_sha256 is None:
        warnings.append(
            "framework semantic contract is not bound; primary execution is blocked")

    ids: set[str] = set()
    paths: set[str] = set()
    raw_required = (required_artifact_ids if required_artifact_ids is not None
                    else manifest.get("required_artifact_ids"))
    required: set[str] = set()
    if raw_required is not None:
        if isinstance(raw_required, str) or not isinstance(
                raw_required, (list, tuple, set, frozenset)):
            errors.append("required_artifact_ids must be a list of strings")
        else:
            required_values = list(raw_required)
            if len(required_values) != len(set(map(str, required_values))):
                errors.append("required_artifact_ids contains duplicates")
            for value in required_values:
                if not isinstance(value, str) or not value:
                    errors.append("required_artifact_ids must contain non-empty strings")
                else:
                    required.add(value)
    try:
        primary_role_set = set(primary_roles)
    except (TypeError, ValueError):
        primary_role_set = set(PRIMARY_ROLES)
        errors.append("primary_roles must be an iterable of role strings")
    if not all(isinstance(role, str) and role for role in primary_role_set):
        errors.append("primary_roles must contain non-empty strings")
    if required_role is not None and (
            not isinstance(required_role, str) or
            not required_role or required_role not in primary_role_set):
        errors.append("required_role must be one of the declared primary roles")
        required_role = None

    for index, artifact in enumerate(artifacts):
        prefix = f"artifact[{index}]"
        if not isinstance(artifact, Mapping):
            errors.append(f"{prefix} must be an object")
            continue
        missing = sorted(REQUIRED_ARTIFACT_FIELDS - set(artifact))
        if missing:
            errors.append(f"{prefix} missing fields: {', '.join(missing)}")
        artifact_id = artifact.get("artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id:
            errors.append(f"{prefix} has no non-empty artifact_id")
            artifact_id = f"<index:{index}>"
        elif artifact_id in ids:
            errors.append(f"duplicate artifact_id: {artifact_id}")
        ids.add(artifact_id)

        role = artifact.get("role")
        if role not in _HANDOFF_ROLES:
            errors.append(f"{artifact_id}: invalid handoff role {role!r}")
        kind = artifact.get("kind")
        if kind not in _HANDOFF_KINDS:
            errors.append(f"{artifact_id}: invalid handoff kind {kind!r}")
        if phase_name is not None:
            phase_contract_errors.extend(validate_phase_artifact(artifact, phase_name))
            if (phase_name == "B" and artifact.get("artifact_id") in
                    _PHASE_ARTIFACT_CONTRACTS["B"] and
                    _PHASE_ARTIFACT_CONTRACTS["B"].get(
                        str(artifact.get("artifact_id")), {}).get("vector_bundle")):
                phase_contract_errors.extend(validate_artifact_bundle(root_path, artifact))

        status = artifact.get("status")
        if status not in DATA_STATUSES:
            errors.append(f"{artifact_id}: invalid data status {status!r}")
            status = "INVALID"
        if status == "READY":
            ready.append(artifact_id)
        else:
            nonready.append(artifact_id)
        if status == "UNAVAILABLE":
            unavailable.append(artifact_id)
        if status in {"UNAVAILABLE", "INCOMPLETE", "INVALID", "PROVISIONAL"}:
            warnings.append(f"{artifact_id}: status {status} is not consumable")

        relative, path_error = _safe_relative_path(artifact.get("relative_path"))
        if path_error:
            errors.append(f"{artifact_id}: {path_error}")
            relative = None
        if relative is not None:
            rel_text = relative.as_posix()
            if rel_text in paths:
                errors.append(f"duplicate artifact relative_path: {rel_text}")
            paths.add(rel_text)
        elif status == "READY":
            errors.append(f"{artifact_id}: READY artifact must have relative_path")

        digest = artifact.get("sha256")
        if status == "READY":
            if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
                errors.append(f"{artifact_id}: READY artifact has invalid sha256")
        elif digest not in ("", None) and (
                not isinstance(digest, str) or not SHA256_RE.fullmatch(digest)):
            errors.append(f"{artifact_id}: non-ready artifact has invalid sha256")

        byte_count = artifact.get("bytes")
        if not isinstance(byte_count, int) or isinstance(byte_count, bool) or byte_count < 0:
            errors.append(f"{artifact_id}: bytes must be a non-negative integer")

        is_required = artifact_id in required
        if status != "READY" and is_required:
            warnings.append(f"required primary artifact {artifact_id} is {status}")
        if (is_required and required_role is not None and
                artifact.get("role") != required_role):
            errors.append(
                f"{artifact_id}: required artifact role {artifact.get('role')!r} "
                f"does not match required role {required_role!r}")

        if relative is None:
            continue
        resolved, resolve_error = _path_under_root(root_path, relative)
        if resolve_error:
            errors.append(f"{artifact_id}: {resolve_error}")
            continue
        assert resolved is not None
        if status != "READY":
            continue
        if not resolved.exists():
            errors.append(f"{artifact_id}: missing artifact file {relative.as_posix()}")
            continue
        if not resolved.is_file():
            errors.append(f"{artifact_id}: artifact path is not a file")
            continue
        actual_bytes = resolved.stat().st_size
        if actual_bytes != byte_count:
            errors.append(f"{artifact_id}: byte count mismatch ({byte_count} -> {actual_bytes})")
        actual_hash = sha256_file(resolved)
        if actual_hash != digest:
            errors.append(f"{artifact_id}: checksum mismatch ({digest} -> {actual_hash})")

    if scan_root_for_raw_slc and root_exists:
        raw_hits = check_no_raw_slc_tree(root_path)
        if raw_hits:
            errors.extend(raw_hits[:20])
            if len(raw_hits) > 20:
                errors.append(f"raw SLC scan found {len(raw_hits)} paths")

    required_ids = required
    if not required_ids:
        warnings.append("required_artifact_ids are not declared; primary execution is blocked")
    if phase_name == "B":
        phase_contract_errors.extend(_validate_b_manifest_declarations(manifest))
        source_errors, source_diagnostics = _validate_contract_source_bindings(
            manifest, root_path, repo_root)
        phase_contract_errors.extend(source_errors)
        checks["contract_source_bindings"] = source_diagnostics
        phase_contract_errors.extend(_validate_package_inventory_files(
            root_path, manifest))
        phase_contract_errors.extend(_validate_b_source_references(manifest, ids))
        declared_required = manifest.get("required_artifact_ids")
        expected_required = list(PHASE_REQUIRED_ARTIFACT_IDS["B"])
        if declared_required != expected_required:
            phase_contract_errors.append(
                "B manifest required_artifact_ids must equal the canonical phase list")
        declared_mapping = manifest.get("target_grid_artifact_ids")
        if declared_mapping != dict(B_TARGET_GRID_ARTIFACT_IDS):
            phase_contract_errors.append(
                "B manifest target_grid_artifact_ids must equal the canonical component mapping")
    blocking_statuses = {"INCOMPLETE", "INVALID", "UNAVAILABLE", "PROVISIONAL"}
    blocking_required = [
        str(a.get("artifact_id")) for a in artifacts
        if isinstance(a, Mapping)
        and str(a.get("artifact_id")) in required_ids
        and a.get("status") in blocking_statuses
    ]
    missing_required = sorted(required_ids - ids)
    if missing_required:
        errors.append("required artifact IDs are absent: " + ", ".join(missing_required))
    checks["primary_role_artifact_ids"] = sorted(
        str(a.get("artifact_id")) for a in artifacts
        if isinstance(a, Mapping) and str(a.get("role")) in primary_role_set)
    if phase_name is not None:
        checks["phase"] = phase_name
        checks["phase_contract_valid"] = not phase_contract_errors
        checks["phase_contract_errors"] = list(dict.fromkeys(phase_contract_errors))
        errors.extend(phase_contract_errors)
    can_run_primary = bool(
        not errors and checks.get("canonical_manifest_authorized") and
        checks.get("contract_bound") and
        checks.get("framework_contract_bound") and required_ids and
        not missing_required and not blocking_required and
        (trusted_manifest_sha256 is None or
         checks.get("trusted_manifest_anchor_bound") is True) and
        (phase_name is None or checks.get("phase_contract_valid") is True))
    checks["required_declaration"] = bool(required_ids)
    checks["artifact_count"] = len(artifacts)
    checks["artifact_status_counts"] = {
        status: sum(1 for artifact in artifacts
                    if isinstance(artifact, Mapping) and artifact.get("status") == status)
        for status in sorted(DATA_STATUSES)
    }
    checks["required_primary_artifacts"] = sorted(required_ids)
    checks["blocking_required_artifacts"] = sorted(blocking_required)
    if not scan_root_for_raw_slc or not root_exists:
        checks["raw_slc_scan"] = "NOT_RUN"
    elif any("raw SLC" in error for error in errors):
        checks["raw_slc_scan"] = "FAIL"
    else:
        checks["raw_slc_scan"] = "PASS"
    return InputManifestVerification(
        ok=not errors,
        can_run_primary=can_run_primary,
        errors=tuple(errors),
        warnings=tuple(dict.fromkeys(warnings)),
        ready_artifact_ids=tuple(sorted(ready)),
        unavailable_artifact_ids=tuple(sorted(unavailable)),
        nonready_artifact_ids=tuple(sorted(nonready)),
        checks=checks,
    )


def verify_phase_manifest(
    manifest: Mapping[str, Any], root: str | Path, phase: str, *,
    expected_contract_sha256: Optional[str] = None,
    expected_framework_contract_sha256: Optional[str] = None,
    trusted_manifest_sha256: Optional[str] = None,
    repo_root: Optional[str | Path] = None,
    scan_root_for_raw_slc: bool = True,
) -> InputManifestVerification:
    """Verify a phase-specific handoff with an explicit required-ID set."""
    try:
        required = PHASE_REQUIRED_ARTIFACT_IDS[str(phase).upper()]
    except KeyError as exc:
        raise ValueError(f"no required artifact contract for phase {phase!r}") from exc
    return verify_input_manifest(
        manifest, root,
        expected_contract_sha256=expected_contract_sha256,
        expected_framework_contract_sha256=expected_framework_contract_sha256,
        trusted_manifest_sha256=trusted_manifest_sha256,
        repo_root=repo_root,
        required_artifact_ids=required,
        required_role=str(phase).upper(),
        phase=str(phase).upper(),
        scan_root_for_raw_slc=scan_root_for_raw_slc,
    )


def verify_canonical_input_manifest(
    manifest: Mapping[str, Any],
    root: str | Path,
    *,
    trusted_manifest_sha256: Optional[str],
    expected_contract_sha256: Optional[str],
    expected_framework_contract_sha256: Optional[str],
    required_artifact_ids: Optional[Iterable[str]] = None,
    required_role: Optional[str] = None,
    primary_roles: Iterable[str] = PRIMARY_ROLES,
    phase: Optional[str] = None,
    repo_root: Optional[str | Path] = None,
    scan_root_for_raw_slc: bool = True,
) -> InputManifestVerification:
    """Run the non-compatibility manifest authorization boundary.

    ``verify_input_manifest`` intentionally retains the downloader's pretty
    JSON result as a diagnostic for historical manifests.  This helper is the
    strict production-facing boundary: both contract domains and an external
    trusted canonical manifest digest are required, so a generated or
    compatibility-only self-hash cannot authorize a primary run.
    """
    required_values = (
        ("expected_contract_sha256", expected_contract_sha256),
        ("expected_framework_contract_sha256", expected_framework_contract_sha256),
        ("trusted_manifest_sha256", trusted_manifest_sha256),
    )
    invalid = [
        f"{name} must be an explicit lowercase SHA-256"
        for name, value in required_values
        if not isinstance(value, str) or not SHA256_RE.fullmatch(value)
    ]
    if invalid:
        return InputManifestVerification(
            ok=False,
            can_run_primary=False,
            errors=tuple(invalid),
            checks={"strict_canonical_authorization": False},
        )
    return verify_input_manifest(
        manifest,
        root,
        expected_contract_sha256=expected_contract_sha256,
        expected_framework_contract_sha256=expected_framework_contract_sha256,
        trusted_manifest_sha256=trusted_manifest_sha256,
        required_artifact_ids=required_artifact_ids,
        required_role=required_role,
        primary_roles=primary_roles,
        phase=phase,
        repo_root=repo_root,
        require_self_hash=True,
        scan_root_for_raw_slc=scan_root_for_raw_slc,
    )
