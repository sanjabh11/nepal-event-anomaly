"""nepal.framework_v1.lineage — deep generation/receipt/source lineage proof.

Shallow loader readiness (``LOAD_READY``) is not provenance recovery.  This
module implements the fail-closed deep verification that must pass before a
candidate package may authorize primary execution:

- one enforced generation identity across manifests, receipts, audit packets,
  waivers, and results (:func:`bind_generation_identity`);
- deep candidate lineage: inventory<->filesystem equality, path safety,
  absolute production path rejection, receipt self-hashes and schema
  versions, waiver/audit/manifest triple consistency, and Sentinel-2
  selected/asset/grid hash-chain verification
  (:func:`verify_candidate_lineage`);
- a strict Phase A envelope verifier that rejects minimal synthetic gates
  (:func:`verify_phase_a_envelope`);
- runtime environment fingerprinting (:func:`environment_fingerprint`);
- generation-aware discovery of current pipeline reports that rejects stale,
  malformed, or mismatched artifacts (:func:`discover_current_run_reports`);
- a disk-reserve guard for every write/download path (:func:`disk_guard`).

Everything here is additive: no historical package is rewritten and no status
field is edited to clear a blocker.
"""
from __future__ import annotations

import platform
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from . import contract as C
from .provenance import sha256_canonical, sha256_file
from .input_manifest import canonical_input_manifest_hash

RECEIPT_SCHEMA_VERSION = "1.0.0"
MIN_FREE_BYTES_FOR_WRITES = 8 * (1024 ** 3)  # hard 8 GiB staging reserve
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_ABSOLUTE_RUN_PATH_RE = re.compile(
    r"(^|[\s\"'=(\[])/(Users|home|tmp|private/var|var/folders|Volumes)/",
    re.IGNORECASE,
)
_FINGERPRINT_PACKAGES = ("numpy", "rasterio", "pyproj", "shapely")


def _package_version(name: str) -> Optional[str]:
    try:  # deferred import: never required for package import
        module = __import__(name)
    except Exception:  # pragma: no cover - optional dependency boundary
        return None
    return getattr(module, "__version__", None)


def environment_fingerprint(requirements_path: Optional[str | Path] = None) -> dict:
    """Deterministic runtime fingerprint recorded with every artifact (G30)."""
    requirements_sha256 = None
    if requirements_path is not None:
        p = Path(requirements_path)
        requirements_sha256 = sha256_file(p) if p.is_file() else None
    return {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "executable": sys.executable,
        "package_versions": {name: _package_version(name)
                             for name in _FINGERPRINT_PACKAGES},
        "requirements_sha256": requirements_sha256,
    }


def environment_fingerprint_sha256(
        requirements_path: Optional[str | Path] = None) -> str:
    return sha256_canonical(environment_fingerprint(requirements_path))



# ---------------------------------------------------------------------------
# Disk reserve guard (G38)
# ---------------------------------------------------------------------------

def disk_guard(path: str | Path,
               minimum_free_bytes: int = MIN_FREE_BYTES_FOR_WRITES) -> dict:
    """Raise :class:`C.ContractViolation` when the write target is low on disk."""
    target = Path(path)
    probe = target if target.exists() else (
        target.parent if str(target.parent) else Path("."))
    free = shutil.disk_usage(probe).free
    result = {
        "path": str(target),
        "free_bytes": free,
        "minimum_free_bytes": minimum_free_bytes,
        "passed": free >= minimum_free_bytes,
    }
    if not result["passed"]:
        raise C.ContractViolation(
            f"disk reserve exhausted before write: "
            f"{free / (1024 ** 3):.2f} GiB free < required "
            f"{minimum_free_bytes / (1024 ** 3):.0f} GiB at {target}")
    return result


# ---------------------------------------------------------------------------
# Generation identity (G01)
# ---------------------------------------------------------------------------

