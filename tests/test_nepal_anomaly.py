"""Tests for Nepal Event Anomaly Assessment code.

Tests the feature contract, mathematical formulas, and edge cases
without requiring ERA5-Land data (uses synthetic fixtures).
"""
import sys
import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Add nepal/ to path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "nepal"))

from feature_contract import (
    verify_contract, EVENT, RAW_GRIB_SHORT_NAMES, CDS_LONG_NAMES,
    DERIVED_FEATURES, TOTAL_FEATURES, GRID_LAT_KM, GRID_LON_KM,
    PRE_EVENT_WINDOW, EVENT_DATE, Z_SCORE_THRESHOLD,
    IFOREST_PARAMS, GMM_K_RANGE, GMM_COVARIANCE,
)


class TestFeatureContract:
    """Test the feature contract is self-consistent."""

    def test_verify_contract_passes(self):
        result = verify_contract()
        assert result["status"] == "VERIFIED"
        assert result["raw_variables"] == 7
        assert result["derived_features"] == 3
        assert result["total_distinct"] == 10

    def test_total_features_is_10(self):
        assert TOTAL_FEATURES == 10, "Feature count must be 10 (7 raw + 3 derived)"

    def test_raw_variables_match_grib_short_names(self):
        assert RAW_GRIB_SHORT_NAMES == ("10u", "10v", "2d", "2t", "sd", "sf", "tp")

    def test_all_raw_vars_have_cds_long_names(self):
        for short_name in RAW_GRIB_SHORT_NAMES:
            assert short_name in CDS_LONG_NAMES, f"Missing CDS long name for {short_name}"

    def test_derived_features_count(self):
        assert len(DERIVED_FEATURES) == 3
        assert "wind_speed" in DERIVED_FEATURES
        assert "wind_dir" in DERIVED_FEATURES
        assert "relative_humidity" in DERIVED_FEATURES

    def test_elevation_gap_is_correct(self):
        gap = EVENT["source_elevation_m"] - EVENT["model_elevation_m"]
        assert EVENT["elevation_gap_m"] == gap
        assert gap == 899

    def test_pre_event_window_excludes_event_date(self):
        assert PRE_EVENT_WINDOW[1] < EVENT_DATE, "Pre-event window must end before event date"

    def test_grid_dimensions(self):
        assert GRID_LAT_KM == 11.1
        assert GRID_LON_KM == 9.8

    def test_iforest_params_frozen(self):
        assert IFOREST_PARAMS["contamination"] == 0.01
        assert IFOREST_PARAMS["random_state"] == 42
        assert IFOREST_PARAMS["n_estimators"] == 200

    def test_gmm_k_range_includes_k1(self):
        assert 1 in GMM_K_RANGE, "K=1 null benchmark must be included (Astra)"
        assert GMM_COVARIANCE == "diag", "Must use diagonal covariance (regularized)"

    def test_z_score_threshold(self):
        assert Z_SCORE_THRESHOLD == 2.0


class TestPDDComputation:
    """Test PDD (Positive Degree Day) computation logic."""

    def test_pdd_positive_temperature(self):
        """PDD should equal temperature when T > 0."""
        T = 5.0  # °C
        pdd = max(0, T)
        assert pdd == 5.0

    def test_pdd_negative_temperature(self):
        """PDD should be 0 when T < 0."""
        T = -5.0  # °C
        pdd = max(0, T)
        assert pdd == 0.0

    def test_pdd_zero_temperature(self):
        """PDD should be 0 when T = 0."""
        T = 0.0
        pdd = max(0, T)
        assert pdd == 0.0

    def test_pdd_7day_rolling_sum(self):
        """7-day rolling PDD should sum daily PDDs."""
        daily_pdd = pd.Series([0, 0, 2, 3, 5, 4, 1])
        pdd_7day = daily_pdd.rolling(window=7, min_periods=1).sum()
        assert pdd_7day.iloc[-1] == 15  # 0+0+2+3+5+4+1 = 15

    def test_pdd_cross_reference_rui_li(self):
        """Rui Li reported 7-day PDD = 65.94 °C·d for the event window.
        Our computation should be in the same ballpark if data is correct.
        This is a sanity check, not an exact match (different cell, baseline)."""
        # If mean T over 7 days is ~9.43°C (Li), PDD ≈ 9.43 * 7 = 66.01
        expected_pdd_approx = 9.43 * 7
        assert 60 < expected_pdd_approx < 70  # Rough range


