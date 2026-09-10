"""ERA5-Land download for Nepal Event Anomaly Assessment — Phase 1.

Downloads ERA5-Land hourly data for JJA (Jun-Aug) 2001-2026 for the
nearest grid cell to the Langtang Lirung source zone.

This is a MINIMAL, Nepal-specific download — NOT the multi-winter
Pir Panjal acquisition. It requests only 7 variables (the pre-registered
feature contract) for JJA months only, for a small area around the event.

Strategy:
- Request JJA (June, July, August) for years 2001-2026
- 7 variables only (pre-registered feature contract)
- Small area: 1° × 1° around the event point (28.25°N, 85.50°E)
- Daily aggregation after download (reduce storage)
- Sequential with retry (CDS queue can be slow)

Usage:
    source .venv/bin/activate
    python nepal/era5_download.py [--dry-run] [--year-range 2001-2026]

Output:
    data/era5_land_nepal_jja_2001_2026.nc  (~200-500 MB)
    data/era5_land_nepal_jja_2026_daily.csv (for quick verification)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from datetime import datetime

import cdsapi

# --- Configuration (from feature_contract.py) ---

# CDS dataset
CDS_DATASET = "reanalysis-era5-land"

# Pre-registered variables (7 raw ERA5-Land variables)
# Using CDS long names (Astra correction: GRIB short names are NOT CDS request names)
CDS_VARIABLES = [
    "2m_temperature",
    "2m_dewpoint_temperature",
    "10m_u_component_of_wind",
    "10m_v_component_of_wind",
    "snow_depth",
    "snowfall",
    "total_precipitation",
]

# Event location (Hausfather's nearest cell)
EVENT_LAT = 28.25
EVENT_LON = 85.50

# Download area: small box around the event point
# CDS area format: [North, West, South, East]
# 1° × 1° box: ~111 km × ~98 km — covers the source and immediate context
AREA = [29.0, 85.0, 27.0, 86.0]

# JJA months
JJA_MONTHS = ["06", "07", "08"]

# Years: 2001-2026 (25-year baseline + event year)
YEAR_RANGE = list(range(2001, 2027))

# All 24 hours
HOURS = [f"{h:02d}:00" for h in range(24)]

# Days per month
DAYS = [f"{d:02d}" for d in range(1, 32)]

# Output paths
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUTPUT_FILE = DATA_DIR / "era5_land_nepal_jja_2001_2026.nc"
LEDGER_FILE = DATA_DIR / "download_ledger.json"


def build_request(year: int, month: str) -> dict:
    """Build a CDS API request for one month."""
    return {
        "variable": CDS_VARIABLES,
        "year": str(year),
        "month": month,
        "day": DAYS,
        "time": HOURS,
        "area": AREA,
        "data_format": "netcdf",
    }


def download_month(client: cdsapi.Client, year: int, month: str,
                   output_path: Path, max_retries: int = 3) -> bool:
    """Download one month of ERA5-Land data with retry."""
    request = build_request(year, month)

    for attempt in range(max_retries):
        try:
            print(f"  Requesting {year}-{month} (attempt {attempt + 1}/{max_retries})...")
            client.retrieve(
                CDS_DATASET,
                request,
                str(output_path),
            )
            size_mb = output_path.stat().st_size / (1024 * 1024)
            print(f"  Downloaded {year}-{month}: {size_mb:.1f} MB")
            return True
        except Exception as e:
            wait = 10 * (2 ** attempt)
            print(f"  Error for {year}-{month}: {e}")
            print(f"  Waiting {wait}s before retry...")
            time.sleep(wait)

    print(f"  FAILED after {max_retries} retries: {year}-{month}")
    return False


def download_all(years: list[int], dry_run: bool = False) -> dict:
    """Download ERA5-Land JJA for all specified years.

    Downloads month-by-month to keep individual CDS requests small
    and allow partial retry on failure.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    ledger = {
        "start_time": datetime.now().isoformat(),
        "dataset": CDS_DATASET,
        "variables": CDS_VARIABLES,
        "area": AREA,
        "event_cell": (EVENT_LAT, EVENT_LON),
        "years": years,
        "months": JJA_MONTHS,
        "status": "started",
        "completed_months": [],
        "failed_months": [],
        "total_size_mb": 0,
    }

    if dry_run:
        total_months = len(years) * len(JJA_MONTHS)
        print(f"DRY RUN: Would download {total_months} months "
              f"({len(years)} years × {len(JJA_MONTHS)} months)")
        print(f"Variables: {CDS_VARIABLES}")
        print(f"Area: {AREA}")
        print(f"Output: {OUTPUT_FILE}")
        ledger["status"] = "dry_run"
        return ledger

    # Initialize CDS client (uses ~/.cdsapirc automatically)
    print("Initializing CDS client...")
    client = cdsapi.Client(quiet=True)
    print("CDS client initialized.")

    # Download month by month
    temp_dir = DATA_DIR / "temp"
    temp_dir.mkdir(exist_ok=True)

    for year in years:
        for month in JJA_MONTHS:
            month_file = temp_dir / f"era5_land_{year}_{month}.nc"

            if month_file.exists():
                size_mb = month_file.stat().st_size / (1024 * 1024)
                print(f"  {year}-{month}: already exists ({size_mb:.1f} MB), skipping")
                ledger["completed_months"].append({
                    "year": year,
                    "month": month,
                    "size_mb": round(size_mb, 1),
                    "status": "skipped_existing",
                })
                ledger["total_size_mb"] += size_mb
                continue

            success = download_month(client, year, month, month_file)
            if success:
                size_mb = month_file.stat().st_size / (1024 * 1024)
                ledger["completed_months"].append({
                    "year": year,
                    "month": month,
                    "size_mb": round(size_mb, 1),
                    "status": "downloaded",
                })
                ledger["total_size_mb"] += size_mb
            else:
                ledger["failed_months"].append({
                    "year": year,
                    "month": month,
                    "status": "failed",
                })

            # Save ledger after each month (checkpoint)
            with open(LEDGER_FILE, "w") as f:
                json.dump(ledger, f, indent=2)

    # Merge all monthly files into one
    if ledger["completed_months"]:
        print("\nMerging monthly files into single NetCDF...")
        try:
            import xarray as xr
            datasets = []
            for entry in ledger["completed_months"]:
                f = temp_dir / f"era5_land_{entry['year']}_{entry['month']}.nc"
                if f.exists():
                    ds = xr.open_dataset(f)
                    datasets.append(ds)

            if datasets:
                merged = xr.concat(datasets, dim="time")
                # Select nearest cell to event point
                if "latitude" in merged.coords:
                    cell = merged.sel(latitude=EVENT_LAT, longitude=EVENT_LON, method="nearest")
                    cell.to_netcdf(OUTPUT_FILE)
                elif "lat" in merged.coords:
                    cell = merged.sel(lat=EVENT_LAT, lon=EVENT_LON, method="nearest")
                    cell.to_netcdf(OUTPUT_FILE)

                final_size_mb = OUTPUT_FILE.stat().st_size / (1024 * 1024)
                print(f"Merged file: {OUTPUT_FILE} ({final_size_mb:.1f} MB)")
                ledger["final_file"] = str(OUTPUT_FILE)
                ledger["final_size_mb"] = round(final_size_mb, 1)

                # Clean up temp files
                for ds in datasets:
                    ds.close()
                for f in temp_dir.glob("era5_land_*.nc"):
                    f.unlink()
                temp_dir.rmdir()

        except Exception as e:
            print(f"Merge error: {e}")
            ledger["merge_error"] = str(e)

    ledger["end_time"] = datetime.now().isoformat()
    ledger["status"] = "completed" if not ledger["failed_months"] else "completed_with_failures"

    with open(LEDGER_FILE, "w") as f:
        json.dump(ledger, f, indent=2)

    print(f"\nDownload complete: {len(ledger['completed_months'])} months, "
          f"{len(ledger['failed_months'])} failed, "
          f"{ledger['total_size_mb']:.1f} MB total")
    print(f"Ledger: {LEDGER_FILE}")

    return ledger


def main():
    parser = argparse.ArgumentParser(description="Download ERA5-Land for Nepal event analysis")
    parser.add_argument("--dry-run", action="store_true", help="Print request without downloading")
    parser.add_argument("--year-range", type=str, default="2001-2026",
                        help="Year range (e.g., 2001-2026)")
    args = parser.parse_args()

    if "-" in args.year_range:
        start, end = map(int, args.year_range.split("-"))
        years = list(range(start, end + 1))
    else:
        years = [int(args.year_range)]

    print(f"Nepal Event Anomaly — ERA5-Land Download")
    print(f"Years: {years[0]}-{years[-1]} ({len(years)} years)")
    print(f"Months: JJA ({JJA_MONTHS})")
    print(f"Variables: {len(CDS_VARIABLES)}")
    print(f"Area: {AREA} (1° × 1° around event)")
    print(f"Event cell: ({EVENT_LAT}°N, {EVENT_LON}°E)")
    print()

    ledger = download_all(years, dry_run=args.dry_run)

    if ledger["failed_months"]:
        print(f"\nWARNING: {len(ledger['failed_months'])} months failed. "
              f"Re-run to retry failed months.")
        sys.exit(1)


if __name__ == "__main__":
    main()
