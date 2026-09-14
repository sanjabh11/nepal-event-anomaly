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

import argparse
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
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

ERA5_FILENAME = "era5_land_nepal_jja_2001_2026.nc"
FEATURE_FILENAME = "features_nepal_jja_2001_2026.csv"
HOURLY_FILENAME = "features_nepal_hourly_jja_2001_2026.csv"
UNITS_FILENAME = "feature_units.json"

# P5-01: data/ is a FROZEN contract surface — no output may be written
# there. Outputs default to a research_runs/ run root (repo-root
# relative). Reading inputs from data/ remains allowed (fallback below).
DEFAULT_RUN_ROOT = REPO_ROOT / "research_runs" / "gmm_confirmation" / "features"
ERA5_FALLBACK_FILE = DATA_DIR / ERA5_FILENAME  # input only, never output

# Frozen surfaces that must never receive pipeline outputs.
FORBIDDEN_RUN_ROOTS = (
    DATA_DIR,
    REPO_ROOT / "pinned",
    REPO_ROOT / "nepal" / "framework_v1",
)

# Elevation disclaimer (printed on every plot)
ELEVATION_DISCLAIMER = (
    f"ERA5-Land model elevation: {EVENT['model_elevation_m']} m. "
    f"Source slope: {EVENT['source_elevation_m']} m. "
    f"Delta: {EVENT['elevation_gap_m']} m. "
    f"Grid: {GRID_LAT_KM} × {GRID_LON_KM} km."
)

# Lapse rate for elevation extrapolation (K/m)
LAPSE_RATE = -0.0065  # Standard atmospheric lapse rate

# CFM-02: units for every daily feature column written to the feature
# CSV. Temperatures are converted K→°C and accumulations m→mm in
# extract_raw_features; this dict is the sidecar contract.
UNITS = {
    "t2m_daily": "degC",
    "d2m_daily": "degC",
    "tp_daily": "mm",
    "sf_daily": "mm",
    "sd_daily": "mm",
    "wind_speed_daily": "m/s",
    "wind_dir_sin": "unitless",
    "wind_dir_cos": "unitless",
    "rh_daily": "percent",
    "pdd_daily": "degC*day",
    "pdd_7day": "degC*day",
    "freezing_height_m": "m",
}


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
    missing = []

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
                missing.append(f"{cds_name} (expected as {nc_name})")

    # P5-06: fail closed — never substitute NaN for a required variable.
    # sde is geometric snow depth, NOT the contract sd (snow-depth water
    # equivalent); it must never be renamed to sd.
    if "sd" not in data and "sde" in ds.data_vars:
        raise ValueError(
            "Dataset contains 'sde' (geometric snow depth) but not 'sd'. "
            "sde is NOT the contract snow-depth water-equivalent (SWE) "
            "variable; refusing to rename sde to sd."
        )
    if missing:
        raise ValueError(
            "Missing required ERA5-Land variables: " + ", ".join(missing)
        )

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
    """Compute daily feature matrix with ALL 10 features + thermal indices.

    GAP FIX: Previously only output 4 columns (t2m, pdd, pdd_7day, freezing_height).
    Now aggregates ALL 10 pre-registered features to daily level:
      - Temperature variables: daily MEAN (t2m, d2m)
      - Accumulation variables: daily SUM (tp, sf) — NOT mean
      - Instantaneous variables: daily MEAN (sd, wind_speed, wind_dir_sin/cos, RH)
      - Thermal indices: PDD, 7-day PDD, freezing height (computed, not counted as features)

    ERA5-Land accumulation note: tp and sf are per-hour accumulations.
    Daily total = sum of 24 hourly values, NOT mean.
    """
    daily_df = pd.DataFrame()

    # --- Temperature variables: daily MEAN (°C) ---
    if "t2m" in df.columns:
        daily_df["t2m_daily"] = df["t2m"].resample("D").mean()
    if "d2m" in df.columns:
        daily_df["d2m_daily"] = df["d2m"].resample("D").mean()

    # --- Accumulation variables: daily SUM (mm) ---
    # GAP FIX: ERA5-Land tp/sf are per-hour accumulations.
    # resample("D").mean() gives hourly mean rate, NOT daily total.
    # Must use .sum() for daily total precipitation/snowfall.
    if "tp" in df.columns:
        daily_df["tp_daily"] = df["tp"].resample("D").sum()
    if "sf" in df.columns:
        daily_df["sf_daily"] = df["sf"].resample("D").sum()

    # --- Instantaneous variables: daily MEAN ---
    if "sd" in df.columns:
        daily_df["sd_daily"] = df["sd"].resample("D").mean()

    # --- Wind: daily MEAN speed, sin/cos encoded direction ---
    if "wind_speed" in df.columns:
        daily_df["wind_speed_daily"] = df["wind_speed"].resample("D").mean()
    if "wind_dir" in df.columns:
        # GAP FIX: wind_dir is circular. Encode as sin/cos for clustering.
        # Raw radians are linear; sin/cos preserves circularity.
        # Circular mean: average the sin/cos components (the mean
        # resultant vector), NOT sin/cos of the mean angle — the old
        # form collapsed bimodal directions incorrectly (CFM-05).
        daily_df["wind_dir_sin"] = np.sin(df["wind_dir"]).resample("D").mean()
        daily_df["wind_dir_cos"] = np.cos(df["wind_dir"]).resample("D").mean()

    # --- Relative humidity: daily MEAN (%) ---
    if "relative_humidity" in df.columns:
        daily_df["rh_daily"] = df["relative_humidity"].resample("D").mean()

    # --- Thermal indices (computed, not counted as features) ---
    # Daily PDD: max(0, T_daily)
    if "t2m_daily" in daily_df.columns:
        daily_df["pdd_daily"] = daily_df["t2m_daily"].clip(lower=0)
        # 7-day rolling PDD — full window required; partial sums at the
        # series edge are censored to NaN, not silently down-weighted
        # (CFM-05). CFM-01: the daily frame holds JJA rows only, so a
        # naive 7-day window silently spans the Aug31→Jun1 year boundary
        # (and any other gap). Roll only within runs of consecutive
        # calendar dates; edge days at run starts get NaN.
        run_id = (daily_df.index.to_series().diff().dt.days != 1).cumsum()
        daily_df["pdd_7day"] = daily_df["pdd_daily"].groupby(run_id).transform(
            lambda s: s.rolling(window=7, min_periods=7).sum()
        )

        # Freezing level height: z_freeze = z_model + T_model / 0.0065
        # lapse_rate = -0.0065 K/m = -0.0065 °C/m
        # T(z) = T_model - 0.0065 * (z - z_model)
        # 0 = T_model - 0.0065 * (z_freeze - z_model)
        # z_freeze = z_model + T_model / 0.0065
        daily_df["freezing_height_m"] = model_elev_m + daily_df["t2m_daily"] / 0.0065

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


