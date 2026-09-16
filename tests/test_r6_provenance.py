"""Round-6 provenance-hardening tests (swarm B surface).

One behavioral test per closed residual:

* PROV-02a — ``regime_assignment_from_artifact`` re-digests every raw
  bound section the payload carries (``config`` -> ``config_digest``,
  decoded ``input_values`` -> ``input_bytes_digest``, ``preprocessing``
  -> ``preprocessing_digest``) exactly as
  ``science_v0.freeze_regime_artifact`` does — a payload freeze would
  reject must be rejected at the adapter boundary.
* PROV-02b — producer-payload audit findings block the association
  lane in ``replay_bundle`` (``REPLAY_FAILED``, never a replayed
  status); the forecast lane is unaffected.
* PROV-03 — non-fixture ``source_manifest`` declarations are
  byte-verified under ``evidence_root`` via
  ``research_v0._hashing.verify_source_evidence``.
* REG-05b — a carried null-family ``family_digest`` must recompute
  over the serialized replicate records; tampered replicates surface
  as findings and block replay.
* FCST-01 — ``RegimeArtifactV0`` mode contract: retrospective mode
  requires ``REANALYSIS`` and forbids forecast bindings; forecast mode
  requires a forecast archive data class plus the vintage-digest and
  feature-set bindings; the adapter never associates a
  ``FORECAST_REGIME`` artifact.
* PROV-04 — the adapter stamps ``producer_payload_digest`` from
  ``freeze_digest`` and replay forwards the verified payload to
  ``run_association`` when its signature binds it.
"""
from __future__ import annotations

import copy
import hashlib
import inspect

import numpy as np
import pytest

from nepal.experiment_v0 import audit as audit_mod
from nepal.experiment_v0.adapters import regime_assignment_from_artifact
from nepal.experiment_v0.audit import (audit_pipeline,
                                     audit_producer_payload,
                                     replay_bundle)
from nepal.experiment_v0.association import (ASSOCIATION_STATUSES,
                                             run_association)
from nepal.research_v0._hashing import sha256_canonical
from nepal.research_v0.gates import REQUIRED_REGIME_GATE_NAMES
from nepal.research_v0.records import RegimeArtifactV0
from nepal.science_v0.regimes import freeze_regime_artifact

from tests.fixtures import synthetic_exp_b4 as fx

_SEEDS = [7, 42, 2024]
_INPUT_VALUES = [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]


# ---------------------------------------------------------------------
# Payload builders — honest digests throughout
# ---------------------------------------------------------------------

def _null_family(family: str, seeds, stats) -> dict:
    """A producer-shaped serialized null-family record whose
    ``family_digest`` is computed exactly as ``run_regimes`` binds
    it: sha256_canonical over the documented dict."""
    replicates = [
        {"i": i, "gen_seed": int(seeds[0]) + 1000003 * (i + 1),
         "fit_seed": int(seeds[i % len(seeds)]),
         "k": 2, "stat": s, "ok": True}
        for i, s in enumerate(stats)]
    rec = {"statistic": "silhouette", "observed": 0.6,
           "n_replicates": len(replicates),
           "n_succeeded": len(replicates), "n_failed": 0,
           "p_value": 0.02, "alpha": 0.05, "status": "PASS",
           "reason": None,
           "selection": "bic_sweep_declared_candidates",
           "null_k_distribution": {"2": len(replicates)},
           "null_stat_min": min(stats), "null_stat_max": max(stats),
           "replicates": replicates}
    rec["family_digest"] = sha256_canonical({
        "family": family, "seed_cycle": list(seeds),
        "n_replicates": rec["n_replicates"],
        "statistic": rec["statistic"], "p_value": rec["p_value"],
        "observed": rec.get("observed"),
        "alpha": rec.get("alpha"),
        "n_succeeded": rec.get("n_succeeded"),
        "n_failed": rec.get("n_failed"),
        "status": rec.get("status"),
        "reason": rec.get("reason"),
        "selection": rec.get("selection"),
        "null_stat_min": rec["null_stat_min"],
        "null_stat_max": rec["null_stat_max"],
        "null_k_distribution": rec["null_k_distribution"],
        "replicates": rec["replicates"]})
    return rec


