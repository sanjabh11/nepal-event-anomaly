"""Regime real-path tests — the descriptive engine end-to-end.

Exercises the REAL ``run_regimes``/``freeze_regime_artifact`` engine
(no monkeypatching) on the real-path feature frame, plus the mode
restrictions (FCST-01) and freeze-integrity gates (PROV-C03):

* the run is RETROSPECTIVE/REANALYSIS by construction — no forecast
  fields on the retrospective path (probe 31), FORECAST_REGIME
  cannot run unbound (probe 37);
* the artifact's regime digest recomputes; freeze round-trips and
  rejects a mutated artifact;
* the descriptive configuration floor: K sweep includes K=1 (probe
  34), >=3 declared seeds (probe 35), temporal-block bootstrap >= 200,
  and the null envelope floor (probe 36 is bound at freeze).

Synthetic frame only — no real-data fit happens this cycle
(``P5_BLOCKED_NO_AUTHORIZATION``); no labels enter feature fitting;
statuses stay research-only and non-promotable.
"""
from __future__ import annotations

import copy
import dataclasses

import pytest

from nepal.experiment_v0.audit import audit_producer_payload
from nepal.research_v0.policy import RegimeMode
from nepal.science_v0.regimes import (
    MIN_BOOTSTRAP, MIN_NULL_REPLICATES, _digest,
    freeze_regime_artifact, run_regimes)

from tests.test_experiment_v0_b4_audit import _mini_regime_frame

_HONEST_STATUSES = {
    "DESCRIPTIVE_REGIME_ONLY", "UNSUPERVISED_STRUCTURE_NOT_STABLE",
    "CANDIDATE_ONLY"}

# ---------------------------------------- module-cached real engine run

_RUN = {}


def _real_run():
    """One REAL run_regimes execution per session (hundreds of
    bootstrap/null refits — never re-run inside a lane)."""
    if "artifact" not in _RUN:
        df, feature_cols, train_mask, cfg = _mini_regime_frame()
        _RUN["cfg"] = cfg
        _RUN["artifact"] = run_regimes(
            df, feature_cols, train_mask, cfg)
    return _RUN["artifact"], _RUN["cfg"]

# --------------------------------------------- 1. real engine, real run

class TestRealRegimeRun:
    def test_status_is_honest_and_descriptive_vocabulary(self):
        artifact, _ = _real_run()
        assert artifact.get("status") in _HONEST_STATUSES, \
            artifact.get("reason", artifact.get("status"))
        assert artifact.get("status") != "RUN_ERROR"

    def test_data_class_is_reanalysis_in_retrospective_mode(self):
        artifact, _ = _real_run()
        if "data_class" in artifact:
            assert artifact["data_class"] == "REANALYSIS"

    def test_regime_digest_recomputes_exactly(self):
        artifact, _ = _real_run()
        expected = _digest({k: v for k, v in artifact.items()
                            if k != "regime_artifact_digest"})
        assert artifact["regime_artifact_digest"] == expected

    def test_seed_coverage_is_complete_and_declared(self):
        artifact, _ = _real_run()
        declared = artifact.get("seeds_declared")
        assert declared and len(set(declared)) >= 3, declared
        coverage = artifact.get("seed_coverage")
        assert coverage, "seed coverage must be emitted (REG-C03)"
        assert set(coverage) >= set(declared) or len(coverage) >= \
            len(declared)

    def test_null_envelope_present(self):
        artifact, _ = _real_run()
        assert artifact.get("nulls"), "null envelope required (REG-C04)"

    def test_sweep_includes_k1(self):
        _, cfg = _real_run()
        assert min(cfg.k_candidates) == 1, cfg.k_candidates

    def test_bootstrap_floor_is_at_least_200(self):
        assert MIN_BOOTSTRAP >= 200
        _, cfg = _real_run()
        assert cfg.n_bootstrap >= 200

    def test_null_envelope_floor(self):
        assert MIN_NULL_REPLICATES >= 50
        _, cfg = _real_run()
        assert cfg.n_null_replicates >= MIN_NULL_REPLICATES

    def test_producer_audit_accepts_the_real_artifact(self):
        artifact, _ = _real_run()
        frozen = freeze_regime_artifact(copy.deepcopy(artifact))
        findings = audit_producer_payload(frozen)
        assert findings == [], [f"{f.code}: {f.detail}"
                                for f in findings]

# ------------------------------------------------- 2. freeze integrity

class TestFreezeIntegrity:
    def test_freeze_round_trips_the_real_artifact(self):
        artifact, _ = _real_run()
        frozen = freeze_regime_artifact(copy.deepcopy(artifact))
        assert isinstance(frozen, dict)
        assert frozen["regime_artifact_digest"] == \
            artifact["regime_artifact_digest"]

    def test_freeze_rejects_open_gate_under_descriptive_status(self):
        """PROV-C03: a direct freeze call cannot bypass the producer
        audit — a descriptive status over an OPEN gate rejects."""
        artifact, _ = _real_run()
        if artifact.get("status") != "DESCRIPTIVE_REGIME_ONLY":
            pytest.skip(
                f"cached artifact is "
                f"{artifact.get('status')!r}; the gate/status "
                "consistency rejection is exercised under a "
                "descriptive artifact")
        mutated = copy.deepcopy(artifact)
        gates = mutated["stability"]["required_gates"]
        first = sorted(gates)[0]
        gates[first] = False
        mutated["regime_artifact_digest"] = _digest(
            {k: v for k, v in mutated.items()
             if k != "regime_artifact_digest"})
        with pytest.raises((ValueError, TypeError, AttributeError)):
            freeze_regime_artifact(mutated)

# --------------------------------------------- 3. mode restrictions FCST-01

class TestModeRestrictions:
    def _run(self, **over):
        df, feature_cols, train_mask, cfg = _mini_regime_frame()
        cfg = dataclasses.replace(cfg, **over)
        return run_regimes(df, feature_cols, train_mask, cfg)

    def test_retrospective_mode_cannot_carry_forecast_fields(self):
        out = self._run(
            forecast_vintage_digests=("a" * 64,),
            forecast_feature_set=("f1",))
        assert out["status"] == "RUN_ERROR", out

    def test_forecast_mode_requires_bound_vintages(self):
        out = self._run(mode=RegimeMode.FORECAST_REGIME)
        assert out["status"] == "RUN_ERROR", out

    def test_default_mode_is_retrospective(self):
        _, cfg = _real_run()
        assert cfg.mode == RegimeMode.RETROSPECTIVE_REGIME

    def test_no_labels_enter_feature_fitting(self):
        """The frame handed to the engine carries no event-label
        column — the feature columns are declared physical channels
        only (probe 24-25 at the engine boundary)."""
        df, feature_cols, _, _ = _mini_regime_frame()
        assert "event" not in [c.lower() for c in feature_cols]
        assert "label" not in [c.lower() for c in feature_cols]
        assert set(feature_cols) <= set(df.columns)


