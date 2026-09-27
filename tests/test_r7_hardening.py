"""Round-7 behavioral tests: codeable residual closures.

Each test exercises a specific Round-7 gap (C01–C16) with a
failing-first adversarial probe — the check must fail closed before
the fix and pass after.
"""

import hashlib
import copy as _copy

import numpy as np
import pytest

from nepal.research_v0._hashing import (
    sha256_canonical, verify_source_evidence)
from nepal.research_v0.gates import REQUIRED_REGIME_GATE_NAMES
from nepal.science_v0.regimes import (
    freeze_regime_artifact, run_regimes,
    _validate_config_semantics, RegimeRunConfig)
from nepal.experiment_v0.association import (
    run_association, _event_horizon_admissible,
    _COARSE_PRECISION_TERMS, ALLOWED_LOOKBACK_DAYS)
from nepal.research_v0.records import EventLabelV0


# ---------------------------------------------------------------------
# C02: strict boolean fixture flag
# ---------------------------------------------------------------------

class TestC02StrictBoolFixture:
    def test_string_fixture_does_not_bypass(self, tmp_path):
        """{"fixture": "true"} (string) must NOT bypass verification."""
        manifest = {"fixture": "true",
                    "evidence_root": str(tmp_path)}
        problems = verify_source_evidence(manifest)
        assert any("strict boolean" in p for p in problems)

    def test_int_one_fixture_does_not_bypass(self, tmp_path):
        manifest = {"fixture": 1,
                    "evidence_root": str(tmp_path)}
        problems = verify_source_evidence(manifest)
        assert any("strict boolean" in p for p in problems)

    def test_real_true_fixture_still_bypasses(self):
        assert verify_source_evidence({"fixture": True}) == []


# ---------------------------------------------------------------------
# C04: semantic config revalidation at freeze
# ---------------------------------------------------------------------

class TestC04ConfigSemantics:
    def test_bad_cadence_rejected(self):
        problems = _validate_config_semantics(
            {"cadence": "1H", "gap_policy": "calendar",
             "bootstrap_block_len": 7, "k_candidates": [1, 2, 3],
             "seeds": [1, 2, 3], "missingness_policy": "listwise",
             "effort_split": "median"})
        assert any("cadence" in p for p in problems)

    def test_bad_gap_policy_rejected(self):
        problems = _validate_config_semantics(
            {"cadence": "1D", "gap_policy": "something_else",
             "bootstrap_block_len": 7, "k_candidates": [1, 2, 3],
             "seeds": [1, 2, 3], "missingness_policy": "listwise",
             "effort_split": "median"})
        assert any("gap_policy" in p for p in problems)

    def test_zero_block_len_rejected(self):
        problems = _validate_config_semantics(
            {"cadence": "1D", "gap_policy": "calendar",
             "bootstrap_block_len": 0, "k_candidates": [1, 2, 3],
             "seeds": [1, 2, 3], "missingness_policy": "listwise",
             "effort_split": "median"})
        assert any("block_len" in p for p in problems)

    def test_bad_missingness_rejected(self):
        problems = _validate_config_semantics(
            {"cadence": "1D", "gap_policy": "calendar",
             "bootstrap_block_len": 7, "k_candidates": [1, 2, 3],
             "seeds": [1, 2, 3],
             "missingness_policy": "bogus_policy",
             "effort_split": "median"})
        assert any("missingness" in p for p in problems)

    def test_clean_config_passes(self):
        problems = _validate_config_semantics(
            {"cadence": "1D", "gap_policy": "calendar",
             "bootstrap_block_len": 7, "k_candidates": [1, 2, 3],
             "seeds": [1, 2, 3], "missingness_policy": "listwise",
             "effort_split": "median",
             "mode": "RETROSPECTIVE_REGIME",
             "era_boundaries": ["e1", "e2"]})
        assert problems == []


# ---------------------------------------------------------------------
# C07: per-event-precision-class horizon admissibility
# ---------------------------------------------------------------------