def _producer_payload(**overrides) -> dict:
    """A frozen producer-shaped payload carrying the FULL canonical
    schema — including the raw bound sections (``config``,
    ``input_values``, ``preprocessing``) and honestly-digested null
    families — so every recomputed binding is exercised."""
    arr = np.ascontiguousarray(
        np.asarray(_INPUT_VALUES, dtype=np.float64))
    assignments = [["u1", "2020-06-01", 0], ["u1", "2020-06-02", 1],
                   ["u2", "2020-06-01", 0]]
    stability = {"seed_ari_min": 0.91, "modal_k_frequency": 1.0,
                 "required_gates": {g: True for g in
                                    sorted(REQUIRED_REGIME_GATE_NAMES)}}
    preprocessing = {"imputer_strategy": "median",
                     "imputer_statistics": [0.0, 0.0],
                     "scaler_mean": [0.0, 0.0],
                     "scaler_var": [1.0, 1.0],
                     "feature_order": ["f1", "f2"],
                     "row_keys_digest": "a" * 64,
                     "train_mask_membership_digest": "b" * 64}
    art = {
        "mode": "RETROSPECTIVE_REGIME", "data_class": "REANALYSIS",
        "fitted_on": "TRAIN_ONLY", "label_blinding": True,
        "k": 2, "seeds": list(_SEEDS), "seeds_declared": list(_SEEDS),
        "seed_coverage": {str(s): "converged" for s in _SEEDS},
        "per_seed_best_k": {str(s): 2 for s in _SEEDS},
        "modal_k_frequency": 1.0,
        "occupancy": [0.6, 0.4],
        "assignments": assignments,
        "assignment_digest": sha256_canonical(assignments),
        "feature_cols": ["f1", "f2"],
        "feature_matrix_digest": "c" * 64,
        "input_values": copy.deepcopy(_INPUT_VALUES),
        "input_bytes_digest": hashlib.sha256(
            arr.tobytes()).hexdigest(),
        "input_schema": {"feature_cols": ["f1", "f2"], "n_rows": 3,
                         "dtypes": {"f1": "float64", "f2": "float64"},
                         "shape": [3, 2]},
        "config": {"seeds": list(_SEEDS), "k_candidates": [1, 2, 3],
                   "null_alpha": 0.05, "cadence": "1D",
                   "gap_policy": "calendar", "bootstrap_block_len": 7,
                   "missingness_policy": "listwise",
                   "effort_split": "median",
                   "mode": "RETROSPECTIVE_REGIME"},
        "model": {"weights": [0.6, 0.4],
                  "means": [[1.0, 2.0], [4.0, 5.0]],
                  "covariances": [[[0.25, 0.0], [0.0, 0.25]],
                                  [[0.5, 0.0], [0.0, 0.5]]]},
        "fit_groups": ["g0", "g1", "g2"],
        "heldout_groups_declared": ["g3"],
        "unit_basin_map": [["u1", "g0"], ["u2", "g1"]],
        "run_manifest": {
            "run_id": "prov-test-run-001",
            "worker_id": "test-worker",
            "created_at": "2020-06-02T00:00:00Z",
            "environment_digest": "e" * 64,
            "seed": 11,
            "input_digests": ["c" * 64],
            "output_digests": ["d" * 64],
            "checkpoint_policy": "atomic_publish_or_quarantine",
            "status": "COMPLETED"},
        "n_train_rows": 3, "n_rows": 3,
        "train_mask_digest": "d" * 64,
        "stability": stability,
        "nulls": {"statistic": "silhouette", "observed": 0.6,
                  "alpha": 0.05, "n_replicates": 4,
                  "season_era_stratified": False,
                  "shuffled": _null_family(
                      "shuffled", _SEEDS, [0.10, 0.20, 0.15, 0.05]),
                  "season_matched": _null_family(
                      "season_matched", _SEEDS,
                      [0.30, 0.25, 0.20, 0.10])},
        "preprocessing": preprocessing,
        "missingness_applied": {"policy": "listwise",
                                "train_rows_total": 3,
                                "train_rows_fitted": 3,
                                "train_rows_dropped": 0},
        "status": "DESCRIPTIVE_REGIME_ONLY",
        "terminal": True, "associable": True,
        "source_manifest": {"fixture": True},
        "environment_digest": "e" * 64,
        "run_manifest_digest": sha256_canonical({
            "run_id": "prov-test-run-001",
            "worker_id": "test-worker",
            "created_at": "2020-06-02T00:00:00Z",
            "environment_digest": "e" * 64,
            "seed": 11,
            "input_digests": ["c" * 64],
            "output_digests": ["d" * 64],
            "checkpoint_policy": "atomic_publish_or_quarantine",
            "status": "COMPLETED"}),
        "disclaimer": "synthetic fixture — interface evidence only",
    }
    art["config_digest"] = sha256_canonical(art["config"])
    art["preprocessing_digest"] = sha256_canonical(preprocessing)
    art["k_selection_digest"] = "1" * 64
    art["stability_report_digest"] = sha256_canonical(stability)
    art["null_model_digest"] = "2" * 64
    art["regime_artifact_digest"] = sha256_canonical(art)
    art["freeze_digest"] = sha256_canonical(dict(art))
    art["frozen"] = True
    art.update(overrides)
    return art


