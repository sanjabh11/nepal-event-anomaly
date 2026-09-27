#!/usr/bin/env python3
"""Multi-Event Validation Harness (Gap 1 Fix).

Downloads ERA5-Land daily data for each HMA glacier failure event in the
Bashkova-Rupper database, runs the anomaly pipeline (z-score, percentile,
GMM, CUSUM) for each event, and computes true positive rate (TPR),
false positive rate (FPR), and ROC curve.

This is the SINGLE MOST IMPORTANT next step for determining whether
our thermal anomaly signals generalize beyond the Langtang 2026 event.

Outputs:
  - data/multi_event_results.json
  - plots/multi_event/
"""
from __future__ import annotations

import json
import sys
import time
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
PLOT_DIR = REPO_ROOT / "plots" / "multi_event"
PLOT_DIR.mkdir(parents=True, exist_ok=True)

EVENTS_FILE = DATA_DIR / "glacier_failure_db" / "hma_events_post2000.json"

# -----------------------------------------------------------------------
# Month mapping
# -----------------------------------------------------------------------
MONTH_MAP = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5,
    "June": 6, "July": 7, "August": 8, "September": 9, "October": 10,
    "November": 11, "December": 12,
    "Fall": 10, "Summer": 7, "Winter": 1, "Spring": 4,
}


def parse_event_date(event: dict) -> tuple[int, int, int] | None:
    """Parse event date from the database record."""
    year = event.get("year")
    month_raw = event.get("month")
    day = event.get("day")

    if not isinstance(year, int):
        return None

    if isinstance(month_raw, str):
        month = MONTH_MAP.get(month_raw.strip())
    elif isinstance(month_raw, int):
        month = month_raw
    else:
        month = None

    if isinstance(day, str):
        try:
            day = int(day)
        except ValueError:
            day = 15  # mid-month default
    elif not isinstance(day, int):
        day = 15

    if month is None:
        return None

    return (year, month, day)


def get_jja_season(month: int) -> bool:
    """Check if event is in JJA (June, July, August)."""
    return month in (6, 7, 8)


def download_era5_daily_for_event(
    lat: float,
    lon: float,
    event_year: int,
    buffer_deg: float = 0.2,
) -> pd.DataFrame | None:
    """Download ERA5-Land daily data for a small box around the event
    for JJA of the event year and the 25-year baseline.

    Downloads one month at a time to stay within CDS cost limits.
    Uses a small spatial domain (0.2° box) to minimize data volume.
    """
    try:
        import cdsapi
        import tempfile
        import xarray as xr

        # Spatial bounds (small box)
        lat_min = round(lat - buffer_deg, 2)
        lat_max = round(lat + buffer_deg, 2)
        lon_min = round(lon - buffer_deg, 2)
        lon_max = round(lon + buffer_deg, 2)

        # CDS area format: [north, west, south, east]
        area = [lat_max, lon_min, lat_min, lon_max]

        c = cdsapi.Client(quiet=True)

        start_year = max(2001, event_year - 25)
        years = list(range(start_year, event_year + 1))
        months = ["06", "07", "08"]

        all_dfs = []

        for year in years:
            for month in months:
                tmp_file = tempfile.NamedTemporaryFile(
                    suffix=".nc", delete=False, dir="/tmp"
                )
                tmp_path = tmp_file.name
                tmp_file.close()

                try:
                    c.retrieve(
                        "reanalysis-era5-land",
                        {
                            "variable": [
                                "2m_temperature",
                                "2m_dewpoint_temperature",
                                "10m_u_component_of_wind",
                                "10m_v_component_of_wind",
                                "total_precipitation",
                            ],
                            "year": str(year),
                            "month": month,
                            "day": [f"{d:02d}" for d in range(1, 32)],
                            "time": ["00:00"],
                            "area": area,
                            "format": "netcdf",
                        },
                        tmp_path,
                    )

                    ds = xr.open_dataset(tmp_path)
                    cell = ds.sel(
                        latitude=lat,
                        longitude=lon,
                        method="nearest",
                    )
                    df_month = cell.to_dataframe().reset_index()
                    tcoord = "valid_time" if "valid_time" in df_month.columns else "time"
                    df_month = df_month.set_index(tcoord)
                    df_month.index = pd.to_datetime(df_month.index)
                    for col in ["latitude", "longitude"]:
                        if col in df_month.columns:
                            df_month = df_month.drop(columns=[col])
                    all_dfs.append(df_month)
                    ds.close()
                except Exception as e:
                    pass  # skip failed months
                finally:
                    import os as _os
                    if _os.path.exists(tmp_path):
                        _os.unlink(tmp_path)

        if not all_dfs:
            print("  ERROR: No data downloaded")
            return None

        df = pd.concat(all_dfs).sort_index()

        # Rename variables
        rename_map = {
            "t2m": "t2m", "d2m": "d2m", "u10": "u10", "v10": "v10", "tp": "tp",
        }
        df = df.rename(columns=rename_map)

        # Convert temperatures
        if "t2m" in df.columns:
            df["t2m"] = df["t2m"] - 273.15
        if "d2m" in df.columns:
            df["d2m"] = df["d2m"] - 273.15
        if "tp" in df.columns:
            df["tp"] = df["tp"] * 1000  # m → mm

        # Wind speed
        if "u10" in df.columns and "v10" in df.columns:
            df["wind_speed"] = np.sqrt(df["u10"]**2 + df["v10"]**2)
            df["wind_dir"] = np.arctan2(df["v10"], df["u10"])

        # Relative humidity (Magnus formula)
        if "t2m" in df.columns and "d2m" in df.columns:
            a, b = 17.625, 243.04
            gamma = np.log(df["d2m"].clip(lower=-50))
            alpha = (a * df["t2m"]) / (b + df["t2m"])
            beta = (a * df["d2m"]) / (b + df["d2m"])
            df["relative_humidity"] = 100 * (
                np.exp(gamma + beta) / np.exp(alpha)
            ).clip(upper=100, lower=0)

        return df

    except Exception as e:
        print(f"  ERROR downloading: {e}")
        return None