def bind_generation_identity(payload: Mapping[str, Any], *,
                             candidate_generation_id: Optional[str] = None,
                             manifest_sha256: Optional[str] = None,
                             manifest_file_sha256: Optional[str] = None,
                             run_id: Optional[str] = None,
                             source_package_id: Optional[str] = None) -> dict:
    """Embed an enforced generation identity into an evidence payload."""
    if candidate_generation_id is not None and (
            not isinstance(candidate_generation_id, str)
            or not candidate_generation_id):
        raise ValueError("candidate_generation_id must be a non-empty string")
    if manifest_sha256 is not None and not SHA256_RE.fullmatch(manifest_sha256):
        raise ValueError("manifest_sha256 must be a lowercase SHA-256")
    if candidate_generation_id is None and manifest_sha256 is None:
        raise ValueError(
            "bind_generation_identity requires candidate_generation_id or "
            "manifest_sha256")
    bound = dict(payload)
    bound["candidate_generation_id"] = candidate_generation_id
    bound["manifest_sha256"] = manifest_sha256
    if manifest_file_sha256 is not None:
        if not SHA256_RE.fullmatch(manifest_file_sha256):
            raise ValueError("manifest_file_sha256 must be a lowercase SHA-256")
        bound["manifest_file_sha256"] = manifest_file_sha256
    if run_id is not None:
        bound["run_id"] = run_id
    if source_package_id is not None:
        bound["source_package_id"] = source_package_id
    return bound



def verify_generation_identity(payload: Mapping[str, Any], *,
                               candidate_generation_id: Optional[str] = None,
                               manifest_sha256: Optional[str] = None,
                               require_all: bool = False,
                               ) -> tuple[bool, list[str]]:
    """Verify a payload carries one consistent generation identity."""
    errors: list[str] = []
    if not isinstance(payload, Mapping):
        return False, ["generation identity payload must be a mapping"]
    present_generation = payload.get("candidate_generation_id")
    present_manifest = payload.get("manifest_sha256")
    if present_generation is None:
        errors.append("candidate_generation_id is missing")
    elif not isinstance(present_generation, str) or not present_generation:
        errors.append("candidate_generation_id must be a non-empty string")
    if present_manifest is None:
        errors.append("manifest_sha256 is missing")
    elif not isinstance(present_manifest, str) or not SHA256_RE.fullmatch(
            present_manifest):
        errors.append("manifest_sha256 must be a lowercase SHA-256")
    if candidate_generation_id is not None and present_generation is not None \
            and present_generation != candidate_generation_id:
        errors.append(
            f"candidate_generation_id mismatch: {present_generation!r} "
            f"!= {candidate_generation_id!r}")
    if manifest_sha256 is not None and present_manifest is not None \
            and present_manifest != manifest_sha256:
        errors.append(
            f"manifest_sha256 mismatch: {present_manifest!r} "
            f"!= {manifest_sha256!r}")
    if require_all and len(errors) > 0:
        return False, errors
    return (not errors), errors


# ---------------------------------------------------------------------------
# Strict Phase A envelope verification (G19)
# ---------------------------------------------------------------------------