def _resolve_run_root(run_root: str | None) -> Path:
    """Resolve the output run root and reject frozen contract surfaces."""
    root = Path(run_root).expanduser() if run_root else DEFAULT_RUN_ROOT
    if not root.is_absolute():
        root = REPO_ROOT / root
    root = root.resolve()
    for forbidden in FORBIDDEN_RUN_ROOTS:
        frozen = forbidden.resolve()
        if root == frozen or frozen in root.parents:
            raise ValueError(
                f"Run root {root} is inside frozen surface {frozen}; "
                "refusing to write outputs there."
            )
    return root


def main():
    """Main Phase 2 execution."""
    parser = argparse.ArgumentParser(
        description="Phase 2: Feature extraction + PDD + EDA "
                    "(GMM confirmation run)."
    )
    parser.add_argument(
        "--run-root",
        default=None,
        help=("Output run root. Default: research_runs/gmm_confirmation/"
              "features/ relative to the repo root. Rejected if inside "
              "data/, pinned/, or nepal/framework_v1/."),
    )
    parser.add_argument(
        "--era5-file",
        default=None,
        help=("Input ERA5-Land NetCDF. Default: <run-root>/era5/merged/"
              f"{ERA5_FILENAME} with fallback to data/{ERA5_FILENAME}."),
    )
    args = parser.parse_args()

    run_root = _resolve_run_root(args.run_root)
    run_root.mkdir(parents=True, exist_ok=True)
    feature_file = run_root / FEATURE_FILENAME
    hourly_file = run_root / HOURLY_FILENAME
    units_file = run_root / UNITS_FILENAME
    eda_plot_dir = run_root / "eda"

    # Input ERA5 file: explicit --era5-file, else the run-root copy,
    # else the frozen data/ fallback (reading from data/ is allowed).
    if args.era5_file:
        era5_file = Path(args.era5_file).expanduser().resolve()
    else:
        era5_file = run_root / "era5" / "merged" / ERA5_FILENAME
        if not era5_file.exists():
            era5_file = ERA5_FALLBACK_FILE

    print("=" * 60)
    print("Phase 2: Feature Extraction + PDD + EDA")
    print("=" * 60)
    print()
    print(f"Run root: {run_root}")
    print(f"ERA5 input: {era5_file}")

    # Load ERA5-Land data
    ds, actual_lat, actual_lon, model_elev = load_era5_land(era5_file)
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

    # Fail loudly if any feature row is on/after the held-out event
    # date — nothing on/after POST_EVENT_CUTOFF may be a feature.
    post_event_rows = daily_df.index >= pd.Timestamp(POST_EVENT_CUTOFF)
    if post_event_rows.any():
        raise ValueError(
            f"Post-event leakage: {int(post_event_rows.sum())} feature "
            f"rows on/after POST_EVENT_CUTOFF ({POST_EVENT_CUTOFF})."
        )

    # Verify feature count
    raw_count = len(RAW_GRIB_SHORT_NAMES)
    derived_count = len(DERIVED_FEATURES)
    total = raw_count + derived_count
    print(f"\nFeature count: {raw_count} raw + {derived_count} derived = {total} (expected: {TOTAL_FEATURES})")
    assert total == TOTAL_FEATURES, f"Feature count mismatch: {total} != {TOTAL_FEATURES}"

    # Save feature matrix
    print(f"\nSaving daily feature matrix to {feature_file}...")
    daily_df.to_csv(feature_file)
    print(f"Saved {len(daily_df)} rows, {len(daily_df.columns)} columns to {feature_file}")

    # CFM-02: units sidecar next to the feature CSV. Fail closed if any
    # output column lacks a unit entry.
    missing_units = [c for c in daily_df.columns if c not in UNITS]
    if missing_units:
        raise ValueError(f"UNITS missing entries for columns: {missing_units}")
    with open(units_file, "w") as f:
        json.dump(UNITS, f, indent=2)
    print(f"Saved units sidecar to {units_file}")

    # GAP FIX: Also save hourly features for auditability
    print(f"Saving hourly feature matrix to {hourly_file}...")
    hourly_df.to_csv(hourly_file)
    print(f"Saved {len(hourly_df)} rows, {len(hourly_df.columns)} columns to {hourly_file}")

    # Generate EDA plots
    print("\nGenerating EDA plots...")
    generate_eda_plots(hourly_df, daily_df, model_elev, eda_plot_dir)

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

    # GAP FIX: Hausfath cross-validation gap
    # Hausfath's committed daily data ends Aug 22, 2026.
    # Our pre-event window is Aug 19-25. We can only cross-validate Aug 19-22.
    print("\nCross-reference (Hausfath ERA5 0.25°):")
    print(f"  GAP: Hausfath data ends Aug 22, 2026. Pre-event window is Aug 19-25.")
    print(f"  Can only cross-validate Aug 19-22 (4 of 7 days).")
    hausfath_file = DATA_DIR / "hausfath_reference" / "langtang_t2m_daily_2026.nc"
    if hausfath_file.exists():
        try:
            import xarray as xr
            hf = xr.open_dataset(hausfath_file)
            hf_cell = hf.sel(latitude=28.25, longitude=85.5, method="nearest")
            hf_pre = hf_cell.where(
                (hf_cell.valid_time >= np.datetime64(PRE_EVENT_WINDOW[0])) &
                (hf_cell.valid_time <= np.datetime64("2026-08-22")),
                drop=True,
            )
            if len(hf_pre.valid_time) > 0:
                hf_t2m_c = hf_pre.t2m.values - 273.15
                print(f"  Hausfath Aug 19-22 mean T: {np.nanmean(hf_t2m_c):.2f} °C (ERA5 0.25°)")
                print(f"  Our Aug 19-22 mean T: {pre_event[pre_event.index <= '2026-08-22']['t2m_daily'].mean():.2f} °C (ERA5-Land)")
            hf.close()
        except Exception as e:
            print(f"  Hausfath comparison error: {e}")

    print("\nPhase 2 EXIT GATE: PASS")
    print(f"Output: {feature_file}")
    print(f"Plots: {eda_plot_dir}/")


if __name__ == "__main__":
    main()
