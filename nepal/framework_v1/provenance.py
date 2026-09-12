"""nepal.framework_v1.provenance — deterministic serialization and manifests.

All hashes, JSON, CSV and manifest serialization in framework_v1 flow through
here so that identical inputs always produce byte-identical artifacts.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from .contract import FRAMEWORK_VERSION, HASH_ALGORITHM, OUTPUT_STATUSES

_RAW_SLC_KEY_MARKERS = (
    "slc_path", "raw_slc", "safe_path", "slc_zip", "slc_scene",
    "slc_file", "safe_file", "safe_archive", "slc_archive",
    "raw_scene_path", "raw_s1_path",
)
_RAW_SLC_VALUE_RE = re.compile(
    r"\bS1[ABCD][_-][^\s'\"]*(?:\.SAFE(?:\.ZIP)?(?:[/\\]|$)|\.SLC(?:[/\\]|$))",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Canonical serialization
# ---------------------------------------------------------------------------

def canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, compact separators, no NaN."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False, default=_json_default)


def _json_default(obj: Any) -> Any:
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if hasattr(obj, "value") and hasattr(obj, "name"):  # str-Enum fallback
        return obj.value
    raise TypeError(f"not JSON-serializable: {type(obj)!r}")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_canonical(obj: Any) -> str:
    return sha256_text(canonical_json(obj))


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def bind_gate_artifact(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Add a deterministic self-hash to a gate payload.

    A gate result is evidence, not authority.  Consumers can use
    :func:`verify_gate_artifact` to reject a modified or unbound payload before
    considering its ``passed`` value.
    """
    bound = dict(payload)
    bound.pop("gate_artifact_sha256", None)
    bound["gate_artifact_sha256"] = sha256_canonical(bound)
    return bound


def verify_gate_artifact(payload: Mapping[str, Any], *,
                         expected_gate_id: Optional[str] = None) -> tuple[bool, list[str]]:
    """Verify a self-hashed gate payload and its optional gate identifier."""
    if not isinstance(payload, Mapping):
        return False, ["gate artifact must be a mapping"]
    problems: list[str] = []
    stored = payload.get("gate_artifact_sha256")
    if not isinstance(stored, str) or not re.fullmatch(r"[0-9a-f]{64}", stored):
        problems.append("gate artifact self-hash is required")
    else:
        try:
            expected = sha256_canonical({
                key: value for key, value in payload.items()
                if key != "gate_artifact_sha256"
            })
        except (TypeError, ValueError) as exc:
            problems.append(f"gate artifact is not canonical JSON: {exc}")
        else:
            if stored != expected:
                problems.append("gate artifact self-hash does not match its content")
    if expected_gate_id is not None and payload.get("gate_id") != expected_gate_id:
        problems.append(
            f"gate artifact gate_id {payload.get('gate_id')!r} does not match "
            f"{expected_gate_id!r}")
    return not problems, problems


def bind_artifact_envelope(payload: Mapping[str, Any], *,
                           hash_field: str = "artifact_sha256") -> dict[str, Any]:
    """Bind the complete content of a non-gate framework artifact.

    Gate self-hashes intentionally cover the gate payload only.  Result and
    validation envelopes need a second, outer binding so that provenance,
    summaries, statuses, and source hashes cannot be edited around an honest
    nested gate.
    """
    if not isinstance(payload, Mapping):
        raise TypeError("artifact envelope must be a mapping")
    bound = dict(payload)
    bound.pop(hash_field, None)
    bound[hash_field] = sha256_canonical(bound)
    return bound


def verify_artifact_envelope(payload: Mapping[str, Any], *,
                             hash_field: str = "artifact_sha256") -> tuple[bool, list[str]]:
    """Verify the complete self-hash of a framework artifact envelope."""
    if not isinstance(payload, Mapping):
        return False, ["artifact envelope must be a mapping"]
    stored = payload.get(hash_field)
    if not isinstance(stored, str) or not re.fullmatch(r"[0-9a-f]{64}", stored):
        return False, [f"artifact envelope {hash_field} is required"]
    try:
        expected = sha256_canonical({
            key: value for key, value in payload.items() if key != hash_field
        })
    except (TypeError, ValueError) as exc:
        return False, [f"artifact envelope is not canonical JSON: {exc}"]
    if stored != expected:
        return False, [f"artifact envelope {hash_field} does not match its content"]
    return True, []