def verify_phase_a_envelope(gate: Any, *,
                            expected_manifest_sha256: Optional[str] = None,
                            candidate_generation_id: Optional[str] = None,
                            require_generation_identity: bool = False,
                            ) -> tuple[bool, list[str]]:
    """Reject caller booleans and minimal synthetic A envelopes.

    A real A_CATALOG gate binds the catalog bytes (``catalog_sha256``), the
    frozen holdout plan (``holdout_plan_sha256``), explicit eligibility
    counts, and the mechanism non-adjudication scope.  Minimal synthetic
    envelopes carry none of these and must never authorize strict B.
    """
    if not isinstance(gate, Mapping):
        return False, ["A gate artifact must be a mapping"]
    errors: list[str] = []
    if gate.get("gate_id") != C.GateId.A_CATALOG.value:
        errors.append("A gate artifact must be a verified A_CATALOG gate")
    if not SHA256_RE.fullmatch(str(gate.get("gate_artifact_sha256") or "")):
        errors.append("A gate artifact self-hash is required")
    else:
        from .provenance import verify_gate_artifact
        ok, problems = verify_gate_artifact(
            gate, expected_gate_id=C.GateId.A_CATALOG.value)
        if not ok:
            errors.extend(problems)
    if gate.get("passed") is not True:
        errors.append("A_CATALOG gate artifact is not passed")
    for key in ("catalog_sha256", "holdout_plan_sha256"):
        if not SHA256_RE.fullmatch(str(gate.get(key) or "")):
            errors.append(f"A gate must bind {key}")
    if not isinstance(gate.get("n_eligible"), int):
        errors.append("A gate must record an integer n_eligible")
    mechanism = gate.get("mechanism_validation")
    if not isinstance(mechanism, Mapping) or \
            mechanism.get("mechanism_independently_adjudicated") is not False:
        errors.append(
            "A gate must declare mechanism_independently_adjudicated=false")
    identity_ok, identity_errors = verify_generation_identity(
        gate, candidate_generation_id=candidate_generation_id,
        manifest_sha256=expected_manifest_sha256)
    if require_generation_identity and not identity_ok:
        errors.extend(identity_errors)
    else:
        # Contradicting identity fields always reject the envelope.
        errors.extend(err for err in identity_errors if "mismatch" in err)
    return (not errors), errors

# ---------------------------------------------------------------------------
# Deep candidate lineage verification (G01/G02/G04/G08/G10/G11)
# ---------------------------------------------------------------------------

def _json_load(path: Path) -> Any:
    import json
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_strings(node: Any, path: str = "") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(node, Mapping):
        for key, value in node.items():
            found.extend(_iter_strings(value, f"{path}.{key}"))
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            found.extend(_iter_strings(value, f"{path}[{index}]"))
    elif isinstance(node, str):
        found.append((path, node))
    return found


@dataclass
class LineageVerification:
    """Result of :func:`verify_candidate_lineage` (evidence, not authority)."""

    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "checks": dict(self.checks),
        }


def _artifact_path_safety(root: Path, artifact_id: str,
                          relative: Any) -> Path:
    """Return the resolved artifact file, or raise on any unsafe path."""
    if not isinstance(relative, str) or not relative:
        raise C.ContractViolation(
            f"{artifact_id}: relative_path must be a string")
    p = Path(relative)
    if p.is_absolute():
        raise C.ContractViolation(
            f"{artifact_id}: absolute production path is forbidden: {relative}")
    if ".." in p.parts:
        raise C.ContractViolation(f"{artifact_id}: path traversal is forbidden")
    current = root
    for part in p.parts:
        current = current / part
        if current.is_symlink():
            raise C.ContractViolation(
                f"{artifact_id}: path contains a symlink: {current}")
    resolved_root = root.resolve(strict=True)
    candidate = (root / p).resolve(strict=False)
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise C.ContractViolation(
            f"{artifact_id}: path escapes the handoff root") from exc
    if not candidate.is_file():
        raise C.ContractViolation(f"{artifact_id}: artifact file is missing")
    return candidate
