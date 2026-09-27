"""ERA5-Land download for Nepal Event Anomaly Assessment — Phase 1 (Optimized).

Optimized version: requests all 3 JJA months per year in a single CDS API call.
This reduces 78 CDS API requests to 26, saving ~8 hours of queue wait time.

Downloads ERA5-Land hourly data for JJA (Jun-Aug) 2001-2026 for the
nearest grid cell to the Langtang Lirung source zone.

Strategy:
- Request JJA (June, July, August) for each year in a SINGLE CDS API call
- 7 variables only (pre-registered feature contract)
- Small area: 2° × 1° around the event point (28.25°N, 85.50°E)
- Sequential with retry (CDS queue can be slow)
- Checkpoint after each year (resumable)

Usage:
    source .venv/bin/activate
    python nepal/era5_download_optimized.py [--dry-run] [--year-range 2001-2026]

Output:
    data/era5_land_nepal_jja_2001_2026.nc  (~50-200 MB after cell selection)
    data/download_ledger.json (checkpoint ledger)
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

CDS_DATASET = "reanalysis-era5-land"

CDS_VARIABLES = [
    "2m_temperature",
    "2m_dewpoint_temperature",
    "10m_u_component_of_wind",
    "10m_v_component_of_wind",
    "snow_depth",
    "snowfall",
    "total_precipitation",
]

EVENT_LAT = 28.25
EVENT_LON = 85.50

AREA = [29.0, 85.0, 27.0, 86.0]

JJA_MONTHS = ["06", "07", "08"]

YEAR_RANGE = list(range(2001, 2027))

HOURS = [f"{h:02d}:00" for h in range(24)]

DAYS = [f"{d:02d}" for d in range(1, 32)]

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUTPUT_FILE = DATA_DIR / "era5_land_nepal_jja_2001_2026.nc"
LEDGER_FILE = DATA_DIR / "download_ledger.json"
TEMP_DIR = DATA_DIR / "temp"


def build_year_request(year: int) -> dict:
    """Build a CDS API request for all JJA months of one year."""
    return {
        "variable": CDS_VARIABLES,
        "year": str(year),
        "month": JJA_MONTHS,
        "day": DAYS,
        "time": HOURS,
        "area": AREA,
        "data_format": "netcdf",
    }


def download_year(client: cdsapi.Client, year: int,
                  output_path: Path, max_retries: int = 3) -> bool:
    """Download all JJA months for one year with retry."""
    request = build_year_request(year)

    for attempt in range(max_retries):
        try:
            print(f"  Requesting {year} JJA (attempt {attempt + 1}/{max_retries})...")
            client.retrieve(
                CDS_DATASET,
                request,
                str(output_path),
            )
            size_mb = output_path.stat().st_size / (1024 * 1024)
            print(f"  Downloaded {year} JJA: {size_mb:.1f} MB")
            return True
        except Exception as e:
            wait = 10 * (2 ** attempt)
            print(f"  Error for {year}: {e}")
            print(f"  Waiting {wait}s before retry...")
            time.sleep(wait)

    print(f"  FAILED after {max_retries} retries: {year}")
    return False


def check_existing_monthly_files(year: int) -> Path | None:
    """Check if all 3 monthly files exist for a year (from previous run).
    If so, merge them into a yearly file and return the path.
    """
    monthly_files = []
    for month in JJA_MONTHS:
        f = TEMP_DIR / f"era5_land_{year}_{month}.nc"
        if f.exists():
            monthly_files.append(f)
        else:
            return None

    if len(monthly_files) == 3:
        yearly_path = TEMP_DIR / f"era5_land_{year}_jja.nc"
        if yearly_path.exists():
            return yearly_path

        print(f"  Merging 3 existing monthly files for {year}...")
        try:
            import xarray as xr
            datasets = [xr.open_dataset(f) for f in monthly_files]
            merged = xr.concat(datasets, dim="time")
            merged.to_netcdf(yearly_path)
            for ds in datasets:
                ds.close()
            # Clean up monthly files
            for f in monthly_files:
                f.unlink()
            size_mb = yearly_path.stat().st_size / (1024 * 1024)
            print(f"  Merged {year} JJA: {size_mb:.1f} MB")
            return yearly_path
        except Exception as e:
            print(f"  Merge error for {year}: {e}")
            return None

    return None


def download_all(years: list[int], dry_run: bool = False) -> dict:
    """Download ERA5-Land JJA for all specified years."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(exist_ok=True)

    ledger = {
        "start_time": datetime.now().isoformat(),
        "dataset": CDS_DATASET,
        "variables": CDS_VARIABLES,
        "area": AREA,
        "event_cell": (EVENT_LAT, EVENT_LON),
        "years": years,
        "months": JJA_MONTHS,
        "status": "started",
        "completed_years": [],
        "failed_years": [],
        "total_size_mb": 0,
        "optimization": "per-year (3 months per CDS request)",
    }

    if dry_run:
        print(f"DRY RUN: Would download {len(years)} yearly requests "
              f"({len(years)} years × 3 months per request)")
        print(f"Variables: {CDS_VARIABLES}")
        print(f"Area: {AREA}")
        print(f"Output: {OUTPUT_FILE}")
        ledger["status"] = "dry_run"
        return ledger

    print("Initializing CDS client...")
    client = cdsapi.Client(quiet=True)
    print("CDS client initialized.")

    for year in years:
        year_file = TEMP_DIR / f"era5_land_{year}_jja.nc"

        if year_file.exists():
            size_mb = year_file.stat().st_size / (1024 * 1024)
            print(f"  {year}: already exists ({size_mb:.1f} MB), skipping")
            ledger["completed_years"].append({
                "year": year,
                "size_mb": round(size_mb, 1),
                "status": "skipped_existing",
            })
            ledger["total_size_mb"] += size_mb
        else:
            # Check for existing monthly files (from previous run)
            merged = check_existing_monthly_files(year)
            if merged:
                size_mb = merged.stat().st_size / (1024 * 1024)
                ledger["completed_years"].append({
                    "year": year,
                    "size_mb": round(size_mb, 1),
                    "status": "merged_from_monthly",
                })
                ledger["total_size_mb"] += size_mb
            else:
                success = download_year(client, year, year_file)
                if success:
                    size_mb = year_file.stat().st_size / (1024 * 1024)
                    ledger["completed_years"].append({
                        "year": year,
                        "size_mb": round(size_mb, 1),
                        "status": "downloaded",
                    })
                    ledger["total_size_mb"] += size_mb
                else:
                    ledger["failed_years"].append({
                        "year": year,
                        "status": "failed",
                    })

        with open(LEDGER_FILE, "w") as f:
            json.dump(ledger, f, indent=2)

    # Merge all yearly files into one
    if ledger["completed_years"]:
        print("\nMerging yearly files into single NetCDF...")
        try:
            import xarray as xr
            datasets = []
            for entry in ledger["completed_years"]:
                f = TEMP_DIR / f"era5_land_{entry['year']}_jja.nc"
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

                for ds in datasets:
                    ds.close()
                # Clean up temp files
                for f in TEMP_DIR.glob("era5_land_*.nc"):
                    f.unlink()
                try:
                    TEMP_DIR.rmdir()
                except OSError:
                    pass

        except Exception as e:
            print(f"Merge error: {e}")
            ledger["merge_error"] = str(e)

    ledger["end_time"] = datetime.now().isoformat()
    ledger["status"] = "completed" if not ledger["failed_years"] else "completed_with_failures"

    with open(LEDGER_FILE, "w") as f:
        json.dump(ledger, f, indent=2)

    print(f"\nDownload complete: {len(ledger['completed_years'])} years, "
          f"{len(ledger['failed_years'])} failed, "
          f"{ledger['total_size_mb']:.1f} MB total")
    print(f"Ledger: {LEDGER_FILE}")

    return ledger


