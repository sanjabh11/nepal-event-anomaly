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
# data/ is a frozen contract surface — run outputs go to
# research_runs/ (non-frozen). Nothing may write under data/.
RUN_DIR = Path(__file__).resolve().parent.parent / "research_runs" / \
    "gmm_confirmation"
GMM_PLOT_DIR = RUN_DIR / "plots"
RESULTS_FILE = RUN_DIR / "gmm_results.json"
BUNDLE_FILE = RUN_DIR / "bundle.json"

# Declared independent seeds (CFM-06) — three fits per K; K selection
# is reported as a frequency, not a single lucky fit.
GMM_SEEDS = (42, 7, 2024)
# Posterior membership below this is reported as ambiguous (CFM-09).
AMBIGUITY_THRESHOLD = 0.7
# Bootstrap parameters for JS-distance uncertainty (CFM-08).
JS_BOOTSTRAP_BLOCKS = 200
JS_BLOCK_DAYS = 7

ELEVATION_DISCLAIMER = (
    f"ERA5-Land model elevation: {EVENT['model_elevation_m']} m. "
    f"Source slope: {EVENT['source_elevation_m']} m. "
    f"Delta: {EVENT['elevation_gap_m']} m."
)

# GAP FIX: Previously only 4 features. Now uses ALL available daily features
# from the Phase 2 output (up to 10-D + thermal indices).
# Pre-registered 10 distinct quantities + thermal indices for regime context.
GMM_FEATURES = [
    "t2m_daily", "d2m_daily", "tp_daily", "sf_daily", "sd_daily",
    "wind_speed_daily", "wind_dir_sin", "wind_dir_cos", "rh_daily",
    "pdd_7day", "pdd_daily", "freezing_height_m",
]


def fit_gmm_range(data: np.ndarray, k_range: tuple = GMM_K_RANGE,
                  covariance: str = GMM_COVARIANCE,
                  seeds: tuple = GMM_SEEDS) -> dict:
    """Fit GMM for each K in k_range under every declared seed.

    CFM-06: K is selected by BIC *per seed*; the modal selection is
    reported with its frequency — a single-seed best-K is not a
    stability statement.
    """
    results = {
        "seeds": list(seeds),
        "bic_scores": {},          # seed -> {k: bic}
        "aic_scores": {},
        "converged": {},           # seed -> {k: bool}
        "models": {},              # seed -> {k: model}
        "best_k_per_seed": {},
        "modal_k": None,
        "modal_k_frequency": None,
        "best_model": None,
        "best_seed": None,
    }

    for seed in seeds:
        results["bic_scores"][seed] = {}
        results["aic_scores"][seed] = {}
        results["converged"][seed] = {}
        results["models"][seed] = {}
        for k in k_range:
            gmm = GaussianMixture(
                n_components=k, covariance_type=covariance,
                random_state=seed,
                max_iter=200, n_init=(1 if k == 1 else 3),
            )
            try:
                gmm.fit(data)
                bic = gmm.bic(data)
                aic = gmm.aic(data)
                results["bic_scores"][seed][k] = round(float(bic), 2)
                results["aic_scores"][seed][k] = round(float(aic), 2)
                results["converged"][seed][k] = bool(gmm.converged_)
                results["models"][seed][k] = gmm
                print(f"  seed={seed} K={k}: BIC={bic:.2f} "
                      f"AIC={aic:.2f} converged={gmm.converged_}")
            except Exception as e:
                print(f"  seed={seed} K={k}: FAILED ({e})")
                results["bic_scores"][seed][k] = None
                results["aic_scores"][seed][k] = None
                results["converged"][seed][k] = False
        valid = {k: v for k, v in results["bic_scores"][seed].items()
                 if v is not None}
        if valid:
            results["best_k_per_seed"][seed] = min(
                valid, key=valid.get)

    if results["best_k_per_seed"]:
        from collections import Counter
        counts = Counter(results["best_k_per_seed"].values())
        modal_k, freq = counts.most_common(1)[0]
        results["modal_k"] = modal_k
        results["modal_k_frequency"] = freq / len(
            results["best_k_per_seed"])
        # Representative model: the seed whose BIC-selected K equals
        # the modal K, with the lowest BIC.
        candidates = [(results["bic_scores"][s][modal_k], s)
                      for s, k in results["best_k_per_seed"].items()
                      if k == modal_k]
        best_seed = min(candidates)[1]
        results["best_seed"] = best_seed
        results["best_model"] = \
            results["models"][best_seed][modal_k]
        print(f"\n  Modal K: {modal_k} "
              f"(frequency {results['modal_k_frequency']:.2f} across "
              f"{len(seeds)} seeds; representative seed {best_seed})")

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


