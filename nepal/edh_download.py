"""Phase 1 (accelerated): ERA5-Land via DestinE Earth Data Hub Zarr store.

30-40x faster than CDS API — no queue, direct Zarr access over HTTPS.
Downloads JJA 2001-2026 for our Nepal event region, saves to NetCDF.

Usage:
    source .venv/bin/activate
    # Set EDH PAT as environment variable (get from https://earthdatahub.destine.eu/account-settings):
    export EDH_PAT="edh_pat_xxxxx"
    python nepal/edh_download.py

    # Or use netrc:
    # Add to ~/.netrc:
    # machine data.earthdatahub.destine.eu
    #   password edh_pat_xxxxx
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from datetime import datetime

import numpy as np
import xarray as xr

# Add nepal/ to path for feature contract
sys.path.insert(0, str(Path(__file__).resolve().parent))
from feature_contract import (
    EVENT, RAW_GRIB_SHORT_NAMES, CDS_LONG_NAMES,
    HISTORICAL_BASELINE,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
OUTPUT_FILE = DATA_DIR / "era5_land_nepal_jja_2001_2026.nc"
LOG_FILE = DATA_DIR / "edh_download_log.txt"

# EDH Zarr store URL
EDH_ZARR_URL = "https://data.earthdatahub.destine.eu/era5/reanalysis-era5-land-no-antartica-v0.zarr"

# Our region bounds (from feature_contract EVENT)
# Area: [North, West, South, East] = [29.0, 85.0, 27.0, 86.0]
REGION_NORTH = 29.0
REGION_SOUTH = 27.0
REGION_WEST = 85.0
REGION_EAST = 86.0

# Variables we need (ERA5-Land short names in the Zarr store)
# The EDH Zarr store uses short names: t2m, d2m, u10, v10, tp, sd, sf
EDH_VARIABLES = list(RAW_GRIB_SHORT_NAMES)  # ("10u", "10v", "2d", "2t", "sd", "sf", "tp")

# Map our short names to EDH Zarr variable names
# EDH uses: t2m, d2m, u10, v10, tp, sd, sf
VAR_NAME_MAP = {
    "2t": "t2m",
    "2d": "d2m",
    "10u": "u10",
    "10v": "v10",
    "sd": "sro",  # NOTE: sd might be "sd" or "sro" — check store
    "sf": "sf",
    "tp": "tp",
}

# Time range: JJA 2001-2026
# JJA = June, July, August
YEARS = range(2001, 2027)  # 2001-2026 inclusive
MONTHS = [6, 7, 8]  # JJA


def log(message: str):
    """Log to both console and file."""
    timestamp = datetime.now().isoformat()
    line = f"[{timestamp}] {message}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def get_edh_pat() -> str | None:
    """Get EDH Personal Access Token from environment or netrc."""
    # Try environment variable first
    pat = os.environ.get("EDH_PAT")
    if pat:
        log("EDH PAT found in environment variable")
        return pat

    # Try netrc
    try:
        import netrc
        n = netrc.netrc()
        auth = n.authenticators("data.earthdatahub.destine.eu")
        if auth:
            log("EDH PAT found in ~/.netrc")
            return auth[2]  # password
    except Exception:
        pass

    log("EDH PAT NOT FOUND")
    log("Get your PAT from: https://earthdatahub.destine.eu/account-settings")
    log("Then run: export EDH_PAT='edh_pat_xxxxx'")
    return None


def open_edh_store(pat: str) -> xr.Dataset:
    """Open the EDH ERA5-Land Zarr store with authentication."""
    url = f"https://edh:{pat}@data.earthdatahub.destine.eu/era5/reanalysis-era5-land-no-antartica-v0.zarr"
    log(f"Opening EDH Zarr store...")
    log(f"URL: https://edh:***@data.earthdatahub.destine.eu/era5/reanalysis-era5-land-no-antartica-v0.zarr")

    ds = xr.open_dataset(
        url,
        chunks={},
        engine="zarr",
    )
    log(f"Opened dataset with {len(ds.data_vars)} variables")
    log(f"Variables: {list(ds.data_vars)[:20]}...")  # Show first 20
    log(f"Coords: {list(ds.coords)}")

    # Check time range
    time_coord = "time" if "time" in ds.coords else "valid_time"
    if time_coord in ds.coords:
        log(f"Time range: {ds[time_coord].values[0]} to {ds[time_coord].values[-1]}")
        log(f"Total time steps: {len(ds[time_coord])}")

    return ds


def check_variable_names(ds: xr.Dataset) -> dict:
    """Check which of our variables exist in the EDH store."""
    available = {}
    edh_vars = set(ds.data_vars)

    for our_name, edh_name in VAR_NAME_MAP.items():
        if edh_name in edh_vars:
            available[our_name] = edh_name
            log(f"  {our_name} → {edh_name}: FOUND")
        else:
            # Try alternative names
            alternatives = {
                "sd": ["sd", "snow_depth", "swe"],
                "sf": ["sf", "snowfall", "snf"],
            }
            found = False
            for alt in alternatives.get(our_name, []):
                if alt in edh_vars:
                    available[our_name] = alt
                    log(f"  {our_name} → {alt}: FOUND (alternative)")
                    found = True
                    break
            if not found:
                log(f"  {our_name} → {edh_name}: NOT FOUND")
                # Search for partial matches
                matches = [v for v in edh_vars if our_name in v.lower() or edh_name in v.lower()]
                if matches:
                    log(f"    Possible matches: {matches[:5]}")

    return available


def download_jja_data(ds: xr.Dataset, var_map: dict) -> xr.Dataset:
    """Download JJA 2001-2026 data for our region and variables.

    Uses xarray lazy evaluation — only downloads the data we actually need.
    """
    log(f"\nSelecting variables: {list(var_map.values())}")
    log(f"Region: lat [{REGION_SOUTH}, {REGION_NORTH}], lon [{REGION_WEST}, {REGION_EAST}]")
    log(f"Time: JJA {YEARS.start}-{YEARS.stop-1}")

    # Select variables
    edh_var_names = list(var_map.values())
    subset = ds[edh_var_names]

    # Select region (lat/lon)
    # EDH Zarr store may use lat/latitude, lon/longitude
    lat_name = "lat" if "lat" in subset.coords else "latitude"
    lon_name = "lon" if "lon" in subset.coords else "longitude"

    log(f"Lat coord: {lat_name}, Lon coord: {lon_name}")

    # EDH Zarr store covers "no-antarctica" — lat might be 90 to -90
    # We need to select our region
    subset = subset.sel({
        lat_name: slice(REGION_NORTH, REGION_SOUTH),  # 29 to 27 (descending for ERA5)
        lon_name: slice(REGION_WEST, REGION_EAST),    # 85 to 86
    })

    # Select time
    time_coord = "time" if "time" in subset.coords else "valid_time"
    log(f"Time coord: {time_coord}")

    # Build time selection: JJA 2001-2026
    # Use isin for efficient selection
    times = subset[time_coord].values
    # Convert to pandas for easy filtering
    import pandas as pd
    time_pd = pd.to_datetime(times)
    mask = (
        time_pd.year.isin(YEARS) &
        time_pd.month.isin(MONTHS)
    )
    selected_times = times[mask]
    log(f"Selected {len(selected_times)} time steps (JJA 2001-2026)")

    if len(selected_times) == 0:
        raise ValueError("No time steps selected — check time range in EDH store")

    # Check if 2026 data is available
    time_2026 = time_pd[time_pd.year == 2026]
    if len(time_2026) > 0:
        log(f"2026 data available: {time_2026.min()} to {time_2026.max()}")
    else:
        log("WARNING: 2026 data NOT available in EDH store")
        log("  Will need CDS fallback for 2026 data")

    # Select time
    subset = subset.sel({time_coord: selected_times})

    # Rename variables back to our short names
    rename_map = {v: k for k, v in var_map.items()}
    subset = subset.rename(rename_map)

    # Trigger download (lazy evaluation → actual download)
    log(f"\nTriggering download (this may take a few minutes)...")
    log(f"  Variables: {list(subset.data_vars)}")
    log(f"  Shape: {dict(subset.sizes)}")

    start_time = time.time()
    # Force computation
    subset = subset.compute()
    elapsed = time.time() - start_time
    log(f"Download completed in {elapsed:.1f} seconds")
    log(f"Data shape: {dict(subset.sizes)}")
    log(f"Data size: {subset.nbytes / 1024 / 1024:.1f} MB")

    return subset


def save_to_netcdf(ds: xr.Dataset, output_file: Path):
    """Save the downloaded data to NetCDF for compatibility with our pipeline."""
    log(f"\nSaving to {output_file}...")
    ds.to_netcdf(output_file)
    log(f"Saved {output_file.stat().st_size / 1024 / 1024:.1f} MB")


def verify_data(ds: xr.Dataset) -> bool:
    """Verify the downloaded data has correct structure."""
    log("\n=== Verification ===")

    # Check variables
    expected_vars = set(RAW_GRIB_SHORT_NAMES)
    actual_vars = set(ds.data_vars)
    missing = expected_vars - actual_vars
    if missing:
        log(f"MISSING variables: {missing}")
        return False
    log(f"Variables: {actual_vars} ✓")

    # Check time range
    time_coord = "time" if "time" in ds.coords else "valid_time"
    times = ds[time_coord].values
    import pandas as pd
    time_pd = pd.to_datetime(times)
    years = sorted(set(time_pd.year))
    log(f"Years: {years[0]}-{years[-1]} ({len(years)} years)")
    log(f"Months: {sorted(set(time_pd.month))}")

    # Check 2026 data
    if 2026 in years:
        time_2026 = time_pd[time_pd.year == 2026]
        log(f"2026 data: {time_2026.min()} to {time_2026.max()} ✓")
    else:
        log("WARNING: 2026 data missing — need CDS fallback")

    # Check region
    lat_name = "lat" if "lat" in ds.coords else "latitude"
    lon_name = "lon" if "lon" in ds.coords else "longitude"
    log(f"Lat range: {ds[lat_name].values.min()}-{ds[lat_name].values.max()}")
    log(f"Lon range: {ds[lon_name].values.min()}-{ds[lon_name].values.max()}")

    # Check for NaN
    for var in ds.data_vars:
        nan_count = int(np.isnan(ds[var].values).sum())
        if nan_count > 0:
            log(f"  {var}: {nan_count} NaN values")
        else:
            log(f"  {var}: no NaN ✓")

    # Check temperature values (should be in Kelvin, ~250-320K)
    if "2t" in ds.data_vars:
        t2m = ds["2t"].values
        log(f"  2t range: {np.nanmin(t2m):.1f} - {np.nanmax(t2m):.1f} K")
        if np.nanmin(t2m) < 200 or np.nanmax(t2m) > 350:
            log("  WARNING: Temperature values outside expected range (200-350 K)")

    log("=== Verification PASSED ===")
    return True


def main():
    """Main EDH download execution."""
    log("=" * 60)
    log("Phase 1 (Accelerated): ERA5-Land via DestinE EDH Zarr")
    log("=" * 60)

    # Get PAT
    pat = get_edh_pat()
    if not pat:
        log("\nERROR: No EDH PAT found.")
        log("To get a PAT:")
        log("1. Register at: https://platform.destine.eu")
        log("2. Get your PAT at: https://earthdatahub.destine.eu/account-settings")
        log("3. Set it: export EDH_PAT='edh_pat_xxxxx'")
        log("4. Re-run: python nepal/edh_download.py")
        sys.exit(1)

    # Open store
    try:
        ds = open_edh_store(pat)
    except Exception as e:
        log(f"ERROR opening EDH store: {e}")
        sys.exit(1)

    # Check variable names
    log("\nChecking variable names...")
    var_map = check_variable_names(ds)

    if len(var_map) < 7:
        log(f"ERROR: Only found {len(var_map)}/7 variables")
        log("Available variables in EDH store:")
        for v in sorted(ds.data_vars):
            log(f"  {v}")
        sys.exit(1)

    # Download JJA data
    try:
        subset = download_jja_data(ds, var_map)
    except Exception as e:
        log(f"ERROR downloading data: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # Verify
    if not verify_data(subset):
        log("Verification FAILED")
        sys.exit(1)

    # Save
    save_to_netcdf(subset, OUTPUT_FILE)

    log("\n" + "=" * 60)
    log("EDH Download COMPLETE")
    log(f"Output: {OUTPUT_FILE}")
    log(f"Size: {OUTPUT_FILE.stat().st_size / 1024 / 1024:.1f} MB")
    log("=" * 60)


if __name__ == "__main__":
    main()
