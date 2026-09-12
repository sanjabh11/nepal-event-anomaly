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
from .provenance import (verify_artifact_envelope, verify_gate_artifact,
                         write_deterministic_text)

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
        add(f"A_CATALOG gate passed: {bool(catalog_gate.get('passed'))}.")
    if screen_gate is not None:
        add(f"B_TO_C gate passed: {bool(screen_gate.get('passed'))}.")
    if validation_gate is not None:
        add(f"E_VALIDATION gate passed: {bool(validation_gate.get('passed'))}.")
    return "\n".join(lines) + "\n"


def _strict_gate_problems(summary: Mapping[str, Any],
                          catalog_gate: Optional[Mapping],
                          screen_gate: Optional[Mapping],
                          validation_gate: Optional[Mapping]) -> list[str]:
    """Return fail-closed F-input problems without trusting gate booleans."""
    problems: list[str] = []
    if not isinstance(summary, Mapping):
        problems.append("validation summary must be a mapping")

    a_ok, a_errors = verify_gate_artifact(
        catalog_gate if isinstance(catalog_gate, Mapping) else {},
        expected_gate_id=C.GateId.A_CATALOG.value)
    if not a_ok:
        problems.extend("A_CATALOG: " + error for error in a_errors)
    elif isinstance(catalog_gate, Mapping) and catalog_gate.get("passed") is not True:
        problems.append("A_CATALOG: verified gate is not passed")

    b_ok, b_errors = verify_artifact_envelope(
        screen_gate if isinstance(screen_gate, Mapping) else {})
    if not b_ok:
        problems.extend("B_SCREEN: " + error for error in b_errors)
    elif isinstance(screen_gate, Mapping):
        inner = screen_gate.get("gate")
        inner_ok, inner_errors = verify_gate_artifact(
            inner if isinstance(inner, Mapping) else {},
            expected_gate_id=C.GateId.B_TO_C.value)
        if not inner_ok:
            problems.extend("B_TO_C: " + error for error in inner_errors)
        elif isinstance(inner, Mapping) and inner.get("passed") is not True:
            problems.append("B_TO_C: verified gate is not passed")

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


def _short(value) -> str:
    return str(value)[:12] if value else "unrecorded"


def write_briefing(path, text: str) -> Path:
    p = Path(path)
    write_deterministic_text(p, text)
    return p
