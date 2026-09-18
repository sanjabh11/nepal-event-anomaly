"""Contract tests for the seismic sidecar — vocabulary, records,
receipt shape, and the SEISMIC_WAVEFORM_RETROSPECTIVE data-class
extension.

Research-only boundary: these tests prove the contract surface and
the fail-closed gates.  No waveform retrieval, no seismic predictor
claim, no authority flags.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nepal.research_v0._hashing import verify_source_evidence
from nepal.research_v0.policy import (
    SEISMIC_WAVEFORM_RETROSPECTIVE_CLASS)
from nepal.research_v0.records import (
    FORECAST_REGIME_DATA_CLASSES, RETROSPECTIVE_REGIME_DATA_CLASSES,
    RegimeArtifactV0)
from nepal.research_v0.source_intake import build_source_manifest
from nepal.science_v0.fmx_audit import ColumnAudit, audit_matrix
import nepal.seismic_sidecar as ss

SEISMIC = SEISMIC_WAVEFORM_RETROSPECTIVE_CLASS


def _evidence_root() -> Path:
    root = Path(tempfile.mkdtemp(prefix="seis-contract-"))
    (root / "wave").mkdir()
    (root / "resp").mkdir()
    for name in ("STA1.bin", "STA2.bin"):
        (root / "wave" / name).write_bytes(b"\x00\x01synthetic" * 8)
    (root / "resp" / "all.xml").write_bytes(b"<StationXML/>synthetic")
    (root / "retrieval.json").write_text(
        json.dumps({"retrieved": "synthetic"}))
    (root / "LICENSE").write_text("CC-BY synthetic")
    return root


def _manifest(root: Path) -> dict:
    def sha(p):
        return hashlib.sha256(p.read_bytes()).hexdigest()
    files = [
        {"relpath": f"wave/{s}.bin",
         "sha256": sha(root / "wave" / f"{s}.bin")}
        for s in ("STA1", "STA2")]
    files += [
        {"relpath": "resp/all.xml",
         "sha256": sha(root / "resp" / "all.xml")},
        {"relpath": "retrieval.json",
         "sha256": sha(root / "retrieval.json")},
        {"relpath": "LICENSE", "sha256": sha(root / "LICENSE")}]
    return build_source_manifest(
        root, source_id="fdsn-synthetic", source_version="0.1",
        source_files=files, units=["STA1", "STA2"],
        feature_allowlist=list(ss.SEISMIC_VALUE_FEATURES),
        lineage="synthetic byte-bound seismic root")


def _artifact_dict(**kw):
    base = dict(
        regime_id="r1",
        mode="RETROSPECTIVE_REGIME",
        preprocessing_digest="a" * 64,
        data_class="REANALYSIS",
        seeds=(1, 2, 3),
        k=1)
    base.update(kw)
    return base


class TestReceiptContract:
    def test_receipt_skeleton_exact_surface(self):
        receipt = ss.seismic_receipt_skeleton()
        assert set(receipt) == {
            "record_type", "status", "claim_scope",
            "source_manifest_digest", "config_digest",
            "station_observability_digest", "raw_waveform_digest",
            "semantic_feature_digest", "catalog_context_digest",
            "regime_artifact_digest", "report_digest",
            "promotion_eligible", "production_authorized",
            "warning_path_authorized", "problems"}
        assert receipt["record_type"] == "SEISMIC_DETECTION_RECEIPT_V0"
        assert receipt["status"] == "RUN_ERROR"
        assert receipt["claim_scope"] == \
            "research_only_post_initiation_detection"
        assert receipt["promotion_eligible"] is False
        assert receipt["production_authorized"] is False
        assert receipt["warning_path_authorized"] is False

    def test_receipt_statuses_vocabulary(self):
        assert ss.RECEIPT_STATUSES == frozenset({
            "RUN_ERROR", "UNOBSERVABLE", "CANDIDATE_ONLY",
            "UNDERPOWERED_DESCRIPTIVE_ONLY",
            "DESCRIPTIVE_REGIME_ONLY"})

    def test_seismic_class_is_policy_constant(self):
        assert ss.SEISMIC_WAVEFORM_RETROSPECTIVE == \
            "SEISMIC_WAVEFORM_RETROSPECTIVE"


class TestFeatureVocabulary:
    _DENYLIST = re.compile(
        r"exposure|impact|rank|ranked|priority|score|b_screen|"
        r"top_five|severity|damage|casualt|fatalit|economic|"
        r"post_event|after_event")

    def test_feature_names_carry_no_forbidden_vocabulary(self):
        for col in ss.SEISMIC_VALUE_FEATURES:
            assert not self._DENYLIST.search(col), col
        for col in ss.SEISMIC_DERIVED_COLUMNS:
            assert not self._DENYLIST.search(col), col

    def test_catalog_context_is_disjoint_from_features(self):
        assert not (set(ss.CATALOG_CONTEXT_COLUMNS) &
                    set(ss.SEISMIC_VALUE_FEATURES))
        assert not (set(ss.CATALOG_CONTEXT_COLUMNS) &
                    set(ss.SEISMIC_IDENTITY_COLUMNS))

    def test_every_default_feature_has_a_declared_unit(self):
        for col in ss.SEISMIC_VALUE_FEATURES:
            assert col in ss.SEISMIC_FEATURE_UNITS, col
            assert ss.SEISMIC_FEATURE_UNITS[col].strip()

    def test_band_names_derive_from_declared_bands(self):
        names = ss.band_feature_names(((0.5, 2.0), (2.0, 8.0)))
        assert names == ("seis_band_0p5_2hz", "seis_band_2_8hz")

    def test_fmx_audit_accepts_declared_seismic_columns(self):
        """The declared observation_metadata seam: a seismic daily
        frame with honest metadata produces no reject verdicts."""
        rng = np.random.default_rng(0)
        n = 40
        columns = {
            "seis_rsam": list(np.abs(rng.normal(1.0, 0.2, n))),
            "seis_snr_db": list(rng.normal(15.0, 2.0, n)),
            "seis_coverage_fraction": list(
                np.clip(rng.normal(0.97, 0.02, n), 0, 1))}
        audits = [ColumnAudit(
            column_name=c,
            declared_field_class="observation_metadata",
            source_lineage=("fdsn-synthetic", "0.1",
                            "seismic_sidecar_window"),
            availability_semantics=(
                "coverage-qualified one-minute windows aggregated "
                "to daily grain; unobservable windows absent"),
            unit=ss.SEISMIC_FEATURE_UNITS[c],
            value_domain="finite float",
            temporal_window=("2020-06-01T00:00:00Z",
                             "2020-07-01T00:00:00Z"),
            missingness_policy="listwise")
            for c in columns]
        verdicts = audit_matrix(
            columns, audits, cutoff_iso=None,
            preprocessing_provenance={c: "train_only"
                                      for c in columns})
        rejects = [v for v in verdicts if v.verdict == "reject"]
        assert not rejects, [
            (v.column_name, v.reasons) for v in rejects]


class TestManifestContract:
    def test_manifest_is_exactly_seven_keys_and_byte_verified(self):
        root = _evidence_root()
        man = _manifest(root)
        assert set(man) == {
            "source_id", "source_digests", "units",
            "feature_allowlist", "lineage", "evidence_root",
            "source_files"}
        assert verify_source_evidence(man) == []

    def test_digest_mutation_fails_verification(self):
        root = _evidence_root()
        man = _manifest(root)
        bad = dict(man)
        bad["source_files"] = [dict(f) for f in man["source_files"]]
        bad["source_files"][0]["sha256"] = "0" * 64
        assert verify_source_evidence(bad)

    def test_path_escape_rejected(self):
        root = _evidence_root()
        man = _manifest(root)
        bad = dict(man)
        bad["source_files"] = list(man["source_files"]) + [
            {"relpath": "../outside.bin", "sha256": "0" * 64}]
        bad["source_digests"] = list(man["source_digests"]) + \
            ["0" * 64]
        assert verify_source_evidence(bad)

    def test_source_manifest_digest_binding(self):
        root = _evidence_root()
        man = _manifest(root)
        d = ss.source_manifest_digest(man)
        assert re.fullmatch(r"[0-9a-f]{64}", d)
        other = dict(man, lineage="mutated lineage")
        assert ss.source_manifest_digest(other) != d


class TestConfigValidation:
    def test_defaults_validate_clean(self):
        assert ss.SeismicSidecarConfig().validate() == []

    @pytest.mark.parametrize("kw", [
        {"window_seconds": 0},
        {"bands": ((2.0, 1.0),)},
        {"sta_seconds": 30.0, "lta_seconds": 5.0},
        {"lta_seconds": 90.0, "window_seconds": 60},
        {"min_coverage_fraction": 1.5},
        {"max_gap_fraction": 1.0},
        {"seeds": (1, 2)},
        {"k_candidates": (2, 3)},
        {"min_trained_stations": 2},
        {"missingness_policy": "impute_anything"},
        {"catalog_ablation": True},
        {"catalog_cols": ("not_a_catalog_col",)},
        {"seeds": ([1], 2, 3)},
        {"catalog_cols": ([1],)},
        {"require_response": "false"},
        {"require_station_holdout": 1},
        {"catalog_ablation": "false"},
        {"waveform_relpaths": ("wave/a.bin", "wave/a.bin")},
        {"response_relpaths": ("resp/a.xml", "resp/a.xml")},
        {"waveform_relpaths": ("same.bin",),
         "response_relpaths": ("same.bin",)},
        {"waveform_relpaths": ("wave/a.bin",),
         "response_relpaths": ()},
        {"heldout_stations": ("STA1", "STA1")},
        {"catalog_cols": ("catalog_event_count",
                           "catalog_event_count")},
    ])
    def test_malformed_configs_rejected(self, kw):
        assert ss.SeismicSidecarConfig(**kw).validate()


class TestDataClassExtension:
    """SEISMIC_WAVEFORM_RETROSPECTIVE: admitted on the retrospective
    lane only, never on forecast paths; weather defaults unchanged."""

    def test_retrospective_vocabulary(self):
        assert RETROSPECTIVE_REGIME_DATA_CLASSES == frozenset(
            {"REANALYSIS", "SEISMIC_WAVEFORM_RETROSPECTIVE"})
        assert "SEISMIC_WAVEFORM_RETROSPECTIVE" not in \
            FORECAST_REGIME_DATA_CLASSES

    def test_artifact_record_admits_seismic_retrospective(self):
        art = RegimeArtifactV0(**_artifact_dict(
            data_class=SEISMIC))
        assert not [p for p in art.problems()
                    if "data class" in p or "reanalysis" in p]

    def test_artifact_record_rejects_seismic_on_forecast(self):
        art = RegimeArtifactV0(**_artifact_dict(
            mode="FORECAST_REGIME", data_class=SEISMIC))
        assert any("forecast" in p.lower() for p in art.problems())

    def test_artifact_record_rejects_undeclared_class(self):
        art = RegimeArtifactV0(**_artifact_dict(
            data_class="CURRENT_FEED"))
        assert any("retrospective" in p.lower()
                   for p in art.problems())

    def test_weather_default_unchanged(self):
        """The weather/GLOF lane still declares REANALYSIS — the
        seismic class did not alter the default."""
        art = RegimeArtifactV0(**_artifact_dict())
        assert not [p for p in art.problems()
                    if "data class" in p or "reanalysis" in p]

    def test_run_regimes_rejects_undeclared_retro_class(self):
        from nepal.science_v0.regimes import (
            RegimeRunConfig, run_regimes)
        df = pd.DataFrame({
            "unit_id": ["u1", "u2"],
            "date": ["2020-01-01", "2020-01-02"],
            "basin_group": ["g0", "g1"],
            "season": ["JJA", "JJA"],
            "f1": [0.0, 1.0]})
        cfg = RegimeRunConfig(
            era_col=None,
            era_waiver_reason="single epoch — waived",
            bootstrap_block_len=12,
            retrospective_data_class="NOT_A_CLASS",
            source_manifest={"fixture": True})
        rep = run_regimes(df, ["f1"],
                          np.array([True, True]), cfg)
        assert rep["status"] == "RUN_ERROR"
        assert "retrospective_data_class" in rep["reason"]

    def test_run_regimes_rejects_seismic_on_forecast_mode(self):
        from nepal.science_v0.regimes import (
            RegimeRunConfig, run_regimes)
        df = pd.DataFrame({
            "unit_id": ["u1"], "date": ["2020-01-01"],
            "basin_group": ["g0"], "season": ["JJA"], "f1": [0.0]})
        cfg = RegimeRunConfig(
            era_col=None,
            era_waiver_reason="single epoch — waived",
            bootstrap_block_len=12,
            mode="FORECAST_REGIME",
            retrospective_data_class=SEISMIC,
            source_manifest={"fixture": True})
        rep = run_regimes(df, ["f1"], np.array([True]), cfg)
        assert rep["status"] == "RUN_ERROR"
        assert "FORECAST_REGIME" in rep["reason"]

    def test_run_regimes_rejects_seismic_with_fixture_manifest(self):
        from nepal.science_v0.regimes import (
            RegimeRunConfig, run_regimes)
        df = pd.DataFrame({
            "unit_id": ["u1"], "date": ["2020-01-01"],
            "basin_group": ["g0"], "season": ["JJA"], "f1": [0.0]})
        cfg = RegimeRunConfig(
            era_col=None,
            era_waiver_reason="single epoch — waived",
            bootstrap_block_len=12,
            retrospective_data_class=SEISMIC,
            source_manifest={"fixture": True})
        rep = run_regimes(df, ["f1"], np.array([True]), cfg)
        assert rep["status"] == "RUN_ERROR"
        assert "non-fixture" in rep["reason"]

    def test_config_field_is_serialized(self):
        from nepal.science_v0.regimes import RegimeRunConfig
        cfg = RegimeRunConfig(
            retrospective_data_class=SEISMIC)
        d = dataclasses.asdict(cfg)
        assert "retrospective_data_class" in d
        assert d["retrospective_data_class"] == SEISMIC
        # forecast_vintages is popped from the serialized surface —
        # the bound contract is exactly 33 fields.
        d.pop("forecast_vintages", None)
        from nepal.research_v0.producer_validation import (
            _CONFIG_FIELDS)
        assert set(d) == set(_CONFIG_FIELDS)

    def test_freeze_rejects_seismic_hand_built_artifact(self):
        """A hand-assembled seismic artifact can never be frozen —
        freeze is an audit, not a stamp: the provenance floor
        refuses the incomplete surface before certification."""
        from nepal.science_v0.regimes import freeze_regime_artifact
        artifact = _artifact_dict(data_class=SEISMIC)
        artifact["source_manifest"] = {"fixture": True}
        with pytest.raises(ValueError):
            freeze_regime_artifact(artifact)

    def test_shared_floor_rejects_seismic_associable_descriptive(
            self):
        """A seismic artifact claiming DESCRIPTIVE + associable is a
        producer-floor violation — the status machine is
        data-class aware."""
        from nepal.research_v0.producer_validation import (
            _scalar_floor_problems)
        payload = {
            "status": "DESCRIPTIVE_REGIME_ONLY",
            "terminal": True, "associable": True,
            "mode": "RETROSPECTIVE_REGIME",
            "data_class": SEISMIC}
        problems = _scalar_floor_problems(payload)
        assert any("associable" in p or "status" in p
                   for p in problems)

    def test_shared_floor_accepts_seismic_descriptive_nonassociable(
            self):
        from nepal.research_v0.producer_validation import (
            _scalar_floor_problems)
        payload = {
            "status": "DESCRIPTIVE_REGIME_ONLY",
            "terminal": True, "associable": False,
            "mode": "RETROSPECTIVE_REGIME",
            "data_class": SEISMIC}
        assert not [p for p in _scalar_floor_problems(payload)
                    if "status" in p and "requires" in p]

    def test_shared_floor_rejects_seismic_fixture_manifest(self):
        from nepal.research_v0.producer_validation import (
            _source_manifest_problems)
        payload = {"source_manifest": {"fixture": True},
                   "data_class": SEISMIC}
        problems = _source_manifest_problems(
            payload, verify_source_bytes=False)
        assert any("SEISMIC" in p for p in problems)


class TestObservabilityContract:
    def test_record_type_tag_and_field_surface(self):
        obs = ss.build_station_observability(
            station_id="NK.KKN", network="NK", station="KKN",
            channel_set=("BHE", "BHN", "BHZ"),
            station_latitude=27.8, station_longitude=85.279,
            target_latitude=28.288708, target_longitude=85.528159,
            sample_rate_hz=50.0,
            response_relpath="resp/kkn.xml", response_sha256="a" * 64,
            coverage_start="2020-01-01T00:00:00Z",
            coverage_end="2020-02-01T00:00:00Z",
            coverage_fraction=0.95, gap_fraction=0.05,
            latency_seconds=0.0,
            noise_floor_by_band={"2-8": 1e-6},
            snr_by_band={"2-8": 12.0},
            source_manifest_digest="b" * 64)
        d = obs.to_dict()
        assert d["record_type"] == "StationObservabilityV0"
        expected = {
            "station_id", "network", "station", "location",
            "channel_set", "target_latitude", "target_longitude",
            "distance_km", "component_count", "sample_rate_hz",
            "response_relpath", "response_sha256",
            "coverage_start", "coverage_end", "coverage_fraction",
            "gap_fraction", "latency_seconds",
            "noise_floor_by_band", "snr_by_band",
            "orientation_status", "response_status", "status",
            "source_manifest_digest", "problems", "record_type"}
        assert set(d) == expected
        rt = ss.station_observability_from_dict(d)
        assert rt == obs

    def test_deserialize_rejects_wrong_record_type(self):
        with pytest.raises(ValueError):
            ss.station_observability_from_dict(
                {"record_type": "SomethingElse"})

    def test_deserialize_rejects_unknown_and_missing_fields(self):
        obs = ss.build_station_observability(
            station_id="S", network="N", station="S",
            channel_set=("BHE", "BHN", "BHZ"),
            station_latitude=0.0, station_longitude=0.0,
            target_latitude=0.0, target_longitude=0.0,
            sample_rate_hz=50.0,
            response_relpath="r", response_sha256="a" * 64,
            coverage_start="2020-01-01T00:00:00Z",
            coverage_end="2020-01-02T00:00:00Z",
            coverage_fraction=1.0, gap_fraction=0.0,
            latency_seconds=0.0,
            noise_floor_by_band={}, snr_by_band={"x": 5.0},
            source_manifest_digest="b" * 64)
        d = obs.to_dict()
        extra = dict(d, sneaky_field=1)
        with pytest.raises(ValueError):
            ss.station_observability_from_dict(extra)
        missing = {k: v for k, v in d.items()
                   if k != "distance_km"}
        with pytest.raises(ValueError):
            ss.station_observability_from_dict(missing)

    def test_great_circle_km(self):
        d = ss.great_circle_km(28.288708, 85.528159, 27.8, 85.279)
        assert 55.0 < d < 65.0
        assert ss.great_circle_km(0, 0, 0, 0) == pytest.approx(0.0)
