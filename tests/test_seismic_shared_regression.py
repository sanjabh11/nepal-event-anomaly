"""SEISMIC-01 shared-boundary regressions — the
``SEISMIC_WAVEFORM_RETROSPECTIVE`` class may extend the retrospective
vocabulary, but it must never leak into forecast, adapter, or
association lanes, and it must never ride a fixture manifest.

The weather/GLOF lane's REANALYSIS behavior is asserted unchanged —
the extension is additive, never a mutation of the existing lane.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nepal.research_v0.policy import (
    SEISMIC_WAVEFORM_RETROSPECTIVE_CLASS)
from nepal.research_v0.producer_validation import (
    validate_producer_payload)
from nepal.research_v0.records import (
    FORECAST_REGIME_DATA_CLASSES,
    RETROSPECTIVE_REGIME_DATA_CLASSES, RegimeArtifactV0)
from nepal.science_v0.regimes import RegimeRunConfig, run_regimes

SEISMIC = SEISMIC_WAVEFORM_RETROSPECTIVE_CLASS


def _tiny_df() -> pd.DataFrame:
    return pd.DataFrame({
        "unit_id": ["u1"], "date": ["2020-01-01"],
        "basin_group": ["g0"], "season": ["JJA"], "f1": [0.0]})


def _retro_cfg(**kw) -> RegimeRunConfig:
    base = dict(
        era_col=None,
        era_waiver_reason="single epoch — waived",
        bootstrap_block_len=12,
        source_manifest={"fixture": True})
    base.update(kw)
    return RegimeRunConfig(**base)


class TestVocabularyIsolation:
    """The class lives in the retrospective vocabulary only."""

    def test_class_in_retro_not_forecast_vocabulary(self):
        assert SEISMIC in RETROSPECTIVE_REGIME_DATA_CLASSES
        assert SEISMIC not in FORECAST_REGIME_DATA_CLASSES
        assert "REANALYSIS" in RETROSPECTIVE_REGIME_DATA_CLASSES

    def test_not_a_forecast_data_class_enum_member(self):
        """The class is deliberately a module-level constant, not a
        ForecastDataClass member — enum iteration can never leak it
        into the forecast-lane allowlist."""
        from nepal.research_v0.policy import ForecastDataClass
        assert SEISMIC not in {c.value for c in ForecastDataClass}


class TestRecordLane:
    """RegimeArtifactV0: seismic admitted retrospectively, rejected
    on forecast, undeclared classes rejected, REANALYSIS unchanged."""

    def _artifact(self, **kw) -> RegimeArtifactV0:
        base = dict(
            regime_id="r-seis", mode="RETROSPECTIVE_REGIME",
            data_class="REANALYSIS",
            preprocessing_digest="f" * 64,
            k_selection_digest="a" * 64,
            stability_report_digest="b" * 64,
            null_model_digest="c" * 64,
            source_digests=("d" * 64,),
            seeds=(1, 2, 3), k=1)
        base.update(kw)
        return RegimeArtifactV0(**base)

    def test_retro_artifact_admits_seismic_class(self):
        art = self._artifact(data_class=SEISMIC)
        assert not [p for p in art.problems()
                    if "data class" in p or "reanalysis" in p]

    def test_weather_default_unchanged(self):
        art = self._artifact()
        assert not [p for p in art.problems()
                    if "data class" in p or "reanalysis" in p]

    def test_forecast_artifact_rejects_seismic_class(self):
        art = self._artifact(mode="FORECAST_REGIME",
                             data_class=SEISMIC)
        assert any("forecast" in p.lower() for p in art.problems())

    def test_retro_artifact_rejects_undeclared_class(self):
        art = self._artifact(data_class="CURRENT_FEED")
        assert any("retrospective" in p or "data class" in p
                   for p in art.problems())


class TestEngineLane:
    """run_regimes: the seismic class is retrospective-only and
    byte-bound; forecast mode and fixture manifests reject."""

    def test_seismic_rejected_on_forecast_mode(self):
        cfg = _retro_cfg(mode="FORECAST_REGIME",
                         retrospective_data_class=SEISMIC)
        rep = run_regimes(_tiny_df(), ["f1"],
                          np.array([True]), cfg)
        assert rep["status"] == "RUN_ERROR"
        assert "FORECAST_REGIME" in rep["reason"]

    def test_seismic_rejected_on_fixture_manifest(self):
        cfg = _retro_cfg(retrospective_data_class=SEISMIC)
        rep = run_regimes(_tiny_df(), ["f1"],
                          np.array([True]), cfg)
        assert rep["status"] == "RUN_ERROR"
        assert "non-fixture" in rep["reason"]

    def test_undeclared_retro_class_rejected(self):
        cfg = _retro_cfg(retrospective_data_class="NOT_A_CLASS")
        rep = run_regimes(_tiny_df(), ["f1"],
                          np.array([True]), cfg)
        assert rep["status"] == "RUN_ERROR"
        assert "retrospective_data_class" in rep["reason"]


class TestProducerFloor:
    """validate_producer_payload: the seismic class is
    terminal-descriptive but NEVER associable, and the serialized
    config's retrospective_data_class must equal the artifact's
    emitted data_class."""

    def _payload(self, **kw) -> dict:
        p = {
            "status": "DESCRIPTIVE_REGIME_ONLY",
            "mode": "RETROSPECTIVE_REGIME",
            "data_class": SEISMIC,
            "terminal": True,
            "associable": False,
            "source_manifest": {"fixture": True},
            "config": {
                "mode": "RETROSPECTIVE_REGIME",
                "data_class": SEISMIC,
                "retrospective_data_class": SEISMIC,
                "source_manifest": {"fixture": True},
            },
        }
        p.update(kw)
        return p

    def test_seismic_descriptive_must_be_non_associable(self):
        problems = validate_producer_payload(
            self._payload(associable=True))
        assert any("associable" in p or "terminal" in p
                   for p in problems)

    def test_seismic_fixture_manifest_rejected(self):
        problems = validate_producer_payload(self._payload())
        assert any("fixture" in p and "SEISMIC" in p
                   for p in problems)

    def test_divergent_retro_class_rejected(self):
        p = self._payload()
        p["config"]["retrospective_data_class"] = "REANALYSIS"
        problems = validate_producer_payload(p)
        assert any("retrospective_data_class" in pr
                   for pr in problems)

    def test_forecast_config_with_seismic_class_rejected(self):
        p = self._payload(mode="FORECAST_REGIME",
                          data_class="ARCHIVED_OPERATIONAL")
        p["config"]["mode"] = "FORECAST_REGIME"
        p["config"]["retrospective_data_class"] = SEISMIC
        problems = validate_producer_payload(p)
        assert any("retrospective_data_class" in pr or
                   "FORECAST" in pr for pr in problems)


class TestAssociationLane:
    """The association adapter admits associable REANALYSIS
    partitions only — a seismic artifact is non-associable by
    construction and rejected."""

    def test_seismic_artifact_rejected_by_adapter(self):
        from nepal.experiment_v0.adapters import (
            regime_assignment_from_artifact)
        # A payload asserting seismic provenance with an honest
        # associable=false flag fails the producer floor's own
        # binding — and a payload claiming associable=true fails the
        # SEISMIC-01 status machine.  Either way it cannot adapt.
        payload = self._seismic_payload(associable=True)
        with pytest.raises(ValueError):
            regime_assignment_from_artifact(
                payload, artifact_id="a-seis")

    def _seismic_payload(self, **kw) -> dict:
        p = {
            "status": "DESCRIPTIVE_REGIME_ONLY",
            "mode": "RETROSPECTIVE_REGIME",
            "data_class": SEISMIC,
            "terminal": True,
            "associable": False,
            "frozen": True,
            "assignments": [{"unit_id": "u", "date": "2020-01-01",
                             "k": 1}],
            "label_blinding": True,
            "fitted_on": "TRAIN_ONLY",
        }
        p.update(kw)
        return p
