"""Nepal Event Anomaly — Feature Contract (Reconciled)

Reconciles the feature count from the original "11 LAND features" claim
to the verified 10 distinct quantities (7 raw + 3 derived).

Based on:
- LAND_VARS from real_source_adapter.py (verified: 7 raw variables)
- Astra's feature count correction (10 distinct, not 11)
- Codebase audit confirming sd = snow water equivalent in ERA5-Land

This file is FROZEN after pre-registration. Do not modify without logging
a post-hoc change in preregistration.md.
"""
from __future__ import annotations

# === RAW ERA5-LAND VARIABLES (7) ===
# Source: backend/research/t2a_regime_discovery/real_source_adapter.py line 34
# These are GRIB short names used in the existing adapter
RAW_GRIB_SHORT_NAMES = ("10u", "10v", "2d", "2t", "sd", "sf", "tp")

# CDS API long names (required for CDS requests — Astra correction:
# GRIB short names are NOT the CDS request variable enumeration)
CDS_LONG_NAMES = {
    "2t":  "2m_temperature",
    "2d":  "2m_dewpoint_temperature",
    "10u": "10m_u_component_of_wind",
    "10v": "10m_v_component_of_wind",
    "sd":  "snow_depth",           # ERA5-Land: this is SWE, not geometric depth
    "sf":  "snowfall",
    "tp":  "total_precipitation",
}

# === DERIVED FEATURES (3) ===
DERIVED_FEATURES = ("wind_speed", "wind_dir", "relative_humidity")

# === TOTAL DISTINCT QUANTITIES (10) ===
# Previous claim of "11 LAND features" was INCORRECT.
# 7 raw + 3 derived = 10 distinct quantities.
# Counting sd and SWE as independent features duplicates information
# because ERA5-Land sd IS snow water equivalent.
TOTAL_FEATURES = len(RAW_GRIB_SHORT_NAMES) + len(DERIVED_FEATURES)  # = 10

# === COMPUTED THERMAL INDICES (not counted as features) ===
THERMAL_INDICES = ("PDD_daily", "PDD_7day", "T_freezing_height")

# === ERA5-LAND GRID DIMENSIONS (Astra correction) ===
# At 28.25°N, 85.50°E, the 0.1° grid is approximately:
#   Latitude spacing: 0.1° × 111.1 km/° ≈ 11.1 km
#   Longitude spacing: 0.1° × 111.1 km/° × cos(28.25°) ≈ 9.8 km
# NOT 9 km square or 8.5 km square as previously stated.
GRID_LAT_KM = 11.1
GRID_LON_KM = 9.8

# === EVENT DEFINITION ===
EVENT = {
    "date": "2026-08-26",
    "time_utc": "02:52",
    "time_npt": "08:37",
    "reference_point": (28.288708, 85.528159),   # HiRisk
    "alt_centroid": (28.28858, 85.52701),         # Rui Li
    "era5_cell": (28.25, 85.50),                  # Hausfather nearest cell
    "model_elevation_m": 4322,                     # ERA5 orography
    "source_elevation_m": 5221,                    # HiRisk/Rui Li
    "elevation_gap_m": 899,                        # 5221 - 4322
    "glacier_id": "RGI2000-v7.0-G-15-05732",
}

# === FROZEN WINDOWS ===
PRE_EVENT_WINDOW = ("2026-08-19", "2026-08-25")   # 7 days before event
EVENT_DATE = "2026-08-26"                          # Held out
POST_EVENT_CUTOFF = "2026-08-26"                   # Nothing on/after this date
JJA_2026 = ("2026-06-01", "2026-08-25")            # Aug 26+ excluded
HISTORICAL_BASELINE = ("2001-01-01", "2025-12-31") # 25 years
JJA_MONTHS = (6, 7, 8)                             # Jun, Jul, Aug
NEGATIVE_CONTROL_YEARS = (2021, 2022, 2023, 2024, 2025)
ROLLING_WINDOWS_DAYS = (3, 7, 14, 30)

# === METHOD PARAMETERS (frozen) ===
Z_SCORE_THRESHOLD = 2.0
PERCENTILE_THRESHOLDS = (95, 99)
IFOREST_PARAMS = {
    "n_estimators": 200,
    "contamination": 0.01,
    "random_state": 42,
}
GMM_K_RANGE = (1, 2, 3, 4, 5)  # k=1 included as null benchmark (Astra)
GMM_COVARIANCE = "diag"         # Regularized, not full (Astra: 233 params for k=3 full)
PELT_MODEL = "rbf"
PELT_PENALTY = 10
CUSUM_K = 1.0
CUSUM_THRESHOLD = 5.0
BLOCK_PERMUTATION_LENGTHS = (7, 14, 30)  # days


def verify_contract() -> dict:
    """Verify the feature contract is self-consistent."""
    assert len(RAW_GRIB_SHORT_NAMES) == 7, "Must have 7 raw variables"
    assert len(DERIVED_FEATURES) == 3, "Must have 3 derived features"
    assert TOTAL_FEATURES == 10, f"Total must be 10, got {TOTAL_FEATURES}"
    assert all(v in CDS_LONG_NAMES for v in RAW_GRIB_SHORT_NAMES), "Missing CDS long name"
    assert EVENT["elevation_gap_m"] == EVENT["source_elevation_m"] - EVENT["model_elevation_m"]
    return {
        "raw_variables": len(RAW_GRIB_SHORT_NAMES),
        "derived_features": len(DERIVED_FEATURES),
        "total_distinct": TOTAL_FEATURES,
        "grid_km": f"{GRID_LAT_KM} × {GRID_LON_KM}",
        "model_elevation_m": EVENT["model_elevation_m"],
        "source_elevation_m": EVENT["source_elevation_m"],
        "elevation_gap_m": EVENT["elevation_gap_m"],
        "pre_event_window": PRE_EVENT_WINDOW,
        "baseline": HISTORICAL_BASELINE,
        "status": "VERIFIED",
    }


if __name__ == "__main__":
    import json
    print(json.dumps(verify_contract(), indent=2))
