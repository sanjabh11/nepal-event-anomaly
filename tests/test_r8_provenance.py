"""Round-8 provenance tests — shared producer validator (C01), typed
fit_partition binding (C09), forecast feature payload binding (C10).

Every probe starts from a REAL ``run_regimes`` artifact (the shared
module-level fixtures) and either deletes a provenance section or
mutates a bound field, then recomputes only the envelope digests —
so the payload is internally self-consistent under rehashing and can
only be rejected by the shared schema/binding floor.  The same
mutated payload must fail at EVERY boundary: ``freeze_regime_artifact``
raises, ``regime_assignment_from_artifact`` raises, and
``audit_producer_payload`` returns a Finding.
"""
from __future__ import annotations

import copy

import pytest

from nepal.research_v0._hashing import sha256_canonical
from nepal.research_v0.producer_validation import (
    PRODUCER_DIGEST_FIELDS, PRODUCER_REQUIRED_FIELDS,
    validate_producer_payload)
from nepal.science_v0.regimes import freeze_regime_artifact
from nepal.experiment_v0.adapters import regime_assignment_from_artifact
from nepal.experiment_v0.audit import audit_producer_payload

from tests.test_science_v0_regimes import _art_full, _art_std
from tests.test_r6_regimes import _art_forecast


def _rehash_pre_freeze(art: dict) -> dict:
    """Repair the producer envelope digest over a mutated PRE-freeze
    artifact — exactly the rehash a hand-assembled artifact would
    carry to survive the old digest-only floor."""
    art["regime_artifact_digest"] = sha256_canonical(
        {k: v for k, v in art.items()
         if k != "regime_artifact_digest"})
    return art


def _rehash_frozen(payload: dict) -> dict:
    """Repair both envelope digests over a mutated FROZEN payload —
    the strongest honest rehash available to an attacker."""
    payload["regime_artifact_digest"] = sha256_canonical(
        {k: v for k, v in payload.items()
         if k not in ("regime_artifact_digest", "freeze_digest",
                      "frozen")})
    payload["freeze_digest"] = sha256_canonical(
        {k: v for k, v in payload.items()
         if k not in ("freeze_digest", "frozen")})
    return payload


def _frozen_payload() -> dict:
    """A real frozen DESCRIPTIVE producer payload — the fully-declared
    fixture run certified by freeze_regime_artifact — so the adapter
    boundary is exercised past its own status gate."""
    art = _art_full()
    assert art["status"] == "DESCRIPTIVE_REGIME_ONLY"
    return freeze_regime_artifact(art)


# ---------------------------------------------------------------------
# C01 — one shared validator, every boundary
# ---------------------------------------------------------------------

class TestC01SharedValidator:
    def test_required_tuples_cover_new_bindings(self):
        assert "fit_partition" in PRODUCER_REQUIRED_FIELDS
        assert "fit_partition_digest" in PRODUCER_REQUIRED_FIELDS
        assert "fit_partition_digest" in PRODUCER_DIGEST_FIELDS
        assert "forecast_feature_payload_digest" in \
            PRODUCER_DIGEST_FIELDS

    def test_real_artifact_validates_clean(self):
        """A raw (pre-freeze) run_regimes artifact may only be
        flagged for the two fields freeze itself emits — nothing
        else may be missing."""
        problems = validate_producer_payload(_art_std())
        assert problems, "pre-freeze artifact lacks freeze fields"
        assert all("freeze_digest" in p or "frozen" in p
                   for p in problems), problems

    def test_real_frozen_payload_validates_clean(self):
        assert validate_producer_payload(_frozen_payload()) == []

    @pytest.mark.parametrize(
        "field", ["model", "source_manifest", "preprocessing",
                  "unit_basin_map", "run_manifest", "fit_partition",
                  "fit_partition_digest"])
    def test_shared_validator_flags_missing_section(self, field):
        art = _art_std()
        del art[field]
        problems = validate_producer_payload(art)
        assert problems, field
        assert any(field in p for p in problems), (field, problems)