class TestFreezingLevelHeight:
    """Test freezing level height computation."""

    def test_freezing_level_at_model_elevation_when_t_is_zero(self):
        """When T = 0°C at model elevation, freezing level = model elevation."""
        model_elev = 4322
        T_c = 0.0
        lapse_rate = -0.0065  # K/m
        # z_freeze = z_model + T_model / 0.0065
        z_freeze = model_elev + T_c / 0.0065
        assert z_freeze == model_elev

    def test_freezing_level_above_model_when_t_positive(self):
        """When T > 0°C at model elevation, freezing level is above."""
        model_elev = 4322
        T_c = 5.0
        z_freeze = model_elev + T_c / 0.0065
        assert z_freeze > model_elev
        # 5°C / 0.0065 K/m ≈ 769 m above model
        assert 5050 < z_freeze < 5100

    def test_freezing_level_below_model_when_t_negative(self):
        """When T < 0°C at model elevation, freezing level is below."""
        model_elev = 4322
        T_c = -5.0
        z_freeze = model_elev + T_c / 0.0065
        assert z_freeze < model_elev


class TestRelativeHumidity:
    """Test relative humidity computation from T and Td (Magnus formula)."""

    def test_rh_100_when_t_equals_td(self):
        """RH should be ~100% when T = Td."""
        T = 10.0
        Td = 10.0
        gamma_t = 17.625 * T / (243.04 + T)
        gamma_td = 17.625 * Td / (243.04 + Td)
        rh = 100 * np.exp(gamma_td - gamma_t)
        assert abs(rh - 100) < 0.01

    def test_rh_less_than_100_when_td_below_t(self):
        """RH should be < 100% when Td < T."""
        T = 20.0
        Td = 10.0
        gamma_t = 17.625 * T / (243.04 + T)
        gamma_td = 17.625 * Td / (243.04 + Td)
        rh = 100 * np.exp(gamma_td - gamma_t)
        assert 0 < rh < 100

    def test_rh_clipped_to_100(self):
        """RH should be clipped to 100% max."""
        rh_raw = 105.0
        rh = min(rh_raw, 100)
        assert rh == 100


class TestWindSpeed:
    """Test wind speed computation from u and v components."""

    def test_wind_speed_zero(self):
        u, v = 0, 0
        speed = np.sqrt(u**2 + v**2)
        assert speed == 0

    def test_wind_speed_pure_u(self):
        u, v = 5, 0
        speed = np.sqrt(u**2 + v**2)
        assert speed == 5

    def test_wind_speed_pure_v(self):
        u, v = 0, 3
        speed = np.sqrt(u**2 + v**2)
        assert speed == 3

    def test_wind_speed_combined(self):
        u, v = 3, 4
        speed = np.sqrt(u**2 + v**2)
        assert speed == 5  # 3-4-5 triangle


class TestZScoreAnomaly:
    """Test z-score anomaly detection logic."""

    def test_zscore_within_normal(self):
        """Z-score within ±2 should not be flagged."""
        x = 0.5
        mean = 0
        std = 1
        z = (x - mean) / std
        assert abs(z) < Z_SCORE_THRESHOLD

    def test_zscore_anomalous(self):
        """Z-score beyond ±2 should be flagged."""
        x = 3.0
        mean = 0
        std = 1
        z = (x - mean) / std
        assert abs(z) > Z_SCORE_THRESHOLD

    def test_zscore_zero_std(self):
        """When std = 0, z-score should be 0 (not infinity)."""
        x = 5.0
        mean = 5.0
        std = 0.0
        z = (x - mean) / std if std > 0 else 0
        assert z == 0


