"""nepal.framework_v1.briefing — deterministic F briefing generator.

Produces a single self-contained markdown briefing from the validation
summary (and optionally the A/B gate outcomes).  Generation is fully
deterministic: identical inputs produce byte-identical text (no timestamps,
no randomness, no external publication or email actions).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

from . import contract as C
from .provenance import (bind_artifact_envelope, canonical_json,
                         gate_input_artifact_sha256,
                         sha256_canonical, sha256_text,
                         verify_artifact_envelope,
                         verify_gate_input,
                         write_deterministic_json, write_deterministic_text)

NOT_EVACUATION = (
    "This is NOT an evacuation map, NOT a prediction, and NOT an early-warning "
    "system. It is a retrospective research prioritization exercise."
)
LIABILITY = (
    "This document carries no ownership claim over any hazard, event, or "
    "event record; it reports on a third-party compilation (Bashkova-Rupper) "
    "with additive normalization. The authors/operators accept no liability "
    "for decisions made from this research-only material."
)
THREE_PATHWAYS = (
    ("Pathway 1 — Continued in-repo monitoring and prioritization within the "
     "frozen box.",
     "Advantages: no new acquisitions; low cost; deterministic, auditable "
     "outputs.",
     "Disadvantages: no new measurement; cannot detect changes the current "
     "features cannot see; no operational value."),
    ("Pathway 2 — Targeted derived-product acquisition for the locked top-five "
     "(checksum-valid InSAR pair products under optional C).",
     "Advantages: addresses acceleration/change directly on a small, "
     "pre-registered candidate set.",
     "Disadvantages: requires licensed/registered data handling, cost and "
     "orbital revisit timing; still research-only."),
    ("Pathway 3 — Institutional handover / partnership (with responsible "
     "authority in the region).",
     "Advantages: transboundary coordination, operational follow-up by "
     "mandated bodies.",
     "Disadvantages: outside repo scope; requires institutional mandates, "
     "funding and liability transfer we cannot provide."),
)

OUTCOME_SECTIONS = {
    C.OutputStatus.PASS.value: (
        "Pairwise event-versus-control win rate exceeds chance with a "
        "Wilson 95% interval excluding 0.5 under the pre-locked holdout "
        "rule. This supports a research priority for further work; it is "
        "not a clearance for operational use."),
    C.OutputStatus.NULL.value: (
        "The win-rate interval lies at or below chance. This null result "
        "is publishable as-is and still triggers the briefing artifacts "
        "(F). No claim is made, and no gate threshold was softened to "
        "produce a different outcome."),
    C.OutputStatus.INDETERMINATE.value: (
        "INDETERMINATE is the default for small samples, incomplete "
        "holdouts, undefined denominators, or Wilson intervals spanning "
        "0.5. The indeterminate rows are retained and reported; no TP>60% "
        "style threshold was used, and no conclusion is attached."),
}


def generate_briefing(validation_summary: Mapping, *,
                      catalog_gate: Optional[Mapping] = None,
                      screen_gate: Optional[Mapping] = None,
                      validation_gate: Optional[Mapping] = None,
                      contract_hash: Optional[str] = None,
                      strict: bool = False) -> str:
    """Deterministic markdown briefing for PASS / NULL / INDETERMINATE."""
    if strict:
        problems = _strict_gate_problems(
            validation_summary, catalog_gate, screen_gate, validation_gate)
        if problems:
            raise ValueError("strict briefing inputs are not verified: " +
                             "; ".join(problems))
    status = validation_summary.get("status",
                                    C.OutputStatus.INDETERMINATE.value)
    lines: list[str] = []
    add = lines.append
    add(f"# Framework v1 Briefing — {status}")
    add("")
    add(NOT_EVACUATION)
    add("")
    add("## Goal and method boundary")
    add("")
    add(
        "Retrospective, pre-registered assessment inside one frozen 30 km "
        "projected box (EPSG:32645) centered on the frozen Langtang source "
        "reference point. The box, windows, statuses, gates and tie-breaks "
        "are fixed in the framework contract; this briefing is generated "
        "deterministically from the validation summary, not from any "
        "publication or messaging activity.")
    add("")
    add("## Catalog and data limitations")
    add("")
    add(
        "Catalog rows are additive normalizations of the preserved raw "
        "compilation; raw rows are unmodified. Month-only, season-only, "
        "malformed, and unresolved dates stay in the catalog but cannot "
        "count toward any validation gate. Rows below the minimum detectable "
        "size or without adequate observation are UNOBSERVABLE, not false "
        "negatives. Mechanism confirmation means source-citation support in "
        "the compilation, not independent field adjudication. Thermal, "
        "Farinotti and permafrost layers are sidecars with no promotion "
        "eligibility.")
    add("")
    add("## Three possible pathways")
    add("")
    for name, adv, dis in THREE_PATHWAYS:
        add(f"### {name}")
        add("")
        add(f"- {adv}")
        add(f"- {dis}")
        add("")
    add("## Liability and non-ownership statement")
    add("")
    add(LIABILITY)
    add("")
    add("## Transboundary and institutional limitations")
    add("")
    add(
        "The study region crosses national boundaries; records cover "
        "multiple HMA countries (e.g. Nepal, China, India, Bhutan, Pakistan, "
        "Tajikistan, Kyrgyzstan). No institutional emails, publication "
        "submissions, or external notification actions are part of this "
        "coding plan, and no claim of operational jurisdiction is made.")
    add("")
    add("## Maintenance and funding requirements")
    add("")
    add(
        "Continuing this research-only pipeline requires maintained inputs "
        "(DEM revisions, RGI/GLIMS updates, licensed worldpop renewal, "
        "acquisition metadata feeds), pinned dependency upgrades, and "
        "sustained compute for terrain processing. None of that funding or "
        "maintenance is committed by this repository.")
    add("")
    add("## Outcome")
    add("")
    add(OUTCOME_SECTIONS.get(status, OUTCOME_SECTIONS[
        C.OutputStatus.INDETERMINATE.value]))
    add("")
    add("---")
    hashes = validation_summary.get("input_hashes", {}) or {}
    parts = [f"catalog={_short(hashes.get('catalog'))}",
             f"controls={_short(hashes.get('controls'))}",
             f"holdout_plan={_short(hashes.get('holdout_plan'))}"]
    add("Input hashes: " + ", ".join(parts) + ".")
    if contract_hash:
        add(f"Contract hash: {contract_hash}.")
    if catalog_gate is not None:
        add(f"A_CATALOG gate passed: {_gate_passed(catalog_gate)}.")
    if screen_gate is not None:
        add(f"B_TO_C gate passed: {_gate_passed(screen_gate)}.")
    if validation_gate is not None:
        add(f"E_VALIDATION gate passed: {_gate_passed(validation_gate)}.")
    return "\n".join(lines) + "\n"


def _strict_gate_problems(summary: Mapping[str, Any],
                          catalog_gate: Optional[Mapping],
                          screen_gate: Optional[Mapping],
                          validation_gate: Optional[Mapping]) -> list[str]:
    """Return fail-closed F-input problems without trusting gate booleans."""
    problems: list[str] = []
    if not isinstance(summary, Mapping):
        problems.append("validation summary must be a mapping")

    a_ok, a_inner, a_outer, a_errors = verify_gate_input(
        catalog_gate,
        expected_gate_id=C.GateId.A_CATALOG.value)
    if not a_ok:
        problems.extend("A_CATALOG: " + error for error in a_errors)
    elif not isinstance(a_inner, Mapping) or a_inner.get("passed") is not True:
        problems.append("A_CATALOG: verified gate is not passed")
    if a_ok and not isinstance(a_outer, Mapping):
        problems.append("A_CATALOG: outer artifact envelope is required")
    if isinstance(a_outer, Mapping):
        a_provenance = a_outer.get("provenance")
        if (not isinstance(a_provenance, Mapping) or
                a_provenance.get("framework_contract_sha256") !=
                C.contract_hash()):
            problems.append(
                "A_CATALOG: outer envelope framework contract does not match runtime")

    b_ok, b_inner, b_outer, b_errors = verify_gate_input(
        screen_gate,
        expected_gate_id=C.GateId.B_TO_C.value,
        require_outer_envelope=True)
    if not b_ok:
        problems.extend("B_SCREEN: " + error for error in b_errors)
    elif not isinstance(b_inner, Mapping) or b_inner.get("passed") is not True:
        problems.append("B_TO_C: verified gate is not passed")
    if isinstance(b_outer, Mapping):
        b_provenance = b_outer.get("provenance")
        if (not isinstance(b_provenance, Mapping) or
                b_provenance.get("framework_contract_sha256") !=
                C.contract_hash()):
            problems.append(
                "B_SCREEN: outer envelope framework contract does not match runtime")

    from .validation import verify_validation_artifact
    e_ok, e_errors = verify_validation_artifact(
        validation_gate if isinstance(validation_gate, Mapping) else {})
    if not e_ok:
        problems.extend("E_VALIDATION: " + error for error in e_errors)
    elif isinstance(validation_gate, Mapping) and isinstance(summary, Mapping):
        e_gate = validation_gate.get("gate")
        if not isinstance(e_gate, Mapping) or e_gate.get("passed") is not True:
            problems.append("E_VALIDATION: verified gate is not passed")
        if validation_gate.get("summary") != dict(summary):
            problems.append("E_VALIDATION summary does not match briefing summary")
    return problems


def _gate_passed(value: Any) -> bool:
    """Read a gate result from either its direct or outer representation."""
    if not isinstance(value, Mapping):
        return False
    inner = value.get("gate") if isinstance(value.get("gate"), Mapping) else value
    return isinstance(inner, Mapping) and inner.get("passed") is True


def _short(value) -> str:
    return str(value)[:12] if value else "unrecorded"


def write_briefing(path, text: str) -> Path:
    p = Path(path)
    write_deterministic_text(p, text)
    return p


def build_briefing_artifact(
    text: str,
    *,
    summary: Mapping[str, Any],
    catalog_gate: Mapping[str, Any],
    screen_gate: Mapping[str, Any],
    validation_gate: Mapping[str, Any],
    contract_hash: str,
) -> dict[str, Any]:
    """Build an authenticated F artifact from verified upstream envelopes.

    The Markdown file remains a convenient presentation output.  This JSON
    envelope is the machine-readable handoff and binds the exact text,
    summary, upstream artifact identities, and research-only claim boundary.
    """
    if not isinstance(text, str):
        raise TypeError("briefing text must be a string")
    if not isinstance(summary, Mapping):
        raise TypeError("briefing summary must be a mapping")
    if contract_hash != C.contract_hash():
        raise ValueError(
            "briefing contract hash does not match the runtime framework contract")
    input_problems = _strict_gate_problems(
        summary, catalog_gate, screen_gate, validation_gate)
    if input_problems:
        raise ValueError("briefing upstream inputs are not verified: " +
                         "; ".join(input_problems))
    return bind_artifact_envelope({
        "profile_id": "FRAMEWORK_V1_FULL",
        "framework_version": C.FRAMEWORK_VERSION,
        "status": C.PHASE_STATUS_F_READY,
        "gate_id": "F_BRIEFING",
        "promotion_eligible": False,
        "production_authorized": False,
        "briefing": text,
        "briefing_sha256": sha256_text(text),
        "summary": dict(summary),
        "summary_sha256": sha256_canonical(dict(summary)),
        "a_gate": dict(catalog_gate),
        "b_gate": dict(screen_gate),
        "e_gate": dict(validation_gate),
        "provenance": {
            "framework_contract_sha256": contract_hash,
            "a_gate_artifact_sha256": gate_input_artifact_sha256(catalog_gate),
            "b_artifact_sha256": screen_gate.get("artifact_sha256"),
            "e_artifact_sha256": validation_gate.get("artifact_sha256"),
        },
        "no_claims": [
            "No operational warning or production authorization",
            "No scientific validation beyond the verified E result",
            "No authority approval or external publication",
        ],
    })


def verify_briefing_artifact(payload: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """Verify the complete F envelope and every upstream gate it references."""
    ok, problems = verify_artifact_envelope(payload)
    if not isinstance(payload, Mapping):
        return False, problems
    if payload.get("profile_id") != "FRAMEWORK_V1_FULL":
        problems.append("briefing artifact profile_id is invalid")
    if payload.get("gate_id") != "F_BRIEFING":
        problems.append("briefing artifact gate_id must be F_BRIEFING")
    if payload.get("status") != C.PHASE_STATUS_F_READY:
        problems.append("briefing artifact status must be F_READY")
    if payload.get("promotion_eligible") is not False:
        problems.append("briefing artifact must not be promotion eligible")
    if payload.get("production_authorized") is not False:
        problems.append("briefing artifact must not authorize production")
    text = payload.get("briefing")
    if not isinstance(text, str):
        problems.append("briefing artifact text is required")
    elif payload.get("briefing_sha256") != sha256_text(text):
        problems.append("briefing artifact briefing_sha256 does not match text")
    summary = payload.get("summary")
    if not isinstance(summary, Mapping):
        problems.append("briefing artifact summary is required")
    elif payload.get("summary_sha256") != sha256_canonical(dict(summary)):
        problems.append("briefing artifact summary_sha256 does not match summary")

    provenance = payload.get("provenance")
    if not isinstance(provenance, Mapping):
        problems.append("briefing artifact provenance is required")
    else:
        for key in ("framework_contract_sha256", "a_gate_artifact_sha256",
                    "b_artifact_sha256", "e_artifact_sha256"):
            value = provenance.get(key)
            if not isinstance(value, str) or len(value) != 64 or any(
                    char not in "0123456789abcdef" for char in value):
                problems.append(f"briefing artifact provenance {key} is invalid")
        if provenance.get("framework_contract_sha256") != C.contract_hash():
            problems.append(
                "briefing artifact framework contract does not match runtime")

    a_gate = payload.get("a_gate")
    a_inner: Optional[Mapping[str, Any]] = None
    if isinstance(a_gate, Mapping):
        a_ok, a_inner, a_outer, a_errors = verify_gate_input(
            a_gate, expected_gate_id=C.GateId.A_CATALOG.value)
        if not a_ok:
            problems.extend("A_CATALOG: " + error for error in a_errors)
        if a_ok and not isinstance(a_outer, Mapping):
            problems.append("A_CATALOG: outer artifact envelope is required")
        if not isinstance(a_inner, Mapping) or a_inner.get("passed") is not True:
            problems.append("A_CATALOG: verified gate is not passed")
        if isinstance(a_outer, Mapping):
            outer_provenance = a_outer.get("provenance")
            if (not isinstance(outer_provenance, Mapping) or
                    outer_provenance.get("framework_contract_sha256") !=
                    C.contract_hash()):
                problems.append(
                    "A_CATALOG: outer envelope framework contract does not "
                    "match runtime")
    b_gate = payload.get("b_gate")
    if isinstance(b_gate, Mapping):
        b_ok, b_inner, b_outer, b_errors = verify_gate_input(
            b_gate, expected_gate_id=C.GateId.B_TO_C.value,
            require_outer_envelope=True)
        if not b_ok:
            problems.extend("B_SCREEN: " + error for error in b_errors)
        if not isinstance(b_inner, Mapping) or b_inner.get("passed") is not True:
            problems.append("B_TO_C: verified gate is not passed")
        if isinstance(b_outer, Mapping):
            outer_provenance = b_outer.get("provenance")
            if (not isinstance(outer_provenance, Mapping) or
                    outer_provenance.get("framework_contract_sha256") !=
                    C.contract_hash()):
                problems.append(
                    "B_SCREEN: outer envelope framework contract does not "
                    "match runtime")
    e_gate = payload.get("e_gate")
    if isinstance(e_gate, Mapping):
        from .validation import verify_validation_artifact
        e_ok, e_errors = verify_validation_artifact(e_gate)
        if not e_ok:
            problems.extend("E_VALIDATION: " + error for error in e_errors)
        elif not isinstance(e_gate.get("gate"), Mapping) or e_gate["gate"].get(
                "passed") is not True:
            problems.append("E_VALIDATION: verified gate is not passed")
    if isinstance(provenance, Mapping):
        if isinstance(a_gate, Mapping) and provenance.get(
                "a_gate_artifact_sha256") != gate_input_artifact_sha256(a_gate):
            problems.append("briefing artifact A gate provenance does not match gate")
        if isinstance(b_gate, Mapping) and provenance.get(
                "b_artifact_sha256") != b_gate.get("artifact_sha256"):
            problems.append("briefing artifact B provenance does not match envelope")
        if isinstance(e_gate, Mapping) and provenance.get(
                "e_artifact_sha256") != e_gate.get("artifact_sha256"):
            problems.append("briefing artifact E provenance does not match envelope")
    for key in ("a_gate", "b_gate", "e_gate"):
        if key not in payload:
            problems.append(f"briefing artifact upstream {key} is required")
    no_claims = payload.get("no_claims")
    if not isinstance(no_claims, list) or not no_claims:
        problems.append("briefing artifact no_claims is required")
    return ok and not problems, problems


def write_briefing_artifact(
    path: str | Path,
    text: str,
    *,
    summary: Mapping[str, Any],
    catalog_gate: Mapping[str, Any],
    screen_gate: Mapping[str, Any],
    validation_gate: Mapping[str, Any],
    contract_hash: str,
) -> dict[str, Any]:
    artifact = build_briefing_artifact(
        text,
        summary=summary,
        catalog_gate=catalog_gate,
        screen_gate=screen_gate,
        validation_gate=validation_gate,
        contract_hash=contract_hash,
    )
    # Keep the complete upstream envelopes inside the authenticated object so
    # a digest-only provenance field cannot be detached from the evidence it
    # claims to reference.
    artifact["a_gate"] = dict(catalog_gate)
    artifact["b_gate"] = dict(screen_gate)
    artifact["e_gate"] = dict(validation_gate)
    artifact = bind_artifact_envelope(artifact)
    write_deterministic_json(path, artifact)
    return artifact