def _repair_outer_digests(payload: dict) -> dict:
    """Recompute only the envelope digests (regime_artifact_digest +
    freeze_digest) so a probe can corrupt ONE inner binding while
    the envelope stays self-consistent."""
    payload["regime_artifact_digest"] = sha256_canonical(
        {k: v for k, v in payload.items()
         if k not in ("regime_artifact_digest", "freeze_digest",
                      "frozen")})
    payload["freeze_digest"] = sha256_canonical(
        {k: v for k, v in payload.items()
         if k not in ("freeze_digest", "frozen")})
    return payload


# ---------------------------------------------------------------------
# PROV-02a — adapter/freeze parity on the raw bound sections
# ---------------------------------------------------------------------

class TestProv02aAdapterFreezeParity:
    def test_full_payload_adapts_and_audits_clean(self):
        payload = _producer_payload()
        rec = regime_assignment_from_artifact(
            payload, artifact_id="ra-r6")
        assert rec.problems() == []
        assert rec.producer_payload_digest == payload["freeze_digest"]
        assert audit_producer_payload(payload) == []

    def test_config_content_tamper_rejected(self):
        """Mutating the carried config while keeping a stale
        config_digest must reject — exactly what freeze does."""
        p = _producer_payload()
        p["config"]["null_alpha"] = 0.99
        _repair_outer_digests(p)
        with pytest.raises(ValueError, match="config_digest"):
            regime_assignment_from_artifact(p, artifact_id="x")

    def test_config_digest_forged_rejected(self):
        p = _producer_payload()
        p["config_digest"] = "9" * 64
        _repair_outer_digests(p)
        with pytest.raises(ValueError, match="config_digest"):
            regime_assignment_from_artifact(p, artifact_id="x")

    def test_input_values_tamper_rejected(self):
        p = _producer_payload()
        p["input_values"][0][0] = 9.5
        _repair_outer_digests(p)
        with pytest.raises(ValueError, match="input_bytes_digest"):
            regime_assignment_from_artifact(p, artifact_id="x")

    def test_input_bytes_digest_forged_rejected(self):
        p = _producer_payload()
        p["input_bytes_digest"] = "0" * 64
        _repair_outer_digests(p)
        with pytest.raises(ValueError, match="input_bytes_digest"):
            regime_assignment_from_artifact(p, artifact_id="x")

    def test_input_values_undecodable_rejected(self):
        p = _producer_payload()
        p["input_values"] = [["bogus", 1.0], [2.0, 3.0],
                             [4.0, 5.0]]
        _repair_outer_digests(p)
        with pytest.raises(ValueError, match="input_values"):
            regime_assignment_from_artifact(p, artifact_id="x")

    def test_input_values_shape_mismatch_rejected(self):
        p = _producer_payload()
        p["input_schema"]["shape"] = [4, 2]
        _repair_outer_digests(p)
        with pytest.raises(ValueError, match="input_schema"):
            regime_assignment_from_artifact(p, artifact_id="x")

    def test_input_values_nonfinite_tokens_roundtrip(self):
        """The producer's token encoding (None=NaN, 'Infinity')
        decodes to the same float64 byte domain the digest binds."""
        vals = [[1.0, None], ["Infinity", -2.5], [0.0, "-Infinity"]]
        arr = np.asarray([[1.0, np.nan], [np.inf, -2.5],
                          [0.0, -np.inf]], dtype=np.float64)
        p = _producer_payload()
        p["input_values"] = copy.deepcopy(vals)
        p["input_schema"]["shape"] = [3, 2]
        p["input_schema"]["n_rows"] = 3
        p["n_rows"] = 3
        p["input_bytes_digest"] = hashlib.sha256(
            np.ascontiguousarray(arr).tobytes()).hexdigest()
        _repair_outer_digests(p)
        rec = regime_assignment_from_artifact(p, artifact_id="ra-tok")
        assert rec.problems() == []

    def test_preprocessing_tamper_rejected(self):
        p = _producer_payload()
        p["preprocessing"]["scaler_mean"] = [1.0, 1.0]
        _repair_outer_digests(p)
        with pytest.raises(ValueError, match="preprocessing_digest"):
            regime_assignment_from_artifact(p, artifact_id="x")

    def test_preprocessing_without_row_keys_rejected(self):
        p = _producer_payload()
        del p["preprocessing"]["row_keys_digest"]
        _repair_outer_digests(p)
        with pytest.raises(ValueError, match="row_keys_digest"):
            regime_assignment_from_artifact(p, artifact_id="x")

    def test_parity_with_freeze_regime_artifact(self):
        """The same payload must pass freeze and the adapter; the
        same tamper must fail both."""
        p = _producer_payload()
        pre_freeze = {k: v for k, v in p.items()
                      if k not in ("frozen", "freeze_digest")}
        frozen = freeze_regime_artifact(copy.deepcopy(pre_freeze))
        assert frozen["frozen"] is True
        rec = regime_assignment_from_artifact(
            frozen, artifact_id="ra-parity")
        assert rec.problems() == []

        bad = _producer_payload()
        bad["config"]["null_alpha"] = 0.5
        bad_pre = {k: v for k, v in bad.items()
                   if k not in ("frozen", "freeze_digest")}
        with pytest.raises(ValueError, match="config_digest"):
            freeze_regime_artifact(bad_pre)
        with pytest.raises(ValueError, match="config_digest"):
            regime_assignment_from_artifact(bad, artifact_id="x")