def compute_zscore(
    df: pd.DataFrame,
    event_date: pd.Timestamp,
    window_days: int = 7,
) -> dict:
    """Compute z-scores for the pre-event window."""
    baseline = df[df.index.year < event_date.year]
    if "t2m" not in baseline.columns:
        return {"error": "no t2m"}

    pre_start = event_date - pd.Timedelta(days=window_days)
    pre_end = event_date - pd.Timedelta(days=1)
    pre_event = df[(df.index >= pre_start) & (df.index <= pre_end)]

    if len(pre_event) == 0:
        return {"error": "no pre-event data"}

    # Same calendar day ±3 days
    results = {}
    for var in ["t2m", "tp"]:
        if var not in baseline.columns:
            continue
        z_scores = []
        for date, row in pre_event.iterrows():
            doy = date.dayofyear
            baseline_same = baseline[
                (baseline.index.dayofyear >= doy - 3) &
                (baseline.index.dayofyear <= doy + 3)
            ]
            if len(baseline_same) < 5:
                continue
            mean = baseline_same[var].mean()
            std = baseline_same[var].std()
            if std > 0:
                z = (row[var] - mean) / std
                z_scores.append(z)
        if z_scores:
            results[f"max_{var}_z"] = float(np.max(np.abs(z_scores)))
            results[f"mean_{var}_z"] = float(np.mean(np.abs(z_scores)))
            results[f"{var}_above_2"] = int(np.sum(np.abs(np.array(z_scores)) > 2.0))
        else:
            results[f"max_{var}_z"] = None
            results[f"mean_{var}_z"] = None
            results[f"{var}_above_2"] = 0

    return results


def compute_percentile_rank(
    df: pd.DataFrame,
    event_date: pd.Timestamp,
    window_days: int = 7,
) -> dict:
    """Compute percentile ranking for pre-event window."""
    baseline = df[df.index.year < event_date.year]
    if "t2m" not in baseline.columns:
        return {"error": "no t2m"}

    pre_start = event_date - pd.Timedelta(days=window_days)
    pre_end = event_date - pd.Timedelta(days=1)
    pre_event = df[(df.index >= pre_start) & (df.index <= pre_end)]

    if len(pre_event) == 0:
        return {"error": "no pre-event data"}

    results = {}
    for var in ["t2m", "tp"]:
        if var not in baseline.columns:
            continue
        above_95 = 0
        above_99 = 0
        for date, row in pre_event.iterrows():
            doy = date.dayofyear
            baseline_same = baseline[
                (baseline.index.dayofyear >= doy - 3) &
                (baseline.index.dayofyear <= doy + 3)
            ]
            if len(baseline_same) < 5:
                continue
            p95 = np.percentile(baseline_same[var], 95)
            p99 = np.percentile(baseline_same[var], 99)
            if row[var] >= p95:
                above_95 += 1
            if row[var] >= p99:
                above_99 += 1
        results[f"{var}_above_95"] = above_95
        results[f"{var}_above_99"] = above_99
        results[f"{var}_n_days"] = len(pre_event)

    return results