def _verify_receipt(artifact: Mapping[str, Any], artifact_id: str,
                    errors: list[str],
                    root: "Path | None" = None) -> dict[str, Any]:
    receipt = artifact.get("receipt")
    summary: dict[str, Any] = {"present": receipt is not None}
    if receipt is None:
        # File-bound producer receipts (G11): the manifest entry declares
        # ``producer_receipt_path`` + ``producer_receipt_sha256``; resolve,
        # hash-bind, and then schema-check the referenced receipt document.
        receipt_path = artifact.get("producer_receipt_path")
        receipt_sha = artifact.get("producer_receipt_sha256")
        if receipt_path is None and receipt_sha is None:
            return summary
        summary["present"] = True
        if not isinstance(receipt_path, str) or not receipt_path:
            errors.append(
                f"{artifact_id}: producer_receipt_path is required when "
                "producer_receipt_sha256 is declared")
            return summary
        if not isinstance(receipt_sha, str) or not SHA256_RE.fullmatch(
                receipt_sha):
            errors.append(
                f"{artifact_id}: producer_receipt_sha256 must be a "
                "lowercase SHA-256")
            return summary
        if root is None:
            errors.append(
                f"{artifact_id}: producer receipt cannot be verified "
                "without the package root")
            return summary
        try:
            resolved = _artifact_path_safety(
                root, f"{artifact_id}->producer_receipt", receipt_path)
        except C.ContractViolation as exc:
            errors.append(str(exc))
            return summary
        actual = sha256_file(resolved)
        if actual != receipt_sha:
            errors.append(
                f"{artifact_id}: producer_receipt_sha256 does not match "
                f"on-disk receipt bytes ({receipt_sha} -> {actual})")
            return summary
        try:
            receipt = _json_load(resolved)
        except (OSError, ValueError) as exc:
            errors.append(
                f"{artifact_id}: producer receipt unreadable: {exc}")
            return summary
        summary["producer_receipt_path"] = receipt_path
    if not isinstance(receipt, Mapping):
        errors.append(f"{artifact_id}: receipt must be a mapping")
        return summary
    schema = receipt.get("receipt_schema_version")
    if not isinstance(schema, str) or not schema:
        errors.append(
            f"{artifact_id}: receipt_schema_version is required (G11)")
    else:
        summary["receipt_schema_version"] = schema
    stored = receipt.get("receipt_sha256")
    if not isinstance(stored, str) or not SHA256_RE.fullmatch(stored):
        errors.append(f"{artifact_id}: receipt canonical self-hash is required")
    else:
        expected = sha256_canonical({
            key: value for key, value in receipt.items()
            if key != "receipt_sha256"})
        if stored != expected:
            errors.append(f"{artifact_id}: receipt canonical self-hash mismatch")
        else:
            summary["receipt_sha256_verified"] = True
    return summary


def _verify_declared_hash_links(artifact: Mapping[str, Any], artifact_id: str,
                                artifacts_by_id: Mapping[str, Mapping],
                                root: Path, errors: list[str]) -> None:
    """Verify any in-manifest hash link (e.g. S2 asset-manifest chain, G06)."""
    for key, value in sorted(artifact.items()):
        if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
            continue
        if key in {"sha256", "gate_artifact_sha256", "receipt_sha256",
                   "artifact_sha256", "manifest_sha256",
                   "manifest_file_sha256"}:
            continue
        target_id = artifact.get(f"{key.rsplit('_sha256', 1)[0]}_artifact_id")
        if not isinstance(target_id, str) or target_id not in artifacts_by_id:
            continue
        target = artifacts_by_id[target_id]
        try:
            target_path = _artifact_path_safety(
                root, f"{artifact_id}->{target_id}",
                target.get("relative_path"))
        except C.ContractViolation as exc:
            errors.append(f"{artifact_id}: {key} target unverifiable: {exc}")
            continue
        actual = sha256_file(target_path)
        if actual != value:
            errors.append(
                f"{artifact_id}: declared {key} for {target_id} does not "
                f"match on-disk bytes ({value} -> {actual})")