# ---------------------------------------------------------------------
# PROV-02b — producer findings block the association lane
# ---------------------------------------------------------------------

class TestProv02bProducerFindingsBlockReplay:
    @pytest.mark.parametrize(
        "field", ["model", "source_manifest", "seed_coverage"])
    def test_missing_producer_field_blocks_lane(self, field):
        bundle = fx.synthetic_bundle()
        del bundle["association"]["artifact_payload"][field]
        result = replay_bundle(bundle)
        assert result["association_status"] == "REPLAY_FAILED"
        assert any("PRODUCER_PROVENANCE_MISSING" in p
                   for p in result["problems"])
        # the forecast lane is isolated — it still replays
        assert result["evaluation_status"] in (
            "FORECAST_EXPERIMENT_ONLY",
            "UNDERPOWERED_DESCRIPTIVE_ONLY")

    def test_open_gate_under_descriptive_status_blocks_lane(self):
        bundle = fx.synthetic_bundle()
        payload = bundle["association"]["artifact_payload"]
        payload["stability"]["required_gates"][
            "season_matched_null"] = False
        result = replay_bundle(bundle)
        assert result["association_status"] == "REPLAY_FAILED"
        assert any("PRODUCER_GATE_BYPASSED" in p
                   for p in result["problems"])

    def test_finding_still_lands_in_problems(self):
        bundle = fx.synthetic_bundle()
        del bundle["association"]["artifact_payload"]["model"]
        result = replay_bundle(bundle)
        assert any("'model'" in p for p in result["problems"])

    def test_audit_pipeline_reports_same_findings(self):
        bundle = fx.synthetic_bundle()
        del bundle["association"]["artifact_payload"]["model"]
        findings = audit_pipeline(bundle)
        assert any(f.code == "PRODUCER_PROVENANCE_MISSING"
                   for f in findings)

    def test_clean_bundle_still_replays(self):
        result = replay_bundle(fx.synthetic_bundle())
        assert result["problems"] == []
        assert result["association_status"] in ASSOCIATION_STATUSES
        assert len(result["association_digest"]) == 64


