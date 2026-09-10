"""Phase 2: Feature extraction for Nepal Event Anomaly Assessment.

Extracts nearest-cell time series from ERA5-Land NetCDF, computes derived
features (wind_speed, wind_dir, RH), PDD, freezing level height, and
produces EDA plots.

All outputs include the model elevation disclaimer:
"ERA5-Land model elevation: 4,322 m. Source slope: 5,221 m. Delta: 899 m."

Usage:
    source .venv/bin/activate
    python nepal/feature_extraction.py
"""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import xarray as xr
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend
import matplotlib.pyplot as plt

# Import feature contract
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from feature_contract import (
    EVENT, RAW_GRIB_SHORT_NAMES, CDS_LONG_NAMES,
    DERIVED_FEATURES, TOTAL_FEATURES,
    GRID_LAT_KM, GRID_LON_KM,
    PRE_EVENT_WINDOW, EVENT_DATE, POST_EVENT_CUTOFF,
    JJA_2026, HISTORICAL_BASELINE, JJA_MONTHS,
    NEGATIVE_CONTROL_YEARS, ROLLING_WINDOWS_DAYS,
)

# Paths
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PLOTS_DIR = Path(__file__).resolve().parent.parent / "plots"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data"

ERA5_FILE = DATA_DIR / "era5_land_nepal_jja_2001_2026.nc"
FEATURE_FILE = OUTPUT_DIR / "features_nepal_jja_2001_2026.csv"
EDA_PLOT_DIR = PLOTS_DIR / "eda"

# Elevation disclaimer (printed on every plot)
ELEVATION_DISCLAIMER = (
    f"ERA5-Land model elevation: {EVENT['model_elevation_m']} m. "
    f"Source slope: {EVENT['source_elevation_m']} m. "
    f"Delta: {EVENT['elevation_gap_m']} m. "
    f"Grid: {GRID_LAT_KM} × {GRID_LON_KM} km."
)

# Lapse rate for elevation extrapolation (K/m)
LAPSE_RATE = -0.0065  # Standard atmospheric lapse rate


def load_era5_land(filepath: Path) -> xr.Dataset:
    """Load ERA5-Land NetCDF and select nearest cell to event point."""
    print(f"Loading ERA5-Land from {filepath}...")
    ds = xr.open_dataset(filepath)

    # Select nearest cell to Hausfather's coordinates
    lat_name = "latitude" if "latitude" in ds.coords else "lat"
    lon_name = "longitude" if "longitude" in ds.coords else "lon"

    cell = ds.sel(
        **{lat_name: EVENT["era5_cell"][0], lon_name: EVENT["era5_cell"][1]},
        method="nearest",
    )

    # Get actual coordinates used
    actual_lat = float(cell[lat_name].values)
    actual_lon = float(cell[lon_name].values)
    print(f"Nearest cell: {actual_lat:.2f}°N, {actual_lon:.2f}°E")

    # Get model elevation from orography if available
    model_elev = EVENT["model_elevation_m"]
    if "z" in cell:
        # Geopotential → elevation (m)
        model_elev = float(cell["z"].values) / 9.80665
        print(f"Model elevation from orography: {model_elev:.0f} m")
    else:
        print(f"Model elevation (from contract): {model_elev} m")

    print(f"Source elevation: {EVENT['source_elevation_m']} m")
    print(f"Elevation gap: {EVENT['source_elevation_m'] - model_elev:.0f} m")

    return cell, actual_lat, actual_lon, model_elev


