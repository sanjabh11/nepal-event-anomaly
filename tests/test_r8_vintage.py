"""Round-8 vintage byte-binding tests (C14 — synthetic fixtures only).

Covers the ForecastVintageV0 evidence-root contract:

* ``build_vintage`` metadata admission yields a *candidate* vintage —
  the audit requires candidates to stay candidates — while
  ``require_bytes=True`` fails closed unless ``evidence_root`` names a
  real directory and both declared files hash to the declared digests.
* ``verify_vintage_evidence`` byte-verifies the declared
  ``archive_payload_path`` / ``retrieval_record_path`` under the
  evidence root: real bytes must match declared 64-hex digests;
  missing files, flipped bytes, wrong declared digests, ``..``
  escapes, absolute paths, and symlinks all reject.
* ``evaluate(..., require_vintage_bytes=True)`` fails closed on
  metadata-only vintages — a candidate can flow through descriptive
  evaluation but cannot ground a forecast-ready claim; byte-bound
  vintages can.

Nothing here is a real-data finding — all bytes are fabricated test
content written under ``tmp_path``.
"""
from __future__ import annotations

import dataclasses
import hashlib

import pytest

from nepal.experiment_v0.evaluation import (
    ForecastExperimentDeclaration, evaluate)
from nepal.experiment_v0.vintages import (VintageRequest, build_vintage)
from nepal.research_v0._hashing import (sha256_canonical,
                                      verify_vintage_evidence)
from nepal.research_v0.records import ForecastVintageV0

from tests.fixtures import synthetic_exp_b3 as fx
from tests.fixtures.synthetic_exp_b2 import synthetic_vintage_request


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_evidence(root, req: VintageRequest) -> None:
    """Materialize the declared payload/retrieval files under ``root``
    so their bytes hash to the request's declared digests — the
    fixture digests are ``_sha("<provider>:payload-bytes")`` and
    ``_sha("<provider>:retrieval-record")``."""
    payload = root / req.archive_payload_path
    payload.parent.mkdir(parents=True, exist_ok=True)
    payload.write_bytes(f"{req.provider}:payload-bytes".encode())
    retrieval = root / req.retrieval_record_path
    retrieval.parent.mkdir(parents=True, exist_ok=True)
    retrieval.write_bytes(
        f"{req.provider}:retrieval-record".encode())


def _eval_kwargs(design, cases=None, admitted=None):
    cases = design["cases"] if cases is None else cases
    return {"holdout": design["holdout"],
            "baseline_probs": fx.make_baseline_probs(cases),
            "admitted_vintages": (fx.admitted_for_cases(cases)
                                  if admitted is None else admitted),
            "opportunities": design.get(
                "opportunities", fx.opportunity_registry(cases)),
            "unit_basins": design.get(
                "unit_basins", fx.unit_basins_for(cases)),
            "region_basins": design.get(
                "region_basins",
                fx.region_basins_for(
                    sorted({c.region for c in cases}))),
            "n_boot": 50, "seed": 7}


def _bound_evidence(cases):
    probs = fx.make_baseline_probs(cases)
    return {name: {"digest": sha256_canonical(
                       [round(float(v), 9) for v in vec]),
                   "fit_provenance": "bound"}
            for name, vec in probs.items()}


def _bind_real_bytes(cases, root):
    """Write real evidence files under ``root`` for every region's
    synthetic vintage and rebind cases + the admitted map to the
    byte-bound records — the canonical vintage digest covers
    ``evidence_root``, so both sides of the binding change together.
    """
    admitted = {}
    digest_of = {}
    for region in sorted({c.region for c in cases}):
        vintage = fx.synthetic_vintage(region)
        for rel, content in ((vintage.archive_payload_path,
                              f"payload:{region}"),
                             (vintage.retrieval_record_path,
                              f"retrieval:{region}")):
            fpath = root / rel
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_bytes(content.encode("utf-8"))
        bound = dataclasses.replace(vintage, evidence_root=str(root))
        digest = sha256_canonical(bound.to_dict())
        admitted[digest] = bound
        digest_of[region] = digest
    rebound = [dataclasses.replace(c,
                                   vintage_digest=digest_of[c.region])
               for c in cases]
    return rebound, admitted


