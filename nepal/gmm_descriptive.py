"""Phase 4: GMM descriptive overlay + Jensen-Shannon distance.

Fits GMM K=1..5 (BIC selection) on 2001-2025 JJA daily vector.
Assigns Aug 2026 days to clusters. Uses Jensen-Shannon distance
for cluster occupancy comparison.

GMM is DESCRIPTIVE ONLY — it describes weather regimes, NOT avalanche
precursors. K=1 (null benchmark) is included per Astra's recommendation.

Usage:
    source .venv/bin/activate
    python nepal/gmm_descriptive.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from sklearn.mixture import GaussianMixture
from scipy.spatial.distance import jensenshannon

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from feature_contract import (
    EVENT, PRE_EVENT_WINDOW, EVENT_DATE,
    GMM_K_RANGE, GMM_COVARIANCE,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PLOTS_DIR = Path(__file__).resolve().parent.parent / "plots"
GMM_PLOT_DIR = PLOTS_DIR / "gmm"
RESULTS_FILE = DATA_DIR / "gmm_results.json"

ELEVATION_DISCLAIMER = (
    f"ERA5-Land model elevation: {EVENT['model_elevation_m']} m. "
    f"Source slope: {EVENT['source_elevation_m']} m. "
    f"Delta: {EVENT['elevation_gap_m']} m."
)

# Features for GMM (use daily features from Phase 2)
GMM_FEATURES = ["t2m_daily", "pdd_7day", "pdd_daily", "freezing_height_m"]


def fit_gmm_range(data: np.ndarray, k_range: tuple = GMM_K_RANGE,
                  covariance: str = GMM_COVARIANCE,
                  random_state: int = 42) -> dict:
    """Fit GMM for each K in k_range, select by BIC.

    Returns dict with BIC scores, fitted models, and best K.
    """
    results = {
        "bic_scores": {},
        "aic_scores": {},
        "models": {},
        "best_k": None,
        "best_model": None,
    }

    for k in k_range:
        if k == 1:
            # K=1 is the null benchmark (single Gaussian)
            gmm = GaussianMixture(
                n_components=1, covariance_type=covariance,
                random_state=random_state,
            )
        else:
            gmm = GaussianMixture(
                n_components=k, covariance_type=covariance,
                random_state=random_state,
                max_iter=200, n_init=3,
            )

        try:
            gmm.fit(data)
            bic = gmm.bic(data)
            aic = gmm.aic(data)
            results["bic_scores"][k] = round(bic, 2)
            results["aic_scores"][k] = round(aic, 2)
            results["models"][k] = gmm
            print(f"  K={k}: BIC={bic:.2f}, AIC={aic:.2f}")
        except Exception as e:
            print(f"  K={k}: FAILED ({e})")
            results["bic_scores"][k] = None
            results["aic_scores"][k] = None

    # Select best K by BIC (lower is better)
    valid_bics = {k: v for k, v in results["bic_scores"].items() if v is not None}
    if valid_bics:
        results["best_k"] = min(valid_bics, key=valid_bics.get)
        results["best_model"] = results["models"][results["best_k"]]
        print(f"\n  Best K (by BIC): {results['best_k']}")

    return results


def compute_cluster_occupancy(model: GaussianMixture, data: np.ndarray) -> np.ndarray:
    """Compute cluster occupancy (fraction of days in each cluster)."""
    labels = model.predict(data)
    n_clusters = model.n_components
    occupancy = np.zeros(n_clusters)
    for i in range(n_clusters):
        occupancy[i] = np.mean(labels == i)
    return occupancy


def compute_js_distance(p: np.ndarray, q: np.ndarray) -> float:
    """Compute Jensen-Shannon distance between two distributions.

    JS distance is bounded [0, 1] and avoids the infinite-KL problem
    when distributions have zero-support regions.
    """
    # Normalize to probability distributions
    p = p / np.sum(p) if np.sum(p) > 0 else p
    q = q / np.sum(q) if np.sum(q) > 0 else q

    # scipy.spatial.distance.jensenshannon returns the JS distance (sqrt of JS divergence)
    return float(jensenshannon(p, q))


def run_gmm_descriptive(daily_df: pd.DataFrame, target_year: int = 2026) -> dict:
    """Run GMM descriptive overlay on JJA data.

    1. Fit GMM K=1..5 on 2001-2025 JJA daily vector
    2. Select best K by BIC
    3. Assign Aug 2026 days to clusters
    4. Compute JS distance between 2026 occupancy and baseline occupancy
    """
    # Build feature matrix
    feature_cols = [c for c in GMM_FEATURES if c in daily_df.columns]
    feature_df = daily_df[feature_cols].dropna()

    baseline = feature_df[feature_df.index.year.isin(range(2001, target_year))]
    target = feature_df[feature_df.index.year == target_year]
    target_pre = target[target.index < pd.Timestamp(EVENT_DATE)]

    if len(baseline) < 100:
        return {"error": f"Insufficient baseline: {len(baseline)} rows"}
    if len(target_pre) < 5:
        return {"error": f"Insufficient pre-event target: {len(target_pre)} rows"}

    print(f"Baseline: {len(baseline)} rows, {len(baseline.columns)} features")
    print(f"Target (pre-event): {len(target_pre)} rows")
    print(f"Features: {feature_cols}")

    # Fit GMM range
    print(f"\nFitting GMM K={list(GMM_K_RANGE)} with {GMM_COVARIANCE} covariance...")
    gmm_results = fit_gmm_range(baseline.values)

    if gmm_results["best_model"] is None:
        return {"error": "All GMM fits failed"}

    best_k = gmm_results["best_k"]
    best_model = gmm_results["best_model"]

    # Assign Aug 2026 days to clusters
    print(f"\nAssigning Aug 2026 days to clusters (K={best_k})...")
    target_labels = best_model.predict(target_pre.values)
    target_proba = best_model.predict_proba(target_pre.values)

    # Cluster occupancy
    baseline_occupancy = compute_cluster_occupancy(best_model, baseline.values)
    target_occupancy = compute_cluster_occupancy(best_model, target_pre.values)

    # JS distance
    js_dist = compute_js_distance(baseline_occupancy, target_occupancy)

    print(f"Baseline occupancy: {baseline_occupancy}")
    print(f"Target occupancy: {target_occupancy}")
    print(f"JS distance: {js_dist:.4f}")

    # Pre-event window specifically
    pre_event = target_pre[
        (target_pre.index >= PRE_EVENT_WINDOW[0]) &
        (target_pre.index <= PRE_EVENT_WINDOW[1])
    ]
    if len(pre_event) > 0:
        pre_event_labels = best_model.predict(pre_event.values)
        pre_event_occupancy = compute_cluster_occupancy(best_model, pre_event.values)
        pre_event_js = compute_js_distance(baseline_occupancy, pre_event_occupancy)
        print(f"\nPre-event window ({PRE_EVENT_WINDOW[0]} to {PRE_EVENT_WINDOW[1]}):")
        print(f"  Occupancy: {pre_event_occupancy}")
        print(f"  JS distance: {pre_event_js:.4f}")
    else:
        pre_event_occupancy = None
        pre_event_js = None

    # Build results
    results = {
        "best_k": best_k,
        "bic_scores": gmm_results["bic_scores"],
        "aic_scores": gmm_results["aic_scores"],
        "covariance_type": GMM_COVARIANCE,
        "features_used": feature_cols,
        "baseline_occupancy": [round(float(o), 4) for o in baseline_occupancy],
        "target_occupancy": [round(float(o), 4) for o in target_occupancy],
        "js_distance_target": round(js_dist, 4),
        "pre_event_occupancy": [round(float(o), 4) for o in pre_event_occupancy] if pre_event_occupancy is not None else None,
        "js_distance_pre_event": round(pre_event_js, 4) if pre_event_js is not None else None,
        "pre_event_cluster_assignments": [
            {"date": str(d), "cluster": int(l)}
            for d, l in zip(pre_event.index, pre_event_labels)
        ] if len(pre_event) > 0 else [],
        "disclaimer": "GMM is DESCRIPTIVE ONLY. It describes weather regimes, NOT avalanche precursors.",
        "k1_null_benchmark": best_k == 1,
    }

    return results


def generate_gmm_plots(daily_df: pd.DataFrame, gmm_results: dict, output_dir: Path):
    """Generate GMM descriptive plots."""
    output_dir.mkdir(parents=True, exist_ok=True)

    if "error" in gmm_results:
        return

    # Plot 1: BIC scores
    fig, ax = plt.subplots(figsize=(10, 6))
    bics = gmm_results["bic_scores"]
    ks = sorted([k for k, v in bics.items() if v is not None])
    bic_values = [bics[k] for k in ks]
    ax.plot(ks, bic_values, "bo-")
    ax.axvline(x=gmm_results["best_k"], color="r", linestyle="--", label=f"Best K={gmm_results['best_k']}")
    ax.set_xlabel("Number of components (K)")
    ax.set_ylabel("BIC (lower is better)")
    ax.set_title("GMM BIC Scores — K=1..5 (K=1 is null benchmark)")
    ax.legend()
    fig.text(0.5, 0.01, ELEVATION_DISCLAIMER, ha="center", fontsize=8, style="italic")
    plt.tight_layout()
    fig.savefig(output_dir / "10_gmm_bic_scores.png", dpi=150)
    plt.close()

    # Plot 2: Cluster occupancy comparison
    fig, ax = plt.subplots(figsize=(10, 6))
    k = gmm_results["best_k"]
    x = range(k)
    width = 0.35
    baseline_occ = gmm_results["baseline_occupancy"]
    target_occ = gmm_results["target_occupancy"]
    ax.bar([i - width/2 for i in x], baseline_occ, width, label="2001-2025 baseline", alpha=0.7)
    ax.bar([i + width/2 for i in x], target_occ, width, label="2026 pre-event", alpha=0.7)
    ax.set_xlabel("Cluster")
    ax.set_ylabel("Occupancy fraction")
    ax.set_title(f"GMM Cluster Occupancy (K={k}) — DESCRIPTIVE ONLY")
    ax.set_xticks(x)
    ax.set_xticklabels([f"Cluster {i}" for i in x])
    ax.legend()
    fig.text(0.5, 0.01, f"{ELEVATION_DISCLAIMER}\nJS distance: {gmm_results['js_distance_target']:.4f}",
             ha="center", fontsize=8, style="italic")
    plt.tight_layout()
    fig.savefig(output_dir / "11_gmm_cluster_occupancy.png", dpi=150)
    plt.close()

    print(f"GMM plots saved to {output_dir}/")


def main():
    """Main Phase 4 GMM descriptive execution."""
    print("=" * 60)
    print("Phase 4: GMM Descriptive Overlay + Jensen-Shannon Distance")
    print("=" * 60)
    print()
    print("WARNING: GMM is DESCRIPTIVE ONLY.")
    print("It describes weather regimes, NOT avalanche precursors.")
    print("K=1 (null benchmark) is included per Astra's recommendation.")
    print()

    # Load feature matrix from Phase 2
    feature_file = DATA_DIR / "features_nepal_jja_2001_2026.csv"
    print(f"Loading features from {feature_file}...")
    daily_df = pd.read_csv(feature_file, index_col=0, parse_dates=True)
    print(f"Loaded {len(daily_df)} rows")
    print()

    # Run GMM descriptive
    gmm_results = run_gmm_descriptive(daily_df, target_year=2026)

    if "error" in gmm_results:
        print(f"ERROR: {gmm_results['error']}")
        return

    # Generate plots
    print("\nGenerating GMM plots...")
    generate_gmm_plots(daily_df, gmm_results, GMM_PLOT_DIR)

    # Save results
    with open(RESULTS_FILE, "w") as f:
        json.dump(gmm_results, f, indent=2, default=str)
    print(f"\nResults saved to {RESULTS_FILE}")

    # Summary
    print("\n" + "=" * 60)
    print("GMM Descriptive Summary")
    print("=" * 60)
    print(f"Best K (by BIC): {gmm_results['best_k']}")
    print(f"K=1 null benchmark: {'YES (single Gaussian is best)' if gmm_results['k1_null_benchmark'] else 'NO'}")
    print(f"JS distance (target vs baseline): {gmm_results['js_distance_target']}")
    if gmm_results.get("js_distance_pre_event") is not None:
        print(f"JS distance (pre-event vs baseline): {gmm_results['js_distance_pre_event']}")
    print(f"\nDISCLAIMER: {gmm_results['disclaimer']}")

    print("\n" + "=" * 60)
    print("Phase 4 GMM Descriptive EXIT GATE: PASS")
    print("=" * 60)


if __name__ == "__main__":
    main()
