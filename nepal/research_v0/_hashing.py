"""Self-contained hashing helpers for the research_v0 namespace.

Deliberately duplicated from any frozen-package implementation so that
research_v0 has zero import dependency on ``nepal.framework_v1`` (G19
path isolation).  Hashing actual bytes is required for external science
artifacts; a self-hash is never external approval.

Canonical JSON is strict: only JSON-native types, UTF-8, sorted keys,
no non-finite numbers, no ``default=`` coercion — a digest must be
stable and reproducible in any conforming JSON implementation.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

_CHUNK = 1 << 20


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    """Hash exact file bytes.  Refuses missing paths and symlinks — a
    symlink target's bytes are not the artifact the caller bound."""
    p = Path(path)
    if p.is_symlink():
        raise ValueError(f"refusing to hash symlink: {p}")
    if not p.is_file():
        raise ValueError(f"not a regular file: {p}")
    digest = hashlib.sha256()
    with open(p, "rb") as handle:
        while True:
            chunk = handle.read(_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def hash_artifact(path: str | Path,
                  allowed_root: str | Path) -> dict[str, Any]:
    """Hash one artifact with root containment (B22).

    The path must resolve to a regular, non-symlink file inside
    ``allowed_root``.  Returns ``{relpath, size_bytes, sha256}`` — the
    digest is computed from the same open stream, so the hashed bytes
    are the consumed bytes.
    """
    p = Path(path)
    root = Path(allowed_root)
    try:
        resolved = p.resolve()
        root_resolved = root.resolve()
    except OSError as exc:
        raise ValueError(f"cannot resolve paths: {exc}") from exc
    try:
        relpath = resolved.relative_to(root_resolved)
    except ValueError:
        raise ValueError(
            f"{resolved} is outside allowed root {root_resolved}") \
            from None
    if resolved.is_symlink():
        raise ValueError(f"refusing to hash symlink: {resolved}")
    if not resolved.is_file():
        raise ValueError(f"not a regular file: {resolved}")
    digest = hashlib.sha256()
    size = 0
    with open(resolved, "rb") as handle:
        while True:
            chunk = handle.read(_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return {"relpath": str(relpath), "size_bytes": size,
            "sha256": digest.hexdigest()}


def _reject_nonjson(payload: Any, path: str = "$") -> None:
    if isinstance(payload, bool) or payload is None or \
            isinstance(payload, (str, int)):
        return
    if isinstance(payload, float):
        import math
        if not math.isfinite(payload):
            raise ValueError(f"non-finite number at {path}")
        return
    if isinstance(payload, (list, tuple)):
        for i, item in enumerate(payload):
            _reject_nonjson(item, f"{path}[{i}]")
        return
    if isinstance(payload, dict):
        for key, item in payload.items():
            if not isinstance(key, str):
                raise ValueError(f"non-string key at {path}: {key!r}")
            _reject_nonjson(item, f"{path}.{key}")
        return
    raise TypeError(
        f"unsupported type at {path}: {type(payload).__name__} — "
        "canonical JSON accepts JSON-native types only")


def canonical_json(payload: Any) -> str:
    """Deterministic strict JSON serialization for hashing and storage."""
    _reject_nonjson(payload)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)


def sha256_canonical(payload: Any) -> str:
    return sha256_bytes(canonical_json(payload).encode("utf-8"))