# ---------------------------------------------------------------------
# PROV-03 — byte-verified source evidence under evidence_root
# ---------------------------------------------------------------------

def _verify_source_evidence():
    import nepal.research_v0._hashing as hashing
    fn = getattr(hashing, "verify_source_evidence", None)
    if fn is None:
        pytest.skip("nepal.research_v0._hashing."
                    "verify_source_evidence not yet landed "
                    "(research_v0 contract pending)")
    return fn


class TestProv03SourceEvidence:
    def _payload_with_manifest(self, manifest: dict) -> dict:
        payload = _producer_payload()
        payload["source_manifest"] = manifest
        _repair_outer_digests(payload)
        return payload

    def _manifest(self, root, sha: str) -> dict:
        return {"source_id": "real_src_v0",
                "source_digests": [sha],
                "units": {"f1": "mm", "f2": "degC"},
                "feature_allowlist": ["f1", "f2"],
                "lineage": {"fetch": "synthetic-test"},
                "evidence_root": str(root),
                "source_files": [
                    {"relpath": "source_a.csv", "sha256": sha}]}

    def test_correct_evidence_bytes_clean(self, tmp_path):
        _verify_source_evidence()
        root = tmp_path / "evidence"
        root.mkdir()
        blob = b"synthetic source bytes\n"
        (root / "source_a.csv").write_bytes(blob)
        sha = hashlib.sha256(blob).hexdigest()
        payload = self._payload_with_manifest(
            self._manifest(root, sha))
        assert audit_producer_payload(payload) == []

    def test_wrong_source_file_digest_flagged(self, tmp_path):
        _verify_source_evidence()
        root = tmp_path / "evidence"
        root.mkdir()
        blob = b"synthetic source bytes\n"
        (root / "source_a.csv").write_bytes(blob)
        # declared digest does not match the file bytes
        manifest = self._manifest(root, "0" * 64)
        payload = self._payload_with_manifest(manifest)
        findings = audit_producer_payload(payload)
        assert any(f.code == "PRODUCER_EVIDENCE_UNVERIFIED"
                   for f in findings), [f"{f.code}: {f.detail}"
                                        for f in findings]

    def test_missing_evidence_root_flagged(self, tmp_path):
        _verify_source_evidence()
        manifest = self._manifest(tmp_path / "nonexistent",
                                  "a" * 64)
        payload = self._payload_with_manifest(manifest)
        findings = audit_producer_payload(payload)
        assert any(f.code == "PRODUCER_EVIDENCE_UNVERIFIED"
                   for f in findings), [f"{f.code}: {f.detail}"
                                        for f in findings]

    def test_fixture_manifest_still_clean(self):
        payload = _producer_payload()
        assert payload["source_manifest"] == {"fixture": True}
        assert audit_producer_payload(payload) == []

    def test_nonfixture_manifest_without_evidence_root_flagged(self):
        """The schema-level floor is independent of byte
        verification: a non-fixture manifest lacking evidence_root
        is a provenance finding even before file checks run."""
        payload = _producer_payload()
        payload["source_manifest"] = {
            "source_id": "real_src", "units": {"f1": "mm"},
            "source_digests": ["a" * 64],
            "feature_allowlist": ["f1", "f2"],
            "lineage": {"fetch": "test"}}
        _repair_outer_digests(payload)
        findings = audit_producer_payload(payload)
        assert any("evidence_root" in f.detail for f in findings)


# ---------------------------------------------------------------------
# REG-05b — carried null-family digests recompute over the records
# ---------------------------------------------------------------------