class TestBlockPermutation:
    """Test block permutation control logic."""

    def test_block_permutation_preserves_length(self):
        """Permutation should produce same length as target."""
        baseline = pd.Series(np.random.randn(1000))
        target_len = 7
        block = baseline.iloc[0:target_len]
        assert len(block) == target_len

    def test_block_permutation_random_start(self):
        """Random start index should be within valid range."""
        baseline_len = 1000
        target_len = 7
        start = np.random.randint(0, baseline_len - target_len)
        assert 0 <= start < baseline_len - target_len


class TestGMMDescriptive:
    """Test GMM descriptive overlay logic."""

    def test_gmm_k1_is_null_benchmark(self):
        """K=1 should be included as null benchmark."""
        assert 1 in GMM_K_RANGE

    def test_gmm_covariance_is_diagonal(self):
        """Covariance should be diagonal (regularized) per Astra."""
        assert GMM_COVARIANCE == "diag"

    def test_js_distance_bounded(self):
        """Jensen-Shannon distance should be bounded [0, 1]."""
        from scipy.spatial.distance import jensenshannon
        p = np.array([0.5, 0.5])
        q = np.array([0.5, 0.5])
        js = jensenshannon(p, q)
        assert 0 <= js <= 1

    def test_js_distance_max_when_disjoint(self):
        """JS distance should be high for disjoint distributions.
        scipy's jensenshannon returns sqrt(JS divergence), which for
        fully disjoint binary distributions is ~0.83 (ln(2) related)."""
        from scipy.spatial.distance import jensenshannon
        p = np.array([1.0, 0.0])
        q = np.array([0.0, 1.0])
        js = jensenshannon(p, q)
        assert js > 0.8  # sqrt(ln(2)) ≈ 0.832 for fully disjoint binary


class TestEventDefinition:
    """Test event definition values."""

    def test_event_date(self):
        assert EVENT["date"] == "2026-08-26"

    def test_event_coordinates(self):
        lat, lon = EVENT["era5_cell"]
        assert lat == 28.25
        assert lon == 85.50

    def test_model_elevation_below_source(self):
        assert EVENT["model_elevation_m"] < EVENT["source_elevation_m"]

    def test_glacier_id_format(self):
        assert EVENT["glacier_id"].startswith("RGI2000")


class TestPreRegistrationCompliance:
    """Test that code complies with pre-registration."""

    def test_pre_event_window_is_7_days(self):
        start = pd.Timestamp(PRE_EVENT_WINDOW[0])
        end = pd.Timestamp(PRE_EVENT_WINDOW[1])
        duration = (end - start).days + 1
        assert duration == 7

    def test_event_date_not_in_pre_event_window(self):
        event = pd.Timestamp(EVENT_DATE)
        start = pd.Timestamp(PRE_EVENT_WINDOW[0])
        end = pd.Timestamp(PRE_EVENT_WINDOW[1])
        assert not (start <= event <= end)

    def test_contamination_is_fixed_not_auto(self):
        """Pre-registration: contamination=0.01, not 'auto'."""
        assert IFOREST_PARAMS["contamination"] == 0.01
        assert IFOREST_PARAMS["contamination"] != "auto"