def _verify_s2_selected_manifest(artifact: Mapping[str, Any], artifact_id: str,
                                 artifacts_by_id: Mapping[str, Mapping],
                                 root: Path, errors: list[str],
                                 warnings: list[str]) -> dict[str, Any]:
    """Sentinel-2 selected-manifest -> asset-manifest chain (G05/G06)."""
    summary: dict[str, Any] = {"checked": True}
    try:
        path = _artifact_path_safety(root, artifact_id,
                                     artifact.get("relative_path"))
    except C.ContractViolation as exc:
        errors.append(str(exc))
        return summary
    try:
        payload = _json_load(path)
    except (OSError, ValueError) as exc:
        errors.append(f"{artifact_id}: selected manifest unreadable: {exc}")
        return summary
    if not isinstance(payload, Mapping):
        errors.append(f"{artifact_id}: selected manifest must be a mapping")
        return summary
    declared_status = payload.get("status")
    summary["status"] = declared_status
    if declared_status is not None and declared_status != "READY":
        errors.append(
            f"{artifact_id}: selected manifest status {declared_status!r} "
            f"contradicts the top manifest binding (G05)")
    declared_asset_hash = payload.get("asset_manifest_sha256")
    declared_asset_id = payload.get("asset_manifest_artifact_id")
    if declared_asset_hash is not None or declared_asset_id is not None:
        if not isinstance(declared_asset_id, str) or \
                declared_asset_id not in artifacts_by_id:
            errors.append(
                f"{artifact_id}: asset_manifest_artifact_id does not resolve "
                f"in the top manifest")
        elif not isinstance(declared_asset_hash, str) or \
                not SHA256_RE.fullmatch(declared_asset_hash):
            errors.append(f"{artifact_id}: asset_manifest_sha256 is malformed")
        else:
            target = artifacts_by_id[declared_asset_id]
            try:
                target_path = _artifact_path_safety(
                    root, f"{artifact_id}->{declared_asset_id}",
                    target.get("relative_path"))
            except C.ContractViolation as exc:
                errors.append(
                    f"{artifact_id}: asset manifest unverifiable: {exc}")
            else:
                actual = sha256_file(target_path)
                if actual != declared_asset_hash:
                    errors.append(
                        f"{artifact_id}: declared asset-manifest hash is "
                        f"stale relative to on-disk bytes (G06): "
                        f"{declared_asset_hash} -> {actual}")
                else:
                    summary["asset_manifest_verified"] = True
    else:
        warnings.append(
            f"{artifact_id}: selected manifest declares no asset-manifest "
            f"hash link")
    return summary
def _verify_waiver_consistency(waiver_path: Optional[str | Path],
                               artifacts_by_id: Mapping[str, Mapping],
                               errors: list[str], warnings: list[str]) -> dict:
    """Waiver/manifest triple-consistency check (G04)."""
    summary: dict[str, Any] = {"checked": waiver_path is not None}
    if waiver_path is None:
        warnings.append("waiver file not provided; waiver consistency unchecked")
        return summary
    path = Path(waiver_path)
    if not path.is_file():
        errors.append(f"waiver file missing: {path}")
        return summary
    try:
        waiver = _json_load(path)
    except (OSError, ValueError) as exc:
        errors.append(f"waiver file unreadable: {exc}")
        return summary
    if not isinstance(waiver, Mapping):
        errors.append("waiver file must be a mapping")
        return summary
    entries = waiver.get("waivers")
    if not isinstance(entries, Mapping):
        errors.append("waiver file must declare a mapping of waivers")
        entries = {}
    checked = 0
    for artifact_id, entry in sorted(entries.items()):
        artifact = artifacts_by_id.get(artifact_id)
        if artifact is None:
            errors.append(f"waiver references unknown artifact {artifact_id}")
            continue
        if not isinstance(entry, Mapping) or "status" not in entry:
            errors.append(f"waiver for {artifact_id} must declare a status")
            continue
        checked += 1
        if entry.get("resolved") is True:
            if artifact.get("status") != entry.get("status"):
                errors.append(
                    f"waiver for {artifact_id} declares resolved "
                    f"{entry.get('status')!r} but manifest status is "
                    f"{artifact.get('status')!r}")
        if artifact.get("status") == "READY" and entry.get("resolved") is not True:
            warnings.append(
                f"waiver for {artifact_id} unresolved but manifest status READY")
    summary["entries_checked"] = checked
    return summary
def _verify_audit_packet_identity(audit_packet_path: Optional[str | Path],
                                  candidate_generation_id: str,
                                  manifest_sha256: str,
                                  errors: list[str], warnings: list[str]) -> dict:
    """Audit packet generation identity binding (G01/G03)."""
    summary: dict[str, Any] = {"checked": audit_packet_path is not None}
    if audit_packet_path is None:
        warnings.append(
            "audit packet not provided; audit generation identity unchecked")
        return summary
    path = Path(audit_packet_path)
    if not path.is_file():
        errors.append(f"audit packet missing: {path}")
        return summary
    try:
        packet = _json_load(path)
    except (OSError, ValueError) as exc:
        errors.append(f"audit packet unreadable: {exc}")
        return summary
    if not isinstance(packet, Mapping):
        errors.append("audit packet must be a mapping")
        return summary
    packet_generation = packet.get("candidate_generation_id")
    packet_manifest = packet.get("manifest_sha256")
    if packet_generation != candidate_generation_id:
        errors.append(
            f"audit packet candidate_generation_id {packet_generation!r} "
            f"does not match {candidate_generation_id!r}")
    if packet_manifest != manifest_sha256:
        errors.append(
            f"audit packet manifest_sha256 {packet_manifest!r} does not "
            f"match {manifest_sha256!r}")
    summary["generation_matches"] = (
        packet_generation == candidate_generation_id
        and packet_manifest == manifest_sha256)
    return summary