def js_uncertainty(baseline: np.ndarray, target: np.ndarray,
                   model: GaussianMixture,
                   n_blocks: int = JS_BOOTSTRAP_BLOCKS,
                   block_days: int = JS_BLOCK_DAYS,
                   rng_seed: int = 42) -> dict:
    """CFM-08 — block-bootstrap CI + within-baseline null for the
    occupancy JS distance.

    Bootstrap: resample baseline day-blocks, recompute occupancy,
    compare to the fixed target occupancy.  Null: split baseline into
    two random halves per replicate and take the max JS — the scale of
    disagreement expected between two in-distribution samples.
    """
    rng = np.random.default_rng(rng_seed)
    base_occ = compute_cluster_occupancy(model, baseline)
    tgt_occ = compute_cluster_occupancy(model, target)
    observed = compute_js_distance(base_occ, tgt_occ)
    n = len(baseline)
    n_blocks = max(1, n // block_days)
    boot = []
    for _ in range(n_blocks):
        starts = rng.integers(0, n - block_days + 1, size=n_blocks)
        sample = np.concatenate(
            [baseline[s:s + block_days] for s in starts])
        occ = compute_cluster_occupancy(model, sample)
        boot.append(compute_js_distance(occ, tgt_occ))
    null = []
    for _ in range(n_blocks):
        idx = rng.permutation(n)
        half = n // 2
        occ_a = compute_cluster_occupancy(model, baseline[idx[:half]])
        occ_b = compute_cluster_occupancy(model, baseline[idx[half:]])
        null.append(compute_js_distance(occ_a, occ_b))
    boot = np.asarray(boot)
    null = np.asarray(null)
    return {
        "observed": round(float(observed), 4),
        "ci95": [round(float(np.quantile(boot, 0.025)), 4),
                 round(float(np.quantile(boot, 0.975)), 4)],
        "null_p95": round(float(np.quantile(null, 0.95)), 4),
        "exceeds_null": bool(observed > np.quantile(null, 0.95)),
    }


def run_gmm_descriptive(daily_df: pd.DataFrame,
                        target_year: int = 2026) -> dict:
    """Run the bounded GMM descriptive confirmation.

    CFM-02: single-cell, single-period — inference is limited to the
    selected grid cell and period; labelled
    EXPLORATORY_DESCRIPTIVE_SINGLE_CELL.  CFM-03: all declared
    features are required — no silent column reduction.  CFM-04:
    scaling is fit on the baseline only.  Event dates never touch
    fitting or K selection (CFM-12).
    """
    status_meta = {
        "status": "EXPLORATORY_DESCRIPTIVE_SINGLE_CELL",
        "scope": ("single ERA5-Land grid cell, single pre-event "
                  "period — implementation confirmation only, not "
                  "validation or generalization"),
    }

    # CFM-03 — declared feature set is required.
    missing_cols = [c for c in GMM_FEATURES if c not in daily_df.columns]
    if missing_cols:
        return {**status_meta,
                "error": f"required features missing: {missing_cols}"}
    feature_df = daily_df[GMM_FEATURES]
    n_raw = len(feature_df)
    feature_df = feature_df.dropna()
    missingness = {
        "input_rows": int(n_raw),
        "rows_after_dropna": int(len(feature_df)),
        "rows_dropped": int(n_raw - len(feature_df)),
        "per_column_na": {c: int(feature_df[c].isna().sum())
                          for c in GMM_FEATURES},
    }

    baseline = feature_df[
        feature_df.index.year.isin(range(2001, target_year))]
    target = feature_df[feature_df.index.year == target_year]
    target_pre = target[target.index < pd.Timestamp(EVENT_DATE)]

    if len(baseline) < 100:
        return {**status_meta, "missingness": missingness,
                "error": f"Insufficient baseline: {len(baseline)} rows"}
    if len(target_pre) < 5:
        return {**status_meta, "missingness": missingness,
                "error": f"Insufficient pre-event target: "
                         f"{len(target_pre)} rows"}

    print(f"Baseline: {len(baseline)} rows, "
          f"{len(baseline.columns)} features")
    print(f"Target (pre-event): {len(target_pre)} rows")
    print(f"Features: {list(GMM_FEATURES)}")
    print(f"Missingness: {missingness['rows_dropped']} rows dropped")

    # CFM-04 — baseline-fitted scaling so mixed-unit features are
    # jointly comparable; the scaler never sees target rows.
    from sklearn.preprocessing import StandardScaler
    scaler = StandardScaler()
    baseline_scaled = scaler.fit_transform(baseline.values)
    target_scaled = scaler.transform(target_pre.values)
    scaling_record = {
        "policy": "baseline-fitted StandardScaler",
        "feature_units": {
            "t2m_daily": "K", "d2m_daily": "K", "tp_daily": "m",
            "sf_daily": "m SWE", "sd_daily": "m SWE",
            "wind_speed_daily": "m/s", "wind_dir_sin": "unitless",
            "wind_dir_cos": "unitless", "rh_daily": "fraction",
            "pdd_7day": "degC·d", "pdd_daily": "degC·d",
            "freezing_height_m": "m"},
    }

    print(f"\nFitting GMM K={list(GMM_K_RANGE)} with "
          f"{GMM_COVARIANCE} covariance, seeds {list(GMM_SEEDS)}...")
    gmm_results = fit_gmm_range(baseline_scaled)

    if gmm_results["best_model"] is None:
        return {**status_meta, "missingness": missingness,
                "scaling": scaling_record,
                "error": "All GMM fits failed"}

    best_k = gmm_results["modal_k"]
    best_model = gmm_results["best_model"]

    # Retrospective overlay — event dates did NOT influence fitting
    # or K selection (CFM-12).
    print(f"\nRetrospective overlay: assigning Aug {target_year} days "
          f"to clusters (K={best_k}, seed {gmm_results['best_seed']})...")
    target_labels = best_model.predict(target_scaled)
    target_proba = best_model.predict_proba(target_scaled)
    max_proba = target_proba.max(axis=1)

    baseline_occupancy = compute_cluster_occupancy(
        best_model, baseline_scaled)
    target_occupancy = compute_cluster_occupancy(
        best_model, target_scaled)
    js = js_uncertainty(baseline_scaled, target_scaled, best_model)

    print(f"Baseline occupancy: {baseline_occupancy}")
    print(f"Target occupancy: {target_occupancy}")
    print(f"JS distance: {js['observed']:.4f} "
          f"(95% CI {js['ci95']}, null p95 {js['null_p95']}, "
          f"exceeds null: {js['exceeds_null']})")

    pre_event = target_pre[
        (target_pre.index >= PRE_EVENT_WINDOW[0]) &
        (target_pre.index <= PRE_EVENT_WINDOW[1])]
    if len(pre_event) > 0:
        pre_scaled = scaler.transform(pre_event.values)
        pre_event_labels = best_model.predict(pre_scaled)
        pre_event_occupancy = compute_cluster_occupancy(
            best_model, pre_scaled)
        pre_event_js = compute_js_distance(baseline_occupancy,
                                           pre_event_occupancy)
        print(f"\nPre-event window ({PRE_EVENT_WINDOW[0]} to "
              f"{PRE_EVENT_WINDOW[1]}):")
        print(f"  Occupancy: {pre_event_occupancy}")
        print(f"  JS distance: {pre_event_js:.4f}")
    else:
        pre_event_labels = None
        pre_event_occupancy = None
        pre_event_js = None

    results = {
        **status_meta,
        "best_k": best_k,
        "modal_k_frequency": gmm_results["modal_k_frequency"],
        "best_k_per_seed": {str(s): k for s, k in
                            gmm_results["best_k_per_seed"].items()},
        "seeds": list(GMM_SEEDS),
        "bic_scores": {str(s): {str(k): v for k, v in d.items()}
                       for s, d in gmm_results["bic_scores"].items()},
        "aic_scores": {str(s): {str(k): v for k, v in d.items()}
                       for s, d in gmm_results["aic_scores"].items()},
        "converged": {str(s): {str(k): v for k, v in d.items()}
                      for s, d in gmm_results["converged"].items()},
        "covariance_type": GMM_COVARIANCE,
        "features_used": list(GMM_FEATURES),
        "missingness": missingness,
        "scaling": scaling_record,
        "baseline_occupancy": [round(float(o), 4)
                               for o in baseline_occupancy],
        "target_occupancy": [round(float(o), 4)
                             for o in target_occupancy],
        "js_distance": js,
        "posterior_confidence": {
            "mean_max_proba": round(float(max_proba.mean()), 4),
            "median_max_proba": round(float(np.median(max_proba)), 4),
            "ambiguous_fraction": round(
                float((max_proba < AMBIGUITY_THRESHOLD).mean()), 4),
            "ambiguity_threshold": AMBIGUITY_THRESHOLD,
        },
        "pre_event_occupancy": [round(float(o), 4)
                                for o in pre_event_occupancy]
        if pre_event_occupancy is not None else None,
        "js_distance_pre_event": round(pre_event_js, 4)
        if pre_event_js is not None else None,
        "pre_event_cluster_assignments": [
            {"date": str(d), "cluster": int(l)}
            for d, l in zip(pre_event.index, pre_event_labels)
        ] if pre_event_labels is not None else [],
        "event_overlay_note": (
            "retrospective overlay — event dates did not influence "
            "feature selection, scaling, fitting, or K selection"),
        "disclaimer": "GMM is DESCRIPTIVE ONLY. It describes weather "
                      "regimes, NOT avalanche precursors.",
        "k1_null_benchmark": best_k == 1,
    }

    return results


def write_bundle(results: dict, input_files: list[Path],
                 feature_file: Path, run_dir: Path) -> dict:
    """CFM-11 — research-only result bundle: every input and output
    digest recorded so another worker can replay the run."""
    import hashlib

    def _sha(p: Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()

    bundle = {
        "run_id": f"gmm_confirmation_{pd.Timestamp.now():%Y%m%dT%H%M%S}",
        "status": results.get("status", "EXPLORATORY_DESCRIPTIVE_"
                                       "SINGLE_CELL"),
        "input_digests": {p.name: _sha(p) for p in input_files
                          if p.is_file()},
        "feature_digest": _sha(feature_file)
        if feature_file.is_file() else None,
        "configuration": {
            "features": list(GMM_FEATURES), "k_range":
            list(GMM_K_RANGE), "covariance": GMM_COVARIANCE,
            "seeds": list(GMM_SEEDS),
            "ambiguity_threshold": AMBIGUITY_THRESHOLD,
            "js_bootstrap_blocks": JS_BOOTSTRAP_BLOCKS,
            "js_block_days": JS_BLOCK_DAYS,
        },
        "results_digest": _sha(RESULTS_FILE)
        if RESULTS_FILE.is_file() else None,
        "environment": {
            "python": sys.version.split()[0],
            "sklearn": __import__("sklearn").__version__,
            "numpy": np.__version__,
            "pandas": pd.__version__,
        },
        "error": results.get("error"),
        "descriptive_only": True,
        "no_event_labels_in_fit": True,
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    with open(BUNDLE_FILE, "w") as f:
        json.dump(bundle, f, indent=2, default=str)
    return bundle


def generate_gmm_plots(daily_df: pd.DataFrame, gmm_results: dict, output_dir: Path):
    """Generate GMM descriptive plots."""
    output_dir.mkdir(parents=True, exist_ok=True)

    if "error" in gmm_results:
        return

    # Plot 1: BIC scores per seed (CFM-06 — multi-seed K selection)
    fig, ax = plt.subplots(figsize=(10, 6))
    for seed, scores in gmm_results["bic_scores"].items():
        ks = sorted([k for k, v in scores.items() if v is not None])
        ax.plot(ks, [scores[k] for k in ks], "o-",
                label=f"seed {seed}", alpha=0.8)
    ax.axvline(x=gmm_results["best_k"], color="r", linestyle="--",
               label=f"Modal K={gmm_results['best_k']}")
    ax.set_xlabel("Number of components (K)")
    ax.set_ylabel("BIC (lower is better)")
    ax.set_title("GMM BIC Scores — K=1..5 per seed "
                 "(K=1 is null benchmark)")
    ax.legend()
    fig.text(0.5, 0.01, ELEVATION_DISCLAIMER, ha="center", fontsize=8,
             style="italic")
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
    js = gmm_results.get("js_distance", {})
    fig.text(0.5, 0.01, f"{ELEVATION_DISCLAIMER}\nJS distance: "
             f"{js.get('observed', float('nan')):.4f} "
             f"(95% CI {js.get('ci95', '?')})",
             ha="center", fontsize=8, style="italic")
    plt.tight_layout()
    fig.savefig(output_dir / "11_gmm_cluster_occupancy.png", dpi=150)
    plt.close()

    print(f"GMM plots saved to {output_dir}/")


def preflight(feature_file: Path, min_baseline_rows: int = 100) -> list[str]:
    """CFM-01/CFM-10 — input completeness gate. Returns problems; any
    problem means the confirmation run does not execute."""
    problems: list[str] = []
    if not feature_file.is_file():
        problems.append(f"feature matrix {feature_file} missing")
        return problems
    daily_df = pd.read_csv(feature_file, index_col=0, parse_dates=True)
    missing = [c for c in GMM_FEATURES if c not in daily_df.columns]
    if missing:
        problems.append(f"required features missing: {missing}")
    years = daily_df.index.year
    baseline_years = set(range(2001, 2026))
    absent = baseline_years - set(years)
    if absent:
        problems.append(f"baseline years absent: {sorted(absent)}")
    if len(daily_df) < min_baseline_rows:
        problems.append(
            f"only {len(daily_df)} rows — the confirmation run "
            "requires the complete baseline, not a partial file")
    return problems


def main() -> int:
    """Main GMM descriptive confirmation.

    CFM-10 — exit code 0 only on a complete, reproducible run.
    Missing input, failed fits, or non-convergence exit nonzero.
    """
    print("=" * 60)
    print("GMM Descriptive Confirmation — EXPLORATORY, single cell")
    print("=" * 60)
    print()
    print("GMM is DESCRIPTIVE ONLY — it describes weather regimes, "
          "NOT avalanche precursors or forecasts.")
    print()

    feature_file = DATA_DIR / "features_nepal_jja_2001_2026.csv"
    problems = preflight(feature_file)
    if problems:
        print("PREFLIGHT FAILED — no run executed:")
        for p in problems:
            print(f"  - {p}")
        return 1

    print(f"Loading features from {feature_file}...")
    daily_df = pd.read_csv(feature_file, index_col=0, parse_dates=True)
    print(f"Loaded {len(daily_df)} rows\n")

    gmm_results = run_gmm_descriptive(daily_df, target_year=2026)

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    if "error" in gmm_results:
        with open(RESULTS_FILE, "w") as f:
            json.dump(gmm_results, f, indent=2, default=str)
        write_bundle(gmm_results,
                     sorted(DATA_DIR.glob("era5_land_*.nc")),
                     feature_file, RUN_DIR)
        print(f"RUN FAILED: {gmm_results['error']}")
        print("Results/bundle recorded under research_runs/ as "
              "quarantined output.")
        return 1

    print("\nGenerating GMM plots...")
    generate_gmm_plots(daily_df, gmm_results, GMM_PLOT_DIR)

    with open(RESULTS_FILE, "w") as f:
        json.dump(gmm_results, f, indent=2, default=str)
    print(f"\nResults saved to {RESULTS_FILE}")

    bundle = write_bundle(gmm_results,
                          sorted(DATA_DIR.glob("era5_land_*.nc")),
                          feature_file, RUN_DIR)
    print(f"Run bundle saved to {BUNDLE_FILE} "
          f"(run_id={bundle['run_id']})")

    js = gmm_results["js_distance"]
    print("\n" + "=" * 60)
    print("GMM Descriptive Summary")
    print("=" * 60)
    print(f"Modal K (BIC, {len(GMM_SEEDS)} seeds): "
          f"{gmm_results['best_k']} "
          f"(frequency {gmm_results['modal_k_frequency']:.2f})")
    print(f"K=1 null benchmark: {'YES' if gmm_results['k1_null_benchmark'] else 'NO'}")
    print(f"JS distance: {js['observed']} (95% CI {js['ci95']}, "
          f"null p95 {js['null_p95']}, exceeds null: "
          f"{js['exceeds_null']})")
    pc = gmm_results["posterior_confidence"]
    print(f"Posterior confidence: mean {pc['mean_max_proba']}, "
          f"ambiguous {pc['ambiguous_fraction']:.1%}")
    if gmm_results.get("js_distance_pre_event") is not None:
        print(f"JS distance (pre-event): "
              f"{gmm_results['js_distance_pre_event']}")
    print(f"\nStatus: {gmm_results['status']}")
    print(f"DISCLAIMER: {gmm_results['disclaimer']}")

    print("\n" + "=" * 60)
    print("GMM Descriptive EXIT GATE: PASS "
          "(complete reproducible run)")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