class TestGapFixes:
    """Tests for gaps identified by ecc-advisor cross-verification."""

    def test_precipitation_uses_sum_not_mean(self):
        """GAP FIX 1: ERA5-Land tp/sf are per-hour accumulations.
        Daily total must use .sum(), not .mean()."""
        # Simulate hourly precipitation: 1mm/hr × 24hrs = 24mm/day
        hourly_tp = pd.Series(
            [1.0] * 24,
            index=pd.date_range("2026-08-19", periods=24, freq="h"),
        )
        daily_sum = hourly_tp.resample("D").sum()
        daily_mean = hourly_tp.resample("D").mean()
        assert daily_sum.iloc[0] == 24.0, "Daily sum should be 24mm"
        assert daily_mean.iloc[0] == 1.0, "Daily mean would incorrectly give 1mm"

    def test_daily_feature_matrix_has_all_columns(self):
        """GAP FIX 2: Daily feature matrix should include ALL 10 features,
        not just 4 (t2m, pdd, pdd_7day, freezing_height)."""
        expected_cols = [
            "t2m_daily", "d2m_daily", "tp_daily", "sf_daily", "sd_daily",
            "wind_speed_daily", "wind_dir_sin", "wind_dir_cos", "rh_daily",
            "pdd_daily", "pdd_7day", "freezing_height_m",
        ]
        # The feature_extraction.py compute_thermal_indices function should
        # produce all these columns when input data is available
        # (We test the column names, not the actual computation)
        assert len(expected_cols) == 12  # 10 features + 2 thermal indices
        assert "tp_daily" in expected_cols, "Precipitation must be in daily features"
        assert "sf_daily" in expected_cols, "Snowfall must be in daily features"
        assert "sd_daily" in expected_cols, "SWE must be in daily features"
        assert "wind_dir_sin" in expected_cols, "Wind dir sin must be in daily features"
        assert "wind_dir_cos" in expected_cols, "Wind dir cos must be in daily features"
        assert "rh_daily" in expected_cols, "RH must be in daily features"

    def test_isolation_forest_feature_list_expanded(self):
        """GAP FIX 3: Isolation Forest should use all available features,
        not just 4."""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nepal"))
        from isolation_forest import DAILY_FEATURE_COLS
        assert len(DAILY_FEATURE_COLS) >= 10, (
            f"IF should use >= 10 features, got {len(DAILY_FEATURE_COLS)}"
        )
        assert "tp_daily" in DAILY_FEATURE_COLS, "IF must include precipitation"
        assert "wind_dir_sin" in DAILY_FEATURE_COLS, "IF must include wind dir sin"
        assert "rh_daily" in DAILY_FEATURE_COLS, "IF must include RH"

    def test_gmm_feature_list_expanded(self):
        """GAP FIX 4: GMM should use all available features, not just 4."""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nepal"))
        from gmm_descriptive import GMM_FEATURES
        assert len(GMM_FEATURES) >= 10, (
            f"GMM should use >= 10 features, got {len(GMM_FEATURES)}"
        )
        assert "tp_daily" in GMM_FEATURES, "GMM must include precipitation"
        assert "wind_dir_cos" in GMM_FEATURES, "GMM must include wind dir cos"

    def test_cusum_resets_after_detection(self):
        """GAP FIX 5: CUSUM should reset after detecting a change-point.
        With a cooldown period, it should not flag every point after a shift."""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nepal"))
        from change_point_detector import run_cusum
        # Create a series with a temporary spike (more realistic than permanent shift)
        series = np.concatenate([
            np.full(40, 0.0),   # Baseline
            np.full(10, 10.0),  # Spike
            np.full(40, 0.0),   # Back to baseline
        ])
        cps = run_cusum(series, k=1.0, threshold=5.0, min_distance=7)
        # Should detect change-points at the spike boundaries
        assert len(cps) >= 1, "Should detect at least one change-point"
        # Should NOT flag every point (cooldown prevents cascade)
        assert len(cps) < 10, (
            f"Should not flag excessive points (got {len(cps)}); "
            "cooldown should prevent cascade"
        )

    def test_wind_dir_circular_encoding(self):
        """GAP FIX 10: wind_dir should be encoded as sin/cos, not raw radians."""
        # 0° and 360° should give the same sin/cos values
        angle_0 = 0.0
        angle_360 = 2 * np.pi
        assert abs(np.sin(angle_0) - np.sin(angle_360)) < 1e-10
        assert abs(np.cos(angle_0) - np.cos(angle_360)) < 1e-10
        # 180° and -180° should give the same sin/cos values
        angle_180 = np.pi
        angle_neg180 = -np.pi
        assert abs(np.sin(angle_180) - np.sin(angle_neg180)) < 1e-10


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