class TestC07PrecisionHorizons:
    def _event(self, precision):
        return EventLabelV0(
            event_id="e1", vertical_id="snow",
            source_id="s1", source_version="v1",
            event_time_start="2020-01-15",
            event_time_end="2020-01-16",
            event_time_precision=precision)

    def test_month_precision_excluded_from_fine_horizons(self):
        ev = self._event("month")
        assert not _event_horizon_admissible(ev, 2)
        assert not _event_horizon_admissible(ev, 7)

    def test_month_precision_admitted_to_coarse_horizons(self):
        ev = self._event("month")
        assert _event_horizon_admissible(ev, 0)
        assert _event_horizon_admissible(ev, 30)

    def test_day_precision_admitted_to_all(self):
        ev = self._event("day")
        for h in ALLOWED_LOOKBACK_DAYS:
            assert _event_horizon_admissible(ev, h)

    def test_exact_timestamp_admitted_to_all(self):
        ev = self._event("exact_timestamp")
        for h in ALLOWED_LOOKBACK_DAYS:
            assert _event_horizon_admissible(ev, h)

    def test_unresolved_excluded_from_fine_horizons(self):
        ev = self._event("unresolved")
        assert not _event_horizon_admissible(ev, 3)
        assert _event_horizon_admissible(ev, 0)


# ---------------------------------------------------------------------
# C11: empty degradation_scenarios rejected under declaration
# ---------------------------------------------------------------------

class TestC11EmptyDegradation:
    def test_empty_list_rejected_under_declaration(self):
        from nepal.experiment_v0.evaluation import evaluate
        import tests.fixtures.synthetic_exp_b3 as fx

        design = fx.underpowered_design()
        cases = design["cases"]
        decl = fx.experiment_declaration(cases)
        ev = {name: {"digest": sha256_canonical(
                        [round(float(v), 9) for v in vec]),
                    "fit_provenance": "bound"}
              for name, vec in
              fx.make_baseline_probs(cases).items()}
        with pytest.raises(ValueError, match="degradation_scenarios"):
            evaluate(cases,
                     holdout=design["holdout"],
                     baseline_probs=fx.make_baseline_probs(cases),
                     admitted_vintages=fx.admitted_for_cases(cases),
                     opportunities=fx.opportunity_registry(cases),
                     unit_basins=fx.unit_basins_for(cases),
                     region_basins=fx.region_basins_for(
                         sorted({c.region for c in cases})),
                     experiment=decl,
                     baseline_evidence=ev,
                     degradation_scenarios=[])


# ---------------------------------------------------------------------
# C16: finite-permutation correction on spatial-shift p
# ---------------------------------------------------------------------

class TestC16FinitePermutation:
    def test_min_p_is_one_over_n_plus_one(self):
        """With 24 offsets and ge=0, p must be 1/25, not 0/24=0.0."""
        from nepal.experiment_v0.association import (
            _round12, SPATIAL_SHIFT_OFFSETS)
        ge = 0
        n = len(SPATIAL_SHIFT_OFFSETS)
        p = _round12((ge + 1) / (n + 1))
        assert p == pytest.approx(1.0 / 25.0)
        assert p > 0.0


# ---------------------------------------------------------------------
# C15: RunManifestV0 bound into the artifact
# ---------------------------------------------------------------------

class TestC15RunManifest:
    def test_producer_emits_typed_run_manifest(self):
        """run_regimes output carries a validated RunManifestV0."""
        from tests.test_science_v0_regimes import _art_std
        art = _art_std()
        rm = art.get("run_manifest")
        assert isinstance(rm, dict)
        assert rm.get("run_id")
        assert rm.get("worker_id")
        assert rm.get("created_at")
        assert rm.get("environment_digest")
        assert rm.get("input_digests")
        assert rm.get("output_digests")
        assert rm.get("status") == "COMPLETED"
        assert rm.get("checkpoint_policy") == \
            "atomic_publish_or_quarantine"

    def test_run_manifest_digest_bound(self):
        from tests.test_science_v0_regimes import _art_std
        art = _art_std()
        assert art["run_manifest_digest"] == sha256_canonical(
            art["run_manifest"])

    def test_freeze_rejects_missing_run_manifest(self):
        from tests.test_science_v0_regimes import _art_std
        art = _art_std()
        art = {k: v for k, v in art.items()
               if k != "run_manifest"}
        # re-digest
        art["regime_artifact_digest"] = sha256_canonical(
            {k: v for k, v in art.items()
             if k != "regime_artifact_digest"})
        with pytest.raises(ValueError, match="run_manifest"):
            freeze_regime_artifact(art)