def _check_absolute_run_paths(manifest: Mapping[str, Any],
                              errors: list[str]) -> int:
    hits = 0
    for path, value in _iter_strings(manifest):
        if _ABSOLUTE_RUN_PATH_RE.search(value):
            hits += 1
            if hits <= 5:
                errors.append(
                    f"absolute run path in promoted metadata at {path}: {value!r}")
    if hits:
        errors.append(f"{hits} absolute run path(s) found in manifest metadata")
    return hits
def verify_candidate_lineage(manifest_path: str | Path, root: str | Path, *,
                             expected_manifest_sha256: str,
                             expected_data_contract_sha256: str,
                             expected_framework_contract_sha256: str,
                             candidate_generation_id: str,
                             trusted_manifest_file_sha256: Optional[str] = None,
                             waiver_path: Optional[str | Path] = None,
                             audit_packet_path: Optional[str | Path] = None,
                             requirements_path: Optional[str | Path] = None,
                             ) -> LineageVerification:
    """Deep lineage verification of one candidate generation (G1 gate).

    Verifies the canonical manifest self-hash, both contract bindings, the
    enforced generation identity, artifact status and inventory counts,
    inventory<->filesystem equality, path safety (no traversal, symlinks,
    absolute production paths, or escapes), receipt hashes and schema
    versions, in-manifest hash links, Sentinel-2 chain linkage, waiver/audit
    consistency, and the runtime environment fingerprint.
    """
    errors: list[str] = []
    warnings: list[str] = []
    checks: dict[str, Any] = {}

    root_path = Path(root)
    if not root_path.is_dir():
        return LineageVerification(False,
                                   [f"lineage root is not a directory: {root}"],
                                   checks=checks)
    manifest_p = Path(manifest_path)
    if not manifest_p.is_file():
        return LineageVerification(
            False, [f"manifest file missing: {manifest_p}"], checks=checks)
    try:
        manifest = _json_load(manifest_p)
    except (OSError, ValueError) as exc:
        return LineageVerification(
            False, [f"manifest unreadable: {exc}"], checks=checks)
    if not isinstance(manifest, Mapping):
        return LineageVerification(
            False, ["manifest must be a mapping"], checks=checks)

    # 1. Canonical manifest hash and trusted external digest (G01/G18).
    canonical = canonical_input_manifest_hash(manifest)
    checks["manifest_self_hash"] = canonical
    checks["expected_manifest_sha256"] = expected_manifest_sha256
    if canonical != expected_manifest_sha256:
        errors.append(
            f"canonical manifest self-hash {canonical} does not match the "
            f"expected {expected_manifest_sha256}")
    declared = manifest.get("manifest_sha256")
    if declared is not None and declared != expected_manifest_sha256:
        errors.append(
            f"manifest declares manifest_sha256 {declared!r} that differs "
            f"from the expected binding")
    checks["manifest_file_sha256"] = sha256_file(manifest_p)
    if trusted_manifest_file_sha256 is not None:
        actual = checks["manifest_file_sha256"]
        if actual != trusted_manifest_file_sha256:
            errors.append(
                f"trusted manifest file digest mismatch: "
                f"{actual} != {trusted_manifest_file_sha256}")
        checks["trusted_manifest_file_sha256_verified"] = (
            actual == trusted_manifest_file_sha256)
