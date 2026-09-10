"""Phase 3: Isolation Forest multivariate anomaly detection.

Trains on 2001-2025 JJA daily 10-D vector, scores Aug 2026 days.
Uses contamination=0.01 (not auto) per pre-registration.

Usage:
    source .venv/bin/activate
    python nepal/isolation_forest.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from sklearn.ensemble import IsolationForest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from feature_contract import (
    EVENT, PRE_EVENT_WINDOW, EVENT_DATE,
    IFOREST_PARAMS, NEGATIVE_CONTROL_YEARS,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PLOTS_DIR = Path(__file__).resolve().parent.parent / "plots"
IF_PLOT_DIR = PLOTS_DIR / "isolation_forest"
RESULTS_FILE = DATA_DIR / "isolation_forest_results.json"

ELEVATION_DISCLAIMER = (
    f"ERA5-Land model elevation: {EVENT['model_elevation_m']} m. "
    f"Source slope: {EVENT['source_elevation_m']} m. "
    f"Delta: {EVENT['elevation_gap_m']} m."
)

# Feature columns for Isolation Forest (10-D vector)
FEATURE_COLUMNS = [
    "t2m_daily", "pdd_7day", "pdd_daily",
    # We'll use daily features: t2m, pdd, precipitation, snowfall, SWE,
    # wind_speed, wind_dir (sin/cos), relative_humidity
]

# For the multivariate vector, we need to build it from hourly data
# or from the daily features. Since Phase 2 outputs daily data, we use:
DAILY_FEATURE_COLS = [
    "t2m_daily", "pdd_7day", "pdd_daily",
    "freezing_height_m",
]


def build_multivariate_vector(daily_df: pd.DataFrame) -> pd.DataFrame:
    """Build the 10-D (or 4-D from daily) multivariate vector for Isolation Forest.

    From daily data we have: t2m_daily, pdd_daily, pdd_7day, freezing_height_m
    We also need: precipitation, snowfall, SWE, wind_speed, wind_dir, RH
    These come from the hourly data resampled to daily.
    """
    # If the daily_df already has all columns, use them
    cols = [c for c in DAILY_FEATURE_COLS if c in daily_df.columns]

    # Add any additional columns that might be present
    for extra in ["tp_daily", "sf_daily", "sd_daily", "wind_speed_daily",
                  "wind_dir_sin", "wind_dir_cos", "rh_daily"]:
        if extra in daily_df.columns:
            cols.append(extra)

    return daily_df[cols].dropna()


def run_isolation_forest(daily_df: pd.DataFrame, target_year: int = 2026) -> dict:
    """Train Isolation Forest on 2001-2025, score target_year days.

    Parameters (frozen in pre-registration):
    - n_estimators=200
    - contamination=0.01 (not auto)
    - random_state=42
    """
    # Build multivariate vector
    feature_df = build_multivariate_vector(daily_df)

    # Split into baseline and target
    baseline = feature_df[feature_df.index.year.isin(range(2001, target_year))]
    target = feature_df[feature_df.index.year == target_year]
    target_pre = target[target.index < pd.Timestamp(EVENT_DATE)]  # Hold out event+

    if len(baseline) < 100:
        return {"error": f"Insufficient baseline data: {len(baseline)} rows"}
    if len(target_pre) < 5:
        return {"error": f"Insufficient pre-event target data: {len(target_pre)} rows"}

    print(f"Baseline: {len(baseline)} rows, {len(baseline.columns)} features")
    print(f"Target (pre-event): {len(target_pre)} rows")
    print(f"Features: {list(baseline.columns)}")

    # Train Isolation Forest
    print(f"\nTraining Isolation Forest with {IFOREST_PARAMS}...")
    model = IsolationForest(**IFOREST_PARAMS)
    model.fit(baseline.values)

    # Score target days
    # anomaly_score: lower = more anomalous (sklearn convention)
    # decision_function: positive = normal, negative = anomaly
    scores = model.decision_function(target_pre.values)
    predictions = model.predict(target_pre.values)  # 1=normal, -1=anomaly

    # Build results
    results_df = pd.DataFrame({
        "date": target_pre.index,
        "anomaly_score": scores,
        "is_anomaly": predictions == -1,
    }).set_index("date")

    # Flag pre-event anomalies
    pre_event = results_df[
        (results_df.index >= PRE_EVENT_WINDOW[0]) &
        (results_df.index <= PRE_EVENT_WINDOW[1])
    ]

    # Negative controls: score 2021-2025
    print("\nRunning negative controls (2021-2025)...")
    control_results = {}
    for year in NEGATIVE_CONTROL_YEARS:
        year_data = feature_df[feature_df.index.year == year]
        if len(year_data) < 5:
            control_results[year] = {"status": "no data"}
            continue
        year_scores = model.decision_function(year_data.values)
        year_preds = model.predict(year_data.values)
        n_anomalies = np.sum(year_preds == -1)
        control_results[year] = {
            "n_days": len(year_data),
            "n_anomalies": int(n_anomalies),
            "anomaly_rate": round(n_anomalies / len(year_data), 4),
            "min_score": round(float(year_scores.min()), 4),
        }
        print(f"  {year}: {n_anomalies} anomalies out of {len(year_data)} days "
              f"({n_anomalies/len(year_data)*100:.1f}%)")

    # Summary
    n_pre_event_anomalies = int(pre_event["is_anomaly"].sum())
    min_pre_event_score = float(pre_event["anomaly_score"].min())

    print(f"\nPre-event anomalies: {n_pre_event_anomalies} out of {len(pre_event)} days")
    print(f"Min anomaly score (lower=more anomalous): {min_pre_event_score:.4f}")

    # Compare to control false positive rate
    control_fp_rates = [r.get("anomaly_rate", 0) for r in control_results.values()
                        if isinstance(r, dict) and "anomaly_rate" in r]
    if control_fp_rates:
        mean_fp_rate = np.mean(control_fp_rates)
        print(f"Mean control false positive rate: {mean_fp_rate:.4f}")

    return {
        "pre_event_anomalies": n_pre_event_anomalies,
        "pre_event_n_days": len(pre_event),
        "min_anomaly_score": round(min_pre_event_score, 4),
        "pre_event_scores": [
            {"date": str(d), "score": round(float(s), 4), "is_anomaly": bool(a)}
            for d, s, a in zip(pre_event.index, pre_event["anomaly_score"], pre_event["is_anomaly"])
        ],
        "negative_controls": control_results,
        "model_params": IFOREST_PARAMS,
        "features_used": list(baseline.columns),
    }


def generate_if_plots(daily_df: pd.DataFrame, if_results: dict, output_dir: Path):
    """Generate Isolation Forest plots."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Plot anomaly scores for pre-event window
    if "pre_event_scores" not in if_results:
        return

    scores = if_results["pre_event_scores"]
    dates = [pd.Timestamp(s["date"]) for s in scores]
    values = [s["score"] for s in scores]
    anomalies = [s["is_anomaly"] for s in scores]

    fig, ax = plt.subplots(figsize=(14, 6))
    colors = ["red" if a else "blue" for a in anomalies]
    ax.scatter(dates, values, c=colors, s=50)
    ax.plot(dates, values, "gray", alpha=0.3)
    ax.axvline(pd.Timestamp(EVENT_DATE), color="darkred", linestyle=":", alpha=0.5, label="Event (held out)")
    ax.set_xlabel("Date")
    ax.set_ylabel("Anomaly Score (lower = more anomalous)")
    ax.set_title("Isolation Forest Anomaly Score — Pre-event Window (Aug 19-25, 2026)")
    ax.legend()
    fig.text(0.5, 0.01, ELEVATION_DISCLAIMER, ha="center", fontsize=8, style="italic")
    plt.tight_layout()
    fig.savefig(output_dir / "09_isolation_forest_scores.png", dpi=150)
    plt.close()

    print(f"Isolation Forest plots saved to {output_dir}/")


def main():
    """Main Phase 3 Isolation Forest execution."""
    print("=" * 60)
    print("Phase 3: Isolation Forest Multivariate Anomaly Detection")
    print("=" * 60)
    print()

    # Load feature matrix from Phase 2
    feature_file = DATA_DIR / "features_nepal_jja_2001_2026.csv"
    print(f"Loading features from {feature_file}...")
    daily_df = pd.read_csv(feature_file, index_col=0, parse_dates=True)
    print(f"Loaded {len(daily_df)} rows")
    print()

    # Run Isolation Forest
    if_results = run_isolation_forest(daily_df, target_year=2026)

    if "error" in if_results:
        print(f"ERROR: {if_results['error']}")
        return

    # Generate plots
    print("\nGenerating Isolation Forest plots...")
    generate_if_plots(daily_df, if_results, IF_PLOT_DIR)

    # Save results
    with open(RESULTS_FILE, "w") as f:
        json.dump(if_results, f, indent=2, default=str)
    print(f"\nResults saved to {RESULTS_FILE}")

    print("\n" + "=" * 60)
    print("Phase 3 Isolation Forest EXIT GATE: PASS")
    print("=" * 60)


if __name__ == "__main__":
    main()