class TestC01FreezeBoundary:
    def test_freeze_rejects_artifact_missing_model(self):
        """A rehashed artifact minus the fitted model must not
        freeze — the old floor only recomputed digests."""
        art = _art_std()
        del art["model"]
        _rehash_pre_freeze(art)
        with pytest.raises(ValueError, match="model"):
            freeze_regime_artifact(art)

    def test_freeze_rejects_artifact_missing_source_manifest(self):
        art = _art_std()
        del art["source_manifest"]
        _rehash_pre_freeze(art)
        with pytest.raises(ValueError, match="source_manifest"):
            freeze_regime_artifact(art)

    def test_freeze_rejects_artifact_missing_preprocessing(self):
        art = _art_std()
        del art["preprocessing"]
        _rehash_pre_freeze(art)
        with pytest.raises(ValueError, match="preprocessing"):
            freeze_regime_artifact(art)

    def test_freeze_rejects_artifact_missing_unit_basin_map(self):
        """The C03 unit->basin binding is a required section — a
        rehashed artifact without it must fail shared validation."""
        art = _art_std()
        del art["unit_basin_map"]
        _rehash_pre_freeze(art)
        with pytest.raises(ValueError, match="unit_basin_map"):
            freeze_regime_artifact(art)

    def test_freeze_rejects_artifact_missing_fit_partition(self):
        art = _art_std()
        del art["fit_partition"]
        del art["fit_partition_digest"]
        _rehash_pre_freeze(art)
        with pytest.raises(ValueError, match="fit_partition"):
            freeze_regime_artifact(art)


class TestC01AdapterBoundary:
    def test_adapter_runs_shared_validator(self):
        """The same rehashed-minus-section payloads the freeze
        boundary rejects are rejected by the association adapter."""
        for field in ("model", "source_manifest", "preprocessing",
                      "unit_basin_map", "run_manifest",
                      "fit_partition"):
            payload = _frozen_payload()
            del payload[field]
            _rehash_frozen(payload)
            with pytest.raises(ValueError, match=field):
                regime_assignment_from_artifact(
                    payload, artifact_id=f"r8-missing-{field}")

    def test_adapter_rejects_unfrozen_payload(self):
        payload = _frozen_payload()
        payload["frozen"] = False
        _rehash_frozen(payload)
        with pytest.raises(ValueError, match="frozen"):
            regime_assignment_from_artifact(
                payload, artifact_id="r8-unfrozen")


class TestC01EveryBoundary:
    @pytest.mark.parametrize(
        "field", ["model", "source_manifest", "fit_partition"])
    def test_tampered_producer_payload_rejected_at_every_boundary(
            self, field):
        """One mutation, three boundaries: freeze raises, the
        adapter raises, and the auditor returns a finding — the
        rules no longer diverge."""
        # freeze boundary (pre-freeze artifact, rehashed)
        art = _art_std()
        del art[field]
        _rehash_pre_freeze(art)
        with pytest.raises(ValueError):
            freeze_regime_artifact(art)
        # adapter boundary (frozen payload, rehashed)
        payload = _frozen_payload()
        del payload[field]
        _rehash_frozen(payload)
        with pytest.raises(ValueError):
            regime_assignment_from_artifact(
                payload, artifact_id=f"r8-all-{field}")
        # audit boundary — a Finding, never a silent pass
        findings = audit_producer_payload(payload)
        assert findings
        assert any(field in f.detail or field in f.path
                   for f in findings)


# ---------------------------------------------------------------------
# C09 — typed fit_partition binding
# ---------------------------------------------------------------------

class TestC09FitPartition:
    def test_producer_emits_typed_fit_partition(self):
        art = _art_std()
        fp = art.get("fit_partition")
        assert isinstance(fp, dict)
        assert fp["record_type"] == "fit_partition/v0"
        assert sorted(fp["train_groups"]) == sorted(art["fit_groups"])
        assert sorted(fp["heldout_groups"]) == sorted(
            art["heldout_groups_declared"])
        assert fp["n_train_rows"] == art["n_train_rows"]
        assert fp["n_rows"] == art["n_rows"]
        assert fp["feature_matrix_digest"] == \
            art["feature_matrix_digest"]
        assert art["fit_partition_digest"] == sha256_canonical(fp)

    def test_fit_partition_survives_freeze(self):
        frozen = _frozen_payload()
        assert frozen["fit_partition"]["record_type"] == \
            "fit_partition/v0"
        assert frozen["fit_partition_digest"] == sha256_canonical(
            frozen["fit_partition"])

    def test_fit_partition_digest_binds_rows_and_groups(self):
        """Mutating the partition membership while rehashing ONLY
        fit_partition_digest must fail on cross-field agreement;
        mutating the artifact's declared fit_groups must fail on the
        digest binding."""
        art = _art_std()
        art["fit_partition"]["heldout_groups"] = ["forged_group"]
        art["fit_partition_digest"] = sha256_canonical(
            art["fit_partition"])
        _rehash_pre_freeze(art)
        with pytest.raises(ValueError, match="fit_partition"):
            freeze_regime_artifact(art)

        art = _art_std()
        art["fit_groups"] = ["forged_fit_group"]
        _rehash_pre_freeze(art)
        with pytest.raises(ValueError):
            freeze_regime_artifact(art)

    def test_fit_partition_digest_tamper_rejected(self):
        art = _art_std()
        art["fit_partition"]["n_train_rows"] = \
            art["n_train_rows"] + 1
        _rehash_pre_freeze(art)   # stale fit_partition_digest
        with pytest.raises(ValueError, match="fit_partition"):
            freeze_regime_artifact(art)

    def test_direct_assignment_without_verified_binding_cannot_promote(
            self):
        """An artifact claiming terminal/associable status without
        the typed fit-partition binding cannot promote into the
        association lane at any boundary."""
        payload = _frozen_payload()
        assert payload["status"] == "DESCRIPTIVE_REGIME_ONLY"
        assert payload["associable"] is True
        assert payload["terminal"] is True
        # baseline: the complete payload validates clean
        assert validate_producer_payload(payload) == []
        del payload["fit_partition"]
        del payload["fit_partition_digest"]
        _rehash_frozen(payload)
        # the audit floor flags the missing binding
        findings = audit_producer_payload(payload)
        assert any(f.code == "PRODUCER_PROVENANCE_MISSING"
                   and "fit_partition" in f.detail
                   for f in findings)
        # the adapter refuses to mint an assignment artifact
        with pytest.raises(ValueError, match="fit_partition"):
            regime_assignment_from_artifact(
                payload, artifact_id="r8-nobinding")
        # and a pre-freeze variant cannot be certified either
        art = _art_std()
        del art["fit_partition"]
        del art["fit_partition_digest"]
        _rehash_pre_freeze(art)
        with pytest.raises(ValueError, match="fit_partition"):
            freeze_regime_artifact(art)