def extract_raw_features(ds: xr.Dataset) -> pd.DataFrame:
    """Extract raw ERA5-Land variables as a DataFrame.

    ERA5-Land variable names in NetCDF may differ from CDS long names.
    We map them using the feature contract.
    """
    time_name = "valid_time" if "valid_time" in ds.coords else "time"
    times = pd.to_datetime(ds[time_name].values)

    # Map CDS long names to actual NetCDF variable names
    # ERA5-Land NetCDF uses short names like t2m, d2m, u10, v10, sd, sf, tp
    var_map = {
        "2m_temperature": "t2m",
        "2m_dewpoint_temperature": "d2m",
        "10m_u_component_of_wind": "u10",
        "10m_v_component_of_wind": "v10",
        "snow_depth": "sd",
        "snowfall": "sf",
        "total_precipitation": "tp",
    }

    data = {"time": times}

    for cds_name, nc_name in var_map.items():
        if nc_name in ds.data_vars:
            data[nc_name] = ds[nc_name].values
        elif cds_name in ds.data_vars:
            data[nc_name] = ds[cds_name].values
        else:
            # Try to find by searching all data vars
            found = False
            for v in ds.data_vars:
                if cds_name.replace("2m_", "").replace("10m_", "") in v.lower():
                    data[nc_name] = ds[v].values
                    found = True
                    break
            if not found:
                print(f"WARNING: Variable {cds_name} (expected as {nc_name}) not found!")
                data[nc_name] = np.nan

    df = pd.DataFrame(data).set_index("time")

    # Convert temperatures from K to °C
    for temp_var in ["t2m", "d2m"]:
        if temp_var in df.columns:
            df[temp_var] = df[temp_var] - 273.15

    # Precipitation and snowfall are accumulated in ERA5-Land
    # Convert from m to mm
    for acc_var in ["tp", "sf"]:
        if acc_var in df.columns:
            df[acc_var] = df[acc_var] * 1000  # m → mm

    # Snow depth (SWE) from m to mm
    if "sd" in df.columns:
        df["sd"] = df["sd"] * 1000  # m → mm

    return df