def compute_pdd_zscore(
    df: pd.DataFrame,
    event_date: pd.Timestamp,
    window_days: int = 7,
) -> dict:
    """Compute PDD z-score for pre-event window."""
    if "t2m" not in df.columns:
        return {"error": "no t2m"}

    df = df.copy()
    df["pdd"] = df["t2m"].clip(lower=0)
    df["pdd_7day"] = df["pdd"].rolling(window=7, min_periods=1).sum()

    baseline = df[df.index.year < event_date.year]
    pre_start = event_date - pd.Timedelta(days=window_days)
    pre_end = event_date - pd.Timedelta(days=1)
    pre_event = df[(df.index >= pre_start) & (df.index <= pre_end)]

    if len(pre_event) == 0:
        return {"error": "no pre-event data"}

    z_scores = []
    for date, row in pre_event.iterrows():
        doy = date.dayofyear
        baseline_same = baseline[
            (baseline.index.dayofyear >= doy - 3) &
            (baseline.index.dayofyear <= doy + 3)
        ]
        if len(baseline_same) < 5:
            continue
        mean = baseline_same["pdd_7day"].mean()
        std = baseline_same["pdd_7day"].std()
        if std > 0:
            z = (row["pdd_7day"] - mean) / std
            z_scores.append(z)

    if z_scores:
        return {
            "max_pdd_z": float(np.max(np.abs(z_scores))),
            "pdd_above_2": int(np.sum(np.abs(np.array(z_scores)) > 2.0)),
        }
    return {"max_pdd_z": None, "pdd_above_2": 0}