def _atomic_write_bytes(path: str | Path, data: bytes) -> None:
    """Write bytes atomically and durably within the destination directory."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{p.name}.", dir=str(p.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
        try:
            dir_fd = os.open(p.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            # Directory fsync is not available on every supported filesystem;
            # the file fsync and atomic replace still provide the key guarantee.
            pass
    finally:
        if tmp.exists():
            tmp.unlink()


def write_deterministic_json(path: str | Path, obj: Any) -> str:
    """Write canonical JSON with LF newlines and a trailing newline."""
    text = canonical_json(obj) + "\n"
    _atomic_write_bytes(path, text.encode("utf-8"))
    return text


def write_deterministic_text(path: str | Path, text: str) -> str:
    """Write UTF-8 LF text atomically, preserving deterministic content."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    _atomic_write_bytes(path, normalized.encode("utf-8"))
    return normalized


def write_deterministic_csv(path: "str | Path", fieldnames: Sequence[str],
                            rows: Iterable[Mapping[str, Any]]) -> str:
    """Write CSV deterministically: LF newlines, QUOTE_MINIMAL, UTF-8.

    Row and column order is exactly as provided; callers must sort rows and
    fix the column order before calling."""
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=list(fieldnames), lineterminator="\n",
                            extrasaction="raise")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: _csv_cell(row.get(k)) for k in fieldnames})
    _atomic_write_bytes(path, buf.getvalue().encode("utf-8"))
    return buf.getvalue()


