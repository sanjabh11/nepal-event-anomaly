"""``RUN_EVIDENCE_MANIFEST_V0`` — typed role-binding wrapper (Phase-4 R1).

Implements the recommended shape recorded in
``docs/science/PHASE4_DUAL_MANIFEST_DECISION_V0.md``: the P5 scope binds
four DISTINCT byte-bound sources (event labels, opportunity frame,
reanalysis features, per-source sidecars), and the single-manifest
digest gate in ``run_glof_descriptive_poc`` cannot express them without
merging source IDs — which the Phase-4 instruction forbids.  This type
binds each role to its own seven-key non-fixture source manifest; it
never merges source identities and never replaces a role manifest.

Status (decision doc §4–§6): `RATIFIED` by owner directive
2026-09-19.  This type is the canonical implementation — re-exported
from ``nepal.research_v0.records`` — and the runner accepts the
optional ``run_evidence_manifest`` package key via
``run_evidence_binding_problems``; the single-manifest event gate is
unchanged and never bypassed.

Nothing in this module authorizes intake, freeze, clustering, or any
operational claim.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from ._hashing import sha256_canonical, verify_source_evidence
from .records import SHA256_RE

#: Serialized schema tag — the exact string the decision doc fixes.
RUN_EVIDENCE_MANIFEST_SCHEMA = "RUN_EVIDENCE_MANIFEST_V0"

#: Declared roles, in canonical order.  ``event`` and ``feature`` are
#: required (they are the two roles the proven defect involves);
#: ``opportunity`` and ``sidecar`` may be explicitly not-applicable.
RUN_EVIDENCE_ROLES = ("event", "opportunity", "feature", "sidecar")
_REQUIRED_ROLES = ("event", "feature")

#: The exact seven-key non-fixture source-manifest contract (REG-11 /
#: ``build_source_manifest``).  A fixture manifest ``{"fixture": true}``
#: is inadmissible as a role — it fails the exact-key test.
_SOURCE_MANIFEST_KEYS = frozenset({
    "source_id", "source_digests", "units", "feature_allowlist",
    "lineage", "evidence_root", "source_files"})


def _role_manifest_map(wrapper: "RunEvidenceManifestV0") -> dict:
    return {
        "event": wrapper.event_manifest,
        "opportunity": wrapper.opportunity_manifest,
        "feature": wrapper.feature_manifest,
        "sidecar": wrapper.sidecar_manifest,
    }


@dataclass(frozen=True)
class RunEvidenceManifestV0:
    """Binds per-role source manifests without merging source IDs.

    ``source_ids``, ``digests``, ``role_digests_digest`` and
    ``run_evidence_digest`` are derived at construction — a caller can
    never assert a digest the bytes do not produce.  Validation
    surfaces as ``problems()`` (structure) and ``verify_problems()``
    (per-role byte verification via the existing
    ``verify_source_evidence`` floor); a non-empty list means the
    wrapper is inadmissible.
    """

    schema: str = RUN_EVIDENCE_MANIFEST_SCHEMA
    event_manifest: Optional[Mapping[str, Any]] = None
    opportunity_manifest: Optional[Mapping[str, Any]] = None
    feature_manifest: Optional[Mapping[str, Any]] = None
    sidecar_manifest: Optional[Mapping[str, Any]] = None
    source_ids: dict[str, str] = field(init=False, default_factory=dict)
    digests: dict[str, str] = field(init=False, default_factory=dict)
    role_digests_digest: str = field(init=False, default="")
    run_evidence_digest: str = field(init=False, default="")

    def __post_init__(self) -> None:
        manifests = _role_manifest_map(self)
        source_ids = {
            role: m["source_id"]
            for role, m in manifests.items()
            if isinstance(m, Mapping)
            and isinstance(m.get("source_id"), str)
            and m["source_id"].strip()}
        digests = {
            role: sha256_canonical(dict(m))
            for role, m in manifests.items()
            if isinstance(m, Mapping)}
        role_digests_digest = sha256_canonical(digests)
        object.__setattr__(self, "source_ids", source_ids)
        object.__setattr__(self, "digests", digests)
        object.__setattr__(
            self, "role_digests_digest", role_digests_digest)
        object.__setattr__(
            self, "run_evidence_digest", sha256_canonical({
                "schema": self.schema,
                "source_ids": self.source_ids,
                "digests": self.digests,
                "role_digests_digest": self.role_digests_digest}))

    def problems(self) -> list[str]:
        problems: list[str] = []
        if self.schema != RUN_EVIDENCE_MANIFEST_SCHEMA:
            problems.append(
                f"schema {self.schema!r} must be "
                f"{RUN_EVIDENCE_MANIFEST_SCHEMA!r}")
        for role, manifest in _role_manifest_map(self).items():
            required = role in _REQUIRED_ROLES
            if manifest is None:
                if required:
                    problems.append(
                        f"{role}_manifest is required — the role may "
                        "not be declared not-applicable")
                continue
            if not isinstance(manifest, Mapping):
                problems.append(
                    f"{role}_manifest must be a seven-key source "
                    "manifest mapping")
                continue
            keys = set(manifest)
            if keys != _SOURCE_MANIFEST_KEYS:
                problems.append(
                    f"{role}_manifest declares keys {sorted(keys)} — "
                    f"expected the exact non-fixture seven-key set "
                    f"{sorted(_SOURCE_MANIFEST_KEYS)}")
            sid = manifest.get("source_id")
            if not isinstance(sid, str) or not sid.strip():
                problems.append(
                    f"{role}_manifest.source_id must be a non-empty "
                    "string")
        declared = [sid for sid in self.source_ids.values()]
        if len(declared) != len(set(declared)):
            problems.append(
                "role source_ids must be distinct — merging source "
                "identities is forbidden")
        for role, digest in self.digests.items():
            if not SHA256_RE.match(digest):
                problems.append(
                    f"digests[{role!r}] must be a 64-hex sha256")
        if self.run_evidence_digest in self.digests.values():
            problems.append(
                "run_evidence_digest must bind the role set as a "
                "whole — it may not coincide with a role digest")
        return problems

    def verify_problems(self) -> list[str]:
        """Byte-verify each admissible role against its own evidence —
        the existing ``verify_source_evidence`` floor, applied per role
        with the role name prefixed.  Roles that fail the structural
        ``problems()`` floor report there instead."""
        problems: list[str] = []
        for role, manifest in _role_manifest_map(self).items():
            if not isinstance(manifest, Mapping) or \
                    set(manifest) != _SOURCE_MANIFEST_KEYS:
                continue
            for problem in verify_source_evidence(manifest):
                problems.append(f"{role}: {problem}")
        return problems

    def to_dict(self) -> dict[str, Any]:
        manifests = _role_manifest_map(self)
        return {
            "schema": self.schema,
            "event_manifest": manifests["event"],
            "opportunity_manifest": manifests["opportunity"],
            "feature_manifest": manifests["feature"],
            "sidecar_manifest": manifests["sidecar"],
            "source_ids": dict(self.source_ids),
            "digests": dict(self.digests),
            "role_digests_digest": self.role_digests_digest,
            "run_evidence_digest": self.run_evidence_digest,
            "record_type": type(self).__name__,
        }


#: Every key a serialized wrapper may carry.  Derived fields are
#: accepted (the runner re-derives them) but never trusted.
_WRAPPER_KEYS = frozenset({
    "schema", "event_manifest", "opportunity_manifest",
    "feature_manifest", "sidecar_manifest", "source_ids", "digests",
    "role_digests_digest", "run_evidence_digest", "record_type"})

#: Derived fields — recomputed at construction, compared when declared.
_DERIVED_FIELDS = ("source_ids", "digests", "role_digests_digest",
                   "run_evidence_digest")


def wrapper_from_mapping(data: Any) -> "RunEvidenceManifestV0":
    """Rebuild a wrapper from its serialized (or typed) form.

    Only the ROLE MANIFESTS are read; ``source_ids``, ``digests``,
    ``role_digests_digest`` and ``run_evidence_digest`` are recomputed
    at construction, so a caller can never assert a digest the bytes do
    not produce.
    """
    if isinstance(data, RunEvidenceManifestV0):
        return data
    if not isinstance(data, Mapping):
        raise ValueError(
            "run_evidence_manifest must be a mapping or "
            "RunEvidenceManifestV0")
    undeclared = set(data) - _WRAPPER_KEYS
    if undeclared:
        raise ValueError(
            f"run_evidence_manifest carries undeclared keys "
            f"{sorted(undeclared)}")
    return RunEvidenceManifestV0(
        schema=data.get("schema", RUN_EVIDENCE_MANIFEST_SCHEMA),
        event_manifest=data.get("event_manifest"),
        opportunity_manifest=data.get("opportunity_manifest"),
        feature_manifest=data.get("feature_manifest"),
        sidecar_manifest=data.get("sidecar_manifest"))


def wrapper_declaration_problems(data: Any) -> list[str]:
    """A DECLARED derived field must equal the recomputed value."""
    if isinstance(data, RunEvidenceManifestV0):
        return []
    if not isinstance(data, Mapping):
        return ["run_evidence_manifest must be a mapping or "
                "RunEvidenceManifestV0"]
    try:
        rebuilt = wrapper_from_mapping(data)
    except (TypeError, ValueError) as exc:
        return [f"run_evidence_manifest inadmissible: {exc}"]
    problems: list[str] = []
    for name in _DERIVED_FIELDS:
        if name not in data:
            continue
        declared = data[name]
        actual = getattr(rebuilt, name)
        if declared != actual:
            problems.append(
                f"run_evidence_manifest.{name} is declared as "
                f"{declared!r} but the role manifests produce "
                f"{actual!r} — a caller may never assert a digest the "
                "bytes do not produce")
    return problems


def run_evidence_binding_problems(
        data: Any,
        cfg_event_manifest: Optional[Mapping[str, Any]] = None) -> list[str]:
    """Admit a wrapper against the fit's declared event role.

    The event role must be canonically IDENTICAL to
    ``regime_config.source_manifest`` (the event gate is checked against
    the event role exactly as before), and every other declared role is
    byte-verified through the existing ``verify_source_evidence`` floor.
    Source identities are never merged and no role manifest is
    rewritten — a non-empty list means the package is inadmissible.
    """
    if not isinstance(data, (RunEvidenceManifestV0, Mapping)):
        return ["event_package.run_evidence_manifest must be a mapping "
                "or RunEvidenceManifestV0"]
    problems = wrapper_declaration_problems(data)
    try:
        wrapper = wrapper_from_mapping(data)
    except (TypeError, ValueError) as exc:
        return problems + [f"run_evidence_manifest inadmissible: {exc}"]
    problems.extend(wrapper.problems())
    problems.extend(wrapper.verify_problems())
    if not isinstance(cfg_event_manifest, Mapping):
        problems.append(
            "run_evidence_manifest requires regime_config."
            "source_manifest — the event role has no declared fit")
        return problems
    event_role = wrapper.event_manifest
    if not isinstance(event_role, Mapping):
        problems.append(
            "run_evidence_manifest.event_manifest is required — the "
            "role may not be declared not-applicable")
        return problems
    try:
        event_digest = sha256_canonical(dict(event_role))
        cfg_digest = sha256_canonical(dict(cfg_event_manifest))
    except (TypeError, ValueError) as exc:
        return problems + [
            f"run_evidence_manifest event role cannot be canonically "
            f"digested: {exc}"]
    if event_digest != cfg_digest:
        problems.append(
            "run_evidence_manifest.event_manifest does not equal "
            "regime_config.source_manifest — the wrapper binds the "
            "event role, and the event DUTY must not be reassigned to "
            "another source")
    feature_role = wrapper.feature_manifest
    if not isinstance(feature_role, Mapping):
        problems.append(
            "run_evidence_manifest.feature_manifest is required while "
            "the declared scope binds a distinct reanalysis source")
        return problems
    event_sid = event_role.get("source_id")
    feature_sid = feature_role.get("source_id")
    if isinstance(event_sid, str) and isinstance(feature_sid, str) \
            and event_sid == feature_sid:
        problems.append(
            "run_evidence_manifest event/feature source_ids must stay "
            "distinct — merging source identities is forbidden")
    return problems


def semantic_binding_problems(
        wrapper: Any,
        package: Mapping[str, Any],
        *,
        frame_relpath: Optional[str] = None,
        frame_columns: Optional[Sequence[str]] = None,
        carrier_columns: Sequence[str] = (),
        fmx_report: Optional[Mapping[str, Any]] = None,
        control_doc_relpaths: Sequence[str] = ()) -> list[str]:
    """Prove the wrapper's roles actually produced what was consumed.

    ``run_evidence_binding_problems`` verifies role *structure and
    bytes*; this gate binds role CONTENT to the derived artifacts the
    run consumed — a role that verifies byte-wise but is unrelated to
    the package/frame/report under audit fails here (R11.9-08):

    - ``package.source_manifest_digest`` must equal the canonical
      digest of the event role (the event DUTY is bound, not copied);
    - ``frame_relpath`` must be a declared ``source_files`` member of
      the feature role;
    - every non-carrier frame column must be inside the feature
      role's ``feature_allowlist`` — an undeclared column can never
      ride a verified role;
    - ``fmx_report.feature_role_digest`` must equal the feature role
      digest and ``fmx_report.frame_sha256`` must equal the digest of
      the declared frame member;
    - every declared control document must be a ``source_files``
      member of the sidecar role — an unbound control document can
      never be consumed silently.
    """
    problems: list[str] = []
    try:
        w = wrapper_from_mapping(wrapper)
    except (TypeError, ValueError) as exc:
        return [f"semantic binding inadmissible: {exc}"]
    if not isinstance(package, Mapping):
        return ["package must be a mapping for semantic binding"]

    event_role = w.event_manifest
    feature_role = w.feature_manifest
    sidecar_role = w.sidecar_manifest

    if isinstance(event_role, Mapping):
        declared = package.get("source_manifest_digest")
        actual = sha256_canonical(dict(event_role))
        if declared != actual:
            problems.append(
                "package.source_manifest_digest does not equal the "
                "event role digest — the package may not bind an "
                "event source other than the declared role")
    else:
        problems.append("semantic binding requires the event role")

    if isinstance(feature_role, Mapping):
        members = {f.get("relpath"): f.get("sha256")
                   for f in feature_role.get("source_files", [])
                   if isinstance(f, Mapping)}
        if frame_relpath is not None and \
                frame_relpath not in members:
            problems.append(
                f"feature frame {frame_relpath!r} is not a declared "
                "feature-role member")
        if frame_columns is not None:
            allowlist = set(feature_role.get("feature_allowlist") or
                            ()) | set(carrier_columns)
            undeclared = sorted(set(frame_columns) - allowlist)
            if undeclared:
                problems.append(
                    f"frame columns {undeclared} are outside the "
                    "feature role's declared allowlist/carriers")
        if isinstance(fmx_report, Mapping):
            role_digest = sha256_canonical(dict(feature_role))
            if fmx_report.get("feature_role_digest") != role_digest:
                problems.append(
                    "fmx_report.feature_role_digest does not equal "
                    "the feature role digest — the report may not "
                    "audit a different source")
            if frame_relpath is not None and \
                    frame_relpath in members and \
                    fmx_report.get("frame_sha256") != \
                    members[frame_relpath]:
                problems.append(
                    "fmx_report.frame_sha256 does not equal the "
                    "declared frame member digest")
    elif frame_relpath is not None or fmx_report is not None:
        problems.append(
            "semantic binding of a frame/FMX report requires the "
            "feature role")

    if control_doc_relpaths:
        if isinstance(sidecar_role, Mapping):
            bound = {f.get("relpath")
                     for f in sidecar_role.get("source_files", [])
                     if isinstance(f, Mapping)}
            unbound = sorted(set(control_doc_relpaths) - bound)
            if unbound:
                problems.append(
                    f"control documents {unbound} are not bound in "
                    "the sidecar role — unbound control evidence may "
                    "not be consumed")
        else:
            problems.append(
                "control-document binding requires the sidecar role")
    return problems
