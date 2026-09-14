"""T2S validation scaffold — schema-only research bridge (T2S-*).

A typed, self-hashed scaffold envelope that records *how* a future
Tranche-1+ validation would be organized — typed references to a MEC
contract and an FMX envelope, split metadata (temporal embargo, geographic
holdout, group-disjoint event separation), and a research-only metric
registry — while executing **nothing**.

Guarantees:

* mode is always ``VALIDATION_SCAFFOLD_ONLY`` and the scaffold status is
  always ``BLOCKED_PENDING_FMX`` in this tranche;
* inherited state must record ``B_TO_C_BLOCKED``, ``E_BLOCKED``,
  ``F_BLOCKED``, and ``ranking_rerun=false`` — a claimed ready/passed state
  is rejected;
* references are typed digests — the B ranked array and priority values
  are never accepted as inputs;
* with ``reference_root``, every reference is file-bound: the file must
  be a verified artifact envelope whose self-hash equals the declared
  digest, and the bound B envelope must contain the declared
  ``ranked_array_canonical_sha256`` and, when it records a ``b_status``,
  agree with the caller-asserted statuses;
* the metric registry is frozen by ``metric_registry_sha256`` and the
  cohort split is frozen by ``split_spec.split_id``;
* the builder asserts ``binding_status`` — ``FILE_BOUND`` when a
  ``reference_root`` bound the references, ``UNBOUND_INFORMATIONAL``
  otherwise; a caller-supplied value is ignored, a ``FILE_BOUND`` claim
  is unverifiable without the root, and strict verification requires it
  (T2S-05);
* ``candidate_generation_id`` and ``code_revision`` are required
  non-empty strings under strict verification, every file-bound
  referenced envelope that carries them must agree with the declared
  values, and under strict verification every bound referenced envelope
  must carry them at all — mixed-generation or unattributed assemblies
  are never legal (T2S-06, T2S-11);
* ``scaffold_profile`` defaults to ``T2_RESEARCH_BLOCKED``; the
  successor profile ``T2_REAL_V1`` requires
  ``fmx_status=FMX_READY``, claimed only file-bound — a
  ``reference_root`` is required and the bound FMX envelope must itself
  carry ``file_bindings`` freeze evidence (T2S-08, T2S-09);
* a file-bound reference's declared fields must equal the bound
  envelope's own records — ``mec_reference.envelope_type``,
  ``fmx_reference.fmx_status``, and the ``b_reference`` status derived
  from the bound doc's ``b_status``/``phase_status``/``gate_passed``
  are all compared against the artifact (T2S-10);
* ``verify_scaffold_envelope(strict=True)`` requires a
  ``reference_root``, ``binding_status=FILE_BOUND``, declared
  ``candidate_generation_id``/``code_revision``, and file-bound
  ``relative_path`` on all three references;
* no caller-supplied gate/verdict booleans;
* no execution entry points exist: nothing here calls B ranking, LOO,
  GMM, Isolation Forest, change-point, anomaly, or ``run_validation`` —
  there is deliberately no such function in this module;
* metric registry entries are descriptive metadata only and may not carry
  operational thresholds.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Optional

import json
from pathlib import Path

from .provenance import (bind_artifact_envelope,
                         sha256_canonical,
                         verify_artifact_envelope)
from .research_boundaries import (QUARANTINED_MODULES,
                                  lint_research_claims)

SCAFFOLD_ENVELOPE_TYPE = "VALIDATION_SCAFFOLD_V1"
MODE_SCAFFOLD_ONLY = "VALIDATION_SCAFFOLD_ONLY"
BLOCKED_PENDING_FMX = "BLOCKED_PENDING_FMX"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_REQUIRED_B_STATUS = "B_TO_C_BLOCKED"
_REQUIRED_E_STATUS = "E_BLOCKED"
_REQUIRED_F_STATUS = "F_BLOCKED"
_ALLOWED_FMX_STATUSES = ("FMX_BLOCKED_PENDING_EXPLICIT_FREEZE",)
_FMX_READY_STATUS = "FMX_READY"

# T2S-05 — binding status is asserted by the builder, never trusted
# from the caller.  FILE_BOUND means every reference digest is pinned
# to a verified on-disk envelope under a reference root;
# UNBOUND_INFORMATIONAL is digest-only and can never satisfy strict
# transition semantics.
BINDING_FILE_BOUND = "FILE_BOUND"
BINDING_UNBOUND = "UNBOUND_INFORMATIONAL"
_BINDING_STATUSES = (BINDING_FILE_BOUND, BINDING_UNBOUND)

# T2S-08 — successor profiles.  The default profile keeps the FMX
# reference blocked; T2_REAL_V1 admits a file-bound FMX_READY claim
# backed by real freeze evidence (file_bindings on the bound FMX
# envelope).  The scaffold status stays BLOCKED_PENDING_FMX under both.
PROFILE_T2_RESEARCH_BLOCKED = "T2_RESEARCH_BLOCKED"
PROFILE_T2_REAL_V1 = "T2_REAL_V1"
_SCAFFOLD_PROFILES = (PROFILE_T2_RESEARCH_BLOCKED, PROFILE_T2_REAL_V1)

# T2S-06 — strict transition semantics bind the assembly to a single
# candidate generation and code revision; file-bound referenced
# envelopes that carry the same fields must agree with the declared
# values — mixed-generation assemblies are never legal.
_CROSS_GENERATION_FIELDS = ("candidate_generation_id", "code_revision")

_FORBIDDEN_REFERENCE_TOKENS = QUARANTINED_MODULES
_FORBIDDEN_CALLER_KEYS = ("gate_override", "verdict", "passed",
                          "gate_passed", "force_ready")
_METRIC_CLASSES = ("calibration", "discrimination", "lead_time",
                   "false_alarm", "skill")
_METRIC_KEYS = ("metric_id", "class", "operational_threshold",
                "description", "unit")

# Exact allowlists — a reference may only carry its typed digest fields
# plus an optional file binding.  No payload keys (ranked, top_five,
# priority) can be smuggled through a reference.
_REFERENCE_KEYS = {
    "mec_reference": {"envelope_sha256", "envelope_type", "relative_path"},
    "fmx_reference": {"envelope_sha256", "fmx_status", "relative_path"},
    "b_reference": {"status", "ranked_array_canonical_sha256",
                    "envelope_sha256", "relative_path"},
}
_REFERENCE_DIGEST_FIELD = {"mec_reference": "envelope_sha256",
                           "fmx_reference": "envelope_sha256",
                           "b_reference": "envelope_sha256"}
_STRICT_BOUND_REFERENCES = ("mec_reference", "fmx_reference",
                            "b_reference")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))


def _iter_strings(obj: Any, prefix: str = ""):
    if isinstance(obj, str):
        yield prefix, obj
    elif isinstance(obj, Mapping):
        for key, value in obj.items():
            yield from _iter_strings(value, f"{prefix}{key}.")
    elif isinstance(obj, (list, tuple)):
        for index, value in enumerate(obj):
            yield from _iter_strings(value, f"{prefix}[{index}].")


def _resolve_reference_file(root: Path, relpath: Any, label: str,
                            problems: list[str]) -> Optional[Path]:
    """Resolve a reference file binding: safe relative path, regular
    file, no symlink, must stay under ``root``."""
    if not isinstance(relpath, str) or not relpath:
        problems.append(f"{label}.relative_path must be a non-empty "
                        "relative path")
        return None
    rel = Path(relpath)
    if rel.is_absolute() or ".." in rel.parts:
        problems.append(f"{label}.relative_path {relpath!r} must be "
                        "relative without traversal")
        return None
    target = root / rel
    if target.is_symlink():
        problems.append(f"{label}.relative_path {relpath!r} is a symlink")
        return None
    root_r = root.resolve()
    resolved = target.resolve()
    if resolved != root_r and root_r not in resolved.parents:
        problems.append(f"{label}.relative_path {relpath!r} resolves "
                        "outside the reference root")
        return None
    if not target.is_file():
        problems.append(f"{label}.relative_path {relpath!r} is not a "
                        "file under the reference root")
        return None
    return target


def _check(payload: Mapping[str, Any], problems: list[str],
           reference_root: "Optional[str | Path]" = None,
           strict: bool = False) -> None:
    # Recursive claim lint — nested forged READY/WARNING/operational
    # fields are rejected wherever they hide in the payload.  The
    # references subtree is deliberately excluded: reference digests and
    # statuses are separately validated against exact key allowlists and
    # file bindings below, and the T2_REAL_V1 successor profile
    # legitimately carries a file-bound FMX_READY status on its
    # fmx_reference which the generic claim lint would reject.
    ok_lint, lint_problems = lint_research_claims(
        {key: value for key, value in payload.items()
         if key != "references"})
    if not ok_lint:
        problems.extend(lint_problems)

    if payload.get("mode") != MODE_SCAFFOLD_ONLY:
        problems.append(f"mode must be {MODE_SCAFFOLD_ONLY!r}")
    if payload.get("research_diagnostic_only") is not True:
        problems.append("research_diagnostic_only must be true")
    for key in _FORBIDDEN_CALLER_KEYS:
        if key in payload:
            problems.append(f"caller-supplied verdict key {key!r} is "
                            "forbidden — the scaffold cannot be promoted "
                            "by argument")

    # T2S-08 — scaffold profile.  Absent means the default research-
    # blocked profile; an unknown profile is rejected and checked as if
    # it were the tighter default.
    profile = payload.get("scaffold_profile", PROFILE_T2_RESEARCH_BLOCKED)
    if profile not in _SCAFFOLD_PROFILES:
        problems.append("scaffold_profile must be one of "
                        f"{_SCAFFOLD_PROFILES}")
        profile = PROFILE_T2_RESEARCH_BLOCKED

    refs = payload.get("references")
    if not isinstance(refs, Mapping):
        problems.append("references must be a mapping")
        refs = {}
    else:
        for rname in refs:
            if rname not in _REFERENCE_KEYS:
                problems.append(f"references.{rname} is not an allowed "
                                "reference type")
        for rname, allowed in _REFERENCE_KEYS.items():
            rval = refs.get(rname)
            if isinstance(rval, Mapping):
                extra = set(rval) - allowed
                if extra:
                    problems.append(
                        f"references.{rname} has disallowed fields "
                        f"{sorted(extra)} — references carry typed "
                        "digests only")
    mec_ref = refs.get("mec_reference")
    if not isinstance(mec_ref, Mapping) or not _is_sha256(
            mec_ref.get("envelope_sha256")) or mec_ref.get(
            "envelope_type") != "MULTI_EVENT_CONTRACT_V1":
        problems.append("references.mec_reference must be a typed MEC "
                        "envelope digest reference")
    fmx_ref = refs.get("fmx_reference")
    # T2S-08/T2S-09 — the profile decides the FMX contract: the default
    # research profile requires the blocked status; the successor
    # T2_REAL_V1 profile REQUIRES a ready matrix, claimed only as a
    # file-bound claim — the reference_root is required even on a
    # non-strict verify and the bound FMX envelope must itself carry
    # file_bindings freeze evidence (checked with the bound docs below).
    fmx_ready_claim = isinstance(fmx_ref, Mapping) and fmx_ref.get(
        "fmx_status") == _FMX_READY_STATUS
    allowed_fmx = ((_FMX_READY_STATUS,)
                   if profile == PROFILE_T2_REAL_V1
                   else _ALLOWED_FMX_STATUSES)
    if not isinstance(fmx_ref, Mapping) or not _is_sha256(
            fmx_ref.get("envelope_sha256")):
        problems.append("references.fmx_reference must be a typed FMX "
                        "envelope digest reference")
    elif fmx_ref.get("fmx_status") not in allowed_fmx:
        if profile == PROFILE_T2_REAL_V1:
            problems.append(
                "references.fmx_reference.fmx_status must be a "
                "file-bound FMX_READY under the "
                f"{PROFILE_T2_REAL_V1} profile — a blocked matrix "
                "cannot back a real-validation design")
        else:
            problems.append(
                "references.fmx_reference.fmx_status must be "
                "FMX_BLOCKED_PENDING_EXPLICIT_FREEZE — the scaffold "
                "cannot advance on an unverified matrix")
    if fmx_ready_claim and reference_root is None:
        problems.append(
            "references.fmx_reference.fmx_status FMX_READY requires a "
            "reference_root — a READY claim must bind to a verified "
            "FMX envelope carrying file_bindings")
    b_ref = refs.get("b_reference")
    if not isinstance(b_ref, Mapping) or not _is_sha256(
            b_ref.get("ranked_array_canonical_sha256")):
        problems.append("references.b_reference must carry the "
                        "ranked_array_canonical_sha256 digest")
    elif b_ref.get("status") != _REQUIRED_B_STATUS:
        problems.append("references.b_reference.status must be the "
                        f"inherited {_REQUIRED_B_STATUS!r} state")
    for dotted, value in _iter_strings(refs):
        for tok in _FORBIDDEN_REFERENCE_TOKENS:
            if tok in value:
                problems.append(f"references field {dotted!r} names a "
                                f"quarantined legacy module {tok!r}")
    for rname, rval in refs.items():
        if not isinstance(rval, Mapping):
            problems.append(f"references.{rname} must be a digest "
                            "reference mapping")
            continue
        for rkey, rvalue in rval.items():
            if isinstance(rvalue, (list, tuple)):
                problems.append(
                    f"references.{rname}.{rkey} carries a data payload; "
                    "only scalar digest references are permitted")

    # File-bound references: when a reference_root is supplied, MEC, FMX
    # and B references must resolve to real envelope files on disk whose
    # verified self-hash equals the declared digest — a plausible-looking
    # 64-hex string that names no valid envelope is rejected.
    bound_docs: dict[str, Mapping[str, Any]] = {}
    if reference_root is not None:
        root = Path(reference_root)
        if isinstance(b_ref, Mapping) and not _is_sha256(
                b_ref.get("envelope_sha256")):
            problems.append(
                "references.b_reference.envelope_sha256 must be a sha256 "
                "digest of the bound B envelope when a reference_root "
                "file-binds the reference")
        for rname, digest_field in _REFERENCE_DIGEST_FIELD.items():
            rval = refs.get(rname)
            if not isinstance(rval, Mapping):
                continue
            target = _resolve_reference_file(
                root, rval.get("relative_path"),
                f"references.{rname}", problems)
            if target is None or not _is_sha256(rval.get(digest_field)):
                continue
            try:
                doc = json.loads(target.read_text("utf-8"))
            except (OSError, ValueError) as exc:
                problems.append(f"references.{rname} file unreadable: "
                                f"{exc}")
                continue
            ok_env, envp = verify_artifact_envelope(doc)
            if not ok_env:
                problems.append(
                    f"references.{rname} file is not a valid artifact "
                    f"envelope: {envp[0] if envp else 'invalid'}")
                continue
            if doc.get("artifact_sha256") != rval[digest_field]:
                problems.append(
                    f"references.{rname}.{digest_field} does not match "
                    "the referenced envelope's verified self-hash")
                continue
            bound_docs[rname] = doc

        # T2S-10: typed ref/doc equality — once a reference is
        # file-bound, the declared reference fields must equal what the
        # bound envelope itself records.  A reference may not describe a
        # document differently from the document's own self-description.
        mec_doc = bound_docs.get("mec_reference")
        if mec_doc is not None and isinstance(mec_ref, Mapping) and \
                mec_doc.get("envelope_type") != \
                mec_ref.get("envelope_type"):
            problems.append(
                "references.mec_reference.envelope_type "
                f"{mec_ref.get('envelope_type')!r} does not equal the "
                f"bound envelope's envelope_type "
                f"{mec_doc.get('envelope_type')!r}")
        fmx_doc_for_status = bound_docs.get("fmx_reference")
        if fmx_doc_for_status is not None and isinstance(
                fmx_ref, Mapping) and \
                fmx_doc_for_status.get("fmx_status") != \
                fmx_ref.get("fmx_status"):
            problems.append(
                "references.fmx_reference.fmx_status "
                f"{fmx_ref.get('fmx_status')!r} does not equal the "
                f"bound envelope's fmx_status "
                f"{fmx_doc_for_status.get('fmx_status')!r}")

        # T2S-02: the B reference is content-bound, not merely
        # existence-bound — the verified B envelope must itself contain
        # the declared ranked-array canonical digest, so a caller cannot
        # point at an unrelated honest envelope.
        b_doc = bound_docs.get("b_reference")
        if b_doc is not None and isinstance(b_ref, Mapping):
            ranked_digest = b_ref.get("ranked_array_canonical_sha256")
            if isinstance(ranked_digest, str) and _is_sha256(
                    ranked_digest) and not any(
                    ranked_digest in value
                    for _, value in _iter_strings(b_doc)):
                problems.append(
                    "references.b_reference.ranked_array_canonical_sha256 "
                    "is not contained in the bound B envelope — the "
                    "digest must be carried by the referenced artifact")
            # T2S-03/T2S-10: the bound B envelope's own recorded status
            # is authoritative — an explicit b_status or phase_status is
            # used directly; a bare gate_passed maps to the B_TO_C phase
            # status it would produce.  The derived doc status must equal
            # the caller-asserted references.b_reference.status and
            # inherited_state.b_status; a bound doc recording no status
            # at all cannot verify the declared one and is rejected.
            if "b_status" in b_doc:
                doc_status = b_doc.get("b_status")
            elif "phase_status" in b_doc:
                doc_status = b_doc.get("phase_status")
            elif "gate_passed" in b_doc:
                doc_status = ("B_TO_C_READY" if b_doc.get("gate_passed")
                              is True else "B_TO_C_BLOCKED")
            else:
                doc_status = None
            if doc_status is None:
                problems.append(
                    "references.b_reference bound envelope records no "
                    "b_status, phase_status, or gate_passed — the "
                    "declared status cannot be verified against the "
                    "artifact")
            else:
                inh = payload.get("inherited_state")
                inh_status = (inh.get("b_status")
                              if isinstance(inh, Mapping) else None)
                if doc_status != b_ref.get("status") or \
                        doc_status != inh_status:
                    problems.append(
                        f"bound B envelope status {doc_status!r} "
                        "disagrees with the caller-asserted "
                        "references.b_reference.status or "
                        "inherited_state.b_status")
            # PKG-11 follow-through: a bound doc that itself marks its
            # ranked digests as digest-only informational evidence can
            # never satisfy a strict assembly — informational B metadata
            # is non-authorizing in every consumer, not just the package.
            if strict and isinstance(b_doc.get("b_evidence_binding"),
                                     str) and \
                    b_doc["b_evidence_binding"] != "FILE_BOUND":
                problems.append(
                    "references.b_reference bound envelope declares "
                    f"b_evidence_binding="
                    f"{b_doc['b_evidence_binding']!r} — informational "
                    "B digests cannot satisfy a strict scaffold")

        # T2S-08: an FMX_READY claim under T2_REAL_V1 is valid only
        # when backed by real freeze evidence — the verified bound FMX
        # envelope must itself carry file_bindings.  Strict file-binding
        # semantics apply to this reference even on a non-strict verify.
        if fmx_ready_claim:
            fmx_doc = bound_docs.get("fmx_reference")
            if fmx_doc is None:
                problems.append(
                    "references.fmx_reference FMX_READY claim is not "
                    "backed by a verified file-bound FMX envelope")
            elif not isinstance(fmx_doc.get("file_bindings"), Mapping) \
                    or not fmx_doc["file_bindings"]:
                problems.append(
                    "bound FMX envelope lacks file_bindings — an "
                    "FMX_READY claim requires real freeze evidence "
                    "carried by the referenced artifact")

        # T2S-06/T2S-11: cross-generation equality — when the payload
        # declares candidate_generation_id / code_revision, every
        # file-bound referenced envelope that carries the same field
        # must agree with the declared value; a mixed-generation
        # assembly is never legal, on strict and non-strict verifies
        # alike.  Under strict verification the identity is mandatory:
        # a bound envelope missing the field rejects outright — an
        # unattributed artifact can never join a strict assembly.
        for field in _CROSS_GENERATION_FIELDS:
            declared = payload.get(field)
            if not (isinstance(declared, str) and declared):
                continue
            for rname, doc in bound_docs.items():
                if field not in doc:
                    if strict:
                        problems.append(
                            f"references.{rname} bound envelope lacks "
                            f"{field!r} — strict verification requires "
                            "every bound reference to carry the "
                            "declared assembly identity")
                elif doc.get(field) != declared:
                    problems.append(
                        f"references.{rname} bound envelope carries "
                        f"{field}={doc.get(field)!r} but the payload "
                        f"declares {declared!r} — mixed-generation "
                        "assemblies are rejected")

    inherited = payload.get("inherited_state")
    if not isinstance(inherited, Mapping):
        problems.append("inherited_state must be a mapping")
        inherited = {}
    else:
        if inherited.get("b_status") != _REQUIRED_B_STATUS:
            problems.append(f"inherited_state.b_status must be "
                            f"{_REQUIRED_B_STATUS!r}")
        if inherited.get("e_status") != _REQUIRED_E_STATUS:
            problems.append(f"inherited_state.e_status must be "
                            f"{_REQUIRED_E_STATUS!r}")
        if inherited.get("f_status") != _REQUIRED_F_STATUS:
            problems.append(f"inherited_state.f_status must be "
                            f"{_REQUIRED_F_STATUS!r}")
        if inherited.get("ranking_rerun") is not False:
            problems.append("inherited_state.ranking_rerun must be false")

    split = payload.get("split_spec")
    if not isinstance(split, Mapping):
        problems.append("split_spec must be a mapping")
        split = {}
    else:
        if not isinstance(split.get("split_id"), str) or \
                not split["split_id"]:
            problems.append("split_spec.split_id must be a non-empty "
                            "string naming the frozen cohort split")
        if not isinstance(split.get("temporal_embargo_days"), int) or \
                split["temporal_embargo_days"] < 0:
            problems.append("split_spec.temporal_embargo_days must be a "
                            "non-negative integer")
        geo = split.get("geographic_holdout")
        if not isinstance(geo, Mapping) or not isinstance(
                geo.get("min_separation_km"), (int, float)) or \
                geo.get("min_separation_km", 0) <= 0:
            problems.append("split_spec.geographic_holdout."
                            "min_separation_km must be positive")
        sep = split.get("event_separation")
        if not isinstance(sep, Mapping) or sep.get("group_disjoint") \
                is not True:
            problems.append("split_spec.event_separation.group_disjoint "
                            "must be true")

    registry = payload.get("metric_registry")
    if not isinstance(registry, list) or not registry:
        problems.append("metric_registry must be a non-empty list")
        registry = []
    seen_metrics: set[str] = set()
    for i, m in enumerate(registry):
        label = f"metric_registry[{i}]"
        if not isinstance(m, Mapping):
            problems.append(f"{label} must be a mapping")
            continue
        extra = set(m) - set(_METRIC_KEYS)
        if extra:
            problems.append(f"{label} has disallowed fields "
                            f"{sorted(extra)} — no operational threshold "
                            "or payload fields")
        mid = m.get("metric_id")
        if not isinstance(mid, str) or not mid:
            problems.append(f"{label}.metric_id is required")
        elif mid in seen_metrics:
            problems.append(f"{label}.metric_id {mid!r} is a duplicate — "
                            "registry ids must be unique")
        else:
            seen_metrics.add(mid)
        if m.get("class") not in _METRIC_CLASSES:
            problems.append(f"{label}.class must be one of "
                            f"{_METRIC_CLASSES}")
        if m.get("operational_threshold") is not None:
            problems.append(f"{label}.operational_threshold must be null "
                            "— research metrics carry no operational "
                            "thresholds")

    # T2S-04: the metric registry is frozen by digest — the declared
    # metric_registry_sha256 must equal the canonical digest of the
    # registry actually carried, so a post-hoc registry edit is caught.
    registry_digest = payload.get("metric_registry_sha256")
    if not _is_sha256(registry_digest):
        problems.append("metric_registry_sha256 must be a sha256 digest "
                        "of the canonical metric_registry")
    elif isinstance(payload.get("metric_registry"), list):
        try:
            actual_registry_digest = sha256_canonical(
                payload["metric_registry"])
        except (TypeError, ValueError):
            actual_registry_digest = None
        if registry_digest != actual_registry_digest:
            problems.append("metric_registry_sha256 does not match "
                            "sha256_canonical(metric_registry) — the "
                            "registry digest is frozen at build time")


def build_scaffold_envelope(payload: Mapping[str, Any], *,
                            reference_root: "Optional[str | Path]" = None
                            ) -> dict[str, Any]:
    """Validate and bind a T2S scaffold envelope.  Status is always
    ``BLOCKED_PENDING_FMX`` — no execution, no promotion.

    With ``reference_root``, MEC/FMX/B references must carry
    ``relative_path`` bindings to real files under the root whose
    recomputed digests match the declared envelope digests; the bound B
    envelope must additionally contain the declared ranked-array
    canonical digest and agree with the caller-asserted b_status.
    ``binding_status`` is asserted here — FILE_BOUND when a root bound
    the references, UNBOUND_INFORMATIONAL otherwise — and any
    caller-supplied value is overwritten, never trusted."""
    if not isinstance(payload, Mapping):
        raise TypeError("scaffold payload must be a mapping")
    problems: list[str] = []
    _check(payload, problems, reference_root)
    if problems:
        raise ValueError("T2S scaffold payload is not valid: "
                         + "; ".join(problems[:6]))
    envelope = dict(payload)
    envelope["envelope_type"] = SCAFFOLD_ENVELOPE_TYPE
    envelope["scaffold_status"] = BLOCKED_PENDING_FMX
    envelope["promotion_eligible"] = False
    envelope["production_authorized"] = False
    envelope["warning_path_authorized"] = False
    envelope["binding_status"] = (BINDING_FILE_BOUND
                                  if reference_root is not None
                                  else BINDING_UNBOUND)
    envelope["no_claims"] = [
        "validation scaffold schema only; no validation was executed",
        "no scientific validation, warning, production, or authority "
        "readiness is established"]
    return bind_artifact_envelope(envelope)


def verify_scaffold_envelope(payload: Any, *,
                             reference_root: "Optional[str | Path]" = None,
                             strict: bool = False
                             ) -> tuple[bool, list[str]]:
    """Fail-closed verification: self-hash, structural contract, the
    blocked-status invariant, and (with ``reference_root``) file-bound
    reference digests.

    With ``strict=True`` every reference — MEC, FMX, and B — must be
    file-bound: a ``reference_root`` is required and each reference must
    carry a ``relative_path`` that resolves to a verified envelope under
    that root.  Strict verification additionally requires
    ``binding_status=FILE_BOUND`` and non-empty declared
    ``candidate_generation_id``/``code_revision`` — an
    UNBOUND_INFORMATIONAL envelope is legal only non-strict, and a
    FILE_BOUND claim without a supplied ``reference_root`` is flagged
    as unverified.  When ``reference_root`` is supplied, the file-bound
    checks run on strict and non-strict verifies alike."""
    problems: list[str] = []
    ok, env_problems = verify_artifact_envelope(payload)
    if not ok:
        problems.extend(env_problems)
    if not isinstance(payload, Mapping):
        problems.append("scaffold envelope must be a mapping")
        return False, problems
    if payload.get("envelope_type") != SCAFFOLD_ENVELOPE_TYPE:
        problems.append(f"envelope_type must be {SCAFFOLD_ENVELOPE_TYPE!r}")
    if payload.get("scaffold_status") != BLOCKED_PENDING_FMX:
        problems.append(f"scaffold_status must be {BLOCKED_PENDING_FMX!r}")
    if payload.get("promotion_eligible") is not False:
        problems.append("promotion_eligible must be false")
    if payload.get("production_authorized") is not False:
        problems.append("production_authorized must be false")
    if payload.get("warning_path_authorized") is not False:
        problems.append("warning_path_authorized must be false")
    # T2S-05: binding_status is part of the signed envelope.  A
    # FILE_BOUND claim can only be honored when the caller supplies the
    # reference_root the references were bound under — without it the
    # claim is unverified.  UNBOUND_INFORMATIONAL is legal on a
    # non-strict verify but never satisfies strict transition semantics.
    binding_status = payload.get("binding_status")
    if binding_status not in _BINDING_STATUSES:
        problems.append("binding_status must be FILE_BOUND or "
                        "UNBOUND_INFORMATIONAL")
    elif binding_status == BINDING_FILE_BOUND and reference_root is None:
        problems.append("binding claim unverified — supply "
                        "reference_root")
    if strict:
        if binding_status != BINDING_FILE_BOUND:
            problems.append(
                "strict verification requires binding_status "
                "FILE_BOUND — an unbound informational scaffold never "
                "satisfies strict transition semantics")
        if reference_root is None:
            problems.append("strict verification requires a "
                            "reference_root — every reference must be "
                            "file-bound")
        # T2S-06: strict mode requires the assembly to name its
        # candidate generation and code revision; bound referenced
        # envelopes carrying the same fields must agree (checked in
        # _check once the files resolve).
        for field in _CROSS_GENERATION_FIELDS:
            if not isinstance(payload.get(field), str) or \
                    not payload.get(field):
                problems.append(
                    f"strict verification requires {field} to be a "
                    "non-empty string naming the candidate generation "
                    "and code revision")
        srefs = payload.get("references")
        for rname in _STRICT_BOUND_REFERENCES:
            rval = srefs.get(rname) if isinstance(srefs, Mapping) else None
            rel = (rval.get("relative_path")
                   if isinstance(rval, Mapping) else None)
            if not isinstance(rel, str) or not rel:
                problems.append(
                    f"strict verification requires references.{rname}."
                    "relative_path bound to a verified envelope file "
                    "under the reference root")
    _check(payload, problems, reference_root, strict=strict)
    return (not problems), problems