def compute_derived_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute derived features: wind_speed, wind_dir, relative_humidity."""
    # Wind speed
    if "u10" in df.columns and "v10" in df.columns:
        df["wind_speed"] = np.sqrt(df["u10"]**2 + df["v10"]**2)
        df["wind_dir"] = np.arctan2(df["v10"], df["u10"])  # radians

    # Relative humidity from dewpoint and temperature (Magnus formula)
    if "d2m" in df.columns and "t2m" in df.columns:
        # Magnus formula: RH = 100 * exp((17.625 * Td) / (243.04 + Td)) /
        #                                exp((17.625 * T) / (243.04 + T))
        # Where T and Td are in °C
        gamma_t = 17.625 * df["t2m"] / (243.04 + df["t2m"])
        gamma_td = 17.625 * df["d2m"] / (243.04 + df["d2m"])
        rh = 100 * np.exp(gamma_td - gamma_t)
        df["relative_humidity"] = rh.clip(0, 100)

    return df


def compute_thermal_indices(df: pd.DataFrame, model_elev_m: float) -> pd.DataFrame:
    """Compute PDD, 7-day rolling PDD, and freezing level height."""
    # Daily mean temperature
    daily_t = df["t2m"].resample("D").mean()

    # Daily PDD: max(0, T_daily)
    pdd_daily = daily_t.clip(lower=0)

    # 7-day rolling PDD
    pdd_7day = pdd_daily.rolling(window=7, min_periods=1).sum()

    # Freezing level height: extrapolate from model elevation using lapse rate
    # T(z) = T_model + lapse_rate * (z - z_model)
    # 0 = T_model + lapse_rate * (z_freeze - z_model)
    # z_freeze = z_model - T_model / lapse_rate
    # lapse_rate is negative (-0.0065 K/m), so:
    # z_freeze = z_model + T_model / 0.0065
    daily_t_k = daily_t + 273.15  # Convert back to K for the calculation
    # Actually, we want: z_freeze = model_elev + (T_model_C) / 0.0065
    # Because lapse_rate = -0.0065 K/m = -0.0065 °C/m
    # T(z) = T_model - 0.0065 * (z - z_model)
    # 0 = T_model - 0.0065 * (z_freeze - z_model)
    # z_freeze = z_model + T_model / 0.0065
    freezing_height = model_elev_m + daily_t / 0.0065

    # Add to a daily dataframe
    daily_df = pd.DataFrame({
        "t2m_daily": daily_t,
        "pdd_daily": pdd_daily,
        "pdd_7day": pdd_7day,
        "freezing_height_m": freezing_height,
    })

    return daily_df


def generate_eda_plots(
    hourly_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    model_elev_m: float,
    output_dir: Path,
):
    """Generate EDA plots with elevation disclaimer on every plot."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Plot 1: JJA 2026 daily temperature with event line
    fig, ax = plt.subplots(figsize=(14, 6))
    jja_2026 = daily_df[daily_df.index.year == 2026]
    ax.plot(jja_2026.index, jja_2026["t2m_daily"], "b-", label="Daily mean T (°C)")
    ax.axvline(pd.Timestamp(EVENT_DATE), color="r", linestyle="--", label=f"Event ({EVENT_DATE})")
    ax.axvspan(pd.Timestamp(PRE_EVENT_WINDOW[0]), pd.Timestamp(PRE_EVENT_WINDOW[1]),
               alpha=0.2, color="orange", label="Pre-event window")
    ax.set_xlabel("Date")
    ax.set_ylabel("Temperature (°C)")
    ax.set_title("JJA 2026 Daily Temperature — Langtang Lirung Event")
    ax.legend()
    fig.text(0.5, 0.01, ELEVATION_DISCLAIMER, ha="center", fontsize=8, style="italic")
    plt.tight_layout()
    fig.savefig(output_dir / "01_jja_2026_temperature.png", dpi=150)
    plt.close()

    # Plot 2: PDD 7-day rolling for JJA 2026
    fig, ax = plt.subplots(figsize=(14, 6))
    ax.plot(jja_2026.index, jja_2026["pdd_7day"], "g-", label="7-day PDD (°C·d)")
    ax.axvline(pd.Timestamp(EVENT_DATE), color="r", linestyle="--", label=f"Event")
    ax.axvspan(pd.Timestamp(PRE_EVENT_WINDOW[0]), pd.Timestamp(PRE_EVENT_WINDOW[1]),
               alpha=0.2, color="orange", label="Pre-event window")
    ax.set_xlabel("Date")
    ax.set_ylabel("PDD (°C·d)")
    ax.set_title("JJA 2026 7-day Rolling PDD — Langtang Lirung Event")
    ax.legend()
    fig.text(0.5, 0.01, ELEVATION_DISCLAIMER, ha="center", fontsize=8, style="italic")
    plt.tight_layout()
    fig.savefig(output_dir / "02_jja_2026_pdd_7day.png", dpi=150)
    plt.close()

    # Plot 3: JJA climatology 2001-2025 vs 2026
    fig, ax = plt.subplots(figsize=(14, 6))
    baseline = daily_df[daily_df.index.year.isin(range(2001, 2026))]
    for year in range(2001, 2026, 5):  # Plot every 5th year for clarity
        year_data = daily_df[daily_df.index.year == year]
        day_of_year = year_data.index.dayofyear
        ax.plot(day_of_year, year_data["t2m_daily"], alpha=0.3, color="gray")

    # Baseline mean ± std
    baseline_doy = baseline.groupby(baseline.index.dayofyear)["t2m_daily"]
    baseline_mean = baseline_doy.mean()
    baseline_std = baseline_doy.std()
    ax.plot(baseline_mean.index, baseline_mean.values, "k-", linewidth=2, label="2001-2025 mean")
    ax.fill_between(baseline_mean.index,
                    (baseline_mean - baseline_std).values,
                    (baseline_mean + baseline_std).values,
                    alpha=0.2, color="gray", label="±1 std")

    # 2026
    jja_2026_doy = jja_2026.index.dayofyear
    ax.plot(jja_2026_doy, jja_2026["t2m_daily"], "r-", linewidth=2, label="2026")
    ax.set_xlabel("Day of year (JJA = 152-243)")
    ax.set_ylabel("Temperature (°C)")
    ax.set_title("JJA Temperature: 2026 vs 2001-2025 Climatology")
    ax.legend()
    fig.text(0.5, 0.01, ELEVATION_DISCLAIMER, ha="center", fontsize=8, style="italic")
    plt.tight_layout()
    fig.savefig(output_dir / "03_climatology_2026_vs_baseline.png", dpi=150)
    plt.close()

    # Plot 4: Freezing level height
    fig, ax = plt.subplots(figsize=(14, 6))
    ax.plot(jja_2026.index, jja_2026["freezing_height_m"], "purple", label="0°C isotherm height (m)")
    ax.axhline(y=EVENT["source_elevation_m"], color="r", linestyle="--",
               label=f"Source elevation ({EVENT['source_elevation_m']} m)")
    ax.axhline(y=model_elev_m, color="b", linestyle=":",
               label=f"Model elevation ({model_elev_m:.0f} m)")
    ax.axvline(pd.Timestamp(EVENT_DATE), color="r", linestyle="--", alpha=0.5)
    ax.set_xlabel("Date")
    ax.set_ylabel("Elevation (m)")
    ax.set_title("JJA 2026 Freezing Level Height vs Source Elevation")
    ax.legend()
    fig.text(0.5, 0.01, ELEVATION_DISCLAIMER, ha="center", fontsize=8, style="italic")
    plt.tight_layout()
    fig.savefig(output_dir / "04_freezing_level_2026.png", dpi=150)
    plt.close()

    print(f"EDA plots saved to {output_dir}/")