class TestReg05bNullDigestRecompute:
    def test_honest_null_families_audit_clean(self):
        assert audit_producer_payload(_producer_payload()) == []

    def test_tampered_replicate_stat_flagged(self):
        p = _producer_payload()
        p["nulls"]["shuffled"]["replicates"][0]["stat"] = 0.999
        findings = audit_producer_payload(p)
        assert any(f.code == "PRODUCER_DIGEST_MISMATCH"
                   and "shuffled" in f.path for f in findings)

    def test_tampered_family_summary_flagged(self):
        p = _producer_payload()
        p["nulls"]["season_matched"]["p_value"] = 0.5
        findings = audit_producer_payload(p)
        assert any(f.code == "PRODUCER_DIGEST_MISMATCH"
                   and "season_matched" in f.path for f in findings)

    def test_forged_family_digest_flagged(self):
        p = _producer_payload()
        p["nulls"]["shuffled"]["family_digest"] = "0" * 64
        findings = audit_producer_payload(p)
        assert any(f.code == "PRODUCER_DIGEST_MISMATCH"
                   for f in findings)

    def test_family_missing_digest_fields_flagged(self):
        """A family record that claims a bound digest but lacks the
        serialized inputs the digest covers cannot recompute."""
        p = _producer_payload()
        del p["nulls"]["shuffled"]["replicates"]
        del p["nulls"]["shuffled"]["n_replicates"]
        findings = audit_producer_payload(p)
        assert any(f.code in ("PRODUCER_PAYLOAD_MALFORMED",
                              "PRODUCER_DIGEST_MISMATCH")
                   and "shuffled" in f.path for f in findings)

    def test_tampered_null_family_blocks_replay(self):
        """REG-05b + PROV-02b: the finding blocks the association
        lane on replay — tampered null evidence cannot ride through
        to a replayed status."""
        bundle = fx.synthetic_bundle()
        payload = bundle["association"]["artifact_payload"]
        fam = _null_family("shuffled", payload["seeds_declared"],
                           [0.10, 0.20, 0.15])
        fam["replicates"][0]["stat"] = 9.9  # tamper after the bind
        payload["nulls"]["shuffled"] = fam
        result = replay_bundle(bundle)
        assert result["association_status"] == "REPLAY_FAILED"
        assert any("PRODUCER_DIGEST_MISMATCH" in p
                   for p in result["problems"])


# ---------------------------------------------------------------------
# FCST-01 — RegimeArtifactV0 mode contract + adapter no-transfer rule
# ---------------------------------------------------------------------

