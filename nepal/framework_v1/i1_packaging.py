"""I1 additive successor packaging (N02).

The accepted v2 candidate carries two provenance defects that cannot be fixed
in place because the candidate is immutable:

* ``manifest.recovery_audit_artifact`` still references the historical v1
  recovery audit packet;
* the v2 audit packet declares ``superseded_by: provenance/
  d1_recovery_audit.json`` — an inverted arrow (v2 *supersedes* v1, it is not
  superseded by it).

I1 closes these additively.  A *successor manifest* copies the v2 manifest
metadata byte-for-byte except for the identity fields: a new
``candidate_generation_id`` (``-i1-``), ``successor_of_*`` parent binding to
the v2 manifest digests, a corrected ``recovery_audit_artifact`` that points at
the current v2 packet, and an ``i1_audit_binding`` locator for the corrected
audit-binding envelope.  All artifact entries, counts, package inventory,
contracts, waivers, and semantic declarations are preserved verbatim, so the
successor verifies against the unchanged v2 artifact root via the existing
``verify_input_manifest`` loader (``manifest_path`` and artifact root are
already separate parameters).

The *I1 audit-binding envelope* is the corrected chain record: it names itself
``ACTIVE_I1``, the v2 packet ``PARENT_V2`` (current), and the v1 packet
``HISTORICAL_V1``.  Its ``candidate_generation_id``/``manifest_sha256`` bind
the I1 generation and successor manifest, so it can serve as the explicit
active audit packet for strict lineage on the I1 generation.  It records, but
does not propagate, the v2 packet's inverted ``superseded_by`` field.

Raw file digests and canonical/envelope self-hashes are kept in distinctly
named fields (``*_file_sha256`` vs ``manifest_sha256``/``artifact_sha256``) so
the two hash domains can never be silently compared (N01).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from .input_manifest import (_validate_package_inventory_files,
                             canonical_input_manifest_hash)
from .provenance import (bind_artifact_envelope, sha256_file,
                         verify_artifact_envelope)

I1_AUDIT_BINDING_TYPE = "I1_AUDIT_BINDING_V1"
SUCCESSOR_MANIFEST_NOTE = "SUCCESSOR_MANIFEST_V1"

AUDIT_ROLE_ACTIVE = "ACTIVE_I1"
AUDIT_ROLE_PARENT = "PARENT_V2"
AUDIT_ROLE_HISTORICAL = "HISTORICAL_V1"

_SHA256_RE = re.compile(r"[0-9a-f]{64}")

#: Manifest fields the successor rewrites.  Everything else is copied
#: verbatim so the v2 artifact root stays authoritative.
_SUCCESSOR_REWRITTEN_FIELDS = frozenset({
    "candidate_generation_id",
    "code_revision",
    "generated_at",
    "manifest_sha256",
    "recovery_audit_artifact",
    "successor_of_generation_id",
    "successor_of_manifest_declared_sha256",
    "successor_of_manifest_file_sha256",
    "i1_audit_binding",
    "packaging_note",
})


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))


def _iter_strings(obj: Any, prefix: str = ""):
    if isinstance(obj, str):
        yield prefix, obj
    elif isinstance(obj, Mapping):
        for key, value in obj.items():
            yield from _iter_strings(value, f"{prefix}{key}.")
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from _iter_strings(value, f"{prefix}[{index}].")


def _check_relative_path(value: Any, label: str, problems: list[str],
                         *, allow_run_root_relative: bool = False) -> None:
    """Reject missing, absolute, or traversing path fields."""
    if not isinstance(value, str) or not value:
        problems.append(f"{label} must be a non-empty relative path")
        return
    if value.startswith("/"):
        problems.append(f"{label} must not be an absolute path")
        return
    if ".." in Path(value).parts:
        problems.append(f"{label} must not traverse outside its root")


def build_successor_manifest(*, parent_manifest: Mapping[str, Any],
                             generation_id: str,
                             parent_generation_id: str,
                             parent_manifest_sha256: str,
                             parent_manifest_file_sha256: str,
                             active_audit_packet: Mapping[str, Any],
                             i1_audit_binding: Mapping[str, Any],
                             code_revision: str,
                             generated_at: Optional[str] = None
                             ) -> dict[str, Any]:
    """Build the additive I1 successor manifest.

    ``active_audit_packet`` is the *current* audit binding — the v2 packet —
    expressed as ``{relative_path, sha256}`` relative to the unchanged v2
    artifact root so the field stays resolvable.  ``i1_audit_binding`` locates
    the corrected I1 binding envelope as ``{run_root_relative_path, sha256}``;
    the field name documents that it is run-root-relative, not
    artifact-root-relative.

    Only identity fields change; every data-bearing field is copied verbatim
    so ``verify_input_manifest`` succeeds against the v2 artifact root.
    """
    if not isinstance(parent_manifest, Mapping):
        raise ValueError("parent_manifest must be a mapping")
    if not generation_id:
        raise ValueError("generation_id must be a non-empty string")
    if not _is_sha256(parent_manifest_sha256):
        raise ValueError("parent_manifest_sha256 must be a lowercase SHA-256")
    if not _is_sha256(parent_manifest_file_sha256):
        raise ValueError(
            "parent_manifest_file_sha256 must be a lowercase SHA-256")
    for label, ref in (("active_audit_packet", active_audit_packet),
                       ("i1_audit_binding", i1_audit_binding)):
        if not isinstance(ref, Mapping) or not _is_sha256(ref.get("sha256")):
            raise ValueError(f"{label} requires a sha256 digest")

    successor = dict(parent_manifest)
    successor["candidate_generation_id"] = generation_id
    successor["successor_of_generation_id"] = parent_generation_id
    successor["successor_of_manifest_declared_sha256"] = (
        parent_manifest_sha256)
    successor["successor_of_manifest_file_sha256"] = (
        parent_manifest_file_sha256)
    successor["recovery_audit_artifact"] = dict(active_audit_packet)
    successor["i1_audit_binding"] = dict(i1_audit_binding)
    successor["code_revision"] = code_revision
    successor["generated_at"] = (generated_at
                                or datetime.now(timezone.utc).isoformat())
    successor["packaging_note"] = (
        f"{SUCCESSOR_MANIFEST_NOTE}: additive metadata successor over the "
        "immutable v2 artifact root; audit reference corrected from the "
        "historical v1 packet to the current v2 packet; see "
        "i1_audit_binding for the corrected supersession chain")
    successor["manifest_sha256"] = canonical_input_manifest_hash(successor)
    return successor


def verify_successor_manifest(
        payload: Any, *,
        expected_generation_id: str,
        parent_manifest_sha256: str,
        parent_manifest_file_sha256: str,
        artifact_root: Optional[str | Path] = None,
        ) -> tuple[bool, list[str]]:
    """Verify an I1 successor manifest.

    Re-checks the canonical self-hash, the new generation identity, the
    parent digest binding, relative-path safety, and — when ``artifact_root``
    is supplied — re-hashes every inventoried package file against the
    unchanged parent artifact root (detecting any parent byte change).
    """
    problems: list[str] = []
    if not isinstance(payload, Mapping):
        return False, ["successor manifest must be a mapping"]

    declared = payload.get("manifest_sha256")
    if not _is_sha256(declared):
        problems.append("successor manifest_sha256 must be a lowercase "
                        "SHA-256")
    elif canonical_input_manifest_hash(payload) != declared:
        problems.append("successor manifest canonical self-hash mismatch")

    if payload.get("candidate_generation_id") != expected_generation_id:
        problems.append(
            f"successor candidate_generation_id "
            f"{payload.get('candidate_generation_id')!r} does not match "
            f"expected generation {expected_generation_id!r}")
    if (payload.get("successor_of_manifest_declared_sha256")
            != parent_manifest_sha256):
        problems.append("successor_of_manifest_declared_sha256 does not "
                        "match the parent manifest canonical hash")
    if (payload.get("successor_of_manifest_file_sha256")
            != parent_manifest_file_sha256):
        problems.append("successor_of_manifest_file_sha256 does not match "
                        "the parent manifest file digest")
    if payload.get("successor_of_generation_id") in (None, "",
                                                   expected_generation_id):
        problems.append("successor_of_generation_id must name a distinct "
                        "parent generation")

    audit_ref = payload.get("recovery_audit_artifact")
    if not isinstance(audit_ref, Mapping):
        problems.append("recovery_audit_artifact must be a mapping")
    else:
        _check_relative_path(audit_ref.get("relative_path"),
                             "recovery_audit_artifact.relative_path",
                             problems)
        if not _is_sha256(audit_ref.get("sha256")):
            problems.append("recovery_audit_artifact.sha256 must be a "
                            "lowercase SHA-256")
        if (isinstance(audit_ref.get("relative_path"), str)
                and "d1_recovery_audit" in audit_ref["relative_path"]):
            problems.append(
                "recovery_audit_artifact still points at the historical v1 "
                "recovery audit packet")
    binding_ref = payload.get("i1_audit_binding")
    if not isinstance(binding_ref, Mapping):
        problems.append("i1_audit_binding must be a mapping")
    else:
        _check_relative_path(
            binding_ref.get("run_root_relative_path"),
            "i1_audit_binding.run_root_relative_path", problems)
        if not _is_sha256(binding_ref.get("sha256")):
            problems.append("i1_audit_binding.sha256 must be a lowercase "
                            "SHA-256")

    if not isinstance(payload.get("artifacts"), list) or not payload.get(
            "artifacts"):
        problems.append("successor manifest must preserve a non-empty "
                        "artifacts list")
    if not isinstance(payload.get("package_inventory"), list) or not \
            payload.get("package_inventory"):
        problems.append("successor manifest must preserve a non-empty "
                        "package_inventory")

    # Canonical fields are relative/diagnostic-free; machine-specific
    # absolute paths are never authoritative here.
    for dotted, value in _iter_strings(
            {k: v for k, v in payload.items() if k != "manifest_sha256"}):
        if value.startswith("/"):
            problems.append(f"absolute path in canonical successor field "
                            f"{dotted!r}")
            break

    if artifact_root is not None:
        problems.extend(_validate_package_inventory_files(
            Path(artifact_root), payload))
    return (not problems), problems


def build_i1_audit_binding(*, generation_id: str,
                           manifest_sha256: str,
                           manifest_file_sha256: str,
                           parent_v2: Mapping[str, Any],
                           historical_v1: Mapping[str, Any],
                           code_revision: str,
                           created_at: Optional[str] = None
                           ) -> dict[str, Any]:
    """Build the corrected I1 audit-binding envelope.

    ``parent_v2`` and ``historical_v1`` each carry
    ``run_root_relative_path``, ``file_sha256`` and
    ``candidate_generation_id``; ``parent_v2`` additionally carries the v2
    manifest canonical hash.  The envelope's ``candidate_generation_id`` /
    ``manifest_sha256`` bind the I1 generation and successor manifest so it
    can act as the explicit active audit packet in strict lineage.
    """
    for label, ref in (("parent_v2", parent_v2),
                       ("historical_v1", historical_v1)):
        if not isinstance(ref, Mapping):
            raise ValueError(f"{label} must be a mapping")
        if not _is_sha256(ref.get("file_sha256")):
            raise ValueError(f"{label}.file_sha256 must be a lowercase "
                             "SHA-256")
        if not isinstance(ref.get("run_root_relative_path"), str) or not \
                ref.get("run_root_relative_path"):
            raise ValueError(f"{label}.run_root_relative_path is required")
    envelope: dict[str, Any] = {
        "receipt_type": I1_AUDIT_BINDING_TYPE,
        "candidate_generation_id": generation_id,
        "manifest_sha256": manifest_sha256,
        "manifest_file_sha256": manifest_file_sha256,
        "code_revision": code_revision,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "audit_chain": {
            "active": {
                "role": AUDIT_ROLE_ACTIVE,
                "description":
                    "this I1 audit-binding envelope; the sole active packet "
                    "for the I1 generation"},
            "parent": {
                "role": AUDIT_ROLE_PARENT,
                "run_root_relative_path":
                    parent_v2["run_root_relative_path"],
                "file_sha256": parent_v2["file_sha256"],
                "candidate_generation_id":
                    parent_v2["candidate_generation_id"],
                "manifest_sha256": parent_v2.get("manifest_sha256"),
            },
            "historical": {
                "role": AUDIT_ROLE_HISTORICAL,
                "run_root_relative_path":
                    historical_v1["run_root_relative_path"],
                "file_sha256": historical_v1["file_sha256"],
                "candidate_generation_id":
                    historical_v1["candidate_generation_id"],
            },
        },
        "supersedes": {
            "candidate_generation_id": parent_v2["candidate_generation_id"],
            "run_root_relative_path": parent_v2["run_root_relative_path"],
            "file_sha256": parent_v2["file_sha256"],
        },
        "correction": (
            "the v2 audit packet's superseded_by -> "
            "provenance/d1_recovery_audit.json is inverted; the correct "
            "order is I1 (active) -> v2 (current parent) -> v1 (historical "
            "predecessor).  v2 bytes are immutable; this envelope records "
            "the correction additively."),
        "no_claims": [
            "No scientific validation",
            "No warning, production, or authority readiness",
            "Additive packaging correction only; no B ranking rerun",
        ],
    }
    return bind_artifact_envelope(envelope)


def verify_i1_audit_binding(
        payload: Any, *,
        expected_generation_id: Optional[str] = None,
        expected_manifest_sha256: Optional[str] = None,
        run_root: Optional[str | Path] = None
        ) -> tuple[bool, list[str]]:
    """Verify an I1 audit-binding envelope end to end.

    Checks the envelope self-hash, generation/manifest binding (so it can
    serve as the strict-lineage active packet), role labels, forward-only
    supersession arrows, and — when ``run_root`` is supplied — the recorded
    file digests of the parent and historical packets on disk.
    """
    problems: list[str] = []
    ok, envelope_problems = verify_artifact_envelope(payload)
    problems.extend(envelope_problems)
    if not isinstance(payload, Mapping):
        problems.append("audit binding must be a mapping")
        return False, problems
    if payload.get("receipt_type") != I1_AUDIT_BINDING_TYPE:
        problems.append(f"receipt_type must be {I1_AUDIT_BINDING_TYPE!r}")

    if expected_generation_id is not None and \
            payload.get("candidate_generation_id") != \
            expected_generation_id:
        problems.append(
            f"audit binding candidate_generation_id "
            f"{payload.get('candidate_generation_id')!r} does not match "
            f"{expected_generation_id!r}; the packet is historical/stale "
            "or bound to a different generation")
    if expected_manifest_sha256 is not None and \
            payload.get("manifest_sha256") != expected_manifest_sha256:
        problems.append(
            "audit binding manifest_sha256 does not match the expected "
            "successor manifest hash")

    # Forward-only supersession: an I1 binding must never be superseded by
    # the historical v1 packet (the v2 packet's inverted arrow, corrected).
    superseded_by = payload.get("superseded_by")
    if superseded_by is not None:
        problems.append(
            "audit binding must not declare superseded_by; inverted "
            "supersession arrows are rejected")

    chain = payload.get("audit_chain")
    if not isinstance(chain, Mapping):
        problems.append("audit_chain must be a mapping")
        chain = {}
    for name, role in (("active", AUDIT_ROLE_ACTIVE),
                       ("parent", AUDIT_ROLE_PARENT),
                       ("historical", AUDIT_ROLE_HISTORICAL)):
        node = chain.get(name)
        if not isinstance(node, Mapping):
            problems.append(f"audit_chain.{name} must be a mapping")
            continue
        if node.get("role") != role:
            problems.append(f"audit_chain.{name}.role must be {role!r}")
        if name != "active":
            _check_relative_path(node.get("run_root_relative_path"),
                                 f"audit_chain.{name}.run_root_relative_path",
                                 problems)
            if not _is_sha256(node.get("file_sha256")):
                problems.append(f"audit_chain.{name}.file_sha256 must be a "
                                "lowercase SHA-256")
    parent: Mapping[str, Any] = {}
    historical: Mapping[str, Any] = {}
    if isinstance(chain.get("parent"), Mapping):
        parent = chain["parent"]
    if isinstance(chain.get("historical"), Mapping):
        historical = chain["historical"]
    parent_gen = parent.get("candidate_generation_id")
    hist_gen = historical.get("candidate_generation_id")
    if isinstance(parent_gen, str) and isinstance(hist_gen, str) \
            and parent_gen == hist_gen:
        problems.append("parent and historical packets must be distinct "
                        "generations")
    if isinstance(hist_gen, str) and isinstance(parent_gen, str) \
            and hist_gen > parent_gen:
        problems.append("historical packet generation sorts after the "
                        "parent — supersession order inverted")

    supersedes = payload.get("supersedes")
    if not isinstance(supersedes, Mapping) or supersedes.get(
            "candidate_generation_id") != parent_gen:
        problems.append("supersedes must name the parent v2 packet")

    if run_root is not None:
        root = Path(run_root)
        for name in ("parent", "historical"):
            node = chain.get(name)
            if not isinstance(node, Mapping):
                continue
            rel = node.get("run_root_relative_path")
            expected = node.get("file_sha256")
            if not isinstance(rel, str) or not _is_sha256(expected):
                continue
            target = root / rel
            if not target.is_file():
                problems.append(f"audit_chain.{name} packet missing on "
                                f"disk: {rel}")
                continue
            actual = sha256_file(target)
            if actual != expected:
                problems.append(
                    f"audit_chain.{name} packet checksum mismatch: {rel} "
                    f"({expected} -> {actual})")
    return (not problems), problems


def build_i1_package_inventory(package_root: str | Path) -> list[dict]:
    """Record every file under the I1 package directory.

    Returns ``[{relative_path, bytes, sha256}]`` entries sorted by path —
    the package-level inventory of new I1 files, distinct from the
    successor manifest's preserved ``package_inventory`` (which describes
    the unchanged v2 artifact root).
    """
    root = Path(package_root)
    if not root.is_dir():
        raise ValueError(f"I1 package root missing: {root}")
    inventory = []
    for path in sorted(root.rglob("*"), key=lambda p: p.as_posix()):
        if not path.is_file() or path.is_symlink():
            continue
        inventory.append({
            "relative_path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    return inventory
