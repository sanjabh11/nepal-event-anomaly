"""Feature-extraction tests for the seismic sidecar.

Pure NumPy window features — sinusoid recovery, energy statistics,
rejection of malformed input, coverage semantics, cross-station
coherence, digest order-invariance, and the label/catalog exclusion
boundary.  No waveform retrieval, no predictor claim.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

import nepal.seismic_sidecar as ss

FS = 50.0
BANDS = ((0.5, 2.0), (2.0, 8.0), (8.0, 20.0))
_KW = dict(noise_floor_rms=0.01, sta_seconds=5.0, lta_seconds=30.0,
           bands=BANDS)


def _sine(freq, n, amp=1.0, fs=FS, seed=None):
    t = np.arange(n) / fs
    sig = amp * np.sin(2 * np.pi * freq * t)
    if seed is not None:
        sig = sig + 0.01 * np.random.default_rng(seed) \
            .standard_normal(n)
    return sig


def _three(sig):
    rng = np.random.default_rng(7)
    return np.stack([sig,
                     sig + 0.005 * rng.standard_normal(len(sig)),
                     sig + 0.005 * rng.standard_normal(len(sig))],
                    axis=1)


class TestWindowFeatures:
    def test_sinusoid_dominant_frequency_recovered(self):
        sig = _sine(10.0, int(60 * FS), seed=1)
        feats = ss.window_features(
            _three(sig), FS, expected_samples=int(60 * FS), **_KW)
        assert feats["seis_dominant_hz"] == pytest.approx(10.0,
                                                        abs=0.5)
        assert feats["seis_band_8_20hz"] > 0.9
        assert feats["seis_band_0p5_2hz"] < 0.05

    def test_low_band_signal_lands_in_low_band(self):
        sig = _sine(1.0, int(60 * FS), seed=2)
        feats = ss.window_features(
            _three(sig), FS, expected_samples=int(60 * FS), **_KW)
        assert feats["seis_band_0p5_2hz"] > 0.8

    def test_rsam_and_crest_factor_exact(self):
        sig = _sine(5.0, int(60 * FS))
        feats = ss.window_features(
            _three(sig), FS, expected_samples=int(60 * FS), **_KW)
        # RMS of a unit-amplitude sine is 1/sqrt(2); crest ~ sqrt(2).
        assert feats["seis_rsam"] == pytest.approx(1.0 / math.sqrt(2),
                                                 rel=0.02)
        assert feats["seis_crest_factor"] == pytest.approx(
            math.sqrt(2), rel=0.05)

    def test_sta_lta_burst_exceeds_stationary(self):
        n = int(60 * FS)
        rng = np.random.default_rng(3)
        quiet = 0.01 * rng.standard_normal(n)
        burst = quiet.copy()
        burst[n // 2:n // 2 + int(2 * FS)] += 1.0
        f_quiet = ss.window_features(
            _three(quiet), FS, expected_samples=n, **_KW)
        f_burst = ss.window_features(
            _three(burst), FS, expected_samples=n, **_KW)
        assert f_burst["seis_sta_lta"] > f_quiet["seis_sta_lta"]
        assert f_burst["seis_sta_lta"] > 2.0

    def test_kurtosis_impulsive_exceeds_gaussian(self):
        n = int(60 * FS)
        rng = np.random.default_rng(4)
        gauss = rng.standard_normal(n)
        impulsive = 0.01 * rng.standard_normal(n)
        impulsive[::1000] += 5.0
        f_g = ss.window_features(
            _three(gauss), FS, expected_samples=n, **_KW)
        f_i = ss.window_features(
            _three(impulsive), FS, expected_samples=n, **_KW)
        assert f_i["seis_kurtosis"] > f_g["seis_kurtosis"]

    def test_entropy_white_noise_exceeds_pure_sine(self):
        n = int(60 * FS)
        rng = np.random.default_rng(5)
        f_w = ss.window_features(
            _three(rng.standard_normal(n)), FS,
            expected_samples=n, **_KW)
        f_s = ss.window_features(
            _three(_sine(4.0, n)), FS, expected_samples=n, **_KW)
        assert f_w["seis_entropy"] > f_s["seis_entropy"]

    def test_snr_against_declared_noise_floor(self):
        sig = _sine(5.0, int(60 * FS))
        f0 = ss.window_features(
            _three(sig), FS, expected_samples=int(60 * FS),
            **{**_KW, "noise_floor_rms": 1.0 / math.sqrt(2)})
        assert f0["seis_snr_db"] == pytest.approx(0.0, abs=0.5)
        f20 = ss.window_features(
            _three(sig), FS, expected_samples=int(60 * FS),
            **{**_KW, "noise_floor_rms": 0.1 / math.sqrt(2)})
        assert f20["seis_snr_db"] == pytest.approx(20.0, abs=1.0)

    def test_coverage_and_gap_fractions(self):
        n = int(60 * FS)
        sig = _sine(5.0, n)
        feats = ss.window_features(
            _three(sig), FS, covered_samples=int(0.9 * n),
            expected_samples=n, **_KW)
        assert feats["seis_coverage_fraction"] == pytest.approx(0.9)
        assert feats["seis_gap_fraction"] == pytest.approx(0.1)
        assert feats["seis_duration_s"] == pytest.approx(0.9 * 60.0)


class TestInputRejection:
    def _good(self):
        return _three(_sine(5.0, int(60 * FS)))

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            ss.window_features(
                np.array([]), FS, expected_samples=100, **_KW)

    def test_nan_rejected(self):
        arr = self._good()
        arr[100, 0] = np.nan
        with pytest.raises(ValueError):
            ss.window_features(arr, FS,
                               expected_samples=len(arr), **_KW)

    def test_inf_rejected(self):
        arr = self._good()
        arr[0, 1] = np.inf
        with pytest.raises(ValueError):
            ss.window_features(arr, FS,
                               expected_samples=len(arr), **_KW)

    def test_three_dimensional_rejected(self):
        with pytest.raises(ValueError):
            ss.window_features(
                np.zeros((10, 3, 2)), FS,
                expected_samples=10, **_KW)

    def test_too_short_rejected(self):
        with pytest.raises(ValueError):
            ss.window_features(
                _three(_sine(5.0, 100)), FS,
                expected_samples=100, **_KW)

    def test_nyquist_violation_rejected(self):
        with pytest.raises(ValueError):
            ss.window_features(
                self._good(), 30.0, expected_samples=1800, **_KW)

    def test_nonpositive_sample_rate_rejected(self):
        with pytest.raises(ValueError):
            ss.window_features(
                self._good(), 0.0, expected_samples=3000, **_KW)

    def test_missing_expected_samples_rejected(self):
        with pytest.raises(ValueError):
            ss.window_features(
                self._good(), FS,
                noise_floor_rms=0.01, sta_seconds=5.0,
                lta_seconds=30.0, bands=BANDS)

    def test_zero_energy_window_rejected(self):
        with pytest.raises(ValueError):
            ss.window_features(
                np.zeros((3000, 3)), FS, expected_samples=3000,
                **_KW)

    def test_noise_floor_required_positive(self):
        with pytest.raises(ValueError):
            ss.window_features(
                self._good(), FS, expected_samples=3000,
                **{**_KW, "noise_floor_rms": 0.0})

    def test_covered_exceeding_supplied_rejected(self):
        with pytest.raises(ValueError):
            ss.window_features(
                self._good(), FS, covered_samples=4000,
                expected_samples=3000, **_KW)


class TestWindowRows:
    def test_window_identities_and_partition(self):
        n = int(3 * 60 * FS)  # 3 one-minute windows
        sig = _sine(5.0, n)
        rows, dropped = ss.build_window_rows(
            station_id="S1", unit_id="u1", basin_group="g",
            samples=_three(sig), sample_rate_hz=FS,
            epoch_start_iso="2020-06-01T00:00:00Z",
            window_seconds=60, noise_floor_rms=0.01,
            sta_seconds=5.0, lta_seconds=30.0, bands=BANDS,
            min_coverage_fraction=0.9)
        assert len(rows) == 3
        assert dropped == []
        assert rows[0]["window_start"] == "2020-06-01T00:00:00Z"
        assert rows[0]["window_end"] == "2020-06-01T00:01:00Z"
        assert rows[1]["window_start"] == "2020-06-01T00:01:00Z"
        assert rows[2]["window_end"] == "2020-06-01T00:03:00Z"
        assert rows[0]["date"] == "2020-06-01"
        assert rows[0]["station_id"] == "S1"
        assert "seis_coherence" not in rows[0]

    def test_gap_window_dropped_never_zero_filled(self):
        n = int(3 * 60 * FS)
        sig = _sine(5.0, n)
        mask = np.ones(n, dtype=bool)
        mask[int(60 * FS):int(60 * FS) + int(0.5 * 60 * FS)] = False
        rows, dropped = ss.build_window_rows(
            station_id="S1", unit_id="u1", basin_group="g",
            samples=_three(sig), sample_rate_hz=FS,
            epoch_start_iso="2020-06-01T00:00:00Z",
            window_seconds=60, noise_floor_rms=0.01,
            sta_seconds=5.0, lta_seconds=30.0, bands=BANDS,
            min_coverage_fraction=0.9,
            present_mask=mask)
        # Window 1 has 50% coverage — dropped, not zero-filled.
        assert len(dropped) == 1
        assert dropped[0]["window_start"] == "2020-06-01T00:01:00Z"
        assert "coverage" in dropped[0]["reason"]
        assert all(r["seis_coverage_fraction"] >= 0.9 for r in rows)

    def test_present_mask_shape_mismatch_rejected(self):
        with pytest.raises(ValueError):
            ss.build_window_rows(
                station_id="S", unit_id="u", basin_group="g",
                samples=_three(_sine(5.0, 100)),
                sample_rate_hz=FS,
                epoch_start_iso="2020-06-01T00:00:00Z",
                window_seconds=60, noise_floor_rms=0.01,
                sta_seconds=5.0, lta_seconds=30.0, bands=BANDS,
                min_coverage_fraction=0.9,
                present_mask=np.ones(50, dtype=bool))

    def test_naive_epoch_rejected(self):
        with pytest.raises(ValueError):
            ss.build_window_rows(
                station_id="S", unit_id="u", basin_group="g",
                samples=_three(_sine(5.0, 100)),
                sample_rate_hz=FS,
                epoch_start_iso="2020-06-01 00:00:00",
                window_seconds=60, noise_floor_rms=0.01,
                sta_seconds=5.0, lta_seconds=30.0, bands=BANDS,
                min_coverage_fraction=0.9)


class TestCrossStationCoherence:
    def test_identical_signals_high_coherence(self):
        sig = _sine(6.0, int(30 * FS))
        c = ss.cross_station_coherence(
            _three(sig), _three(sig), FS, BANDS)
        assert c > 0.9

    def test_independent_signals_lower_coherence(self):
        rng = np.random.default_rng(8)
        a = rng.standard_normal(int(30 * FS))
        b = rng.standard_normal(int(30 * FS))
        c = ss.cross_station_coherence(
            _three(a), _three(b), FS, BANDS)
        assert c < 0.9

    def test_unequal_lengths_rejected(self):
        with pytest.raises(ValueError):
            ss.cross_station_coherence(
                _three(_sine(5.0, 1000)),
                _three(_sine(5.0, 500)), FS, BANDS)


class TestDigestBoundary:
    def _rows(self):
        return [
            {"window_start": "2020-06-01T00:00:00Z",
             "window_end": "2020-06-01T00:01:00Z",
             "station_id": "S1", "unit_id": "u1",
             "basin_group": "g", "date": "2020-06-01",
             "seis_rsam": 0.5, "seis_snr_db": 12.0,
             "catalog_event_count": 3, "label_anything": 9},
            {"window_start": "2020-06-01T00:00:00Z",
             "window_end": "2020-06-01T00:01:00Z",
             "station_id": "S2", "unit_id": "u2",
             "basin_group": "g", "date": "2020-06-01",
             "seis_rsam": 0.6, "seis_snr_db": 11.0,
             "catalog_event_count": 1, "label_anything": 2},
        ]

    def test_row_order_permutation_preserves_digest(self):
        rows = self._rows()
        d1 = ss.semantic_feature_digest(
            rows, ["seis_rsam", "seis_snr_db"])
        d2 = ss.semantic_feature_digest(
            list(reversed(rows)), ["seis_rsam", "seis_snr_db"])
        assert d1 == d2

    def test_labels_and_catalog_never_enter_digest(self):
        rows = self._rows()
        d1 = ss.semantic_feature_digest(
            rows, ["seis_rsam", "seis_snr_db"])
        mutated = [dict(r, catalog_event_count=99,
                        label_anything=0) for r in rows]
        d2 = ss.semantic_feature_digest(
            mutated, ["seis_rsam", "seis_snr_db"])
        assert d1 == d2

    def test_feature_value_change_changes_digest(self):
        rows = self._rows()
        d1 = ss.semantic_feature_digest(
            rows, ["seis_rsam", "seis_snr_db"])
        mutated = [dict(r) for r in rows]
        mutated[0]["seis_rsam"] = 0.9
        d2 = ss.semantic_feature_digest(
            mutated, ["seis_rsam", "seis_snr_db"])
        assert d1 != d2


class TestAggregation:
    def test_daily_grain_and_effort_column(self):
        rows = []
        for station in ("S1", "S2"):
            for day in ("2020-06-01", "2020-06-02"):
                for w in range(3):
                    rows.append({
                        "window_start":
                            f"2020-06-0{'1' if day.endswith('1') else '2'}"
                            f"T00:0{w}:00Z",
                        "window_end": "x",
                        "station_id": station,
                        "unit_id": f"u{station}",
                        "basin_group": "g", "date": day,
                        "seis_rsam": 1.0 + w * 0.1})
        daily = ss.aggregate_daily(rows, ["seis_rsam"])
        assert len(daily) == 4
        for r in daily:
            assert r["seis_window_count"] == 3
            assert r["seis_rsam"] == pytest.approx(1.1)
            assert r["season"] in ("DJF", "MAM", "JJA", "SON")
        assert all(r["season"] == "JJA" for r in daily)

    def test_season_of_date(self):
        assert ss.season_of_date("2020-01-15") == "DJF"
        assert ss.season_of_date("2020-04-15") == "MAM"
        assert ss.season_of_date("2020-07-15") == "JJA"
        assert ss.season_of_date("2020-10-15") == "SON"
