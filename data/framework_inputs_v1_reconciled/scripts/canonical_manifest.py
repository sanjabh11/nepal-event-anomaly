"""Canonical manifest builder for reconciled framework inputs.

Every artifact requires the full 15+ field metadata per the reconciled contract.
This module provides registration, checksum, contract hashing, and manifest verification.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "2.0.0-reconciled"
ALLOWED_STATUSES = {"READY", "INCOMPLETE", "INVALID", "UNAVAILABLE", "PROVISIONAL"}
ALLOWED_ROLES = {"A", "B", "C", "D", "E", "F", "context", "sidecar"}
ALLOWED_KINDS = {"raster", "vector", "tabular", "timeseries", "metadata", "composite"}

RECONCILED_DIR = Path(__file__).resolve().parent.parent
MANIFEST_FILE = RECONCILED_DIR / "manifest.json"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compute_contract_sha256(contract_path: Path) -> str:
    """Hash the feature contract file for manifest binding."""
    if not contract_path.exists():
        return ""
    return sha256_file(contract_path)


def register_artifact(
    artifact_id: str,
    role: str,
    kind: str,
    source_url: str,
    query_or_request: str,
    source_record_id: str,
    acquired_at: str,
    observation_start: str,
    observation_end: str,
    publication_or_validity_date: str,
    relative_path: str = "",
    file_path: Path | None = None,
    data_bytes: bytes | None = None,
    crs: str = "",
    units: str = "",
    license: str = "",
    processing: str = "",
    status: str = "READY",
    language_access_status: str = "en/accessible",
) -> dict[str, Any]:
    """Register an artifact with full canonical metadata."""
    if role not in ALLOWED_ROLES:
        raise ValueError(f"Invalid role '{role}'. Must be one of {ALLOWED_ROLES}")
    if kind not in ALLOWED_KINDS:
        raise ValueError(f"Invalid kind '{kind}'. Must be one of {ALLOWED_KINDS}")
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"Invalid status '{status}'. Must be one of {ALLOWED_STATUSES}")

    sha = ""
    size = 0
    if file_path and file_path.exists():
        sha = sha256_file(file_path)
        size = file_path.stat().st_size
    elif data_bytes is not None:
        sha = sha256_bytes(data_bytes)
        size = len(data_bytes)

    return {
        "artifact_id": artifact_id,
        "role": role,
        "kind": kind,
        "relative_path": relative_path,
        "status": status,
        "sha256": sha,
        "bytes": size,
        "crs": crs,
        "units": units,
        "license": license,
        "source_url": source_url,
        "query_or_request": query_or_request,
        "source_record_id": source_record_id,
        "acquired_at": acquired_at,
        "observation_start": observation_start,
        "observation_end": observation_end,
        "publication_or_validity_date": publication_or_validity_date,
        "processing": processing,
        "language_access_status": language_access_status,
    }


def save_manifest(
    artifacts: list[dict],
    contract_sha256: str,
    extra_fields: dict | None = None,
) -> str:
    """Save canonical manifest with self-hash. Artifacts sorted deterministically."""
    sorted_artifacts = sorted(artifacts, key=lambda a: a["artifact_id"])

    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "contract_sha256": contract_sha256,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifact_count": len(sorted_artifacts),
        "artifacts": sorted_artifacts,
    }
    if extra_fields:
        manifest.update(extra_fields)

    # Compute self-hash (hash of everything except the self-hash field itself)
    manifest_text = json.dumps(manifest, indent=2, sort_keys=True)
    manifest_hash = hashlib.sha256(manifest_text.encode()).hexdigest()
    manifest["manifest_sha256"] = manifest_hash

    RECONCILED_DIR.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST_FILE, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)

    return manifest_hash


def verify_manifest() -> dict[str, Any]:
    """Verify manifest integrity and return detailed results."""
    result: dict[str, Any] = {"valid": False, "errors": [], "warnings": [], "checks": {}}

    if not MANIFEST_FILE.exists():
        result["errors"].append("Manifest file not found")
        return result

    with open(MANIFEST_FILE) as f:
        manifest = json.load(f)

    # Check schema version
    sv = manifest.get("schema_version", "")
    result["checks"]["schema_version"] = sv
    if sv != SCHEMA_VERSION:
        result["errors"].append(f"Schema version mismatch: {sv} != {SCHEMA_VERSION}")

    # Verify self-hash
    stored_hash = manifest.pop("manifest_sha256", None)
    if not stored_hash:
        result["errors"].append("No manifest_sha256 field found")
    else:
        recomputed = hashlib.sha256(
            json.dumps(manifest, indent=2, sort_keys=True).encode()
        ).hexdigest()
        result["checks"]["self_hash_valid"] = stored_hash == recomputed
        if stored_hash != recomputed:
            result["errors"].append("Manifest self-hash verification failed")

    # Verify artifacts
    artifacts = manifest.get("artifacts", [])
    result["checks"]["artifact_count"] = len(artifacts)

    required_fields = {
        "artifact_id", "role", "kind", "relative_path", "status",
        "sha256", "bytes", "crs", "units", "license",
        "source_url", "query_or_request", "source_record_id",
        "acquired_at", "observation_start", "observation_end",
        "publication_or_validity_date", "processing", "language_access_status",
    }

    for a in artifacts:
        missing = required_fields - set(a.keys())
        if missing:
            result["errors"].append(
                f"Artifact {a.get('artifact_id', '?')}: missing fields {missing}"
            )
        if a.get("status") not in ALLOWED_STATUSES:
            result["errors"].append(
                f"Artifact {a.get('artifact_id', '?')}: invalid status '{a.get('status')}'"
            )
        if a.get("role") not in ALLOWED_ROLES:
            result["errors"].append(
                f"Artifact {a.get('artifact_id', '?')}: invalid role '{a.get('role')}'"
            )
        if a.get("kind") not in ALLOWED_KINDS:
            result["errors"].append(
                f"Artifact {a.get('artifact_id', '?')}: invalid kind '{a.get('kind')}'"
            )
        # READY artifacts must have checksums
        if a.get("status") == "READY" and not a.get("sha256"):
            result["errors"].append(
                f"Artifact {a['artifact_id']}: READY but no SHA-256 checksum"
            )

    # Check deterministic sort
    ids = [a["artifact_id"] for a in artifacts]
    if ids != sorted(ids):
        result["warnings"].append("Artifacts are not sorted by artifact_id")

    result["valid"] = len(result["errors"]) == 0
    return result


def check_size_limits() -> dict[str, Any]:
    """Check persistent and temp size limits."""
    persistent = 0
    temp = 0
    for f in RECONCILED_DIR.rglob("*"):
        if f.is_file():
            persistent += f.stat().st_size
    tmp_dir = RECONCILED_DIR.parent / "framework_inputs_v1_reconciled_tmp"
    if tmp_dir.exists():
        for f in tmp_dir.rglob("*"):
            if f.is_file():
                temp += f.stat().st_size
    return {
        "persistent_mb": round(persistent / (1024 * 1024), 1),
        "temp_mb": round(temp / (1024 * 1024), 1),
        "persistent_limit_mb": 5120,
        "temp_limit_mb": 8192,
        "persistent_ok": persistent < 5 * 1024 * 1024 * 1024,
        "temp_ok": temp < 8 * 1024 * 1024 * 1024,
    }


def scan_raw_slc(root: Path | None = None) -> list[str]:
    """Scan for raw SLC files. Returns list of found paths."""
    if root is None:
        root = RECONCILED_DIR
    found = []
    for f in root.rglob("*"):
        if f.is_file():
            name_upper = f.name.upper()
            if "SLC" in name_upper or name_upper.endswith(".SAFE"):
                found.append(str(f))
    return found
