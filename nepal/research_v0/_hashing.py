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


def _check_evidence_root(root_raw: Any,
                         ) -> tuple[Path | None, list[str]]:
    """The declared-evidence-root floor (R10-P08).

    Policy: *no symlink anywhere beneath-and-including the declared
    path*.  ``root_raw`` must be a non-empty string whose path
    lstat-resolves to a real directory — the declared path's own leaf
    component may not be a symlink — and every path component between
    the root and an evidence leaf is lstat-checked by
    ``_resolve_evidence_leaf`` before any byte is read.

    Residual (documented): ancestor components ABOVE the declared
    root are canonicalized by ``resolve()`` and are out of policy
    scope — host-level conveniences like macOS's ``/var ->
    /private/var`` must stay admissible or no temporary evidence
    directory could ever verify.  A mid-path symlink inside the
    declared root string is likewise normalized by ``resolve()``;
    the byte binding is anchored at the resolved real directory.
    Returns ``(resolved_root, problems)`` — ``resolved_root`` is
    ``None`` exactly when problems is non-empty.  Never raises.
    """
    if not isinstance(root_raw, str) or not root_raw.strip():
        return None, ["evidence_root must be a non-empty string "
                      "naming a directory"]
    root = Path(root_raw)
    try:
        st = root.lstat()
    except OSError:
        return None, [f"evidence_root {root} is not a directory"]
    if stat.S_ISLNK(st.st_mode):
        return None, [f"evidence_root {root} is a symlink — the "
                      "declared evidence path itself must be real"]
    if not stat.S_ISDIR(st.st_mode):
        return None, [f"evidence_root {root} is not a directory"]
    try:
        root_resolved = root.resolve()
    except OSError as exc:
        return None, [f"cannot resolve evidence_root {root}: {exc}"]
    return root_resolved, []


def _resolve_evidence_leaf(
        root_resolved: Path, rel: Any, label: str,
        ) -> tuple[Path | None, str | None]:
    """Resolve one declared relpath to a verified real leaf file.

    Returns ``(leaf_path, None)`` on success or ``(None, problem)``
    on any violation.  Fail-closed contract (R10-P08): the relpath
    must be a non-empty relative path with no ``..`` escape; EVERY
    path component from ``root_resolved`` down to the leaf is
    lstat-checked — intermediate components must be real directories
    (never symlinks), the leaf a real regular file (never a symlink);
    the resolved leaf must stay inside the resolved root; and the
    leaf's pre/post-read identity is pinned by
    ``_sha256_evidence_file``.

    TOCTOU residual (documented): fd-level ``O_NOFOLLOW``/``openat2``
    hardening needs ``os.open``, which the research_v0 isolation
    policy forbids — the component-walk + leaf identity-pin is the
    enforceable equivalent: a swapped intermediate directory between
    the walk and the read can still race the open, but the leaf
    itself cannot be swapped or rewritten without the post-read
    signature catching it.  Never raises.
    """
    if not isinstance(rel, str) or not rel.strip():
        return None, f"{label} must be a non-empty relative path"
    rel_path = Path(rel)
    if rel_path.is_absolute():
        return None, (f"{label} path {rel!r} is absolute — it must "
                      "resolve inside evidence_root")
    parts = rel_path.parts
    if not parts or any(part == ".." for part in parts):
        return None, (f"{label} path {rel!r} resolves outside "
                      "evidence_root — '..' components are not "
                      "admissible")
    # Component walk: lstat EVERY intermediate component under the
    # resolved root — a symlinked directory in the declared path is
    # rejected before the leaf is ever opened.
    acc = root_resolved
    for part in parts[:-1]:
        acc = acc / part
        try:
            st = acc.lstat()
        except OSError as exc:
            return None, (f"{label} path {rel!r}: intermediate "
                          f"component {acc} cannot be stat'd: {exc}")
        if stat.S_ISLNK(st.st_mode):
            return None, (f"{label} path {rel!r}: component {acc} "
                          "is a symlink — no symlink is admissible "
                          "beneath the declared evidence_root")
        if not stat.S_ISDIR(st.st_mode):
            return None, (f"{label} path {rel!r}: component {acc} "
                          "is not a directory")
    leaf = root_resolved.joinpath(*parts)
    try:
        st = leaf.lstat()
    except OSError as exc:
        return None, (f"{label} path {rel!r}: leaf cannot be "
                      f"stat'd: {exc}")
    if stat.S_ISLNK(st.st_mode):
        return None, (f"{label} path {rel!r} is a symlink — "
                      "evidence files must be real files")
    if not stat.S_ISREG(st.st_mode):
        return None, (f"{label} path {rel!r} is not a regular file")
    try:
        resolved = leaf.resolve()
    except OSError as exc:
        return None, f"{label} path {rel!r} cannot be resolved: {exc}"
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        return None, (f"{label} path {rel!r} resolves outside "
                      "evidence_root")
    return leaf, None


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


