"""Artifact manifest builder for GLM2 framework_inputs_v1.

Every artifact requires 15 metadata fields per the GLM2 data ownership contract.
This module provides the registration, checksum, and manifest hashing utilities.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


FRAMEWORK_DIR = Path(__file__).resolve().parent.parent
MANIFEST_FILE = FRAMEWORK_DIR / "manifest.json"


def sha256_file(path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def register_artifact(
    artifact_id: str,
    source_url: str,
    query_or_request: str,
    source_record_id: str,
    acquired_at: str,
    observation_start: str,
    observation_end: str,
    publication_or_validity_date: str,
    file_path: Path | None = None,
    data_bytes: bytes | None = None,
    crs: str = "",
    units: str = "",
    license: str = "",
    processing: str = "",
    status: str = "READY",
) -> dict:
    """Register an artifact with full 15-field metadata."""
    if file_path and file_path.exists():
        sha = sha256_file(file_path)
        size = file_path.stat().st_size
    elif data_bytes is not None:
        sha = sha256_bytes(data_bytes)
        size = len(data_bytes)
    else:
        sha = ""
        size = 0

    return {
        "artifact_id": artifact_id,
        "source_url": source_url,
        "query_or_request": query_or_request,
        "source_record_id": source_record_id,
        "acquired_at": acquired_at,
        "observation_start": observation_start,
        "observation_end": observation_end,
        "publication_or_validity_date": publication_or_validity_date,
        "sha256": sha,
        "bytes": size,
        "crs": crs,
        "units": units,
        "license": license,
        "processing": processing,
        "status": status,
    }


def save_manifest(artifacts: list[dict]) -> str:
    """Save manifest with self-hash."""
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
    }
    manifest_text = json.dumps(manifest, indent=2, sort_keys=True)
    manifest_hash = hashlib.sha256(manifest_text.encode()).hexdigest()
    manifest["manifest_sha256"] = manifest_hash

    FRAMEWORK_DIR.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST_FILE, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)

    return manifest_hash


def check_size_limits() -> dict:
    """Check persistent and temp size limits."""
    persistent = 0
    temp = 0
    for f in FRAMEWORK_DIR.rglob("*"):
        if f.is_file():
            persistent += f.stat().st_size
    tmp_dir = FRAMEWORK_DIR.parent / "framework_inputs_v1_tmp"
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