def _csv_cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "|".join("" if v is None else str(v) for v in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def build_manifest(files: Mapping[str, "str | bytes | Path"],
                   manifest_type: str = "artifact") -> dict[str, Any]:
    """Build a deterministic checksum manifest over *files*.

    Keys are relative artifact paths (sorted); values are byte content or
    filesystem paths to hash.  No timestamps are recorded, so identical
    inputs always give identical manifests."""
    entries: dict[str, str] = {}
    for rel in sorted(files):
        rel_path = Path(str(rel))
        if (rel_path.is_absolute() or ".." in rel_path.parts or
                "\\" in str(rel) or "\x00" in str(rel) or
                rel_path == Path(".")):
            raise ValueError(f"unsafe manifest relative path: {rel!r}")
        content = files[rel]
        if isinstance(content, bytes):
            digest = sha256_bytes(content)
        elif isinstance(content, Path) or (isinstance(content, str)
                                           and Path(content).exists()):
            digest = sha256_file(Path(content))
        else:
            digest = sha256_text(str(content))
        entries[rel_path.as_posix()] = digest
    manifest = {
        "algorithm": HASH_ALGORITHM,
        "framework_version": FRAMEWORK_VERSION,
        "manifest_type": manifest_type,
        "files": entries,
    }
    manifest["manifest_sha256"] = sha256_canonical(
        {k: v for k, v in manifest.items()})
    return manifest


def verify_manifest(root: "str | Path", manifest: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """Verify every file listed in *manifest* against its recorded checksum."""
    root_p = Path(root)
    problems: list[str] = []
    if not isinstance(manifest, Mapping):
        return False, ["manifest must be a mapping"]
    if not root_p.exists() or not root_p.is_dir():
        return False, [f"manifest root is not an existing directory: {root_p}"]
    if manifest.get("algorithm") != HASH_ALGORITHM:
        problems.append(f"unexpected algorithm {manifest.get('algorithm')!r}")
    try:
        expected_self = sha256_canonical(
            {k: v for k, v in manifest.items() if k != "manifest_sha256"})
    except (TypeError, ValueError) as exc:
        expected_self = None
        problems.append(f"manifest contains non-canonical JSON values: {exc}")
    if expected_self is not None and manifest.get("manifest_sha256") != expected_self:
        problems.append("manifest_sha256 does not match manifest content "
                        "(manifest was modified after signing)")
    files = manifest.get("files")
    if not isinstance(files, Mapping):
        problems.append("manifest files must be a mapping")
        files = {}
    for rel, digest in sorted(files.items(), key=lambda item: str(item[0])):
        if not isinstance(rel, str) or "\x00" in rel:
            problems.append(f"unsafe manifest path: {rel!r}")
            continue
        rel_path = Path(rel)
        if rel_path.is_absolute() or "\\" in rel or ".." in rel_path.parts:
            problems.append(f"unsafe manifest path: {rel!r}")
            continue
        f = root_p / rel_path
        try:
            f.resolve(strict=False).relative_to(root_p.resolve(strict=True))
        except (FileNotFoundError, OSError, ValueError):
            problems.append(f"manifest path escapes root: {rel}")
            continue
        if any(part.is_symlink() for part in _path_parts(root_p, rel_path)):
            problems.append(f"symlink artifact path is forbidden: {rel}")
            continue
        if not f.exists():
            problems.append(f"missing artifact: {rel}")
            continue
        if not f.is_file():
            problems.append(f"artifact path is not a file: {rel}")
            continue
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            problems.append(f"invalid sha256 for artifact: {rel}")
            continue
        actual = sha256_file(f)
        if actual != digest:
            problems.append(f"checksum mismatch: {rel} ({digest} -> {actual})")
    return (len(problems) == 0, problems)


def _path_parts(root: Path, relative: Path) -> list[Path]:
    current = root
    parts = []
    for part in relative.parts:
        current = current / part
        parts.append(current)
    return parts


def check_no_raw_slc_paths(manifest: Mapping[str, Any]) -> list[str]:
    """Raw Sentinel-1 SLC scene paths must never appear in accepted manifests.

    framework_v1 B/E artifacts may reference acquisition *metadata* and
    checksum-valid derived products only.  Returns a list of violations."""
    violations: list[str] = []

    def scan(node: Any, path: str) -> None:
        if isinstance(node, Mapping):
            for k, v in node.items():
                if str(k).lower() in _RAW_SLC_KEY_MARKERS:
                    violations.append(f"forbidden raw-SLC key {k!r} at {path}")
                scan(v, f"{path}.{k}")
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                scan(v, f"{path}[{i}]")
        elif isinstance(node, str):
            if _RAW_SLC_VALUE_RE.search(node):
                violations.append(f"raw SLC scene path at {path}: {node!r}")

    scan(manifest, "manifest")
    return violations


def check_no_raw_slc_tree(root: str | Path) -> list[str]:
    """Scan a data root independently of its manifest contents.

    This catches an unlisted raw scene, which a manifest-only scan cannot see.
    Derived metadata files whose names merely contain ``S1`` are allowed; raw
    SAFE/SLC scene names and Sentinel-1 SLC archives are not.
    """
    root_p = Path(root)
    if not root_p.exists():
        return [f"raw SLC scan root does not exist: {root_p}"]
    hits: list[str] = []
    for path in sorted(root_p.rglob("*")):
        name = path.name
        upper = name.upper()
        raw_name = (
            ".SAFE" in upper or upper.endswith(".SLC") or
            (upper.endswith(".ZIP") and "S1" in upper and "SLC" in upper) or
            bool(_RAW_SLC_VALUE_RE.search(name))
        )
        if raw_name:
            hits.append(f"raw SLC path found under root: {path}")
    return hits


def assert_accepted_manifest(manifest: Mapping[str, Any]) -> None:
    """Raise ValueError if a manifest may not be accepted by A/B/E pipelines."""
    if not isinstance(manifest, Mapping):
        raise ValueError("manifest must be a mapping")
    slc = check_no_raw_slc_paths(manifest)
    if slc:
        raise ValueError("manifest contains raw SLC references: "
                         + "; ".join(slc[:3]))
    raw_statuses = manifest.get("statuses", [])
    if raw_statuses is None:
        raw_statuses = []
    if isinstance(raw_statuses, (str, bytes)) or not isinstance(
            raw_statuses, (list, tuple, set, frozenset)):
        raise ValueError("manifest statuses must be a sequence")
    statuses = {s for s in raw_statuses if s is not None}
    unknown = statuses - set(OUTPUT_STATUSES)
    if unknown:
        raise ValueError(f"manifest carries non-contract statuses: {sorted(unknown)}")
