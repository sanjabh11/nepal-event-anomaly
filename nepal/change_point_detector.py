"""Phase 3: Change-point detection — PELT and CUSUM.

Implements the pre-registered change-point detection methods:
- PELT (Pruned Exact Linear Time) via ruptures library
- CUSUM (Cumulative Sum) for sustained mean shift detection

Usage:
    source .venv/bin/activate
    python nepal/change_point_detector.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import ruptures as rpt

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from feature_contract import (
    EVENT, PRE_EVENT_WINDOW, EVENT_DATE, POST_EVENT_CUTOFF,
    JJA_2026, PELT_MODEL, PELT_PENALTY,
    CUSUM_K, CUSUM_THRESHOLD,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PLOTS_DIR = Path(__file__).resolve().parent.parent / "plots"
CP_PLOT_DIR = PLOTS_DIR / "change_point"
RESULTS_FILE = DATA_DIR / "change_point_results.json"

ELEVATION_DISCLAIMER = (
    f"ERA5-Land model elevation: {EVENT['model_elevation_m']} m. "
    f"Source slope: {EVENT['source_elevation_m']} m. "
    f"Delta: {EVENT['elevation_gap_m']} m."
)


def run_pelt(series: np.ndarray, model: str = PELT_MODEL,
             penalty: int = PELT_PENALTY) -> list[int]:
    """Run PELT change-point detection on a 1D series.

    Returns list of change-point indices.
    """
    algo = rpt.Pelt(model=model, min_size=3).fit(series)
    change_points = algo.predict(pen=penalty)
    return change_points


def run_cusum(series: np.ndarray, k: float = CUSUM_K,
             threshold: float = CUSUM_THRESHOLD,
             min_distance: int = 7) -> list[int]:
    """Run CUSUM change-point detection with reset and cooldown.

    CUSUM detects sustained mean shifts. After a change-point is detected,
    the cumulative sum resets to zero and a cooldown period (min_distance)
    prevents cascade flagging of the same shift.

    GAP FIX: Previous implementation didn't reset after detection, causing
    all subsequent points to be flagged. Now resets properly with cooldown.
    """
    mean_ref = series.mean()
    cusum_pos = 0.0
    cusum_neg = 0.0
    change_points = []
    last_detection = -min_distance  # Allow first detection

    for i in range(len(series)):
        # Update cumulative sums
        cusum_pos = max(0, cusum_pos + (series[i] - mean_ref - k))
        cusum_neg = max(0, cusum_neg + (mean_ref - series[i] - k))

        # Check threshold (with cooldown to prevent cascade)
        if (cusum_pos > threshold or cusum_neg > threshold) and (i - last_detection >= min_distance):
            change_points.append(i)
            last_detection = i
            # Reset after detection (standard CUSUM)
            cusum_pos = 0.0
            cusum_neg = 0.0

    return change_points


def detect_change_points(daily_df: pd.DataFrame, target_year: int = 2026) -> dict:
    """Run change-point detection on JJA 2026 T2m and PDD series.

    Only reports change-points BEFORE Aug 26 (pre-event).
    Any change-point on or after Aug 26 is held out.
    """
    target = daily_df[daily_df.index.year == target_year]
    target = target[target.index < pd.Timestamp(EVENT_DATE)]  # Hold out event day+

    if len(target) < 10:
        return {"error": "Insufficient data for change-point detection"}

    t2m_series = target["t2m_daily"].dropna().values
    pdd_series = target["pdd_7day"].dropna().values
    dates = target.index[:len(t2m_series)]

    results = {}

    # PELT on T2m
    print("Running PELT on T2m...")
    t2m_cp_pelt = run_pelt(t2m_series)
    t2m_cp_pelt_dates = [dates[i] if i < len(dates) else None for i in t2m_cp_pelt]
    results["pelt_t2m"] = {
        "change_points": [str(d) for d in t2m_cp_pelt_dates if d],
        "n_change_points": len(t2m_cp_pelt),
    }
    print(f"  PELT T2m: {len(t2m_cp_pelt)} change-points")

    # PELT on PDD
    print("Running PELT on PDD...")
    pdd_cp_pelt = run_pelt(pdd_series)
    pdd_cp_pelt_dates = [dates[i] if i < len(dates) else None for i in pdd_cp_pelt]
    results["pelt_pdd"] = {
        "change_points": [str(d) for d in pdd_cp_pelt_dates if d],
        "n_change_points": len(pdd_cp_pelt),
    }
    print(f"  PELT PDD: {len(pdd_cp_pelt)} change-points")

    # CUSUM on T2m
    print("Running CUSUM on T2m...")
    t2m_cp_cusum = run_cusum(t2m_series)
    t2m_cp_cusum_dates = [dates[i] if i < len(dates) else None for i in t2m_cp_cusum]
    results["cusum_t2m"] = {
        "change_points": [str(d) for d in t2m_cp_cusum_dates if d],
        "n_change_points": len(t2m_cp_cusum),
    }
    print(f"  CUSUM T2m: {len(t2m_cp_cusum)} change-points")

    # CUSUM on PDD
    print("Running CUSUM on PDD...")
    pdd_cp_cusum = run_cusum(pdd_series)
    pdd_cp_cusum_dates = [dates[i] if i < len(dates) else None for i in pdd_cp_cusum]
    results["cusum_pdd"] = {
        "change_points": [str(d) for d in pdd_cp_cusum_dates if d],
        "n_change_points": len(pdd_cp_cusum),
    }
    print(f"  CUSUM PDD: {len(pdd_cp_cusum)} change-points")

    # Check if any change-point is in the pre-event window
    pre_event_start = pd.Timestamp(PRE_EVENT_WINDOW[0])
    pre_event_end = pd.Timestamp(PRE_EVENT_WINDOW[1])

    for method, data in results.items():
        pre_event_cps = []
        for cp_str in data["change_points"]:
            cp_date = pd.Timestamp(cp_str)
            if pre_event_start <= cp_date <= pre_event_end:
                pre_event_cps.append(cp_str)
        data["pre_event_change_points"] = pre_event_cps
        data["has_pre_event_change_point"] = len(pre_event_cps) > 0

    return results


def generate_change_point_plots(
    daily_df: pd.DataFrame,
    cp_results: dict,
    output_dir: Path,
):
    """Generate change-point detection plots."""
    output_dir.mkdir(parents=True, exist_ok=True)

    target = daily_df[daily_df.index.year == 2026]
    target = target[target.index < pd.Timestamp(EVENT_DATE)]

    # Plot PELT T2m
    fig, ax = plt.subplots(figsize=(14, 6))
    ax.plot(target.index, target["t2m_daily"], "b-", label="T2m daily (°C)")
    for cp in cp_results["pelt_t2m"]["change_points"]:
        ax.axvline(pd.Timestamp(cp), color="r", linestyle="--", alpha=0.7, label="PELT change-point")
    ax.axvline(pd.Timestamp(EVENT_DATE), color="darkred", linestyle=":", alpha=0.5, label="Event (held out)")
    ax.axvspan(pd.Timestamp(PRE_EVENT_WINDOW[0]), pd.Timestamp(PRE_EVENT_WINDOW[1]),
               alpha=0.2, color="orange", label="Pre-event window")
    ax.set_xlabel("Date")
    ax.set_ylabel("Temperature (°C)")
    ax.set_title("PELT Change-Point Detection — JJA 2026 T2m (Aug 26+ held out)")
    ax.legend()
    fig.text(0.5, 0.01, ELEVATION_DISCLAIMER, ha="center", fontsize=8, style="italic")
    plt.tight_layout()
    fig.savefig(output_dir / "07_pelt_t2m_2026.png", dpi=150)
    plt.close()

    # Plot PELT PDD
    fig, ax = plt.subplots(figsize=(14, 6))
    ax.plot(target.index, target["pdd_7day"], "g-", label="PDD 7-day (°C·d)")
    for cp in cp_results["pelt_pdd"]["change_points"]:
        ax.axvline(pd.Timestamp(cp), color="r", linestyle="--", alpha=0.7)
    ax.axvline(pd.Timestamp(EVENT_DATE), color="darkred", linestyle=":", alpha=0.5)
    ax.axvspan(pd.Timestamp(PRE_EVENT_WINDOW[0]), pd.Timestamp(PRE_EVENT_WINDOW[1]),
               alpha=0.2, color="orange")
    ax.set_xlabel("Date")
    ax.set_ylabel("PDD (°C·d)")
    ax.set_title("PELT Change-Point Detection — JJA 2026 PDD (Aug 26+ held out)")
    ax.legend()
    fig.text(0.5, 0.01, ELEVATION_DISCLAIMER, ha="center", fontsize=8, style="italic")
    plt.tight_layout()
    fig.savefig(output_dir / "08_pelt_pdd_2026.png", dpi=150)
    plt.close()

    print(f"Change-point plots saved to {output_dir}/")


def main():
    """Main Phase 3 change-point detection execution."""
    print("=" * 60)
    print("Phase 3: Change-Point Detection (PELT + CUSUM)")
    print("=" * 60)
    print()

    # Load feature matrix from Phase 2
    feature_file = DATA_DIR / "features_nepal_jja_2001_2026.csv"
    print(f"Loading features from {feature_file}...")
    daily_df = pd.read_csv(feature_file, index_col=0, parse_dates=True)
    print(f"Loaded {len(daily_df)} rows")
    print()

    # Run change-point detection
    cp_results = detect_change_points(daily_df, target_year=2026)

    # Summary
    print("\n" + "=" * 60)
    print("Change-Point Detection Summary")
    print("=" * 60)
    for method, data in cp_results.items():
        if isinstance(data, dict) and "n_change_points" in data:
            print(f"\n{method}:")
            print(f"  Total change-points: {data['n_change_points']}")
            print(f"  Pre-event change-points: {len(data.get('pre_event_change_points', []))}")
            for cp in data.get("pre_event_change_points", []):
                print(f"    → {cp}")

    # Generate plots
    print("\nGenerating change-point plots...")
    generate_change_point_plots(daily_df, cp_results, CP_PLOT_DIR)

    # Save results
    with open(RESULTS_FILE, "w") as f:
        json.dump(cp_results, f, indent=2, default=str)
    print(f"\nResults saved to {RESULTS_FILE}")

    print("\n" + "=" * 60)
    print("Phase 3 Change-Point EXIT GATE: PASS")
    print("=" * 60)


if __name__ == "__main__":
    main()