def read_evidence_file(root_resolved: Path, rel: str,
                       *, label: str = "evidence") -> bytes:
    """Read one declared evidence leaf's exact bytes, fail-closed.

    The single safe byte-read path for evidence consumers (R11.1):
    the relpath resolves through ``_resolve_evidence_leaf`` — every
    intermediate component is lstat-checked as a real directory, the
    leaf must be a real regular file inside ``root_resolved``, and
    the (inode, size, mtime_ns) signature is pinned across the read
    so a swapped or rewritten file is rejected rather than bound.
    Callers MUST pass the bytes this returns to their parser — no
    second ``open``/``read_bytes`` on the path.  Raises ``ValueError``.
    """
    leaf, problem = _resolve_evidence_leaf(root_resolved, rel,
                                         label=label)
    if problem is not None:
        raise ValueError(problem)
    try:
        st0 = leaf.lstat()
    except OSError as exc:
        raise ValueError(f"{label} path {rel!r}: leaf cannot be "
                         f"stat'd: {exc}")
    if not stat.S_ISREG(st0.st_mode):
        raise ValueError(f"{label} path {rel!r} is not a regular "
                         "file")
    try:
        data = leaf.read_bytes()
    except OSError as exc:
        raise ValueError(f"{label} path {rel!r} cannot be read: "
                         f"{exc}")
    try:
        st1 = leaf.lstat()
    except OSError as exc:
        raise ValueError(f"{label} path {rel!r} disappeared during "
                         f"reading: {exc}")
    if _stat_signature(st1) != _stat_signature(st0):
        raise ValueError(
            f"{label} path {rel!r} changed during reading — a "
            "moving target is never a bound artifact")
    return data


def verify_source_evidence(manifest: Any) -> list[str]:
    """Byte-verify a non-fixture source manifest's evidence binding.

    Fail-closed contract for governed inputs (PROV-03):
    ``evidence_root`` must be a non-empty string naming a directory
    that exists on disk (and is not itself a symlink); ``source_files``
    must be a non-empty list of ``{relpath, sha256}`` entries; every
    relpath must resolve INSIDE the root (absolute paths and ``..``
    escapes reject), name a real non-symlink file with no symlink at
    ANY intermediate path component beneath the root (R10-P08
    component-walk), and hash to its declared digest; and the
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
    root_resolved, root_problems = _check_evidence_root(
        manifest.get("evidence_root"))
    if root_problems:
        return root_problems
    files = manifest.get("source_files")
    if not isinstance(files, (list, tuple)) or not files:
        return ["source_files must be a non-empty list of "
                "{relpath, sha256} bindings"]
    problems: list[str] = []
    verified: list[str] = []
    seen_relpaths: set = set()
    for i, entry in enumerate(files):
        if not isinstance(entry, Mapping):
            problems.append(f"source_files[{i}] is not a "
                            "{relpath, sha256} mapping")
            continue
        # R10-P11: the evidence binding record is exact — only
        # {relpath, sha256} may be declared.
        extra = sorted(set(entry) - {"relpath", "sha256"})
        if extra:
            problems.append(f"source_files[{i}] carries undeclared "
                            f"fields {extra} — the evidence binding "
                            "record admits exactly {relpath, sha256}")
        rel = entry.get("relpath")
        declared = entry.get("sha256")
        if isinstance(rel, str) and rel.strip():
            if rel in seen_relpaths:
                problems.append(
                    f"source_files[{i}] relpath {rel!r} is "
                    "duplicated — one evidence binding per file")
                continue
            seen_relpaths.add(rel)
        if not isinstance(declared, str) or \
                not _SHA256_RE.match(declared):
            problems.append(f"source_files[{i}].sha256 must be a "
                            "64-hex sha256")
            continue
        # R10-P08: component-walked resolution — no symlink at any
        # component beneath the declared root, leaf pinned by the
        # pre/post-read identity check inside _sha256_evidence_file.
        leaf, prob = _resolve_evidence_leaf(
            root_resolved, rel, f"source_files[{i}] relpath")
        if prob is not None:
            problems.append(prob)
            continue
        try:
            actual = _sha256_evidence_file(leaf)
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
    directory that exists on disk (and is not itself a symlink);
    ``archive_payload_path`` and ``retrieval_record_path`` must be
    non-empty relative paths that resolve INSIDE the root (absolute
    paths, ``..`` escapes, and symlinks all reject — every path
    component beneath the root is lstat-checked, so a symlinked
    intermediate directory rejects too, R10-P08), name real files,
    and hash to the declared ``archive_payload_sha256`` /
    ``retrieval_record_sha256``.

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
    root_resolved, problems = _check_evidence_root(root_raw)
    if problems:
        return problems
    for label, path_field, digest_field in (
            ("archive_payload", "archive_payload_path",
             "archive_payload_sha256"),
            ("retrieval_record", "retrieval_record_path",
             "retrieval_record_sha256")):
        rel = _field(path_field)
        declared = _field(digest_field)
        if not isinstance(declared, str) or \
                not _SHA256_RE.match(declared):
            problems.append(f"{digest_field} must be a 64-hex sha256")
            continue
        # R10-P08: component-walked resolution — no symlink at any
        # component beneath the declared root, leaf pinned by the
        # pre/post-read identity check inside _sha256_evidence_file.
        leaf, prob = _resolve_evidence_leaf(
            root_resolved, rel, f"{label} path")
        if prob is not None:
            problems.append(prob)
            continue
        try:
            # R9-P03 parity: stat-pinned hashing — the evidence file
            # must be a stable regular file across the whole read.
            actual = _sha256_evidence_file(leaf)
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