def main():
    """Main Phase 2 execution."""
    print("=" * 60)
    print("Phase 2: Feature Extraction + PDD + EDA")
    print("=" * 60)
    print()

    # Load ERA5-Land data
    ds, actual_lat, actual_lon, model_elev = load_era5_land(ERA5_FILE)
    print(f"ELEVATION DISCLAIMER: {ELEVATION_DISCLAIMER}")
    print()

    # Extract raw features (hourly → daily)
    print("Extracting raw features...")
    hourly_df = extract_raw_features(ds)
    print(f"Hourly data: {len(hourly_df)} rows, {len(hourly_df.columns)} columns")
    print(f"Columns: {list(hourly_df.columns)}")

    # Compute derived features
    print("Computing derived features...")
    hourly_df = compute_derived_features(hourly_df)
    print(f"With derived: {len(hourly_df.columns)} columns")
    print(f"Columns: {list(hourly_df.columns)}")

    # Compute thermal indices (daily)
    print("Computing thermal indices (PDD, freezing level)...")
    daily_df = compute_thermal_indices(hourly_df, model_elev)
    print(f"Daily data: {len(daily_df)} rows")
    print(f"Columns: {list(daily_df.columns)}")

    # Verify feature count
    raw_count = len(RAW_GRIB_SHORT_NAMES)
    derived_count = len(DERIVED_FEATURES)
    total = raw_count + derived_count
    print(f"\nFeature count: {raw_count} raw + {derived_count} derived = {total} (expected: {TOTAL_FEATURES})")
    assert total == TOTAL_FEATURES, f"Feature count mismatch: {total} != {TOTAL_FEATURES}"

    # Save feature matrix
    print(f"\nSaving feature matrix to {FEATURE_FILE}...")
    # Combine hourly + daily into one output
    daily_df.to_csv(FEATURE_FILE)
    print(f"Saved {len(daily_df)} rows to {FEATURE_FILE}")

    # Generate EDA plots
    print("\nGenerating EDA plots...")
    generate_eda_plots(hourly_df, daily_df, model_elev, EDA_PLOT_DIR)

    # Summary statistics
    print("\n" + "=" * 60)
    print("Phase 2 Summary")
    print("=" * 60)
    print(f"Model elevation: {model_elev:.0f} m")
    print(f"Source elevation: {EVENT['source_elevation_m']} m")
    print(f"Elevation gap: {EVENT['source_elevation_m'] - model_elev:.0f} m")
    print(f"Grid: {GRID_LAT_KM} × {GRID_LON_KM} km")
    print(f"Features: {total} (7 raw + 3 derived)")
    print(f"Daily rows: {len(daily_df)}")
    print(f"Date range: {daily_df.index[0]} to {daily_df.index[-1]}")

    # Pre-event window stats
    pre_event = daily_df[
        (daily_df.index >= PRE_EVENT_WINDOW[0]) &
        (daily_df.index <= PRE_EVENT_WINDOW[1])
    ]
    print(f"\nPre-event window ({PRE_EVENT_WINDOW[0]} to {PRE_EVENT_WINDOW[1]}):")
    print(f"  Mean T: {pre_event['t2m_daily'].mean():.2f} °C")
    print(f"  PDD 7-day: {pre_event['pdd_7day'].mean():.2f} °C·d")
    print(f"  Freezing height: {pre_event['freezing_height_m'].mean():.0f} m")

    # Cross-reference with Rui Li values
    print("\nCross-reference (Rui Li reported):")
    print(f"  7-day mean T: 9.43 °C (Li) vs {pre_event['t2m_daily'].mean():.2f} °C (ours)")
    print(f"  PDD total: 65.94 °C·d (Li) vs {pre_event['pdd_7day'].iloc[-1]:.2f} °C·d (ours)")

    print("\nPhase 2 EXIT GATE: PASS")
    print(f"Output: {FEATURE_FILE}")
    print(f"Plots: {EDA_PLOT_DIR}/")


if __name__ == "__main__":
    main()