class TestFcast01ModeContract:
    def _record(self, **kw) -> RegimeArtifactV0:
        base = dict(
            regime_id="r-r6", mode="RETROSPECTIVE_REGIME",
            preprocessing_digest="a" * 64,
            k_selection_digest="b" * 64,
            stability_report_digest="c" * 64,
            null_model_digest="d" * 64,
            source_digests=("e" * 64,), seeds=(1, 2, 3), k=2)
        base.update(kw)
        return RegimeArtifactV0(**base)

    def test_retrospective_mode_clean(self):
        assert self._record().problems() == []

    def test_retrospective_mode_forbids_forecast_fields(self):
        rec = self._record(forecast_vintage_digests=("f" * 64,))
        assert any("forecast" in p for p in rec.problems())
        rec = self._record(forecast_feature_set=("precip_6h",))
        assert any("forecast" in p for p in rec.problems())

    def test_forecast_mode_rejects_reanalysis(self):
        rec = self._record(mode="FORECAST_REGIME")  # REANALYSIS
        assert any("data_class" in p for p in rec.problems())

    @pytest.mark.parametrize(
        "data_class", ["REFORECAST", "ARCHIVED_OPERATIONAL"])
    def test_forecast_mode_complete_clean(self, data_class):
        rec = self._record(
            mode="FORECAST_REGIME", data_class=data_class,
            forecast_vintage_digests=("f" * 64,),
            forecast_feature_set=("precip_6h", "t2m_6h"))
        assert rec.problems() == []

    def test_forecast_mode_missing_bindings_flagged(self):
        rec = self._record(mode="FORECAST_REGIME",
                           data_class="REFORECAST")
        problems = rec.problems()
        assert any("forecast_vintage_digests" in p
                   for p in problems)
        assert any("forecast_feature_set" in p for p in problems)

    def test_forecast_vintage_digests_must_be_sha256(self):
        rec = self._record(mode="FORECAST_REGIME",
                           data_class="REFORECAST",
                           forecast_vintage_digests=("not-hex",),
                           forecast_feature_set=("f",))
        assert any("forecast_vintage_digest" in p
                   for p in rec.problems())

    def test_forecast_mode_rejects_feed_data_class(self):
        rec = self._record(mode="FORECAST_REGIME",
                           data_class="CURRENT_FEED",
                           forecast_vintage_digests=("f" * 64,),
                           forecast_feature_set=("f",))
        assert any("data_class" in p for p in rec.problems())

    def test_unknown_mode_flagged(self):
        rec = self._record(mode="SOMEWHERE_ELSE")
        assert any("mode" in p for p in rec.problems())

    def test_fitted_on_gate_retained_for_both_modes(self):
        # The TRAIN_ONLY gate is unconditional — it must fire for
        # every mode, including an unknown one and a fully-bound
        # forecast record (mode-specific dispatch must not mask it).
        for kw in ({}, {"mode": "FORECAST_REGIME",
                        "data_class": "REFORECAST",
                        "forecast_vintage_digests": ("f" * 64,),
                        "forecast_feature_set": ("f",)},
                   {"mode": "SOMEWHERE_ELSE"}):
            rec = self._record(fitted_on="ALL_DATA", **kw)
            assert any("training" in p for p in rec.problems()), kw

    def test_forecast_mode_serialization_roundtrip(self):
        rec = self._record(mode="FORECAST_REGIME",
                           data_class="REFORECAST",
                           forecast_vintage_digests=("f" * 64,),
                           forecast_feature_set=("f",))
        from nepal.research_v0.records import deserialize_record
        clone = deserialize_record(rec.to_dict())
        assert clone == rec

    # --- the adapter no-transfer rule ---

    def test_forecast_mode_payload_rejected_by_adapter(self):
        p = _producer_payload()
        p["mode"] = "FORECAST_REGIME"
        _repair_outer_digests(p)
        with pytest.raises(ValueError, match="FORECAST_REGIME"):
            regime_assignment_from_artifact(p, artifact_id="x")

    def test_retrospective_payload_still_adapts(self):
        rec = regime_assignment_from_artifact(
            _producer_payload(), artifact_id="ra-retro")
        assert rec.mode == "RETROSPECTIVE_REGIME"
        assert rec.problems() == []


# ---------------------------------------------------------------------
# PROV-04 — producer binding stamped by the adapter and forwarded
# ---------------------------------------------------------------------

class TestProv04ProducerBinding:
    def test_adapter_stamps_freeze_digest_binding(self):
        payload = _producer_payload()
        rec = regime_assignment_from_artifact(
            payload, artifact_id="ra-prov4")
        assert rec.producer_payload_digest == \
            payload["freeze_digest"]
        assert rec.regime_digest == payload["freeze_digest"]

    def test_adapted_record_serialization_binds_digest(self):
        payload = _producer_payload()
        rec = regime_assignment_from_artifact(
            payload, artifact_id="ra-prov4")
        from nepal.experiment_v0.association import (
            RegimeAssignmentArtifact)
        clone = RegimeAssignmentArtifact.from_dict(rec.to_dict())
        assert clone.producer_payload_digest == \
            payload["freeze_digest"]

    def test_replay_forwards_producer_payload(self, monkeypatch):
        """When run_association binds the ``producer_payload``
        keyword, replay forwards the exact artifact_payload mapping
        that passed the adapter — the verified binding rides into
        the association run."""
        if "producer_payload" not in inspect.signature(
                run_association).parameters:
            pytest.skip("run_association has no producer_payload "
                        "keyword yet — the association-side binding "
                        "contract is landing separately")
        seen: list = []
        real = audit_mod.run_association

        def spy(artifact, events, controls, unit_basins, *,
                producer_payload=None, **kw):
            seen.append(producer_payload)
            return real(artifact, events, controls, unit_basins,
                        producer_payload=producer_payload, **kw)

        monkeypatch.setattr(audit_mod, "run_association", spy)
        bundle = fx.synthetic_bundle()
        result = replay_bundle(bundle)
        assert result["problems"] == []
        expected = bundle["association"]["artifact_payload"]
        assert seen, "run_association was never invoked"
        assert all(p is expected or p == expected for p in seen)
