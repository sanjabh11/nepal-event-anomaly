"""Narrow, fail-closed integrity proof of concept.

This module deliberately proves a smaller property than the full framework
pipeline: a canonical manifest and a small set of registered files can be
verified with explicit contract bindings, while tampering and resource
failures are reported without promoting a Phase B gate.

The PoC does not provide authenticity, signatures, key management, a CAS, or
scientific validation.  A run-generated anchor is useful for demonstrating the
mechanism only; it is not an authority artifact.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from . import contract as C
from . import input_manifest as input_manifest_module
from .input_manifest import (
    canonical_input_manifest_hash,
    pretty_input_manifest_hash,
    verify_input_manifest,
)
from .provenance import (
    bind_artifact_envelope,
    sha256_file,
    verify_artifact_envelope,
    write_deterministic_json,
)


PROFILE_ID = "INTEGRITY_POC_V1"
GATE_ID = PROFILE_ID
STATUS_PASS = "INTEGRITY_POC_PASS"
STATUS_BLOCKED = "INTEGRITY_POC_BLOCKED"
STATUS_INCOMPLETE = "INTEGRITY_POC_INCOMPLETE"
STATUS_FAILED = "INTEGRITY_POC_FAILED"

EXIT_PASS = 0
EXIT_BLOCKED = 2
EXIT_CORRECTNESS_FAILED = 3
EXIT_INCOMPLETE = 4
EXIT_UNEXPECTED = 5

DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_WARNING_SECONDS = 30.0
DEFAULT_CHUNK_BYTES = 1 << 20
DEFAULT_FIXTURE_BYTES = 64 << 10
MAX_FIXTURE_BYTES = 1 << 20
DEFAULT_MAX_PEAK_RSS_MIB = 512.0
DEFAULT_MINIMUM_FREE_GIB = 4.0
WARNING_FREE_GIB = 5.0
MAX_RUN_BYTES = 16 << 20
MAX_REPORT_BYTES = 1 << 20
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class IntegrityPocBlocked(ValueError):
    """A required PoC input or trust binding is not acceptable."""


class IntegrityPocIncomplete(RuntimeError):
    """The bounded PoC could not complete within its resource policy."""


class IntegrityPocCorrectness(RuntimeError):
    """The PoC implementation or tamper oracle failed its contract."""


@dataclass(frozen=True)
class IntegrityPocConfig:
    """Inputs and resource policy for one narrow integrity run."""

    repo_root: Path
    source_manifest: Optional[Path]
    real_root: Path
    real_artifact_id: str
    output_path: Path
    expected_data_contract_sha256: str
    expected_framework_contract_sha256: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    warning_seconds: float = DEFAULT_WARNING_SECONDS
    chunk_bytes: int = DEFAULT_CHUNK_BYTES
    fixture_bytes: int = DEFAULT_FIXTURE_BYTES
    max_peak_rss_mib: float = DEFAULT_MAX_PEAK_RSS_MIB
    minimum_free_gib: float = DEFAULT_MINIMUM_FREE_GIB
    scope_manifest: Optional[Path] = None
    scope_root: Optional[Path] = None
    trusted_manifest_sha256: Optional[str] = None
    generate_demo_anchor: bool = False
    run_b_loader_boundary: bool = False


def _valid_sha(value: Any) -> bool:
    return isinstance(value, str) and bool(SHA256_RE.fullmatch(value))


def _as_path(value: str | Path) -> Path:
    return Path(value).expanduser()


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=True))
    except (FileNotFoundError, OSError, ValueError):
        return False
    return True


def _safe_relative_path(value: Any) -> Optional[Path]:
    if not isinstance(value, str) or not value or "\x00" in value:
        return None
    path = Path(value)
    if path.is_absolute() or "\\" in value or ".." in path.parts:
        return None
    if path == Path("."):
        return None
    return path


def _regular_file(root: Path, relative: Any) -> tuple[Optional[Path], list[str]]:
    """Resolve one file without permitting symlinks or root escape."""
    problems: list[str] = []
    rel = _safe_relative_path(relative)
    if rel is None:
        return None, [f"unsafe relative_path: {relative!r}"]
    if not root.exists() or not root.is_dir():
        return None, [f"artifact root is not an existing directory: {root}"]
    resolved_root = root.resolve(strict=True)
    current = resolved_root
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            return None, [f"symlink path component is forbidden: {rel.as_posix()}"]
    candidate = root / rel
    try:
        candidate.resolve(strict=False).relative_to(resolved_root)
    except (FileNotFoundError, OSError, ValueError):
        return None, [f"artifact path escapes root: {rel.as_posix()}"]
    if not candidate.exists():
        problems.append(f"artifact file is missing: {rel.as_posix()}")
    elif not candidate.is_file():
        problems.append(f"artifact path is not a file: {rel.as_posix()}")
    return (candidate if not problems else None), problems


def _rss_bytes(value: int) -> int:
    # ru_maxrss is bytes on macOS and KiB on Linux and most BSDs.
    return int(value if sys.platform == "darwin" else value * 1024)


def _worker_hash(path: Path, chunk_bytes: int) -> dict[str, Any]:
    """Hash one file in a child process and emit only JSON-safe metrics."""
    started = time.perf_counter()
    digest = hashlib.sha256()
    bytes_read = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_bytes), b""):
            bytes_read += len(chunk)
            digest.update(chunk)
    elapsed = time.perf_counter() - started
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "algorithm": "sha256",
        "chunk_bytes": chunk_bytes,
        "digest": digest.hexdigest(),
        "bytes_read": bytes_read,
        "elapsed_seconds": elapsed,
        "peak_rss_bytes": _rss_bytes(usage.ru_maxrss),
        "rss_source": "resource.RUSAGE_SELF.ru_maxrss",
        "output_bytes": 0,
        "temporary_bytes_written": 0,
        "hash_calls": 1,
        "unique_paths_hashed": 1,
        "cache_state": "not_controlled",
    }


def _run_hash_worker(path: Path, chunk_bytes: int,
                     timeout_seconds: float) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    if timeout_seconds <= 0:
        return None, "timeout_seconds must be positive"
    module_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = (
        str(module_root) if not existing_pythonpath else
        str(module_root) + os.pathsep + existing_pythonpath
    )
    command = [
        sys.executable, "-B", "-m", "nepal.framework_v1.integrity_poc",
        "--hash-worker", str(path), "--chunk-bytes", str(chunk_bytes),
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, check=False,
            timeout=timeout_seconds, cwd=str(module_root), env=environment,
        )
    except subprocess.TimeoutExpired:
        return None, "hash worker timed out"
    except OSError as exc:
        return None, f"hash worker could not start: {exc}"
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        return None, f"hash worker failed ({completed.returncode}): {detail}"
    try:
        payload = json.loads(completed.stdout)
    except (TypeError, ValueError) as exc:
        return None, f"hash worker emitted invalid JSON: {exc}"
    if not isinstance(payload, dict):
        return None, "hash worker emitted a non-object result"
    return payload, None


def _empty_benchmark(path: Path, *, chunk_bytes: int,
                     max_peak_rss_mib: float) -> dict[str, Any]:
    """Return the complete metric shape for a blocked/incomplete hash run."""
    return {
        "algorithm": "sha256",
        "chunk_bytes": chunk_bytes,
        "digest": None,
        "bytes_read": 0,
        "elapsed_seconds": 0.0,
        "peak_rss_bytes": None,
        "rss_source": "resource.RUSAGE_SELF.ru_maxrss",
        "output_bytes": 0,
        "temporary_bytes_written": 0,
        "hash_calls": 0,
        "unique_paths_hashed": 0,
        "cache_state": "not_controlled",
        "path": str(path),
        "expected_bytes": None,
        "max_peak_rss_bytes": int(max_peak_rss_mib * 1024 * 1024),
        "warning_exceeded": False,
        "timeout": False,
        "status": STATUS_INCOMPLETE,
        "errors": [],
    }


def benchmark_sha256(
    path: Path,
    *,
    timeout_seconds: float,
    warning_seconds: float,
    chunk_bytes: int,
    max_peak_rss_mib: float,
) -> dict[str, Any]:
    """Measure one streaming SHA-256 pass with bounded child execution."""
    path = _as_path(path)
    empty = _empty_benchmark(
        path, chunk_bytes=chunk_bytes, max_peak_rss_mib=max_peak_rss_mib)
    if chunk_bytes <= 0:
        empty.update({
            "status": STATUS_FAILED,
            "errors": ["chunk_bytes must be positive"],
        })
        return empty
    if max_peak_rss_mib <= 0:
        empty.update({
            "status": STATUS_FAILED,
            "errors": ["max_peak_rss_mib must be positive"],
        })
        return empty
    if path.is_symlink() or not path.is_file():
        empty.update({
            "status": STATUS_BLOCKED,
            "errors": ["benchmark path must be a regular file and not a symlink"],
        })
        return empty
    expected_bytes = path.stat().st_size
    empty["expected_bytes"] = expected_bytes
    if timeout_seconds <= 0:
        empty.update({
            "status": STATUS_INCOMPLETE,
            "timeout": True,
            "errors": ["hash benchmark timeout was non-positive"],
        })
        return empty
    metrics, error = _run_hash_worker(path, chunk_bytes, timeout_seconds)
    if error is not None or metrics is None:
        status = STATUS_INCOMPLETE if "timed out" in (error or "") else STATUS_FAILED
        empty.update({
            "status": status,
            "timeout": status == STATUS_INCOMPLETE,
            "errors": [error or "hash worker returned no metrics"],
        })
        return empty
    errors: list[str] = []
    bytes_read = metrics.get("bytes_read")
    if bytes_read != expected_bytes:
        errors.append(f"bytes_read {bytes_read!r} does not equal file size {expected_bytes}")
    peak_rss = metrics.get("peak_rss_bytes")
    max_rss = int(max_peak_rss_mib * 1024 * 1024)
    if not isinstance(peak_rss, int) or peak_rss < 0:
        errors.append("peak RSS metric is missing or invalid")
    elif peak_rss > max_rss:
        errors.append(
            f"peak RSS {peak_rss} exceeds configured limit {max_rss} bytes")
    elapsed = metrics.get("elapsed_seconds")
    if not isinstance(elapsed, (int, float)) or not math.isfinite(float(elapsed)):
        errors.append("elapsed_seconds metric is missing or invalid")
        elapsed = 0.0
    metrics.update({
        "status": STATUS_INCOMPLETE if errors else "PASS",
        "path": str(path),
        "expected_bytes": expected_bytes,
        "warning_exceeded": float(elapsed) > warning_seconds,
        "timeout": False,
        "max_peak_rss_bytes": max_rss,
        "errors": errors,
    })
    return metrics


def _scope_checks(manifest: Mapping[str, Any],
                  trusted_manifest_sha256: str,
                  expected_data_contract_sha256: str,
                  expected_framework_contract_sha256: str,
                  required_artifact_id: str) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    checks: dict[str, Any] = {}
    if not isinstance(manifest, Mapping):
        return ["scope manifest must be a mapping"], checks
    for name, value in (
        ("trusted_manifest_sha256", trusted_manifest_sha256),
        ("expected_data_contract_sha256", expected_data_contract_sha256),
        ("expected_framework_contract_sha256", expected_framework_contract_sha256),
    ):
        if not _valid_sha(value):
            errors.append(f"{name} must be an explicit lowercase SHA-256")

    checks["profile_id"] = manifest.get("profile_id") == PROFILE_ID
    if not checks["profile_id"]:
        errors.append(f"manifest profile_id must be {PROFILE_ID!r}")
    checks["manifest_hash_encoding"] = manifest.get("manifest_hash_encoding")
    if checks["manifest_hash_encoding"] != "canonical_json":
        errors.append("manifest_hash_encoding must be 'canonical_json'")
    checks["canonical_hash_domain"] = manifest.get("canonical_hash_domain")
    if checks["canonical_hash_domain"] != "canonical_json_without_manifest_sha256":
        errors.append(
            "canonical_hash_domain must be 'canonical_json_without_manifest_sha256'")
    if manifest.get("production_manifest") is not False:
        errors.append("integrity scope must not be marked as a production manifest")
    if manifest.get("promotion_eligible") is not False:
        errors.append("integrity scope must not be promotion eligible")

    stored = manifest.get("manifest_sha256")
    try:
        canonical = canonical_input_manifest_hash(manifest)
    except (TypeError, ValueError) as exc:
        canonical = None
        errors.append(f"manifest is not canonical JSON: {exc}")
    checks["stored_manifest_sha256"] = stored
    checks["canonical_manifest_sha256"] = canonical
    checks["canonical_manifest_match"] = bool(
        _valid_sha(stored) and canonical is not None and stored == canonical)
    if not checks["canonical_manifest_match"]:
        errors.append("manifest self-hash does not match canonical JSON")
    checks["trusted_anchor_match"] = bool(
        _valid_sha(trusted_manifest_sha256) and stored == trusted_manifest_sha256)
    if not checks["trusted_anchor_match"]:
        errors.append("manifest does not match the trusted external anchor")

    checks["data_contract_match"] = (
        manifest.get("contract_sha256") == expected_data_contract_sha256)
    if not checks["data_contract_match"]:
        errors.append("manifest data contract does not match the expected hash")
    checks["framework_contract_match"] = (
        manifest.get("framework_contract_sha256") == expected_framework_contract_sha256)
    if not checks["framework_contract_match"]:
        errors.append("manifest framework contract does not match the expected hash")
    checks["required_artifact_ids"] = manifest.get("required_artifact_ids")
    if manifest.get("required_artifact_ids") != [required_artifact_id]:
        errors.append("scope required_artifact_ids must contain exactly the selected artifact")
    artifacts = manifest.get("artifacts")
    if isinstance(artifacts, list):
        artifact_ids = [
            str(artifact.get("artifact_id")) for artifact in artifacts
            if isinstance(artifact, Mapping)
        ]
        relative_paths = [
            str(artifact.get("relative_path")) for artifact in artifacts
            if isinstance(artifact, Mapping)
        ]
        if len(artifact_ids) != len(set(artifact_ids)):
            errors.append("scope contains duplicate artifact_id values")
        if len(relative_paths) != len(set(relative_paths)):
            errors.append("scope contains duplicate relative_path values")
    return errors, checks


def verify_integrity_scope(
    manifest: Mapping[str, Any],
    root: Path,
    *,
    trusted_manifest_sha256: str,
    expected_data_contract_sha256: str,
    expected_framework_contract_sha256: str,
    required_artifact_id: str,
    verified_file_hashes: Optional[Mapping[Any, str]] = None,
) -> dict[str, Any]:
    """Verify one canonical scope through the real adapter.

    ``verified_file_hashes`` is an internal PoC optimization: when supplied,
    the streaming benchmark has already read the exact resolved file and the
    adapter reuses that digest instead of performing a second physical read.
    The default remains an ordinary adapter verification for callers outside
    the end-to-end PoC.
    """
    started = time.perf_counter()
    errors, checks = _scope_checks(
        manifest, trusted_manifest_sha256, expected_data_contract_sha256,
        expected_framework_contract_sha256, required_artifact_id,
    )
    if not isinstance(manifest, Mapping):
        errors.append("scope manifest must be a mapping")
        return {
            "status": STATUS_BLOCKED,
            "ok": False,
            "errors": errors,
            "checks": checks,
            "elapsed_seconds": time.perf_counter() - started,
        }

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        errors.append("scope artifacts must be a list")
        artifacts = []
    selected = [
        artifact for artifact in artifacts
        if isinstance(artifact, Mapping) and
        artifact.get("artifact_id") == required_artifact_id
    ]
    if len(selected) != 1:
        errors.append("scope must contain exactly one selected artifact record")

    verification = None
    hash_cache_hits = 0
    normalized_hashes: dict[Path, str] = {}
    if not errors:
        if verified_file_hashes:
            normalized_hashes = {
                Path(path).resolve(strict=False): digest
                for path, digest in verified_file_hashes.items()
            }
            original_sha256_file = input_manifest_module.sha256_file

            def cached_sha256_file(path: str | Path) -> str:
                nonlocal hash_cache_hits
                normalized = Path(path).resolve(strict=False)
                digest = normalized_hashes.get(normalized)
                if digest is not None:
                    hash_cache_hits += 1
                    return digest
                return original_sha256_file(path)

            input_manifest_module.sha256_file = cached_sha256_file
            try:
                verification = verify_input_manifest(
                    manifest, root,
                    expected_contract_sha256=expected_data_contract_sha256,
                    expected_framework_contract_sha256=expected_framework_contract_sha256,
                    required_artifact_ids=[required_artifact_id],
                    required_role="B",
                    scan_root_for_raw_slc=True,
                )
            finally:
                input_manifest_module.sha256_file = original_sha256_file
        else:
            verification = verify_input_manifest(
                manifest, root,
                expected_contract_sha256=expected_data_contract_sha256,
                expected_framework_contract_sha256=expected_framework_contract_sha256,
                required_artifact_ids=[required_artifact_id],
                required_role="B",
                scan_root_for_raw_slc=True,
            )
        errors.extend(verification.errors)
        checks["adapter_ok"] = verification.ok
        checks["adapter_can_run_primary"] = verification.can_run_primary
        checks["adapter_checks"] = verification.checks
        checks["hash_cache_paths"] = [str(path) for path in normalized_hashes] \
            if verified_file_hashes else []
        checks["hash_cache_hits"] = hash_cache_hits
        if not verification.ok or not verification.can_run_primary:
            errors.append("real manifest adapter did not authorize the integrity scope")
    else:
        checks["adapter_ok"] = False
        checks["adapter_can_run_primary"] = False

    unique_errors = list(dict.fromkeys(errors))
    return {
        "status": "PASS" if not unique_errors else STATUS_BLOCKED,
        "ok": not unique_errors,
        "root": str(root),
        "required_artifact_id": required_artifact_id,
        "errors": unique_errors,
        "checks": checks,
        "manifest_sha256": manifest.get("manifest_sha256"),
        "verification": verification.to_dict() if verification is not None else None,
        "elapsed_seconds": time.perf_counter() - started,
    }


def _artifact_template(artifact_id: str, relative_path: str, digest: str,
                       byte_count: int, *, source_url: str,
                       source_record_id: str) -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "role": "B",
        "kind": "metadata",
        "relative_path": relative_path,
        "status": "READY",
        "sha256": digest,
        "bytes": byte_count,
        "crs": "",
        "units": "bytes",
        "license": "PoC fixture",
        "source_url": source_url,
        "query_or_request": "INTEGRITY_POC_V1 deterministic fixture",
        "source_record_id": source_record_id,
        "acquired_at": "2026-09-12",
        "observation_start": "",
        "observation_end": "",
        "publication_or_validity_date": "",
        "processing": "integrity PoC fixture; not a Phase B scientific artifact",
        "language_access_status": "en/accessible",
    }


def _build_scope_manifest(artifact: Mapping[str, Any], data_hash: str,
                          framework_hash: str) -> dict[str, Any]:
    selected = copy.deepcopy(dict(artifact))
    scope: dict[str, Any] = {
        "schema_version": "integrity-poc-v1",
        "profile_id": PROFILE_ID,
        "production_manifest": False,
        "promotion_eligible": False,
        "security_authority": "NOT_PROVEN",
        "manifest_hash_encoding": "canonical_json",
        "canonical_hash_domain": "canonical_json_without_manifest_sha256",
        "contract_sha256": data_hash,
        "framework_contract_sha256": framework_hash,
        "required_artifact_ids": [str(selected.get("artifact_id"))],
        "artifact_count": 1,
        "artifacts": [selected],
    }
    scope["manifest_sha256"] = canonical_input_manifest_hash(scope)
    return scope


def _load_json(path: Path) -> Mapping[str, Any]:
    if path.is_symlink():
        raise IntegrityPocBlocked(f"JSON input must not be a symlink: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        raise IntegrityPocBlocked(f"JSON input could not be loaded: {path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise IntegrityPocBlocked(f"JSON input must be an object: {path}")
    return payload


def _source_manifest_diagnostic(path: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    stored = manifest.get("manifest_sha256")
    canonical = None
    pretty = None
    try:
        canonical = canonical_input_manifest_hash(manifest)
        pretty = pretty_input_manifest_hash(manifest)
    except (TypeError, ValueError):
        pass
    if stored == canonical:
        state = "CANONICAL"
    elif stored == pretty:
        state = "COMPATIBILITY_ONLY"
    else:
        state = "UNVERIFIED"
    return {
        "path": str(path),
        "file_sha256": sha256_file(path),
        "stored_manifest_sha256": stored,
        "canonical_manifest_sha256": canonical,
        "pretty_manifest_sha256": pretty,
        "state": state,
        "used_as_production_authorization": False,
    }


def _find_artifact(manifest: Mapping[str, Any], artifact_id: str) -> Mapping[str, Any]:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise IntegrityPocBlocked("source manifest artifacts must be a list")
    matches = [
        artifact for artifact in artifacts
        if isinstance(artifact, Mapping) and artifact.get("artifact_id") == artifact_id
    ]
    if len(matches) != 1:
        raise IntegrityPocBlocked(
            f"source manifest must contain exactly one artifact {artifact_id!r}")
    artifact = matches[0]
    if artifact.get("status") != "READY":
        raise IntegrityPocBlocked(f"real artifact {artifact_id} is not READY")
    return artifact


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _write_checkpoint(path: Path, payload: Mapping[str, Any]) -> None:
    write_deterministic_json(path, dict(payload))


def _append_progress(path: Path, payload: Mapping[str, Any]) -> None:
    previous = path.read_text(encoding="utf-8") if path.exists() else ""
    line = json.dumps(dict(payload), sort_keys=True, separators=(",", ":")) + "\n"
    _atomic_bytes(path, (previous + line).encode("utf-8"))


def _run_bytes(root: Path) -> int:
    total = 0
    if not root.exists():
        return 0
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            try:
                total += path.stat().st_size
            except OSError:
                pass
    return total


def _fixture_bytes(size: int) -> bytes:
    pattern = b"INTEGRITY_POC_V1 deterministic fixture\n"
    repeats = (size + len(pattern) - 1) // len(pattern)
    return (pattern * repeats)[:size]


def _tamper_result(expected: str, observed_verified: bool,
                   errors: list[str], *, classification: str = "DETECTED") -> dict[str, Any]:
    if expected == "pass":
        passed = observed_verified
    elif expected == "reject":
        passed = not observed_verified
    else:
        passed = classification == "ANCHOR_COMPROMISED"
    return {
        "expected": expected,
        "observed_verified": observed_verified,
        "passed": passed,
        "classification": classification,
        "errors": errors,
    }


def _run_tamper_benchmark(
    run_root: Path,
    fixture_manifest: Mapping[str, Any],
    fixture_root: Path,
    trusted_anchor: str,
    config: IntegrityPocConfig,
) -> dict[str, Any]:
    base_file = fixture_root / "fixture" / "poc_fixture.bin"
    started = time.perf_counter()
    base_data = base_file.read_bytes()
    results: dict[str, dict[str, Any]] = {}

    def execute(case_id: str, expected: str, manifest: Mapping[str, Any],
                root: Path, *, anchor: Optional[str] = None,
                classification: str = "DETECTED") -> None:
        observed = verify_integrity_scope(
            manifest, root,
            trusted_manifest_sha256=trusted_anchor if anchor is None else anchor,
            expected_data_contract_sha256=config.expected_data_contract_sha256,
            expected_framework_contract_sha256=config.expected_framework_contract_sha256,
            required_artifact_id="poc_fixture",
        )
        results[case_id] = _tamper_result(
            expected, observed.get("ok") is True, list(observed.get("errors", [])),
            classification=classification,
        )

    execute("T02", "pass", fixture_manifest, fixture_root)

    def case_root(case_id: str) -> Path:
        root = run_root / "tamper" / case_id
        (root / "fixture").mkdir(parents=True, exist_ok=True)
        _atomic_bytes(root / "fixture" / "poc_fixture.bin", base_data)
        return root

    root = case_root("T03")
    mutated = bytearray(base_data)
    mutated[0] ^= 0x01
    _atomic_bytes(root / "fixture" / "poc_fixture.bin", bytes(mutated))
    execute("T03", "reject", fixture_manifest, root)

    root = case_root("T04")
    (root / "fixture" / "poc_fixture.bin").unlink()
    execute("T04", "reject", fixture_manifest, root)

    root = case_root("T05")
    manifest = copy.deepcopy(dict(fixture_manifest))
    manifest["artifacts"][0]["bytes"] += 1
    execute("T05", "reject", manifest, root)

    root = case_root("T06")
    manifest = copy.deepcopy(dict(fixture_manifest))
    manifest["artifacts"][0]["source_record_id"] = "tampered-source-record"
    execute("T06", "reject", manifest, root)

    root = case_root("T07")
    manifest = copy.deepcopy(dict(fixture_manifest))
    manifest["manifest_sha256"] = pretty_input_manifest_hash(manifest)
    execute("T07", "reject", manifest, root)

    root = case_root("T08")
    manifest = copy.deepcopy(dict(fixture_manifest))
    manifest["artifacts"][0]["source_record_id"] = "tampered-and-rehashed"
    manifest["manifest_sha256"] = canonical_input_manifest_hash(manifest)
    execute("T08", "reject", manifest, root)

    root = case_root("T09")
    manifest = copy.deepcopy(dict(fixture_manifest))
    observed = verify_integrity_scope(
        manifest, root, trusted_manifest_sha256=trusted_anchor,
        expected_data_contract_sha256="f" * 64,
        expected_framework_contract_sha256=config.expected_framework_contract_sha256,
        required_artifact_id="poc_fixture",
    )
    results["T09"] = _tamper_result(
        "reject", observed.get("ok") is True, list(observed.get("errors", [])))

    root = case_root("T10")
    observed = verify_integrity_scope(
        fixture_manifest, root, trusted_manifest_sha256=trusted_anchor,
        expected_data_contract_sha256=config.expected_data_contract_sha256,
        expected_framework_contract_sha256="e" * 64,
        required_artifact_id="poc_fixture",
    )
    results["T10"] = _tamper_result(
        "reject", observed.get("ok") is True, list(observed.get("errors", [])))

    for case_id, relative_path in (("T11", "/absolute/poc_fixture.bin"),
                                   ("T12", "../outside/poc_fixture.bin")):
        root = case_root(case_id)
        manifest = copy.deepcopy(dict(fixture_manifest))
        manifest["artifacts"][0]["relative_path"] = relative_path
        manifest["manifest_sha256"] = canonical_input_manifest_hash(manifest)
        anchor = manifest["manifest_sha256"]
        execute(case_id, "reject", manifest, root, anchor=anchor)

    root = case_root("T13")
    incomplete_scenarios: list[str] = []
    outside = run_root / "outside-tamper-target.bin"
    _atomic_bytes(outside, base_data)
    symlink = root / "fixture" / "poc_fixture.bin"
    symlink.unlink()
    try:
        symlink.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        results["T13"] = {
            "expected": "reject", "observed_verified": False, "passed": False,
            "classification": "INCOMPLETE", "errors": [f"symlink test unavailable: {exc}"],
        }
        incomplete_scenarios.append("T13")
    else:
        execute("T13", "reject", fixture_manifest, root)

    for case_id, mutation in (("T14", "duplicate-id"), ("T15", "duplicate-path")):
        root = case_root(case_id)
        manifest = copy.deepcopy(dict(fixture_manifest))
        duplicate = copy.deepcopy(manifest["artifacts"][0])
        if mutation == "duplicate-id":
            duplicate["artifact_id"] = "poc_fixture"
            duplicate["relative_path"] = "fixture/poc_fixture_duplicate.bin"
        else:
            duplicate["artifact_id"] = "poc_fixture_duplicate"
        manifest["artifacts"].append(duplicate)
        manifest["artifact_count"] = 2
        manifest["manifest_sha256"] = canonical_input_manifest_hash(manifest)
        # The trusted anchor is intentionally updated here so the structural
        # validator, rather than the anchor mismatch, proves the rejection.
        execute(case_id, "reject", manifest, root,
                anchor=manifest["manifest_sha256"])

    tampered_envelope = bind_artifact_envelope({
        "profile_id": PROFILE_ID, "poc_status": STATUS_PASS,
        "gate_id": GATE_ID, "promotion_eligible": False,
    })
    tampered_envelope["poc_status"] = STATUS_FAILED
    envelope_ok, envelope_errors = verify_artifact_envelope(tampered_envelope)
    results["T16"] = _tamper_result(
        "reject", envelope_ok, envelope_errors,
    )

    root = case_root("T17")
    changed = bytearray(base_data)
    changed[-1] ^= 0x01
    changed_bytes = bytes(changed)
    _atomic_bytes(root / "fixture" / "poc_fixture.bin", changed_bytes)
    manifest = copy.deepcopy(dict(fixture_manifest))
    manifest["artifacts"][0]["sha256"] = hashlib.sha256(changed_bytes).hexdigest()
    manifest["manifest_sha256"] = canonical_input_manifest_hash(manifest)
    observed = verify_integrity_scope(
        manifest, root, trusted_manifest_sha256=manifest["manifest_sha256"],
        expected_data_contract_sha256=config.expected_data_contract_sha256,
        expected_framework_contract_sha256=config.expected_framework_contract_sha256,
        required_artifact_id="poc_fixture",
    )
    results["T17"] = _tamper_result(
        "document", observed.get("ok") is True, list(observed.get("errors", [])),
        classification="ANCHOR_COMPROMISED",
    )

    failed = [case_id for case_id, result in results.items()
              if result.get("passed") is not True]
    return {
        "status": (STATUS_INCOMPLETE if incomplete_scenarios else
                    ("PASS" if not failed else STATUS_FAILED)),
        "scenario_count": len(results),
        "failed_scenarios": failed,
        "incomplete_scenarios": incomplete_scenarios,
        "elapsed_seconds": time.perf_counter() - started,
        "scenarios": results,
        "limitation": (
            "T17 demonstrates that changing content, manifest, and trusted anchor "
            "together is not detectable without an external trust root."
        ),
    }


def _validate_config(config: IntegrityPocConfig) -> list[str]:
    problems: list[str] = []
    if not _valid_sha(config.expected_data_contract_sha256):
        problems.append("expected_data_contract_sha256 must be an explicit lowercase SHA-256")
    if not _valid_sha(config.expected_framework_contract_sha256):
        problems.append(
            "expected_framework_contract_sha256 must be an explicit lowercase SHA-256")
    if not config.real_artifact_id:
        problems.append("real_artifact_id is required")
    if config.fixture_bytes <= 0 or config.fixture_bytes > MAX_FIXTURE_BYTES:
        problems.append(
            f"fixture_bytes must be between 1 and {MAX_FIXTURE_BYTES} bytes")
    if config.timeout_seconds <= 0:
        problems.append("timeout_seconds must be positive")
    elif config.timeout_seconds > DEFAULT_TIMEOUT_SECONDS:
        problems.append(
            f"timeout_seconds must not exceed {DEFAULT_TIMEOUT_SECONDS:g} seconds")
    if config.warning_seconds < 0:
        problems.append("warning_seconds must be non-negative")
    elif config.warning_seconds > DEFAULT_WARNING_SECONDS:
        problems.append(
            f"warning_seconds must not exceed {DEFAULT_WARNING_SECONDS:g} seconds")
    if config.warning_seconds > config.timeout_seconds:
        problems.append("warning_seconds must not exceed timeout_seconds")
    if config.chunk_bytes <= 0:
        problems.append("chunk_bytes must be positive")
    if config.max_peak_rss_mib <= 0:
        problems.append("max_peak_rss_mib must be positive")
    elif config.max_peak_rss_mib > DEFAULT_MAX_PEAK_RSS_MIB:
        problems.append(
            f"max_peak_rss_mib must not exceed {DEFAULT_MAX_PEAK_RSS_MIB:g}")
    if config.minimum_free_gib < 0:
        problems.append("minimum_free_gib must be non-negative")
    elif config.minimum_free_gib < DEFAULT_MINIMUM_FREE_GIB:
        problems.append(
            f"minimum_free_gib must be at least {DEFAULT_MINIMUM_FREE_GIB:g}")
    if config.scope_manifest is None and config.source_manifest is None:
        problems.append("either source_manifest or scope_manifest is required")
    if config.scope_manifest is not None and config.source_manifest is not None:
        problems.append("source_manifest and scope_manifest are mutually exclusive")
    if config.scope_manifest is not None and config.scope_root is None:
        problems.append("scope_root is required with scope_manifest")
    if config.scope_manifest is not None and config.generate_demo_anchor:
        problems.append("strict scope replay requires an externally supplied trusted anchor")
    if (config.scope_manifest is not None and
            not _valid_sha(config.trusted_manifest_sha256)):
        problems.append("strict scope replay requires a trusted manifest SHA-256")
    if config.source_manifest is not None and config.generate_demo_anchor and (
            config.trusted_manifest_sha256 is not None):
        problems.append(
            "source mode cannot combine --generate-demo-anchor with a trusted anchor")
    if (config.scope_manifest is None and not config.generate_demo_anchor and
            not _valid_sha(config.trusted_manifest_sha256)):
        problems.append(
            "source mode requires --generate-demo-anchor or a trusted manifest hash")
    return problems


def _git_text(repo_root: Path, args: list[str]) -> tuple[Optional[str], Optional[str]]:
    try:
        completed = subprocess.run(
            ["git", *args], cwd=str(repo_root), capture_output=True,
            text=True, check=False, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"git {' '.join(args)} failed: {exc}"
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        return None, f"git {' '.join(args)} failed ({completed.returncode}): {detail}"
    return completed.stdout.strip(), None


def _git_stream_digest(repo_root: Path, args: list[str]) -> tuple[
        Optional[str], int, int, Optional[str]]:
    """Hash git output without retaining a potentially large diff in memory."""
    try:
        process = subprocess.Popen(
            ["git", *args], cwd=str(repo_root), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        return None, 0, -1, f"git {' '.join(args)} failed to start: {exc}"
    digest = hashlib.sha256()
    output_bytes = 0
    try:
        stdout = process.stdout
        if stdout is None:
            raise OSError("git stdout pipe was not created")
        while True:
            chunk = stdout.read(1 << 20)
            if not chunk:
                break
            output_bytes += len(chunk)
            digest.update(chunk)
        stderr = process.stderr.read().decode("utf-8", errors="replace") \
            if process.stderr is not None else ""
        return_code = process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        return None, output_bytes, -1, f"git {' '.join(args)} timed out"
    except OSError as exc:
        process.kill()
        process.wait()
        return None, output_bytes, -1, f"git {' '.join(args)} failed: {exc}"
    if return_code != 0:
        detail = stderr.strip()
        return None, output_bytes, return_code, (
            f"git {' '.join(args)} failed ({return_code}): {detail}")
    return digest.hexdigest(), output_bytes, return_code, None


def _baseline_snapshot(repo_root: Path, free_bytes: int) -> dict[str, Any]:
    """Capture a compact, read-only baseline without storing dirty-file content."""
    errors: list[str] = []
    git_root_text, git_root_error = _git_text(repo_root, ["rev-parse", "--show-toplevel"])
    if git_root_error:
        errors.append(git_root_error)
    git_root = Path(git_root_text).resolve(strict=False) if git_root_text else None
    root_match = git_root == repo_root.resolve(strict=False)

    head, head_error = _git_text(repo_root, ["rev-parse", "HEAD"])
    if head_error:
        errors.append(head_error)

    command_digests: dict[str, Any] = {}
    for name, args in (
        ("status_sha256", ["status", "--short", "--untracked-files=all"]),
        ("diff_sha256", ["diff", "--no-ext-diff", "--binary"]),
        ("diff_stat_sha256", ["diff", "--no-ext-diff", "--stat"]),
        ("diff_check_sha256", ["diff", "--no-ext-diff", "--check"]),
    ):
        digest, output_bytes, return_code, error = _git_stream_digest(repo_root, args)
        command_digests[name] = {
            "sha256": digest,
            "output_bytes": output_bytes,
            "return_code": return_code,
        }
        # A non-zero diff --check records a dirty-state warning but does not
        # turn an unrelated pre-existing user change into a PoC mutation.
        if error and name != "diff_check_sha256":
            errors.append(error)

    def source_hash(label: str, path: Path) -> Optional[str]:
        if path.is_symlink() or not path.is_file():
            errors.append(f"{label} source is unavailable: {path}")
            return None
        try:
            return sha256_file(path)
        except OSError as exc:
            errors.append(f"{label} source hash failed: {exc}")
            return None

    data_source = repo_root / input_manifest_module.AUTHORITATIVE_DATA_CONTRACT_PATH
    framework_source = repo_root / "nepal" / "framework_v1" / "contract.py"
    preregistration = repo_root / "preregistration.md"
    return {
        "repo_root": str(repo_root.resolve(strict=False)),
        "git_root": str(git_root) if git_root is not None else None,
        "root_match": root_match,
        "head": head,
        "git": command_digests,
        "data_contract_source": str(data_source),
        "data_contract_source_sha256": source_hash("data contract", data_source),
        "framework_contract_source": str(framework_source),
        "framework_contract_source_sha256": source_hash(
            "framework contract", framework_source),
        "runtime_framework_contract_sha256": C.contract_hash(),
        "preregistration": str(preregistration),
        "preregistration_sha256": source_hash("preregistration", preregistration),
        "python_executable": sys.executable,
        "python_version": sys.version,
        "free_bytes_before": free_bytes,
        "dirty_state_policy": "read-only; preserve all pre-existing user changes",
        "diff_check_passed": command_digests.get(
            "diff_check_sha256", {}).get("return_code") == 0,
        "errors": errors,
    }


def _preflight(config: IntegrityPocConfig, run_root: Path,
               *, run_root_preexisting: bool = False) -> dict[str, Any]:
    problems: list[str] = []
    repo_root = config.repo_root.resolve(strict=False)
    real_root = config.real_root.resolve(strict=False)
    output_path = config.output_path.resolve(strict=False)
    if not repo_root.is_dir():
        problems.append(f"repo_root is not a directory: {repo_root}")
    if not real_root.is_dir():
        problems.append(f"real_root is not a directory: {real_root}")
    if _path_is_under(output_path, repo_root) or _path_is_under(output_path, real_root):
        problems.append("PoC output must not be inside the repository or live data root")
    input_paths = [
        path.resolve(strict=False) for path in
        (config.source_manifest, config.scope_manifest) if path is not None
    ]
    if output_path in input_paths:
        problems.append("PoC output must not overwrite an input manifest")
    run_root_has_entries = any(run_root.iterdir()) if run_root.exists() else False
    if run_root_preexisting and run_root_has_entries:
        problems.append("PoC output parent must be a fresh empty run directory")
    disk_path = run_root
    while not disk_path.exists() and disk_path != disk_path.parent:
        disk_path = disk_path.parent
    usage = shutil.disk_usage(disk_path)
    minimum_bytes = int(config.minimum_free_gib * 1024 ** 3)
    disk = {
        "free_bytes": usage.free,
        "free_gib": round(usage.free / 1024 ** 3, 3),
        "minimum_free_gib": config.minimum_free_gib,
        "warning_below_gib": WARNING_FREE_GIB,
        "passed": usage.free >= minimum_bytes,
    }
    if not disk["passed"]:
        problems.append("free disk space is below the PoC hard stop")
    if config.source_manifest is not None:
        source = config.source_manifest.resolve(strict=False)
        if source.is_symlink():
            problems.append("source manifest must not be a symlink")
        if not source.is_file():
            problems.append(f"source manifest is not a file: {source}")
    if config.scope_manifest is not None:
        scope = config.scope_manifest.resolve(strict=False)
        if scope.is_symlink() or not scope.is_file():
            problems.append(f"scope manifest is not a regular file: {scope}")
    baseline = _baseline_snapshot(repo_root, usage.free)
    if not baseline["root_match"]:
        problems.append("repo_root is not the authoritative git checkout root")
    if baseline["errors"]:
        problems.extend(baseline["errors"])
    return {
        "status": "PASS" if not problems else STATUS_BLOCKED,
        "passed": not problems,
        "errors": problems,
        "repo_root": str(repo_root),
        "real_root": str(real_root),
        "output_path": str(output_path),
        "run_root": str(run_root),
        "fresh_run_directory": not run_root_has_entries,
        "run_directory_created_by_poc": not run_root_preexisting,
        "clean_run_directory": not run_root_has_entries,
        "disk_path": str(disk_path),
        "disk": disk,
        "baseline": baseline,
        "limits": {
            "max_fixture_bytes": MAX_FIXTURE_BYTES,
            "max_run_bytes": MAX_RUN_BYTES,
            "max_report_bytes": MAX_REPORT_BYTES,
            "no_data_download": True,
            "no_live_root_copy": True,
        },
    }


def _write_final_report(config: IntegrityPocConfig, report: Mapping[str, Any]) -> dict[str, Any]:
    """Atomically write and bind the complete report, including write timing."""
    candidate = dict(report)
    started = time.perf_counter()
    bound = bind_artifact_envelope(candidate)
    first_text = write_deterministic_json(config.output_path, bound)
    first_elapsed = time.perf_counter() - started
    first_bytes = len(first_text.encode("utf-8"))
    try:
        report_bytes = config.output_path.stat().st_size
    except OSError:
        report_bytes = None
    if isinstance(report_bytes, int) and report_bytes > MAX_REPORT_BYTES:
        candidate["poc_status"] = STATUS_INCOMPLETE
        candidate["exit_code"] = EXIT_INCOMPLETE
        errors = list(candidate.get("errors", []))
        errors.append(
            f"final report is {report_bytes} bytes, above {MAX_REPORT_BYTES}-byte limit")
        candidate["errors"] = list(dict.fromkeys(errors))
    candidate["report_writing"] = {
        "atomic": True,
        "elapsed_seconds": first_elapsed,
        "output_bytes": report_bytes,
        "temporary_bytes_written": first_bytes,
        "temporary_bytes_retained": 0,
        "atomic_write_passes": 2,
        "max_report_bytes": MAX_REPORT_BYTES,
    }
    bound = bind_artifact_envelope(candidate)
    write_deterministic_json(config.output_path, bound)
    return bound


def _normalise_config(config: IntegrityPocConfig) -> IntegrityPocConfig:
    return IntegrityPocConfig(
        **{**config.__dict__,
           "repo_root": _as_path(config.repo_root),
           "real_root": _as_path(config.real_root),
           "output_path": _as_path(config.output_path),
           "source_manifest": (_as_path(config.source_manifest)
                               if config.source_manifest is not None else None),
           "scope_manifest": (_as_path(config.scope_manifest)
                              if config.scope_manifest is not None else None),
           "scope_root": (_as_path(config.scope_root)
                          if config.scope_root is not None else None),}
    )


def _guard_deadline(started: float, config: IntegrityPocConfig, label: str) -> None:
    elapsed = time.perf_counter() - started
    if elapsed > config.timeout_seconds:
        raise IntegrityPocIncomplete(
            f"PoC deadline exceeded after {label}: {elapsed:.3f}s > "
            f"{config.timeout_seconds:.3f}s")


def _require_benchmark(label: str, metric: Mapping[str, Any]) -> None:
    if metric.get("status") == "PASS":
        return
    message = f"{label} benchmark did not pass: " + "; ".join(
        str(error) for error in metric.get("errors", []))
    if metric.get("status") == STATUS_INCOMPLETE:
        raise IntegrityPocIncomplete(message)
    if metric.get("status") == STATUS_BLOCKED:
        raise IntegrityPocBlocked(message)
    raise RuntimeError(message)


def _prepare_scope(
    config: IntegrityPocConfig,
    run_root: Path,
    base_report: dict[str, Any],
    started: float,
) -> tuple[dict[str, Any], Path, str, str, dict[str, str], Optional[dict[str, Any]]]:
    source_diagnostic: Optional[dict[str, Any]] = None
    verified_hashes: dict[str, str] = {}
    if config.scope_manifest is not None:
        scope_manifest = dict(_load_json(config.scope_manifest))
        scope_root = config.scope_root
        assert scope_root is not None
        trusted_anchor = config.trusted_manifest_sha256
        anchor_mode = "external"
        scope_artifacts = scope_manifest.get("artifacts")
        selected_scope = [
            artifact for artifact in scope_artifacts or []
            if isinstance(artifact, Mapping) and
            artifact.get("artifact_id") == config.real_artifact_id
        ]
        if len(selected_scope) != 1:
            raise IntegrityPocBlocked(
                f"scope manifest must contain real artifact {config.real_artifact_id!r}")
        scope_path, path_errors = _regular_file(
            scope_root, selected_scope[0].get("relative_path"))
        if path_errors or scope_path is None:
            raise IntegrityPocBlocked("; ".join(path_errors))
        scope_hash = benchmark_sha256(
            scope_path, timeout_seconds=config.timeout_seconds,
            warning_seconds=config.warning_seconds, chunk_bytes=config.chunk_bytes,
            max_peak_rss_mib=config.max_peak_rss_mib,
        )
        _require_benchmark("scope artifact", scope_hash)
        if scope_hash.get("digest") != selected_scope[0].get("sha256"):
            raise IntegrityPocBlocked("scope artifact digest does not match its manifest")
        base_report["hash_benchmark"] = {"real_artifact": scope_hash}
        verified_hashes[str(scope_path)] = str(scope_hash["digest"])
    else:
        assert config.source_manifest is not None
        source_manifest = _load_json(config.source_manifest)
        source_diagnostic = _source_manifest_diagnostic(
            config.source_manifest, source_manifest)
        source_artifact = _find_artifact(source_manifest, config.real_artifact_id)
        artifact_path, path_errors = _regular_file(
            config.real_root, source_artifact.get("relative_path"))
        if path_errors or artifact_path is None:
            raise IntegrityPocBlocked("; ".join(path_errors))
        if source_artifact.get("bytes") != artifact_path.stat().st_size:
            raise IntegrityPocBlocked("real artifact byte count does not match source manifest")
        real_hash = benchmark_sha256(
            artifact_path, timeout_seconds=config.timeout_seconds,
            warning_seconds=config.warning_seconds, chunk_bytes=config.chunk_bytes,
            max_peak_rss_mib=config.max_peak_rss_mib,
        )
        _require_benchmark("real artifact", real_hash)
        if real_hash.get("digest") != source_artifact.get("sha256"):
            raise IntegrityPocBlocked("real artifact digest does not match source manifest")
        verified_hashes[str(artifact_path)] = str(real_hash["digest"])
        scope_manifest = _build_scope_manifest(
            source_artifact, config.expected_data_contract_sha256,
            config.expected_framework_contract_sha256,
        )
        scope_root = config.real_root
        trusted_anchor = (scope_manifest["manifest_sha256"]
                          if config.generate_demo_anchor else
                          config.trusted_manifest_sha256)
        anchor_mode = "run_generated_demo" if config.generate_demo_anchor else "external"
        base_report["hash_benchmark"] = {"real_artifact": real_hash}
        write_deterministic_json(run_root / "source_scope_manifest.json", scope_manifest)

    if not _valid_sha(trusted_anchor):
        raise IntegrityPocBlocked("a trusted manifest anchor is required")
    assert isinstance(trusted_anchor, str)
    _guard_deadline(started, config, "real artifact hash")
    return (scope_manifest, scope_root, trusted_anchor, anchor_mode,
            verified_hashes, source_diagnostic)


def _run_fixture(
    config: IntegrityPocConfig,
    run_root: Path,
    base_report: dict[str, Any],
    started: float,
) -> tuple[dict[str, Any], Path, str, dict[str, Any]]:
    fixture_root = run_root / "fixture_root"
    fixture_path = fixture_root / "fixture" / "poc_fixture.bin"
    fixture_data = _fixture_bytes(config.fixture_bytes)
    _atomic_bytes(fixture_path, fixture_data)
    fixture_artifact = _artifact_template(
        "poc_fixture", "fixture/poc_fixture.bin",
        hashlib.sha256(fixture_data).hexdigest(), len(fixture_data),
        source_url="https://example.invalid/integrity-poc-v1",
        source_record_id="integrity-poc-v1-fixture",
    )
    fixture_manifest = _build_scope_manifest(
        fixture_artifact, config.expected_data_contract_sha256,
        config.expected_framework_contract_sha256,
    )
    write_deterministic_json(run_root / "fixture_scope_manifest.json", fixture_manifest)
    fixture_anchor = fixture_manifest["manifest_sha256"]
    fixture_benchmark = benchmark_sha256(
        fixture_path, timeout_seconds=config.timeout_seconds,
        warning_seconds=config.warning_seconds, chunk_bytes=config.chunk_bytes,
        max_peak_rss_mib=config.max_peak_rss_mib,
    )
    _require_benchmark("fixture", fixture_benchmark)
    fixture_scope = verify_integrity_scope(
        fixture_manifest, fixture_root,
        trusted_manifest_sha256=fixture_anchor,
        expected_data_contract_sha256=config.expected_data_contract_sha256,
        expected_framework_contract_sha256=config.expected_framework_contract_sha256,
        required_artifact_id="poc_fixture",
        verified_file_hashes={str(fixture_path): str(fixture_benchmark["digest"])},
    )
    base_report["fixture"] = {
        "path": str(fixture_path), "bytes": len(fixture_data),
        "scope_manifest": fixture_scope, "benchmark": fixture_benchmark,
    }
    base_report.setdefault("hash_benchmark", {})["fixture"] = fixture_benchmark
    base_report["loader_smoke"]["fixture"] = fixture_scope
    if not fixture_scope.get("ok"):
        raise IntegrityPocCorrectness("fixture loader did not pass")
    _guard_deadline(started, config, "fixture loader smoke")
    return fixture_manifest, fixture_root, fixture_anchor, fixture_scope


def _run_full_b_boundary(config: IntegrityPocConfig,
                         base_report: dict[str, Any]) -> None:
    if not (config.run_b_loader_boundary and config.source_manifest is not None):
        return
    from .adapters import load_verified_b_input_bundle
    current_manifest = _load_json(config.source_manifest)
    boundary = load_verified_b_input_bundle(
        config.real_root, current_manifest,
        expected_contract_sha256=config.expected_data_contract_sha256,
        expected_framework_contract_sha256=config.expected_framework_contract_sha256,
        repo_root=config.repo_root,
    )
    base_report["loader_boundary"] = {
        "status": boundary.status,
        "fail_closed": boundary.status in {"BLOCKED", C.PHASE_STATUS_LOAD_READY},
        "errors": list(boundary.errors), "warnings": list(boundary.warnings),
    }
    base_report["loader_smoke"]["full_b_loader_boundary"] = base_report[
        "loader_boundary"]
    if not base_report["loader_boundary"]["fail_closed"]:
        raise IntegrityPocCorrectness("full B loader returned an unsafe boundary status")


def _resource_guard(config: IntegrityPocConfig, preflight: Mapping[str, Any],
                    run_root: Path, hash_benchmark: Mapping[str, Any]) -> dict[str, Any]:
    usage_before = int(preflight["disk"]["free_bytes"])
    usage_after = shutil.disk_usage(run_root)
    run_bytes = _run_bytes(run_root)
    benchmark_records = [
        value for value in hash_benchmark.values()
        if isinstance(value, Mapping) and value.get("algorithm") == "sha256"
    ]
    rss_values: list[int] = []
    for value in benchmark_records:
        peak_rss = value.get("peak_rss_bytes")
        if isinstance(peak_rss, int) and not isinstance(peak_rss, bool):
            rss_values.append(peak_rss)
    rss_limit = int(config.max_peak_rss_mib * 1024 * 1024)
    rss_present = len(rss_values) == len(benchmark_records)
    return {
        "disk_free_before_bytes": usage_before,
        "disk_free_after_bytes": usage_after.free,
        "free_gib_after": round(usage_after.free / 1024 ** 3, 3),
        "run_directory_bytes": run_bytes,
        "max_run_directory_bytes": MAX_RUN_BYTES,
        "fixture_bytes": config.fixture_bytes,
        "max_fixture_bytes": MAX_FIXTURE_BYTES,
        "warning_free_gib": WARNING_FREE_GIB,
        "rss_source": "resource.RUSAGE_SELF.ru_maxrss",
        "rss_measurements_present": rss_present,
        "peak_rss_bytes": max(rss_values) if rss_values else None,
        "max_peak_rss_bytes": rss_limit,
        "passed": (
            run_bytes <= MAX_RUN_BYTES and
            usage_after.free >= int(config.minimum_free_gib * 1024 ** 3) and
            rss_present and all(value <= rss_limit for value in rss_values)
        ),
    }


def _record_timing(report: dict[str, Any], started: float,
                   config: IntegrityPocConfig, *, timeout: bool) -> None:
    elapsed = time.perf_counter() - started
    report["timing"] = {
        "elapsed_seconds": elapsed, "warning_seconds": config.warning_seconds,
        "warning_exceeded": elapsed > config.warning_seconds, "timeout": timeout,
    }
    warnings = list(report.get("warnings", []))
    if elapsed > config.warning_seconds:
        warnings.append(
            f"PoC runtime exceeded warning threshold of {config.warning_seconds:g}s")
    report["warnings"] = list(dict.fromkeys(warnings))


def _failure_report(config: IntegrityPocConfig, report: dict[str, Any],
                    checkpoint_path: Path, started: float, status: str,
                    exit_code: int, exc: BaseException, *, timeout: bool) -> dict[str, Any]:
    report["poc_status"] = status
    report["exit_code"] = exit_code
    report["errors"] = [str(exc)]
    _record_timing(report, started, config, timeout=timeout)
    _write_checkpoint(checkpoint_path, {
        "profile_id": PROFILE_ID, "run_state": status,
        "promotion_eligible": False, "errors": [str(exc)],
    })
    return _write_final_report(config, report)


def _execute_poc(config: IntegrityPocConfig, run_root: Path,
                 preflight: Mapping[str, Any], base_report: dict[str, Any],
                 progress_path: Path, started: float) -> None:
    scope_manifest, scope_root, trusted_anchor, anchor_mode, verified_hashes, source_diag = (
        _prepare_scope(config, run_root, base_report, started)
    )
    scope_path = run_root / "scope_manifest.json"
    write_deterministic_json(scope_path, scope_manifest)
    write_deterministic_json(run_root / "trusted_anchor.json", {
        "profile_id": PROFILE_ID,
        "manifest_sha256": scope_manifest.get("manifest_sha256"),
        "trusted_manifest_sha256": trusted_anchor,
        "anchor_mode": anchor_mode,
        "security_authority": "NOT_PROVEN" if anchor_mode != "external" else "EXTERNAL_INPUT",
    })
    _append_progress(progress_path, {"step": "scope-manifest", "status": "STARTED"})

    selected_id = str(scope_manifest.get("required_artifact_ids", [""])[0])
    real_scope = verify_integrity_scope(
        scope_manifest, scope_root,
        trusted_manifest_sha256=trusted_anchor,
        expected_data_contract_sha256=config.expected_data_contract_sha256,
        expected_framework_contract_sha256=config.expected_framework_contract_sha256,
        required_artifact_id=selected_id,
        verified_file_hashes=verified_hashes,
    )
    base_report["scope_manifest"] = {
        "path": str(scope_path), "manifest_sha256": scope_manifest.get("manifest_sha256"),
        "anchor_mode": anchor_mode, "verification": real_scope,
    }
    base_report["loader_smoke"] = {"real_artifact": real_scope}
    if not real_scope.get("ok"):
        raise IntegrityPocBlocked("real scope loader did not pass")
    _guard_deadline(started, config, "real loader smoke")
    _append_progress(progress_path, {"step": "real-loader-smoke", "status": "PASS"})

    fixture_manifest, fixture_root, fixture_anchor, fixture_scope = _run_fixture(
        config, run_root, base_report, started)
    base_report["hash_accounting"] = {
        "benchmark_hash_calls": sum(
            int(value.get("hash_calls", 0))
            for value in base_report["hash_benchmark"].values()
            if isinstance(value, Mapping)
        ),
        "benchmark_unique_paths_hashed": sum(
            int(value.get("unique_paths_hashed", 0))
            for value in base_report["hash_benchmark"].values()
            if isinstance(value, Mapping)
        ),
        "loader_hash_cache_hits": sum(
            int(smoke.get("checks", {}).get("hash_cache_hits", 0))
            for smoke in (real_scope, fixture_scope)
        ),
        "duplicate_hash_passes_in_measured_baseline": 0,
        "cache_reuse_scope": "exact verified artifact paths only",
    }
    _append_progress(progress_path, {"step": "fixture-loader-smoke", "status": "PASS"})

    tamper = _run_tamper_benchmark(
        run_root, fixture_manifest, fixture_root, fixture_anchor, config)
    scenarios = dict(tamper.get("scenarios", {}))
    scenarios = {
        "T01": _tamper_result(
            "pass", real_scope.get("ok") is True,
            list(real_scope.get("errors", [])),
        ),
        **scenarios,
    }
    tamper["scenarios"] = scenarios
    tamper["scenario_count"] = len(scenarios)
    tamper["failed_scenarios"] = [
        case_id for case_id, result in scenarios.items()
        if result.get("passed") is not True
    ]
    if tamper["failed_scenarios"]:
        tamper["status"] = STATUS_FAILED
    base_report["tamper_benchmark"] = tamper
    _append_progress(progress_path, {
        "step": "tamper-benchmark", "status": tamper["status"],
        "scenario_count": tamper["scenario_count"],
    })
    if tamper.get("status") == STATUS_INCOMPLETE:
        raise IntegrityPocIncomplete("tamper benchmark could not complete")
    if tamper.get("status") != "PASS":
        raise IntegrityPocCorrectness("tamper benchmark failed")

    _run_full_b_boundary(config, base_report)
    _guard_deadline(started, config, "tamper and loader boundary")
    resource = _resource_guard(
        config, preflight, run_root, base_report["hash_benchmark"])
    base_report["resource_guard"] = resource
    if not resource["passed"]:
        raise IntegrityPocIncomplete("resource guard failed")

    _record_timing(base_report, started, config, timeout=False)
    warnings = list(base_report.get("warnings", []))
    for label, metric in base_report.get("hash_benchmark", {}).items():
        if isinstance(metric, Mapping) and metric.get("warning_exceeded"):
            warnings.append(f"{label} hash runtime exceeded the warning threshold")
    if preflight["disk"]["free_bytes"] < int(WARNING_FREE_GIB * 1024 ** 3):
        warnings.append(f"free disk space was below {WARNING_FREE_GIB:g} GiB")
    base_report["warnings"] = list(dict.fromkeys(warnings))
    base_report["source_manifest"] = (
        source_diag if source_diag is not None else {
            "used": False, "state": "NOT_USED",
            "used_as_production_authorization": False,
        }
    )
    base_report["contract_binding"] = {
        "data_contract_sha256": config.expected_data_contract_sha256,
        "framework_contract_sha256": config.expected_framework_contract_sha256,
        "trusted_manifest_sha256": trusted_anchor,
        "anchor_mode": anchor_mode,
    }
    base_report["poc_status"] = STATUS_PASS
    base_report["exit_code"] = EXIT_PASS


def run_integrity_poc(config: IntegrityPocConfig) -> dict[str, Any]:
    """Run the complete narrow PoC and return its authenticated report."""
    config = _normalise_config(config)
    output_candidate = config.output_path.resolve(strict=False)
    repo_candidate = config.repo_root.resolve(strict=False)
    real_candidate = config.real_root.resolve(strict=False)
    if (_path_is_under(output_candidate, repo_candidate) or
            _path_is_under(output_candidate, real_candidate)):
        return {
            "profile_id": PROFILE_ID, "poc_status": STATUS_BLOCKED,
            "gate_id": GATE_ID, "production_manifest_authorized": False,
            "promotion_eligible": False, "security_authority": "NOT_PROVEN",
            "errors": ["PoC output must not be inside the repository or live data root"],
            "exit_code": EXIT_BLOCKED,
        }
    run_root = config.output_path.parent
    run_root_preexisting = run_root.exists()
    run_root.mkdir(parents=True, exist_ok=True)
    preflight = _preflight(
        config, run_root, run_root_preexisting=run_root_preexisting)
    checkpoint_path = run_root / "poc_checkpoint.json"
    progress_path = run_root / "progress.jsonl"
    _write_checkpoint(checkpoint_path, {
        "profile_id": PROFILE_ID, "run_state": "RUNNING",
        "promotion_eligible": False,
    })
    _append_progress(progress_path, {"step": "preflight", "status": preflight["status"]})
    base_report: dict[str, Any] = {
        "profile_id": PROFILE_ID, "poc_status": STATUS_BLOCKED,
        "gate_id": GATE_ID, "production_manifest_authorized": False,
        "promotion_eligible": False, "security_authority": "NOT_PROVEN",
        "preflight": preflight, "baseline": preflight.get("baseline", {}),
        "provenance": {
            "repo_root": str(config.repo_root.resolve(strict=False)),
            "real_root": str(config.real_root.resolve(strict=False)),
            "output_path": str(config.output_path.resolve(strict=False)),
            "python": sys.executable, "platform": sys.platform,
            "hash_algorithm": "sha256",
        },
        "warnings": [],
        "limitations": [
            "No signature or key-management infrastructure",
            "A run-generated anchor does not prove external authenticity",
            "No B ranking was executed",
            "No B_TO_C, A, E, F, warning, or production authorization",
        ],
    }
    config_problems = _validate_config(config)
    if config_problems:
        base_report["exit_code"] = EXIT_BLOCKED
        base_report["errors"] = config_problems
        _write_checkpoint(checkpoint_path, {
            "profile_id": PROFILE_ID, "run_state": STATUS_BLOCKED,
            "promotion_eligible": False, "errors": config_problems,
        })
        return _write_final_report(config, base_report)
    if not preflight["passed"]:
        base_report["exit_code"] = EXIT_BLOCKED
        base_report["errors"] = preflight["errors"]
        _write_checkpoint(checkpoint_path, {
            "profile_id": PROFILE_ID, "run_state": STATUS_BLOCKED,
            "promotion_eligible": False, "errors": preflight["errors"],
        })
        return _write_final_report(config, base_report)

    started = time.perf_counter()
    try:
        _execute_poc(config, run_root, preflight, base_report, progress_path, started)
        _write_checkpoint(checkpoint_path, {
            "profile_id": PROFILE_ID, "run_state": "COMPLETE",
            "poc_status": STATUS_PASS, "promotion_eligible": False,
        })
        return _write_final_report(config, base_report)
    except IntegrityPocBlocked as exc:
        return _failure_report(
            config, base_report, checkpoint_path, started, STATUS_BLOCKED,
            EXIT_BLOCKED, exc, timeout=False)
    except IntegrityPocIncomplete as exc:
        return _failure_report(
            config, base_report, checkpoint_path, started, STATUS_INCOMPLETE,
            EXIT_INCOMPLETE, exc, timeout=True)
    except IntegrityPocCorrectness as exc:
        return _failure_report(
            config, base_report, checkpoint_path, started, STATUS_FAILED,
            EXIT_CORRECTNESS_FAILED, exc, timeout=False)
    except (OSError, TypeError, ValueError, RuntimeError, C.FrameworkError) as exc:
        return _failure_report(
            config, base_report, checkpoint_path, started, STATUS_FAILED,
            EXIT_UNEXPECTED, exc, timeout=False)


def _worker_entry(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--hash-worker", required=True)
    parser.add_argument("--chunk-bytes", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        payload = _worker_hash(Path(args.hash_worker), args.chunk_bytes)
    except (OSError, ValueError) as exc:
        sys.stderr.write(str(exc) + "\n")
        return 1
    sys.stdout.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess
    raise SystemExit(_worker_entry(sys.argv[1:]))
