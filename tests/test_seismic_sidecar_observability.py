"""Observability-gate tests for the seismic sidecar.

Every gate is fail-closed: missing channels, missing response,
excessive gaps, failed SNR, invalid timestamps, or unavailable
waveform bytes produce UNOBSERVABLE — never a zero-valued feature.
"""
from __future__ import annotations

import pytest

import nepal.seismic_sidecar as ss

_KW = dict(
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


def _build(**kw):
    merged = dict(_KW)
    merged.update(kw)
    return ss.build_station_observability(**merged)


class TestObservablePath:
    def test_valid_record_is_observable(self):
        obs = _build()
        assert obs.status == "OBSERVABLE"
        assert obs.problems == ()
        assert obs.orientation_status == "ORTHO_3C"
        assert obs.response_status == "BOUND"
        assert obs.component_count == 3
        assert obs.distance_km == pytest.approx(59.6, abs=2.0)
        assert obs.validate() == []

    def test_rotated_horizontal_components_observable(self):
        """XQ-style HH1/HH2/HHZ: rotated horizontals are still a
        three-component record — flagged as ROTATED_3C, never
        relabelled E/N."""
        obs = _build(channel_set=("HH1", "HH2", "HHZ"),
                     sample_rate_hz=200.0)
        assert obs.status == "OBSERVABLE"
        assert obs.orientation_status == "ROTATED_3C"

    def test_response_not_required_when_policy_waived(self):
        cfg = ss.SeismicSidecarConfig(require_response=False)
        obs = _build(response_relpath="", response_sha256="",
                     config=cfg)
        assert obs.status == "OBSERVABLE"
        assert obs.response_status == "MISSING"


class TestUnobservableGates:
    def test_two_component_record_rejected(self):
        obs = _build(channel_set=("BHE", "BHN"))
        assert obs.status == "UNOBSERVABLE"
        assert any("component" in p for p in obs.problems)

    def test_empty_channel_set_rejected(self):
        obs = _build(channel_set=())
        assert obs.status == "UNOBSERVABLE"

    def test_duplicate_channels_rejected(self):
        obs = _build(channel_set=("BHE", "BHE", "BHZ"))
        assert obs.status == "UNOBSERVABLE"
        assert any("duplicate" in p for p in obs.problems)

    def test_missing_response_rejected(self):
        obs = _build(response_relpath="", response_sha256="")
        assert obs.status == "UNOBSERVABLE"
        assert any("response" in p.lower() for p in obs.problems)
        assert obs.response_status == "MISSING"

    def test_gap_fraction_exceeds_max(self):
        obs = _build(gap_fraction=0.5, coverage_fraction=0.95)
        assert obs.status == "UNOBSERVABLE"
        assert any("gap_fraction" in p for p in obs.problems)

    def test_coverage_below_minimum(self):
        obs = _build(coverage_fraction=0.4, gap_fraction=0.05)
        assert obs.status == "UNOBSERVABLE"
        assert any("coverage_fraction" in p for p in obs.problems)

    def test_low_snr_rejected(self):
        obs = _build(snr_by_band={"2-8": -5.0})
        assert obs.status == "UNOBSERVABLE"
        assert any("snr_by_band" in p for p in obs.problems)

    def test_nonfinite_snr_rejected(self):
        obs = _build(snr_by_band={"2-8": float("nan")})
        assert obs.status == "UNOBSERVABLE"

    def test_empty_snr_map_rejected_under_policy(self):
        obs = _build(snr_by_band={})
        assert obs.status == "UNOBSERVABLE"
        assert any("snr_by_band" in p for p in obs.problems)

    def test_inverted_timestamps_rejected(self):
        obs = _build(coverage_start="2020-02-01T00:00:00Z",
                     coverage_end="2020-01-01T00:00:00Z")
        assert obs.status == "UNOBSERVABLE"

    def test_naive_timestamps_rejected(self):
        obs = _build(coverage_start="2020-01-01 00:00:00")
        assert obs.status == "UNOBSERVABLE"

    def test_malformed_timestamps_rejected(self):
        obs = _build(coverage_end="not-a-date")
        assert obs.status == "UNOBSERVABLE"

    def test_waveform_unavailable_rejected(self):
        obs = _build(waveform_available=False)
        assert obs.status == "UNOBSERVABLE"
        assert any("waveform" in p for p in obs.problems)

    def test_latency_ceiling(self):
        cfg = ss.SeismicSidecarConfig(max_latency_seconds=10.0)
        obs = _build(latency_seconds=30.0, config=cfg)
        assert obs.status == "UNOBSERVABLE"
        assert any("latency" in p for p in obs.problems)

    def test_nonfinite_noise_floor_rejected(self):
        obs = _build(noise_floor_by_band={"2-8": float("inf")})
        assert obs.status == "UNOBSERVABLE"


class TestCarriedRecordValidation:
    def test_observable_record_cannot_carry_problems(self):
        obs = ss.StationObservabilityV0(
            station_id="S", network="N", station="S",
            channel_set=("BHE", "BHN", "BHZ"),
            target_latitude=0.0, target_longitude=0.0,
            distance_km=1.0, component_count=3,
            sample_rate_hz=50.0,
            response_relpath="r", response_sha256="a" * 64,
            coverage_start="2020-01-01T00:00:00Z",
            coverage_end="2020-01-02T00:00:00Z",
            coverage_fraction=1.0, gap_fraction=0.0,
            latency_seconds=0.0,
            noise_floor_by_band={}, snr_by_band={"x": 1.0},
            orientation_status="ORTHO_3C", response_status="BOUND",
            status="OBSERVABLE",
            source_manifest_digest="b" * 64,
            problems=("smuggled problem",))
        assert obs.validate()

    def test_bad_manifest_digest_rejected(self):
        obs = ss.StationObservabilityV0(
            station_id="S", network="N", station="S",
            channel_set=("BHE", "BHN", "BHZ"),
            target_latitude=0.0, target_longitude=0.0,
            distance_km=1.0, component_count=3,
            sample_rate_hz=50.0,
            coverage_start="2020-01-01T00:00:00Z",
            coverage_end="2020-01-02T00:00:00Z",
            coverage_fraction=1.0, gap_fraction=0.0,
            latency_seconds=0.0,
            noise_floor_by_band={}, snr_by_band={},
            orientation_status="ORTHO_3C", response_status="BOUND",
            status="UNOBSERVABLE",
            source_manifest_digest="not-a-sha",
            problems=("x",))
        assert any("source_manifest_digest" in p
                   for p in obs.validate())

    def test_unknown_status_rejected(self):
        obs = ss.StationObservabilityV0(
            station_id="S", network="N", station="S",
            channel_set=("BHE", "BHN", "BHZ"),
            target_latitude=0.0, target_longitude=0.0,
            distance_km=1.0, component_count=3,
            sample_rate_hz=50.0,
            coverage_start="2020-01-01T00:00:00Z",
            coverage_end="2020-01-02T00:00:00Z",
            coverage_fraction=1.0, gap_fraction=0.0,
            latency_seconds=0.0,
            noise_floor_by_band={}, snr_by_band={},
            orientation_status="ORTHO_3C", response_status="BOUND",
            status="EVIDENCE_VERIFIED",
            source_manifest_digest="b" * 64)
        assert obs.validate()


class TestOrientationDerivation:
    @pytest.mark.parametrize("channels,expected", [
        (("BHE", "BHN", "BHZ"), "ORTHO_3C"),
        (("HH1", "HH2", "HHZ"), "ROTATED_3C"),
        (("BHE", "BHZ"), "PARTIAL"),
        ((), "MISSING"),
    ])
    def test_orientation_classes(self, channels, expected):
        assert ss.derive_orientation_status(channels) == expected