# 2. Semantic manifest verification with both contracts bound.
    from .input_manifest import verify_input_manifest
    try:
        verification = verify_input_manifest(
            manifest, root_path,
            expected_contract_sha256=expected_data_contract_sha256,
            expected_framework_contract_sha256=expected_framework_contract_sha256,
            repo_root=None)
        checks["input_manifest_verification"] = {
            "ok": verification.ok,
            "can_run_primary": verification.can_run_primary,
            "n_errors": len(verification.errors),
            "n_warnings": len(verification.warnings),
        }
        errors.extend(f"input manifest: {error}" for error in verification.errors)
        warnings.extend(f"input manifest: {warning}"
                        for warning in verification.warnings)
    except (C.FrameworkError, TypeError, ValueError, OSError) as exc:
        errors.append(f"input manifest verification failed: {exc}")

    # 3. Enforced generation identity (G01).
    identity_ok, identity_errors = verify_generation_identity(
        manifest, candidate_generation_id=candidate_generation_id,
        manifest_sha256=expected_manifest_sha256, require_all=True)
    checks["generation_identity"] = {
        "candidate_generation_id": candidate_generation_id,
        "manifest_candidate_generation_id": manifest.get("candidate_generation_id"),
        "manifest_sha256": expected_manifest_sha256,
        "ok": identity_ok,
    }
    errors.extend(identity_errors)

    # 4. Absolute production path rejection (G08).
    checks["absolute_run_path_hits"] = _check_absolute_run_paths(manifest, errors)

    # 5. Artifact inventory <-> filesystem equality (G02/G10).
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        errors.append("manifest must declare an artifacts list")
        artifacts = []
    artifacts_by_id: dict[str, Mapping[str, Any]] = {}
    seen_ids: set[str] = set()
    counts: dict[str, int] = {}
    ok_files = missing_files = hash_mismatches = 0
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, Mapping):
            errors.append(f"artifact[{index}] must be a mapping")
            continue
        artifact_id = artifact.get("artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id:
            errors.append(f"artifact[{index}] has no artifact_id")
            continue
        if artifact_id in seen_ids:
            errors.append(f"duplicate artifact_id: {artifact_id}")
        seen_ids.add(artifact_id)
        artifacts_by_id[artifact_id] = artifact
        status = artifact.get("status")
        status_key = str(status) if isinstance(status, str) else "INVALID"
        counts[status_key] = counts.get(status_key, 0) + 1
        relative = artifact.get("relative_path")
        if (relative is None or relative == "") and status_key in (
                "UNAVAILABLE", "INCOMPLETE", "PROVISIONAL"):
            counts["no_path_non_ready"] = counts.get("no_path_non_ready", 0) + 1
            continue
        try:
            candidate = _artifact_path_safety(root_path, artifact_id, relative)
        except C.ContractViolation as exc:
            errors.append(str(exc))
            continue
        declared = artifact.get("sha256")
        if not isinstance(declared, str) or not SHA256_RE.fullmatch(declared):
            errors.append(f"{artifact_id}: declared sha256 is malformed")
            continue
        actual = sha256_file(candidate)
        if actual != declared:
            hash_mismatches += 1
            errors.append(
                f"{artifact_id}: checksum mismatch ({declared} -> {actual})")
        else:
            ok_files += 1
    checks["inventory"] = {
        "artifacts_declared": len(artifacts),
        "files_verified": ok_files,
        "files_missing_or_unsafe": len(artifacts) - ok_files - hash_mismatches,
        "checksum_mismatches": hash_mismatches,
        "status_counts": counts,
    }
    declared_count = manifest.get("inventory_count")
    if declared_count is None:
        declared_count = manifest.get("artifact_count")
    if declared_count is not None:
        checks["inventory_count"] = {"declared": declared_count,
                                     "computed": len(artifacts)}
        if declared_count != len(artifacts):
            errors.append(
                f"inventory count mismatch: manifest declares "
                f"{declared_count}, canonical package contains {len(artifacts)}")
    else:
        warnings.append(
            "manifest does not declare a frozen inventory count; "
            "count equality cannot be enforced (G02)")
    # 6. Receipts and in-manifest hash links (G11/G06/G07).
    receipt_checks: dict[str, Any] = {}
    for artifact_id, artifact in sorted(artifacts_by_id.items()):
        receipt_checks[artifact_id] = _verify_receipt(
            artifact, artifact_id, errors, root=root_path)
        _verify_declared_hash_links(
            artifact, artifact_id, artifacts_by_id, root_path, errors)
    checks["receipts"] = receipt_checks

    # 7. Sentinel-2 selected/asset/grid chain (G05/G06).
    s2_checks: dict[str, Any] = {}
    for artifact_id in sorted(artifacts_by_id):
        lowered = artifact_id.lower()
        if "selected" in lowered and "sentinel2" in lowered:
            s2_checks[artifact_id] = _verify_s2_selected_manifest(
                artifacts_by_id[artifact_id], artifact_id,
                artifacts_by_id, root_path, errors, warnings)
    checks["sentinel2_chain"] = s2_checks if s2_checks else None

    # 8. Waiver and audit-packet triple consistency (G04/G03).
    checks["waiver_consistency"] = _verify_waiver_consistency(
        waiver_path, artifacts_by_id, errors, warnings)
    checks["audit_packet_identity"] = _verify_audit_packet_identity(
        audit_packet_path, candidate_generation_id,
        expected_manifest_sha256, errors, warnings)

    # 9. Environment fingerprint (G30).
    fingerprint = environment_fingerprint(requirements_path)
    checks["environment_fingerprint"] = fingerprint
    checks["environment_fingerprint_sha256"] = (
        environment_fingerprint_sha256(requirements_path))

    checks["verifier"] = "framework_v1.lineage"
    return LineageVerification(
        not errors, sorted(set(errors)), sorted(set(warnings)), checks)
# ---------------------------------------------------------------------------
# Generation-aware current report discovery (G27)
# ---------------------------------------------------------------------------

def discover_current_run_reports(runs_root: str | Path, *,
                                 candidate_generation_id: str,
                                 manifest_sha256: Optional[str] = None,
                                 ) -> dict[str, Any]:
    """Return only reports bound to the current generation.

    Stale (different generation), null, malformed, or generation-mismatched
    reports are rejected before any pipeline continuation.
    """
    root = Path(runs_root)
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    if not root.is_dir():
        return {
            "ok": False,
            "errors": [f"runs root is not a directory: {root}"],
            "accepted": accepted,
            "rejected": rejected,
        }
    for report_path in sorted(root.rglob("pipeline_report.json")):
        entry: dict[str, Any] = {"path": str(report_path), "run_dir": str(
            report_path.parent)}
        try:
            payload = _json_load(report_path)
        except (OSError, ValueError) as exc:
            entry["reason"] = f"malformed: {exc}"
            rejected.append(entry)
            continue
        if not isinstance(payload, Mapping):
            entry["reason"] = "malformed: not a mapping"
            rejected.append(entry)
            continue
        payload_generation = payload.get("candidate_generation_id")
        payload_manifest = payload.get("manifest_sha256")
        if payload_generation is None and payload_manifest is None:
            entry["reason"] = "generation identity missing (null-shaped)"
            rejected.append(entry)
            continue
        if payload_generation != candidate_generation_id:
            entry["reason"] = (
                f"stale generation {payload_generation!r} != "
                f"{candidate_generation_id!r}")
            rejected.append(entry)
            continue
        if manifest_sha256 is not None and payload_manifest is not None \
                and payload_manifest != manifest_sha256:
            entry["reason"] = (
                f"manifest mismatch {payload_manifest!r} != {manifest_sha256!r}")
            rejected.append(entry)
            continue
        entry["report"] = payload
        accepted.append(entry)
    return {
        "ok": len(accepted) >= 1,
        "candidate_generation_id": candidate_generation_id,
        "manifest_sha256": manifest_sha256,
        "accepted": [{"path": a["path"], "run_dir": a["run_dir"]}
                     for a in accepted],
        "rejected": rejected,
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
    }
