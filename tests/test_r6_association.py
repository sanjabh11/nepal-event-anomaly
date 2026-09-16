"""Round-6 association hardening probes.

PROV-04 — verified producer binding: a local
``RegimeAssignmentArtifact`` admitted without its serialized frozen
producer payload can never carry ``REGIME_ASSOCIATION_SUPPORTED``;
a supplied payload is recomputed end-to-end and bound to the
artifact field-by-field, and a mismatching one rejects outright.

ASSOC-03 — input binding + report revalidation: every report
carries the canonical ``association_input_manifest/v0`` it was
digested over, and ``problems()`` revalidates manifest, digest, and
carried references for every status — a tampered descriptive report
fails exactly like a tampered supported one.

All fixtures are synthetic and deterministic.
"""
from __future__ import annotations

import dataclasses
import hashlib

import pytest

from nepal.experiment_v0.association import (
    ASSOCIATION_BINDINGS, BINDING_UNVERIFIED, BINDING_VERIFIED,
    AssociationReport, RegimeAssignmentArtifact, run_association)
from nepal.research_v0._hashing import sha256_canonical

from tests.fixtures import synthetic_exp_b4 as fx

_N_BOOT = 32
_SEED = 7


def _lane(events):
    """The (controls, holdout, opportunities) tuple matching the
    b4 synthetic fixture geometry for ``events``."""
    return {
        "controls": fx.make_controls(),
        "holdout": fx.make_holdout(events),
        "opportunities": fx.make_opportunities(),
    }


def _run(artifact, lane, events, **kw):
    kw.setdefault("n_boot", _N_BOOT)
    kw.setdefault("seed", _SEED)
    return run_association(
        artifact, events, lane["controls"], fx.UNIT_BASINS,
        holdout=lane["holdout"], region_basins=fx.REGION_BASINS,
        opportunities=lane["opportunities"], **kw)


# ---------------------------------------------------------------------
# PROV-04: producer binding gates the supported verdict
# ---------------------------------------------------------------------

def test_unverified_local_artifact_cannot_reach_supported():
    """An adapter-built artifact run WITHOUT its producer payload is
    demoted to descriptive even though the evidence surface
    qualifies — the supported verdict is unreachable for a local
    artifact."""
    events = fx.make_events(21)
    artifact, _payload = fx.planted_artifact_and_payload(events)
    report = _run(artifact, _lane(events), events)
    assert report.binding == BINDING_UNVERIFIED
    assert report.status == "DESCRIPTIVE_REGIME_ONLY"
    assert report.problems() == []
    # the demoted report cannot be promoted by editing the status —
    # problems() blocks a supported verdict without a verified
    # binding
    promoted = dataclasses.replace(
        report, status="REGIME_ASSOCIATION_SUPPORTED")
    assert any("verified_producer_payload" in p
               for p in promoted.problems())


def test_hand_built_artifact_never_supported():
    """The audit-confirmed residual: a hand-built artifact whose
    ``producer_payload_digest`` is a bare self-chosen hash passes the
    shape floor but must never carry the supported verdict."""
    events = fx.make_events(21)
    artifact, _payload = fx.planted_artifact_and_payload(events)
    forged = dataclasses.replace(
        artifact,
        producer_payload_digest=hashlib.sha256(b"x").hexdigest())
    assert forged.problems() == []
    report = _run(forged, _lane(events), events)
    assert report.binding == BINDING_UNVERIFIED
    assert report.status != "REGIME_ASSOCIATION_SUPPORTED"
    assert report.problems() == []


def test_verified_payload_binding_reaches_supported():
    """The same artifact with its matching serialized producer
    payload verifies end-to-end and the supported verdict is
    reachable."""
    events = fx.make_events(21)
    artifact, payload = fx.planted_artifact_and_payload(events)
    report = _run(artifact, _lane(events), events,
                  producer_payload=payload)
    assert report.binding == BINDING_VERIFIED
    assert report.status == "REGIME_ASSOCIATION_SUPPORTED"
    assert report.problems() == []
    # the manifest binds the artifact digests into the report
    assert report.inputs["regime_digest"] == artifact.regime_digest
    assert report.inputs["assignment_digest"] == \
        artifact.assignment_digest
    assert report.inputs["producer_payload_digest"] == \
        artifact.producer_payload_digest
    assert report.input_digest == sha256_canonical(report.inputs)


def test_mismatched_freeze_digest_rejected():
    """A supplied payload that is internally consistent but binds a
    DIFFERENT artifact rejects outright — a forged binding is a
    binding violation, never a descriptive fallback."""
    events = fx.make_events(21)
    artifact, _payload = fx.planted_artifact_and_payload(events)
    other_payload = fx.planted_artifact_payload(
        fx.make_events(14))
    assert other_payload["freeze_digest"] != \
        artifact.producer_payload_digest
    with pytest.raises(ValueError, match="producer-payload"):
        _run(artifact, _lane(events), events,
             producer_payload=other_payload)


def test_tampered_payload_digest_chain_rejected():
    """A payload mutated after freeze — assignment row edited, digest
    left stale — fails the recomputed freeze chain."""
    events = fx.make_events(21)
    artifact, payload = fx.planted_artifact_and_payload(events)
    payload = dict(payload)
    payload["assignments"] = list(payload["assignments"])
    row = list(payload["assignments"][0])
    row[2] = (row[2] + 1) % 5
    payload["assignments"][0] = row
    with pytest.raises(ValueError, match="producer-payload"):
        _run(artifact, _lane(events), events,
             producer_payload=payload)
    # a well-formed but wrong carried freeze_digest also rejects
    artifact2 = dataclasses.replace(
        artifact, producer_payload_digest="0" * 64)
    good_payload = fx.planted_artifact_payload(events)
    with pytest.raises(ValueError, match="producer-payload"):
        _run(artifact2, _lane(events), events,
             producer_payload=good_payload)