# ---------------------------------------------------------------------
# C10 — forecast feature payload binding
# ---------------------------------------------------------------------

class TestC10ForecastPayload:
    def test_forecast_artifact_emits_feature_payload(self):
        art = _art_forecast()
        assert art["mode"] == "FORECAST_REGIME"
        ffp = art.get("forecast_feature_payload")
        assert isinstance(ffp, dict)
        assert ffp["record_type"] == "forecast_feature_payload/v0"
        assert sorted(ffp["forecast_feature_set"]) == sorted(
            art["forecast_feature_set"])
        assert sorted(ffp["forecast_vintage_digests"]) == sorted(
            art["forecast_vintage_digests"])
        assert ffp["feature_matrix_digest"] == \
            art["feature_matrix_digest"]
        assert art["forecast_feature_payload_digest"] == \
            sha256_canonical(ffp)

    def test_forecast_artifact_freezes_with_payload(self):
        frozen = freeze_regime_artifact(_art_forecast())
        assert frozen["frozen"] is True
        assert frozen["forecast_feature_payload"]["record_type"] == \
            "forecast_feature_payload/v0"

    def test_forecast_mode_requires_feature_payload(self):
        """A bare 64-hex forecast_feature_set plus vintage digests is
        no longer the only binding — the typed payload structure must
        exist."""
        art = _art_forecast()
        assert art["forecast_vintage_digests"]
        assert art["forecast_feature_set"]
        del art["forecast_feature_payload"]
        del art["forecast_feature_payload_digest"]
        _rehash_pre_freeze(art)
        with pytest.raises(ValueError,
                           match="forecast_feature_payload"):
            freeze_regime_artifact(art)

    def test_forecast_payload_tamper_rejected(self):
        art = _art_forecast()
        art["forecast_feature_payload"]["forecast_vintage_digests"] \
            = ["0" * 64]
        _rehash_pre_freeze(art)   # stale payload digest
        with pytest.raises(ValueError, match="forecast"):
            freeze_regime_artifact(art)

    def test_retrospective_must_not_carry_feature_payload(self):
        art = _art_std()
        assert art["mode"] == "RETROSPECTIVE_REGIME"
        art["forecast_feature_payload"] = {
            "record_type": "forecast_feature_payload/v0",
            "forecast_feature_set": ["f1"],
            "forecast_vintage_digests": ["a" * 64],
            "feature_matrix_digest": art["feature_matrix_digest"],
            "row_count": art["n_rows"],
            "row_keys_digest": art["preprocessing"]
                                  ["row_keys_digest"]}
        art["forecast_feature_payload_digest"] = sha256_canonical(
            art["forecast_feature_payload"])
        _rehash_pre_freeze(art)
        with pytest.raises(ValueError, match="forecast"):
            freeze_regime_artifact(art)

    def test_retrospective_artifact_carries_no_forecast_payload(self):
        art = _art_std()
        assert "forecast_feature_payload" not in art
        assert "forecast_feature_payload_digest" not in art