def main():
    parser = argparse.ArgumentParser(description="Download ERA5-Land for Nepal event analysis (optimized)")
    parser.add_argument("--dry-run", action="store_true", help="Print request without downloading")
    parser.add_argument("--year-range", type=str, default="2001-2026",
                        help="Year range (e.g., 2001-2026)")
    args = parser.parse_args()

    if "-" in args.year_range:
        start, end = map(int, args.year_range.split("-"))
        years = list(range(start, end + 1))
    else:
        years = [int(args.year_range)]

    print(f"Nepal Event Anomaly — ERA5-Land Download (Optimized: per-year)")
    print(f"Years: {years[0]}-{years[-1]} ({len(years)} years)")
    print(f"Months: JJA ({JJA_MONTHS}) — all 3 per CDS request")
    print(f"Variables: {len(CDS_VARIABLES)}")
    print(f"Area: {AREA} (2° × 1° around event)")
    print(f"Event cell: ({EVENT_LAT}°N, {EVENT_LON}°E)")
    print(f"CDS requests: {len(years)} (vs {len(years) * 3} in original script)")
    print()

    ledger = download_all(years, dry_run=args.dry_run)

    if ledger["failed_years"]:
        print(f"\nWARNING: {len(ledger['failed_years'])} years failed. "
              f"Re-run to retry failed years.")
        sys.exit(1)


if __name__ == "__main__":
    main()