class TestByteBoundAdmission:
    def test_forecast_vintage_requires_bound_evidence_root_for_ready_status(
            self, tmp_path):
        # Default metadata admission still yields a candidate.
        req = VintageRequest(**synthetic_vintage_request())
        vintage = build_vintage(req, "vint-c14")
        assert type(vintage) is ForecastVintageV0
        assert vintage.evidence_root == ""
        # The same request can never be byte-bound: no evidence_root.
        with pytest.raises(ValueError, match="evidence_root"):
            build_vintage(req, "vint-c14", require_bytes=True)
        # A declared root whose payload file does not exist also fails
        # closed — declared digests over absent bytes are not evidence.
        req_missing = VintageRequest(**synthetic_vintage_request(
            evidence_root=str(tmp_path)))
        with pytest.raises(ValueError, match="payload"):
            build_vintage(req_missing, "vint-c14-missing",
                          require_bytes=True)
        # ... yet still admits as a candidate without the byte gate.
        candidate = build_vintage(req_missing, "vint-c14-candidate")
        assert candidate.evidence_root == str(tmp_path)

    def test_forecast_payload_hashes_actual_bytes(self, tmp_path):
        req = VintageRequest(**synthetic_vintage_request(
            evidence_root=str(tmp_path)))
        _write_evidence(tmp_path, req)
        vintage = build_vintage(req, "vint-c14-bytes",
                                require_bytes=True)
        assert vintage.evidence_root == str(tmp_path)
        assert verify_vintage_evidence(vintage) == []
        assert verify_vintage_evidence(vintage.to_dict()) == []
        # Flip one payload byte — the declared digest no longer
        # matches the file.
        payload = tmp_path / req.archive_payload_path
        data = bytearray(payload.read_bytes())
        data[0] ^= 0xFF
        payload.write_bytes(bytes(data))
        problems = verify_vintage_evidence(vintage, str(tmp_path))
        assert any("sha256 mismatch" in p for p in problems)

    def test_symlink_and_outside_root_rejected(self, tmp_path):
        root = tmp_path / "evidence"
        root.mkdir()
        outside = tmp_path / "outside.bin"
        outside.write_bytes(b"outside-bytes")
        outside_sha = _sha("outside-bytes")

        # '..' escape: the declared path resolves outside the root
        # even though the target file exists and hashes correctly.
        req_escape = VintageRequest(**synthetic_vintage_request(
            evidence_root=str(root),
            archive_payload_path="../outside.bin",
            archive_payload_sha256=outside_sha))
        problems = verify_vintage_evidence(req_escape, str(root))
        assert any("outside evidence_root" in p for p in problems)

        # A symlink inside the root pointing outside rejects — the
        # symlink's target bytes are not the artifact the caller named.
        (root / "payload-link.bin").symlink_to(outside)
        retrieval = root / "retrieval.json"
        retrieval.write_bytes(b"retrieval-bytes")
        req_link = VintageRequest(**synthetic_vintage_request(
            evidence_root=str(root),
            archive_payload_path="payload-link.bin",
            archive_payload_sha256=outside_sha,
            retrieval_record_path="retrieval.json",
            retrieval_record_sha256=_sha("retrieval-bytes")))
        problems = verify_vintage_evidence(req_link, str(root))
        assert any("symlink" in p for p in problems)

    def test_tampered_retrieval_record_rejected(self, tmp_path):
        req = VintageRequest(**synthetic_vintage_request(
            evidence_root=str(tmp_path)))
        _write_evidence(tmp_path, req)
        # Correct payload, tampered retrieval record bytes.
        (tmp_path / req.retrieval_record_path).write_bytes(
            b"tampered retrieval record")
        problems = verify_vintage_evidence(req, str(tmp_path))
        assert problems
        assert any("retrieval_record" in p and "sha256 mismatch" in p
                   for p in problems)

    def test_wrong_declared_digest_rejected(self, tmp_path):
        # Real file on disk, but the declared digest is a different
        # valid 64-hex — a claim over bytes that were never these.
        req = VintageRequest(**synthetic_vintage_request(
            evidence_root=str(tmp_path),
            archive_payload_sha256="0" * 64))
        _write_evidence(tmp_path, req)
        problems = verify_vintage_evidence(req, str(tmp_path))
        assert any("sha256 mismatch" in p for p in problems)