def test_nonmapping_producer_payload_rejected():
    events = fx.make_events(21)
    artifact, _payload = fx.planted_artifact_and_payload(events)
    with pytest.raises(ValueError, match="producer-payload"):
        _run(artifact, _lane(events), events,
             producer_payload=[1, 2, 3])


# ---------------------------------------------------------------------
# Artifact self-digest: assignment_digest is recomputably bound
# ---------------------------------------------------------------------

def test_assignment_digest_recomputed_in_problems():
    events = fx.make_events(6)
    artifact, _payload = fx.planted_artifact_and_payload(events)
    assert artifact.assignment_digest
    assert artifact.problems() == []
    # a stamped digest that disagrees with the rows is tampering
    bad = dataclasses.replace(artifact, assignment_digest="0" * 64)
    assert any("assignment_digest" in p for p in bad.problems())
    # the digest is order-canonical: permuted rows recompute equal
    rev = dataclasses.replace(
        artifact, assignment_digest="",
        assignments=tuple(reversed(artifact.assignments)))
    assert rev.assignment_digest == artifact.assignment_digest
    assert rev.problems() == []
    # to_dict/from_dict round-trip carries the field
    clone = RegimeAssignmentArtifact.from_dict(artifact.to_dict())
    assert clone.assignment_digest == artifact.assignment_digest


# ---------------------------------------------------------------------
# ASSOC-03: input_digest + manifest revalidation, every status
# ---------------------------------------------------------------------

def test_input_digest_binds_inputs():
    """A report claiming different inputs digests differently."""
    events = fx.make_events(21)
    artifact, payload = fx.planted_artifact_and_payload(events)
    lane = _lane(events)
    base = _run(artifact, lane, events, producer_payload=payload)
    # one extra admitted event changes the manifest
    extra = fx.make_event("ev-extra", "karnali",
                          fx._BASE_DATE)
    events2 = events + [extra]
    lane2 = {"controls": lane["controls"],
             "holdout": fx.make_holdout(events2),
             "opportunities": lane["opportunities"]}
    other = _run(artifact, lane2, events2,
                 producer_payload=payload)
    assert base.input_digest != other.input_digest
    assert base.inputs["event_digests"] != \
        other.inputs["event_digests"]
    # a different seed binds differently too
    reseeded = _run(artifact, lane, events, seed=_SEED + 1,
                    producer_payload=payload)
    assert base.input_digest != reseeded.input_digest


def test_tampered_input_digest_flagged():
    """problems() recomputes the carried manifest — any tampering of
    digest, manifest, or manifest-vs-report consistency surfaces."""
    events = fx.make_events(21)
    artifact, payload = fx.planted_artifact_and_payload(events)
    rep = _run(artifact, _lane(events), events,
               producer_payload=payload)
    assert rep.status == "REGIME_ASSOCIATION_SUPPORTED"
    assert rep.problems() == []
    # well-formed but wrong digest
    probs = dataclasses.replace(
        rep, input_digest="0" * 64).problems()
    assert any("input_digest" in p for p in probs)
    # malformed digest
    probs = dataclasses.replace(
        rep, input_digest="not-a-digest").problems()
    assert any("input_digest" in p for p in probs)
    # stripped manifest
    probs = dataclasses.replace(rep, inputs={}).problems()
    assert any("inputs" in p for p in probs)
    # manifest mutated under a stale digest
    bad_inputs = dict(rep.inputs)
    bad_inputs["seed"] = rep.inputs["seed"] + 1
    probs = dataclasses.replace(rep, inputs=bad_inputs).problems()
    assert any("input_digest" in p or "inputs" in p
               for p in probs)
    # manifest/report field disagreement
    probs = dataclasses.replace(
        rep, regime_digest="f" * 64).problems()
    assert probs


def test_tampered_descriptive_report_flagged():
    """A non-SUPPORTED report is revalidated too — tampered carried
    digests, stripped sensitivity dispositions, and unverified
    bindings all fail problems()."""
    events = fx.make_events(21)
    artifact, _payload = fx.planted_artifact_and_payload(events)
    rep = _run(artifact, _lane(events), events)
    assert rep.status == "DESCRIPTIVE_REGIME_ONLY"
    assert rep.binding == BINDING_UNVERIFIED
    assert rep.problems() == []
    # tamper a carried negative-control digest payload
    neg = dict(rep.negative_controls)
    bad_shift = dict(neg["spatial_shift"])
    bad_shift["n_shifts"] = bad_shift["n_shifts"] + 1
    neg["spatial_shift"] = bad_shift
    probs = dataclasses.replace(
        rep, negative_controls=neg).problems()
    assert any("spatial_shift" in p and "digest" in p
               for p in probs)
    # strip a sensitivity disposition
    sens = dict(rep.sensitivities)
    sens.pop("mechanism")
    probs = dataclasses.replace(
        rep, sensitivities=sens).problems()
    assert any("mechanism" in p for p in probs)
    # a bogus binding mode is rejected for every status
    probs = dataclasses.replace(
        rep, binding="self_attested").problems()
    assert any("binding" in p for p in probs)
    # a hand-built report with no input binding fails
    hollow = dataclasses.replace(rep, inputs={}, input_digest="")
    assert hollow.problems()


def test_binding_vocabulary_is_closed():
    assert ASSOCIATION_BINDINGS == frozenset(
        {"verified_producer_payload",
         "local_artifact_unverified"})
    report_fields = {f.name for f in
                     dataclasses.fields(AssociationReport)}
    assert {"binding", "inputs", "input_digest"} <= report_fields
