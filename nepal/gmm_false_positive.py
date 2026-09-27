#!/usr/bin/env python3
"""GMM False-Positive Characterization (Gap 2 Fix).

Computes how often the "cluster-4 regime" (or any single-cluster dominance)
occurs in non-event JJA windows across 2001-2025. This determines whether
the GMM regime shift observed before the Langtang 2026 event is truly
unusual or happens routinely.

Outputs:
  - data/gmm_false_positive_results.json
  - plots/gmm_false_positive/
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.mixture import GaussianMixture

# -----------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
PLOT_DIR = REPO_ROOT / "plots" / "gmm_false_positive"
PLOT_DIR.mkdir(parents=True, exist_ok=True)

FEATURE_FILE = DATA_DIR / "features_nepal_jja_2001_2026.csv"

# Feature columns (must match gmm_descriptive.py)
GMM_FEATURES = [
    "t2m_daily", "d2m_daily", "tp_daily",
    "wind_speed_daily", "wind_dir_sin", "wind_dir_cos", "rh_daily",
    "pdd_7day", "pdd_daily", "freezing_height_m",
]

# Pre-event window for Langtang 2026
EVENT_DATE = pd.Timestamp("2026-08-26")
PRE_EVENT_START = pd.Timestamp("2026-08-19")
PRE_EVENT_END = pd.Timestamp("2026-08-25")


def load_features() -> pd.DataFrame:
    """Load the daily feature matrix."""
    df = pd.read_csv(FEATURE_FILE, index_col=0, parse_dates=True)

    # Keep only available features (drop all-NaN columns)
    available = [c for c in GMM_FEATURES if c in df.columns]
    subset = df[available]
    non_null = [c for c in subset.columns if not subset[c].isna().all()]
    return df[non_null]


def fit_gmm_baseline(df: pd.DataFrame, k_range=range(1, 6)) -> tuple[dict, GaussianMixture]:
    """Fit GMM K=1..5 on 2001-2025 JJA baseline, select best K by BIC."""
    baseline = df[df.index.year.isin(range(2001, 2026))]
    features = [c for c in GMM_FEATURES if c in baseline.columns]
    X = baseline[features].dropna().values

    bic_scores = {}
    models = {}
    for k in k_range:
        gmm = GaussianMixture(
            n_components=k,
            covariance_type="diag",
            random_state=42,
            max_iter=200,
        )
        gmm.fit(X)
        bic_scores[k] = float(gmm.bic(X))
        models[k] = gmm

    best_k = min(bic_scores, key=bic_scores.get)
    return {"bic_scores": bic_scores, "best_k": best_k}, models[best_k]


def compute_7day_occupancy(
    df: pd.DataFrame,
    gmm: GaussianMixture,
    features: list[str],
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
) -> np.ndarray | None:
    """Compute cluster occupancy for a 7-day window."""
    if (
        not isinstance(df.index, pd.DatetimeIndex)
        or pd.isna(start_date)
        or pd.isna(end_date)
        or start_date != start_date.normalize()
        or end_date != start_date + pd.Timedelta(days=6)
        or not features
        or not set(features).issubset(df.columns)
    ):
        return None
    window = df[(df.index >= start_date) & (df.index <= end_date)].sort_index()
    expected_dates = pd.date_range(start_date, periods=7, freq="D")
    if not window.index.equals(expected_dates):
        return None
    try:
        X = window[features].to_numpy(dtype=float)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(X).all():
        return None
    labels = gmm.predict(X)
    k = gmm.n_components
    occupancy = np.zeros(k)
    for label in labels:
        occupancy[label] += 1
    occupancy /= len(labels)
    return occupancy


def js_divergence(p: np.ndarray, q: np.ndarray) -> float:
    """Jensen-Shannon distance between two distributions."""
    from scipy.spatial.distance import jensenshannon
    return float(jensenshannon(p, q))


def run_false_positive_analysis() -> dict:
    """Run the full false-positive characterization."""
    print("=" * 60)
    print("GMM False-Positive Characterization")
    print("=" * 60)
    print()

    df = load_features()
    print(f"Loaded {len(df)} rows, {len(df.columns)} columns")
    print()

    # Fit GMM on 2001-2025 baseline
    print("Fitting GMM on 2001-2025 baseline...")
    result, best_gmm = fit_gmm_baseline(df)
    best_k = result["best_k"]
    print(f"  Best K (BIC): {best_k}")
    print(f"  BIC scores: {result['bic_scores']}")
    print()

    features = [c for c in GMM_FEATURES if c in df.columns]

    # Compute baseline occupancy (full 2001-2025)
    baseline = df[df.index.year.isin(range(2001, 2026))]
    X_base = baseline[features].dropna().values
    base_labels = best_gmm.predict(X_base)
    base_occupancy = np.bincount(base_labels, minlength=best_k) / len(base_labels)
    print(f"Baseline occupancy: {base_occupancy}")
    print()

    # Compute occupancy for EVERY 7-day window in JJA 2001-2025
    print("Computing 7-day window occupancies for all JJA 2001-2025...")
    all_js = []
    all_dates = []
    all_occupancies = []

    for year in range(2001, 2026):
        jja = df[df.index.year == year]
        if len(jja) < 7:
            continue
        # Slide a 7-day window across JJA
        for i in range(len(jja) - 6):
            start = jja.index[i]
            end = jja.index[i + 6]
            occ = compute_7day_occupancy(df, best_gmm, features, start, end)
            if occ is not None:
                js = js_divergence(occ, base_occupancy)
                all_js.append(js)
                all_dates.append(start)
                all_occupancies.append(occ)

    all_js = np.array(all_js)
    print(f"  Total 7-day windows: {len(all_js)}")
    print(f"  JS distance: mean={np.mean(all_js):.4f}, std={np.std(all_js):.4f}")
    print(f"  JS distance: 50th={np.percentile(all_js, 50):.4f}, "
          f"90th={np.percentile(all_js, 90):.4f}, "
          f"95th={np.percentile(all_js, 95):.4f}, "
          f"99th={np.percentile(all_js, 99):.4f}")
    print()

    # Compute the Langtang 2026 pre-event JS distance
    print("Computing Langtang 2026 pre-event JS distance...")
    event_occ = compute_7day_occupancy(
        df, best_gmm, features, PRE_EVENT_START, PRE_EVENT_END
    )
    if event_occ is not None:
        event_js = js_divergence(event_occ, base_occupancy)
        print(f"  Pre-event occupancy: {event_occ}")
        print(f"  Pre-event JS distance: {event_js:.4f}")
        print()

        # Where does the event JS rank?
        rank = np.sum(all_js >= event_js)
        percentile = (1 - rank / len(all_js)) * 100
        print(f"  Event JS ranks at {percentile:.1f}th percentile")
        print(f"  ({rank} of {len(all_js)} windows have JS >= event JS)")
        print()

        # False positive rate at different thresholds
        print("False positive rates at different JS thresholds:")
        for threshold in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.6168]:
            fp = np.sum(all_js >= threshold)
            fpr = fp / len(all_js)
            detected = event_js >= threshold
            print(f"  JS >= {threshold:.4f}: FPR={fpr*100:.2f}%, "
                  f"event detected={detected}")
        print()

    # Plot histogram
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.hist(all_js, bins=50, alpha=0.7, color="blue", label="All JJA 2001-2025 7-day windows")
    if event_occ is not None:
        ax.axvline(event_js, color="red", linewidth=2, linestyle="--",
                   label=f"Langtang 2026 pre-event (JS={event_js:.4f})")
    ax.axvline(np.percentile(all_js, 95), color="orange", linestyle=":",
               label=f"95th percentile ({np.percentile(all_js, 95):.4f})")
    ax.axvline(np.percentile(all_js, 99), color="purple", linestyle=":",
               label=f"99th percentile ({np.percentile(all_js, 99):.4f})")
    ax.set_xlabel("Jensen-Shannon Distance")
    ax.set_ylabel("Count")
    ax.set_title("GMM Regime Shift JS Distance Distribution (JJA 2001-2025)")
    ax.legend()
    plt.tight_layout()
    plt.savefig(PLOT_DIR / "gmm_false_positive_histogram.png", dpi=150)
    plt.close()
    print(f"Plot saved to {PLOT_DIR / 'gmm_false_positive_histogram.png'}")

    # Save results
    results = {
        "best_k": best_k,
        "bic_scores": result["bic_scores"],
        "baseline_occupancy": base_occupancy.tolist(),
        "n_windows": len(all_js),
        "js_mean": float(np.mean(all_js)),
        "js_std": float(np.std(all_js)),
        "js_percentiles": {
            "50": float(np.percentile(all_js, 50)),
            "90": float(np.percentile(all_js, 90)),
            "95": float(np.percentile(all_js, 95)),
            "99": float(np.percentile(all_js, 99)),
        },
        "event_js": float(event_js) if event_occ is not None else None,
        "event_percentile": float(percentile) if event_occ is not None else None,
        "event_rank": int(rank) if event_occ is not None else None,
        "false_positive_rates": {},
    }
    for threshold in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.6168]:
        fp = int(np.sum(all_js >= threshold))
        results["false_positive_rates"][str(threshold)] = {
            "threshold": threshold,
            "false_positives": fp,
            "fpr": float(fp / len(all_js)),
            "event_detected": bool(event_js >= threshold) if event_occ is not None else None,
        }

    out_file = DATA_DIR / "gmm_false_positive_results.json"
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {out_file}")

    return results


if __name__ == "__main__":
    run_false_positive_analysis()
