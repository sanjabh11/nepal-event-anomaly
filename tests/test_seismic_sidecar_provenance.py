"""SEISMIC-PROV — feature-generation provenance tests (S06).

The frame must be cryptographically bound to the bytes it came
from: mutating the frame while keeping bytes, swapping bytes while
keeping the frame, changing config, or changing parser all break
``feature_generation_digest``.  The real path produces its own
frame — a caller-prepared frame cannot ride a real manifest.
"""
from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import nepal.seismic_sidecar as ss
import nepal.seismic_sidecar.io as io_mod
import nepal.seismic_sidecar.provenance as prov
from nepal.seismic_sidecar.contracts import SeismicSidecarConfig

from tests.test_seismic_sidecar_io import (
    E0, _evidence, _miniseed_file, _stationxml)


_BANDS = ((1.0, 4.0), (4.0, 10.0))


def _cfg(**kw):
    base = dict(
        window_seconds=10, sta_seconds=2.0, lta_seconds=5.0,
        min_coverage_fraction=0.5, min_snr_db=-200.0,
        bands=_BANDS,
        heldout_stations=("STA4",),
        waveform_relpaths=("wave/STA1.mseed", "wave/STA2.mseed",
                           "wave/STA3.mseed", "wave/STA4.mseed"),
        response_relpaths=("resp/all.xml",))
    base.update(kw)
    return SeismicSidecarConfig(**base)


def _fixture(tmp_path, stations=("STA1", "STA2", "STA3", "STA4"),
             seconds=1200):
    files = {}
    from datetime import timedelta
    for i, s in enumerate(stations):
        # One segment per channel crossing midnight — a fully
        # covered record that still spans two calendar dates, so
        # the median split yields an early cell plus a held-out
        # late tail per station.
        files[f"wave/{s}.mseed"] = _miniseed_file(
            station=s, rate=50, seed=i,
            spans=[(E0 + timedelta(hours=23, minutes=55), seconds)])
    # one StationXML covering every station
    import tests.test_seismic_sidecar_io as tio
    files["resp/all.xml"] = tio._multi_stationxml(
        stations, rate=50)
    allowlist = list(ss.SEISMIC_NONBAND_FEATURES) + \
        list(ss.band_feature_names(_BANDS))
    return _evidence(tmp_path, files,
                     feature_allowlist=allowlist)


def _bundle(tmp_path, cfg=None):
    cfg = cfg or _cfg()
    root, manifest = _fixture(tmp_path)
    b = io_mod.read_verified_waveform_bundle(
        evidence_root=root, source_manifest=manifest,
        waveform_relpaths=cfg.waveform_relpaths,
        response_relpaths=cfg.response_relpaths,
        config=cfg)
    assert b.problems == ()
    return b, manifest, root, cfg


class TestGenerationBinding:
    def test_happy_artifact_verifies(self, tmp_path):
        b, manifest, root, cfg = _bundle(tmp_path)
        floors = _floors(b, manifest, cfg)
        art = prov.build_seismic_feature_artifact(
            b, cfg, noise_floor_by_station=floors)
        assert art.frame_rows
        assert len(art.feature_generation_digest) == 64
        assert len(art.semantic_feature_digest) == 64
        assert art.dropped_ledger_digest
        assert prov.verify_feature_generation(
            art, b, cfg, noise_floor_by_station=floors) == []

    def test_frame_mutation_breaks_binding(self, tmp_path):
        b, manifest, root, cfg = _bundle(tmp_path)
        floors = _floors(b, manifest, cfg)
        art = prov.build_seismic_feature_artifact(
            b, cfg, noise_floor_by_station=floors)
        mutated = list(art.frame_rows)
        mutated[0] = dict(mutated[0], seis_rsam=9e9)
        bad = dataclasses.replace(
            art, frame_rows=tuple(mutated))
        problems = prov.verify_feature_generation(
            bad, b, cfg, noise_floor_by_station=floors)
        assert any("mutated" in p or "semantic" in p
                   for p in problems)

    def test_byte_substitution_breaks_binding(self, tmp_path):
        b, manifest, root, cfg = _bundle(tmp_path)
        floors = _floors(b, manifest, cfg)
        art = prov.build_seismic_feature_artifact(
            b, cfg, noise_floor_by_station=floors)
        other = dataclasses.replace(
            b, waveform_bytes_digest="0" * 64)
        problems = prov.verify_feature_generation(
            art, other, cfg, noise_floor_by_station=floors)
        assert any("waveform" in p for p in problems)

    def test_config_change_breaks_binding(self, tmp_path):
        b, manifest, root, cfg = _bundle(tmp_path)
        floors = _floors(b, manifest, cfg)
        art = prov.build_seismic_feature_artifact(
            b, cfg, noise_floor_by_station=floors)
        other_cfg = _cfg(window_seconds=20)
        problems = prov.verify_feature_generation(
            art, b, other_cfg, noise_floor_by_station=floors)
        assert any("configuration" in p for p in problems)

    def test_no_floor_station_drops_into_ledger(self, tmp_path):
        b, manifest, root, cfg = _bundle(tmp_path)
        floors = _floors(b, manifest, cfg)
        floors.pop("STA4", None)
        art = prov.build_seismic_feature_artifact(
            b, cfg, noise_floor_by_station=floors)
        stations_in_frame = {r["station_id"]
                             for r in art.frame_rows}
        assert "STA4" not in stations_in_frame
        assert any(d["station_id"] == "STA4"
                   for d in art.dropped_windows)


