"""Phase 1 (accelerated): ERA5-Land DAILY via DestinE Earth Data Hub Zarr v3.

Uses the new api.earthdatahub.destine.eu domain with standard API key auth.
Downloads JJA 2001-2026 daily data for the Nepal event region.

Variables available (14 total):
  t2m, d2m, u10, v10, tp, sp, ssr, ssrd, str, swvl1, swvl2, e, pev, ro

Missing from contract: sd (snow depth), sf (snowfall)
  → For JJA monsoon regime at 28°N, sd/sf are near-zero.
  → Documented as data-source substitution in preregistration.

Usage:
    source .venv/bin/activate
    export EDH_KEY="edh_key_xxxxx"
    python nepal/edh_daily_download.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from datetime import datetime

import numpy as np
import xarray as xr

sys.path.insert(0, str(Path(__file__).resolve().parent))
from feature_contract import EVENT

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
OUTPUT_FILE = DATA_DIR / "era5_land_nepal_jja_2001_2026.nc"
LOG_FILE = DATA_DIR / "edh_daily_download_log.txt"

# EDH Zarr v3 store URL (new API domain, standard API key auth)
EDH_URL_TEMPLATE = "https://edh:{key}@api.earthdatahub.destine.eu/era5/era5-land-daily-utc-v1.zarr"

# Region bounds
REGION_NORTH = 29.0
REGION_SOUTH = 27.0
REGION_WEST = 85.0
REGION_EAST = 86.0

# Variables to download (our 5 available contract variables)
VARS_TO_DOWNLOAD = ["t2m", "d2m", "u10", "v10", "tp"]

# Time range: JJA 2001-2026
YEARS = range(2001, 2027)
MONTHS = [6, 7, 8]


def log(message: str):
    timestamp = datetime.now().isoformat()
    line = f"[{timestamp}] {message}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def get_edh_key() -> str | None:
    """Get EDH API key from environment."""
    key = os.environ.get("EDH_KEY") or os.environ.get("EDH_PAT")
    if key:
        log(f"EDH key found (type: {'key' if key.startswith('edh_key_') else 'pat'})")
        return key
    log("EDH key NOT FOUND. Set: export EDH_KEY='edh_key_xxxxx'")
    return None


def main():
    log("=" * 60)
    log("Phase 1 (Accelerated): ERA5-Land DAILY via EDH Zarr v3")
    log("=" * 60)

    key = get_edh_key()
    if not key:
        sys.exit(1)

    url = EDH_URL_TEMPLATE.format(key=key)
    log(f"Opening EDH Zarr v3 store...")

    ds = xr.open_dataset(url, chunks={}, engine="zarr", zarr_format=3)
    log(f"Opened: {len(ds.data_vars)} variables")
    log(f"Variables: {list(ds.data_vars)}")
    log(f"Coords: {list(ds.coords)}")

    # Check time range
    time_coord = "valid_time" if "valid_time" in ds.coords else "time"
    log(f"Time coord: {time_coord}")
    log(f"Full time range: {ds[time_coord].values[0]} to {ds[time_coord].values[-1]}")

    # Select variables
    log(f"\nSelecting variables: {VARS_TO_DOWNLOAD}")
    subset = ds[VARS_TO_DOWNLOAD]

    # Select region
    lat_name = "latitude" if "latitude" in subset.coords else "lat"
    lon_name = "longitude" if "longitude" in subset.coords else "lon"
    log(f"Lat coord: {lat_name}, Lon coord: {lon_name}")

    # EDH lat is 90 to -90 (descending), lon is -180 to 180
    subset = subset.sel({
        lat_name: slice(REGION_NORTH, REGION_SOUTH),  # 29 to 27
        lon_name: slice(REGION_WEST, REGION_EAST),    # 85 to 86
    })
    log(f"Region selected: lat [{REGION_SOUTH}, {REGION_NORTH}], lon [{REGION_WEST}, {REGION_EAST}]")

    # Select JJA 2001-2026
    import pandas as pd
    times = subset[time_coord].values
    time_pd = pd.to_datetime(times)
    mask = time_pd.year.isin(YEARS) & time_pd.month.isin(MONTHS)
    selected_times = times[mask]
    log(f"Selected {len(selected_times)} daily time steps (JJA 2001-2026)")

    # Check 2026 data
    time_2026 = time_pd[time_pd.year == 2026]
    if len(time_2026) > 0:
        log(f"2026 data: {time_2026.min()} to {time_2026.max()}")
        # Check if Aug 19-25 is covered
        aug_2026 = time_2026[time_2026.month == 8]
        if len(aug_2026) > 0:
            log(f"Aug 2026 data: {aug_2026.min()} to {aug_2026.max()}")
            pre_event = aug_2026[(aug_2026 >= "2026-08-19") & (aug_2026 <= "2026-08-25")]
            log(f"Pre-event window (Aug 19-25): {len(pre_event)} days")
    else:
        log("WARNING: 2026 data NOT available")

    # Select time
    subset = subset.sel({time_coord: selected_times})

    # Trigger download
    log(f"\nTriggering download...")
    log(f"  Variables: {list(subset.data_vars)}")
    log(f"  Shape: {dict(subset.sizes)}")

    start = time.time()
    subset = subset.compute()
    elapsed = time.time() - start
    log(f"Download completed in {elapsed:.1f} seconds")
    log(f"Data size: {subset.nbytes / 1024 / 1024:.1f} MB")

    # Verify
    log("\n=== Verification ===")
    for var in subset.data_vars:
        vals = subset[var].values
        nan_count = int(np.isnan(vals).sum())
        log(f"  {var}: min={np.nanmin(vals):.2f}, max={np.nanmax(vals):.2f}, NaN={nan_count}")

    # Check temperature (should be Kelvin)
    if "t2m" in subset.data_vars:
        t2m = subset["t2m"].values
        log(f"  t2m range: {np.nanmin(t2m):.1f} - {np.nanmax(t2m):.1f} K")
        log(f"  t2m in Celsius: {np.nanmin(t2m)-273.15:.1f} - {np.nanmax(t2m)-273.15:.1f} °C")

    # Save
    log(f"\nSaving to {OUTPUT_FILE}...")
    subset.to_netcdf(OUTPUT_FILE)
    log(f"Saved: {OUTPUT_FILE.stat().st_size / 1024 / 1024:.1f} MB")

    log("\n" + "=" * 60)
    log("EDH DAILY DOWNLOAD COMPLETE")
    log(f"Output: {OUTPUT_FILE}")
    log(f"Variables: {list(subset.data_vars)}")
    log(f"Time range: {subset[time_coord].values[0]} to {subset[time_coord].values[-1]}")
    log(f"Missing contract vars: sd (snow depth), sf (snowfall)")
    log(f"  → Near-zero for JJA monsoon at 28°N, documented as substitution")
    log("=" * 60)


if __name__ == "__main__":
    main()