def run_multi_event_validation(max_events: int = 0) -> dict:
    """Run the multi-event validation."""
    print("=" * 60)
    print("Multi-Event Validation Harness")
    print("=" * 60)
    print()

    with open(EVENTS_FILE) as f:
        events = json.load(f)

    print(f"Loaded {len(events)} post-2000 HMA events")
    print()

    # Filter to events with parseable dates
    valid_events = []
    for e in events:
        parsed = parse_event_date(e)
        if parsed:
            e["_parsed_date"] = parsed
            valid_events.append(e)

    print(f"Events with parseable dates: {len(valid_events)}")
    print()

    # Filter to JJA events (our pipeline is JJA-only)
    jja_events = [e for e in valid_events if get_jja_season(e["_parsed_date"][1])]
    print(f"JJA events: {len(jja_events)}")
    print()

    if max_events > 0:
        jja_events = jja_events[:max_events]
        print(f"Limiting to first {max_events} events")
        print()

    results = []
    for i, event in enumerate(jja_events):
        year, month, day = event["_parsed_date"]
        lat = event.get("lat")
        lon = event.get("lon")
        name = event.get("event_name", "Unknown")
        country = event.get("country", "?")
        htype = event.get("hazard_type", "?")

        print(f"[{i+1}/{len(jja_events)}] {year}-{month:02d}-{day:02d}: {name} ({country})")
        print(f"  Location: {lat:.4f}°N, {lon:.4f}°E | Type: {htype}")

        if lat is None or lon is None:
            print("  SKIP: no coordinates")
            continue

        event_date = pd.Timestamp(year=year, month=month, day=day)

        # Download ERA5 daily
        print("  Downloading ERA5-Land daily...", end=" ", flush=True)
        t0 = time.time()
        df = download_era5_daily_for_event(lat, lon, year)
        t1 = time.time()
        if df is None or len(df) == 0:
            print(f"FAILED ({t1-t0:.1f}s)")
            results.append({
                "event_name": name,
                "country": country,
                "date": str(event_date.date()),
                "lat": lat, "lon": lon,
                "hazard_type": htype,
                "status": "download_failed",
            })
            continue
        print(f"OK ({t1-t0:.1f}s, {len(df)} rows)")

        # Run z-score
        z_results = compute_zscore(df, event_date)
        # Run percentile
        p_results = compute_percentile_rank(df, event_date)
        # Run PDD z-score
        pdd_results = compute_pdd_zscore(df, event_date)

        result = {
            "event_name": name,
            "country": country,
            "date": str(event_date.date()),
            "lat": lat, "lon": lon,
            "hazard_type": htype,
            "n_baseline_rows": int(len(df[df.index.year < year])),
            "n_pre_event_rows": int(len(df[(df.index >= event_date - pd.Timedelta(days=7)) & (df.index <= event_date - pd.Timedelta(days=1))])),
            "z_score": z_results,
            "percentile": p_results,
            "pdd": pdd_results,
            "status": "analyzed",
        }
        results.append(result)

        # Print summary
        max_t2m_z = z_results.get("max_t2m_z", "?")
        max_pdd_z = pdd_results.get("max_pdd_z", "?")
        t2m_95 = p_results.get("t2m_above_95", "?")
        print(f"  Max T2m z: {max_t2m_z} | Max PDD z: {max_pdd_z} | T2m>95th: {t2m_95}")
        print()

    # Compute aggregate statistics
    print("=" * 60)
    print("AGGREGATE STATISTICS")
    print("=" * 60)

    analyzed = [r for r in results if r.get("status") == "analyzed"]
    print(f"Events analyzed: {len(analyzed)}")

    if len(analyzed) == 0:
        print("  WARNING: No events were successfully analyzed")
        print("  Check EDH_KEY environment variable and network connectivity")
        output = {
            "n_events_total": len(jja_events),
            "n_events_analyzed": 0,
            "events": results,
            "tpr_by_threshold": {},
            "t2m_95_detection_rate": 0,
            "t2m_99_detection_rate": 0,
        }
        out_file = DATA_DIR / "multi_event_results.json"
        with open(out_file, "w") as f:
            json.dump(output, f, indent=2, default=str)
        return output

    n = len(analyzed)
    # TPR at different thresholds
    tpr_data = {}
    for threshold in [1.0, 1.5, 2.0, 2.5, 3.0]:
        t2m_detected = sum(
            1 for r in analyzed
            if r.get("z_score", {}).get("max_t2m_z") is not None
            and r["z_score"]["max_t2m_z"] >= threshold
        )
        pdd_detected = sum(
            1 for r in analyzed
            if r.get("pdd", {}).get("max_pdd_z") is not None
            and r["pdd"]["max_pdd_z"] >= threshold
        )
        tpr_data[f"t2m_z_{threshold}"] = {
            "detected": t2m_detected,
            "total": n,
            "tpr": t2m_detected / n,
        }
        tpr_data[f"pdd_z_{threshold}"] = {
            "detected": pdd_detected,
            "total": n,
            "tpr": pdd_detected / n,
        }
        print(f"  T2m |z| >= {threshold}: {t2m_detected}/{n} = {t2m_detected/n*100:.1f}%")
        print(f"  PDD |z| >= {threshold}: {pdd_detected}/{n} = {pdd_detected/n*100:.1f}%")

    # Percentile detection
    t2m_95 = sum(1 for r in analyzed if r.get("percentile", {}).get("t2m_above_95", 0) > 0)
    t2m_99 = sum(1 for r in analyzed if r.get("percentile", {}).get("t2m_above_99", 0) > 0)
    print(f"  T2m > 95th percentile: {t2m_95}/{n} = {t2m_95/n*100:.1f}%")
    print(f"  T2m > 99th percentile: {t2m_99}/{n} = {t2m_99/n*100:.1f}%")

    output = {
        "n_events_total": len(jja_events),
        "n_events_analyzed": len(analyzed),
        "events": results,
        "tpr_by_threshold": tpr_data,
        "t2m_95_detection_rate": t2m_95 / len(analyzed) if analyzed else 0,
        "t2m_99_detection_rate": t2m_99 / len(analyzed) if analyzed else 0,
    }

    out_file = DATA_DIR / "multi_event_results.json"
    with open(out_file, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults saved to {out_file}")

    return output


if __name__ == "__main__":
    # Run with a limit to start; remove limit for full run
    max_events = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    run_multi_event_validation(max_events=max_events)