class TestEvaluationBoundary:
    def test_metadata_only_request_cannot_enter_forecast_ready_status(
            self, tmp_path):
        design = fx.underpowered_design(vintage_byte_bound=False)
        cases = design["cases"]
        kwargs = _eval_kwargs(
            design,
            admitted=fx.admitted_for_cases(cases, byte_bound=False))
        # Candidate flow-through: metadata-only vintages still support
        # descriptive evaluation.
        report = evaluate(cases, **kwargs)
        assert report.status == "UNDERPOWERED_DESCRIPTIVE_ONLY"
        # Under the byte gate the same metadata-only vintages fail
        # closed — they can never ground a forecast-ready evaluation.
        with pytest.raises(ValueError, match="evidence_root"):
            evaluate(cases, require_vintage_bytes=True, **kwargs)

    def test_metadata_only_vintages_cap_forecast_status(self):
        """C14 status-level gate: even a powered design carrying a
        complete bound declaration cannot emit
        FORECAST_EXPERIMENT_ONLY over metadata-only vintages — byte
        evidence is required by the status itself, not only by the
        require_vintage_bytes admission flag."""
        design = fx.powered_design(vintage_byte_bound=False)
        cases = design["cases"]
        kwargs = _eval_kwargs(
            design,
            admitted=fx.admitted_for_cases(cases, byte_bound=False))
        kwargs["baseline_evidence"] = _bound_evidence(cases)
        row_keys = fx.feature_row_keys_for(cases)
        kwargs["feature_row_keys"] = row_keys
        kwargs["experiment"] = ForecastExperimentDeclaration(
            declaration_id="decl-c14-meta",
            feature_artifact_digest="a" * 64,
            threshold_record={"threshold": 0.5},
            ablations=("model",),
            vintage_lineage=tuple(
                sorted({c.vintage_digest for c in cases})),
            forecast_regime_digest="c" * 64,
            feature_row_keys_digest=sha256_canonical(sorted(row_keys)))
        report = evaluate(cases, **kwargs)
        assert report.power["powered"] is True
        assert report.status == "UNDERPOWERED_DESCRIPTIVE_ONLY"

    def test_claimed_byte_binding_failure_always_rejects(
            self, tmp_path):
        # Even without require_vintage_bytes, a declared evidence_root
        # whose bytes fail verification is tamper evidence, not a
        # candidate.
        design = fx.underpowered_design()
        cases = design["cases"]
        admitted = {}
        for key, vintage in fx.admitted_for_cases(cases).items():
            claimed = dataclasses.replace(
                vintage, evidence_root=str(tmp_path))
            admitted[sha256_canonical(claimed.to_dict())] = claimed
        rebound = [dataclasses.replace(
            c, vintage_digest=sha256_canonical(
                dataclasses.replace(
                    fx.synthetic_vintage(c.region),
                    evidence_root=str(tmp_path)).to_dict()))
            for c in cases]
        kwargs = _eval_kwargs(design, cases=rebound,
                              admitted=admitted)
        with pytest.raises(ValueError, match="vintage"):
            evaluate(rebound, **kwargs)

    def test_byte_bound_vintages_ground_forecast_ready_status(
            self, tmp_path):
        design = fx.powered_design()
        cases, admitted = _bind_real_bytes(design["cases"], tmp_path)
        kwargs = _eval_kwargs(design, cases=cases, admitted=admitted)
        kwargs["baseline_evidence"] = _bound_evidence(cases)
        row_keys = fx.feature_row_keys_for(cases)
        kwargs["feature_row_keys"] = row_keys
        kwargs["experiment"] = ForecastExperimentDeclaration(
            declaration_id="decl-c14",
            feature_artifact_digest="a" * 64,
            threshold_record={"threshold": 0.5},
            ablations=("model",),
            vintage_lineage=tuple(
                sorted({c.vintage_digest for c in cases})),
            forecast_regime_digest="c" * 64,
            feature_row_keys_digest=sha256_canonical(sorted(row_keys)))
        report = evaluate(cases, require_vintage_bytes=True, **kwargs)
        assert report.status == "FORECAST_EXPERIMENT_ONLY"
