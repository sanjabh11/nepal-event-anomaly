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
import re
import stat
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_CHUNK = 1 << 20

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


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


def _stat_signature(st) -> tuple:
    """The identity tuple a TOCTOU re-check compares: inode, size,
    and nanosecond mtime — a swapped or rewritten file cannot keep
    all three."""
    return (st.st_ino, st.st_size, st.st_mtime_ns)


def _sha256_evidence_file(path: str | Path) -> str:
    """Hash evidence bytes with TOCTOU hardening (R9-P03).

    ``Path.lstat()`` (no-follow stat) runs BEFORE and AFTER the
    read: the path must be a regular file (a post-resolution symlink
    swap fails the mode check), and the (inode, size, mtime_ns)
    signature must be identical on both sides — evidence that
    changed mid-hash is rejected rather than bound.  ``lstat`` is
    the pathlib no-follow stat; ``os`` itself stays import-banned at
    the research_v0 isolation boundary.
    """
    p = Path(path)
    try:
        st0 = p.lstat()
    except OSError as exc:
        raise ValueError(f"cannot stat evidence file {p}: {exc}") \
            from exc
    if not stat.S_ISREG(st0.st_mode):
        raise ValueError(f"not a regular file: {p}")
    digest = hashlib.sha256()
    with open(p, "rb") as handle:
        while True:
            chunk = handle.read(_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    try:
        st1 = p.lstat()
    except OSError as exc:
        raise ValueError(
            f"evidence file {p} disappeared during hashing: {exc}") \
            from exc
    if _stat_signature(st1) != _stat_signature(st0):
        raise ValueError(
            f"evidence file {p} changed during hashing — a moving "
            "target is never a bound artifact")
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
    # Reject the symlink itself before resolution: an inside-root
    # symlink's target is not the artifact the caller named (C10).
    if p.is_symlink():
        raise ValueError(f"refusing to hash symlink: {p}")
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


def verify_source_evidence(manifest: Any) -> list[str]:
    """Byte-verify a non-fixture source manifest's evidence binding.

    Fail-closed contract for governed inputs (PROV-03):
    ``evidence_root`` must be a non-empty string naming a directory
    that exists on disk; ``source_files`` must be a non-empty list of
    ``{relpath, sha256}`` entries; every relpath must resolve INSIDE
    the root (absolute paths and ``..`` escapes reject), name a real
    non-symlink file, and hash to its declared digest; and the
    multiset of verified file digests must equal the manifest's
    ``source_digests``.  ``{"fixture": true}`` manifests carry no
    on-disk evidence by declaration and bypass byte verification.

    Returns human-readable problem strings — an empty list means
    verified.  Never raises.
    """
    if not isinstance(manifest, Mapping):
        return ["source manifest is not a mapping"]
    if manifest.get("fixture") is True:
        return []
    # R9-P01: only a strict boolean marker is schema-valid — a
    # truthy non-bool ("true", 1) is malformed, never a bypass; a
    # boolean False is simply a non-fixture manifest that must
    # byte-verify like any other.
    if "fixture" in manifest and \
            not isinstance(manifest["fixture"], bool):
        return [f"fixture must be a strict boolean to bypass or "
                f"decline byte verification — got "
                f"{manifest['fixture']!r}"]
    root_raw = manifest.get("evidence_root")
    if not isinstance(root_raw, str) or not root_raw.strip():
        return ["evidence_root must be a non-empty string naming a "
                "directory"]
    root = Path(root_raw)
    if not root.is_dir():
        return [f"evidence_root {root} is not a directory"]
    try:
        root_resolved = root.resolve()
    except OSError as exc:
        return [f"cannot resolve evidence_root {root}: {exc}"]
    files = manifest.get("source_files")
    if not isinstance(files, (list, tuple)) or not files:
        return ["source_files must be a non-empty list of "
                "{relpath, sha256} bindings"]
    problems: list[str] = []
    verified: list[str] = []
    for i, entry in enumerate(files):
        if not isinstance(entry, Mapping):
            problems.append(f"source_files[{i}] is not a "
                            "{relpath, sha256} mapping")
            continue
        rel = entry.get("relpath")
        declared = entry.get("sha256")
        if not isinstance(rel, str) or not rel.strip():
            problems.append(f"source_files[{i}].relpath must be a "
                            "non-empty relative path")
            continue
        if Path(rel).is_absolute():
            problems.append(f"source_files[{i}] relpath {rel!r} is "
                            "absolute — it must resolve inside "
                            "evidence_root")
            continue
        if not isinstance(declared, str) or \
                not _SHA256_RE.match(declared):
            problems.append(f"source_files[{i}].sha256 must be a "
                            "64-hex sha256")
            continue
        joined = root_resolved / rel
        # R9-P03: reject the symlink itself BEFORE resolution — an
        # inside-root symlink's target is not the artifact the
        # caller named, even when the target also lives inside the
        # root (parity with verify_vintage_evidence).
        if joined.is_symlink():
            problems.append(f"source_files[{i}] relpath {rel!r} is "
                            "a symlink — evidence files must be "
                            "real files")
            continue
        try:
            resolved = joined.resolve()
        except OSError as exc:
            problems.append(f"source_files[{i}] {rel!r} cannot be "
                            f"resolved: {exc}")
            continue
        try:
            resolved.relative_to(root_resolved)
        except ValueError:
            problems.append(f"source_files[{i}] relpath {rel!r} "
                            "resolves outside evidence_root")
            continue
        try:
            actual = _sha256_evidence_file(resolved)
        except ValueError as exc:
            problems.append(f"source_files[{i}] {rel!r}: {exc}")
            continue
        if actual != declared:
            problems.append(
                f"source_files[{i}] {rel!r}: sha256 mismatch — "
                f"declared {declared[:16]}… != file "
                f"{actual[:16]}…")
            continue
        verified.append(actual)
    declared_set = manifest.get("source_digests")
    if not isinstance(declared_set, (list, tuple)) or \
            not declared_set:
        problems.append("source_digests must be a non-empty list of "
                        "declared file digests")
    elif Counter(verified) != Counter(str(d) for d in declared_set):
        problems.append("source_digests does not equal the multiset "
                        "of verified source_files digests")
    return problems


def verify_vintage_evidence(vintage: Any,
                            evidence_root: Any = None) -> list[str]:
    """Byte-verify a forecast vintage's evidence binding (C14/E05).

    Fail-closed contract for an admitted ``ForecastVintageV0`` (or its
    serialized mapping): the evidence root — the ``evidence_root``
    argument, or the record's own ``evidence_root`` field when the
    argument is ``None`` — must be a non-empty string naming a
    directory that exists on disk; ``archive_payload_path`` and
    ``retrieval_record_path`` must be non-empty relative paths that
    resolve INSIDE the root (absolute paths, ``..`` escapes, and
    symlinks all reject — ``os.path.realpath`` of each file must stay
    inside the realpath of the root), name real files, and hash to the
    declared ``archive_payload_sha256`` / ``retrieval_record_sha256``.

    Returns human-readable problem strings — an empty list means both
    files exist inside the root and hash to the declared digests.
    Never raises.  A metadata-only candidate vintage
    (``evidence_root == ""``) is a problem here by construction: this
    is the byte-boundary check a forecast-ready claim must pass, not
    the metadata admission check — candidates are admitted without
    bytes via ``build_vintage`` and promoted only through this gate.
    """
    if not isinstance(vintage, Mapping) and not all(
            hasattr(vintage, name) for name in (
                "archive_payload_path", "archive_payload_sha256",
                "retrieval_record_path",
                "retrieval_record_sha256")):
        return ["vintage evidence payload is not a mapping or a "
                "ForecastVintageV0-like record"]

    def _field(name: str) -> Any:
        if isinstance(vintage, Mapping):
            return vintage.get(name)
        return getattr(vintage, name, None)

    root_raw = evidence_root if evidence_root is not None \
        else _field("evidence_root")
    if not isinstance(root_raw, str) or not root_raw.strip():
        return ["evidence_root must be a non-empty string naming a "
                "directory"]
    root = Path(root_raw)
    if not root.is_dir():
        return [f"evidence_root {root} is not a directory"]
    try:
        root_resolved = root.resolve()
    except OSError as exc:
        return [f"cannot resolve evidence_root {root}: {exc}"]
    problems: list[str] = []
    for label, path_field, digest_field in (
            ("archive_payload", "archive_payload_path",
             "archive_payload_sha256"),
            ("retrieval_record", "retrieval_record_path",
             "retrieval_record_sha256")):
        rel = _field(path_field)
        declared = _field(digest_field)
        if not isinstance(rel, str) or not rel.strip():
            problems.append(f"{path_field} must be a non-empty "
                            "relative path")
            continue
        if Path(rel).is_absolute():
            problems.append(f"{label} path {rel!r} is absolute — it "
                            "must resolve inside evidence_root")
            continue
        if not isinstance(declared, str) or \
                not _SHA256_RE.match(declared):
            problems.append(f"{digest_field} must be a 64-hex sha256")
            continue
        joined = root_resolved / rel
        # Reject the symlink itself before resolution: an inside-root
        # symlink's target is not the artifact the caller named.
        if joined.is_symlink():
            problems.append(f"{label} path {rel!r} is a symlink — "
                            "evidence files must be real files")
            continue
        try:
            resolved = joined.resolve()
        except OSError as exc:
            problems.append(f"{label} path {rel!r} cannot be "
                            f"resolved: {exc}")
            continue
        try:
            resolved.relative_to(root_resolved)
        except ValueError:
            problems.append(f"{label} path {rel!r} resolves outside "
                            "evidence_root")
            continue
        try:
            # R9-P03 parity: stat-pinned hashing — the evidence file
            # must be a stable regular file across the whole read.
            actual = _sha256_evidence_file(resolved)
        except ValueError as exc:
            problems.append(f"{label} path {rel!r}: {exc}")
            continue
        if actual != declared:
            problems.append(
                f"{label} path {rel!r}: sha256 mismatch — declared "
                f"{declared[:16]}… != file {actual[:16]}…")
    return problems


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