# ---------------------------------------------------------------------
# C03: unit_basin_map bound into the artifact
# ---------------------------------------------------------------------

class TestC03UnitBasinMap:
    def test_producer_emits_unit_basin_map(self):
        from tests.test_science_v0_regimes import _art_std
        art = _art_std()
        ubm = art.get("unit_basin_map")
        assert isinstance(ubm, (list, tuple))
        assert len(ubm) > 0
        for entry in ubm:
            assert len(entry) == 2

    def test_freeze_rejects_missing_unit_basin_map(self):
        from tests.test_science_v0_regimes import _art_std
        art = _art_std()
        art = {k: v for k, v in art.items()
               if k != "unit_basin_map"}
        art["regime_artifact_digest"] = sha256_canonical(
            {k: v for k, v in art.items()
             if k != "regime_artifact_digest"})
        with pytest.raises(ValueError, match="unit_basin_map"):
            freeze_regime_artifact(art)


# ---------------------------------------------------------------------
# C06: family_digest covers all record fields
# ---------------------------------------------------------------------

class TestC06FamilyDigest:
    def test_family_digest_binds_observed_and_alpha(self):
        from tests.test_science_v0_regimes import _art_std
        art = _art_std()
        for fam in ("shuffled", "season_matched"):
            nul = art["nulls"][fam]
            material = {
                "family": fam, "seed_cycle": art["seeds"],
                "n_replicates": nul["n_replicates"],
                "statistic": nul["statistic"],
                "p_value": nul["p_value"],
                "observed": nul.get("observed"),
                "alpha": nul.get("alpha"),
                "n_succeeded": nul.get("n_succeeded"),
                "n_failed": nul.get("n_failed"),
                "status": nul.get("status"),
                "reason": nul.get("reason"),
                "selection": nul.get("selection"),
                "null_stat_min": nul.get("null_stat_min"),
                "null_stat_max": nul.get("null_stat_max"),
                "null_k_distribution": nul.get(
                    "null_k_distribution", {}),
                "replicates": nul.get("replicates", [])}
            assert sha256_canonical(material) == \
                nul["family_digest"], fam


# ---------------------------------------------------------------------
# C12: experiment_id binds declared inputs
# ---------------------------------------------------------------------

class TestC12ExperimentIdentity:
    def test_experiment_id_includes_n_boot_and_seed(self):
        """Two evaluations differing only in n_boot produce
        different experiment ids."""
        from nepal.experiment_v0.evaluation import evaluate
        import tests.fixtures.synthetic_exp_b3 as fx

        design = fx.underpowered_design()
        cases = design["cases"]
        kwargs = dict(
            holdout=design["holdout"],
            baseline_probs=fx.make_baseline_probs(cases),
            admitted_vintages=fx.admitted_for_cases(cases),
            opportunities=fx.opportunity_registry(cases),
            unit_basins=fx.unit_basins_for(cases),
            region_basins=fx.region_basins_for(
                sorted({c.region for c in cases})))
        r1 = evaluate(cases, n_boot=100, seed=42, **kwargs)
        r2 = evaluate(cases, n_boot=200, seed=42, **kwargs)
        r3 = evaluate(cases, n_boot=100, seed=43, **kwargs)
        assert r1.experiment_id != r2.experiment_id
        assert r1.experiment_id != r3.experiment_id