def _floors(bundle, manifest, cfg):
    floors = {}
    for sta in sorted({t.station for t in bundle.traces}):
        rec = io_mod.derive_station_observability(
            bundle=bundle, station_id=sta,
            target_latitude=28.0, target_longitude=85.0,
            source_manifest_digest_value=
            io_mod.source_manifest_digest(manifest),
            source_manifest=manifest, config=cfg)
        vals = list(rec.noise_floor_by_band.values())
        if vals:
            floors[sta] = float(vals[0])
    return floors


class TestRealPath:
    def test_receipt_and_artifact(self, tmp_path):
        b, manifest, root, cfg = _bundle(tmp_path)
        receipt, art = prov.run_seismic_real_path(
            evidence_root=root, source_manifest=manifest,
            train_mask=None, seismic_config=cfg,
            station_selectors=(),
            station_metadata={
                s: {"target_latitude": 28.0,
                    "target_longitude": 85.0,
                    "station_latitude": 27.9,
                    "station_longitude": 85.3}
                for s in ("STA1", "STA2", "STA3", "STA4")})
        assert receipt["record_type"] == \
            "SEISMIC_DETECTION_RECEIPT_V0"
        # An honest terminal state — synthetic single-day evidence
        # cannot reach descriptive power, but the run must not die
        # on a contract violation either.
        assert receipt["status"] in (
            "CANDIDATE_ONLY", "UNDERPOWERED_DESCRIPTIVE_ONLY",
            "UNOBSERVABLE")
        assert receipt["status"] != "RUN_ERROR" or \
            not receipt["problems"]
        # The real path produced its own frame — provenance bound.
        assert art.frame_rows
        assert art.feature_generation_digest
        # The receipt must never carry authority flags.
        for flag in ("promotion_eligible", "production_authorized",
                     "warning_path_authorized"):
            assert receipt[flag] is False

    def test_audit_gate_flags_forged_frame_pairing(self, tmp_path):
        """S06 advisory gate: a byte-bound manifest + caller frame
        with no generation artifact must be flagged — the frame
        could be forged against real bytes."""
        b, manifest, root, cfg = _bundle(tmp_path)
        problems = prov.audit_frame_provenance(
            source_manifest=manifest, artifact=None)
        assert any("generation" in p for p in problems)
        # With a bound artifact the same manifest is honest.
        floors = _floors(b, manifest, cfg)
        art = prov.build_seismic_feature_artifact(
            b, cfg, noise_floor_by_station=floors)
        assert prov.audit_frame_provenance(
            source_manifest=manifest, artifact=art) == []
        # An unbound artifact is also flagged.
        unbound = dataclasses.replace(
            art, feature_generation_digest="")
        assert prov.audit_frame_provenance(
            source_manifest=manifest, artifact=unbound)

    def test_no_caller_frame_on_real_path(self):
        """Structural closure of S06: the real path's signature has
        NO feature_frame parameter — a caller-prepared frame can
        never ride a byte-bound manifest through this entry."""
        import inspect
        params = set(inspect.signature(
            prov.run_seismic_real_path).parameters)
        assert "feature_frame" not in params
        assert "frame" not in params

    def test_response_waiver_rejects_real_manifest(self, tmp_path):
        cfg = _cfg(require_response=False)
        root, manifest = _fixture(tmp_path)
        receipt, art = prov.run_seismic_real_path(
            evidence_root=root, source_manifest=manifest,
            train_mask=None, seismic_config=cfg)
        assert receipt["status"] == "UNOBSERVABLE"
        assert any("response waiver" in p
                   for p in receipt["problems"])

    def test_bundle_failure_is_unobservable(self, tmp_path):
        cfg = _cfg()
        root, manifest = _fixture(tmp_path)
        # Corrupt the waveform bytes post-manifest.
        (root / "wave" / "STA1.mseed").write_bytes(b"\x00" * 8)
        receipt, art = prov.run_seismic_real_path(
            evidence_root=root, source_manifest=manifest,
            train_mask=None, seismic_config=cfg)
        assert receipt["status"] == "UNOBSERVABLE"
        assert receipt["problems"]
